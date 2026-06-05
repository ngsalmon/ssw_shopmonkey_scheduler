"""Verify POST /v3/appointment/search filters by assigned technician.

Read-only (search is a query endpoint). Verified 2026-06-05 against prod:

  POST /v3/appointment/search
  {"where": {"startDate": {"gte": ..., "lte": ...},
             "technicians": ["<userId>", ...],
             "includeUnassigned": false},
   "limit": 100, "skip": 0}

- `technicians` is ANY-match: a row is returned if any of its assigned
  techs is in the list (Mina-only -> 7/33 rows; Mina+Gus -> 11/33, every
  row matching at least one of the two).
- `includeUnassigned: true` additionally returns appointments whose
  technicians array is empty.
- The GET /v3/appointment `where` param does NOT support any tech/relation
  filter (11 syntaxes tried in probe_filter_appts_by_tech.py - all
  silently ignored). The search endpoint is the supported path, per
  https://shopmonkey.dev/resources/appointment.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

MINA = "269943af-9eae-42b4-a5c6-0e5949b0e3f2"
WINDOW = {"startDate": {"gte": "2026-06-02T00:00:00Z", "lte": "2026-06-04T23:59:59Z"}}


def search(c: httpx.Client, body: dict[str, Any]) -> list[dict[str, Any]]:
    for attempt in range(6):
        r = c.post("/v3/appointment/search", json=body)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json().get("data", [])
    r.raise_for_status()
    return []


def with_any(rows: list[dict[str, Any]], tech_ids: set[str]) -> int:
    return sum(
        1
        for a in rows
        if any(t.get("id") in tech_ids for t in (a.get("technicians") or []))
    )


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        base = search(c, {"where": WINDOW, "limit": 100})
        print(f"baseline: {len(base)} rows, {with_any(base, {MINA})} with Mina")

        mina_only = search(c, {"where": {**WINDOW, "technicians": [MINA]}, "limit": 100})
        ok = bool(mina_only) and with_any(mina_only, {MINA}) == len(mina_only)
        print(f"technicians=[Mina]: {len(mina_only)} rows, all-match={ok}")

        gus = next(
            (
                t["id"]
                for a in base
                for t in a.get("technicians") or []
                if "Gus" in (t.get("firstName") or "")
            ),
            None,
        )
        if gus:
            pair = search(c, {"where": {**WINDOW, "technicians": [MINA, gus]}, "limit": 100})
            any_match = bool(pair) and with_any(pair, {MINA, gus}) == len(pair)
            print(f"technicians=[Mina,Gus]: {len(pair)} rows, any-match={any_match}")

        unassigned = search(
            c,
            {"where": {**WINDOW, "technicians": [MINA], "includeUnassigned": True}, "limit": 100},
        )
        empty = sum(1 for a in unassigned if not (a.get("technicians") or []))
        print(f"+includeUnassigned: {len(unassigned)} rows ({empty} unassigned in window)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
