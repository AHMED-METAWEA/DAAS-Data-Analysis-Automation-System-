"""CRM persistence round trip against the live docker-compose Postgres.

Marked ``integration`` — skipped automatically when Postgres is unreachable
(see tests/conftest.py), so the hermetic suite stays fast.

Each test creates and tears down its own throwaway project, because the
properties under test (idempotency, one row per customer-day, orphan-free
replacement) are only meaningful on data the test fully controls.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from agents.crm import repository
from agents.crm.contracts import CustomerRecord
from db.session import get_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def project_id():
    """A bare project row — enough to satisfy the customer_state foreign key."""
    pid = uuid.uuid4().hex
    slug = f"crmtest_{pid[:8]}"
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, name, slug, data_source_mode, status, "
                "created_at, updated_at) VALUES (:id, :name, :slug, 'files', 'active', "
                "NOW(), NOW())"
            ),
            {"id": pid, "name": f"CRM Test {pid[:6]}", "slug": slug},
        )
    yield pid
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_state WHERE project_id=:p"), {"p": pid})
        conn.execute(text("DELETE FROM crm_snapshots WHERE project_id=:p"), {"p": pid})
        conn.execute(text("DELETE FROM projects WHERE id=:p"), {"p": pid})


def _records(day: date, n: int = 5, *, churn_base: float = 0.5) -> list[CustomerRecord]:
    return [
        CustomerRecord(
            customer_id=f"C{i:03d}",
            snapshot_date=day,
            display_name=f"Person {i}",
            contact={"email": f"p{i}@example.com"},
            recency_days=i * 3,
            frequency=10 - i,
            monetary=1000.0 - (i * 50),
            tenure_days=400,
            avg_order_value=100.0,
            rfm_segment="Champions" if i < 2 else "At Risk",
            lifecycle_stage="Established" if i < 2 else "Dormant",
            churn_probability=min(0.99, churn_base + i * 0.05),
        ).with_value_at_risk()
        for i in range(n)
    ]


_META = {
    "grain": {"customer_column": "customer_id", "time_column": "order_date"},
    "components": {"rfm": {"status": "ok"}, "churn": {"status": "ok"},
                   "clv": {"status": "pending"}},
    "row_count": 500,
    "history_days": 400,
    "stage_rules": {"cadence_days": 40.0, "cadence_basis": "median_interpurchase_days"},
}


def _count(sql: str, pid: str) -> int:
    with get_engine().connect() as conn:
        return conn.execute(text(sql), {"p": pid}).scalar()


# ── Idempotency ─────────────────────────────────────────────────────────────

def test_rerunning_the_same_date_replaces_rather_than_duplicating(project_id):
    """A double-click must not fork the customer's timeline."""
    day = date(2025, 5, 31)

    first = repository.save_snapshot(project_id, _records(day, 5), _META)
    assert first.status == "success"

    second = repository.save_snapshot(project_id, _records(day, 5, churn_base=0.9), _META)
    assert second.status == "success"
    assert second.snapshot_id != first.snapshot_id

    assert _count("SELECT count(*) FROM crm_snapshots WHERE project_id=:p", project_id) == 1
    assert _count("SELECT count(*) FROM customer_state WHERE project_id=:p", project_id) == 5
    # The second write's values won, not the first's.
    with get_engine().connect() as conn:
        prob = conn.execute(
            text("SELECT churn_probability FROM customer_state "
                 "WHERE project_id=:p AND customer_id='C000'"),
            {"p": project_id},
        ).scalar()
    assert prob == pytest.approx(0.9)


def test_replacement_leaves_no_orphaned_state_rows(project_id):
    """Delete-and-insert must be one transaction, not two half-applied ones."""
    day = date(2025, 5, 31)
    repository.save_snapshot(project_id, _records(day, 5), _META)
    repository.save_snapshot(project_id, _records(day, 3), _META)

    orphans = _count(
        "SELECT count(*) FROM customer_state cs LEFT JOIN crm_snapshots s "
        "ON s.id = cs.snapshot_id WHERE cs.project_id=:p AND s.id IS NULL",
        project_id,
    )
    assert orphans == 0
    assert _count("SELECT count(*) FROM customer_state WHERE project_id=:p", project_id) == 3


def test_different_dates_accumulate_into_a_timeline(project_id):
    for offset in range(3):
        day = date(2025, 5, 1) + timedelta(days=offset)
        repository.save_snapshot(project_id, _records(day, 4, churn_base=0.3 + offset * 0.2), _META)

    assert _count("SELECT count(*) FROM crm_snapshots WHERE project_id=:p", project_id) == 3
    assert _count("SELECT count(*) FROM customer_state WHERE project_id=:p", project_id) == 12

    timeline = repository.get_customer_timeline(project_id, "C000")
    assert [point.snapshot_date for point in timeline] == [
        date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 3)
    ]
    # Oldest first, so a chart can be drawn straight from it.
    assert timeline[0].churn_probability < timeline[-1].churn_probability


# ── Failure is total, never raised ──────────────────────────────────────────

def test_empty_records_skip_with_a_reason(project_id):
    result = repository.save_snapshot(project_id, [], {"reason": "No customer column."})
    assert result.status == "skipped"
    assert result.reason == "No customer column."
    assert not result.ok


