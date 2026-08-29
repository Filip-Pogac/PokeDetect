"""Client for the Pokemon TCG API (https://pokemontcg.io).

This is the card database used for recognition matching. Crucially it also
carries Cardmarket price data per card, which we surface to the user.
"""
from __future__ import annotations

import re

import httpx

from ..config import get_settings

settings = get_settings()

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


def _build_query(name: str | None, number: str | None) -> str:
    parts: list[str] = []
    if name:
        # Escape quotes; wildcard match so partial OCR still hits.
        cleaned = re.sub(r'["\\]', "", name).strip()
        if cleaned:
            parts.append(f'name:"{cleaned}*"')
    if number:
        num = number.split("/")[0].strip()
        if num:
            parts.append(f"number:{num}")
    return " ".join(parts)


def _extract_cardmarket_price(card: dict) -> tuple[float | None, str]:
    """Return (price, currency). Prefers trend price, falls back to average."""
    cm = card.get("cardmarket") or {}
    prices = cm.get("prices") or {}
    for key in ("trendPrice", "averageSellPrice", "avg7", "avg30"):
        value = prices.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value), "EUR"
    # Fall back to TCGplayer (USD) if Cardmarket is missing.
    tcg = card.get("tcgplayer") or {}
    tcg_prices = tcg.get("prices") or {}
    for variant in tcg_prices.values():
        if isinstance(variant, dict):
            market = variant.get("market") or variant.get("mid")
            if isinstance(market, (int, float)) and market > 0:
                return float(market), "USD"
    return None, "EUR"


def _simplify(card: dict) -> dict:
    price, currency = _extract_cardmarket_price(card)
    images = card.get("images") or {}
    set_info = card.get("set") or {}
    return {
        "tcg_id": card.get("id", ""),
        "name": card.get("name", ""),
        "set_name": set_info.get("name", ""),
        "number": card.get("number", ""),
        "rarity": card.get("rarity", ""),
        "image_url": images.get("small") or images.get("large") or "",
        "market_price": price,
        "currency": currency,
    }


async def search_cards(
    name: str | None = None, number: str | None = None, limit: int = 8
) -> list[dict]:
    """Search cards by (partial) name and/or collector number."""
    query = _build_query(name, number)
    if not query:
        return []

    headers = {}
    if settings.pokemontcg_api_key:
        headers["X-Api-Key"] = settings.pokemontcg_api_key

    params = {
        "q": query,
        "page": 1,
        "pageSize": limit,
        "orderBy": "-set.releaseDate",
    }
    url = f"{settings.pokemontcg_base_url}/cards"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    cards = data.get("data") or []
    return [_simplify(c) for c in cards]
