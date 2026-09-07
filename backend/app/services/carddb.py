"""Client for the TCGdex card database (https://api.tcgdex.net/v2/en).

Replaces the former pokemontcg.io client: that API no longer has a usable free
plan and now answers 502, which is why scans stopped finding any cards. TCGdex
is free, needs no API key, and carries Cardmarket EUR pricing.

Three responsibilities:

- search_candidates()  Broad, typo-tolerant candidate lookup by name/number.
- enrich_matches()     Fill in set, rarity and pricing for a small final list
                       (TCGdex search results are lean - no set/rarity/price -
                       so pricing needs a per-card detail fetch).
- resolve_name()       Snap a noisy OCR reading onto a real card name using a
                       cached index of every name in the database. This is what
                       turns a misread like "Lucarlo" into "Lucario", and picks
                       the right line when OCR returns several candidates.
- get_set_index()      Cached {set id -> printed card count}, which is what lets
                       the "/165" half of a collector number pick out the set.
"""
from __future__ import annotations

import asyncio
import copy
import difflib
import re
import time
from collections import OrderedDict

import httpx

from ..config import get_settings
from . import cardname, timing

settings = get_settings()

try:
    from rapidfuzz import fuzz, process, utils as fuzz_utils  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_RAPIDFUZZ = False

# Condition multipliers applied to the Cardmarket trend price to produce a
# rough condition-adjusted estimate. Cardmarket trend prices broadly track
# Near Mint stock, so NM ~= 1.0 and worse conditions scale down.
CONDITION_MULTIPLIERS: dict[str, float] = {
    "Mint": 1.15,
    "Near Mint": 1.0,
    "Excellent": 0.85,
    "Good": 0.65,
    "Light Played": 0.5,
    "Played": 0.38,
    "Poor": 0.25,
}

PRICE_DISCLAIMER = (
    "Price is an approximate market figure from Cardmarket data and is not an "
    "exact quote. Actual value depends on condition, edition, printing and demand, "
    "but this should be roughly in the right range."
)

PRICE_SOURCE = "Cardmarket (via TCGdex)"


# --------------------------------------------------------------------------- #
# Response mapping
# --------------------------------------------------------------------------- #
def _image_url(card: dict, quality: str = "low", fmt: str = "webp") -> str:
    """Build a usable image URL.

    TCGdex image URLs are extension-less base paths - a quality + format suffix
    is mandatory or the URL 404s. Some cards carry no image at all.
    """
    base = card.get("image")
    if not isinstance(base, str) or not base:
        return ""
    return f"{base}/{quality}.{fmt}"


def _extract_price(pricing_holder: dict, *, suffix: str = "") -> tuple[float | None, str]:
    """Return (price, currency) out of one `pricing` block.

    `pricing_holder` is anything carrying a `pricing` key of TCGdex's usual
    shape - a card, or one entry of its `variants_detailed[]`, which repeats
    the same shape. `suffix` selects a specific finish's Cardmarket columns
    ("-holo" for the foiled listing alongside the plain one) - see
    select_variant_price(), which is the only caller that passes it.
    """
    pricing = pricing_holder.get("pricing") or {}
    cardmarket = pricing.get("cardmarket") or {}
    for key in (f"trend{suffix}", f"avg{suffix}", f"avg7{suffix}", f"avg30{suffix}"):
        value = cardmarket.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value), cardmarket.get("unit") or "EUR"

    tcgplayer = pricing.get("tcgplayer") or {}
    unit = tcgplayer.get("unit") or "USD"
    for variant in tcgplayer.values():
        if isinstance(variant, dict):
            price = variant.get("marketPrice") or variant.get("midPrice")
            if isinstance(price, (int, float)) and price > 0:
                return float(price), unit
    return None, "EUR"


