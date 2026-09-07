"""Scan route: image in -> recognized card matches + condition estimate."""
from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import get_current_user
from ..models import User
from ..schemas import (
    CardMatch,
    ConditionEstimate,
    PriceInfo,
    ScanResult,
)
from ..services import carddb, imagematch, timing, vision

router = APIRouter(prefix="/api/scan", tags=["scan"])
settings = get_settings()
logger = logging.getLogger("pokedetect.scan")


class ScanRequest(BaseModel):
    image: str  # data URL or base64-encoded JPEG/PNG
    # Optional manual hints if OCR struggles.
    name_hint: str | None = None
    number_hint: str | None = None
    # Optional user-corrected card corners, four (x, y) points normalized to
    # 0-1 fractions of the image. Sent when automatic detection missed and the
    # user dragged the edges by hand.
    corners: list[tuple[float, float]] | None = Field(default=None, min_length=4, max_length=4)


def _price_info(market_price: float | None, currency: str, condition: str) -> PriceInfo:
    estimated = None
    if market_price is not None:
        multiplier = carddb.CONDITION_MULTIPLIERS.get(condition, 1.0)
        estimated = round(market_price * multiplier, 2)
    return PriceInfo(
        market_price=market_price,
        currency=currency,
        estimated_price=estimated,
        source=carddb.PRICE_SOURCE,
        disclaimer=carddb.PRICE_DISCLAIMER,
    )


def _build_matches(ranked: list[dict], condition: str) -> list[CardMatch]:
    return [
        CardMatch(
            tcg_id=m["tcg_id"],
            name=m["name"],
            set_name=m["set_name"],
            number=m["number"],
            rarity=m["rarity"],
            image_url=m["image_url"],
            price=_price_info(m["market_price"], m["currency"], condition),
            confidence=m.get("confidence", 0.0),
            variant=m.get("variant", ""),
        )
        for m in ranked
    ]


class DetailSignals(NamedTuple):
    """What was read off the card that only detail data can be checked against.

    Bundled rather than passed as three parameters because they travel together
    through every matching path and are always applied at the same point - after
    enrichment, never before it.
    """

    hp: int | None
    attack_names: list[str]
    types: list[str]
    # Print-variant hints. Unlike the three above, these never reorder
    # candidates - they only pick which of a card's own listed prices to show,
    # in _apply_detail below. None means "no usable reading", distinct from
    # False's "confirmed absent" - see vision.detect_first_edition.
    is_foil: bool | None
    is_first_edition: bool | None


def _detail_signals(
    reading: vision.TextReading,
    card_type: vision.TypeReading,
    finish: vision.FinishReading,
    is_first_edition: bool | None,
) -> DetailSignals:
    """Collect the post-enrichment signals, dropping unsure readings.

    The frame colour and foil-texture readings are both discarded below their
    confidence floors rather than applied at reduced weight: a full-art card or
    a glare-washed photo produces a colour/texture reading that is not merely
    uncertain but actively wrong, and acting on it demotes the correct card or
    mislabels its price.
    """
    types = (
        card_type.types
        if card_type.confidence >= settings.type_min_confidence
        else []
    )
    is_foil = (
        finish.is_foil
        if finish.confidence >= settings.finish_min_confidence
        else None
    )
    return DetailSignals(
        hp=reading.hp,
        attack_names=reading.attack_names,
        types=types,
        is_foil=is_foil,
        is_first_edition=is_first_edition,
    )


def _apply_detail(cards: list[dict], detail: "DetailSignals | None") -> list[dict]:
    if detail is None:
        return cards
    reranked = imagematch.apply_detail_agreement(
        cards,
        hp=detail.hp,
        attack_names=detail.attack_names,
        types=detail.types,
    )
    return carddb.apply_variant_pricing(reranked, detail.is_foil, detail.is_first_edition)


def _set_total_of(number: str | None) -> int | None:
    """The "/165" half of a collector number, as an int."""
    if not number or "/" not in number:
        return None
    try:
        return int(number.split("/", 1)[1].strip())
    except ValueError:
        return None


