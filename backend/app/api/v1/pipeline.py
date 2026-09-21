from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agents.cleaning.multi_table import clean_tables, key_columns_for_table
from agents.cleaning.planner import review_manual_step
from agents.constants import (
    DEFAULT_CODER_MODEL,
    DEFAULT_PLANNER_MODEL,
    DEFAULT_SCHEMA_DISCOVERY_MODEL,
)
from backend.app.api.deps import get_current_user, get_db
from backend.app.schemas.pipeline import (
    CleanRemainingResponse,
    CleanRequest,
    CleanResponse,
    IntegrityResponse,
    PlanEditRequest,
    PlanRequest,
    PlanResponse,
    PlanStepOpinionRequest,
    PlanStepOpinionResponse,
    ReconciliationCheckOut,
    RelationshipCandidateOut,
    RelationshipDecision,
    RelationshipOut,
    RelationshipsRequest,
    SaveResponse,
    SchemaDiscoveryResponse,
    TableCleaningStatus,
    TableProfileOut,
)
from backend.app.services.json_safe import json_safe
from backend.app.services.pipeline_sessions import (
    PipelineSession,
    PipelineSessionNotFoundError,
    get_session as get_pipeline_session,
)
from backend.app.services.previews import table_preview
from data_manager.manager import invalidate as invalidate_view_cache
from db.auth_models import User
from db.ddl import PrimaryKeyViolationError
from db.loader import load_project_schema
from db.platform_models import Project
from graphs.cleaning_graph import build_cleaning_graph
from graphs.planner_graph import build_planner_graph
from integrity.validation import run_integrity_validation
from models.profiler_models import DatasetProfile
from reconciliation.normalize import normalize_relationship_keys
from reconciliation.orphans import reconcile
from relationships.review import (
    bucket_candidates,
    load_relationships,
    persist_relationships,
    validate_relationship,
)
from schema_discovery.discover import run_schema_discovery
from schema_discovery.models import RelationshipCandidate

router = APIRouter(prefix="/pipeline/{session_id}", tags=["pipeline"])


def _approved_relationship_candidates(project_id: str) -> list[RelationshipCandidate]:
    """Reload this project's approved relationships from storage as
    RelationshipCandidate objects — the shape every downstream engine
    (cleaning, reconciliation, integrity, DDL) expects. The DB row doesn't
    keep the original discovery `source`/`reasoning`, which don't matter
    past this point (only table/column/confidence do)."""
    return [
        RelationshipCandidate(
            table_a=r.table_a, column_a=r.column_a,
            table_b=r.table_b, column_b=r.column_b,
            confidence=r.confidence,
        )
        for r in load_relationships(project_id)
    ]


def _tables_for_storage(
    session: PipelineSession, approved: list[RelationshipCandidate]
) -> dict[str, pd.DataFrame]:
    """The cleaned tables as they will be stored, with every approved
    relationship's key columns canonicalised on BOTH sides, written back to the
    session so integrity and save can never disagree.

    Cleaning is per table, so an LLM plan that standardises casing/whitespace on
    one table's key column but not the other silently turns matching keys into
    orphans — and a generated FOREIGN KEY compares raw values, so Postgres
    rejects the COPY. ``reconcile`` already normalises both sides, but only on
    the "clean remaining tables" path; a project whose tables were each cleaned
    individually never reached it, so the mismatch survived to the save. Doing it
    at the one choke point both endpoints read means the gate validates exactly
    the values Postgres receives. Normalising twice is a no-op, so it is safe to
    call from both.
    """
    tables = {n: s.cleaned_df for n, s in session.cleaning.items() if s.cleaned_df is not None}
    for rel in approved:
        if rel.table_a in tables and rel.table_b in tables:
            tables = normalize_relationship_keys(tables, rel)
    for name, df in tables.items():
        session.cleaning[name].cleaned_df = df
    return tables


