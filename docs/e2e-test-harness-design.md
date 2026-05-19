# E2E Test Harness Design

**Status:** Approved (2026-05-19) — decisions locked, ready to implement
**Author:** Claude (with Nathan)
**Date:** 2026-05-19

## Goals

1. Catch widget-level regressions like the "Full Vehicle" / XL SUV/Van bug before they reach Anne.
2. Cover every user-facing capability of the scheduler end-to-end (UI → FastAPI → mocked external services).
3. Run hermetically on a dev laptop in seconds, with no real Shopmonkey or Google Sheets credentials.
4. Be CI-friendly later (single `npm test` style command, deterministic, no flakes from real APIs).
5. Stay close to production: the real FastAPI app, the real widget JS, the real availability logic. Only the *external* boundaries (Shopmonkey HTTP, Sheets API) are mocked.

## Non-Goals

- Replace the existing pytest suite. Pytest still owns unit/integration coverage of Python modules. E2E is for the whole-system flow.
- Test against the real Shopmonkey staging API. That belongs in a separate manual or smoke-test track.
- Visual regression / pixel-diff testing. Out of scope for v1; can be layered on later.

## Approach

### Mock at the backend boundary, not the network

The widget's `fetch` calls hit our own FastAPI server. We mock one level lower: replace `ShopmonkeyClient` and `SheetsClient` instances with in-process fakes when the server is launched in E2E mode.

**Why not mock at the Playwright `page.route` level?**
- That bypasses `availability.py`, our most logic-heavy module. We'd be testing the widget against fiction, not against our own code.
- Booking would never exercise `main.py:/book` validation, race-condition checks, or the SM-YYYYMMDD-XXXXXX confirmation number format.

**Why not refactor to FastAPI `Depends()` injection?**
- It's a worthwhile cleanup but not necessary for this. Module-level `shopmonkey_client` and `sheets_client` globals (main.py:70-71) can be swapped during the lifespan startup based on an env flag. Minimum disruption to existing code.

### Server lifecycle

A Playwright `globalSetup` script spawns `uvicorn main:app --port 8081` with `E2E_MODE=1` in the env. `globalTeardown` kills it. Tests run against `http://127.0.0.1:8081`.

The server is shared across tests in a run for speed. State is reset per-test via a test-only endpoint (see below).

### Per-test fixture state

In E2E_MODE the server exposes `POST /test/state` (404 otherwise). Tests call it before each scenario to set:

- `services`: list of canned services (id, name, category, labels, duration, price)
- `techs`: tech roster with department skill matrix (the Sheets data)
- `appointments`: existing bookings keyed by date
- `active_user_ids`: which techs are "active" in Shopmonkey (drives the deferred-active-state behavior from f62e9b7)
- `errors`: optional error injection per endpoint (e.g., make `get_appointments_for_date` raise to test error UI)

A default fixture set (`fixtures/default.json`) is loaded on server startup so tests that don't care about state still get a sensible world.

### Booking submission

When the widget POSTs `/book`, the mock `ShopmonkeyClient.create_appointment` records the call and returns a fake appointment with a deterministic id. Tests assert:
- The widget shows the confirmation number.
- The mock received the expected payload (assigned tech, customer, vehicle, time window).

No real Shopmonkey, no real customer or vehicle records.

## Proposed structure

