# Build warnings and dependency audit

**Date:** 2026-08-22
**Basis:** CI run `32598583220` (commit `e02c282`), all jobs green.
**Scope:** warnings surfaced by the pipeline, and headroom between the version
ranges in `requirements.txt` / `e2e/package.json` and what is published today.

Nothing here is breaking the build. This is the list of things that will bite
later, ordered by when they will.

---

## 1. Build warnings

### 1.1 `httpx` is deprecated for Starlette's TestClient — and stale generally

The only warning pytest emits:

```
fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with
`starlette.testclient` is deprecated; install `httpx2` instead.
```

This is worth more attention than a test-only deprecation, because of what sits
behind it:

| package | latest | released |
| --- | --- | --- |
| `httpx` | 0.28.1 | 2024-12-06 |
| `httpx2` | 2.12.0 | 2026-08-18 |

`httpx` has not shipped a release in ~20 months; `httpx2` carries the identical
summary ("The next generation HTTP client") and is where the project moved. Our
pin `httpx>=0.28,<0.29` is already *at* the newest `httpx`, so this is not a
bump we can take — it is a rename to migrate to.

**This is not confined to tests.** `httpx` is the transport for every Shopmonkey
API call in `shopmonkey_client.py`, and the retry logic keys off its exception
types (`HTTPStatusError`, `TimeoutException`, `NetworkError`) in both the client
and `tests/test_shopmonkey_client.py`. A dozen `scripts/probe_*.py` use it too.

**Recommendation: do not migrate yet.** `httpx2` 2.12.0 is four days old at time
of writing. Moving the client that books real appointments onto a package that
new, to silence one warning, is a bad trade. Revisit when it has some mileage,
and treat it as its own change with its own test pass — not a drive-by bump.

### 1.2 Container installs dependencies as root — HELD, not shipped

```
WARNING: Running pip as the 'root' user can result in broken permissions ...
```

`Dockerfile` is `FROM python:3.12-slim` with `RUN pip install --no-cache-dir -r
requirements.txt` and no `USER` directive, so both the build and the running
container are root. Cloud Run sandboxes the container, so this is hardening
rather than an active vulnerability.

**Deliberately not shipped.** Two things make this unverifiable right now:

1. `docker` is not installed on the dev machine, so the image cannot be built
   or run locally.
2. **CI's deploy gate is blind to how this fails.** `/health` (main.py:1410)
   returns an unconditional 200 with no Sheets or Shopmonkey call, and
   `sheets_client._get_service()` reads `/secrets/google-credentials.json`
   *lazily* on the first sheet read. A container that boots but cannot traverse
   `/secrets` as a non-root uid would pass the health check, never trigger the
   auto-rollback, and post success to Slack — while every real booking fails.

The verified edit, for when it can be built and canaried:

```dockerfile
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# After the pip layer so the dependency cache survives this edit. Deps stay
# root-owned: the serving process can import them but cannot rewrite them.
RUN useradd --create-home --uid 10001 appuser
ENV HOME=/home/appuser

COPY . .
EXPOSE 8080

# /app stays root-owned and world-readable. The app only reads config.yaml and
# static/, never writes to disk. PORT is 8080, bindable unprivileged.
USER appuser
CMD ["python", "main.py"]
```

No `COPY --chown` — that would make the app's own source writable by the uid
that parses untrusted HTTP bodies.

**Correction (2026-08-22).** An earlier version of this section claimed the
prerequisite was to make `/health/ready` touch the credentials file. That was
wrong: it already did. `health_check()` (sheets_client.py:451) calls
`_read_sheet(use_cache=False)`, which reaches `_get_service()` and forces the
lazy credential load — a client pointed at a nonexistent path returns
`FileNotFoundError` through the health check. The real gap was that the deploy
gate curled `/health`, an unconditional 200 that touches nothing. Closed
separately by pointing the gate at `/health/ready` and gating on the body.

### 1.2b No `.dockerignore` — FIXED

Found while scoping 1.2. The Dockerfile does `COPY . .` with no `.dockerignore`,
so the entire repo root entered the image:

- **`.env` and `credential.json`.** Both gitignored, so CI's clean checkout never
  had them — but `CLAUDE.md:28` documents `docker build -t shopmonkey-scheduler .`
  for local use, and a local build baked the live Shopmonkey API token and Google
  service-account key into an image layer.
- **The full git history.** The deploy job checks out with `fetch-depth: 0`, so
  3.9M of `.git` shipped in every production image.
- Locally also `.venv` (232M), `e2e/node_modules` (44M), `.omc` (1.6M).