def get_owned_pipeline_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PipelineSession:
    try:
        session = get_pipeline_session(session_id)
    except PipelineSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline session not found or expired — please re-upload your data.",
        ) from exc
    project = db.get(Project, session.project_id)
    if project is None or (project.owner_id is not None and project.owner_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your project")
    return session


def _candidate_out(candidate: RelationshipCandidate) -> RelationshipCandidateOut:
    return RelationshipCandidateOut(
        **candidate.model_dump(), validation=validate_relationship(candidate)
    )


@router.post("/schema-discovery", response_model=SchemaDiscoveryResponse)
def discover_schema(
    session: PipelineSession = Depends(get_owned_pipeline_session),
    current_user: User = Depends(get_current_user),
) -> SchemaDiscoveryResponse:
    model = current_user.model_preferences.get("schema_discovery") or DEFAULT_SCHEMA_DISCOVERY_MODEL
    result = run_schema_discovery(session.raw_tables, model=model)
    if session.known_relationships:
        # Trust hierarchy: existing DB constraints > heuristic > LLM. Drop any
        # heuristic/LLM candidate that duplicates a relationship the source
        # database already declared, then let the known ones win outright —
        # they're always confidence 1.0, so bucket_candidates auto-approves them.
        known_pairs = {(r.table_a, r.column_a, r.table_b, r.column_b) for r in session.known_relationships}
        heuristic_only = [
            c for c in result.candidates
            if (c.table_a, c.column_a, c.table_b, c.column_b) not in known_pairs
        ]
        result.candidates = [*session.known_relationships, *heuristic_only]
    session.schema_discovery = result.model_dump()
    buckets = bucket_candidates(result.candidates)
    return SchemaDiscoveryResponse(
        tables=[TableProfileOut(**t.model_dump()) for t in result.tables],
        auto=[_candidate_out(c) for c in buckets["auto"]],
        review=[_candidate_out(c) for c in buckets["review"]],
        manual=[_candidate_out(c) for c in buckets["manual"]],
    )


@router.post("/relationships", response_model=list[RelationshipOut])
def submit_relationships(
    payload: RelationshipsRequest,
    session: PipelineSession = Depends(get_owned_pipeline_session),
) -> list[RelationshipOut]:
    approved = [
        RelationshipCandidate(
            table_a=d.table_a,
            column_a=d.column_a,
            table_b=d.table_b,
            column_b=d.column_b,
            confidence=d.confidence,
            evidence=d.evidence,
        )
        for d in payload.decisions
        if d.status == "approved"
    ]
    persist_relationships(session.project_id, approved)
    session.relationship_decisions = [d.model_dump() for d in payload.decisions]

    stored = load_relationships(session.project_id)
    return [
        RelationshipOut(
            table_a=r.table_a,
            column_a=r.column_a,
            table_b=r.table_b,
            column_b=r.column_b,
            confidence=r.confidence,
            status=r.status,
            approved_by=r.approved_by,
        )
        for r in stored
    ]


def _initial_graph_state(
    table_name: str, session: PipelineSession, planner_model: str, coder_model: str
) -> dict:
    key_columns = key_columns_for_table(
        table_name, _approved_relationship_candidates(session.project_id)
    )
    return {
        "raw_df": session.raw_tables[table_name],
        "file_path": table_name,
        "planner_model": planner_model,
        "coder_model": coder_model,
        "key_columns": key_columns,
        "metadata": {},
        "cleaning_plan": [],
        "generated_code": "",
        "execution_result": {},
        "validation_report": {},
        "transformation_log": [],
        "retry_count": 0,
        "last_error": "",
        "messages": [],
    }


def _require_table(session: PipelineSession, table_name: str) -> None:
    if table_name not in session.raw_tables:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{table_name}' is not one of this session's tables.",
        )


@router.post("/tables/{table_name}/plan", response_model=PlanResponse)
def plan_table(
    table_name: str,
    payload: PlanRequest,
    session: PipelineSession = Depends(get_owned_pipeline_session),
    current_user: User = Depends(get_current_user),
) -> PlanResponse:
    _require_table(session, table_name)
    planner_model = payload.planner_model or current_user.model_preferences.get("planner") or DEFAULT_PLANNER_MODEL
    coder_model = payload.coder_model or current_user.model_preferences.get("coder") or DEFAULT_CODER_MODEL
    graph = build_planner_graph()
    result = graph.invoke(_initial_graph_state(table_name, session, planner_model, coder_model))

    state = session.cleaning[table_name]
    state.profile = json_safe(result.get("metadata", {}))
    state.cleaning_plan = result.get("cleaning_plan", [])
    state.phase = "planned"
    return PlanResponse(table_name=table_name, profile=state.profile, cleaning_plan=state.cleaning_plan)