def _set_id_of(card: dict) -> str:
    """The card's set id, e.g. "ecard1" for "ecard1-9".

    Detail responses nest it under `set`; lean search rows carry only the
    composite `id`, whose prefix (up to the last "-") is the set id.
    """
    set_info = card.get("set") or {}
    if set_info.get("id"):
        return str(set_info["id"])
    card_id = str(card.get("id", ""))
    return card_id.rsplit("-", 1)[0] if "-" in card_id else ""


def _simplify(card: dict, set_sizes: dict[str, int] | None = None) -> dict:
    """Map a TCGdex card (lean search row or full detail) to our shape.

    `set_sizes` (from get_set_index) fills in `set_total` for lean search rows,
    which otherwise carry no set information at all - that number is what a
    scanned "9/165" is matched against, so it has to be present on candidates
    *before* ranking, not only on the enriched final few.
    """
    price, currency = _extract_price(card)
    set_info = card.get("set") or {}
    set_id = _set_id_of(card)
    card_count = set_info.get("cardCount") or {}
    total = card_count.get("official") or card_count.get("total")
    if not isinstance(total, int) and set_sizes:
        total = set_sizes.get(set_id)
    return {
        "tcg_id": card.get("id", ""),
        "name": card.get("name", ""),
        "set_id": set_id,
        "set_name": set_info.get("name", ""),
        "number": str(card.get("localId", "") or ""),
        # The set's printed card count - the "/165" in "9/165".
        "set_total": total if isinstance(total, int) else None,
        # TCGdex spells "no rarity" as the literal string "None" on some sets.
        "rarity": "" if card.get("rarity") in (None, "None") else str(card["rarity"]),
        # Detail-only fields: lean search rows carry none of these, so they
        # are empty until enrich_matches has run. All three are late
        # tie-breakers between printings rather than search terms - see
        # imagematch.apply_detail_agreement.
        "hp": card["hp"] if isinstance(card.get("hp"), int) else None,
        "attack_names": [
            str(a["name"])
            for a in (card.get("attacks") or [])
            if isinstance(a, dict) and a.get("name")
        ],
        "types": [str(t) for t in (card.get("types") or []) if t],
        # Which finishes this printing was ever produced in, e.g.
        # {"normal": True, "reverse": True, "holo": False, "firstEdition":
        # False}. Detail-only, and the gate select_variant_price() checks
        # before offering a finish-specific price - a card TCGdex never lists
        # as holo should never be priced as one just because a photo looked
        # shiny.
        "variant_flags": card.get("variants") if isinstance(card.get("variants"), dict) else None,
        "variants_detailed": card.get("variants_detailed")
        if isinstance(card.get("variants_detailed"), list)
        else [],
        "image_url": _image_url(card),
        "market_price": price,
        "currency": currency,
    }


# --------------------------------------------------------------------------- #
# Print variant pricing
# --------------------------------------------------------------------------- #
def _default_variant_label(flags: dict) -> str:
    """The variant a card's own flags already imply, with no photo needed.

    Blank whenever the card was printed more than one way and nothing has
    disambiguated it yet - "normal" would be a guess, not a fact, for a card
    that also exists as reverse holo.
    """
    if flags.get("reverse") and flags.get("normal"):
        return ""
    if flags.get("firstEdition"):
        return ""
    if flags.get("holo"):
        return "Holo"
    if flags.get("reverse"):
        return "Reverse Holo"
    if flags.get("normal"):
        return "Normal"
    return ""


def _variant_price(
    entries: list[dict],
    *,
    type_: str | None = None,
    subtype: str | None = None,
    suffix: str = "",
) -> tuple[float, str] | None:
    """First priced entry in `entries` matching `type_`/`subtype`, or None."""
    for entry in entries:
        if type_ is not None and entry.get("type") != type_:
            continue
        if subtype is not None and entry.get("subtype") != subtype:
            continue
        price, currency = _extract_price(entry, suffix=suffix)
        if price is not None:
            return price, currency
    return None