async def _search_and_enrich(
    name: str | None,
    number: str | None,
    scan_phash: str | None,
) -> list[dict]:
    """The network half of matching: search, rank, enrich. Returns raw dicts.

    Deliberately free of the detail signals and the condition grade. Those come
    from CV work that this stage does not depend on, so keeping them out lets
    the caller run that work concurrently and apply it once, afterwards.
    """
    candidates = await carddb.search_candidates(
        name=name, number=number, limit=settings.match_candidate_limit
    )
    ranked = await imagematch.rank_candidates(
        candidates,
        ocr_name=name,
        ocr_number=number,
        ocr_set_total=_set_total_of(number),
        scan_phash=scan_phash if settings.enable_visual_rerank else None,
        visual_limit=settings.match_visual_rerank_limit,
        final_limit=settings.match_final_limit,
    )
    # Search results are lean - set, rarity, HP, attacks, type and price only
    # come from the per-card detail endpoint, so fetch those for the few we
    # actually show.
    return await carddb.enrich_matches(ranked)


async def _find_matches(
    name: str | None,
    number: str | None,
    condition: str,
    scan_phash: str | None,
    detail: "DetailSignals | None" = None,
) -> list[CardMatch]:
    enriched = await _search_and_enrich(name, number, scan_phash)
    # Those attributes only exist post-enrichment, so they reorder here.
    return _build_matches(_apply_detail(enriched, detail), condition)


async def _find_suggestions(
    name_candidates: list[str], condition: str
) -> tuple[list[CardMatch], list[str]]:
    """Cards sharing the scanned name, for when nothing matched outright.

    Rather than dead-ending on "no match found", offer every card carrying the
    name OCR read (or the closest real names to it) so the user can just pick
    theirs. Returns (cards, names_searched).
    """
    names = await carddb.suggest_names(name_candidates)
    if not names:
        names = [n for n in name_candidates[:1] if n]
    if not names:
        return [], []

    seen: set[str] = set()
    rows: list[dict] = []
    for name in names:
        for card in await carddb.search_candidates(
            name=name, limit=settings.match_candidate_limit
        ):
            # Keep only cards that really carry the name - the API matches on
            # substrings, so a search for "Lucario" also returns "Lucario GL",
            # which is welcome, but not unrelated cards that merely contain the
            # fragment we searched with.
            if name.lower() not in card.get("name", "").lower():
                continue
            if card["tcg_id"] in seen:
                continue
            seen.add(card["tcg_id"])
            rows.append(card)
        if len(rows) >= settings.suggestion_limit:
            break

    enriched = await carddb.enrich_matches(rows[: settings.suggestion_limit])
    return _build_matches(enriched, condition), names


