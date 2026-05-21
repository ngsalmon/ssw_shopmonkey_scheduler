"""Live integration tests for availability ↔ booking interaction.

Opt-in only. Gated behind the `integration` and `booking` pytest markers
(both deselected by default per pytest.ini). Run explicitly with:

    pytest -s -m "integration and booking" tests/test_integration_availability.py

These tests hit the REAL Shopmonkey + Google Sheets APIs and create real
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env before the module-level skip check below (main.py would load
# it later via lifespan, but the skip runs at import time).
load_dotenv(Path(__file__).parent.parent / ".env")

pytestmark = [pytest.mark.integration, pytest.mark.booking]


# Skip the entire file if required env not set
_required_env = ("SHOPMONKEY_API_TOKEN", "GOOGLE_SHEETS_ID")
if not all(os.getenv(v) for v in _required_env):
    pytest.skip(
        f"Integration tests require {', '.join(_required_env)}",
        allow_module_level=True,
    )


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
