# E2E Tests

End-to-end Playwright tests for the Shopmonkey scheduling widget.

The tests boot a real FastAPI server with `E2E_MODE=1`, which swaps the
Shopmonkey and Sheets clients for in-process fakes. The widget, FastAPI
routes, and `availability.py` all run for real — only the outbound HTTP to
external services is mocked. See `../docs/e2e-test-harness-design.md` for the
full design.

## Quick start

```bash
# One-time install
cd e2e
npm install
npx playwright install chromium

# Run everything (desktop + mobile)
npm test

# Just one spec
npm test -- tint-filters

# Headed / debug
npm run test:headed
npm run test:debug

# Open the HTML report after a failure
npm run report
```

The Playwright config auto-spawns `uvicorn main:app --port 8081` on its own.
Set `E2E_PORT=8082` to use a different port. Set `CI=1` to force a fresh
server per run.

## How the mocks work

When the server boots with `E2E_MODE=1`:

1. `main.py` lifespan skips the real `ShopmonkeyClient` and `SheetsClient`
   constructors and instead calls `tests/e2e_mocks/install.py:install_mocks`.
2. `install_mocks` populates `main.shopmonkey_client` and `main.sheets_client`
   with in-memory fakes (`MockShopmonkeyClient`, `MockSheetsClient`) backed
   by a shared `MockState` singleton.
3. `MockState` loads a realistic default fixture: every Window Tint
   area × type combination, every Detail size × level × kind, plus Bedliner,
   Alignment, Headlight Restoration, and a multi-day ceramic-coating service.
4. A test-only `/test/state` router is mounted on the app, used by specs to
   override services / techs / appointments / errors / config per scenario.

Without `E2E_MODE=1` the mock module is never imported and `/test/state`
returns 404. There's a pytest in `tests/test_e2e_mock_surface_parity.py`
that fails if the real clients grow a method the mocks don't implement.

## Adding a new test

1. **Pick a spec file** — group by capability (tint-filters, booking, etc).
   Create a new file in `tests/` only if the capability is genuinely new.
2. **Reset state in `beforeEach`** — call `resetToDefaults(request)` to start
   from the default fixture.
3. **Override state if needed** — `setScenario(request, { ... })` to inject
   services, techs, appointments, errors, or config overrides.
4. **Drive the widget through the page object** — `helpers/widget.ts`
   centralizes selectors. If you need a new selector, add it there.
5. **Assert on UI + state** — UI assertions for what the user sees; call
   `getState(request)` for invisible side effects like recorded booking
   payloads.

### Available scenario keys

```ts
setScenario(request, {
  reset: true,               // clear all state
  load_default: true,        // re-load the default fixture
  services: [...],           // override service catalog
  techs: [...],              // override tech roster
  appointments: [...],       // seed existing bookings
  errors: {                  // inject errors per endpoint
    get_canned_service: { status_code: 500, message: '...' },
    create_appointment: { status_code: 502 },
  },
  config: {                  // override main.config (shallow merge)
    disabled_departments: {},
  },
});
```

### Date handling

The harness does not freeze time — tests use "today + N days". All date
helpers live in `helpers/dates.ts`:

```ts
import { nextWeekday, toISODate, daysFromToday } from '../helpers/dates';

const target = nextWeekday(1);          // next Mon-Fri at least 1 day out
const iso = toISODate(target);          // "2026-05-20"
```

Avoid hard-coded weekday assertions — compute them from "today" instead.

## Mobile specs

Specs tagged `@mobile` in their test names run only on the
`mobile-chromium` project (iPhone 14 emulation). Untagged specs run on
desktop chromium only.

## Failure debugging

On failure Playwright captures:

- `test-results/<spec>/trace.zip` — interactive timeline. View with
  `npx playwright show-trace test-results/.../trace.zip`.
- `test-results/<spec>/video.webm` — recording of the run.
- `test-results/<spec>/error-context.md` — a snapshot of the DOM at failure.

Common failures:

- **Selector not found** — the widget UI changed. Update `helpers/widget.ts`,
  not individual specs.
- **No slots available** — your fixture may not have a qualified tech for the
  service's department. Check `state.techs` matches a service's first label.
- **`/test/state` 404** — the server was started without `E2E_MODE=1`. The
  Playwright config sets this automatically; if you're running uvicorn by
  hand, you need to set it yourself.