def select_variant_price(
    card: dict, is_foil: bool | None, is_first_edition: bool | None
) -> tuple[float | None, str, str]:
    """The (price, currency, variant label) matching what the photo showed.

    A card's default price - what enrich_matches already fills in - covers
    whichever finish TCGdex happens to list first, which is frequently not the
    finish that was actually scanned: a reverse holo Weedle prices five times
    higher than its normal printing, and an unlimited Base Set Charizard
    a fraction of its 1st Edition self. This picks the matching listing out of
    `variants_detailed` when the photo gave a usable hint and the card's own
    `variant_flags` confirm that finish genuinely exists for it - never
    inventing a variant a card was never printed in.

    Falls through to the card's existing default whenever a hint is missing,
    unconfirmed, or the card isn't actually ambiguous (most cards only have one
    real-world finish, and the default is already correct for them).
    """
    default_price = card.get("market_price")
    default_currency = card.get("currency") or "EUR"
    flags = card.get("variant_flags")
    if not flags:
        return default_price, default_currency, ""

    entries = card.get("variants_detailed") or []
    label = _default_variant_label(flags)

    # 1st Edition vs Unlimited. Only meaningful for a card that genuinely has
    # a 1st Edition print run, and only acted on in the direction the reading
    # actually supports - an *unread* stamp (None) must never be treated as a
    # confirmed absence, since the crop simply misses on most photos (bad
    # angle, cropped-out margin) far more often than it correctly finds
    # nothing on a card that was never printed 1st Edition to begin with.
    if flags.get("firstEdition"):
        if is_first_edition:
            tier = _variant_price(entries, subtype="shadowless")
            if tier:
                return tier[0], tier[1], "1st Edition"
        elif is_first_edition is False:
            tier = _variant_price(entries, subtype="unlimited")
            if tier:
                return tier[0], tier[1], "Unlimited"

    # Normal vs Reverse Holo. Only meaningful when the card was printed both
    # ways - the common case for every set since EX Ruby & Sapphire.
    if is_foil is not None and flags.get("normal") and flags.get("reverse"):
        if is_foil:
            tier = _variant_price(entries, type_="reverse", suffix="-holo")
            if tier:
                return tier[0], tier[1], "Reverse Holo"
        else:
            tier = _variant_price(entries, type_="normal")
            if tier:
                return tier[0], tier[1], "Normal"

    return default_price, default_currency, label


def apply_variant_pricing(
    cards: list[dict], is_foil: bool | None, is_first_edition: bool | None
) -> list[dict]:
    """Overlay finish-specific pricing onto already-enriched matches.

    Applied after enrich_matches, since select_variant_price needs the detail
    fetch's `variants_detailed` - lean search rows carry none of it. Pure
    relabelling of price/currency/variant; never changes which cards are in
    the list or their order.
    """
    if not cards:
        return cards
    out = []
    for card in cards:
        price, currency, variant = select_variant_price(card, is_foil, is_first_edition)
        out.append({**card, "market_price": price, "currency": currency, "variant": variant})
    return out


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
# One client for the whole process instead of one per request.
#
# A single scan can issue well over a hundred calls to this API (a search, then
# a detail fetch per ranked printing), and building a client per call meant a
# fresh TCP connection and TLS handshake for every one of them. The connection
# pool below is the single largest saving available in the network stage, and
# it changes nothing about what is requested.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=settings.card_api_timeout_seconds,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
            headers={"user-agent": "PokeDetect/1.0"},
        )
    return _client