```
e2e/
  package.json              # @playwright/test, typescript
  playwright.config.ts      # one project per browser (chromium + mobile chromium)
  tsconfig.json
  global-setup.ts           # spawn uvicorn, wait for /health, return baseUrl
  global-teardown.ts        # kill the subprocess
  fixtures/
    default.json            # baseline services / techs / appointments
    scenarios.ts            # helpers: setScenario(page, partial state)
  helpers/
    widget.ts               # page object: selectCategory, applyFilter, pickSlot, fillForm, submit
    deeplinks.ts            # URL builders
  tests/
    services.spec.ts        # category tabs render, counts are right
    detail-filters.spec.ts  # vehicle size + service type matrix
    tint-filters.spec.ts    # ★ includes the Full Vehicle / XL SUV/Van regression
    availability.spec.ts    # date picker, slot calculation, multi-day services
    booking.spec.ts         # happy path + form validation
    booking-errors.spec.ts  # no slots, booking-time conflict, backend 5xx
    deeplinks.spec.ts       # ?service_id, ?service_name preselect
    mobile.spec.ts          # responsive layout, iframe height postMessage
    departments.spec.ts     # config-driven department disabling

tests/
  e2e_mocks/                # NEW python package (sibling of existing tests/)
    __init__.py
    install.py              # install_mocks(): swap clients on the main module
    mock_shopmonkey.py      # async fake matching ShopmonkeyClient surface
    mock_sheets.py          # sync fake matching SheetsClient surface
    state.py                # in-memory fixture store, JSON load/dump
    test_endpoints.py       # FastAPI router for /test/state, guarded by E2E_MODE

main.py
  # +6 lines in lifespan: if os.getenv("E2E_MODE"): from tests.e2e_mocks.install
  #                       install_mocks(app)
```

## Capability coverage matrix

| Capability | Spec file | Key assertions |
| --- | --- | --- |
| Service catalog loads | services.spec.ts | Categories render with correct counts; "no services" empty state |
| Detail vehicle size filter | detail-filters.spec.ts | Each size shows only matching services; "Any" shows all |
| Detail service type filter | detail-filters.spec.ts | Interior / Exterior / Combo / Express filter independently and combine |
| **Tint area filter (group match)** | tint-filters.spec.ts | "Full Vehicle" chip shows Coupe + Sedan + SUV + XL SUV/Van. Regression for Anne's bug. |
| Tint type filter | tint-filters.spec.ts | Carbon / Ceramic filter rows correctly |
| Service search | services.spec.ts | Free-text matches name and category |
| Calendar render | availability.spec.ts | Past dates disabled; weekends honored from config.yaml |
| Slot calculation | availability.spec.ts | Slots respect business hours, existing appointments, service duration |
| Multi-day services | availability.spec.ts | Service > 5h spans days; confirmation reflects span |
| Booking happy path | booking.spec.ts | Form → submit → confirmation number matches SM-YYYYMMDD-XXXXXX |
| Form validation | booking.spec.ts | Required fields, email format, phone format |
| No availability | booking-errors.spec.ts | UI message when no techs qualified, when all slots booked |
| Booking race | booking-errors.spec.ts | Re-validation rejects a slot taken between availability and submit |
| Backend 5xx | booking-errors.spec.ts | Error injected → graceful UI message |
| Deeplink by id | deeplinks.spec.ts | ?service=ID auto-selects and scrolls into view |
| Deeplink by name | deeplinks.spec.ts | ?service_name matches partial |
| Deeplink → filter pre-apply | deeplinks.spec.ts | Selecting tint deeplink sets group filter (regression-proof) |
| Mobile layout | mobile.spec.ts | At 375px width, key elements visible without horizontal scroll |
| Iframe postMessage | mobile.spec.ts | Embed page receives height messages; height matches content |
| Department disabled | departments.spec.ts | Disabled department services hidden; exception override re-enables |
| Deferred tech active state | departments.spec.ts | Inactive in Shopmonkey + active in sheet → hidden; sheet "Inactive" override → hidden even if Shopmonkey says active |
| Health endpoints | services.spec.ts (smoke) | /health returns ok in E2E mode |

## Mock fidelity contract

The mocks must match the *public surface* of the real clients used by main.py and availability.py. To keep mocks honest:

- A pytest test (`tests/e2e_mocks/test_surface_parity.py`) enumerates the methods used by main.py / availability.py and asserts the mock implements each one with a compatible signature.
- If main.py calls a new client method, surface-parity test fails until the mock is updated.

