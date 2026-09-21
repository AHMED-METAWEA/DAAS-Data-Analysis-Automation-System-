"""Path C ingestion — Google Sheets, via a GCP service account.

Service-account auth, not a full interactive OAuth consent flow: the user
creates a service account in GCP, downloads its JSON key, and shares the
target Sheet with the service account's email address. Equivalent read-only
capability, far less engineering for a single local user.

Returns the same ``dict[str, pd.DataFrame]`` contract as Stage 2
(``ingestion.multi_table``) and Stage 11 (existing-database linking) — one
entry per worksheet/tab — so it converges into the same Schema Discovery /
Cleaning / Reconciliation steps as any other source. Read-only unless a
write-back feature is explicitly requested later; sync is on-demand (the
caller re-invokes ``load_google_sheet``), not continuous.
"""

from __future__ import annotations

import re

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

from db.ddl import sanitize_identifier

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


class GoogleSheetError(RuntimeError):
    """Any Google Sheets connection/read problem — the message is meant to
    be shown directly to the user, not a raw traceback."""


def extract_sheet_id(url_or_id: str) -> str:
    """Accept either a full Google Sheets URL or a bare spreadsheet ID."""
    match = _SHEET_ID_RE.search(url_or_id)
    return match.group(1) if match else url_or_id.strip()


def _client(service_account_info: dict) -> gspread.Client:
    try:
        creds = Credentials.from_service_account_info(service_account_info, scopes=_SCOPES)
    except Exception as exc:
        raise GoogleSheetError(f"Invalid service account credentials: {exc}") from exc
    return gspread.authorize(creds)


def _open_spreadsheet(client: gspread.Client, sheet_id: str):
    try:
        return client.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise GoogleSheetError(
            "Spreadsheet not found. Check the URL/ID, and make sure it's been "
            "shared with the service account's email."
        ) from exc
    except gspread.exceptions.APIError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 403:
            raise GoogleSheetError(
                "Access denied (403). Share the Google Sheet with the service "
                "account's email address (found in the JSON key's 'client_email' field)."
            ) from exc
        raise GoogleSheetError(f"Google Sheets API error: {exc}") from exc


def check_connection(service_account_info: dict, url_or_id: str) -> None:
    """Health check: can we authenticate, does the sheet exist, do we have
    read access, is it non-empty. Raises GoogleSheetError with a clear
    message on any failure — never a raw traceback.
    """
    client = _client(service_account_info)
    spreadsheet = _open_spreadsheet(client, extract_sheet_id(url_or_id))
    if not spreadsheet.worksheets():
        raise GoogleSheetError("The spreadsheet has no worksheets/tabs.")


def load_google_sheet(service_account_info: dict, url_or_id: str) -> dict[str, pd.DataFrame]:
    """Read every worksheet/tab into its own DataFrame, keyed by tab name
    (de-duplicated like ``ingestion.multi_table.tables_from_datasets``).
    """
    client = _client(service_account_info)
    spreadsheet = _open_spreadsheet(client, extract_sheet_id(url_or_id))

    worksheets = spreadsheet.worksheets()
    if not worksheets:
        raise GoogleSheetError("The spreadsheet has no worksheets/tabs.")

    tables: dict[str, pd.DataFrame] = {}
    for worksheet in worksheets:
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)

        # Tab titles are free text (spaces, punctuation, emoji) — sanitize
        # into a valid SQL identifier, same as file-upload table names (see
        # ingestion.multi_table._table_name), so both paths converge on
        # names every downstream engine can safely use as a schema key.
        name = sanitize_identifier(worksheet.title)
        final_name = name
        suffix = 2
        while final_name in tables:
            final_name = f"{name}_{suffix}"
            suffix += 1
        tables[final_name] = df

    return tables
