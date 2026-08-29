"""Database models: users and the cards they save to their collection."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    cards: Mapped[list["CollectionCard"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class CollectionCard(Base):
    """A single card a user has scanned and saved to their personal collection."""

    __tablename__ = "collection_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    # Identity of the card
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    set_name: Mapped[str] = mapped_column(String(255), default="")
    number: Mapped[str] = mapped_column(String(50), default="")
    rarity: Mapped[str] = mapped_column(String(120), default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    tcg_id: Mapped[str] = mapped_column(String(120), default="")  # pokemontcg.io card id

    # Valuation (Cardmarket, via pokemontcg.io) — an approximate market figure.
    market_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="EUR")

    # Condition — a Cardmarket-style grade, either auto-estimated or user-set.
    condition: Mapped[str] = mapped_column(String(40), default="Near Mint")
    condition_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    damage_notes: Mapped[str] = mapped_column(Text, default="")

    # How many copies of this exact card (same printing + condition) are owned.
    # Saving the same card again increments this rather than adding a duplicate row.
    quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    owner: Mapped["User"] = relationship(back_populates="cards")