@router.patch("/tables/{table_name}/plan", response_model=PlanResponse)
def edit_plan(
    table_name: str,
    payload: PlanEditRequest,
    session: PipelineSession = Depends(get_owned_pipeline_session),
) -> PlanResponse:
    _require_table(session, table_name)
    state = session.cleaning[table_name]
    if state.phase == "idle":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /plan for this table before editing its plan.",
        )
    state.cleaning_plan = [s.model_dump() for s in payload.cleaning_plan]
    return PlanResponse(table_name=table_name, profile=state.profile or {}, cleaning_plan=state.cleaning_plan)


@router.post("/tables/{table_name}/plan/opinion", response_model=PlanStepOpinionResponse)
def plan_step_opinion(
    table_name: str,
    payload: PlanStepOpinionRequest,
    session: PipelineSession = Depends(get_owned_pipeline_session),
    current_user: User = Depends(get_current_user),
) -> PlanStepOpinionResponse:
    """LLM opinion on ONE step the user is about to add manually — grounded
    in the same profile the generated plan used, so it isn't a generic
    platitude. Read-only: doesn't mutate the stored plan."""
    _require_table(session, table_name)
    state = session.cleaning[table_name]
    if state.phase == "idle" or state.profile is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /plan for this table before asking for an opinion on a step.",
        )
    model = payload.planner_model or current_user.model_preferences.get("planner") or DEFAULT_PLANNER_MODEL
    opinion = review_manual_step(
        DatasetProfile(**state.profile),
        state.cleaning_plan or [],
        payload.step_description,
        model=model,
    )
    return PlanStepOpinionResponse(opinion=opinion)


@router.post("/tables/{table_name}/clean", response_model=CleanResponse)
def clean_table(
    table_name: str,
    payload: CleanRequest,
    session: PipelineSession = Depends(get_owned_pipeline_session),
    current_user: User = Depends(get_current_user),
) -> CleanResponse:
    _require_table(session, table_name)
    state = session.cleaning[table_name]
    # NOTE: an empty `cleaning_plan` list is a valid, common state — it means
    # the table is already clean (see agents/cleaning/planner.py). Only the
    # phase (was /plan ever run?) gates execution, not the plan's length.
    if state.phase == "idle":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /plan for this table before cleaning it.",
        )
    planner_model = payload.planner_model or current_user.model_preferences.get("planner") or DEFAULT_PLANNER_MODEL
    coder_model = payload.coder_model or current_user.model_preferences.get("coder") or DEFAULT_CODER_MODEL

    graph_state = _initial_graph_state(table_name, session, planner_model, coder_model)
    graph_state["metadata"] = state.profile or {}
    graph_state["cleaning_plan"] = state.cleaning_plan

    graph = build_cleaning_graph()
    result = graph.invoke(graph_state)

    exec_result = result.get("execution_result", {}) or {}
    validation_report = json_safe(result.get("validation_report", {}) or {})
    clean_df = exec_result.get("clean_df")

    state.generated_code = result.get("generated_code", "")
    state.cleaned_df = clean_df
    state.validation_report = validation_report
    state.transformation_log = result.get("transformation_log", [])
    state.retry_count = result.get("retry_count", 0)
    state.last_error = exec_result.get("error") or ""
    state.ledger = json_safe(exec_result.get("ledger") or {})
    state.phase = "done" if validation_report.get("passed") else "failed"

    return CleanResponse(
        table_name=table_name,
        generated_code=state.generated_code,
        success=bool(validation_report.get("passed")),
        error=state.last_error or None,
        validation_report=validation_report,
        transformation_log=state.transformation_log,
        retry_count=state.retry_count,
        preview=table_preview(table_name, clean_df) if clean_df is not None else None,
        ledger=state.ledger,
    )