@router.post("", response_model=ScanResult)
async def scan(
    payload: ScanRequest,
    current_user: User = Depends(get_current_user),
) -> ScanResult:
    # One timer per request, published on a ContextVar so the service layer can
    # record fetch counts and durations without every function growing a
    # parameter. See services/timing.py.
    scan_timer = timing.ScanTimer()
    timing.current_timer.set(scan_timer)

    # --- CPU stage 1: everything the card-database lookup depends on --------
    # Run in a worker thread rather than inline. On the event loop, a scan's
    # several seconds of OCR stall every other request in the process,
    # including the network waits of scans already in flight; off it, the loop
    # keeps driving them. asyncio.to_thread carries the ContextVar across, so
    # the timer above still collects from inside the thread.
    def _primary_cv() -> tuple:
        with scan_timer.stage("decode"):
            image = vision.decode_image(payload.image)

        # 1. Locate & flatten the card. User-supplied corners win over automatic
        # detection - they only get sent after detection already failed.
        with scan_timer.stage("detect"):
            if image is None:
                detection = vision.CardDetection(image=None, detected=False)  # type: ignore[arg-type]
            elif payload.corners:
                detection = vision.warp_with_corners(image, payload.corners)
            else:
                detection = vision.detect_card(image)

        # 2. Read text off the card. OCR returns several name candidates; which
        # one is the actual card name is decided in step 5 against the real
        # name index.
        with scan_timer.stage("ocr_text"):
            reading = vision.recognize_text(detection.image)

        # 3. Perceptual hash of the scanned card, for visual candidate
        # re-ranking. Only computed on a successfully detected/warped card -
        # comparing a raw, un-warped photo against clean reference thumbnails
        # isn't meaningful.
        with scan_timer.stage("phash"):
            phash = vision.compute_phash(detection.image) if detection.detected else None
        return image, detection, reading, phash

    image, detection, reading, scan_phash = await asyncio.to_thread(_primary_cv)

    # --- CPU stage 2: independent of the lookup, so it runs alongside it -----
    # Condition, energy type, foil finish and the 1st Edition stamp feed only
    # the price shown and the post-enrichment re-rank. Nothing here decides
    # *which* cards are searched for, so it does not belong on the critical
    # path ahead of the network stage.
    def _secondary_cv() -> tuple:
        with scan_timer.stage("condition"):
            cond = vision.assess_condition(detection.image, detection.detected)

        # Energy type from the frame colour, gated on detection: sampling fixed
        # regions of a card that was never located would read the photo's
        # background, not the frame.
        with scan_timer.stage("type"):
            card_type = (
                vision.detect_type(detection.image)
                if detection.detected and settings.enable_type_detection
                else vision.TypeReading(types=[], confidence=0.0)
            )

        # Print variant: foil texture and the 1st Edition stamp. Same gating -
        # a card that was never located has no frame to sample texture from,
        # and detect_first_edition already no-ops on its own when OCR is
        # unavailable.
        with scan_timer.stage("finish"):
            finish = (
                vision.detect_finish(detection.image)
                if detection.detected and settings.enable_finish_detection
                else vision.FinishReading(is_foil=None, confidence=0.0)
            )
        with scan_timer.stage("first_edition"):
            is_first_edition = (
                vision.detect_first_edition(detection.image) if detection.detected else None
            )
        return cond, card_type, finish, is_first_edition

    # 5. Resolve the card identity against the card database.
    if payload.name_hint:
        name: str | None = payload.name_hint
        name_candidates = [payload.name_hint]
    else:
        name_candidates = reading.name_candidates
        with scan_timer.stage("resolve_name"):
            name, _score = await carddb.resolve_name(name_candidates)
    number = payload.number_hint or reading.number

    # The long network stage and the independent CV work run together. gather
    # rather than a bare create_task: it ties the two lifetimes together, so a
    # failure on either side cannot leave the other orphaned.
    with scan_timer.stage("find_matches"):
        enriched, (cond, card_type, finish, is_first_edition) = await asyncio.gather(
            _search_and_enrich(name, number, scan_phash),
            asyncio.to_thread(_secondary_cv),
        )

    # Both halves are in: fold the CV signals into the enriched rows exactly
    # where they were folded in before - after enrichment, never before it.
    condition_estimate = ConditionEstimate(
        condition=cond.condition,
        confidence=cond.confidence,
        is_potentially_damaged=cond.is_potentially_damaged,
        notes=cond.notes,
    )
    detail = _detail_signals(reading, card_type, finish, is_first_edition)
    matches = _build_matches(_apply_detail(enriched, detail), cond.condition)

    # A read collector number identifies one printing exactly, so if the name
    # search missed it - or there was no usable name at all - look it up
    # directly and fold it in. This covers both "OCR read 'Dragonite 9/165' but
    # the name pool lacked the Expedition printing" and "the name was
    # unreadable but the number wasn't".
    if number and (not matches or matches[0].confidence < 0.95):
        with scan_timer.stage("number_lookup"):
            matches = await _merge_number_lookup(
                matches, name, number, cond.condition, scan_phash, detail
            )

    # 6. No confident identity? Fall back to "cards with this name" rather than
    # leaving the user with nothing to pick.
    suggestions: list[CardMatch] = []
    suggested_names: list[str] = []
    if not matches and name_candidates:
        with scan_timer.stage("suggestions"):
            suggestions, suggested_names = await _find_suggestions(
                name_candidates, cond.condition
            )

    if matches:
        top = matches[0]
        read = f" (read {number})" if number else ""
        if top.confidence >= 0.9 and number:
            message = (
                f"Matched {top.name} — {top.set_name} #{top.number}{read}. "
                "Pick another below if that's not it."
            )
        elif top.confidence >= 0.7:
            message = f"Best guess: {top.name}{read}. Confirm or pick another match below."
        else:
            message = (
                f"Possible match: {top.name}{read}, but confidence is low. "
                "Double-check against the matches below."
            )
    elif image is None:
        message = "Could not read the image. Try again or search by name."
    elif suggestions:
        names = ", ".join(f"“{n}”" for n in suggested_names)
        message = (
            f"Read '{name}' but couldn't confirm the exact printing. "
            f"Here are the cards named {names} — pick yours below."
        )
    elif name:
        message = (
            f"Read '{name}' but found no matching card. "
            "Try re-scanning or search by name."
        )
    else:
        message = (
            "Couldn't read the card text clearly. Improve lighting and framing, "
            "or search by name."
        )

    scan_timer.count("matches", len(matches))
    logger.info(
        "scan detected=%s %s", detection.detected, scan_timer.summary()
    )

    return ScanResult(
        matches=matches,
        suggestions=suggestions,
        suggested_names=suggested_names,
        condition=condition_estimate,
        recognized_text=reading.lines,
        recognized_number=number,
        card_detected=detection.detected,
        message=message,
        # Off by default: this is a diagnostic, not part of the contract.
        timings=scan_timer.as_dict() if settings.debug_timings else None,
    )


