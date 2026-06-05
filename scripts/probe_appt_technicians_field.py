"""Verify Shopmonkey support's claim (2026-06-05): GET /v3/appointment
returns a `technicians` array of associated user objects.

Read-only. Checks:
1. Does the plain list endpoint return `technicians` on appointment rows?
2. Is it populated on STAFF-created appointments (OOTB scheduler)?
3. Is it populated on OUR online bookings (we send `technicianId` on
   create) - e.g. Javion Cotton's June 3 booking?

If (1) and (3) hold, availability can read busy techs straight off the
appointment instead of walking Appointment → Order → Service.labors →
technicianId (N+1 fetches per availability call).
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

ONLINE_BOOKING_CONF = "SM-20260603-1BE359"  # Javion Cotton, June 3


def get(c: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(5):
        r = c.get(path, params=params or {})
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def summarize_techs(appt: dict[str, Any]) -> str:
    techs = appt.get("technicians")
    if techs is None:
        return "technicians key MISSING"
    if not techs:
        return "technicians: [] (empty)"
    names = [f"{t.get('firstName')} {t.get('lastName')} ({t.get('id', '')[:8]})" for t in techs]
    return f"technicians: {names}"


def main() -> int:
    loc_params: dict[str, Any] = {"locationId": LOCATION_ID} if LOCATION_ID else {}
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # June 2-5 window: contains Javion's online booking AND plenty of
        # staff-created appointments for comparison.
        params = {
            "where": json.dumps(
                {"startDate": {"gte": "2026-06-02T00:00:00Z", "lt": "2026-06-05T00:00:00Z"}}
            ),
            "limit": "100",
            **loc_params,
        }
        appts = get(c, "/v3/appointment", params=params).get("data", [])
        print(f"{len(appts)} appointments June 2-4\n")

        with_key = sum(1 for a in appts if "technicians" in a)
        non_empty = sum(1 for a in appts if a.get("technicians"))
        print(f"rows with 'technicians' key:      {with_key}/{len(appts)}")
        print(f"rows with non-empty technicians:  {non_empty}/{len(appts)}\n")

        print("Sample (first 10 orderId-bearing appointments):")
        shown = 0
        for a in appts:
            if not a.get("orderId"):
                continue
            origin = "ONLINE" if "ONLINE BOOKING" in (a.get("note") or "") else "staff "
            print(f"  [{origin}] {a.get('name', '')[:48]:48} {summarize_techs(a)}")
            shown += 1
            if shown >= 10:
                break

        hit = next((a for a in appts if ONLINE_BOOKING_CONF in (a.get("note") or "")), None)
        print(f"\nJavion's online booking ({ONLINE_BOOKING_CONF}):")
        if hit:
            print(f"  technicianId field: {hit.get('technicianId')!r}")
            print(f"  {summarize_techs(hit)}")
            print(f"  all keys: {sorted(hit.keys())}")
        else:
            print("  NOT FOUND")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
