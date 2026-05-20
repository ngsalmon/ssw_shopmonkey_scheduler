"""Check whether labor.technicianId is actually set in practice.

The per-tech availability fix depends on walking
Appointment -> orderId -> Order -> services -> labors -> technicianId.

That only works if labors actually have technicianId populated. If they're
mostly null (tech only gets assigned after work happens), the chain
returns nothing and we'd need a different strategy.

Read-only. Samples a handful of recent appointments across origins
(OOTB Scheduler, manual shop entries, our /book bookings) and reports
how many labors have a tech assigned.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def loc(p: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(p or {})
    if LOCATION_ID:
        out["locationId"] = LOCATION_ID
    return out


def get(c: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
    r = c.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # Fetch upcoming appointments (future-dated, customer-linked).
        where = {"startDate": {"gte": "2026-05-20T00:00:00Z", "lt": "2026-06-30T00:00:00Z"}}
        upcoming = get(
            c,
            "/v3/appointment",
            params=loc({"where": json.dumps(where), "limit": "100"}),
        ).get("data", [])
        with_order = [a for a in upcoming if a.get("orderId")]
        print(f"Upcoming customer appointments (May 20 - Jun 30, 2026): {len(with_order)}\n")

        # Group by origin so we can see Scheduler vs Shop vs ours.
        by_origin: dict[str, list[dict]] = defaultdict(list)
        for a in with_order:
            by_origin[a.get("origin") or "<none>"].append(a)
        for origin, lst in by_origin.items():
            print(f"  origin={origin!r}: {len(lst)} appointments")
        print()

        # Walk each appointment to labors. Tally tech-assigned vs not.
        labor_with_tech = 0
        labor_total = 0
        appts_with_any_tech = 0
        details: list[dict[str, Any]] = []

        for appt in with_order[:30]:
            try:
                services = get(c, f"/v3/order/{appt['orderId']}/service").get("data", []) or []
            except httpx.HTTPStatusError as e:
                print(f"  skip {appt['id'][:8]} order fetch failed: {e.response.status_code}")
                continue
            tech_ids_this_appt: set[str] = set()
            this_appt_labor_count = 0
            this_appt_with_tech = 0
            for svc in services:
                for labor in svc.get("labors") or []:
                    labor_total += 1
                    this_appt_labor_count += 1
                    tid = labor.get("technicianId")
                    if tid:
                        labor_with_tech += 1
                        this_appt_with_tech += 1
                        tech_ids_this_appt.add(tid)
            if tech_ids_this_appt:
                appts_with_any_tech += 1
            details.append(
                {
                    "id": appt["id"][:8],
                    "origin": appt.get("origin"),
                    "name": (appt.get("name") or "")[:60],
                    "labors": this_appt_labor_count,
                    "labors_with_tech": this_appt_with_tech,
                    "tech_ids": sorted(tech_ids_this_appt),
                }
            )

        print(
            f"\nSampled {len(details)} appointments → "
            f"{labor_with_tech}/{labor_total} labors have technicianId "
            f"({(labor_with_tech / labor_total * 100) if labor_total else 0:.0f}%)"
        )
        print(
            f"{appts_with_any_tech}/{len(details)} appointments have at least one "
            f"tech-assigned labor "
            f"({(appts_with_any_tech / len(details) * 100) if details else 0:.0f}%)\n"
        )

        print("Per-appointment detail:")
        for d in details:
            tech_str = ", ".join(t[:8] for t in d["tech_ids"]) if d["tech_ids"] else "(no tech)"
            print(
                f"  {d['id']} origin={d['origin']:>10} "
                f"labors={d['labors_with_tech']}/{d['labors']:>2} "
                f"techs=[{tech_str}]  name={d['name']!r}"
            )

        # Also peek at a recent past appointment to see if completed work has techs
        print("\n---\nNow sampling 10 PAST appointments to compare (work likely done):")
        past_where = {"startDate": {"gte": "2026-04-01T00:00:00Z", "lt": "2026-05-15T00:00:00Z"}}
        past = get(
            c,
            "/v3/appointment",
            params=loc({"where": json.dumps(past_where), "limit": "100"}),
        ).get("data", [])
        past_with_order = [a for a in past if a.get("orderId")][:10]
        past_labor_with_tech = 0
        past_labor_total = 0
        for appt in past_with_order:
            try:
                services = get(c, f"/v3/order/{appt['orderId']}/service").get("data", []) or []
            except httpx.HTTPStatusError:
                continue
            for svc in services:
                for labor in svc.get("labors") or []:
                    past_labor_total += 1
                    if labor.get("technicianId"):
                        past_labor_with_tech += 1
        print(
            f"  past: {past_labor_with_tech}/{past_labor_total} labors have technicianId "
            f"({(past_labor_with_tech / past_labor_total * 100) if past_labor_total else 0:.0f}%)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
