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


@router.post("", response_model=CollectionCardOut, status_code=status.HTTP_201_CREATED)
def add_card(
    payload: CollectionCardCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CollectionCard:
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
