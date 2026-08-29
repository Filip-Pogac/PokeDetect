"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---- Auth ----
class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- Prices / conditions ----
class ConditionEstimate(BaseModel):
    condition: str
    confidence: float
    is_potentially_damaged: bool
    notes: list[str]


class PriceInfo(BaseModel):
    market_price: float | None = None
    currency: str = "EUR"
    # A condition-adjusted estimate derived from the market price.
    estimated_price: float | None = None
    source: str = "Cardmarket (via pokemontcg.io)"
    disclaimer: str


# ---- Card matches from a scan ----
class CardMatch(BaseModel):
    tcg_id: str
    name: str
    set_name: str = ""
    number: str = ""
    rarity: str = ""
    image_url: str = ""
    price: PriceInfo
    confidence: float = 0.0  # 0-1 blended text+visual match confidence


class ScanResult(BaseModel):
    matches: list[CardMatch]
    condition: ConditionEstimate
    recognized_text: list[str]
    card_detected: bool
    message: str = ""


# ---- Collection ----
class CollectionCardCreate(BaseModel):
    name: str
    set_name: str = ""
    number: str = ""
    rarity: str = ""
    image_url: str = ""
    tcg_id: str = ""
    market_price: float | None = None
    currency: str = "EUR"
    condition: str = "Near Mint"
    condition_confidence: float | None = None
    damage_notes: str = ""
    quantity: int = Field(default=1, ge=1)


class CollectionCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    set_name: str
    number: str
    rarity: str
    image_url: str
    tcg_id: str
    market_price: float | None
    currency: str
    condition: str
    condition_confidence: float | None
    damage_notes: str
    quantity: int
    created_at: datetime


class CollectionCardUpdate(BaseModel):
    condition: str | None = None
    damage_notes: str | None = None
    quantity: int | None = Field(default=None, ge=1)
