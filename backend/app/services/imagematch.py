"""Rank card-database candidates against a scanned card.

Three signals are blended into a single confidence score:

- Name score: how well the OCR'd name matches each candidate's name (fuzzy
  string similarity, tolerant of OCR misreads).
- Number score: how well the OCR'd collector number - "9/165" - matches the
  candidate's own. This is the signal that separates the ~55 printings of
  Dragonite from each other, since they all score an identical 1.0 on name
  alone. Both halves count: the index ("9") and the set's printed card count
  ("165"), which identifies the set.
- Visual score: perceptual-hash similarity between the user's scanned card
  and each candidate's reference image. Only computed for the top few
  candidates (not the whole database) to keep this cheap, and deliberately the
  weakest of the three - a phone photo's phash is noisy enough (glare, holo
  foiling, imperfect warp) that at a higher weight it will confidently promote
  a different Pokemon over an exact name match.

Three further signals - HP, attack names and energy type - are applied
separately by apply_detail_agreement(). They arrive only with the per-card
detail fetch, so they re-order the enriched shortlist rather than taking part
in the main ranking pass.

Every stage degrades gracefully: if optional dependencies are missing, a
candidate's image can't be fetched, or the visual stage fails outright, the
pipeline falls back to text-only ranking rather than failing the scan.
"""
from __future__ import annotations

import asyncio
import difflib
import io
import re
from collections import OrderedDict

import httpx

from ..config import get_settings
from . import cardname, timing

settings = get_settings()

try:
    from PIL import Image  # type: ignore
    import imagehash  # type: ignore
    _HAS_IMAGEHASH = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_IMAGEHASH = False

try:
    from rapidfuzz import fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_RAPIDFUZZ = False

# In-process cache of tcg_id -> phash, so repeatedly-scanned/popular cards
# don't re-fetch and re-hash their reference image on every scan. Bounded
# with manual LRU eviction; lost on process restart, which is fine - it's a
# pure memoization concern, not durable state.
_HASH_CACHE: "OrderedDict[str, str]" = OrderedDict()
_HASH_CACHE_MAX = 2000


def _cache_get(tcg_id: str) -> str | None:
    value = _HASH_CACHE.get(tcg_id)
    if value is not None:
        _HASH_CACHE.move_to_end(tcg_id)
    return value


def _cache_put(tcg_id: str, phash: str) -> None:
    _HASH_CACHE[tcg_id] = phash
    _HASH_CACHE.move_to_end(tcg_id)
    while len(_HASH_CACHE) > _HASH_CACHE_MAX:
        _HASH_CACHE.popitem(last=False)


# How much of the name score is left standing when a candidate's modifiers
# disagree completely with the reading's. The floor is not zero because a
# stitched modifier can be stray text - a disagreement demotes a candidate, it
# does not eliminate it.
_MODIFIER_FLOOR = 0.55


def _text_score(query_name: str | None, candidate_name: str) -> float:
    """0-1 name agreement, sharpened by form/subtype modifiers.

    Raw fuzzy similarity is far too generous about the words that actually
    distinguish printings: WRatio rewards substrings, so a reading of "Galarian
    Darmanitan VMAX" scores ~0.9 against the plain "Darmanitan" it contains -
    a different card, in a different set, worth a different amount. Scaling by
    modifier agreement is what stops a broad character match from outranking
    the specific printing that was actually scanned.
    """
    if not query_name:
        return 0.5  # neutral - no OCR name to compare against
    # Lowercase both sides - OCR case is unreliable (all-caps misreads are
    # common) and WRatio is case-sensitive by default, which otherwise tanks
    # the score for an exact match that merely differs in case.
    query_lower, candidate_lower = query_name.lower(), candidate_name.lower()
    if _HAS_RAPIDFUZZ:
        similarity = fuzz.WRatio(query_lower, candidate_lower) / 100.0
    else:
        similarity = difflib.SequenceMatcher(None, query_lower, candidate_lower).ratio()

    agreement = cardname.agreement(query_name, candidate_name)
    return similarity * (_MODIFIER_FLOOR + (1.0 - _MODIFIER_FLOOR) * agreement)


def _normalize_number(value: str | None) -> str:
    """Comparable form of a collector-number index.

    Card numbers are printed and stored inconsistently - "049", "49", "SWSH154"
    - so casing, padding and punctuation are stripped before comparing.
    """
    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    stripped = cleaned.lstrip("0")
    return stripped or cleaned


