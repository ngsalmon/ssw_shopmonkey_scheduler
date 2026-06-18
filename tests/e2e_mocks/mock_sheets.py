"""In-process fake SheetsClient for E2E tests."""

from __future__ import annotations

from typing import Any

from .state import STATE, MockState


class MockSheetsClient:
    """Drop-in replacement for SheetsClient backed by MockState.

    Implements only the methods main.py actually calls plus a few utility
    methods (health_check, get_cache_status, clear_cache) used by health probes.
    """

    SERVICE_DEPARTMENTS_TAB = "Bookable Canned Services"
    TECH_DEPARTMENTS_TAB = "Tech/Dept"

    def __init__(self, state: MockState | None = None) -> None:
        self._state = state if state is not None else STATE

    @property
    def state(self) -> MockState:
        return self._state

    def clear_cache(self) -> None:
        return None

    async def get_service_departments(self) -> dict[str, str]:
        # Not used by main.py at runtime (Shopmonkey labels drive departments),
        # but expose for parity with the real client.
        result: dict[str, str] = {}
        for svc in self._state.services.values():
            if svc.labels:
                result[svc.name] = svc.labels[0]
        return result

    async def get_department_for_service(self, service_name: str) -> str | None:
        for svc in self._state.services.values():
            if svc.name == service_name and svc.labels:
                return svc.labels[0]
        return None

    async def get_tech_departments(
        self, active_tech_ids: set[str] | None = None
    ) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for tech in self._state.techs.values():
            if tech.status.lower() == "inactive":
                continue
            if active_tech_ids is not None and tech.tech_id not in active_tech_ids:
                continue
            result[tech.tech_id] = {
                "tech_name": tech.tech_name,
                "role": "Technician",
                "departments": dict(tech.departments),
                "status": tech.status,
            }
        return result

    async def get_techs_for_department(
        self, department: str, active_tech_ids: set[str] | None = None
    ) -> list[dict]:
        mapping = await self.get_tech_departments(active_tech_ids)
        qualified = []
        for tech_id, tech_info in mapping.items():
            priority = tech_info["departments"].get(department, 0)
            if priority > 0:
                qualified.append(
                    {
                        "tech_id": tech_id,
                        "tech_name": tech_info["tech_name"],
                        "priority": priority,
                    }
                )
        qualified.sort(key=lambda t: t["priority"])
        return qualified

    async def get_all_departments(self) -> list[str]:
        seen: set[str] = set()
        for tech in self._state.techs.values():
            seen.update(tech.departments.keys())
        return sorted(seen)

    async def get_department_concurrency(self) -> dict[str, int]:
        return dict(self._state.department_concurrency)

    async def get_max_concurrency_for_department(self, department: str) -> int | None:
        return self._state.department_concurrency.get(department)

    async def health_check(self) -> bool:
        if "sheets_health_check" in self._state.errors:
            return False
        return True

    def get_cache_status(self) -> dict[str, Any]:
        return {"cache_size": 0, "cache_ttl_seconds": 0, "cache_maxsize": 0}
