"""CRM — the stateful customer layer.

Layering, and the rule that keeps it clean:

    contracts.py   vocabulary + the CustomerRecord dataclass + the PII boundary
    snapshot.py    pure computation:  DataFrame -> list[CustomerRecord]
    repository.py  persistence:       CustomerRecord <-> Postgres
    service.py     orchestration:     project_id -> refresh -> stored snapshot

``snapshot.py`` never touches the database and ``repository.py`` never computes
anything. That separation is what makes the compute testable without Postgres
and the persistence testable without a dataset, and it is why this package does
not follow ``agents/churn/storage.py``, where the DDL, the computation and the
transaction are interleaved in one function.

Nothing in this package imports FastAPI. The HTTP layer lives in
``backend/app/api/v1/crm.py`` and depends on this package, never the reverse.
"""

from agents.crm.contracts import (
    CustomerRecord,
    SnapshotResult,
    strip_pii,
)
from agents.crm.service import get_latest_snapshot, refresh_customer_state

__all__ = [
    "CustomerRecord",
    "SnapshotResult",
    "strip_pii",
    "refresh_customer_state",
    "get_latest_snapshot",
]
