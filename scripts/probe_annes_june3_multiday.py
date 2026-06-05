"""Investigate Anne's June 3 report: multi-day Window Tint booking only
blocked 5:00-5:30 PM on June 3 and never rolled onto June 4.

Read-only. Pulls:
1. The appointment for confirmation SM-20260603-1BE359 (June 3) and
   SM-20260615-20AB62 (June 15) - start/end, technician, created/updated
   timestamps (updatedDate tells us if staff manually moved one).
2. The linked order's service labors so we know the real labor hours.
3. The bookable canned service's labor hours (expected appointment span).

NOTE: the Shopmonkey where-filter uses Mongo-style operators WITHOUT the
`$` prefix (`{"gte": ...}`); `$gte` is silently ignored (see
shopmonkey_client.get_appointments_for_date docstring).
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

CASES = [
    ("SM-20260603-1BE359", "2026-06-02T00:00:00Z", "2026-06-05T00:00:00Z"),
    ("SM-20260615-20AB62", "2026-06-14T00:00:00Z", "2026-06-17T00:00:00Z"),
]


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


def main() -> int:
    loc_params: dict[str, Any] = {"locationId": LOCATION_ID} if LOCATION_ID else {}
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        for conf, gte, lt in CASES:
            print(f"\n{'=' * 72}\nConfirmation: {conf}\n{'=' * 72}")
            params = {
                "where": json.dumps({"startDate": {"gte": gte, "lt": lt}}),
                "limit": "100",
                **loc_params,
            }
            appts = get(c, "/v3/appointment", params=params).get("data", [])
            print(f"  ({len(appts)} appointments in window)")
            hit = next((a for a in appts if conf in (a.get("note") or "")), None)
            if not hit:
                print("  NOT FOUND")
                continue

            for k in (
                "id",
                "name",
                "startDate",
                "endDate",
                "technicianId",
                "orderId",
                "createdDate",
                "updatedDate",
                "origin",
            ):
                print(f"  {k}: {hit.get(k)}")

            order_id = hit.get("orderId")
            if order_id:
                order = get(
                    c, f"/v3/order/{order_id}", params={"include": json.dumps({"services": True})}
                ).get("data", {})
                print(f"\n  Order #{order.get('number')} services:")
                for svc in order.get("services") or []:
                    total_hours = 0.0
                    for labor in svc.get("labors") or []:
                        total_hours += float(labor.get("hours") or 0)
                        print(
                            f"    labor: {labor.get('name')!r} hours={labor.get('hours')} "
                            f"technicianId={labor.get('technicianId')}"
                        )
                    print(f"    service {svc.get('name')!r} total labor hours = {total_hours}")

        # Bookable canned services matching the booked service name
        print(f"\n{'=' * 72}\nBookable canned service lookup\n{'=' * 72}")
        cs_params = {"where": json.dumps({"bookable": True}), **loc_params}
        services = get(c, "/v3/canned_service", params=cs_params).get("data", [])
        print(f"  ({len(services)} bookable services)")
        for svc in services:
            name = svc.get("name") or ""
            if "ceramic" in name.lower() and "sedan" in name.lower():
                detail = get(c, f"/v3/canned_service/{svc['id']}").get("data", {})
                hours = sum(
                    float(labor.get("hours") or 0) for labor in detail.get("labors") or []
                )
                print(f"  {name!r} id={svc['id'][:8]} labor hours={hours}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
