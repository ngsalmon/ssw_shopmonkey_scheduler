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

### 1.2 Container installs dependencies as root

```
WARNING: Running pip as the 'root' user can result in broken permissions ...
```

`Dockerfile` is `FROM python:3.12-slim` with `RUN pip install --no-cache-dir -r
requirements.txt` and no `USER` directive, so both the build and the running
container are root. Cloud Run sandboxes the container, so this is hardening
rather than an active vulnerability — but a non-root `USER` is cheap and also
silences the warning.

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
| `fastapi` | `>=0.115,<0.137` | 0.136.3 | 0.141.1 | capped |
| `uvicorn[standard]` | `>=0.32,<0.48` | 0.47.0 | 0.52.4 | capped |
| `structlog` | `>=25.0,<26` | 25.5.0 | 26.1.0 | capped, major |
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

1. **Rebuild the local venv on Python 3.12** — removes a whole class of
   "passes locally, fails in CI" surprises. Cheap, no production risk.
2. **`npm update @playwright/test`** in `e2e/` — already in range, no manifest
   change needed.
3. **`fastapi` and `uvicorn` ceilings** — raise and let the test suite judge.
   Both are patch/minor moves within 0.x, so read the changelogs, but the blast
   radius is contained and covered by 556 tests plus the E2E suite.
4. **Non-root `USER` in the Dockerfile** — hardening, silences 1.2.
5. **`structlog` 26** — its own change, changelog read first.
6. **`@types/node` and `typescript` majors** — own change, `e2e/` only.
7. **`httpx` → `httpx2`** — deliberately last. Wait for the package to mature.

Items 1–4 are low risk. 5–7 each deserve to be their own commit with their own
verification.
