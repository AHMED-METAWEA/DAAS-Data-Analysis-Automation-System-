from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.pipeline import (
    DbLinkIngestRequest,
    DbLinkTestRequest,
    GSheetIngestRequest,
    IngestResponse,
)
from backend.app.services.pipeline_sessions import create_session
from backend.app.services.previews import table_preview
from db.auth_models import User
from db.connection_configs import save_connection_config
from db.platform_models import Project
from ingestion import db_link, gsheets
from ingestion.multi_table import choose_primary_table, tables_from_datasets
from schema_discovery.models import RelationshipCandidate
from tools.ingestion import Dataset, load_tabular_file

router = APIRouter(prefix="/projects/{project_id}/ingest", tags=["ingestion"])
# Connection testing doesn't belong to any one project — it happens before
# the project's data is committed, so it gets its own unscoped route rather
# than an unused {project_id} path segment.
unscoped_router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def _to_response(
    project_id: str,
    tables: dict[str, pd.DataFrame],
    known_relationships: list[RelationshipCandidate] | None = None,
) -> IngestResponse:
    if not tables:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tables found in source")
    primary = choose_primary_table(tables)
    session = create_session(
        project_id=project_id, tables=tables, primary_table=primary, known_relationships=known_relationships,
    )
    return IngestResponse(
        pipeline_session_id=session.id,
        project_id=project_id,
        primary_table=primary,
        tables=[table_preview(name, df) for name, df in tables.items()],
    )


class _BytesFile:
    """Adapts raw bytes to the file-like object `load_tabular_file` expects."""

    def __init__(self, raw: bytes, name: str | None) -> None:
        self._raw = raw
        self.name = name or ""

    def seek(self, _pos: int) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


@router.post("/files", response_model=IngestResponse)
async def ingest_files(
    files: list[UploadFile],
    project: Project = Depends(get_owned_project),
) -> IngestResponse:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files uploaded")
    datasets: list[Dataset] = []
    for f in files:
        raw = await f.read()
        try:
            df = load_tabular_file(_BytesFile(raw, f.filename), f.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        datasets.append(Dataset(name=f.filename or "file", df=df))
    tables = tables_from_datasets(datasets)
    return _to_response(project.id, tables)


@router.post("/gsheet", response_model=IngestResponse)
def ingest_gsheet(
    payload: GSheetIngestRequest,
    project: Project = Depends(get_owned_project),
) -> IngestResponse:
    try:
        service_account_info = json.loads(payload.service_account_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service account key is not valid JSON: {exc}",
        ) from exc
    try:
        gsheets.check_connection(service_account_info, payload.sheet_url_or_id)
        tables = gsheets.load_google_sheet(service_account_info, payload.sheet_url_or_id)
    except gsheets.GoogleSheetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_response(project.id, tables)


@unscoped_router.post("/db-link/test")
def test_db_link(
    payload: DbLinkTestRequest, current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    try:
        url = db_link.build_connection_url(
            payload.dialect, payload.host, payload.port, payload.database, payload.username, payload.password
        )
        connector = db_link.connect(url)
        try:
            db_link.check_connection(connector)
        finally:
            connector.dispose()
    except db_link.ReadOnlyConnectionError as exc:
        detail = db_link.friendly_connect_error(exc, payload.host, payload.port, payload.database)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    return {"status": "ok"}


@router.post("/db-link", response_model=IngestResponse)
def ingest_db_link(
    payload: DbLinkIngestRequest,
    project: Project = Depends(get_owned_project),
) -> IngestResponse:
    try:
        url = db_link.build_connection_url(
            payload.dialect, payload.host, payload.port, payload.database, payload.username, payload.password
        )
        connector = db_link.connect(url)
        try:
            db_link.check_connection(connector)
            tables = db_link.sample_tables(connector, payload.tables)
            # Trust the source database's own declared FK constraints ahead
            # of Schema Discovery's heuristics (see db_link.py's trust
            # hierarchy docstring) — only keep candidates between tables we
            # actually ingested, in case `payload.tables` narrowed the set.
            relationships = [
                r for r in db_link.introspect_relationships(connector)
                if r.table_a in tables and r.table_b in tables
            ]
        finally:
            connector.dispose()
    except db_link.ReadOnlyConnectionError as exc:
        detail = db_link.friendly_connect_error(exc, payload.host, payload.port, payload.database)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    if payload.save_connection:
        save_connection_config(
            project_id=project.id,
            source_type=payload.dialect,
            credentials={
                "host": payload.host,
                "port": payload.port,
                "database": payload.database,
                "username": payload.username,
                "password": payload.password,
            },
        )
    return _to_response(project.id, tables, known_relationships=relationships)