def _number_score(
    ocr_number: str | None, ocr_set_total: int | None, candidate: dict
) -> float | None:
    """0-1 agreement between the scanned collector number and a candidate.

    Returns None when nothing was read, so the caller can fall back to name-only
    scoring rather than penalising every candidate equally.

    The two halves are scored separately and deliberately weighted apart: the
    set total is worth nearly as much as the index because it pins down the
    *set*, and a set is what tells a 1999 Fossil Dragonite from a 2002
    Expedition one. Either half alone is good evidence; both together is an
    exact identification.
    """
    index = _normalize_number((ocr_number or "").split("/")[0])
    if not index and ocr_set_total is None:
        return None

    candidate_index = _normalize_number(candidate.get("number"))
    candidate_total = candidate.get("set_total")

    index_match = bool(index) and index == candidate_index
    total_match = (
        ocr_set_total is not None
        and isinstance(candidate_total, int)
        and ocr_set_total == candidate_total
    )

    if index_match and total_match:
        return 1.0
    if index_match:
        return 0.6
    if total_match:
        return 0.45
    return 0.0


def _hamming_to_similarity(distance: int, hash_bits: int = 64) -> float:
    return max(0.0, 1.0 - (distance / hash_bits))


# Reference images live on a different host to the card API and want their own
# short timeout, so they get their own pooled client - built once, not per call.
_image_client: httpx.AsyncClient | None = None


def get_image_client() -> httpx.AsyncClient:
    global _image_client
    if _image_client is None or _image_client.is_closed:
        _image_client = httpx.AsyncClient(
            timeout=settings.image_fetch_timeout_seconds,
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=24),
            headers={"user-agent": "PokeDetect/1.0"},
        )
    return _image_client


async def close_image_client() -> None:
    """Release the pool at shutdown. Safe to call when nothing was opened."""
    global _image_client
    if _image_client is not None and not _image_client.is_closed:
        await _image_client.aclose()
    _image_client = None


async def _fetch_and_hash(client: httpx.AsyncClient, tcg_id: str, image_url: str) -> str | None:
    cached = _cache_get(tcg_id)
    if cached is not None:
        timing.timer().count("phash_cache_hit")
        return cached
    if not image_url:
        return None
    try:
        with timing.timer().stage("image_fetch_ms"):
            resp = await client.get(image_url, timeout=settings.image_fetch_timeout_seconds)
            resp.raise_for_status()
        timing.timer().count("image_fetch")
        pil_image = Image.open(io.BytesIO(resp.content))
        phash = str(imagehash.phash(pil_image))
        _cache_put(tcg_id, phash)
        return phash
    except Exception:
        # Any failure (timeout, 404, corrupt/non-image content) - this one
        # candidate just won't get a visual score. Never propagate.
        return None


async def visual_scores(
    candidates: list[dict], scan_phash: str | None, limit: int
) -> dict[str, float]:
    """Return {tcg_id: similarity} for up to `limit` candidates.

    Returns {} immediately (no network calls) if imagehash is unavailable or
    the scan has no phash - the primary degradation path to text-only ranking.
    """
    if not _HAS_IMAGEHASH or not scan_phash:
        return {}

    subset = candidates[:limit]
    try:
        scan_hash = imagehash.hex_to_hash(scan_phash)
    except Exception:
        return {}

    try:
        client = get_image_client()
        tasks = [_fetch_and_hash(client, c["tcg_id"], c["image_url"]) for c in subset]
        hashes = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        return {}

    scores: dict[str, float] = {}
    for candidate, result in zip(subset, hashes):
        if isinstance(result, Exception) or not result:
            continue
        try:
            distance = scan_hash - imagehash.hex_to_hash(result)
            scores[candidate["tcg_id"]] = _hamming_to_similarity(distance)
        except Exception:
            continue
    return scores


async def rank_candidates(
    candidates: list[dict],
    ocr_name: str | None,
    ocr_number: str | None,
    scan_phash: str | None,
    ocr_set_total: int | None = None,
    visual_limit: int | None = None,
    final_limit: int | None = None,
) -> list[dict]:
    """Score, blend and sort candidates. Returns candidate dicts with an
    added "confidence" (0-1) key, best match first, truncated to final_limit.

    Nothing is filtered out, only ordered: a misread digit or a soft name should
    push the right card down the list, never off it, because the list is what
    the user picks from.
    """
    visual_limit = visual_limit if visual_limit is not None else settings.match_visual_rerank_limit
    final_limit = final_limit if final_limit is not None else settings.match_final_limit

    if not candidates:
        return []

    # Stage 1: text-only score (name + collector number), over every candidate.
    scored: list[tuple[dict, float, bool]] = []
    for c in candidates:
        name_score = _text_score(ocr_name, c.get("name", ""))
        number_score = _number_score(ocr_number, ocr_set_total, c)
        if number_score is None:
            text = name_score
        else:
            # Mismatching numbers are weighted down rather than dropped - OCR
            # misreads a digit often enough that a hard filter would throw away
            # the correct card.
            text = 0.6 * name_score + 0.4 * number_score
        scored.append((c, text, number_score == 1.0))

    scored.sort(key=lambda row: row[1], reverse=True)

    # Stage 2: visual re-rank, over the best text scorers only.
    visual: dict[str, float] = {}
    try:
        visual = await visual_scores(
            [c for c, _, _ in scored], scan_phash, limit=visual_limit
        )
    except Exception:
        visual = {}

    weight = settings.match_visual_weight
    blended: list[dict] = []
    for c, text, exact_number in scored:
        v = visual.get(c["tcg_id"])
        # The visual signal is ignored when the name plainly doesn't match:
        # image similarity should refine a shortlist of the right Pokemon, not
        # nominate a different one.
        if v is not None and text >= settings.match_visual_min_text:
            confidence = (1.0 - weight) * text + weight * v
        else:
            confidence = text
        if exact_number:
            # Index *and* set size agreeing is an exact identification; a noisy
            # phash must not be able to rank anything above it.
            confidence = max(confidence, 0.95)
        blended.append({**c, "confidence": round(confidence, 3)})

    blended.sort(key=lambda c: c["confidence"], reverse=True)
    return blended[:final_limit]


