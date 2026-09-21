"""Encrypted storage for Path B (existing database) and Path C (Google
Sheet) connection credentials — Stage 11.

Fernet-encrypted at rest with a ``FERNET_KEY`` env var. This is
proportionate for a single-dev local tool, not real hardening: no key
rotation/versioning, and losing ``FERNET_KEY`` means re-entering
credentials — that trade-off is documented here, not oversold as a real
security feature.
"""

from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from db.platform_models import ConnectionConfig
from db.session import get_session


class FernetKeyMissingError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY")
    if not key:
        raise FernetKeyMissingError(
            "FERNET_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"\n"
            "and add it to your .env file."
        )
    return Fernet(key.encode())


def encrypt_credentials(credentials: dict) -> str:
    return _fernet().encrypt(json.dumps(credentials).encode()).decode()


def decrypt_credentials(token: str) -> dict:
    try:
        payload = _fernet().decrypt(token.encode())
    except InvalidToken as exc:
        raise ValueError(
            "Could not decrypt stored credentials — wrong or rotated FERNET_KEY?"
        ) from exc
    return json.loads(payload.decode())


def save_connection_config(
    project_id: str, source_type: str, credentials: dict, extra: dict | None = None,
) -> ConnectionConfig:
    encrypted = encrypt_credentials(credentials)
    with get_session() as session:
        config = ConnectionConfig(
            project_id=project_id,
            source_type=source_type,
            encrypted_credentials=encrypted,
            extra=extra or {},
        )
        session.add(config)
        session.commit()
        session.refresh(config)
        return config


def load_connection_config(config_id: str) -> tuple[ConnectionConfig, dict]:
    with get_session() as session:
        config = session.get(ConnectionConfig, config_id)
        if config is None:
            raise ValueError(f"No connection config found with id '{config_id}'")
        return config, decrypt_credentials(config.encrypted_credentials)


def list_connection_configs(
    project_id: str, source_type: str | None = None,
) -> list[ConnectionConfig]:
    with get_session() as session:
        stmt = select(ConnectionConfig).where(ConnectionConfig.project_id == project_id)
        if source_type:
            stmt = stmt.where(ConnectionConfig.source_type == source_type)
        return list(session.scalars(stmt))
