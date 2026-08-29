"""Scan route: image in -> recognized card matches + condition estimate."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..models import User
from ..schemas import (
    CardMatch,
    ConditionEstimate,
    PriceInfo,
    ScanResult,
)
from ..services import pokemontcg, vision

router = APIRouter(prefix="/api/scan", tags=["scan"])


class ScanRequest(BaseModel):
    image: str  # data URL or base64-encoded JPEG/PNG
    # Optional manual hints if OCR struggles.
    name_hint: str | None = None
    number_hint: str | None = None


def _price_info(market_price: float | None, currency: str, condition: str) -> PriceInfo:
    estimated = None
    if market_price is not None:
        multiplier = pokemontcg.CONDITION_MULTIPLIERS.get(condition, 1.0)
        estimated = round(market_price * multiplier, 2)
    return PriceInfo(
        market_price=market_price,
        currency=currency,
        estimated_price=estimated,
        disclaimer=pokemontcg.PRICE_DISCLAIMER,
    )


@router.post("", response_model=ScanResult)
async def scan(
    payload: ScanRequest,
    current_user: User = Depends(get_current_user),
) -> ScanResult:
    image = vision.decode_image(payload.image)

    # 1. Locate & flatten the card.
    detection = vision.detect_card(image) if image is not None else vision.CardDetection(
        image=None, detected=False  # type: ignore[arg-type]
    )

    # 2. Read text off the card.
    lines, ocr_name, ocr_number = vision.recognize_text(detection.image)

    # 3. Estimate condition/damage.
    cond = vision.assess_condition(detection.image, detection.detected)
    condition_estimate = ConditionEstimate(
        condition=cond.condition,
        confidence=cond.confidence,
        is_potentially_damaged=cond.is_potentially_damaged,
        notes=cond.notes,
    )

    # 4. Resolve the card identity against the Pokemon TCG database.
    name = payload.name_hint or ocr_name
    number = payload.number_hint or ocr_number
    raw_matches = await pokemontcg.search_cards(name=name, number=number)

    matches: list[CardMatch] = [
        CardMatch(
            tcg_id=m["tcg_id"],
            name=m["name"],
            set_name=m["set_name"],
            number=m["number"],
            rarity=m["rarity"],
            image_url=m["image_url"],
            price=_price_info(m["market_price"], m["currency"], cond.condition),
        )
        for m in raw_matches
    ]

    if matches:
        message = f"Best guess: {matches[0].name}. Confirm or pick another match below."
    elif not image:
        message = "Could not read the image. Try again or search by name."
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

    return ScanResult(
        matches=matches,
        condition=condition_estimate,
        recognized_text=lines,
        card_detected=detection.detected,
        message=message,
    )


@router.get("/search", response_model=list[CardMatch])
async def search(
    name: str | None = None,
    number: str | None = None,
    condition: str = "Near Mint",
    current_user: User = Depends(get_current_user),
) -> list[CardMatch]:
    """Manual lookup by name/number — used as a fallback when OCR is unclear."""
    raw_matches = await pokemontcg.search_cards(name=name, number=number)
    return [
        CardMatch(
            tcg_id=m["tcg_id"],
            name=m["name"],
            set_name=m["set_name"],
            number=m["number"],
            rarity=m["rarity"],
            image_url=m["image_url"],
            price=_price_info(m["market_price"], m["currency"], condition),
        )
        for m in raw_matches
    ]
