"""Can GET /v3/appointment filter by assigned technician (any-match)?

Read-only. The where-syntax uses Mongo/Prisma-style operators WITHOUT the
`$` prefix, and the API silently returns the FULL UNFILTERED set for
unknown filter fields - so a "working" response must be validated two
ways: (a) row count differs from the unfiltered baseline, and (b) every
returned row actually contains the target tech in `technicians`.

Candidates tried (Prisma to-many relation filter conventions first, since
the schema dump and operator style suggest a Prisma backend):
  {"technicians": {"some": {"id": X}}}
  {"technicians": {"every": {"id": X}}}
  {"technicians": {"id": X}}
  {"technicians.id": X}
  {"technicianId": X}        (known-ignored per 2026-05-20 probe; control)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Mina Vang (from the June 3 window - she has bookings there).
TECH_ID = "269943af-9eae-42b4-a5c6-0e5949b0e3f2"
DATE_WINDOW = {"startDate": {"gte": "2026-06-02T00:00:00Z", "lt": "2026-06-05T00:00:00Z"}}


def get(c: httpx.Client, path: str, params: dict[str, Any]) -> httpx.Response:
    for attempt in range(6):
        r = c.get(path, params=params)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        return r
    return r


def rows_with_tech(rows: list[dict[str, Any]]) -> int:
    return sum(
        1 for a in rows if any(t.get("id") == TECH_ID for t in (a.get("technicians") or []))
    )


def main() -> int:
    loc_params: dict[str, Any] = {"locationId": LOCATION_ID} if LOCATION_ID else {}
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # Baseline: date window only.
        base = get(
            c,
            "/v3/appointment",
            {"where": json.dumps(DATE_WINDOW), "limit": "100", **loc_params},
        ).json()["data"]
        expected = rows_with_tech(base)
        print(f"Baseline June 2-4: {len(base)} rows, {expected} contain tech {TECH_ID[:8]}\n")

        candidates: list[tuple[str, dict[str, Any]]] = [
            ("technicians.some.id", {**DATE_WINDOW, "technicians": {"some": {"id": TECH_ID}}}),
            ("technicians.every.id", {**DATE_WINDOW, "technicians": {"every": {"id": TECH_ID}}}),
            ("technicians.id (bare)", {**DATE_WINDOW, "technicians": {"id": TECH_ID}}),
            ("dot-path technicians.id", {**DATE_WINDOW, "technicians.id": TECH_ID}),
            ("technicianId (control)", {**DATE_WINDOW, "technicianId": TECH_ID}),
        ]

        for label, where in candidates:
            r = get(
                c,
                "/v3/appointment",
                {"where": json.dumps(where), "limit": "100", **loc_params},
            )
            if r.status_code != 200:
                print(f"  {label:28} -> HTTP {r.status_code}: {r.text[:120]}")
                continue
            rows = r.json().get("data", [])
            hits = rows_with_tech(rows)
            verdict = (
                "WORKS (exact match set)"
                if rows and hits == len(rows) and len(rows) == expected
                else "filter ignored (full set)"
                if len(rows) == len(base)
                else f"partial? rows={len(rows)} with-tech={hits}"
            )
            print(f"  {label:28} -> {len(rows)} rows, {hits} with tech -> {verdict}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
