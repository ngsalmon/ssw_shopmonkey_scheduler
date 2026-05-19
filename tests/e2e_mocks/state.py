"""Shared in-memory state for the E2E mocks.

A single module-level `STATE` object is mutated by both the mock clients
and the /test/state router. Tests POST scenario data; mocks read from it.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockTech:
    tech_id: str
    tech_name: str
    departments: dict[str, int]  # dept name -> priority (0 = not qualified)
    status: str = "Active"  # "Inactive" excludes via sheet override
    active_in_shopmonkey: bool = True

    def to_sheet_row(self) -> dict[str, Any]:
        return {
            "tech_id": self.tech_id,
            "tech_name": self.tech_name,
            "role": "Technician",
            "departments": dict(self.departments),
            "status": self.status,
        }


@dataclass
class MockService:
    id: str
    name: str
    total_cents: int | None = None
    bookable: bool = True
    labels: list[str] = field(default_factory=list)
    labor_hours: float | None = None

    def to_canned_service_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "totalCents": self.total_cents,
            "bookable": self.bookable,
            "labels": [{"name": label} for label in self.labels],
            "labors": (
                [{"hours": self.labor_hours}] if self.labor_hours is not None else []
            ),
        }


@dataclass
class MockAppointment:
    id: str
    technician_id: str | None
    start_date: str  # ISO with offset, matches Shopmonkey API shape
    end_date: str
    customer_id: str | None = None
    vehicle_id: str | None = None
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "technicianId": self.technician_id,
            "userId": self.technician_id,
            "startDate": self.start_date,
            "endDate": self.end_date,
            "customerId": self.customer_id,
            "vehicleId": self.vehicle_id,
            "name": self.name,
        }


@dataclass
class MockCustomer:
    id: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "firstName": self.first_name,
            "lastName": self.last_name,
            "email": self.email,
            "phone": self.phone,
        }


@dataclass
class MockVehicle:
    id: str
    customer_id: str
    year: int
    make: str
    model: str
    vin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "customerId": self.customer_id,
            "year": self.year,
            "make": self.make,
            "model": self.model,
            "vin": self.vin,
        }


class MockState:
    """Mutable in-memory scenario state shared by the mock clients."""

    def __init__(self) -> None:
        self.services: dict[str, MockService] = {}
        self.techs: dict[str, MockTech] = {}
        self.appointments: list[MockAppointment] = []
        self.customers: list[MockCustomer] = []
        self.vehicles: list[MockVehicle] = []
        self.orders: dict[str, dict[str, Any]] = {}
        # Endpoint name -> error spec like {"status_code": 500, "message": "..."}
        # or {"network": True} / {"timeout": True}. Cleared on reset.
        self.errors: dict[str, dict[str, Any]] = {}
        # Recorded calls for assertions
        self.recorded_create_appointment_payloads: list[dict[str, Any]] = []
        self.recorded_create_order_payloads: list[dict[str, Any]] = []
        self.recorded_attach_services_payloads: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.services.clear()
        self.techs.clear()
        self.appointments.clear()
        self.customers.clear()
        self.vehicles.clear()
        self.orders.clear()
        self.errors.clear()
        self.recorded_create_appointment_payloads.clear()
        self.recorded_create_order_payloads.clear()
        self.recorded_attach_services_payloads.clear()

    def load_default(self) -> None:
        """Load the default realistic fixture: all categories, all sizes."""
        self.reset()
        self._load_default_services()
        self._load_default_techs()

    def add_appointment(
        self,
        technician_id: str | None,
        start_date: str,
        end_date: str,
        customer_id: str | None = None,
        vehicle_id: str | None = None,
        name: str = "Existing booking",
    ) -> MockAppointment:
        appt = MockAppointment(
            id=f"appt_{uuid.uuid4().hex[:8]}",
            technician_id=technician_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            name=name,
        )
        self.appointments.append(appt)
        return appt

    def apply_scenario(self, payload: dict[str, Any]) -> None:
        """Apply a partial scenario from /test/state POST body.

        Recognized keys (all optional, applied in this order):
          reset (bool)
          load_default (bool)
          services (list[dict])
          techs (list[dict])
          appointments (list[dict])
          errors (dict[str, dict])
        """
        if payload.get("reset"):
            self.reset()
        if payload.get("load_default"):
            self.load_default()
        if "services" in payload:
            self.services = {
                s["id"]: MockService(
                    id=s["id"],
                    name=s["name"],
                    total_cents=s.get("totalCents") or s.get("total_cents"),
                    bookable=s.get("bookable", True),
                    labels=s.get("labels", []),
                    labor_hours=s.get("laborHours") or s.get("labor_hours"),
                )
                for s in payload["services"]
            }
        if "techs" in payload:
            self.techs = {
                t["tech_id"]: MockTech(
                    tech_id=t["tech_id"],
                    tech_name=t["tech_name"],
                    departments=dict(t.get("departments", {})),
                    status=t.get("status", "Active"),
                    active_in_shopmonkey=t.get("active_in_shopmonkey", True),
                )
                for t in payload["techs"]
            }
        if "appointments" in payload:
            self.appointments = []
            for a in payload["appointments"]:
                self.add_appointment(
                    technician_id=a.get("technician_id") or a.get("technicianId"),
                    start_date=a["start_date"] if "start_date" in a else a["startDate"],
                    end_date=a["end_date"] if "end_date" in a else a["endDate"],
                    customer_id=a.get("customer_id") or a.get("customerId"),
                    vehicle_id=a.get("vehicle_id") or a.get("vehicleId"),
                    name=a.get("name", "Existing booking"),
                )
        if "errors" in payload:
            self.errors = copy.deepcopy(payload["errors"])

    def snapshot(self) -> dict[str, Any]:
        return {
            "services": [
                {
                    "id": s.id,
                    "name": s.name,
                    "totalCents": s.total_cents,
                    "bookable": s.bookable,
                    "labels": list(s.labels),
                    "laborHours": s.labor_hours,
                }
                for s in self.services.values()
            ],
            "techs": [
                {
                    "tech_id": t.tech_id,
                    "tech_name": t.tech_name,
                    "departments": dict(t.departments),
                    "status": t.status,
                    "active_in_shopmonkey": t.active_in_shopmonkey,
                }
                for t in self.techs.values()
            ],
            "appointments": [a.to_dict() for a in self.appointments],
            "orders": list(self.orders.values()),
            "errors": copy.deepcopy(self.errors),
            "recorded_create_appointment_payloads": list(
                self.recorded_create_appointment_payloads
            ),
            "recorded_create_order_payloads": list(self.recorded_create_order_payloads),
            "recorded_attach_services_payloads": list(self.recorded_attach_services_payloads),
        }

    # --- default fixture ---------------------------------------------------
    def _load_default_services(self) -> None:
        # Window Tint - all area/type combinations, including the XL SUV/Van
        # variants that triggered Anne's report.
        tint_areas = [
            "Full Coupe",
            "Full Sedan",
            "Full SUV",
            "Full XL SUV/Van (7 Window)",
            "Front Doors",
            "Windshield",
            "Sunstrip",
        ]
        for area in tint_areas:
            for tint_type, cents in [("Carbon", 25000), ("Ceramic", 45000)]:
                sid = f"svc_tint_{_slug(area)}_{tint_type.lower()}"
                self.services[sid] = MockService(
                    id=sid,
                    name=f"Window Tint - {area} - {tint_type}",
                    total_cents=cents,
                    labels=["Tint"],
                    labor_hours=1.5,
                )

        # Detail - all sizes x all variants
        sizes = ["Coupe", "Sedan", "SUV", "XL SUV/Van"]
        for size in sizes:
            for level in (1, 2):
                for kind, prefix in [("Interior", "int"), ("Exterior", "ext")]:
                    sid = f"svc_detail_{prefix}_l{level}_{_slug(size)}"
                    self.services[sid] = MockService(
                        id=sid,
                        name=f"Detail - {kind} Level {level} - {size}",
                        total_cents=15000 + level * 5000,
                        labels=["Detail"],
                        labor_hours=2.0 + level,
                    )

        # Headlight Restoration (Detail exception - stays enabled)
        self.services["svc_headlight"] = MockService(
            id="svc_headlight",
            name="Headlight Restoration",
            total_cents=8000,
            labels=["Detail"],
            labor_hours=1.0,
        )

        # Bedliner - exercises the buffer (cure time) logic.
        self.services["svc_bedliner_short_bed"] = MockService(
            id="svc_bedliner_short_bed",
            name="Bedliner - Short Bed",
            total_cents=50000,
            labels=["Bedliner"],
            labor_hours=2.0,
        )

        # Alignment - generic single-tech department.
        self.services["svc_alignment"] = MockService(
            id="svc_alignment",
            name="Alignment - 4 Wheel",
            total_cents=12000,
            labels=["Alignment"],
            labor_hours=1.0,
        )

        # Multi-day service (> 5 hours) - triggers multi-day branch in
        # availability.py:get_availability. Sized to fit a single 8.5h business
        # day with comfortable headroom so the slot calculation produces at
        # least one starting time.
        self.services["svc_multiday_ceramic"] = MockService(
            id="svc_multiday_ceramic",
            name="Ceramic Coating - Full Vehicle",
            total_cents=200000,
            labels=["Vinyl"],
            labor_hours=6.0,
        )

    def _load_default_techs(self) -> None:
        # Departments must match the labels used on services.
        # Priority 1 = highest; 0 = not qualified.
        self.techs["tech_alex"] = MockTech(
            tech_id="tech_alex",
            tech_name="Alex Tint",
            departments={
                "Tint": 1,
                "Detail": 0,
                "Bedliner": 0,
                "Alignment": 0,
                "Vinyl": 0,
            },
        )
        self.techs["tech_bri"] = MockTech(
            tech_id="tech_bri",
            tech_name="Bri Detail",
            departments={
                "Tint": 0,
                "Detail": 1,
                "Bedliner": 0,
                "Alignment": 0,
                "Vinyl": 0,
            },
        )
        self.techs["tech_cam"] = MockTech(
            tech_id="tech_cam",
            tech_name="Cam Multi",
            departments={
                "Tint": 2,
                "Detail": 2,
                "Bedliner": 1,
                "Alignment": 1,
                "Vinyl": 1,
            },
        )


def _slug(s: str) -> str:
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -/()":
            out.append("_")
    result = "".join(out)
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_")


# Module-level singleton, mutated by both the mock clients and /test/state.
STATE: MockState = MockState()
