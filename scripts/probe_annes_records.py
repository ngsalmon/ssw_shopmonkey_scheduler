"""Look up the specific records Anne flagged and figure out what happened.

Read-only. Searches for the two confirmation numbers, pulls the appointment,
the linked customer, and any other appointments that customer has, so we
can see whether the mismatch came from a prior booking, a shared phone,
or something else.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# From the screenshots Anne shared:
CONFIRMATIONS = ["SM-20260520-D4F5EF", "SM-20260520-CBFBF2"]


def get(c: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = c.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def trunc(s: Any, n: int = 80) -> str:
    text = str(s) if s is not None else ""
    return text if len(text) <= n else text[: n - 1] + "…"


def fmt_customer(cust: dict[str, Any]) -> str:
    parts = [
        f"id={cust.get('id', '')[:8]}",
        f"name={cust.get('firstName')!r} {cust.get('lastName')!r}",
        f"email={cust.get('email')!r}",
        f"phone={cust.get('phone')!r}",
        f"customerType={cust.get('customerType')!r}",
        f"createdDate={cust.get('createdDate')}",
    ]
    return " ".join(parts)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # 1. Fetch recent online-booking appointments by note pattern.
        # We can't full-text search note, but recent ONLINE BOOKING appointments
        # share a prefix; pull a wider net and filter client-side.
        loc_params: dict[str, Any] = {}
        if LOCATION_ID:
            loc_params["locationId"] = LOCATION_ID

        # Pull appointments for the May 20 2026 booking date (Anne's screenshots)
        appt_params: dict[str, Any] = {
            "where": json.dumps(
                {
                    "startDate": {
                        "$gte": "2026-05-19T00:00:00Z",
                        "$lt": "2026-05-21T23:59:59Z",
                    }
                }
            ),
            "limit": "100",
            "sort": json.dumps([{"createdDate": "desc"}]),
            **loc_params,
        }
        appts = get(c, "/v3/appointment", params=appt_params).get("data", [])
        print(f"Scanned {len(appts)} appointments for May 19-20 2026\n")

        for conf in CONFIRMATIONS:
            print(f"\n{'=' * 70}")
            print(f"Looking for confirmation: {conf}")
            print("=" * 70)
            hit = next((a for a in appts if conf in (a.get("note") or "")), None)
            if not hit:
                print("  NOT FOUND in last 200 appointments. Widening search...")
                # Try a larger pull
                appt_params2 = {**appt_params, "limit": "500"}
                appts2 = get(c, "/v3/appointment", params=appt_params2).get("data", [])
                hit = next((a for a in appts2 if conf in (a.get("note") or "")), None)
                if not hit:
                    print("  Still not found. Skipping.")
                    continue

            print("\nAppointment record:")
            for k in (
                "id",
                "name",
                "customerId",
                "vehicleId",
                "orderId",
                "startDate",
                "endDate",
                "color",
                "createdDate",
                "origin",
                "publicId",
            ):
                v = hit.get(k)
                print(f"  {k}: {trunc(v)}")
            print(f"  note (first 400 chars): {trunc(hit.get('note'), 400)}")

            # 2. Pull the customer the appointment is attached to
            cust_id = hit.get("customerId")
            if not cust_id:
                continue
            print(f"\nLinked customer ({cust_id[:8]}):")
            cust_r = get(c, f"/v3/customer/{cust_id}").get("data", {})
            print(f"  {fmt_customer(cust_r)}")
            # Customer emails/phones may be on related sub-resources
            for k in ("emails", "phoneNumbers", "addresses"):
                sub = cust_r.get(k)
                if sub and isinstance(sub, list):
                    print(f"  {k}:")
                    for item in sub:
                        print(f"    - {trunc(item, 200)}")

            # 3. Pull the linked vehicle
            veh_id = hit.get("vehicleId")
            if veh_id:
                try:
                    veh = get(c, f"/v3/vehicle/{veh_id}").get("data", {})
                    print(f"\nLinked vehicle ({veh_id[:8]}):")
                    print(
                        f"  {veh.get('year')} {veh.get('make')} {veh.get('model')} "
                        f"customerId={veh.get('customerId', '')[:8]} "
                        f"createdDate={veh.get('createdDate')}"
                    )
                except httpx.HTTPStatusError as e:
                    print(f"  vehicle lookup failed: {e.response.status_code}")

            # 4. Other appointments tied to the same customer (booking history)
            cust_appt_params = {
                "where": json.dumps({"customerId": cust_id}),
                "sort": json.dumps([{"createdDate": "asc"}]),
                "limit": "20",
                **loc_params,
            }
            other = get(c, "/v3/appointment", params=cust_appt_params).get("data", [])
            print(f"\nAll appointments for this customer ({len(other)} total, oldest first):")
            for a in other[:15]:
                origin = a.get("origin") or "?"
                created = a.get("createdDate", "")
                name_field = trunc(a.get("name") or "", 50)
                print(
                    f"  - {created}  origin={origin}  name={name_field}  "
                    f"id={a.get('id', '')[:8]}  conf-in-note={any(c in (a.get('note') or '') for c in CONFIRMATIONS)}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