# Per-signal nudges for the detail stage, ordered by how much each one
# actually distinguishes one printing from another. An attack name is close to
# a fingerprint; HP is shared by plenty of cards; frame colour barely narrows
# anything on its own. None of them decide a match - they reorder one.
_ATTACK_MATCH_BONUS = 0.08
_ATTACK_MISMATCH_FACTOR = 0.92
_HP_MATCH_BONUS = 0.05
_HP_MISMATCH_FACTOR = 0.85
_TYPE_MATCH_BONUS = 0.03
_TYPE_MISMATCH_FACTOR = 0.94


def _attack_similarity(read_name: str, candidate_name: str) -> float:
    """Whole-string similarity between two attack names.

    fuzz.ratio, not WRatio: attack names are short and share vocabulary, and
    WRatio's substring bias scores "Slash" against "Slashing Strike" high
    enough to call them the same attack.
    """
    read_lower, candidate_lower = read_name.lower(), candidate_name.lower()
    if _HAS_RAPIDFUZZ:
        return fuzz.ratio(read_lower, candidate_lower) / 100.0
    return difflib.SequenceMatcher(None, read_lower, candidate_lower).ratio()


def _attacks_agree(read_names: list[str], candidate_names: list[str]) -> bool:
    """Whether any read attack name is one of the candidate's attacks.

    Any single hit is enough. OCR of the attack block reliably returns some
    rules-text fragments alongside the real names, so requiring all of them to
    match would mean requiring OCR to be clean, which it is not.
    """
    return any(
        _attack_similarity(read, candidate) >= settings.attack_match_threshold
        for read in read_names
        for candidate in candidate_names
    )


def apply_detail_agreement(
    cards: list[dict],
    hp: int | None = None,
    attack_names: list[str] | None = None,
    types: list[str] | None = None,
) -> list[dict]:
    """Re-order already-enriched matches by the card's detail-only attributes.

    HP, attack names and energy type all arrive with the per-card detail fetch -
    search rows are lean - so unlike name and collector number these cannot ride
    along in rank_candidates. They are applied afterwards, to the handful of
    matches that were actually enriched.

    They earn the extra stage because they separate printings that agree on
    everything the search could see. Every Charizard matches the name
    "Charizard" perfectly; only one of them attacks with "Fire Spin" at 120 HP.

    Every signal demotes, none eliminates. All three are read off small print or
    off colour that foiling and glare distort, so a disagreement has to be able
    to push the right card down the list without pushing it off - the list is
    what the user picks from. Candidates missing an attribute are left untouched
    rather than penalised for the database's gaps.
    """
    attack_names = attack_names or []
    types = types or []
    if not hp and not attack_names and not types:
        return cards

    adjusted: list[dict] = []
    for card in cards:
        confidence = card.get("confidence", 0.0)

        card_hp = card.get("hp")
        if hp and isinstance(card_hp, int) and card_hp > 0:
            if card_hp == hp:
                confidence = min(1.0, confidence + _HP_MATCH_BONUS)
            else:
                confidence *= _HP_MISMATCH_FACTOR

        card_attacks = card.get("attack_names") or []
        if attack_names and card_attacks:
            if _attacks_agree(attack_names, card_attacks):
                confidence = min(1.0, confidence + _ATTACK_MATCH_BONUS)
            else:
                confidence *= _ATTACK_MISMATCH_FACTOR

        card_types = card.get("types") or []
        if types and card_types:
            # `types` is the set of types the frame colour could plausibly be,
            # so an overlap is agreement rather than an exact identification.
            if set(types) & set(card_types):
                confidence = min(1.0, confidence + _TYPE_MATCH_BONUS)
            else:
                confidence *= _TYPE_MISMATCH_FACTOR

        adjusted.append({**card, "confidence": round(confidence, 3)})

    adjusted.sort(key=lambda c: c["confidence"], reverse=True)
    return adjusted
