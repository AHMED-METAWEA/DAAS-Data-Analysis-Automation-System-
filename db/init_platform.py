"""Idempotent bootstrap for the platform metadata tables.

Called once on app startup. Safe to call repeatedly (create_all only creates
tables that don't already exist).
"""

from __future__ import annotations

from db import auth_models  # noqa: F401  (registers models on Base.metadata)
from db import crm_models  # noqa: F401  (registers models on Base.metadata)
from db import monitoring_models  # noqa: F401  (registers models on Base.metadata)
from db import platform_models  # noqa: F401  (registers models on Base.metadata)
from db import report_models  # noqa: F401  (registers models on Base.metadata)
from db import usage_models  # noqa: F401  (registers models on Base.metadata)
from db.base import Base
from db.session import get_engine


def ensure_platform_tables() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
