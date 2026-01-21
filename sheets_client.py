"""Google Sheets client for reading service/tech department mappings."""

import logging
import os
import time
from functools import lru_cache

from cachetools import TTLCache
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Default cache TTL in seconds (5 minutes)
DEFAULT_CACHE_TTL = 300


class SheetsClient:
    """Client for reading scheduling configuration from Google Sheets."""

    # Tab names in the Google Sheet (must match actual sheet tab names)
    SERVICE_DEPARTMENTS_TAB = "Bookable Canned Services"  # Legacy - no longer used (using Shopmonkey labels)
    TECH_DEPARTMENTS_TAB = "Tech/Dept"

    def __init__(
        self,
        spreadsheet_id: str | None = None,
        credentials_path: str | None = None,
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ):
        self.spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEETS_ID")
        self.credentials_path = credentials_path or os.getenv(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )

        if not self.spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_ID is required")

        self._service = None
        self._cache_ttl = cache_ttl
        # Cache for sheet data: key -> (data, expiry_time)
        self._cache: TTLCache = TTLCache(maxsize=100, ttl=cache_ttl)

        logger.debug(
            "SheetsClient initialized with spreadsheet_id=%s, cache_ttl=%d",
            self.spreadsheet_id,
            cache_ttl,
        )

    def _get_service(self):
        if self._service is None:
            if self.credentials_path:
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_path,
                    scopes=["https://www.googleapis.com/auth/spreadsheets"],
                )
            else:
                # Use Application Default Credentials (ADC) - works on Cloud Run
                from google.auth import default

                credentials, _ = default(
                    scopes=["https://www.googleapis.com/auth/spreadsheets"]
                )

            self._service = build("sheets", "v4", credentials=credentials)
        return self._service

    def _read_sheet(self, range_name: str, use_cache: bool = True) -> list[list[str]]:
        """Read data from a sheet range with optional caching."""
        cache_key = f"sheet:{range_name}"

        # Check cache first
        if use_cache and cache_key in self._cache:
            logger.debug("Cache hit for %s", range_name)
            return self._cache[cache_key]

        logger.debug("Cache miss for %s, fetching from API", range_name)
        service = self._get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        data = result.get("values", [])

        # Store in cache
        if use_cache:
            self._cache[cache_key] = data
            logger.debug("Cached %s with %d rows", range_name, len(data))

        return data

    def clear_cache(self) -> None:
        """Clear all cached data. Call this after making changes to the sheet."""
        self._cache.clear()
        logger.info("Sheet cache cleared")

    def get_service_departments(self) -> dict[str, str]:
        """
        Read Bookable Canned Services tab and return mapping.

        Actual schema:
            Column A: Bookable Service Name
            Column B: Department

        Returns:
            Dict mapping service_name to department
        """
        range_name = f"'{self.SERVICE_DEPARTMENTS_TAB}'!A:B"
        rows = self._read_sheet(range_name)

        if not rows:
            return {}

        # Skip header row
        result = {}
        for row in rows[1:]:
            if len(row) >= 2:
                service_name = row[0].strip()
                department = row[1].strip()
                if service_name and department:
                    result[service_name] = department

        return result

    def _normalize_department(self, department: str) -> str:
        """
        Normalize department names to match Tech/Dept column headers.

        The Services sheet may have different naming than Tech/Dept columns:
        - "Alignment/Tech" -> "Alignment"
        - "Ceramic/Paint Restoration" -> (no match, could map to Vinyl or similar)
        """
        # Mapping from service department names to tech department column names
        dept_mapping = {
            "Alignment/Tech": "Alignment",
            # Add other mappings as needed
        }

        return dept_mapping.get(department, department)

    def get_department_for_service(self, service_name: str) -> str | None:
        """Get the normalized department for a specific service name."""
        mappings = self.get_service_departments()
        department = mappings.get(service_name)
        if department:
            return self._normalize_department(department)
        return None

    def get_tech_departments(self) -> dict[str, dict]:
        """
        Read Tech/Dept tab and return mapping.

        Actual schema:
        | Name  | ID      | Primary Role | Vinyl | Alignment | Tint | Detail | Bedliner | Status |
        | John  | user123 | Technician   | TRUE  | FALSE     | ...  | ...    | ...      | Active |

        Returns:
            Dict mapping tech_id to {tech_name, role, departments: {dept_name: bool}, status}
        """
        logger.debug("Fetching tech departments")
        range_name = f"'{self.TECH_DEPARTMENTS_TAB}'!A:Z"
        rows = self._read_sheet(range_name)

        if not rows or len(rows) < 2:
            return {}

        # First row is header
        # Columns: Name (A), ID (B), Primary Role (C), Departments (D+), Status (last)
        header = rows[0]

        # Find department columns (between Primary Role and Status)
        # Departments start at index 3 (column D)
        # Status is at the last column
        status_col_index = None
        for i, col_name in enumerate(header):
            if "status" in col_name.lower():
                status_col_index = i
                break

        # Department columns are from index 3 up to (but not including) status column
        dept_start_index = 3
        dept_end_index = status_col_index if status_col_index else len(header)
        department_names = [d.strip() for d in header[dept_start_index:dept_end_index] if d.strip()]

        result = {}
        for row in rows[1:]:
            if len(row) >= 2:
                tech_name = row[0].strip()
                tech_id = row[1].strip()
                role = row[2].strip() if len(row) > 2 else ""

                # Get status (last column) - default to Active if not found
                status = "Active"
                if status_col_index and len(row) > status_col_index:
                    status = row[status_col_index].strip()

                # Skip inactive technicians
                if status.lower() != "active":
                    continue

                # Skip if no tech_id
                if not tech_id:
                    continue

                # Parse department flags (TRUE/FALSE)
                departments = {}
                for i, dept_name in enumerate(department_names):
                    col_index = dept_start_index + i
                    if col_index < len(row):
                        value = row[col_index].strip().upper()
                        departments[dept_name] = value in ("TRUE", "YES", "1", "X")
                    else:
                        departments[dept_name] = False

                result[tech_id] = {
                    "tech_name": tech_name,
                    "role": role,
                    "departments": departments,
                    "status": status,
                }

        return result

    def get_techs_for_department(self, department: str) -> list[dict[str, str]]:
        """
        Get all technicians qualified for a specific department.

        Args:
            department: Department name to filter by

        Returns:
            List of {tech_id, tech_name} for qualified technicians
        """
        logger.debug("Getting techs for department: %s", department)
        tech_mappings = self.get_tech_departments()

        qualified_techs = []
        for tech_id, tech_info in tech_mappings.items():
            if tech_info["departments"].get(department, False):
                qualified_techs.append(
                    {
                        "tech_id": tech_id,
                        "tech_name": tech_info["tech_name"],
                    }
                )

        logger.debug("Found %d qualified techs for %s", len(qualified_techs), department)
        return qualified_techs

    def get_all_departments(self) -> list[str]:
        """Get list of all department names from the Tech/Dept tab."""
        range_name = f"'{self.TECH_DEPARTMENTS_TAB}'!A1:Z1"
        rows = self._read_sheet(range_name)

        if not rows:
            return []

        header = rows[0]

        # Find status column to know where departments end
        status_col_index = None
        for i, col_name in enumerate(header):
            if "status" in col_name.lower():
                status_col_index = i
                break

        # Department names start from column D (index 3), up to status column
        dept_start_index = 3
        dept_end_index = status_col_index if status_col_index else len(header)
        return [d.strip() for d in header[dept_start_index:dept_end_index] if d.strip()]


# Cached instance for reuse
@lru_cache(maxsize=1)
def get_sheets_client() -> SheetsClient:
    """Get a cached SheetsClient instance."""
    return SheetsClient()
