"""Unit tests for Google Sheets client with mocked API."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_sheet(mock_build, values):
    """Wire the Google Sheets API mock to return `values` and hand back the mock.

    Returns the MagicMock standing in for the discovery service so tests can
    count `.execute()` calls (the real network boundary).
    """
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = values
    return mock_service


def _execute_mock(mock_service):
    """The `.execute` callable, i.e. the one real API round-trip per read."""
    return mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute


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
                [
                    "Jane Smith",
                    "tech-456",
                    "Technician",
                    "FALSE",
                    "TRUE",
                    "FALSE",
                    "TRUE",
                    "Active",
                ],
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


class TestSheetsClientActiveTechIdsFilter:
    """Tests for the active_tech_ids filter (Shopmonkey active-state override)."""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_excludes_techs_not_in_active_set(self, mock_build, mock_creds):
        """Techs whose ID is not in active_tech_ids should be excluded."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech A", "tech-1", "Technician", "TRUE", "Active"],
                ["Tech B", "tech-2", "Technician", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client._sync_get_tech_departments(active_tech_ids={"tech-1"})

        assert "tech-1" in result
        assert "tech-2" not in result

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_inactive_override_wins_even_when_active_in_shopmonkey(self, mock_build, mock_creds):
        """Sheet 'Inactive' status excludes regardless of Shopmonkey active state."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech A", "tech-1", "Technician", "TRUE", "Inactive"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        # Shopmonkey says active, but sheet override forces inactive.
        result = client._sync_get_tech_departments(active_tech_ids={"tech-1"})

        assert "tech-1" not in result

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_no_filter_when_active_tech_ids_is_none(self, mock_build, mock_creds):
        """When active_tech_ids is None, no Shopmonkey filtering happens."""
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech A", "tech-1", "Technician", "TRUE", "Active"],
                ["Tech B", "tech-2", "Technician", "TRUE", "Active"],
            ]
        }

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        result = client._sync_get_tech_departments()

        assert "tech-1" in result
        assert "tech-2" in result


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
                [
                    "Name",
                    "ID",
                    "Role",
                    "Vinyl",
                    "Alignment",
                    "Window Tint",
                    "Detail",
                    "Bedliner",
                    "Status",
                ]
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


class TestSheetsClientDepartmentConcurrency:
    """Tests for the MAX CONCURRENCY row parsing in the Tech/Dept tab."""

    SHEET = {
        "values": [
            ["Name", "ID", "Role", "Vinyl", "Tint", "Bedliner", "Status"],
            ["MAX CONCURRENCY", "", "", "2", "1", "", ""],
            ["John Doe", "tech-1", "Technician", "1", "0", "2", "Active"],
            ["Jane Smith", "tech-2", "Technician", "2", "1", "0", "Active"],
        ]
    }

    def _client(self, mock_build, values):
        from sheets_client import SheetsClient

        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = values
        return SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_parses_concurrency_row(self, mock_build, mock_creds):
        """Positive integers become caps; blank cells are omitted (no cap)."""
        client = self._client(mock_build, self.SHEET)
        caps = client._sync_get_department_concurrency()

        assert caps == {"Vinyl": 2, "Tint": 1}
        assert "Bedliner" not in caps  # blank cell -> no cap

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_get_max_concurrency_for_department(self, mock_build, mock_creds):
        client = self._client(mock_build, self.SHEET)
        assert client._sync_get_max_concurrency_for_department("Tint") == 1
        assert client._sync_get_max_concurrency_for_department("Vinyl") == 2
        # Blank cell and entirely-unknown department both return None.
        assert client._sync_get_max_concurrency_for_department("Bedliner") is None
        assert client._sync_get_max_concurrency_for_department("Nope") is None

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_concurrency_row_is_not_treated_as_a_tech(self, mock_build, mock_creds):
        """The MAX CONCURRENCY row must never appear in the tech mapping."""
        client = self._client(mock_build, self.SHEET)
        techs = client._sync_get_tech_departments()

        assert set(techs) == {"tech-1", "tech-2"}
        assert all(t["tech_name"].lower() != "max concurrency" for t in techs.values())

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_concurrency_row_ignored_even_with_stray_id(self, mock_build, mock_creds):
        """A stray ID on the cap row still doesn't make it a tech."""
        values = {
            "values": [
                ["Name", "ID", "Role", "Tint", "Status"],
                ["Max Concurrency", "oops-id", "", "1", ""],
                ["Real Tech", "tech-1", "Technician", "1", "Active"],
            ]
        }
        client = self._client(mock_build, values)
        techs = client._sync_get_tech_departments()
        assert set(techs) == {"tech-1"}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_zero_and_invalid_values_are_no_cap(self, mock_build, mock_creds):
        values = {
            "values": [
                ["Name", "ID", "Role", "Vinyl", "Tint", "Status"],
                ["MAX CONCURRENCY", "", "", "0", "abc", ""],
            ]
        }
        client = self._client(mock_build, values)
        assert client._sync_get_department_concurrency() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_empty_when_no_concurrency_row(self, mock_build, mock_creds):
        values = {
            "values": [
                ["Name", "ID", "Role", "Tint", "Status"],
                ["John", "tech-1", "Technician", "1", "Active"],
            ]
        }
        client = self._client(mock_build, values)
        assert client._sync_get_department_concurrency() == {}


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

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_repeat_reads_hit_cache_instead_of_the_api(self, mock_build, mock_creds):
        """A second read of the same range must not round-trip to Google.

        The whole point of the TTL cache is that availability checks (which read
        the tech matrix on every request) don't burn Sheets API quota.
        """
        from sheets_client import SheetsClient

        service = _mock_sheet(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Tech", "tech-1", "Technician", "1", "Active"],
                ]
            },
        )

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")
        client._sync_get_tech_departments()
        client._sync_get_tech_departments()

        assert _execute_mock(service).call_count == 1

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_clear_cache_makes_the_next_read_see_updated_sheet_data(self, mock_build, mock_creds):
        """After clear_cache the client must observe edits made to the sheet.

        Stale tech data silently misroutes bookings: if a tech is removed from a
        department in the sheet and clear_cache doesn't actually force a refetch,
        we keep offering (and assigning) slots to an unqualified tech.
        """
        from sheets_client import SheetsClient

        before = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech", "tech-1", "Technician", "1", "Active"],
            ]
        }
        after = {
            "values": [
                ["Name", "ID", "Role", "Detail", "Status"],
                ["Tech", "tech-1", "Technician", "0", "Active"],
            ]
        }

        service = _mock_sheet(mock_build, before)
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert client._sync_get_techs_for_department("Detail") != []

        _execute_mock(service).return_value = after
        # Still cached: the edit is invisible until the cache is dropped.
        assert client._sync_get_techs_for_department("Detail") != []

        client.clear_cache()
        assert client._sync_get_techs_for_department("Detail") == []

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_use_cache_false_bypasses_and_does_not_populate_the_cache(self, mock_build, mock_creds):
        """An uncached read must always hit the API and leave the cache empty."""
        from sheets_client import SheetsClient

        service = _mock_sheet(mock_build, {"values": [["Name"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        client._sync_read_sheet("'Tech/Dept'!A1:A1", use_cache=False)
        client._sync_read_sheet("'Tech/Dept'!A1:A1", use_cache=False)

        assert _execute_mock(service).call_count == 2
        assert len(client._cache) == 0

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_credentials_and_service_are_built_once_and_reused(self, mock_build, mock_creds):
        """_get_service must memoize; rebuilding per read re-authenticates every call."""
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, {"values": [["Name"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        client._sync_read_sheet("range-a", use_cache=False)
        client._sync_read_sheet("range-b", use_cache=False)

        assert mock_build.call_count == 1
        assert mock_creds.call_count == 1

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_cache_is_keyed_by_range(self, mock_build, mock_creds):
        """Different ranges must not share a cache entry (header-only vs full tab)."""
        from sheets_client import SheetsClient

        service = _mock_sheet(mock_build, {"values": [["Name", "ID", "Role", "Detail"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        client._sync_read_sheet("'Tech/Dept'!A:Z")
        client._sync_read_sheet("'Tech/Dept'!A1:Z1")

        assert _execute_mock(service).call_count == 2


class TestSheetsClientInit:
    """Tests for constructor configuration and credential selection."""

    def test_requires_a_spreadsheet_id(self):
        """Booting without a sheet ID must fail loudly, not silently read nothing."""
        from sheets_client import SheetsClient

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                SheetsClient()

    def test_falls_back_to_environment_variables(self):
        """Deployment supplies config via env, so env must be honoured."""
        from sheets_client import SheetsClient

        env = {
            "GOOGLE_SHEETS_ID": "env-sheet",
            "GOOGLE_APPLICATION_CREDENTIALS": "/env/creds.json",
        }
        with patch.dict(os.environ, env, clear=True):
            client = SheetsClient()

        assert client.spreadsheet_id == "env-sheet"
        assert client.credentials_path == "/env/creds.json"

    def test_explicit_arguments_win_over_environment(self):
        from sheets_client import SheetsClient

        env = {"GOOGLE_SHEETS_ID": "env-sheet", "GOOGLE_APPLICATION_CREDENTIALS": "/env/c.json"}
        with patch.dict(os.environ, env, clear=True):
            client = SheetsClient(spreadsheet_id="explicit", credentials_path="/explicit.json")

        assert client.spreadsheet_id == "explicit"
        assert client.credentials_path == "/explicit.json"

    def test_cache_ttl_is_configurable(self):
        """A custom TTL must reach the underlying cache, not just get reported."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="c.json", cache_ttl=7)

        assert client._cache.ttl == 7
        assert client.get_cache_status()["cache_ttl_seconds"] == 7

    @patch("google.auth.default")
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_uses_application_default_credentials_when_no_key_file(
        self, mock_build, mock_creds, mock_default
    ):
        """On Cloud Run there is no key file; ADC must be used instead of crashing."""
        from sheets_client import SheetsClient

        mock_default.return_value = ("adc-creds", "project")
        _mock_sheet(mock_build, {"values": []})

        with patch.dict(os.environ, {"GOOGLE_SHEETS_ID": "env-sheet"}, clear=True):
            client = SheetsClient()
            client._sync_read_sheet("range", use_cache=False)

        mock_creds.assert_not_called()
        mock_default.assert_called_once()
        assert mock_build.call_args.kwargs["credentials"] == "adc-creds"

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_requests_the_configured_spreadsheet(self, mock_build, mock_creds):
        """Reading the wrong spreadsheet would serve another shop's tech roster."""
        from sheets_client import SheetsClient

        service = _mock_sheet(mock_build, {"values": []})
        client = SheetsClient(spreadsheet_id="sheet-abc", credentials_path="c.json")
        client._sync_read_sheet("'Tech/Dept'!A:Z")

        get_kwargs = service.spreadsheets.return_value.values.return_value.get.call_args.kwargs
        assert get_kwargs["spreadsheetId"] == "sheet-abc"
        assert get_kwargs["range"] == "'Tech/Dept'!A:Z"


class TestSheetsClientServiceDepartments:
    """Tests for the legacy Bookable Canned Services tab parsing."""

    def _client(self, mock_build, values):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, values)
        return SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_parses_service_to_department_mapping(self, mock_build, mock_creds):
        """Header row is dropped and remaining rows become the mapping."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Service", "Department"],
                    ["Oil Change", "Quick Services"],
                    ["Full Tint", "Window Tint"],
                ]
            },
        )

        assert client._sync_get_service_departments() == {
            "Oil Change": "Quick Services",
            "Full Tint": "Window Tint",
        }

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_trims_whitespace_from_hand_typed_cells(self, mock_build, mock_creds):
        """Humans pad cells; an untrimmed key would never match a Shopmonkey service."""
        client = self._client(
            mock_build,
            {"values": [["Service", "Department"], ["  Oil Change  ", "  Quick Services "]]},
        )

        assert client._sync_get_service_departments() == {"Oil Change": "Quick Services"}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_skips_blank_and_incomplete_rows(self, mock_build, mock_creds):
        """Blank spacer rows and half-filled rows must not create junk mappings."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Service", "Department"],
                    ["Good", "Detail"],
                    [],
                    ["Missing Department"],
                    ["", "Detail"],
                    ["Missing Dept Value", "   "],
                ]
            },
        )

        assert client._sync_get_service_departments() == {"Good": "Detail"}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_returns_empty_when_tab_is_empty(self, mock_build, mock_creds):
        """An empty tab must yield {} rather than raising on rows[0]."""
        client = self._client(mock_build, {"values": []})

        assert client._sync_get_service_departments() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_header_only_tab_yields_no_mappings(self, mock_build, mock_creds):
        client = self._client(mock_build, {"values": [["Service", "Department"]]})

        assert client._sync_get_service_departments() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_department_for_service_is_normalized(self, mock_build, mock_creds):
        """The services tab spells departments differently than the Tech/Dept columns.

        Without normalization the returned department never matches a tech column
        and the service looks like it has zero qualified techs.
        """
        client = self._client(
            mock_build,
            {"values": [["Service", "Department"], ["Alignment Job", "Alignment/Tech"]]},
        )

        assert client._sync_get_department_for_service("Alignment Job") == "Alignment"

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_department_for_unknown_service_is_none(self, mock_build, mock_creds):
        client = self._client(
            mock_build, {"values": [["Service", "Department"], ["Oil Change", "Quick Services"]]}
        )

        assert client._sync_get_department_for_service("Not Listed") is None

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_department_lookup_is_case_and_space_sensitive(self, mock_build, mock_creds):
        """Lookup is an exact match on the trimmed name; callers must pass the sheet name."""
        client = self._client(
            mock_build, {"values": [["Service", "Department"], ["Oil Change", "Quick Services"]]}
        )

        assert client._sync_get_department_for_service("Oil Change") == "Quick Services"
        assert client._sync_get_department_for_service("oil change") is None


class TestSheetsClientTechDepartmentParsing:
    """Parsing edge cases for the hand-maintained Tech/Dept matrix."""

    def _client(self, mock_build, values):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, values)
        return SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_non_numeric_priority_is_not_qualified(self, mock_build, mock_creds):
        """A typo like "maybe" must mean "not qualified", never a silent qualification.

        Treating garbage as qualified would route bookings to a tech who can't do
        the work.
        """
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Typo Tech", "tech-1", "Technician", "maybe", "Active"],
                ]
            },
        )

        assert client._sync_get_tech_departments()["tech-1"]["departments"]["Detail"] == 0
        assert client._sync_get_techs_for_department("Detail") == []

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_decimal_priority_is_not_qualified(self, mock_build, mock_creds):
        """ "1.5" is not an int; it must fall back to 0 rather than crash the parse."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Tech", "tech-1", "Technician", "1.5", "Active"],
                ]
            },
        )

        assert client._sync_get_tech_departments()["tech-1"]["departments"]["Detail"] == 0

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_negative_priority_does_not_qualify(self, mock_build, mock_creds):
        """Only priorities >= 1 qualify; a negative must not sort to the front."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Neg", "tech-1", "Technician", "-1", "Active"],
                    ["Ok", "tech-2", "Technician", "1", "Active"],
                ]
            },
        )

        assert [t["tech_id"] for t in client._sync_get_techs_for_department("Detail")] == ["tech-2"]

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_short_row_missing_trailing_columns_defaults_to_unqualified(
        self, mock_build, mock_creds
    ):
        """Sheets truncates trailing empty cells; missing columns must read as 0."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Tint", "Detail", "Status"],
                    ["Short", "tech-1", "Technician", "1"],
                ]
            },
        )

        info = client._sync_get_tech_departments()["tech-1"]
        assert info["departments"] == {"Vinyl": 1, "Tint": 0, "Detail": 0}
        assert info["status"] == ""

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_row_with_only_a_name_is_skipped(self, mock_build, mock_creds):
        """A name typed with no ID yet cannot be booked against; drop it."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Pending Hire"],
                    ["Real", "tech-1", "Technician", "1", "Active"],
                ]
            },
        )

        assert set(client._sync_get_tech_departments()) == {"tech-1"}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_whitespace_padded_id_and_priority_are_trimmed(self, mock_build, mock_creds):
        """An untrimmed ID would never match the Shopmonkey user ID on appointments."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["  Padded Tech ", "  tech-1  ", " Technician ", " 2 ", " Active "],
                ]
            },
        )

        result = client._sync_get_tech_departments()
        assert set(result) == {"tech-1"}
        assert result["tech-1"]["tech_name"] == "Padded Tech"
        assert result["tech-1"]["role"] == "Technician"
        assert result["tech-1"]["departments"]["Detail"] == 2
        assert result["tech-1"]["status"] == "Active"

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_id_whitespace_only_counts_as_missing(self, mock_build, mock_creds):
        """A cell containing only spaces is not an ID."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Blankish", "   ", "Technician", "1", "Active"],
                ]
            },
        )

        assert client._sync_get_tech_departments() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_status_matching_is_case_insensitive(self, mock_build, mock_creds):
        """Sheet status is typed by hand; "inactive"/"INACTIVE" must all exclude."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Lower", "tech-1", "Technician", "1", "inactive"],
                    ["Upper", "tech-2", "Technician", "1", "INACTIVE"],
                    ["Padded", "tech-3", "Technician", "1", " Inactive "],
                    ["Active", "tech-4", "Technician", "1", "Active"],
                ]
            },
        )

        assert set(client._sync_get_tech_departments()) == {"tech-4"}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_boolean_word_variants_all_parse(self, mock_build, mock_creds):
        """The sheet mixes legacy TRUE/YES/X with the newer numeric priorities."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "A", "B", "C", "D", "E", "Status"],
                    ["Tech", "tech-1", "Technician", "true", "yes", "x", "no", "", "Active"],
                ]
            },
        )

        assert client._sync_get_tech_departments()["tech-1"]["departments"] == {
            "A": 1,
            "B": 1,
            "C": 1,
            "D": 0,
            "E": 0,
        }

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_techs_are_keyed_by_id_so_duplicate_names_both_survive(self, mock_build, mock_creds):
        """Two techs can share a first name; keying by name would drop one silently."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Detail", "Status"],
                    ["Mike", "tech-1", "Technician", "1", "Active"],
                    ["Mike", "tech-2", "Technician", "1", "Active"],
                ]
            },
        )

        assert set(client._sync_get_tech_departments()) == {"tech-1", "tech-2"}
        assert len(client._sync_get_techs_for_department("Detail")) == 2

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_empty_tab_and_header_only_tab_yield_no_techs(self, mock_build, mock_creds):
        """Never raise on an empty or freshly-created tab."""
        assert self._client(mock_build, {"values": []})._sync_get_tech_departments() == {}
        assert (
            self._client(
                mock_build, {"values": [["Name", "ID", "Role", "Detail", "Status"]]}
            )._sync_get_tech_departments()
            == {}
        )

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_header_without_status_column_treats_trailing_columns_as_departments(
        self, mock_build, mock_creds
    ):
        """When nobody added a Status column, everything from D on is a department."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Tint"],
                    ["Tech", "tech-1", "Technician", "1", "2"],
                ]
            },
        )

        assert client._sync_get_all_departments() == ["Vinyl", "Tint"]
        assert client._sync_get_tech_departments()["tech-1"]["departments"] == {
            "Vinyl": 1,
            "Tint": 2,
        }

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_columns_after_status_are_not_departments(self, mock_build, mock_creds):
        """Notes columns parked to the right of Status must not become departments."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Status", "Notes"],
                    ["Tech", "tech-1", "Technician", "1", "Active", "on vacation"],
                ]
            },
        )

        assert client._sync_get_all_departments() == ["Vinyl"]
        assert client._sync_get_tech_departments()["tech-1"]["departments"] == {"Vinyl": 1}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_status_header_is_matched_case_insensitively(self, mock_build, mock_creds):
        """ "STATUS" or "Active Status" must still bound the department columns."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "STATUS"],
                    ["Tech", "tech-1", "Technician", "1", "Inactive"],
                ]
            },
        )

        assert client._sync_get_all_departments() == ["Vinyl"]
        assert client._sync_get_tech_departments() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_department_header_names_are_trimmed(self, mock_build, mock_creds):
        """A padded header must produce the trimmed key callers look up."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "  Window Tint  ", "Status"],
                    ["Tech", "tech-1", "Technician", "1", "Active"],
                ]
            },
        )

        assert client._sync_get_all_departments() == ["Window Tint"]
        assert client._sync_get_techs_for_department("Window Tint")[0]["tech_id"] == "tech-1"


class TestSheetsClientConcurrencyEdgeCases:
    """Additional malformed-input cases for the MAX CONCURRENCY row."""

    def _client(self, mock_build, values):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, values)
        return SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_sentinel_variants_are_recognized(self, mock_build, mock_creds):
        """The sheet owner may type any of the accepted sentinel spellings."""
        for sentinel in ("max concurrency", "MAX_CONCURRENCY", "Concurrency"):
            client = self._client(
                mock_build,
                {
                    "values": [
                        ["Name", "ID", "Role", "Vinyl", "Status"],
                        [sentinel, "", "", "3", ""],
                    ]
                },
            )
            assert client._sync_get_department_concurrency() == {"Vinyl": 3}, sentinel

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_sentinel_is_matched_after_trimming(self, mock_build, mock_creds):
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Status"],
                    ["  MAX CONCURRENCY  ", "", "", "3", ""],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {"Vinyl": 3}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_blank_spacer_rows_are_skipped(self, mock_build, mock_creds):
        """A fully empty row above the cap row must not abort the scan."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Status"],
                    [],
                    ["John", "tech-1", "Technician", "1", "Active"],
                    ["MAX CONCURRENCY", "", "", "4", ""],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {"Vinyl": 4}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_whitespace_padded_cap_value_parses(self, mock_build, mock_creds):
        """A padded number is still a cap; a cell holding only spaces is not.

        Both spellings are produced by hand-editing the sheet. The spaces-only
        cell is the dangerous one: if it were read as a cap of 0 (or crashed the
        parse) the department would show zero bookable slots.
        """
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Tint", "Status"],
                    ["MAX CONCURRENCY", "", "", "  2  ", "   ", ""],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {"Vinyl": 2}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_negative_and_decimal_caps_are_no_cap(self, mock_build, mock_creds):
        """A nonsense cap must mean "unlimited", never "zero bookable slots"."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Tint", "Status"],
                    ["MAX CONCURRENCY", "", "", "-2", "1.5", ""],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_truncated_cap_row_leaves_later_departments_uncapped(self, mock_build, mock_creds):
        """Sheets drops trailing blanks; the missing columns must be "no cap"."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Tint", "Detail", "Status"],
                    ["MAX CONCURRENCY", "", "", "2"],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {"Vinyl": 2}

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_empty_and_header_only_tab_yield_no_caps(self, mock_build, mock_creds):
        assert self._client(mock_build, {"values": []})._sync_get_department_concurrency() == {}
        assert (
            self._client(
                mock_build, {"values": [["Name", "ID", "Role", "Vinyl", "Status"]]}
            )._sync_get_department_concurrency()
            == {}
        )

    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    def test_cap_row_may_appear_below_the_techs(self, mock_build, mock_creds):
        """The sheet owner can park the cap row anywhere; position must not matter."""
        client = self._client(
            mock_build,
            {
                "values": [
                    ["Name", "ID", "Role", "Vinyl", "Status"],
                    ["John", "tech-1", "Technician", "1", "Active"],
                    ["Jane", "tech-2", "Technician", "2", "Active"],
                    ["MAX CONCURRENCY", "", "", "1", ""],
                ]
            },
        )

        assert client._sync_get_department_concurrency() == {"Vinyl": 1}
        assert set(client._sync_get_tech_departments()) == {"tech-1", "tech-2"}


