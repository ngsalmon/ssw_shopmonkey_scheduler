"""Unit tests for Google Sheets client with mocked API."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSheetsClientGetTechDepartments:
    """Tests for get_tech_departments method."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_parses_tech_departments_correctly(self, mock_build, mock_creds):
        """Should correctly parse technician department mappings."""
        from sheets_client import SheetsClient

        # Mock the sheets API response
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Vinyl", "Alignment", "Tint", "Detail", "Status"],
                ["John Doe", "tech-123", "Technician", "TRUE", "FALSE", "TRUE", "FALSE", "Active"],
                ["Jane Smith", "tech-456", "Technician", "FALSE", "TRUE", "FALSE", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_tech_departments()

        assert "tech-123" in result
        assert result["tech-123"]["tech_name"] == "John Doe"
        assert result["tech-123"]["departments"]["Vinyl"] is True
        assert result["tech-123"]["departments"]["Alignment"] is False
        assert result["tech-123"]["departments"]["Tint"] is True

        assert "tech-456" in result
        assert result["tech-456"]["tech_name"] == "Jane Smith"
        assert result["tech-456"]["departments"]["Alignment"] is True
        assert result["tech-456"]["departments"]["Detail"] is True

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_filters_inactive_technicians(self, mock_build, mock_creds):
        """Should filter out inactive technicians."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Active Tech", "tech-1", "Technician", "TRUE", "Active"],
                ["Inactive Tech", "tech-2", "Technician", "TRUE", "Inactive"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_tech_departments()

        assert "tech-1" in result
        assert "tech-2" not in result

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_skips_rows_without_tech_id(self, mock_build, mock_creds):
        """Should skip rows without a tech ID."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Valid Tech", "tech-1", "Technician", "TRUE", "Active"],
                ["No ID Tech", "", "Technician", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_tech_departments()

        assert "tech-1" in result
        assert len(result) == 1


class TestSheetsClientGetTechsForDepartment:
    """Tests for get_techs_for_department method."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_qualified_techs(self, mock_build, mock_creds):
        """Should return only techs qualified for the department."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Tint", "Status"],
                ["Detail Tech", "tech-1", "Technician", "TRUE", "FALSE", "Active"],
                ["Tint Tech", "tech-2", "Technician", "FALSE", "TRUE", "Active"],
                ["Both Tech", "tech-3", "Technician", "TRUE", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        detail_techs = client.get_techs_for_department("Detail")
        assert len(detail_techs) == 2
        tech_ids = [t["tech_id"] for t in detail_techs]
        assert "tech-1" in tech_ids
        assert "tech-3" in tech_ids
        assert "tech-2" not in tech_ids

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_empty_for_unknown_department(self, mock_build, mock_creds):
        """Should return empty list for unknown department."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech", "tech-1", "Technician", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_techs_for_department("Unknown")
        assert result == []


class TestSheetsClientGetAllDepartments:
    """Tests for get_all_departments method."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_department_columns(self, mock_build, mock_creds):
        """Should return department column names."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Vinyl", "Alignment", "Window Tint", "Detail", "Bedliner", "Status"]
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_all_departments()

        assert result == ["Vinyl", "Alignment", "Window Tint", "Detail", "Bedliner"]

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_empty_when_no_data(self, mock_build, mock_creds):
        """Should return empty list when no header row."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": []
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client.get_all_departments()

        assert result == []


class TestSheetsClientNormalizeDepartment:
    """Tests for _normalize_department method."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_normalizes_alignment_tech(self, mock_build, mock_creds):
        """Should normalize 'Alignment/Tech' to 'Alignment'."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client._normalize_department("Alignment/Tech")
        assert result == "Alignment"

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_unchanged_when_no_mapping(self, mock_build, mock_creds):
        """Should return unchanged when no mapping exists."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client._normalize_department("Detail")
        assert result == "Detail"
