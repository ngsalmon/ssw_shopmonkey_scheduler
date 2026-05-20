"""Follow-up swimlane probe after the first pass came up empty.

Things still to check:
- Does Shopmonkey publish an OpenAPI / schema doc we can read?
- Is there a GraphQL endpoint mirroring REST?
- Does the appointment detail have nested fields we missed by reading
  the outer envelope instead of `data`?
- Does writing technicianId actually persist (read-after-write) or is it
  silently dropped on POST?
- Does Shopmonkey provide a "webhooks" or "events" channel that exposes
  the relationship?
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


def loc(p: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(p or {})
    if LOCATION_ID:
        out["locationId"] = LOCATION_ID
    return out


def header(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # -----------------------------------------------------------------
        # 1. Inspect a known scheduler-origin appointment's data dict in full
        # -----------------------------------------------------------------
        header("1. Dump every field of a known [Scheduler] appointment")
        sched = c.get(
            "/v3/appointment",
            params=loc(
                {
                    "where": json.dumps({"origin": "AppointmentScheduler"}),
                    "limit": "1",
                }
            ),
        ).json()
        data_arr = sched.get("data", [])
        if not data_arr:
            print("  no scheduler appointments found")
            return 1
        appt_id = data_arr[0]["id"]
        full_resp = c.get(f"/v3/appointment/{appt_id}").json()
        full = full_resp.get("data") or {}
        print(f"  Appointment id={appt_id}")
        print(f"  Full data keys: {sorted(full.keys())}")
        for k, v in sorted(full.items()):
            if isinstance(v, str) and len(v) > 80:
                v = v[:80] + "..."
            print(f"    {k}: {v}")

        # -----------------------------------------------------------------
        # 2. Try OpenAPI spec at standard locations
        # -----------------------------------------------------------------
        header("2. Look for an OpenAPI / schema doc")
        for path in (
            "/openapi.json",
            "/openapi.yaml",
            "/v3/openapi.json",
            "/v3/openapi",
            "/schema",
            "/v3/schema",
            "/docs",
            "/api-docs",
            "/swagger.json",
            "/swagger",
            "/.well-known/openapi",
        ):
            try:
                r = c.get(path)
                print(f"  {path}: HTTP {r.status_code}")
                if r.status_code == 200 and "appointment" in r.text.lower()[:5000]:
                    # Found something useful
                    print(f"    First 200 chars: {r.text[:200]}")
            except Exception as e:
                print(f"  {path}: error {e}")

        # -----------------------------------------------------------------
        # 3. Try a GraphQL endpoint
        # -----------------------------------------------------------------
        header("3. GraphQL endpoint?")
        for path in ("/graphql", "/v3/graphql", "/api/graphql"):
            try:
                r = c.post(path, json={"query": "{ __typename }"})
                print(f"  POST {path}: HTTP {r.status_code}", end="")
                if r.status_code == 200:
                    print(f"  body={r.text[:200]}")
                else:
                    print()
            except Exception as e:
                print(f"  POST {path}: error {e}")

        # -----------------------------------------------------------------
        # 4. Round-trip test: write technicianId, read back
        # -----------------------------------------------------------------
        header("4. POST technicianId on a new appointment, then GET to verify")
        # Get a customer/vehicle to attach to (read-only - we'll DELETE after)
        cust_resp = c.get("/v3/customer", params=loc({"limit": "1"})).json()
        if not cust_resp.get("data"):
            print("  no customers - skipping")
            return 0
        customer_id = cust_resp["data"][0]["id"]
        veh_resp = c.get(
            "/v3/vehicle",
            params=loc({"where": json.dumps({"customerId": customer_id}), "limit": "1"}),
        ).json()
        if not veh_resp.get("data"):
            print("  no vehicle on test customer - skipping")
            return 0
        vehicle_id = veh_resp["data"][0]["id"]

        # Pick a real tech id
        user_resp = c.get(
            "/v3/user",
            params=loc(
                {"where": json.dumps({"assignedTechnician": True}), "limit": "1"}
            ),
        ).json()
        if not user_resp.get("data"):
            print("  no tech users - skipping")
            return 0
        tech_id = user_resp["data"][0]["id"]
        tech_name = user_resp["data"][0].get("firstName")
        print(f"  Using tech_id={tech_id} ({tech_name})")

        # Create a far-future appointment so it doesn't appear on real calendars
        body = {
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "startDate": "2027-06-15T22:00:00.000Z",
            "endDate": "2027-06-15T23:00:00.000Z",
            "color": "blue",
            "name": "CLAUDE PROBE - DELETE ME (technicianId round-trip test)",
            "technicianId": tech_id,
            # Try several field names defensively in one POST
            "userId": tech_id,
            "userIds": [tech_id],
            "technicianIds": [tech_id],
            "assignedToUserId": tech_id,
        }
        if LOCATION_ID:
            body["locationId"] = LOCATION_ID
        r = c.post("/v3/appointment", json=body)
        print(f"  POST status: {r.status_code}")
        if r.status_code in (200, 201):
            created = r.json().get("data", {})
            created_id = created.get("id")
            print(f"  Created appointment id={created_id}")
            print(f"  POST response data keys: {sorted(created.keys())}")
            # Print any tech-ish key on the response
            for k in created:
                if any(t in k.lower() for t in ("user", "tech", "assign")):
                    print(f"    {k}: {created[k]}")

            # Now GET it back
            get_resp = c.get(f"/v3/appointment/{created_id}").json()
            got = get_resp.get("data") or {}
            print(f"\n  GET response data keys: {sorted(got.keys())}")
            for k in got:
                if any(t in k.lower() for t in ("user", "tech", "assign")):
                    print(f"    {k}: {got[k]}")

            # Now look in meta or any extra field
            for k, v in got.items():
                if isinstance(v, str) and (tech_id in v or (tech_name and tech_name in v)):
                    print(f"  TECH ID/NAME APPEARS in field {k}: {v[:100]}")
                elif isinstance(v, (dict, list)) and (
                    tech_id in str(v) or (tech_name and tech_name in str(v))
                ):
                    print(f"  TECH ID/NAME APPEARS in field {k}: {str(v)[:200]}")

            # Try the labors path - maybe POSTing technicianId on the
            # appointment creates a labor automatically
            order_id = got.get("orderId")
            if order_id:
                try:
                    svc_resp = c.get(f"/v3/order/{order_id}/service")
                    svcs = svc_resp.json().get("data", [])
                    print(f"  Order has {len(svcs)} services")
                    for svc in svcs:
                        for labor in svc.get("labors") or []:
                            print(f"    labor.technicianId={labor.get('technicianId')}")
                except Exception:
                    pass

            # Clean up
            del_r = c.delete(f"/v3/appointment/{created_id}", json={})
            print(f"  Cleanup delete: HTTP {del_r.status_code}")
        else:
            print(f"  POST failed: {r.text[:300]}")

        # -----------------------------------------------------------------
        # 5. Webhook / event subscription endpoints
        # -----------------------------------------------------------------
        header("5. Webhooks - does Shopmonkey publish appointment.tech changes?")
        for path in ("/v3/webhook", "/v3/event", "/v3/event_subscription", "/v3/webhook_subscription"):
            r = c.get(path, params=loc({"limit": "1"}))
            print(f"  {path}: HTTP {r.status_code}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
