from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TablePreview(BaseModel):
    name: str
    rows: int
    columns: list[str]
    preview: list[dict[str, Any]]


class IngestResponse(BaseModel):
    pipeline_session_id: str
    project_id: str
    primary_table: str
    tables: list[TablePreview]


class GSheetIngestRequest(BaseModel):
    service_account_json: str = Field(description="Raw JSON contents of the GCP service account key")
    sheet_url_or_id: str


class DbLinkTestRequest(BaseModel):
    dialect: str = Field(pattern="^(postgresql|mysql|mssql)$")
    host: str
    port: int
    database: str
    username: str
    password: str


class DbLinkIngestRequest(DbLinkTestRequest):
    tables: list[str] | None = None
    save_connection: bool = False


class TableProfileOut(BaseModel):
    name: str
    row_count: int
    column_count: int
    columns: list[str]


class RelationshipCandidateOut(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float
    evidence: dict[str, float]
    source: str
    reasoning: str
    validation: dict[str, Any]


class SchemaDiscoveryResponse(BaseModel):
    tables: list[TableProfileOut]
    auto: list[RelationshipCandidateOut]
    review: list[RelationshipCandidateOut]
    manual: list[RelationshipCandidateOut]


class RelationshipDecision(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float = 0.0
    evidence: dict[str, float] = Field(default_factory=dict)
    status: str = Field(pattern="^(approved|rejected)$")


class RelationshipsRequest(BaseModel):
    decisions: list[RelationshipDecision]


class RelationshipOut(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float
    status: str
    approved_by: str


class PlanRequest(BaseModel):
    planner_model: str | None = None
    coder_model: str | None = None


class CleaningPlanStep(BaseModel):
    id: str
    description: str
    source: str = Field(default="generated", pattern="^(generated|manual|detector|fallback)$")
    # A step is normally a typed operator from tools.cleaning_ops.REGISTRY.
    # An empty `op` marks a free-text step, which falls back to generated code.
    op: str = ""
    columns: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class PlanResponse(BaseModel):
    table_name: str
    profile: dict[str, Any]
    cleaning_plan: list[CleaningPlanStep]


class PlanEditRequest(BaseModel):
    cleaning_plan: list[CleaningPlanStep]


class PlanStepOpinionRequest(BaseModel):
    step_description: str
    planner_model: str | None = None


class PlanStepOpinionResponse(BaseModel):
    opinion: str


class CleanRequest(BaseModel):
    planner_model: str | None = None
    coder_model: str | None = None


class CleanResponse(BaseModel):
    table_name: str
    generated_code: str
    success: bool
    error: str | None
    validation_report: dict[str, Any]
    transformation_log: list[str]
    retry_count: int
    preview: TablePreview | None
    # Measured per-step audit trail (counts computed by diffing the frame, not
    # reported by the code that changed it). See models/cleaning_ops.py.
    ledger: dict[str, Any] | None = None


class TableCleaningStatus(BaseModel):
    table_name: str
    success: bool
    error: str | None
    validation_report: dict[str, Any]
    transformation_log: list[str]
    retry_count: int
    ledger: dict[str, Any] | None = None


class ReconciliationCheckOut(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    orphan_rate_before: float
    orphan_rate_after: float
    within_tolerance: bool


class CleanRemainingResponse(BaseModel):
    results: list[TableCleaningStatus]
    reconciliation: list[ReconciliationCheckOut]


class IntegrityResponse(BaseModel):
    passed: bool
    report: dict[str, Any]


class SaveResponse(BaseModel):
    saved: bool
    schema_name: str
    tables: list[str]