Fixed by adding `.dockerignore`. `tests/` is excluded too: it is imported only
under `E2E_MODE` (main.py:459, 493), and the Cloud Run service does not set that
variable — confirmed against the live service, which carries 12 env vars, none of
them `E2E_MODE`.

### 1.3 Not actionable (noise)

- `hint: ... suppress this warning` — git's default-branch hint from `checkout`.
- `tar --warning=no-unknown-keyword` — internal to `setup-gcloud`.

### 1.4 Resolved this session

The Node 20 deprecation annotations on all three jobs are gone as of `e02c282`,
which moved every action to its current major. `npm audit` reports
**0 vulnerabilities**.

---

## 2. Python dependencies

Ranges in `requirements.txt` whose ceiling now sits below the published latest.
"CI installs" is what the range actually resolves to.

| package | range | CI installs | latest | gap |
| --- | --- | --- | --- | --- |
| `fastapi` | `>=0.115,<0.142` | 0.141.1 | 0.141.1 | **current** |
| `uvicorn[standard]` | `>=0.32,<0.53` | 0.52.4 | 0.52.4 | **current** |
| `structlog` | `>=26.1,<27` | 26.1.0 | 26.1.0 | **current** |
| `httpx` | `>=0.28,<0.29` | 0.28.1 | 0.28.1 | at latest; see 1.1 |

Comfortably inside their ranges, no action: `pydantic`, `google-api-python-client`,
`google-auth`, `pytest` (9.1.1 < 10), `pytest-asyncio` (1.4.0 < 2), `cachetools`
(7.1.7 < 8), `aiosmtplib` (5.1.2 < 6), `tenacity`, `pyyaml`, `email-validator`,
`python-dotenv`.

`ruff` is pinned exactly (`==0.16.4`) as of `be42829`. Leave it pinned — it is a
CI gate, and an unpinned formatter is what blocked the deploy for two commits.

`structlog` 26 is the one major bump here. We use it in anger — bound loggers,
`capture_logs` in tests, and the `log_level` / field assertions added with the
vehicle fix — so that one deserves its release notes read, not a blind bump.

---

## 3. npm dependencies (`e2e/`)

| package | current | wanted | latest | note |
| --- | --- | --- | --- | --- |
| `@playwright/test` | 1.60.0 | 1.62.1 | 1.62.1 | in-range; `npm update` takes it |
| `@types/node` | 22.19.19 | 22.20.1 | 26.2.0 | 4 majors behind, pinned `^22` |
| `typescript` | 5.9.3 | 5.9.3 | 7.0.2 | 2 majors behind, pinned `^5.7` |

`@playwright/test` is free — already allowed by `^1.49.0`. The other two need the
`package.json` range widened, and TypeScript 5 → 7 is the kind of jump that wants
its own change.

Note the browser install (`npx playwright install`) must stay in step with the
`@playwright/test` version; CI installs fresh each run, so it follows along.

---

## 4. Environment drift

| environment | Python |
| --- | --- |
| local `.venv` | **3.13.12** |
| CI (`setup-python`) | **3.12** |
| `Dockerfile` / production | **3.12-slim** |

Local development runs a different minor Python than both CI and production.
This is the same shape of problem as the ruff drift that blocked the deploy on
`36cd9c0` and `8ca8fd4`: the local gate and the CI gate were not the same gate,
so "green locally" meant nothing. CI and prod agree with each other, which is
what matters most — but a local venv on 3.12 would close the loop.

---

## 5. Suggested order

| # | item | status |
| --- | --- | --- |
| 1 | Local venv on Python 3.12 | **done** — rebuilt on 3.12.13 |
| 2 | `@playwright/test` 1.62 | **done** — `7442d92`, lockfile only, e2e 37 passed |
| 3 | `fastapi` + `uvicorn` ceilings | **done** — `fac6d53`, `1475ddc` |
| 4 | Non-root `USER` | **held** — see 1.2; unverifiable here |
| 4b | `.dockerignore` | **done** — see 1.2b |
| 5 | `structlog` 26 | **done** — `fac6d53` |
| 6 | `@types/node` / `typescript` majors | **declined** — see below |
| 7 | `httpx` → `httpx2` | **held** — see 1.1 |

**Why 6 is a decline rather than a deferral.** TypeScript 7 removed
`moduleResolution=node10`, which `e2e/tsconfig.json` sets, so `tsc` fails
outright; and 7.0.2 ships `bin/tsc` with no `tsserver`, trading the workspace
language service for a compiler no CI step invokes. Since nothing in CI runs
`tsc`, a broken one would sit there green and unnoticed. `@types/node` 26 is
mechanically clean but is the non-LTS Current line while CI installs Node 22,
so it would aim the types away from the runtime.
