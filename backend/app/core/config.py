"""Backend settings.

Reuses the project's existing `.env` (loaded once via python-dotenv at process
startup) — PG_*/GROQ_API_KEY/etc. stay the single source of truth; this module
only adds the handful of settings that are new for the API layer (JWT, CORS
origin).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    jwt_secret_key: str = field(
        default_factory=lambda: os.environ.get("JWT_SECRET_KEY", "dev-insecure-secret-change-me")
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = field(
        default_factory=lambda: int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    )
    jwt_refresh_token_expire_days: int = field(
        default_factory=lambda: int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))
    )
    frontend_origin: str = field(
        default_factory=lambda: os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
    )

    @property
    def frontend_origins(self) -> list[str]:
        """Every allowed CORS origin. ``FRONTEND_ORIGIN`` accepts a
        comma-separated list so serving the UI on a second host/port (a LAN IP
        for a phone/demo, say) doesn't require a code change — a browser whose
        origin isn't allowed has *every* API call blocked, which surfaces in the
        UI only as an opaque "Failed to fetch".
        """
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
