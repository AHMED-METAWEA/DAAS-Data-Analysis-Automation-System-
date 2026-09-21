from __future__ import annotations

from unittest.mock import MagicMock, patch

import gspread
import pytest

from ingestion.gsheets import (
    GoogleSheetError,
    check_connection,
    extract_sheet_id,
    load_google_sheet,
)

_FAKE_CREDS = {"type": "service_account", "client_email": "svc@example.iam.gserviceaccount.com"}


def _mock_worksheet(title: str, records: list[dict]):
    ws = MagicMock()
    ws.title = title
    ws.get_all_records.return_value = records
    return ws


def _api_error(status_code: int, message: str = "error") -> gspread.exceptions.APIError:
    response = MagicMock()
    response.json.return_value = {"error": {"code": status_code, "message": message}}
    response.status_code = status_code
    return gspread.exceptions.APIError(response)


class TestExtractSheetId:
    def test_extracts_id_from_full_url(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=0"
        assert extract_sheet_id(url) == "1AbCdEfGhIjKlMnOp"

    def test_passes_through_bare_id(self) -> None:
        assert extract_sheet_id("1AbCdEfGhIjKlMnOp") == "1AbCdEfGhIjKlMnOp"

    def test_strips_whitespace_on_bare_id(self) -> None:
        assert extract_sheet_id("  1AbCdEfGhIjKlMnOp  ") == "1AbCdEfGhIjKlMnOp"


class TestLoadGoogleSheet:
    def test_one_dataframe_per_worksheet(self) -> None:
        customers_ws = _mock_worksheet("customers", [{"customer_id": 1, "name": "Alice"}])
        orders_ws = _mock_worksheet("orders", [{"order_id": 100, "customer_id": 1}])

        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheets.return_value = [customers_ws, orders_ws]

        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            tables = load_google_sheet(_FAKE_CREDS, "sheet-id")

        assert set(tables) == {"customers", "orders"}
        assert list(tables["customers"].columns) == ["customer_id", "name"]
        assert len(tables["orders"]) == 1

    def test_duplicate_tab_names_are_deduplicated(self) -> None:
        ws1 = _mock_worksheet("data", [{"a": 1}])
        ws2 = _mock_worksheet("data", [{"b": 2}])
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheets.return_value = [ws1, ws2]
        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            tables = load_google_sheet(_FAKE_CREDS, "sheet-id")

        assert set(tables) == {"data", "data_2"}

    def test_spreadsheet_not_found_raises_clear_error(self) -> None:
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = gspread.exceptions.SpreadsheetNotFound()

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            with pytest.raises(GoogleSheetError, match="not found"):
                load_google_sheet(_FAKE_CREDS, "sheet-id")

    def test_403_access_denied_raises_clear_error_not_traceback(self) -> None:
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = _api_error(403, "The caller does not have permission")

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            with pytest.raises(GoogleSheetError, match="Access denied"):
                load_google_sheet(_FAKE_CREDS, "sheet-id")

    def test_other_api_error_still_wrapped(self) -> None:
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = _api_error(500, "internal error")

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            with pytest.raises(GoogleSheetError, match="Google Sheets API error"):
                load_google_sheet(_FAKE_CREDS, "sheet-id")

    def test_no_worksheets_raises_clear_error(self) -> None:
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheets.return_value = []
        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            with pytest.raises(GoogleSheetError, match="no worksheets"):
                load_google_sheet(_FAKE_CREDS, "sheet-id")

    def test_invalid_credentials_raises_clear_error(self) -> None:
        with patch(
            "ingestion.gsheets.Credentials.from_service_account_info",
            side_effect=ValueError("bad key format"),
        ), pytest.raises(GoogleSheetError, match="Invalid service account credentials"):
            load_google_sheet(_FAKE_CREDS, "sheet-id")


class TestCheckConnection:
    def test_healthy_sheet_does_not_raise(self) -> None:
        ws = _mock_worksheet("data", [{"a": 1}])
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheets.return_value = [ws]
        mock_client = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            check_connection(_FAKE_CREDS, "sheet-id")  # should not raise

    def test_not_shared_sheet_raises_403_message(self) -> None:
        mock_client = MagicMock()
        mock_client.open_by_key.side_effect = _api_error(403)

        with patch("ingestion.gsheets.Credentials.from_service_account_info"), \
             patch("ingestion.gsheets.gspread.authorize", return_value=mock_client):
            with pytest.raises(GoogleSheetError, match="Access denied"):
                check_connection(_FAKE_CREDS, "sheet-id")