async def _merge_number_lookup(
    matches: list[CardMatch],
    name: str | None,
    number: str | None,
    condition: str,
    scan_phash: str | None,
    detail: "DetailSignals | None" = None,
) -> list[CardMatch]:
    """Fold a direct collector-number lookup into an existing match list.

    Searching by localId finds every card printed at that index across all sets,
    so the results are narrowed before ranking:

    - With a name, to cards carrying it - an unrelated Pokemon that happens to
      be card 9 of a 165-card set is not a match.
    - Without one (OCR could not read the name, or it resolved to nothing), to
      cards whose index *and* set size both match. That pair is on its own a
      complete identification - there is exactly one card 9 in a given 165-card
      set - so it can rescue a scan whose name was unreadable.

    Only the genuinely new cards are enriched, and existing matches keep their
    scores, so this can add the right printing but never remove one.
    """
    try:
        pool = await carddb.search_candidates(
            number=number, limit=settings.match_candidate_limit
        )
    except Exception:
        return matches

    set_total = _set_total_of(number)
    known = {m.tcg_id for m in matches}
    lowered = (name or "").lower()
    fresh = []
    for c in pool:
        if c["tcg_id"] in known:
            continue
        if lowered:
            if lowered not in c.get("name", "").lower():
                continue
        elif set_total is None or c.get("set_total") != set_total:
            # No name to corroborate with, so demand an exact set-size match.
            continue
        fresh.append(c)
    if not fresh:
        return matches

    ranked = await imagematch.rank_candidates(
        fresh,
        ocr_name=name,
        ocr_number=number,
        ocr_set_total=_set_total_of(number),
        scan_phash=scan_phash if settings.enable_visual_rerank else None,
    )
    enriched = _apply_detail(await carddb.enrich_matches(ranked), detail)
    merged = matches + _build_matches(enriched, condition)
    merged.sort(key=lambda m: m.confidence, reverse=True)
    return merged[: settings.match_final_limit]


@router.get("/search", response_model=list[CardMatch])
async def search(
    name: str | None = None,
    number: str | None = None,
    condition: str = "Near Mint",
    current_user: User = Depends(get_current_user),
) -> list[CardMatch]:
    """Manual lookup by name/number — used as a fallback when OCR is unclear.

    A number alongside the name ("Lucario 67") is a ranking signal, not a
    filter: every Lucario is still listed, but the one printed at that index
    comes first. If the name pool was too large to contain it, the number is
    also looked up directly so the printing the user asked for is present.
    """
    matches = await _find_matches(name, number, condition, scan_phash=None)
    if number and name and (not matches or matches[0].confidence < 0.95):
        matches = await _merge_number_lookup(
            matches, name, number, condition, scan_phash=None
        )
    return matches
