"""Shared pytest configuration.

``@pytest.mark.integration`` marks tests that need the live docker-compose
Postgres instance (see docker-compose.yml) — they're skipped automatically
when it's unreachable, so the rest of the suite stays hermetic and fast.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: needs the live docker-compose Postgres instance"
    )


def _postgres_reachable() -> bool:
    try:
        from db.session import get_engine

        engine = get_engine()
        with engine.connect():
            pass
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _postgres_reachable():
        return
    skip_marker = pytest.mark.skip(reason="docker-compose Postgres not reachable")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
