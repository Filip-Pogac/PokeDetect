"""Rank Pokemon TCG API candidates against a scanned card.

Two independent signals are blended into a single confidence score:

- Text score: how well the OCR'd name matches each candidate's name
  (fuzzy string similarity, tolerant of OCR misreads), with a small boost
  when the OCR'd collector number matches too.
- Visual score: perceptual-hash similarity between the user's scanned card
  and each candidate's reference image. Only computed for the top few
  text-scored candidates (not the whole database) to keep this cheap.

Both stages degrade gracefully: if optional dependencies are missing, a
candidate's image can't be fetched, or the visual stage fails outright, the
pipeline falls back to text-only ranking rather than failing the scan.
"""
from __future__ import annotations

import asyncio
import difflib
import io
from collections import OrderedDict

import httpx

from ..config import get_settings

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


def _text_score(query_name: str | None, candidate_name: str) -> float:
    if not query_name:
        return 0.5  # neutral - no OCR name to compare against
    # Lowercase both sides - OCR case is unreliable (all-caps misreads are
    # common) and WRatio is case-sensitive by default, which otherwise tanks
    # the score for an exact match that merely differs in case.
    query_lower, candidate_lower = query_name.lower(), candidate_name.lower()
    if _HAS_RAPIDFUZZ:
        return fuzz.WRatio(query_lower, candidate_lower) / 100.0
    return difflib.SequenceMatcher(None, query_lower, candidate_lower).ratio()


def _hamming_to_similarity(distance: int, hash_bits: int = 64) -> float:
    return max(0.0, 1.0 - (distance / hash_bits))


async def _fetch_and_hash(client: httpx.AsyncClient, tcg_id: str, image_url: str) -> str | None:
    cached = _cache_get(tcg_id)
    if cached is not None:
        return cached
    if not image_url:
        return None
    try:
        resp = await client.get(image_url, timeout=settings.image_fetch_timeout_seconds)
        resp.raise_for_status()
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
        async with httpx.AsyncClient() as client:
            tasks = [
                _fetch_and_hash(client, c["tcg_id"], c["image_url"]) for c in subset
            ]
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
    visual_limit: int | None = None,
    final_limit: int | None = None,
) -> list[dict]:
    """Score, blend and sort candidates. Returns candidate dicts with an
    added "confidence" (0-1) key, best match first, truncated to final_limit.
    """
    visual_limit = visual_limit if visual_limit is not None else settings.match_visual_rerank_limit
    final_limit = final_limit if final_limit is not None else settings.match_final_limit

    if not candidates:
        return []

    number_fragment = (ocr_number or "").split("/")[0].strip()
    scored: list[tuple[dict, float]] = []
    for c in candidates:
        text = _text_score(ocr_name, c.get("name", ""))
        if number_fragment and c.get("number") == number_fragment:
            text = min(1.0, text + 0.15)
        scored.append((c, text))

    scored.sort(key=lambda pair: pair[1], reverse=True)

    visual: dict[str, float] = {}
    try:
        visual = await visual_scores(
            [c for c, _ in scored], scan_phash, limit=visual_limit
        )
    except Exception:
        visual = {}

    blended: list[dict] = []
    for c, text in scored:
        v = visual.get(c["tcg_id"])
        confidence = 0.55 * v + 0.45 * text if v is not None else text
        blended.append({**c, "confidence": round(confidence, 3)})

    blended.sort(key=lambda c: c["confidence"], reverse=True)
    return blended[:final_limit]
