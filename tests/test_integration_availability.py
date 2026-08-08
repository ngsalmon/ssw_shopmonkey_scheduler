"""Integration tests for availability ↔ booking interaction.

Two halves live in this file:

1. LIVE tests (bottom half of the "live" section, decorated with `@live`).
   Opt-in only: they carry the `integration` and `booking` markers (both
   deselected by default per pytest.ini) and skip when credentials are
   missing. Run explicitly with:

       pytest -s -m "integration and booking" tests/test_integration_availability.py

2. OFFLINE tests (second half of the file). These run in the normal suite.
   They drive the REAL FastAPI app and the REAL availability math, with
   in-process fakes substituted only at the true external boundaries
   (Shopmonkey HTTP, Google Sheets). That makes the seams between main.py
   and availability.py - advertise → book → re-advertise, multi-day
   rollover, department concurrency, timezone conversion, partial booking
   failures - observable end to end without credentials.

The live tests hit the REAL Shopmonkey + Google Sheets APIs and create real
appointments + repair orders on Salmon SpeedWorx's account. They do NOT
auto-clean. At the end of the run a summary is printed AND written to
`.omc/integration-test-records.md` listing every created appointment/order
so a human can delete them via the Shopmonkey UI.

Test slot strategy: all tests share one target date (~21 days out) but
each books a different time slot, so tests don't interfere even though
there is no per-test cleanup.

Service selection is dynamic from /services so the tests adapt if the
catalog changes; the chosen department combinations are documented in
docstrings and reflect the tech matrix on 2026-05-20.
"""

from __future__ import annotations

