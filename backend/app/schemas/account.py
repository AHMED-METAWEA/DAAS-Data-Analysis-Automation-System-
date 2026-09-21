from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    organization: str | None = None
    timezone: str | None = None
    bio: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ApiKeyCreatedOut(ApiKeyOut):
    # The full key, in cleartext — returned ONLY here, at creation time.
    # Never stored or shown again; only `prefix` is retained after this.
    key: str
