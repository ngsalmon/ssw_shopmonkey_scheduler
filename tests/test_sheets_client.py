"""Unit tests for Google Sheets client with mocked API."""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSheetsClientGetTechDepartments:
    """Tests for get_tech_departments method (sync version)."""

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
        # Use the sync internal method for testing
        result = client._sync_get_tech_departments()

        assert "tech-123" in result
        assert result["tech-123"]["tech_name"] == "John Doe"
        assert result["tech-123"]["departments"]["Vinyl"] == 1  # TRUE -> priority 1
        assert result["tech-123"]["departments"]["Alignment"] == 0  # FALSE -> 0
        assert result["tech-123"]["departments"]["Tint"] == 1

        assert "tech-456" in result
        assert result["tech-456"]["tech_name"] == "Jane Smith"
        assert result["tech-456"]["departments"]["Alignment"] == 1
        assert result["tech-456"]["departments"]["Detail"] == 1

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
        result = client._sync_get_tech_departments()

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
        result = client._sync_get_tech_departments()

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

        detail_techs = client._sync_get_techs_for_department("Detail")
        assert len(detail_techs) == 2
        tech_ids = [t["tech_id"] for t in detail_techs]
        assert "tech-1" in tech_ids
        assert "tech-3" in tech_ids
        assert "tech-2" not in tech_ids
        # Verify priority field is included
        for tech in detail_techs:
            assert "priority" in tech
            assert tech["priority"] == 1  # TRUE maps to priority 1

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
        result = client._sync_get_techs_for_department("Unknown")
        assert result == []

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_techs_sorted_by_priority(self, mock_build, mock_creds):
        """Should return techs sorted by priority (1=highest first)."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Alignment", "Status"],
                ["Low Priority", "tech-3", "Technician", "3", "Active"],
                ["High Priority", "tech-1", "Technician", "1", "Active"],
                ["Med Priority", "tech-2", "Technician", "2", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        techs = client._sync_get_techs_for_department("Alignment")

        assert len(techs) == 3
        # Should be sorted by priority: 1, 2, 3
        assert techs[0]["tech_id"] == "tech-1"
        assert techs[0]["priority"] == 1
        assert techs[1]["tech_id"] == "tech-2"
        assert techs[1]["priority"] == 2
        assert techs[2]["tech_id"] == "tech-3"
        assert techs[2]["priority"] == 3


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
        result = client._sync_get_all_departments()

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
        result = client._sync_get_all_departments()

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


class TestSheetsClientAsync:
    """Tests for async wrapper methods."""

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_async_get_techs_for_department(self, mock_build, mock_creds):
        """Should return techs via async method."""
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
        result = await client.get_techs_for_department("Detail")
        assert len(result) == 1
        assert result[0]["tech_id"] == "tech-1"


class TestSheetsClientCache:
    """Tests for cache functionality."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_cache_status(self, mock_build, mock_creds):
        """Should return cache status information."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        status = client.get_cache_status()

        assert "cache_size" in status
        assert "cache_ttl_seconds" in status
        assert "cache_maxsize" in status
        assert status["cache_ttl_seconds"] == 300

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_clear_cache(self, mock_build, mock_creds):
        """Should clear cache when clear_cache is called."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        # Manually add something to cache
        client._cache["test_key"] = "test_value"
        assert len(client._cache) == 1

        client.clear_cache()
        assert len(client._cache) == 0