def test_partial_status_when_a_component_was_skipped(project_id):
    meta = {**_META, "components": {"rfm": {"status": "ok"},
                                    "churn": {"status": "skipped", "reason": "no model"},
                                    "clv": {"status": "pending"}}}
    result = repository.save_snapshot(project_id, _records(date(2025, 5, 31)), meta)

    assert result.status == "partial"
    assert result.ok, "a partial snapshot is still usable and must not read as failure"


def test_unknown_project_fails_without_raising():
    """A storage failure must return, not propagate into a live analysis."""
    result = repository.save_snapshot(
        "no_such_project_" + "0" * 16, _records(date(2025, 5, 31)), _META
    )
    assert result.status == "failed"
    assert result.reason


# ── Read paths ──────────────────────────────────────────────────────────────

def test_list_defaults_to_the_latest_snapshot(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 1), 4), _META)
    repository.save_snapshot(project_id, _records(date(2025, 6, 1), 6), _META)

    rows, total = repository.list_customers(project_id)
    assert total == 6
    assert all(row.snapshot_date == date(2025, 6, 1) for row in rows)


def test_value_at_risk_sort_puts_nulls_last(project_id):
    """The least-known customers must not head a list that ranks by what is known."""
    day = date(2025, 5, 31)
    records = _records(day, 3)
    unknown = CustomerRecord(customer_id="ZZZ", snapshot_date=day, monetary=None,
                             churn_probability=None).with_value_at_risk()
    assert unknown.value_at_risk is None
    repository.save_snapshot(project_id, [*records, unknown], _META)

    rows, _ = repository.list_customers(project_id, sort_by="value_at_risk", descending=True)
    assert rows[0].value_at_risk is not None
    assert rows[-1].customer_id == "ZZZ"


def test_unknown_sort_key_falls_back_instead_of_injecting(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 31), 4), _META)

    rows, total = repository.list_customers(project_id, sort_by="'; DROP TABLE customer_state--")
    assert total == 4
    assert len(rows) == 4
    # The table is still there.
    assert _count("SELECT count(*) FROM customer_state WHERE project_id=:p", project_id) == 4


def test_filters_and_pagination(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 31), 5), _META)

    champions, n = repository.list_customers(project_id, segment="Champions")
    assert n == 2 and all(r.rfm_segment == "Champions" for r in champions)

    dormant, n = repository.list_customers(project_id, stage="Dormant")
    assert n == 3

    page1, total = repository.list_customers(project_id, limit=2, offset=0)
    page2, _ = repository.list_customers(project_id, limit=2, offset=2)
    assert total == 5
    assert {r.customer_id for r in page1} & {r.customer_id for r in page2} == set()


def test_search_matches_id_or_display_name(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 31), 5), _META)

    by_id, n_id = repository.list_customers(project_id, search="C001")
    assert n_id == 1 and by_id[0].customer_id == "C001"

    by_name, n_name = repository.list_customers(project_id, search="person 2")
    assert n_name == 1 and by_name[0].display_name == "Person 2"


def test_portfolio_summary_reports_coverage_and_basis(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 31), 5), _META)

    summary = repository.portfolio_summary(project_id)
    assert summary["available"]
    assert summary["customers"] == 5
    assert summary["customers_scored"] == 5
    assert summary["customers_with_clv"] == 0
    assert summary["value_basis"] == {"historical_monetary": 5}
    assert summary["by_segment"] == {"Champions": 2, "At Risk": 3}


def test_portfolio_reports_null_not_zero_when_money_is_unmeasurable(project_id):
    """"EGP 0.00" reads as a measurement; null reads as "not measurable"."""
    day = date(2025, 5, 31)
    moneyless = [
        CustomerRecord(customer_id=f"C{i}", snapshot_date=day, monetary=None,
                       churn_probability=0.5).with_value_at_risk()
        for i in range(3)
    ]
    repository.save_snapshot(project_id, moneyless, _META)

    summary = repository.portfolio_summary(project_id)
    assert summary["total_monetary"] is None
    assert summary["total_value_at_risk"] is None
    assert summary["customers_with_monetary"] == 0
    assert summary["customers_scored"] == 3


def test_portfolio_without_a_snapshot_is_unavailable(project_id):
    summary = repository.portfolio_summary(project_id)
    assert summary["available"] is False
    assert summary["reason"]


def test_get_customer_returns_the_most_recent_state(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 1), 3, churn_base=0.2), _META)
    repository.save_snapshot(project_id, _records(date(2025, 6, 1), 3, churn_base=0.8), _META)

    row = repository.get_customer(project_id, "C000")
    assert row.snapshot_date == date(2025, 6, 1)
    assert row.churn_probability == pytest.approx(0.8)

    older = repository.get_customer(project_id, "C000", snapshot_date=date(2025, 5, 1))
    assert older.churn_probability == pytest.approx(0.2)


def test_pii_is_stored_for_the_browser_but_strippable_for_prompts(project_id):
    repository.save_snapshot(project_id, _records(date(2025, 5, 31), 2), _META)

    row = repository.get_customer(project_id, "C000")
    assert row.display_name == "Person 0"
    assert row.contact == {"email": "p0@example.com"}

    from agents.crm.contracts import strip_pii

    safe = strip_pii({"display_name": row.display_name, "contact": row.contact,
                      "customer_id": row.customer_id})
    assert safe == {"customer_id": "C000"}