async def close_client() -> None:
    """Release the pool at shutdown. Safe to call when nothing was opened."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_json(path: str, params: dict | None = None, timeout: float | None = None):
    url = f"{settings.tcgdex_base_url}{path}"
    watch = timing.timer()
    try:
        with watch.stage("http_card_api_ms"):
            resp = await get_client().get(
                url,
                params=params,
                timeout=timeout or settings.card_api_timeout_seconds,
            )
            resp.raise_for_status()
            watch.count("http_card_api")
            return resp.json()
    except (httpx.HTTPError, ValueError):
        watch.count("http_card_api_failed")
        return None


# --------------------------------------------------------------------------- #
# Response caches
#
# Two bounded TTL caches sitting under the fetch layer. They exist because the
# same detail rows are fetched over and over: every printing of a Pokemon is
# enriched on each scan of that Pokemon, and a scan that also does a collector
# number lookup enriches a second, heavily overlapping list. Caching here -
# rather than trimming the lists - keeps the pipeline's shape identical: the
# same candidates are ranked, enriched and re-ranked, they just stop being
# re-fetched.
#
# Failures are deliberately not cached: a transient timeout must not pin a card
# to "unknown" for the next six hours.
#
# Every hit is handed out as a deep copy. Nothing downstream mutates these rows
# today, but a cache that lends out its own objects turns any future in-place
# edit into a silent, process-wide corruption that only shows up on the second
# scan - a copy per hit is nothing next to the fetch it replaces.
# --------------------------------------------------------------------------- #
_MISS = object()

_CARD_CACHE: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_SEARCH_CACHE: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()
# Detail fetches in flight, so concurrent scans of the same card - or the two
# enrichment rounds within one scan - share a single request instead of racing.
_CARD_INFLIGHT: dict[str, asyncio.Future] = {}


def _cache_lookup(store: OrderedDict, key: str, ttl: float):
    entry = store.get(key)
    if entry is None:
        return _MISS
    stored_at, value = entry
    if time.time() - stored_at >= ttl:
        store.pop(key, None)
        return _MISS
    store.move_to_end(key)
    return copy.deepcopy(value)


def _cache_store(store: OrderedDict, key: str, value, cap: int) -> None:
    store[key] = (time.time(), value)
    store.move_to_end(key)
    while len(store) > cap:
        store.popitem(last=False)


def clear_caches() -> None:
    """Drop every cached response. For tests and manual invalidation."""
    _CARD_CACHE.clear()
    _SEARCH_CACHE.clear()


# --------------------------------------------------------------------------- #
# Name index - every distinct card name, for fuzzy OCR correction
# --------------------------------------------------------------------------- #
_name_index: list[str] = []
_name_index_at: float = 0.0
_name_index_lock = asyncio.Lock()


def _index_is_fresh() -> bool:
    return bool(_name_index) and (
        time.time() - _name_index_at < settings.name_index_ttl_seconds
    )


async def _get_name_index() -> list[str]:
    """Lazily fetch and cache the list of distinct card names (~4.6k entries).

    One ~2MB request, refreshed daily. On failure this returns whatever is
    already cached (possibly nothing) and callers fall back to the raw OCR text.
    """
    global _name_index, _name_index_at

    if _index_is_fresh():
        return _name_index

    async with _name_index_lock:
        # Another coroutine may have refreshed it while we waited on the lock.
        if _index_is_fresh():
            return _name_index
        data = await _get_json("/cards", timeout=settings.name_index_timeout_seconds)
        if isinstance(data, list) and data:
            names = {c.get("name", "") for c in data if isinstance(c, dict)}
            _name_index = sorted(n for n in names if n)
            _name_index_at = time.time()
    return _name_index


# How much each form/subtype word in a resolved name is worth when choosing
# between OCR readings. Enough to settle a near-tie, not enough to override a
# genuinely better match.
_MODIFIER_BONUS = 0.03


def _best_index_match(candidate: str, index: list[str]) -> tuple[str, float]:
    """Closest real card name to one OCR reading, as (name, 0-1 score).

    token_sort_ratio, not WRatio: WRatio rewards substrings, so it scores a
    short name contained in the reading ("Mew" inside "Mewtwo VMAX") at 0.9 and
    would happily resolve to the wrong card. Whole-string similarity is what
    "is this reading that card's name?" actually asks.
    """
    if _HAS_RAPIDFUZZ:
        # default_process lowercases and strips punctuation on both sides.
        # Without it an all-caps OCR reading - the common case, card names are
        # printed in caps on several eras - is scored as a near-total mismatch
        # against the database's mixed-case spelling.
        hit = process.extractOne(
            candidate,
            index,
            scorer=fuzz.token_sort_ratio,
            processor=fuzz_utils.default_process,
        )
        return (hit[0], hit[1] / 100.0) if hit else ("", 0.0)
    best = max(
        index, key=lambda n: difflib.SequenceMatcher(None, candidate.lower(), n.lower()).ratio()
    )
    return best, difflib.SequenceMatcher(None, candidate.lower(), best.lower()).ratio()


async def resolve_name(candidates: list[str]) -> tuple[str | None, float]:
    """Pick the best real card name for a list of OCR name candidates.

    OCR hands us several plausible lines from the top of the card: the name
    banner, but also "Evolves from ...", stage labels and outright misreads.
    Scoring every candidate against the real name index and keeping the best is
    what stops a stray line - "conar" read off a Dragonite card - from becoming
    the search term. Returns (name, score); the name is the *database* spelling
    when the match is confident, otherwise the best raw candidate.
    """
    cleaned = [c.strip() for c in candidates if c and c.strip()]
    if not cleaned:
        return None, 0.0

    index = await _get_name_index()
    if not index:
        return cleaned[0], 0.0

    best_name = ""
    best_score = 0.0  # the raw similarity of the winner, for the threshold
    best_rank = -1.0  # similarity plus the modifier bonus, for choosing
    for candidate in cleaned:
        name, score = _best_index_match(candidate, index)
        if not name:
            continue
        # A reading carrying form/subtype words describes a more specific card
        # than a bare one, so it wins near-ties. Without this the stitched
        # "Galarian Darmanitan VMAX" loses to the bare "Darmanitan" line it was
        # built from, purely because a shorter string is easier to match
        # cleanly - and the scan then identifies the wrong card. The bonus is
        # small by design: it breaks ties, it cannot rescue a bad match.
        rank = score + _MODIFIER_BONUS * len(cardname.modifiers(name))
        # On a tie prefer the longer name: "Charizard ex" over "Charizard".
        if rank > best_rank or (rank == best_rank and len(name) > len(best_name)):
            best_name, best_score, best_rank = name, score, rank

    if best_name and best_score >= settings.name_resolve_threshold:
        return best_name, best_score
    return cleaned[0], best_score


async def suggest_names(candidates: list[str], limit: int = 3) -> list[str]:
    """Real card names closest to the OCR reading, best first.

    Used to offer "did you mean" options when the primary search comes back
    empty, so the user gets a pickable list instead of a dead end.
    """
    index = await _get_name_index()
    if not index:
        return []

    scored: dict[str, float] = {}
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        if _HAS_RAPIDFUZZ:
            # WRatio here, unlike in _best_index_match: suggestions want recall,
            # and its substring bias is what surfaces "Lucario & Melmetal GX"
            # alongside plain "Lucario".
            hits = [
                (name, score / 100.0)
                for name, score, _ in process.extract(
                    candidate, index, scorer=fuzz.WRatio, limit=limit
                )
            ]
        else:
            name, score = _best_index_match(candidate, index)
            hits = [(name, score)] if name else []
        for name, score in hits:
            scored[name] = max(scored.get(name, 0.0), score)

    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, score in ranked[:limit] if score >= settings.name_suggest_threshold]


# --------------------------------------------------------------------------- #
# Set index - set id -> printed card count, for collector-number matching
# --------------------------------------------------------------------------- #
_set_index: dict[str, int] = {}
_set_index_at: float = 0.0
_set_index_lock = asyncio.Lock()


async def get_set_index() -> dict[str, int]:
    """Cached {set id -> official printed card count}.

    One small request (~600 sets), refreshed daily. This is what gives meaning
    to the denominator of a collector number: "9/165" can only come from a set
    that printed 165 cards, which on its own rules out almost every printing of
    a Pokemon that shares the name.
    """
    global _set_index, _set_index_at

    fresh = bool(_set_index) and (
        time.time() - _set_index_at < settings.set_index_ttl_seconds
    )
    if fresh:
        return _set_index

    async with _set_index_lock:
        if _set_index and time.time() - _set_index_at < settings.set_index_ttl_seconds:
            return _set_index
        data = await _get_json("/sets")
        if isinstance(data, list) and data:
            sizes: dict[str, int] = {}
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                counts = entry.get("cardCount") or {}
                total = counts.get("official") or counts.get("total")
                if entry.get("id") and isinstance(total, int) and total > 0:
                    sizes[str(entry["id"])] = total
            if sizes:
                _set_index = sizes
                _set_index_at = time.time()
    return _set_index


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _name_fragment(name: str) -> str:
    """A short, substring-friendly fragment of the name.

    TCGdex name filtering is substring-based, so searching a short leading
    fragment keeps a misread trailing character from zeroing out results. The
    collector number is deliberately not a hard filter - a misread digit would
    eliminate an exact name match - it becomes a ranking signal downstream in
    imagematch.rank_candidates instead.
    """
    cleaned = re.sub(r"[^A-Za-z0-9' \-]", "", name).strip()
    if not cleaned:
        return ""
    first_word = cleaned.split(" ", 1)[0]
    return first_word[:6] if len(first_word) > 6 else first_word


async def _search(params: dict[str, str | int], limit: int) -> list[dict]:
    """One /cards query, mapped to our shape with set sizes filled in."""
    query: dict[str, str | int] = {
        "pagination:page": 1,
        "pagination:itemsPerPage": limit,
        **params,
    }
    key = repr(sorted(query.items()))
    cached = _cache_lookup(_SEARCH_CACHE, key, settings.search_cache_ttl_seconds)
    if cached is not _MISS:
        timing.timer().count("search_cache_hit")
        return cached

    data = await _get_json("/cards", query)
    if not isinstance(data, list):
        return []
    set_sizes = await get_set_index()
    rows = [_simplify(c, set_sizes) for c in data if isinstance(c, dict)]
    _cache_store(_SEARCH_CACHE, key, rows, settings.search_cache_max)
    return copy.deepcopy(rows)


# A precise, modified name matches only a printing or two. Below this many
# rows the pool is topped up with the base name's printings - see
# _widen_with_base_name.
_WIDEN_BELOW = 8


async def _widen_with_base_name(rows: list[dict], name: str, limit: int) -> list[dict]:
    """Top a thin modified-name result up with the base name's printings.

    Searching the exact "Galarian Darmanitan VMAX" matches one card, which is
    the point - it is a precise identification. But it also means one misread
    modifier leaves the correct card out of the pool entirely, and the user
    with nothing to pick instead. So when the precise search comes back thin,
    the base name's printings are appended *behind* it. Ranking sorts them out:
    imagematch penalises candidates whose modifiers disagree with the reading,
    so the precise match keeps the top spot and the rest are merely available.
    """
    base = cardname.base_name(name)
    if len(rows) >= _WIDEN_BELOW or not base or base.lower() == name.lower().strip():
        return rows
    seen = {row["tcg_id"] for row in rows}
    extra = [
        row for row in await _search({"name": base}, limit) if row["tcg_id"] not in seen
    ]
    return (rows + extra)[:limit]


async def search_candidates(
    name: str | None = None, number: str | None = None, limit: int = 250
) -> list[dict]:
    """Fetch a broad candidate pool by (partial) name and/or collector number.

    Searches the *full* name first. TCGdex matches names as substrings, so
    "Dragonite" returns all ~55 printings (plus "Dark Dragonite", which is a
    genuinely useful thing to offer). Only if that comes back empty - the sign
    of a misread character - does it retry with a short leading fragment, which
    is tolerant of the misread but much noisier.

    The collector number is deliberately not a hard filter here: a misread digit
    would eliminate the correct card outright. It becomes a strong ranking
    signal downstream in imagematch.rank_candidates instead.

    Rows are lean (no rarity/price); call enrich_matches() on the final few.
    """
    if name:
        cleaned = re.sub(r"[^A-Za-z0-9' \-]", "", name).strip()
        if not cleaned:
            return []
        rows = await _search({"name": cleaned}, limit)
        if rows:
            return await _widen_with_base_name(rows, cleaned, limit)
        # Nothing at all: a character in the reading is wrong. Retry with
        # progressively shorter leading fragments, since a misread is far more
        # likely at the end of a word than at its start.
        fragment = _name_fragment(cleaned)
        seen = {cleaned.lower()}
        while len(fragment) >= 4:
            if fragment.lower() not in seen:
                seen.add(fragment.lower())
                rows = await _search({"name": fragment}, limit)
                if rows:
                    return rows
            fragment = fragment[:-1]
        return []

    if number:
        return await _search({"localId": number.split("/")[0].strip()}, limit)

    return []


async def _fetch_card(tcg_id: str) -> dict | None:
    data = await _get_json(f"/cards/{tcg_id}")
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return _simplify(data, await get_set_index())


async def get_card(tcg_id: str) -> dict | None:
    """Full detail for one card (set, rarity, pricing), cached and coalesced."""
    if not tcg_id:
        return None

    cached = _cache_lookup(_CARD_CACHE, tcg_id, settings.card_detail_ttl_seconds)
    if cached is not _MISS:
        timing.timer().count("card_cache_hit")
        return cached

    # Someone else is already fetching this card - wait on their result rather
    # than firing a second identical request.
    inflight = _CARD_INFLIGHT.get(tcg_id)
    if inflight is not None:
        timing.timer().count("card_coalesced")
        result = await asyncio.shield(inflight)
        return copy.deepcopy(result) if isinstance(result, dict) else None

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _CARD_INFLIGHT[tcg_id] = future
    try:
        result = await _fetch_card(tcg_id)
    except BaseException as exc:  # never leave a waiter hanging
        if not future.done():
            future.set_exception(exc)
        _CARD_INFLIGHT.pop(tcg_id, None)
        raise
    if isinstance(result, dict):
        _cache_store(_CARD_CACHE, tcg_id, result, settings.card_detail_cache_max)
    if not future.done():
        future.set_result(result)
    _CARD_INFLIGHT.pop(tcg_id, None)
    # The stored copy and the returned one must be different objects too.
    return copy.deepcopy(result) if isinstance(result, dict) else result


async def enrich_matches(cards: list[dict]) -> list[dict]:
    """Fill in set/rarity/pricing for a short list of already-ranked cards.

    TCGdex search rows carry no pricing, so this N+1 is unavoidable - kept
    cheap by running only over the handful of matches actually shown, in
    parallel. A failed detail fetch leaves that card with its lean fields
    rather than dropping it from the results.
    """
    if not cards:
        return []

    # The final list can now be dozens of cards (every printing of a Pokemon),
    # so the fan-out is capped rather than firing one request per card at once.
    semaphore = asyncio.Semaphore(settings.enrich_concurrency)

    async def fetch(tcg_id: str) -> dict | None:
        async with semaphore:
            return await get_card(tcg_id)

    timing.timer().count("enrich_n", len(cards))
    details = await asyncio.gather(
        *(fetch(c["tcg_id"]) for c in cards), return_exceptions=True
    )
    enriched: list[dict] = []
    for card, detail in zip(cards, details):
        if isinstance(detail, dict):
            merged = {**card}
            for key, value in detail.items():
                if value not in ("", None):  # detail wins where it has content
                    merged[key] = value
            # "confidence" is computed by the ranker and has no counterpart in
            # the API response, so it survives the merge untouched.
            enriched.append(merged)
        else:
            enriched.append(card)
    return enriched
