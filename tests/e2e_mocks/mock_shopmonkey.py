"""In-process fake ShopmonkeyClient for E2E tests."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from shopmonkey_client import (
    ShopmonkeyAPIError,
    ShopmonkeyNetworkError,
    ShopmonkeyTimeoutError,
)

from .state import STATE, MockState

logger = structlog.get_logger(__name__)


def _raise_if_error(endpoint: str, state: MockState) -> None:
    err = state.errors.get(endpoint)
    if not err:
        return
    if err.get("timeout"):
        raise ShopmonkeyTimeoutError(f"Injected timeout for {endpoint}")
    if err.get("network"):
        raise ShopmonkeyNetworkError(f"Injected network error for {endpoint}")
    raise ShopmonkeyAPIError(
        err.get("message", f"Injected error for {endpoint}"),
        status_code=err.get("status_code", 500),
    )


def _parse_iso_to_date(iso_str: str) -> str:
    """Strip ISO timestamp to YYYY-MM-DD for date matching."""
    return iso_str.split("T")[0]


class MockShopmonkeyClient:
    """Drop-in replacement for ShopmonkeyClient backed by MockState."""

    def __init__(self, state: MockState | None = None) -> None:
        self._state = state if state is not None else STATE

    @property
    def state(self) -> MockState:
        return self._state

    async def close(self) -> None:
        return None

    async def get_bookable_canned_services(self) -> list[dict[str, Any]]:
        _raise_if_error("get_bookable_canned_services", self._state)
        return [
            svc.to_canned_service_dict()
            for svc in self._state.services.values()
            if svc.bookable
        ]

    async def get_canned_service(self, service_id: str) -> dict[str, Any] | None:
        _raise_if_error("get_canned_service", self._state)
        svc = self._state.services.get(service_id)
        return svc.to_canned_service_dict() if svc else None

    async def get_appointments_for_date(
        self, date_str: str, tech_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        _raise_if_error("get_appointments_for_date", self._state)
        result: list[dict[str, Any]] = []
        tech_filter = set(tech_ids) if tech_ids else None
        for appt in self._state.appointments:
            if _parse_iso_to_date(appt.start_date) != date_str:
                continue
            if tech_filter is not None and appt.technician_id not in tech_filter:
                continue
            result.append(appt.to_dict())
        return result

    async def find_or_create_customer(
        self,
        first_name: str,
        last_name: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        _raise_if_error("find_or_create_customer", self._state)
        for c in self._state.customers:
            if email and c.email == email:
                return c.to_dict()
            if phone and c.phone == phone:
                return c.to_dict()
        from .state import MockCustomer

        customer = MockCustomer(
            id=f"cust_{uuid.uuid4().hex[:8]}",
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
        )
        self._state.customers.append(customer)
        return customer.to_dict()

    async def find_or_create_vehicle(
        self,
        customer_id: str,
        year: int,
        make: str,
        model: str,
        vin: str | None = None,
    ) -> dict[str, Any]:
        _raise_if_error("find_or_create_vehicle", self._state)
        for v in self._state.vehicles:
            if vin and v.vin == vin:
                return v.to_dict()
            if (
                v.customer_id == customer_id
                and v.year == year
                and v.make == make
                and v.model == model
            ):
                return v.to_dict()
        from .state import MockVehicle

        vehicle = MockVehicle(
            id=f"veh_{uuid.uuid4().hex[:8]}",
            customer_id=customer_id,
            year=year,
            make=make,
            model=model,
            vin=vin,
        )
        self._state.vehicles.append(vehicle)
        return vehicle.to_dict()

    async def create_appointment(
        self,
        customer_id: str,
        vehicle_id: str,
        start_date: str,
        end_date: str,
        title: str | None = None,
        notes: str | None = None,
        technician_id: str | None = None,
        color: str = "blue",
        order_id: str | None = None,
    ) -> dict[str, Any]:
        _raise_if_error("create_appointment", self._state)
        payload = {
            "customer_id": customer_id,
            "vehicle_id": vehicle_id,
            "start_date": start_date,
            "end_date": end_date,
            "title": title,
            "notes": notes,
            "technician_id": technician_id,
            "color": color,
            "order_id": order_id,
        }
        self._state.recorded_create_appointment_payloads.append(payload)
        appt = self._state.add_appointment(
            technician_id=technician_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            name=title or "Online Booking",
        )
        result = appt.to_dict()
        result["orderId"] = order_id
        return result

    async def get_workflow_statuses(self) -> list[dict[str, Any]]:
        _raise_if_error("get_workflow_statuses", self._state)
        # Default Shopmonkey-style workflow columns. Tests can override via
        # /test/state if they need a custom set.
        return [
            {"id": "ws_scheduled", "name": "Scheduled"},
            {"id": "ws_in_progress", "name": "In Progress"},
            {"id": "ws_invoices", "name": "Invoices"},
        ]

    async def get_workflow_status_id(self, name: str) -> str | None:
        for s in await self.get_workflow_statuses():
            if s.get("name") == name:
                return s.get("id")
        return None

    async def create_order(
        self,
        customer_id: str,
        vehicle_id: str,
        workflow_status_id: str,
        status: str = "Estimate",
        color: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        _raise_if_error("create_order", self._state)
        order_id = f"order_{uuid.uuid4().hex[:8]}"
        order = {
            "id": order_id,
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "workflowStatusId": workflow_status_id,
            "status": status,
            "color": color,
            "name": name,
            "number": str(len(self._state.recorded_create_order_payloads) + 1000),
        }
        self._state.recorded_create_order_payloads.append(
            {
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "workflow_status_id": workflow_status_id,
                "status": status,
                "color": color,
                "name": name,
            }
        )
        self._state.orders[order_id] = order
        return order

    async def attach_services_to_order(
        self,
        order_id: str,
        services: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        _raise_if_error("attach_services_to_order", self._state)
        self._state.recorded_attach_services_payloads.append(
            {"order_id": order_id, "services": services}
        )
        attached = []
        for svc in services:
            attached.append(
                {
                    "id": f"svc_{uuid.uuid4().hex[:8]}",
                    "orderId": order_id,
                    "name": svc.get("name", ""),
                    "cannedServiceId": svc.get("cannedServiceId"),
                    "labors": svc.get("labors", []),
                }
            )
        self._state.orders.setdefault(order_id, {}).setdefault("services", []).extend(attached)
        return attached

    async def get_users(self) -> list[dict[str, Any]]:
        _raise_if_error("get_users", self._state)
        return [
            {
                "id": t.tech_id,
                "firstName": t.tech_name.split(" ")[0],
                "lastName": " ".join(t.tech_name.split(" ")[1:]) or "",
                "active": t.active_in_shopmonkey,
            }
            for t in self._state.techs.values()
        ]

    async def get_active_user_ids(self) -> set[str]:
        _raise_if_error("get_active_user_ids", self._state)
        return {t.tech_id for t in self._state.techs.values() if t.active_in_shopmonkey}

    async def health_check(self) -> bool:
        # Honor injected error so tests can drive readiness probe to unhealthy.
        if "health_check" in self._state.errors:
            return False
        return True

    async def get_appointment(self, appointment_id: str) -> dict[str, Any] | None:
        _raise_if_error("get_appointment", self._state)
        for appt in self._state.appointments:
            if appt.id == appointment_id:
                return appt.to_dict()
        return None

    async def delete_appointment(self, appointment_id: str) -> bool:
        _raise_if_error("delete_appointment", self._state)
        before = len(self._state.appointments)
        self._state.appointments = [
            a for a in self._state.appointments if a.id != appointment_id
        ]
        return len(self._state.appointments) < before
