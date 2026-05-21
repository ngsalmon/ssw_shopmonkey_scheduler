# Integration test plan: live availability ↔ booking interaction

Goal: prove against the **real Shopmonkey + Sheets APIs** that the conflict
detection we shipped on 2026-05-20 holds end-to-end. Specifically:

1. **Single-dept**: booking a service decrements its `available_techs` count
   for the same slot.
2. **Cross-dept isolation**: booking a service whose qualified-tech set is
   disjoint from another service's set does NOT decrement the other
   service's availability.

These run against production data, so they're gated behind the
`integration` + `booking` pytest markers (already deselected by default in
`pytest.ini`). Opt in via `pytest -m integration` when you want to run them.

---

## Stack

- **Real `ShopmonkeyClient` + `SheetsClient`** built from `.env`
- **FastAPI `TestClient`** wrapping the actual app so we exercise `/availability`
  and `/book` exactly as the widget does
- Sequential test execution (the in-process `booking_lock` already serializes
  requests within a process, but `pytest -m integration` runs single-threaded
  anyway so we don't need extra coordination)

## Skip rules

- Skip the file entirely if `SHOPMONKEY_API_TOKEN` / `GOOGLE_SHEETS_ID` /
  `GOOGLE_APPLICATION_CREDENTIALS` aren't set
- Skip individual tests when the required service / tech configuration
  can't be found in the live data (with an informative reason)

## Date selection

Pick a target date 21 days out, advancing to the next weekday if the
default lands on a closed day. Two reasons:

1. Far enough out that a real customer won't try to book the same slot
   while the test runs (and Anne can see the test booking in time to
   delete if cleanup fails).
2. Close enough that it falls inside whatever range the widget shows so
   we exercise the same code path as production.

If `get_business_hours()` reports the chosen date as closed, advance one
day at a time up to 7 attempts, then skip.

## Test customer / vehicle

- **Stable identity**: `firstName="Claude"`, `lastName="TestUser"`,
  `email="claude-test@salmonspeedworx-do-not-contact.invalid"`,
  `phone="+15555550101"` (the `555` reserved range — never reaches anyone).
- Vehicle: `2020 Test TestVehicle` (no VIN).
- Stable identity means `find_or_create_customer` returns the same record
  across runs → no orphan customers piling up. If we ever want a clean
  slate, the test customer is easy to find and delete by hand.

## Booking labeling

Every appointment we create gets `*** CLAUDE INTEGRATION TEST - DELETE ME ***`
in its note so a human can spot leftovers if cleanup fails.

## Cleanup

Per-test fixture `cleanup_tracker` collects `appointment_id`s. Teardown
calls `shopmonkey_client.delete_appointment(id)` for each — best-effort
(log and continue on failure).

**Deliberately NOT cleaning up:**

- The order created alongside the appointment (no DELETE on /v3/order in
  our current client; staff workflow lets them archive it).
- The test customer/vehicle (stable identity, reused across runs).

**Session-level safety net:** a `session_cleanup` autouse fixture runs at
the START of every integration test session and deletes any appointment
whose note contains `CLAUDE INTEGRATION TEST` on dates in the next 60 days.
Catches orphans from previous failed runs.

## Capacity assertions are deltas, not absolutes

We can't assume an empty calendar on the test date — Anne's shop has real
work scheduled. So every assertion is a delta:

```
baseline_capacity = get_available_techs_for_slot(...)
book_one()
new_capacity = get_available_techs_for_slot(...)
assert new_capacity == baseline_capacity - 1
```

If `baseline_capacity == 0` for a test's chosen slot, the test should
fail loudly with "slot already full on date X, choose a quieter slot or
date" rather than silently skipping.

## Slot selection

For each test, pick a slot in the morning (9:00 AM local) — least likely
to be already booked solid by the shop. If 9:00 has zero capacity, try
10:00, 11:00, then advance the date.

---

## Test 1 — `test_booking_decrements_available_techs_for_same_slot`

**Setup:**
- Pick any bookable service (first one from `/services` is fine).
- Pick target date + 9:00 slot. Confirm baseline capacity ≥ 1.

**Steps:**
1. `GET /availability?service_id=S&date=D` → record baseline N for the slot.
2. `POST /book` with the test customer/vehicle and that slot. Expect 200.
3. `GET /availability` again → assert slot capacity is `N - 1`.
4. If `N >= 2`, book a second time as a different test booking → assert `N - 2`.
   (Both bookings go to the same test customer, but Shopmonkey doesn't
   reject duplicate appointments for the same customer/vehicle.)

**Asserts:**
- /book status 200
- confirmation number matches `SM-YYYYMMDD-XXXXXX` regex
- post-book capacity == baseline - 1
- (optional) post-second-book capacity == baseline - 2

**Cleanup:** delete both appointments.

---

## Test 2 — `test_booking_one_service_does_not_affect_disjoint_dept`

**Setup:**
1. Fetch all bookable services. For each, look up its qualified techs via
   `sheets_client.get_techs_for_department(...)`.
2. Find a pair `(S1, S2)` such that `qualified(S1) ∩ qualified(S2) == ∅`.
3. If no such pair exists in the live data, skip the test with reason.

**Pick target date + 9:00 slot for both services.** Confirm baseline
capacity ≥ 1 for both.

**Steps:**
1. Record baseline capacity for S1 and for S2.
2. `POST /book` for S1 at the 9:00 slot. Expect 200.
3. Read back the created appointment's labor.technicianId to confirm
   which tech got assigned (for diagnostic detail in failure messages).
4. `GET /availability` for S1 → assert capacity dropped by 1.
5. `GET /availability` for S2 → assert capacity unchanged.

**Cleanup:** delete the S1 appointment.

**Edge cases this test catches:**
- The bug we just fixed: if conflict detection were still shop-wide
  (`overlap_count` only), S2's capacity would also drop by 1. The per-tech
  fix means only S1's techs are affected.

---

## Test 3 (recommended) — `test_full_slot_returns_409_on_extra_booking`

This is the strongest test of the original Anne bug. If conflict detection
ever regresses to a no-op, this test fails loudly.

**Setup:**
- Pick a service S with a small qualified-tech count N (ideally 1 or 2 so
  we don't have to make many bookings). If S has 1 tech qualified and 9:00
  shows N=1, one booking should fully consume the slot.

**Steps:**
1. Record baseline capacity N for the slot.
2. Loop N times: book the same slot for the test customer. Each one
   should succeed.
3. The (N+1)th `POST /book` for the same slot must return **409**.
4. `GET /availability` → the slot must no longer appear, OR have
   `available_techs == 0`.

**Asserts:**
- All N bookings return 200
- Booking N+1 returns 409 with "no longer available"
- Slot removed (or capacity 0) on the final /availability call

**Cleanup:** delete all N appointments.

**Edge case:** if N is large (say 5+), the test makes many real bookings.
Cap N at 3 — find a service with ≤ 3 qualified techs to keep test cheap.
If no such service exists, skip with reason.

---

## Test 4 (recommended) — `test_booking_one_service_reduces_overlapping_dept_when_shared_tech_assigned`

Complement to Test 2: when two services SHARE a qualified tech, booking
one MAY reduce the other's capacity, depending on which tech round-robin
picks.

**Setup:**
- Find a service pair `(S1, S2)` where `qualified(S1) ∩ qualified(S2)` is
  non-empty AND there's a tech `t_uniq` qualified only for S1 (so the
  outcome can vary by assignment).

**Steps:**
1. Record baseline for S1 and S2.
2. Book S1.
3. Inspect the assigned tech (from labor.technicianId).
4. Re-fetch availability for both services.
5. If the assigned tech was in S2's qualified pool:
   - assert S2 capacity dropped by 1
6. If the assigned tech was unique to S1:
   - assert S2 capacity unchanged

**Why this matters:** verifies the per-tech math holds in the harder case
where pools intersect, not just the trivially-disjoint case.

**Cleanup:** delete the appointment.

**Note:** less critical than Tests 1-3. Useful but skip if implementation
time is tight.

---

## Test 5 (optional) — `test_multi_day_service_reserves_capacity_across_days`

A service over 5 hours triggers multi-day logic. Booking one should
decrement availability on each spanned day.

Skip recommended for the first cut — multi-day is exercised by Anne's
real Window Tint XL bookings and by `tests/test_availability.py`. Adding
the live version is nice-to-have but not necessary to prove the fix.

---

## Test ordering

pytest sorts file-internally by definition order. Run order:

1. Test 3 (full-slot 409) — strongest signal if anything breaks
2. Test 1 (decrement) — basic positive case
3. Test 2 (cross-dept isolation) — the test the user specifically asked for
4. Test 4 (shared-tech case) — if included

Each test cleans up its own appointments so order shouldn't matter, but
running 3 first means a regression is caught quickly.

---

## Open questions for review

1. **Include Tests 3 and 4?** The user asked for 1 and 2. Tests 3 and 4
   add high-value coverage of the per-tech math. Recommend yes.
2. **Cleanup of the created Order**: leave it for staff (current plan)
   or extend `ShopmonkeyClient` with `delete_order()` if Shopmonkey
   supports it?
3. **Date offset**: 21 days out — too far? Too close? Configurable via
   env var?
4. **Run frequency**: ad-hoc only, or wire into CI nightly? Nightly would
   catch Shopmonkey API regressions early; daily test bookings are very
   visible to the shop.
5. **`mock_sheets` shortcut?** The Sheets API has its own quirks. If
   Sheets is flaky, do we want a lighter "Shopmonkey-only" variant of
   the test that swaps in a mock SheetsClient with hand-picked tech maps?

---

## Files I'll create when this is approved

- `tests/test_integration_availability.py` — the tests themselves
- Possibly `tests/integration_helpers.py` — shared `find_disjoint_service_pair()`,
  `pick_workable_slot()`, etc. (only if multiple tests share substantial code)
- Adds: `delete_order()` to `ShopmonkeyClient` IF we decide to clean up orders
