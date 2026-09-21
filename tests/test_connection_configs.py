from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.connection_configs as connection_configs
import db.projects as projects
from db import platform_models  # noqa: F401 (registers models on Base.metadata)
from db.base import Base


@pytest.fixture()
def fernet_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", key)
    return key


@pytest.fixture()
def sqlite_session_factory(monkeypatch):
    """Fast, hermetic round-trip tests against an in-memory sqlite DB —
    same pattern as tests/test_relationships.py."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(connection_configs, "get_session", lambda: factory())
    monkeypatch.setattr(projects, "get_session", lambda: factory())
    return factory


class TestEncryptDecrypt:
    def test_round_trip(self, fernet_key) -> None:
        creds = {"host": "db.example.com", "password": "hunter2"}
        token = connection_configs.encrypt_credentials(creds)
        assert token != str(creds)
        assert "hunter2" not in token
        assert connection_configs.decrypt_credentials(token) == creds

    def test_missing_fernet_key_raises_clear_error(self, monkeypatch) -> None:
        monkeypatch.delenv("FERNET_KEY", raising=False)
        with pytest.raises(connection_configs.FernetKeyMissingError):
            connection_configs.encrypt_credentials({"a": 1})

    def test_wrong_key_fails_to_decrypt(self, fernet_key, monkeypatch) -> None:
        token = connection_configs.encrypt_credentials({"a": 1})
        monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
        with pytest.raises(ValueError, match="Could not decrypt"):
            connection_configs.decrypt_credentials(token)


class TestSaveLoadConnectionConfig:
    def test_round_trip(self, fernet_key, sqlite_session_factory) -> None:
        project = projects.create_project("DB Link Test Project")
        creds = {"host": "db.example.com", "port": 5432, "password": "s3cret"}

        saved = connection_configs.save_connection_config(project.id, "postgres", creds)
        config, decrypted = connection_configs.load_connection_config(saved.id)

        assert decrypted == creds
        assert config.source_type == "postgres"

    def test_list_filters_by_project_and_source_type(self, fernet_key, sqlite_session_factory) -> None:
        project = projects.create_project("Proj A")
        connection_configs.save_connection_config(project.id, "postgres", {"a": 1})
        connection_configs.save_connection_config(project.id, "google_sheet", {"b": 2})

        pg_configs = connection_configs.list_connection_configs(project.id, source_type="postgres")
        assert len(pg_configs) == 1
        assert pg_configs[0].source_type == "postgres"

        all_configs = connection_configs.list_connection_configs(project.id)
        assert len(all_configs) == 2

    def test_unknown_config_id_raises(self, fernet_key, sqlite_session_factory) -> None:
        with pytest.raises(ValueError, match="No connection config found"):
            connection_configs.load_connection_config("does-not-exist")
