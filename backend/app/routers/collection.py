"""Routes for a user's saved card collection."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import CollectionCard, User
from ..schemas import (
    CollectionCardCreate,
    CollectionCardOut,
    CollectionCardUpdate,
)

router = APIRouter(prefix="/api/collection", tags=["collection"])


@router.get("", response_model=list[CollectionCardOut])
def list_cards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CollectionCard]:
    stmt = (
        select(CollectionCard)
        .where(CollectionCard.owner_id == current_user.id)
        .order_by(CollectionCard.created_at.desc())
    )
    return list(db.scalars(stmt).all())


def _find_duplicate(
    payload: CollectionCardCreate, user: User, db: Session
) -> CollectionCard | None:
    """Find an existing entry for the same printing in the same condition.

    Collectors think in "I own 3 of these", not three identical rows, so saving
    a card already in the collection bumps its quantity instead. Matches on the
    TCG id when we have one (the precise printing), otherwise falls back to the
    name/set/number triple for manually-added cards.
    """
    stmt = select(CollectionCard).where(
        CollectionCard.owner_id == user.id,
        CollectionCard.condition == payload.condition,
    )
    if payload.tcg_id:
        stmt = stmt.where(CollectionCard.tcg_id == payload.tcg_id)
    else:
        stmt = stmt.where(
            CollectionCard.tcg_id == "",
            CollectionCard.name == payload.name,
            CollectionCard.set_name == payload.set_name,
            CollectionCard.number == payload.number,
        )
    return db.scalars(stmt).first()


@router.post("", response_model=CollectionCardOut, status_code=status.HTTP_201_CREATED)
def add_card(
    payload: CollectionCardCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CollectionCard:
    existing = _find_duplicate(payload, current_user, db)
    if existing is not None:
        existing.quantity += payload.quantity
        # Refresh the price - the market moves between scans.
        if payload.market_price is not None:
            existing.market_price = payload.market_price
            existing.currency = payload.currency
        db.commit()
        db.refresh(existing)
        return existing

    card = CollectionCard(owner_id=current_user.id, **payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def _get_owned_card(card_id: int, user: User, db: Session) -> CollectionCard:
    card = db.get(CollectionCard, card_id)
    if card is None or card.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Card not found."
        )
    return card


@router.patch("/{card_id}", response_model=CollectionCardOut)
def update_card(
    card_id: int,
    payload: CollectionCardUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CollectionCard:
    card = _get_owned_card(card_id, current_user, db)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(card, key, value)
    db.commit()
    db.refresh(card)
    return card


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(
    card_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    card = _get_owned_card(card_id, current_user, db)
    db.delete(card)
    db.commit()
