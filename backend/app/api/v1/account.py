from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, get_db
from backend.app.core.security import hash_password, verify_password
from backend.app.schemas.account import (
    ApiKeyCreatedOut,
    ApiKeyCreateRequest,
    ApiKeyOut,
    ChangePasswordRequest,
    UpdateProfileRequest,
)
from backend.app.schemas.auth import UserOut
from db.auth_models import ApiKey, User

router = APIRouter(prefix="/account", tags=["account"])


def _generate_api_key() -> tuple[str, str]:
    """Returns (full_key, prefix). Only the prefix is ever stored in the clear."""
    full_key = f"sa_live_{secrets.token_urlsafe(32)}"
    return full_key, full_key[:12]


@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, current_user.id)
    for field in ("name", "role", "organization", "timezone", "bio"):
        value = getattr(payload, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    user = db.get(User, current_user.id)
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    user.hashed_password = hash_password(payload.new_password)
    db.commit()


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ApiKey]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(db.scalars(stmt))


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedOut:
    full_key, prefix = _generate_api_key()
    key = ApiKey(
        user_id=current_user.id, name=payload.name, prefix=prefix,
        hashed_key=hash_password(full_key),
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return ApiKeyCreatedOut(
        id=key.id, name=key.name, prefix=key.prefix, revoked=key.revoked,
        created_at=key.created_at, last_used_at=key.last_used_at, key=full_key,
    )


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API key not found")
    key.revoked = True
    db.commit()