import os
import re
import sys
import time
from contextlib import ExitStack
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from shopmonkey_client import ShopmonkeyAPIError

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env before the module-level skip check below (main.py would load
# it later via lifespan, but the skip runs at import time).
load_dotenv(Path(__file__).parent.parent / ".env")

_required_env = ("SHOPMONKEY_API_TOKEN", "GOOGLE_SHEETS_ID")
_needs_live_env = pytest.mark.skipif(
    not all(os.getenv(v) for v in _required_env),
    reason=f"Live integration tests require {', '.join(_required_env)}",
)


def live(func):
    """Mark a test as hitting the real Shopmonkey + Google Sheets APIs.

    The marks are applied per-test rather than module-wide so the offline
    integration tests in the second half of this file (which need no
    credentials and create nothing) still run in the default suite.
    """
    for mark in (pytest.mark.integration, pytest.mark.booking, _needs_live_env):
        func = mark(func)
    return func


CONFIRMATION_RE = re.compile(r"^SM-\d{8}-[A-Z0-9]{6}$")

# Accumulates across tests. Printed AND written to a markdown file at the
# end of the module so the user has a paper trail for manual cleanup.
CLEANUP_LOG: list[dict[str, Any]] = []


# --------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def app_client():
    """Real FastAPI app + real Shopmonkey/Sheets clients via lifespan."""
    # Defer import so module-level pytest.skip can short-circuit.
    from main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def sm_http():
    """Synchronous httpx client to Shopmonkey, used only for read-back
    inspection (fetching the order/tech assignment after a booking).

    We use sync httpx instead of the async ShopmonkeyClient because the
    async client's httpx.AsyncClient binds to the event loop it's created
    in - and TestClient closes its own loop between calls, leaving the
    standalone client unusable. Sync httpx sidesteps the loop entirely.
    """
    base = os.environ.get("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
    token = os.environ["SHOPMONKEY_API_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
def sheets():
    """Standalone SheetsClient for inspecting the tech/dept mapping."""
    from sheets_client import SheetsClient

    return SheetsClient()


@pytest.fixture(scope="module")
def target_date() -> str:
    """Pick a weekday roughly 21 days out for all tests."""
    d = datetime.now() + timedelta(days=21)
    while d.weekday() in (5, 6):  # Sat, Sun
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def services_catalog(app_client) -> list[dict[str, Any]]:
    """Fetch the bookable services list once; tests pick by name."""
    resp = app_client.get("/services")
    assert resp.status_code == 200, resp.text
    return resp.json()["services"]


@pytest.fixture(scope="module")
def test_customer() -> dict[str, str]:
    """Stable test identity so find_or_create_customer reuses the same record.

    Email and phone both omitted:
    - Pydantic's EmailStr rejects reserved TLDs (.invalid / .test / .example)
    - Shopmonkey rejects 555-prefix test phone numbers ("Phone number not valid")
    - find_or_create_customer accepts name-only matches and returns the first
      same-name record on subsequent runs.
    """
    return {
        "firstName": "Claude",
        "lastName": "TestUser",
    }


@pytest.fixture(scope="module")
def test_vehicle() -> dict[str, Any]:
    return {"year": 2020, "make": "Test", "model": "TestVehicle"}


@pytest.fixture(scope="module", autouse=True)
def _emit_cleanup_log_at_end():
    """After the module's tests run, print + persist the appointment list."""
    yield
    if not CLEANUP_LOG:
        return

    lines = [
        "",
        "=" * 76,
        "CLAUDE INTEGRATION TEST — APPOINTMENTS CREATED (cleanup needed)",
        "=" * 76,
        "Delete these via the Shopmonkey UI (calendar or order list).",
        "",
    ]
    for i, entry in enumerate(CLEANUP_LOG, 1):
        lines.extend(
            [
                f"{i}. {entry['service_name']} @ {entry['when']}",
                f"   appointment_id: {entry['appointment_id']}",
                f"   order_id:       {entry['order_id'] or '<unknown>'}",
                f"   confirmation:   {entry['confirmation']}",
                f"   assigned_tech:  {entry['assigned_tech'] or '<unknown>'}",
                "",
            ]
        )
    text = "\n".join(lines)
    print(text)

    # Also persist to a file (some test runners hide -s output).
    out_dir = Path(__file__).parent.parent / ".omc"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "integration-test-records.md").write_text(text)


# --------------------------------------------------------------------- helpers


def _slot_iso(date_str: str, hhmm: str) -> str:
    """Build the naive-local ISO datetime that /book accepts."""
    return f"{date_str}T{hhmm}:00"


def _service_by_name(services: list[dict], name_substring: str) -> dict:
    """Find a service whose name contains substring (case-insensitive).

    Used so tests adapt when the catalog changes. Raises pytest.skip when
    no match is found - the dept config the test depends on isn't present.
    """
    matches = [s for s in services if name_substring.lower() in (s.get("name") or "").lower()]
    if not matches:
        pytest.skip(f"No bookable service matched {name_substring!r}")
    # Prefer exact substring match; if many, take the shortest service.
    matches.sort(key=lambda s: float(s.get("laborHours") or 99))
    return matches[0]


def _capacity_at(slots: list[dict], hhmm: str) -> int:
    """Return available_techs for the slot starting at hhmm, 0 if absent."""
    for s in slots:
        if s["start"] == hhmm:
            return s["available_techs"]
    return 0


def _retry_on_rate_limit(call):
    """Retry the given zero-arg callable on 502 (Shopmonkey 429 surfaces as
    our 502) with exponential backoff. Up to 4 attempts."""
    delay = 1.5
    last = None
    for attempt in range(4):
        resp = call()
        last = resp
        if resp.status_code != 502:
            return resp
        # 502 from our app is almost always a wrapped Shopmonkey 429.
        # Backoff and retry.
        time.sleep(delay)
        delay *= 2
    return last


def _availability(app_client, service_id: str, date_str: str) -> list[dict]:
    resp = _retry_on_rate_limit(
        lambda: app_client.get(f"/availability?service_id={service_id}&date={date_str}")
    )
    assert resp.status_code == 200, f"availability call failed: {resp.text}"
    return resp.json()["slots"]


def _book(
    app_client,
    service: dict,
    date_str: str,
    hhmm: str,
    customer: dict,
    vehicle: dict,
):
    """POST /book for the slot. Caller asserts on status."""
    # Slot end = start + ceiling(labor + buffer) — but the widget passes
    # whatever /availability returned. To keep things simple here, derive
    # from labor hours (no buffer needed for our test services).
    hours = float(service.get("laborHours") or 1.0)
    start_dt = datetime.strptime(f"{date_str} {hhmm}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(hours=hours)
    body = {
        "service_id": service["id"],
        "slot_start": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "slot_end": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "customer": customer,
        "vehicle": vehicle,
    }
    return _retry_on_rate_limit(lambda: app_client.post("/book", json=body))


def _record_for_cleanup(
    appointment_id: str,
    confirmation: str,
    service_name: str,
    when: str,
    sm_http: httpx.Client,
) -> dict[str, Any]:
    """Inspect the freshly created appointment for order_id + assigned tech,
    then append to the cleanup log. Uses sync httpx to avoid event-loop
    rebinding issues with TestClient's lifespan."""
    order_id: str | None = None
    assigned_tech: str | None = None
    try:
        appt_resp = sm_http.get(f"/v3/appointment/{appointment_id}")
        appt_resp.raise_for_status()
        appt = (appt_resp.json() or {}).get("data") or {}
        order_id = appt.get("orderId")
        if order_id:
            svc_resp = sm_http.get(f"/v3/order/{order_id}/service")
            svc_resp.raise_for_status()
            services = (svc_resp.json() or {}).get("data") or []
            for svc in services:
                for labor in svc.get("labors") or []:
                    if labor.get("technicianId"):
                        assigned_tech = labor["technicianId"]
                        break
                if assigned_tech:
                    break
    except Exception as e:
        print(f"  (couldn't fetch order/tech for {appointment_id}: {e})")

    entry = {
        "appointment_id": appointment_id,
        "order_id": order_id,
        "confirmation": confirmation,
        "service_name": service_name,
        "when": when,
        "assigned_tech": assigned_tech,
    }
    CLEANUP_LOG.append(entry)
    return entry


# --------------------------------------------------------------------- tests


@live
def test_01_booking_decrements_capacity_for_same_slot(
    app_client, services_catalog, target_date, test_customer, test_vehicle, sm_http
):
    """A single booking reduces available_techs at the same slot by 1.

    Uses Sales Consultation (3 qualified techs, 0.5h) - small enough that
    one booking doesn't fill the slot, big enough to see a clean decrement.
    """
    service = _service_by_name(services_catalog, "Sales Consultation")
    slot = "09:00"

    baseline_slots = _availability(app_client, service["id"], target_date)
    baseline = _capacity_at(baseline_slots, slot)
    assert baseline >= 1, (
        f"need >=1 tech available at {slot} for {service['name']} on "
        f"{target_date}; got {baseline}. Pick another date / inspect calendar."
    )

    resp = _book(app_client, service, target_date, slot, test_customer, test_vehicle)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert CONFIRMATION_RE.match(body["confirmation_number"]), body
    _record_for_cleanup(
        body["appointment_id"],
        body["confirmation_number"],
        service["name"],
        f"{target_date} {slot}",
        sm_http,
    )

    after_slots = _availability(app_client, service["id"], target_date)
    after = _capacity_at(after_slots, slot)
    assert after == baseline - 1, (
        f"expected capacity {baseline - 1} at {slot} after booking, got {after}"
    )


@live
def test_02_booking_does_not_affect_disjoint_dept(
    app_client,
    services_catalog,
    target_date,
    test_customer,
    test_vehicle,
    sm_http,
):
    """Booking a Window Tint service should leave Alignment capacity intact.

    Window Tint qualified = {Mina}; Alignment qualified = {Jerry, Grant,
    Chandler, Gus}. The sets are disjoint, so the assigned tech (Mina) is
    NOT in Alignment's pool and Alignment availability is unaffected.

    Pre-fix this would have failed: the old per-tech check was a no-op so
    every overlap reduced shop-wide capacity for every department.
    """
    tint = _service_by_name(services_catalog, "Window Tint - Rear Windshield")
    align = _service_by_name(services_catalog, "Alignment - Front End")
    slot = "10:00"

    tint_before = _capacity_at(_availability(app_client, tint["id"], target_date), slot)
    align_before = _capacity_at(_availability(app_client, align["id"], target_date), slot)
    assert tint_before >= 1, f"need >=1 Window Tint tech at {slot}; got {tint_before}"
    assert align_before >= 1, f"need >=1 Alignment tech at {slot}; got {align_before}"

    resp = _book(app_client, tint, target_date, slot, test_customer, test_vehicle)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = _record_for_cleanup(
        body["appointment_id"],
        body["confirmation_number"],
        tint["name"],
        f"{target_date} {slot}",
        sm_http,
    )

    tint_after = _capacity_at(_availability(app_client, tint["id"], target_date), slot)
    align_after = _capacity_at(_availability(app_client, align["id"], target_date), slot)

    assert tint_after == tint_before - 1, (
        f"Window Tint: expected {tint_before - 1}, got {tint_after} "
        f"(assigned tech: {entry['assigned_tech']})"
    )
    assert align_after == align_before, (
        f"Alignment should be unaffected by a Window Tint booking, but went "
        f"from {align_before} → {align_after} (Window Tint tech assigned: "
        f"{entry['assigned_tech']}). If this fails, the per-tech filter is "
        f"degraded back to shop-wide capacity."
    )


@live
def test_03_full_slot_returns_409_on_extra_booking(
    app_client, services_catalog, target_date, test_customer, test_vehicle, sm_http
):
    """A 1-tech department fills after one booking; the next attempt 409s.

    Custom Exhaust Consultation has 1 qualified tech (Zack), 0.5h service.
    Strongest test of the original Anne bug: if conflict detection ever
    regresses to a no-op, the second /book here will succeed and this
    test will fail loudly.
    """
    service = _service_by_name(services_catalog, "Custom Exhaust Consultation")
    slot = "11:00"

    before = _capacity_at(_availability(app_client, service["id"], target_date), slot)
    assert before >= 1, f"need >=1 tech at {slot} for {service['name']}; got {before}"

    first = _book(app_client, service, target_date, slot, test_customer, test_vehicle)
    assert first.status_code == 200, first.text
    _record_for_cleanup(
        first.json()["appointment_id"],
        first.json()["confirmation_number"],
        service["name"],
        f"{target_date} {slot}",
        sm_http,
    )

    mid = _capacity_at(_availability(app_client, service["id"], target_date), slot)
    assert mid == before - 1, f"expected {before - 1} after 1 booking, got {mid}"

    if before > 1:
        pytest.skip(
            f"Custom Exhaust has {before} techs available - 409 test requires "
            f"exactly 1 to keep the booking count small. Try a different date."
        )

    # Slot is now at 0 capacity. /availability should drop the slot or
    # report 0.
    final_slots = _availability(app_client, service["id"], target_date)
    final_capacity = _capacity_at(final_slots, slot)
    assert final_capacity == 0, f"slot {slot} should be 0 capacity, got {final_capacity}"

    second = _book(app_client, service, target_date, slot, test_customer, test_vehicle)
    assert second.status_code == 409, (
        f"second booking on full slot must 409, got {second.status_code}: {second.text}"
    )
    assert "no longer available" in second.json().get("detail", "").lower(), second.text


@live
def test_04a_unique_tech_assignment_leaves_overlapping_dept_alone(
    app_client, services_catalog, target_date, test_customer, test_vehicle, sm_http
):
    """When booking assigns a tech UNIQUE to one dept, the dept whose pool
    only shares OTHER techs is unaffected.

    Sales Consultation techs (in priority order): Nikki (1, unique), Zack
    (2, shared with Custom Exhaust), Josh (3, unique). Round-robin picks
    Nikki at priority 1. Nikki isn't in Custom Exhaust's pool, so Custom
    Exhaust availability is unchanged.
    """
    sales = _service_by_name(services_catalog, "Sales Consultation")
    custom_exh = _service_by_name(services_catalog, "Custom Exhaust Consultation")
    slot = "13:00"

    sales_before = _capacity_at(_availability(app_client, sales["id"], target_date), slot)
    custom_before = _capacity_at(_availability(app_client, custom_exh["id"], target_date), slot)
    assert sales_before >= 1, f"need >=1 Sales tech at {slot}; got {sales_before}"
    assert custom_before >= 1, f"need >=1 Custom Exhaust tech at {slot}; got {custom_before}"

    resp = _book(app_client, sales, target_date, slot, test_customer, test_vehicle)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = _record_for_cleanup(
        body["appointment_id"],
        body["confirmation_number"],
        sales["name"],
        f"{target_date} {slot}",
        sm_http,
    )

    sales_after = _capacity_at(_availability(app_client, sales["id"], target_date), slot)
    custom_after = _capacity_at(_availability(app_client, custom_exh["id"], target_date), slot)

    assert sales_after == sales_before - 1, f"Sales: expected {sales_before - 1}, got {sales_after}"
    # If Nikki (the unique-to-Sales priority-1 tech) was assigned, Custom
    # Exhaust should be unchanged. Print the assigned tech so failures are
    # diagnosable - and use a soft assertion: if a different tech was
    # assigned (e.g. priority changed in sheet) it's reasonable that Custom
    # Exhaust would drop by 1 instead, and the test should explain that.
    if entry["assigned_tech"] and custom_after != custom_before:
        pytest.fail(
            f"Custom Exhaust dropped from {custom_before} to {custom_after} "
            f"after Sales booking. Assigned tech was {entry['assigned_tech']}. "
            f"This is consistent with the per-tech math IF that tech is in "
            f"Custom Exhaust's qualified pool (i.e. Zack). Check the sheet."
        )
    assert custom_after == custom_before, (
        f"Custom Exhaust should be unaffected when Sales booking assigns a "
        f"Sales-unique tech, but went {custom_before} → {custom_after}. "
        f"Assigned tech: {entry['assigned_tech']}"
    )


@live
def test_04b_shared_tech_booking_reduces_overlapping_dept(
    app_client, services_catalog, target_date, test_customer, test_vehicle, sm_http
):
    """When a booking's ONLY qualified tech is shared with another dept,
    the other dept's availability drops by 1.

    Custom Exhaust has exactly one qualified tech (Zack). Zack is also
    qualified for Sales Consultation. So booking Custom Exhaust forces
    Zack as the assignment, and Sales availability drops by 1.

    Inverse of test_04a: catches the case where the per-tech math has to
    PROPAGATE a cross-dept constraint, not just isolate a non-cross one.
    """
    custom_exh = _service_by_name(services_catalog, "Custom Exhaust Consultation")
    sales = _service_by_name(services_catalog, "Sales Consultation")
    slot = "14:00"

    custom_before = _capacity_at(_availability(app_client, custom_exh["id"], target_date), slot)
    sales_before = _capacity_at(_availability(app_client, sales["id"], target_date), slot)
    assert custom_before >= 1, f"need >=1 Custom Exhaust tech at {slot}; got {custom_before}"
    assert sales_before >= 1, f"need >=1 Sales tech at {slot}; got {sales_before}"

    resp = _book(app_client, custom_exh, target_date, slot, test_customer, test_vehicle)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = _record_for_cleanup(
        body["appointment_id"],
        body["confirmation_number"],
        custom_exh["name"],
        f"{target_date} {slot}",
        sm_http,
    )

    custom_after = _capacity_at(_availability(app_client, custom_exh["id"], target_date), slot)
    sales_after = _capacity_at(_availability(app_client, sales["id"], target_date), slot)

    assert custom_after == custom_before - 1, (
        f"Custom Exhaust: expected {custom_before - 1}, got {custom_after}"
    )
    assert sales_after == sales_before - 1, (
        f"Sales should drop by 1 (Zack is the only Custom Exhaust tech and "
        f"is also in Sales' pool), but went {sales_before} → {sales_after}. "
        f"Assigned tech: {entry['assigned_tech']}. If this fails, the per-tech "
        f"chain (Appointment → Order → labor.technicianId) isn't propagating."
    )


# =====================================================================
# OFFLINE INTEGRATION TESTS
# =====================================================================
# Everything below runs in the default suite. The real FastAPI app, the
# real availability math and the real config plumbing are exercised; only
# the two external boundaries (Shopmonkey HTTP, Google Sheets) are faked
# in-process. The fakes keep state, so a booking made through /book is
# visible to the next /availability call - which is the whole point: the
# advertise → book → re-advertise seam is where double-bookings hide.


BUSINESS_TZ = ZoneInfo("America/Chicago")

# All offline tests use these dates. Wed/Thu/Fri/Mon of the same week so
# multi-day rollover (including over a closed weekend) is expressible.
WED = "2026-09-16"
THU = "2026-09-17"
FRI = "2026-09-18"
MON = "2026-09-21"
# A winter Wednesday: America/Chicago is on CST (-06:00) here, vs CDT
# (-05:00) in September. Used to pin DST-correct offsets.
WINTER_WED = "2027-01-20"

# Frozen "now" for the elapsed-slot guards: well before every date above.
FROZEN_NOW = datetime(2026, 9, 1, 8, 0, 0)

# Mirrors config.yaml's shape (business hours, buffer, order creation) so
# the config → duration → slot → booking chain is the real one.
OFFLINE_CONFIG: dict[str, Any] = {
    "timezone": "America/Chicago",
    "business_hours": {
        "monday": {"open": "09:00", "close": "17:30"},
        "tuesday": {"open": "09:00", "close": "17:30"},
        "wednesday": {"open": "09:00", "close": "17:30"},
        "thursday": {"open": "09:00", "close": "17:30"},
        "friday": {"open": "09:00", "close": "17:30"},
        "saturday": None,
        "sunday": None,
    },
    "default_slot_duration_minutes": 60,
    "service_buffers": {"Bedliner": 180},
    "online_booking": {
        "create_order": True,
        "workflow_status_name": "Scheduled",
        "order_status": "Estimate",
        "order_color": "blue",
    },
}

CUSTOMER = {"firstName": "Offline", "lastName": "Tester"}
VEHICLE = {"year": 2020, "make": "Test", "model": "Rig"}


# ------------------------------------------------------------- fake boundaries


def _to_utc_iso(local_naive: datetime) -> str:
    """Business-local wall clock → the UTC string Shopmonkey stores."""
    return (
        local_naive.replace(tzinfo=BUSINESS_TZ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )


def _to_local(iso_str: str) -> datetime:
    """Any offset-carrying ISO string → naive business-local wall clock."""
    return (
        datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        .astimezone(BUSINESS_TZ)
        .replace(tzinfo=None)
    )


def _make_service(service_id: str, name: str, department: str, hours: float) -> dict[str, Any]:
    return {
        "id": service_id,
        "name": name,
        "labels": [{"name": department}],
        "labors": [{"name": name, "hours": hours, "rateCents": 12000}],
        "parts": [],
        "fees": [],
        "subcontracts": [],
        "totalCents": int(hours * 12000),
    }


class FakeWorld:
    """The shop's world: services, techs, calendar, and what's been created.

    Shared by both fake clients so a write through one is visible to the
    other, exactly like the real Shopmonkey/Sheets pair.
    """

    def __init__(self) -> None:
        self.services: dict[str, dict[str, Any]] = {
            # 2 techs (alex, bri); 90 minutes.
            "svc_tint": _make_service("svc_tint", "Window Tint - Full Sedan", "Tint", 1.5),
            # 1 tech (cam) - disjoint from Tint, used to prove a booking in
            # one department doesn't eat another department's capacity.
            "svc_align": _make_service("svc_align", "Alignment - 4 Wheel", "Alignment", 1.0),
            # 1 tech (dee); the strictest double-book subject.
            "svc_exhaust": _make_service("svc_exhaust", "Exhaust Consult", "Exhaust", 1.0),
            # 1 tech (dee); 6h, rolls into the next business day from 13:00.
            "svc_ceramic": _make_service("svc_ceramic", "Ceramic Coating", "Vinyl", 6.0),
            # 3 techs but a MAX CONCURRENCY of 2 bays.
            "svc_bay": _make_service("svc_bay", "Bay Service", "Bay", 1.0),
            # 2h labor + 180 min config buffer = 300 min of calendar.
            "svc_bedliner": _make_service("svc_bedliner", "Bedliner - Short Bed", "Bedliner", 2.0),
        }
        self.techs: list[dict[str, Any]] = [
            {
                "tech_id": "tech_alex",
                "tech_name": "Alex",
                "departments": {"Tint": 1, "Bay": 1},
                "active": True,
            },
            {
                "tech_id": "tech_bri",
                "tech_name": "Bri",
                "departments": {"Tint": 2, "Bay": 2},
                "active": True,
            },
            {
                "tech_id": "tech_cam",
                "tech_name": "Cam",
                "departments": {"Alignment": 1, "Bay": 3},
                "active": True,
            },
            {
                "tech_id": "tech_dee",
                "tech_name": "Dee",
                "departments": {"Exhaust": 1, "Vinyl": 1, "Bedliner": 1},
                "active": True,
            },
        ]
        self.concurrency: dict[str, int] = {"Bay": 2}
        self.appointments: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.customers: list[dict[str, Any]] = []
        self.vehicles: list[dict[str, Any]] = []
        # method name -> (exception, succeed_this_many_calls_first)
        self.failures: dict[str, tuple[Exception, int]] = {}
        self.calls: dict[str, int] = {}
        self.deleted_appointment_ids: list[str] = []
        self._seq = 0

    # -- helpers used by tests ------------------------------------------
    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def block(
        self,
        tech_id: str | None,
        date_str: str,
        start_hhmm: str,
        end_hhmm: str,
        order_id: str | None = "ord_existing",
    ) -> dict[str, Any]:
        """Put a pre-existing calendar entry on the books.

        Stored in UTC like the real API returns it. `order_id=None` models
        a non-ticket block (time off), which still occupies the tech but
        holds no service bay.
        """
        appt = {
            "id": self.next_id("appt_seed"),
            "startDate": _to_utc_iso(
                datetime.strptime(f"{date_str} {start_hhmm}", "%Y-%m-%d %H:%M")
            ),
            "endDate": _to_utc_iso(datetime.strptime(f"{date_str} {end_hhmm}", "%Y-%m-%d %H:%M")),
            "orderId": order_id,
            "technicians": [{"id": tech_id}] if tech_id else [],
            "name": "Existing entry",
        }
        self.appointments.append(appt)
        return appt

    def clear_bookings(self) -> None:
        """Reset everything /book creates, keeping services/techs/config."""
        self.appointments.clear()
        self.orders.clear()
        self.customers.clear()
        self.vehicles.clear()
        self.calls.clear()
        self.deleted_appointment_ids.clear()

    def booked_spans(self) -> list[tuple[datetime, datetime]]:
        """Local wall-clock (start, end) of every appointment, in order."""
        return [(_to_local(a["startDate"]), _to_local(a["endDate"])) for a in self.appointments]


class FakeShopmonkeyClient:
    """In-process stand-in for ShopmonkeyClient.

    Normalizes created appointments to UTC exactly like the real API, so
    the UTC → business-timezone conversion in availability.py is genuinely
    exercised instead of being papered over by local-time storage.
    """

    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    def _enter(self, name: str) -> None:
        w = self.world
        w.calls[name] = w.calls.get(name, 0) + 1
        spec = w.failures.get(name)
        if spec is not None:
            exc, succeed_first = spec
            if w.calls[name] > succeed_first:
                raise exc

    async def close(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def get_bookable_canned_services(self) -> list[dict[str, Any]]:
        self._enter("get_bookable_canned_services")
        return [dict(s) for s in self.world.services.values()]

    async def get_canned_service(self, service_id: str) -> dict[str, Any] | None:
        self._enter("get_canned_service")
        svc = self.world.services.get(service_id)
        return dict(svc) if svc else None

    async def get_active_user_ids(self) -> set[str]:
        self._enter("get_active_user_ids")
        return {t["tech_id"] for t in self.world.techs if t["active"]}

    async def get_appointments_for_date(
        self, date_str: str, tech_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Mirror the real client's UTC-date window filter."""
        self._enter("get_appointments_for_date")
        out = []
        for appt in self.world.appointments:
            if appt["startDate"][:10] != date_str:
                continue
            copied = dict(appt)
            copied["technicians"] = [dict(t) for t in appt.get("technicians", [])]
            out.append(copied)
        return out

    async def get_busy_techs_for_appointments(
        self, appointments: list[dict[str, Any]]
    ) -> dict[str, set[str]]:
        """Union of appointment.technicians[] and the order → labor walk."""
        self._enter("get_busy_techs_for_appointments")
        busy: dict[str, set[str]] = {}
        for appt in appointments:
            appt_id = appt.get("id")
            if not appt_id:
                continue
            techs = {t["id"] for t in (appt.get("technicians") or []) if t.get("id")}
            order = self.world.orders.get(appt.get("orderId") or "")
            for svc in (order or {}).get("services", []):
                for labor in svc.get("labors") or []:
                    if labor.get("technicianId"):
                        techs.add(labor["technicianId"])
            busy[appt_id] = techs
        return busy

    async def find_or_create_customer(
        self,
        first_name: str,
        last_name: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        self._enter("find_or_create_customer")
        for c in self.world.customers:
            if c["firstName"] == first_name and c["lastName"] == last_name:
                return dict(c)
        customer = {
            "id": self.world.next_id("cust"),
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "phone": phone,
        }
        self.world.customers.append(customer)
        return dict(customer)

    async def find_or_create_vehicle(
        self,
        customer_id: str,
        year: int,
        make: str,
        model: str,
        vin: str | None = None,
    ) -> dict[str, Any]:
        self._enter("find_or_create_vehicle")
        for v in self.world.vehicles:
            if (v["customerId"], v["year"], v["make"], v["model"]) == (
                customer_id,
                year,
                make,
                model,
            ):
                return dict(v)
        vehicle = {
            "id": self.world.next_id("veh"),
            "customerId": customer_id,
            "year": year,
            "make": make,
            "model": model,
            "vin": vin,
        }
        self.world.vehicles.append(vehicle)
        return dict(vehicle)

    async def get_workflow_status_id(self, name: str) -> str | None:
        self._enter("get_workflow_status_id")
        return "ws_scheduled" if name == "Scheduled" else None

    async def create_order(
        self,
        customer_id: str,
        vehicle_id: str,
        workflow_status_id: str,
        status: str = "Estimate",
        color: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        self._enter("create_order")
        order = {
            "id": self.world.next_id("ord"),
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "workflowStatusId": workflow_status_id,
            "status": status,
            "color": color,
            "name": name,
            "services": [],
        }
        self.world.orders[order["id"]] = order
        return dict(order)

    async def attach_services_to_order(
        self, order_id: str, services: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self._enter("attach_services_to_order")
        self.world.orders.setdefault(order_id, {"id": order_id, "services": []})
        self.world.orders[order_id].setdefault("services", []).extend(services)
        return services

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
        self._enter("create_appointment")
        appt = {
            "id": self.world.next_id("appt"),
            # Shopmonkey persists UTC regardless of the offset we send.
            "startDate": _to_utc_iso(_to_local(start_date)),
            "endDate": _to_utc_iso(_to_local(end_date)),
            "orderId": order_id,
            "technicians": [{"id": technician_id}] if technician_id else [],
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "name": title or "",
            "note": notes,
            # The exact strings main.py sent, so tests can assert the
            # offset we hand Shopmonkey (DST correctness).
            "_sent_start": start_date,
            "_sent_end": end_date,
        }
        self.world.appointments.append(appt)
        return dict(appt)

    async def delete_appointment(self, appointment_id: str) -> bool:
        self._enter("delete_appointment")
        before = len(self.world.appointments)
        self.world.appointments = [a for a in self.world.appointments if a["id"] != appointment_id]
        self.world.deleted_appointment_ids.append(appointment_id)
        return len(self.world.appointments) < before


class FakeSheetsClient:
    """In-process stand-in for SheetsClient (Tech/Dept tab)."""

    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    def clear_cache(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    def get_cache_status(self) -> dict[str, Any]:
        return {"cache_size": 0, "cache_ttl_seconds": 300, "cache_maxsize": 100}

    async def get_techs_for_department(
        self, department: str, active_tech_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        qualified = []
        for tech in self.world.techs:
            priority = tech["departments"].get(department, 0)
            if priority <= 0:
                continue
            if active_tech_ids is not None and tech["tech_id"] not in active_tech_ids:
                continue
            qualified.append(
                {
                    "tech_id": tech["tech_id"],
                    "tech_name": tech["tech_name"],
                    "priority": priority,
                }
            )
        qualified.sort(key=lambda t: t["priority"])
        return qualified

    async def get_max_concurrency_for_department(self, department: str) -> int | None:
        return self.world.concurrency.get(department)


class _DisabledEmailClient:
    """Email is off in tests; booking must not depend on it."""

    enabled = False


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def world() -> FakeWorld:
    return FakeWorld()


@pytest.fixture
def make_client(world):
    """Factory building a TestClient wired to the fake world.

    `now` drives the elapsed-slot guards in both /availability and /book,
    so a test can move the clock without touching datetime globally.
    """
    stack = ExitStack()

    def _make(now: datetime = FROZEN_NOW) -> TestClient:
        for patcher in (
            patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False),
            patch("main.API_KEY", None),
            patch("main.ShopmonkeyClient", return_value=FakeShopmonkeyClient(world)),
            patch("main.SheetsClient", return_value=FakeSheetsClient(world)),
            patch("main.load_config", return_value=deepcopy(OFFLINE_CONFIG)),
            patch("main.validate_config"),
            patch("main._now_local", return_value=now),
            patch("main.get_email_client", return_value=_DisabledEmailClient()),
        ):
            stack.enter_context(patcher)

        from main import app

        return stack.enter_context(TestClient(app))

    yield _make
    stack.close()


@pytest.fixture
def client(make_client) -> TestClient:
    """The common case: a client with the clock frozen far before the dates."""
    return make_client()


# ------------------------------------------------------------------ helpers


def _slot_map(client: TestClient, service_id: str, date_str: str) -> dict[str, int]:
    """{'09:00': available_techs, ...} for the advertised slots."""
    resp = client.get(f"/availability?service_id={service_id}&date={date_str}")
    assert resp.status_code == 200, resp.text
    return {s["start"]: s["available_techs"] for s in resp.json()["slots"]}


def _availability_body(client: TestClient, service_id: str, date_str: str) -> dict[str, Any]:
    resp = client.get(f"/availability?service_id={service_id}&date={date_str}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_book(
    client: TestClient,
    service_id: str,
    date_str: str,
    start_hhmm: str,
    end_hhmm: str = "23:59",
    end_date_str: str | None = None,
):
    return client.post(
        "/book",
        json={
            "service_id": service_id,
            "slot_start": f"{date_str}T{start_hhmm}:00",
            "slot_end": f"{end_date_str or date_str}T{end_hhmm}:00",
            "customer": CUSTOMER,
            "vehicle": VEHICLE,
        },
    )


# -------------------------------------------------------------------- tests
# ---- full round trip: advertise → book → re-advertise → refuse


def test_offline_booking_consumes_the_slot_it_was_advertised_for(client, world):
    """The slot /availability advertises is bookable, and each booking
    removes exactly one unit of capacity until the slot disappears.

    This is the double-book guard end to end: after both Tint techs are
    taken at 09:00 the slot must be gone from /availability AND the next
    /book must be refused without writing anything to Shopmonkey.
    """
    assert _slot_map(client, "svc_tint", WED)["09:00"] == 2

    first = _post_book(client, "svc_tint", WED, "09:00", "10:30")
    assert first.status_code == 200, first.text
    assert CONFIRMATION_RE.match(first.json()["confirmation_number"])
    assert _slot_map(client, "svc_tint", WED)["09:00"] == 1

    second = _post_book(client, "svc_tint", WED, "09:00", "10:30")
    assert second.status_code == 200, second.text
    assert "09:00" not in _slot_map(client, "svc_tint", WED)

    created_before = world.calls["create_appointment"]
    third = _post_book(client, "svc_tint", WED, "09:00", "10:30")
    assert third.status_code == 409, third.text
    assert "no longer available" in third.json()["detail"].lower()
    # The contract that matters: a refused booking writes nothing.
    assert world.calls["create_appointment"] == created_before
    assert len(world.appointments) == 2


def test_offline_second_booking_of_single_tech_slot_writes_nothing(client, world):
    """A one-tech department fills after a single booking.

    Exhaust has exactly one qualified tech, so the second attempt must
    409 before creating a customer, an order, or an appointment. If
    conflict detection regresses to a no-op this test fails loudly.
    """
    assert _slot_map(client, "svc_exhaust", WED)["09:00"] == 1

    assert _post_book(client, "svc_exhaust", WED, "09:00", "10:00").status_code == 200
    assert "09:00" not in _slot_map(client, "svc_exhaust", WED)

    second = _post_book(client, "svc_exhaust", WED, "09:00", "10:00")
    assert second.status_code == 409, second.text
    assert len(world.appointments) == 1
    assert len(world.orders) == 1


def test_offline_booking_leaves_a_disjoint_department_untouched(client):
    """Booking Tint must not shrink Alignment's capacity.

    Tint = {Alex, Bri}, Alignment = {Cam}: the assigned tech is not in
    Alignment's pool. If the per-tech attribution is lost (the booking
    landing in the "unattributed" bucket) Alignment would drop to 0 and
    the slot would vanish - a shop-wide capacity hit for an unrelated
    department.
    """
    assert _slot_map(client, "svc_align", WED)["09:00"] == 1

    assert _post_book(client, "svc_tint", WED, "09:00", "10:30").status_code == 200

    assert _slot_map(client, "svc_align", WED)["09:00"] == 1


def test_offline_every_advertised_slot_can_actually_be_booked(client, world):
    """Anything /availability offers, /book must accept.

    Includes the late starts whose service rolls past close - those are
    advertised with end=close but require the server to reserve a
    continuation day. A slot that is advertised but unbookable is a dead
    end for the customer.
    """
    starts = list(_slot_map(client, "svc_tint", WED))
    assert starts, "expected the fixture day to advertise slots"

    for start in starts:
        world.clear_bookings()
        resp = _post_book(client, "svc_tint", WED, start, "23:59")
        assert resp.status_code == 200, f"advertised {start} was rejected: {resp.text}"


def test_offline_closed_day_is_neither_advertised_nor_bookable(client, world):
    """Saturday is closed in config: no slots, and /book refuses it.

    The widget can be pointed at any date, so the two endpoints have to
    agree that a closed day has no capacity - otherwise a hand-crafted
    request lands an appointment on a day nobody is at the shop.
    """
    saturday = "2026-09-19"
    assert _slot_map(client, "svc_tint", saturday) == {}

    resp = _post_book(client, "svc_tint", saturday, "10:00", "11:30")
    assert resp.status_code == 409, resp.text
    assert world.appointments == []


# ---- server-derived duration wins over the client's slot_end


def test_offline_client_slot_end_cannot_shrink_the_reservation(client, world):
    """A short client-submitted slot_end must not shrink the booking.

    The widget sends whatever it last saw; the server owns the duration.
    Booking the 90-minute Tint service at 09:00 with a bogus 15-minute
    slot_end must still reserve 09:00-10:30, which is observable because
    the 10:00 slot loses that tech.
    """
    assert _slot_map(client, "svc_tint", WED)["10:00"] == 2

    resp = _post_book(client, "svc_tint", WED, "09:00", "09:15")
    assert resp.status_code == 200, resp.text

    (start, end) = world.booked_spans()[0]
    assert (end - start).total_seconds() / 60 == 90
    assert _slot_map(client, "svc_tint", WED)["10:00"] == 1


def test_offline_config_buffer_extends_both_advertised_and_booked_span(client, world):
    """The config buffer (cure time) must reach both endpoints.

    Bedliner is 2h of labor + a 180-minute buffer from config. If either
    /availability or /book forgets the buffer they disagree: the customer
    is shown a 2-hour slot and the calendar reserves 5 hours, or worse the
    reverse, and the bay is double-booked during cure time.
    """
    body = _availability_body(client, "svc_bedliner", WED)
    assert body["duration_minutes"] == 300
    slots = {s["start"]: s["end"] for s in body["slots"]}
    assert slots["09:00"] == "14:00"

    assert _post_book(client, "svc_bedliner", WED, "09:00", "11:00").status_code == 200
    (start, end) = world.booked_spans()[0]
    assert (end - start).total_seconds() / 60 == 300


# ---- multi-day services


def test_offline_multiday_slot_advertises_close_but_reserves_both_days(client, world):
    """A 6h service starting at 13:00 spans two business days.

    /availability advertises it ending at close (day 1 only). /book must
    derive the real span and create one appointment per day, both linked
    to the same order - the June 3 bug was a single 30-minute stub on day
    one while the rollover day was never reserved.
    """
    body = _availability_body(client, "svc_ceramic", WED)
    slots = {s["start"]: s["end"] for s in body["slots"]}
    assert slots["13:00"] == "17:30"

    resp = _post_book(client, "svc_ceramic", WED, "13:00", "17:30")
    assert resp.status_code == 200, resp.text

    spans = world.booked_spans()
    assert len(spans) == 2
    assert spans[0] == (
        datetime.strptime(f"{WED} 13:00", "%Y-%m-%d %H:%M"),
        datetime.strptime(f"{WED} 17:30", "%Y-%m-%d %H:%M"),
    )
    # Remaining 90 minutes start at the next business day's opening time.
    assert spans[1] == (
        datetime.strptime(f"{THU} 09:00", "%Y-%m-%d %H:%M"),
        datetime.strptime(f"{THU} 10:30", "%Y-%m-%d %H:%M"),
    )
    order_ids = {a["orderId"] for a in world.appointments}
    assert len(order_ids) == 1 and None not in order_ids


def test_offline_multiday_reservation_blocks_the_continuation_day(client, world):
    """The continuation day must really be taken, not just written down.

    A Wednesday 13:00 Vinyl booking rolls into Thursday morning, so
    Thursday's 09:00 must no longer be offered afterwards.

    This also pins tech SELECTION across days, which needs two qualified
    techs to be observable: Cam is given Vinyl at priority 2 (behind Dee)
    and Dee is booked solid on Thursday. Dee is therefore free on
    Wednesday but unusable for a job that spills into Thursday, so the
    only legal assignment is Cam - on BOTH segments. Capacity at
    Wednesday 13:00 is correspondingly 1, not 2: the bottleneck day
    governs, and the free-tech pool is the INTERSECTION across days, not
    the union.
    """
    cam = next(t for t in world.techs if t["tech_id"] == "tech_cam")
    cam["departments"]["Vinyl"] = 2
    world.block("tech_dee", THU, "09:00", "17:30")

    assert _slot_map(client, "svc_ceramic", THU).get("09:00") == 1

    # Dee is free all Wednesday but cannot finish on Thursday, so only Cam
    # can take the multi-day slot.
    assert _slot_map(client, "svc_ceramic", WED)["13:00"] == 1

    assert _post_book(client, "svc_ceramic", WED, "13:00", "17:30").status_code == 200

    # "_sent_start" only exists on entries /book created, so the seeded
    # Thursday block for Dee is excluded.
    booked = [a for a in world.appointments if "_sent_start" in a]
    assert len(booked) == 2
    assert [a["technicians"] for a in booked] == [[{"id": "tech_cam"}], [{"id": "tech_cam"}]], (
        "a tech booked solid on the continuation day must never be assigned to a multi-day job"
    )

    assert "09:00" not in _slot_map(client, "svc_ceramic", THU)


def test_offline_multiday_slot_not_offered_when_continuation_day_is_full(client, world):
    """A late start is unbookable when the rollover day has no capacity.

    Dee is booked solid on Thursday, so Wednesday 13:00 (which needs
    Thursday morning) must be neither advertised nor accepted. Checking
    only day one would hand the customer a slot the shop cannot honor.
    """
    world.block("tech_dee", THU, "09:00", "17:30")

    wed_slots = _slot_map(client, "svc_ceramic", WED)
    assert "09:00" in wed_slots, "an all-day-Thursday block must not affect Wednesday mornings"
    assert "13:00" not in wed_slots

    resp = _post_book(client, "svc_ceramic", WED, "13:00", "17:30")
    assert resp.status_code == 409, resp.text
    assert world.calls.get("create_appointment", 0) == 0


def test_offline_multiday_rolls_over_the_closed_weekend(client, world):
    """Friday's rollover lands on Monday, not Saturday.

    Saturday and Sunday are closed in config, so the continuation
    appointment must be created on the next OPEN day.
    """
    resp = _post_book(client, "svc_ceramic", FRI, "13:00", "17:30")
    assert resp.status_code == 200, resp.text

    spans = world.booked_spans()
    assert len(spans) == 2
    assert spans[0][0].strftime("%Y-%m-%d") == FRI
    assert spans[1][0] == datetime.strptime(f"{MON} 09:00", "%Y-%m-%d %H:%M")


# ---- department concurrency across the day


def test_offline_department_concurrency_caps_capacity_below_free_techs(client, world):
    """A 2-bay department never offers 3 slots even with 3 free techs.

    The Bay department has Alex, Bri and Cam qualified but MAX
    CONCURRENCY 2. Availability must advertise 2, each booking must
    decrement it, and the third booking must be refused even though Cam
    is idle - the bays, not the techs, are the constraint.
    """
    assert _slot_map(client, "svc_bay", WED)["09:00"] == 2

    assert _post_book(client, "svc_bay", WED, "09:00", "10:00").status_code == 200
    assert _slot_map(client, "svc_bay", WED)["09:00"] == 1

    assert _post_book(client, "svc_bay", WED, "09:00", "10:00").status_code == 200
    assert "09:00" not in _slot_map(client, "svc_bay", WED)

    third = _post_book(client, "svc_bay", WED, "09:00", "10:00")
    assert third.status_code == 409, third.text
    assert len(world.appointments) == 2

    # Cam is still free, so a later hour is unaffected by the cap.
    assert _slot_map(client, "svc_bay", WED)["11:00"] == 2


def test_offline_concurrency_cap_does_not_leak_into_other_departments(client, world):
    """The cap belongs to the department, not the shop.

    Two Bay bookings consume Alex and Bri, so Tint (which shares them)
    correctly drops to 0 at that hour - but Alignment, staffed by Cam,
    must still be offered.
    """
    assert _post_book(client, "svc_bay", WED, "09:00", "10:00").status_code == 200
    assert _post_book(client, "svc_bay", WED, "09:00", "10:00").status_code == 200

    assert "09:00" not in _slot_map(client, "svc_tint", WED)
    assert _slot_map(client, "svc_align", WED)["09:00"] == 1


def test_offline_time_off_block_frees_the_bay_but_not_the_tech(client, world):
    """A no-ticket calendar block occupies the tech without using a bay.

    Alex is on PTO (no orderId) and Bri is on a real ticket. Bay's
    ceiling is 2 and only one bay is occupied, so the remaining capacity
    is Cam alone: 1, not 0 (which is what counting PTO against the bays
    would produce) and not 2 (which is what ignoring the PTO block
    entirely would produce).
    """
    world.block("tech_alex", WED, "09:00", "10:00", order_id=None)
    world.block("tech_bri", WED, "09:00", "10:00")

    assert _slot_map(client, "svc_bay", WED)["09:00"] == 1


# ---- timezone correctness end to end


def test_offline_existing_utc_entry_consumes_the_matching_local_slot(client, world):
    """Shopmonkey speaks UTC; the shop books in America/Chicago.

    An existing 09:00 CDT entry comes back from the API as 14:00Z. It
    must consume the 09:00 slot and leave 14:00 untouched - dropping the
    UTC → local conversion inverts exactly this pair.
    """
    baseline = _slot_map(client, "svc_tint", WED)
    assert baseline["09:00"] == 2 and baseline["14:00"] == 2

    world.block("tech_alex", WED, "09:00", "10:30")

    slots = _slot_map(client, "svc_tint", WED)
    assert slots["09:00"] == 1, "09:00 CDT (14:00Z) must be the blocked slot"
    assert slots["14:00"] == 2, "14:00 local must be untouched by a 14:00Z entry"


def test_offline_booking_sends_local_wallclock_with_dst_correct_offset(client, world):
    """The offset we hand Shopmonkey must follow the date, not a constant.

    September is CDT (-05:00) and January is CST (-06:00). Both bookings
    are for 09:00 on the shop wall clock; a hard-coded offset would move
    one of them by an hour.
    """
    assert _post_book(client, "svc_tint", WED, "09:00", "10:30").status_code == 200
    assert _post_book(client, "svc_tint", WINTER_WED, "09:00", "10:30").status_code == 200

    summer, winter = world.appointments[0], world.appointments[1]
    assert summer["_sent_start"].startswith(f"{WED}T09:00:00")
    assert summer["_sent_start"].endswith("-05:00")
    assert winter["_sent_start"].startswith(f"{WINTER_WED}T09:00:00")
    assert winter["_sent_start"].endswith("-06:00")

    # And the instants those map to, as Shopmonkey stores them.
    assert summer["startDate"] == f"{WED}T14:00:00.000Z"
    assert winter["startDate"] == f"{WINTER_WED}T15:00:00.000Z"


def test_offline_booked_slot_round_trips_without_offset_drift(client, world):
    """Book 09:00, re-read availability: 09:00 is gone, 15:00 is not.

    Any offset error in the write path or the read path would shift the
    hole in the calendar by the UTC offset instead of leaving it where
    the customer booked.

    10:00 also pins the half-open overlap boundary: the booking ends at
    exactly 10:00, so the 10:00 slot must stay fully available. A closed
    (>=/<=) comparison would eat an extra hour of every booking.
    """
    assert _post_book(client, "svc_exhaust", WED, "09:00", "10:00").status_code == 200

    slots = _slot_map(client, "svc_exhaust", WED)
    assert "09:00" not in slots
    assert slots["10:00"] == 1
    assert slots["15:00"] == 1


# ---- elapsed slots: /availability and /book must agree


def test_offline_availability_and_book_agree_on_elapsed_slots(make_client):
    """At noon, morning slots are neither offered nor accepted.

    Both endpoints derive "now" from the same business-timezone clock; if
    only one of them applied the guard a stale widget could still book
    9:00 AM in the afternoon.
    """
    noon = datetime.strptime(f"{WED} 12:00", "%Y-%m-%d %H:%M")
    client = make_client(now=noon)

    slots = _slot_map(client, "svc_tint", WED)
    assert "09:00" not in slots
    # Exact, not `>=`: an over-eager guard that also hid 13:00/14:00/15:00
    # would silently delete a bookable afternoon from every customer's view.
    assert min(slots) == "13:00"

    past = _post_book(client, "svc_tint", WED, "09:00", "10:30")
    assert past.status_code == 409
    assert "passed" in past.json()["detail"].lower()

    future = _post_book(client, "svc_tint", WED, min(slots), "23:59")
    assert future.status_code == 200, future.text


# ---- partial failures during booking


def test_offline_vehicle_failure_leaves_the_slot_bookable(client, world):
    """A vehicle-creation failure must not half-book the slot.

    What this test guards is the calendar: no order and no appointment may
    exist, and a retry must succeed.

    The customer count is pinned only to document today's behaviour, NOT
    to require it. The customer record is created before the vehicle call,
    so a failure here leaves an orphan behind - a known, harmless wart
    (find_or_create reuses the record on retry, and it has no calendar
    impact). If someone later adds compensating cleanup, update this
    number rather than treating it as a broken contract.
    """
    world.failures["find_or_create_vehicle"] = (
        ShopmonkeyAPIError("vehicle service down", status_code=500),
        0,
    )

    resp = _post_book(client, "svc_exhaust", WED, "09:00", "10:00")
    assert resp.status_code == 502, resp.text
    assert world.appointments == []
    assert world.orders == {}
    # Documented wart, not a requirement (see docstring): the customer
    # created before the failing vehicle call is left behind.
    assert len(world.customers) == 1

    world.failures.clear()
    assert _slot_map(client, "svc_exhaust", WED)["09:00"] == 1
    assert _post_book(client, "svc_exhaust", WED, "09:00", "10:00").status_code == 200


def test_offline_vehicle_without_id_aborts_before_writing_the_calendar(client, world):
    """A vehicle payload with no id is a failed booking, not a booked one."""

    async def _no_id(*args, **kwargs):
        return {}

    with patch.object(FakeShopmonkeyClient, "find_or_create_vehicle", new=_no_id):
        resp = _post_book(client, "svc_exhaust", WED, "09:00", "10:00")

    assert resp.status_code == 500, resp.text
    assert world.appointments == []
    assert world.orders == {}


def test_offline_failed_second_day_rolls_back_the_first_day(client, world):
    """A multi-day booking is all-or-nothing.

    Day one succeeds, day two fails: leaving day one behind would
    silently under-reserve the calendar (the vehicle stays overnight with
    nothing on the books) while telling the customer nothing. The first
    appointment must be deleted and the slot must be bookable again.
    """
    world.failures["create_appointment"] = (
        ShopmonkeyAPIError("appointment service down", status_code=500),
        1,  # first create succeeds, second one blows up
    )

    resp = _post_book(client, "svc_ceramic", WED, "13:00", "17:30")
    assert resp.status_code == 502, resp.text
    assert world.calls["create_appointment"] == 2
    assert len(world.deleted_appointment_ids) == 1
    assert world.appointments == []

    world.failures.clear()
    assert _slot_map(client, "svc_ceramic", WED)["13:00"] == 1
    assert _post_book(client, "svc_ceramic", WED, "13:00", "17:30").status_code == 200


def test_offline_order_failure_falls_back_to_appointment_only(client, world):
    """Losing the repair order must not lose the customer's appointment.

    Order creation is a Shopmonkey-parity nicety; if it fails the booking
    still has to land on the calendar, unlinked.

    Unlinked is exactly what makes this path dangerous: with no order there
    is no Appointment → Order → labor walk, so the appointment's own
    `technicians[]` stamp is the ONLY remaining tech attribution. Drop it
    and the booking becomes invisible to /availability - the slot is
    re-offered and the tech is double-booked.
    """
    world.failures["create_order"] = (ShopmonkeyAPIError("order down", status_code=500), 0)

    resp = _post_book(client, "svc_exhaust", WED, "09:00", "10:00")
    assert resp.status_code == 200, resp.text
    assert len(world.appointments) == 1
    assert world.appointments[0]["orderId"] is None
    # Dee is Exhaust's only tech, so the stamp is unambiguous.
    assert world.appointments[0]["technicians"] == [{"id": "tech_dee"}]

    # The load-bearing consequence: the slot must not come back.
    assert "09:00" not in _slot_map(client, "svc_exhaust", WED)


def test_offline_attached_service_stamps_the_assigned_tech_on_every_labor(client, world):
    """The order line item must carry the assigned technicianId.

    That stamp is what lets the NEXT availability check see this booking
    as taking a specific tech (via Appointment → Order → labor) instead
    of a shop-wide unattributed capacity hit.
    """
    assert _post_book(client, "svc_tint", WED, "09:00", "10:30").status_code == 200

    order = next(iter(world.orders.values()))
    labors = [labor for svc in order["services"] for labor in svc["labors"]]
    assert labors, "expected the canned service's labor lines to be attached"
    assigned = {labor.get("technicianId") for labor in labors}
    assert assigned == {"tech_alex"}, assigned