class TestSheetsClientHealthCheck:
    """Tests for the /health dependency probe."""

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_returns_true_when_sheet_is_reachable(self, mock_build, mock_creds):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, {"values": [["Name"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert await client.health_check() is True

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_returns_false_when_api_raises(self, mock_build, mock_creds):
        """An API/permission failure must surface as unhealthy, not as an exception.

        health_check is awaited by the /health endpoint; a raised error there
        would turn a degraded dependency into a 500.
        """
        from sheets_client import SheetsClient

        service = _mock_sheet(mock_build, {"values": []})
        _execute_mock(service).side_effect = RuntimeError("403 permission denied")
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert await client.health_check() is False

    @pytest.mark.asyncio
    @patch("sheets_client.build")
    async def test_returns_false_when_credentials_cannot_be_loaded(self, mock_build):
        """A missing/invalid key file must report unhealthy rather than crash."""
        from sheets_client import SheetsClient

        client = SheetsClient(spreadsheet_id="test-id", credentials_path="/nope/missing.json")

        with patch(
            "sheets_client.service_account.Credentials.from_service_account_file",
            side_effect=FileNotFoundError("missing.json"),
        ):
            assert await client.health_check() is False

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_does_not_serve_a_cached_result(self, mock_build, mock_creds):
        """A health check must probe live; a cached "OK" would hide an outage."""
        from sheets_client import SheetsClient

        service = _mock_sheet(mock_build, {"values": [["Name"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        await client.health_check()
        _execute_mock(service).side_effect = RuntimeError("sheet went away")

        assert await client.health_check() is False
        assert len(client._cache) == 0


class TestSheetsClientAsyncWrappers:
    """The async wrappers must return the same results as their sync bodies."""

    SHEET = {
        "values": [
            ["Name", "ID", "Role", "Vinyl", "Tint", "Status"],
            ["MAX CONCURRENCY", "", "", "2", "", ""],
            ["John", "tech-1", "Technician", "1", "0", "Active"],
            ["Jane", "tech-2", "Technician", "0", "1", "Inactive"],
        ]
    }

    def _client(self, mock_build):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, self.SHEET)
        return SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_tech_departments(self, mock_build, mock_creds):
        client = self._client(mock_build)
        result = await client.get_tech_departments()

        assert set(result) == {"tech-1"}
        assert result["tech-1"]["departments"] == {"Vinyl": 1, "Tint": 0}

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_tech_departments_passes_active_filter_through(self, mock_build, mock_creds):
        """The async wrapper must forward active_tech_ids, not drop it."""
        client = self._client(mock_build)

        assert await client.get_tech_departments(active_tech_ids=set()) == {}
        assert set(await client.get_tech_departments(active_tech_ids={"tech-1"})) == {"tech-1"}

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_all_departments(self, mock_build, mock_creds):
        client = self._client(mock_build)

        assert await client.get_all_departments() == ["Vinyl", "Tint"]

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_department_concurrency(self, mock_build, mock_creds):
        client = self._client(mock_build)

        assert await client.get_department_concurrency() == {"Vinyl": 2}

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_max_concurrency_for_department(self, mock_build, mock_creds):
        client = self._client(mock_build)

        assert await client.get_max_concurrency_for_department("Vinyl") == 2
        assert await client.get_max_concurrency_for_department("Tint") is None

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_get_service_departments_and_department_for_service(self, mock_build, mock_creds):
        from sheets_client import SheetsClient

        _mock_sheet(
            mock_build, {"values": [["Service", "Department"], ["Align", "Alignment/Tech"]]}
        )
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert await client.get_service_departments() == {"Align": "Alignment/Tech"}
        assert await client.get_department_for_service("Align") == "Alignment"
        assert await client.get_department_for_service("Missing") is None

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_read_sheet_async_wrapper(self, mock_build, mock_creds):
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, {"values": [["a", "b"]]})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert await client._read_sheet("'Tech/Dept'!A:Z") == [["a", "b"]]

    @pytest.mark.asyncio
    @patch("sheets_client.service_account.Credentials.from_service_account_file")
    @patch("sheets_client.build")
    async def test_missing_values_key_reads_as_empty(self, mock_build, mock_creds):
        """A range with no data omits "values" entirely; treat it as no rows."""
        from sheets_client import SheetsClient

        _mock_sheet(mock_build, {})
        client = SheetsClient(spreadsheet_id="test-id", credentials_path="test.json")

        assert await client._read_sheet("'Tech/Dept'!A:Z") == []


class TestGetSheetsClientFactory:
    """Tests for the module-level cached factory."""

    def test_returns_the_same_cached_instance(self):
        """The singleton exists so the TTL cache is shared across requests.

        A new client per call would mean every request re-reads the sheet.
        """
        import sheets_client as module

        module.get_sheets_client.cache_clear()
        try:
            with patch.dict(os.environ, {"GOOGLE_SHEETS_ID": "env-sheet"}, clear=True):
                first = module.get_sheets_client()
                second = module.get_sheets_client()

            assert first is second
            assert first.spreadsheet_id == "env-sheet"
        finally:
            module.get_sheets_client.cache_clear()

    def test_propagates_missing_configuration(self):
        """A misconfigured deployment must fail at startup, not return a broken client."""
        import sheets_client as module

        module.get_sheets_client.cache_clear()
        try:
            with patch.dict(os.environ, {}, clear=True):
                with pytest.raises(ValueError):
                    module.get_sheets_client()
        finally:
            module.get_sheets_client.cache_clear()