@router.post("/clean-remaining", response_model=CleanRemainingResponse)
def clean_remaining(
    session: PipelineSession = Depends(get_owned_pipeline_session),
) -> CleanRemainingResponse:
    remaining = {
        name: df for name, df in session.raw_tables.items() if name != session.primary_table
    }
    approved = _approved_relationship_candidates(session.project_id)

    out: list[TableCleaningStatus] = []
    if remaining:
        results = clean_tables(
            remaining, approved, planner_model=DEFAULT_PLANNER_MODEL, coder_model=DEFAULT_CODER_MODEL,
        )
        for name, r in results.items():
            state = session.cleaning[name]
            state.cleaning_plan = r.cleaning_plan
            state.cleaned_df = r.clean_df
            state.validation_report = json_safe(r.validation_report)
            state.transformation_log = r.transformation_log
            state.retry_count = r.retry_count
            state.last_error = r.error
            state.ledger = json_safe(r.ledger or {})
            state.phase = "done" if r.success else "failed"
            out.append(TableCleaningStatus(
                table_name=name, success=r.success, error=r.error or None,
                validation_report=state.validation_report,
                transformation_log=r.transformation_log, retry_count=r.retry_count,
                ledger=state.ledger,
            ))

    # Reconcile across EVERY cleaned table (primary included) — a relationship
    # between the HITL-cleaned primary table and an auto-cleaned dimension
    # table (e.g. orders -> customers) is the common case, not an edge case,
    # so this can't be scoped to just the "remaining" subset.
    all_cleaned = {
        name: s.cleaned_df for name, s in session.cleaning.items() if s.cleaned_df is not None
    }
    normalized, checks = reconcile(approved, session.raw_tables, all_cleaned)
    for name, df in normalized.items():
        session.cleaning[name].cleaned_df = df

    reconciliation_out = [
        ReconciliationCheckOut(
            table_a=c.relationship.table_a, column_a=c.relationship.column_a,
            table_b=c.relationship.table_b, column_b=c.relationship.column_b,
            orphan_rate_before=c.orphan_rate_before, orphan_rate_after=c.orphan_rate_after,
            within_tolerance=c.within_tolerance(),
        )
        for c in checks
    ]
    session.reconciliation = json_safe([c.model_dump() for c in reconciliation_out])
    return CleanRemainingResponse(results=out, reconciliation=reconciliation_out)


@router.get("/reconciliation", response_model=list[ReconciliationCheckOut])
def get_reconciliation(
    session: PipelineSession = Depends(get_owned_pipeline_session),
) -> list[ReconciliationCheckOut]:
    return [ReconciliationCheckOut(**c) for c in (session.reconciliation or [])]


@router.post("/integrity", response_model=IntegrityResponse)
def check_integrity(
    session: PipelineSession = Depends(get_owned_pipeline_session),
) -> IntegrityResponse:
    cleaned_tables = {}
    missing: list[str] = []
    for name in session.raw_tables:
        state = session.cleaning[name]
        if state.cleaned_df is None:
            missing.append(name)
        else:
            cleaned_tables[name] = state.cleaned_df
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"These tables haven't been cleaned yet: {', '.join(missing)}",
        )

    approved = _approved_relationship_candidates(session.project_id)
    # Validate the values that will actually be stored, not the pre-reconciliation
    # ones — see _tables_for_storage.
    cleaned_tables = _tables_for_storage(session, approved)
    report = run_integrity_validation(cleaned_tables, approved)
    report_dict = json_safe(report.model_dump())
    session.integrity = report_dict
    return IntegrityResponse(passed=report.passed, report=report_dict)


@router.post("/save", response_model=SaveResponse)
def save_to_project(
    session: PipelineSession = Depends(get_owned_pipeline_session),
    db: Session = Depends(get_db),
) -> SaveResponse:
    if session.integrity is None or not session.integrity.get("passed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run /integrity and resolve any blocking issues before saving.",
        )

    approved = _approved_relationship_candidates(session.project_id)
    cleaned_tables = _tables_for_storage(session, approved)
    project = db.get(Project, session.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    confirmed_pks = session.integrity.get("confirmed_primary_keys") or {}
    # These two failures are expected and user-actionable, so they get a real
    # status + message. Anything else propagates to the app-wide handler in
    # backend.app.main, which reports it as a 500 *with* CORS headers — without
    # that, the browser only ever sees "Failed to fetch".
    try:
        table_ddls = load_project_schema(
            project.schema_name, cleaned_tables, approved, primary_keys=confirmed_pks
        )
    except PrimaryKeyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Could not write to the database: {getattr(exc, 'orig', None) or exc}. "
                "Is Postgres running (`docker compose up -d`)?"
            ),
        ) from exc

    invalidate_view_cache(project.id)
    session.saved = True

    # The names Postgres actually received, which can differ from the session's
    # table keys once sanitization runs (see db.ddl.sanitize_schema_columns).
    return SaveResponse(
        saved=True, schema_name=project.schema_name, tables=[d.name for d in table_ddls]
    )