This is cheap insurance against mocks drifting out of sync with reality.

## Test data philosophy

- **Default fixture is realistic**: includes the actual problematic services (Window Tint - Full XL SUV/Van Carbon/Ceramic, Detail levels per vehicle size, etc.) so the default spec catches regressions even without per-test setup.
- **Scenario fixtures are minimal**: a scenario file overrides only the slices it needs. E.g., `tint-only.json` strips Detail services so the tint specs are fast.
- **Time is frozen**: all tests run with a fixed "now" injected through the mocks so date math is deterministic.

## Commands

After implementation, the developer workflow is:

```bash
# from repo root, one-time
cd e2e && npm install && npx playwright install chromium

# run all e2e tests (spawns its own server)
cd e2e && npm test

# run a single spec
cd e2e && npm test -- tint-filters

# headed / debug
cd e2e && npm run test:headed
cd e2e && npm run test:debug

# update fixtures (regenerate from a sandbox if needed; future work)
```

Existing `pytest` continues to work unchanged and does not need the e2e env.

## Risks and open questions

1. **Port conflicts.** 8081 might collide on some machines. Mitigation: pick port via `get_free_port()` in `globalSetup` and pass it through `process.env` to tests.
2. **uvicorn startup time.** ~1-2s. Acceptable for now; if it becomes a problem we can use FastAPI's TestClient over ASGI in-process (loses real-HTTP fidelity, but ~10x faster).
3. **Time freezing.** Backend uses `datetime.now()` in several places. We need to inject a clock or monkey-patch `datetime` in mock-installed mode. Decision needed: pass `now` as a header, or freeze globally per-test via `/test/state`.
4. **CI later.** Out of scope to wire up Github Actions now, but the design supports it - the only requirement is `playwright install` and Python deps in the runner.
5. **The /test/state endpoint exposed in prod by accident.** Mitigation: it's only mounted when `E2E_MODE=1` is set at startup. A unit test asserts /test/state is 404 when the env is unset.

## Rollout plan

If you approve this design, implementation order:

1. **Backend mock plumbing** (tests/e2e_mocks/ + main.py hook + /test/state).
2. **Surface parity test** (pytest) - ensures mocks stay honest from day one.
3. **Playwright scaffolding** (e2e/ folder, globalSetup, one trivial spec to prove it works).
4. **Page-object helper** (helpers/widget.ts).
5. **Spec files** in capability order, with **tint-filters.spec.ts written first** so Anne's bug has a regression test today.
6. **README** in e2e/ explaining how to run and add tests.

Each step is a separate commit. After step 1-3 we can stop and reassess if priorities change.

## Decisions (locked 2026-05-19)

1. **Overall approach**: backend-boundary mocking + Playwright. The widget, FastAPI, and `availability.py` all run for real; only `ShopmonkeyClient` and `SheetsClient` are swapped.
2. **Time injection**: **none**. Tests use relative dates ("today + N days"). No clock freezing, no `/test/state` clock setter, no `E2E_NOW` env var.
   - *Implication*: avoid hard-coded weekday assertions. Where a test needs a specific weekday (e.g. business-hours config testing), compute the next matching weekday from today rather than picking a fixed date. Where a test asserts on the confirmation-number date portion, compare against `new Date()` at test time.
   - *Risk acknowledged*: tests crossing midnight or DST boundaries may be flaky. Mitigation: keep date math inside helpers so we can change strategy later without rewriting every spec.
3. **Booking mock**: stateful. `create_appointment` persists in-memory; follow-up `get_appointments_for_date` reflects new bookings within the same test. Race-condition coverage comes free.
4. **Mobile**: chromium mobile emulation only (one Playwright project using the iPhone 14 device descriptor). No WebKit project in v1.
5. **Auxiliary checks**: none. No visual regression, no axe-core, no Lighthouse. v1 is functional only. Reassess once the suite is in active use.
