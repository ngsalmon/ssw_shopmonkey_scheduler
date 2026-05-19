"""Create one test booking against the live Shopmonkey environment.

Goal: nail down the exact POST shapes for /v3/order, /v3/service_item, and
appointment-with-orderId so we can implement OOTB parity. Every created
record is clearly named "CLAUDE TEST - DELETE ME" so staff can clean up.

Run only when ready; refuses to do anything unless --create is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

TEST_PREFIX = "CLAUDE TEST - DELETE ME"


def get(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = client.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def post(client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
    r = client.post(path, json=body)
    if r.status_code >= 400:
        print(f"  POST {path} failed [{r.status_code}]: {r.text}")
        r.raise_for_status()
    return r.json()


def next_weekday(days_out: int) -> date:
    d = date.today() + timedelta(days=days_out)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main(create: bool) -> int:
    if not create:
        print("Dry run. Pass --create to actually hit Shopmonkey.")
        return 0

    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # 1. Resolve workflow_status "Scheduled" id
        ws = get(c, "/v3/workflow_status").get("data", [])
        scheduled = next((s for s in ws if s.get("name") == "Scheduled"), None)
        if not scheduled:
            print("No 'Scheduled' workflow status found.")
            return 1
        scheduled_id = scheduled["id"]
        print(f"workflow_status 'Scheduled' = {scheduled_id}")

        # 2. Find a bookable canned service (cheapest, alphabetical first)
        services_params: dict[str, Any] = {"where": json.dumps({"bookable": True})}
        if LOCATION_ID:
            services_params["locationId"] = LOCATION_ID
        services = get(c, "/v3/canned_service", params=services_params).get("data", [])
        if not services:
            print("No bookable canned services found.")
            return 1
        svc = sorted(services, key=lambda s: s.get("totalCents") or 0)[0]
        print(f"canned_service: id={svc['id']} name={svc.get('name')!r}")

        # 3. Create or reuse a test customer
        cust_where = {"firstName": "Claude", "lastName": "Test"}
        cust_params: dict[str, Any] = {"where": json.dumps(cust_where)}
        if LOCATION_ID:
            cust_params["locationId"] = LOCATION_ID
        existing = get(c, "/v3/customer", params=cust_params).get("data", [])
        if existing:
            customer = existing[0]
            print(f"reusing customer id={customer['id']}")
        else:
            cust_body: dict[str, Any] = {
                "firstName": "Claude",
                "lastName": "Test",
                "email": "claude-test@example.com",
                "customerType": "Customer",
            }
            if LOCATION_ID:
                cust_body["locationId"] = LOCATION_ID
            customer = post(c, "/v3/customer", cust_body).get("data", {})
            print(f"created customer id={customer['id']}")

        # 4. Create or reuse a test vehicle
        veh_where = {"customerId": customer["id"], "year": 2020, "make": "Test", "model": "Vehicle"}
        veh_params: dict[str, Any] = {"where": json.dumps(veh_where)}
        if LOCATION_ID:
            veh_params["locationId"] = LOCATION_ID
        existing_v = get(c, "/v3/vehicle", params=veh_params).get("data", [])
        if existing_v:
            vehicle = existing_v[0]
            print(f"reusing vehicle id={vehicle['id']}")
        else:
            veh_body: dict[str, Any] = {
                "customerId": customer["id"],
                "year": 2020,
                "make": "Test",
                "model": "Vehicle",
                "size": "LightDuty",
            }
            if LOCATION_ID:
                veh_body["locationId"] = LOCATION_ID
            vehicle = post(c, "/v3/vehicle", veh_body).get("data", {})
            print(f"created vehicle id={vehicle['id']}")

        # 5. Create the order (RO) in the Scheduled column
        order_body: dict[str, Any] = {
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "workflowStatusId": scheduled_id,
            "status": "Estimate",
            "color": "blue",
            "name": f"{TEST_PREFIX} / 2020 Test Vehicle / {svc.get('name')}",
        }
        if LOCATION_ID:
            order_body["locationId"] = LOCATION_ID
        order_resp = post(c, "/v3/order", order_body)
        order = order_resp.get("data", order_resp)
        order_id = order["id"]
        print(f"\nCREATED ORDER: id={order_id} number={order.get('number')}")
        # Surface fields likely to drift between docs and reality
        for k in ("id", "number", "status", "color", "name", "workflowStatusId"):
            print(f"  {k}: {order.get(k)!r}")

        # 6. Attach the service item to the order
        # We don't know required fields yet - try the minimal shape and grow
        # from the 4xx error if needed.
        si_body: dict[str, Any] = {
            "orderId": order_id,
            "cannedServiceId": svc["id"],
            "name": svc.get("name") or "Service",
        }
        if LOCATION_ID:
            si_body["locationId"] = LOCATION_ID
        try:
            si_resp = post(c, "/v3/service_item", si_body)
            service_item = si_resp.get("data", si_resp)
            print(f"\nCREATED SERVICE ITEM: id={service_item.get('id')}")
            for k in ("id", "name", "orderId", "cannedServiceId"):
                print(f"  {k}: {service_item.get(k)!r}")
        except httpx.HTTPStatusError as e:
            print(f"  service_item POST returned {e.response.status_code}; body was:")
            print(f"  {json.dumps(si_body, indent=2)}")
            print(f"  response: {e.response.text}")

        # 7. Create the appointment linked to the order
        target = next_weekday(14)
        # 1pm Central
        start_dt = datetime.combine(target, time(13, 0))
        end_dt = datetime.combine(target, time(14, 0))
        # Use +00:00 placeholder; Shopmonkey accepts offsets. Use Central:
        # May 2026 is CDT (-05:00); June still CDT.
        tz_str = "-05:00"
        appt_body: dict[str, Any] = {
            "customerId": customer["id"],
            "vehicleId": vehicle["id"],
            "orderId": order_id,
            "startDate": start_dt.strftime("%Y-%m-%dT%H:%M:%S") + f".000{tz_str}",
            "endDate": end_dt.strftime("%Y-%m-%dT%H:%M:%S") + f".000{tz_str}",
            "color": "blue",
            "name": f"{TEST_PREFIX} / 2020 Test Vehicle / {svc.get('name')}",
            "note": (
                f"*** {TEST_PREFIX} ***\n"
                "Generated by scripts/probe_create_test_booking.py.\n"
                "Safe to delete; created to verify create_order + service_item shape."
            ),
        }
        if LOCATION_ID:
            appt_body["locationId"] = LOCATION_ID
        try:
            appt_resp = post(c, "/v3/appointment", appt_body)
            appt = appt_resp.get("data", appt_resp)
            print(f"\nCREATED APPOINTMENT: id={appt.get('id')}")
            for k in ("id", "orderId", "startDate", "endDate", "name"):
                print(f"  {k}: {appt.get(k)!r}")
        except httpx.HTTPStatusError as e:
            print(f"  appointment POST returned {e.response.status_code}; body was:")
            print(f"  {json.dumps(appt_body, indent=2)}")
            print(f"  response: {e.response.text}")

        print("\nDone. Staff can delete the order + appointment by searching the prefix")
        print(f"  '{TEST_PREFIX}' in Shopmonkey.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true", help="Actually POST to Shopmonkey")
    args = parser.parse_args()
    sys.exit(main(args.create))
