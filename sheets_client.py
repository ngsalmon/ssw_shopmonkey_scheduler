"""Google Sheets client for reading service/tech department mappings."""

import asyncio
import os
from functools import lru_cache

import structlog
from cachetools import TTLCache
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = structlog.get_logger(__name__)

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
            "sheets_client_initialized",
            spreadsheet_id=self.spreadsheet_id,
            cache_ttl=cache_ttl,
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

    def _sync_read_sheet(self, range_name: str, use_cache: bool = True) -> list[list[str]]:
        """Synchronous implementation of sheet reading with caching."""
        cache_key = f"sheet:{range_name}"

        # Check cache first
        if use_cache and cache_key in self._cache:
            logger.debug("sheets_cache_hit", range_name=range_name)
            return self._cache[cache_key]

        logger.debug("sheets_cache_miss", range_name=range_name)
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
            logger.debug("sheets_data_cached", range_name=range_name, row_count=len(data))

        return data

    async def _read_sheet(self, range_name: str, use_cache: bool = True) -> list[list[str]]:
        """Read data from a sheet range with optional caching (async wrapper)."""
        return await asyncio.to_thread(self._sync_read_sheet, range_name, use_cache)

    def clear_cache(self) -> None:
        """Clear all cached data. Call this after making changes to the sheet."""
        self._cache.clear()
        logger.info("sheets_cache_cleared")

    def _sync_get_service_departments(self) -> dict[str, str]:
        """Synchronous implementation of get_service_departments."""
        range_name = f"'{self.SERVICE_DEPARTMENTS_TAB}'!A:B"
        rows = self._sync_read_sheet(range_name)

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

    async def get_service_departments(self) -> dict[str, str]:
        """
        Read Bookable Canned Services tab and return mapping.

        Actual schema:
            Column A: Bookable Service Name
            Column B: Department

        Returns:
            Dict mapping service_name to department
        """
        return await asyncio.to_thread(self._sync_get_service_departments)

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

    def _sync_get_department_for_service(self, service_name: str) -> str | None:
        """Synchronous implementation of get_department_for_service."""
        mappings = self._sync_get_service_departments()
        department = mappings.get(service_name)
        if department:
            return self._normalize_department(department)
        return None

    async def get_department_for_service(self, service_name: str) -> str | None:
        """Get the normalized department for a specific service name."""
        return await asyncio.to_thread(self._sync_get_department_for_service, service_name)

    def _sync_get_tech_departments(self) -> dict[str, dict]:
        """Synchronous implementation of get_tech_departments."""
        logger.debug("fetching_tech_departments")
        range_name = f"'{self.TECH_DEPARTMENTS_TAB}'!A:Z"
        rows = self._sync_read_sheet(range_name)

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

                # Parse department priorities (0=not qualified, 1+=priority, lower=higher)
                departments = {}
                for i, dept_name in enumerate(department_names):
                    col_index = dept_start_index + i
                    if col_index < len(row):
                        value = row[col_index].strip().upper()
                        # Support both old boolean format and new priority format
                        if value in ("TRUE", "YES", "X"):
                            departments[dept_name] = 1  # Treat as priority 1
                        elif value in ("FALSE", "NO", ""):
                            departments[dept_name] = 0  # Not qualified
                        else:
                            try:
                                departments[dept_name] = int(value)
                            except ValueError:
                                departments[dept_name] = 0
                    else:
                        departments[dept_name] = 0

                result[tech_id] = {
                    "tech_name": tech_name,
                    "role": role,
                    "departments": departments,
                    "status": status,
                }

        return result

    async def get_tech_departments(self) -> dict[str, dict]:
        """
        Read Tech/Dept tab and return mapping.

        Actual schema:
        | Name  | ID      | Primary Role | Vinyl | Alignment | Tint | Detail | Bedliner | Status |
        | John  | user123 | Technician   | 1     | 0         | ...  | ...    | ...      | Active |

        Priority values: 0=not qualified, 1=highest priority, 2=second priority, etc.

        Returns:
            Dict mapping tech_id to {tech_name, role, departments: {dept_name: int}, status}
        """
        return await asyncio.to_thread(self._sync_get_tech_departments)

    def _sync_get_techs_for_department(self, department: str) -> list[dict]:
        """Synchronous implementation of get_techs_for_department."""
        logger.debug("getting_techs_for_department", department=department)
        tech_mappings = self._sync_get_tech_departments()

        qualified_techs = []
        for tech_id, tech_info in tech_mappings.items():
            priority = tech_info["departments"].get(department, 0)
            if priority > 0:  # 0 means not qualified
                qualified_techs.append(
                    {
                        "tech_id": tech_id,
                        "tech_name": tech_info["tech_name"],
                        "priority": priority,
                    }
                )

        # Sort by priority (1 is highest priority, lower numbers first)
        qualified_techs.sort(key=lambda t: t["priority"])

        logger.debug(
            "found_qualified_techs",
            department=department,
            tech_count=len(qualified_techs),
        )
        return qualified_techs

    async def get_techs_for_department(self, department: str) -> list[dict]:
        """
        Get all technicians qualified for a specific department, sorted by priority.

        Args:
            department: Department name to filter by

        Returns:
            List of {tech_id, tech_name, priority} for qualified technicians,
            sorted by priority (1=highest priority first)
        """
        return await asyncio.to_thread(self._sync_get_techs_for_department, department)

    def _sync_get_all_departments(self) -> list[str]:
        """Synchronous implementation of get_all_departments."""
        range_name = f"'{self.TECH_DEPARTMENTS_TAB}'!A1:Z1"
        rows = self._sync_read_sheet(range_name)

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

    async def get_all_departments(self) -> list[str]:
        """Get list of all department names from the Tech/Dept tab."""
        return await asyncio.to_thread(self._sync_get_all_departments)

    async def health_check(self) -> bool:
        """
        Perform a lightweight health check against the Google Sheets API.

        Returns True if the sheet is accessible, False otherwise.
        """
        try:
            # Try to read just the header row
            range_name = f"'{self.TECH_DEPARTMENTS_TAB}'!A1:A1"
            await self._read_sheet(range_name, use_cache=False)
            return True
        except Exception:
            return False

    def get_cache_status(self) -> dict:
        """Return information about the current cache state."""
        return {
            "cache_size": len(self._cache),
            "cache_ttl_seconds": self._cache_ttl,
            "cache_maxsize": self._cache.maxsize,
        }


# Cached instance for reuse
@lru_cache(maxsize=1)
def get_sheets_client() -> SheetsClient:
    """Get a cached SheetsClient instance."""
    return SheetsClient()
