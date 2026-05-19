"""Read-only probe of the Shopmonkey API to learn how the OOTB scheduler
attaches a ticket / order to an appointment.

Uses the credentials in .env. Does not create, modify, or delete anything.
Prints sanitized fields so PII isn't dumped to stdout.
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


def redact(obj: Any, keep_keys: set[str] | None = None) -> Any:
    """Recursively redact strings inside obj, optionally keeping a few keys verbatim."""
    keep_keys = keep_keys or set()
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for k, v in obj.items():
            if k in keep_keys or isinstance(v, (bool, type(None))):
                result[k] = v
            elif k in {
                "id",
                "createdDate",
                "updatedDate",
                "companyId",
                "locationId",
                "orderId",
                "customerId",
                "vehicleId",
                "workflowStatusId",
                "workflowId",
                "appointmentId",
            }:
                result[k] = "<id>" if v else v
            elif isinstance(v, (dict, list)):
                result[k] = redact(v, keep_keys)
            elif isinstance(v, str) and len(v) > 0:
                result[k] = "<str>"
            else:
                result[k] = v
        return result
    if isinstance(obj, list):
        return [redact(item, keep_keys) for item in obj[:3]]  # limit list samples
    return obj


def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        r = c.get(path, params=params or {})
        r.raise_for_status()
        return r.json()


def main() -> int:
    # 1. Grab recent appointments (most recent 20).
    params: dict[str, Any] = {"limit": "20", "sort": json.dumps([{"createdDate": "desc"}])}
    if LOCATION_ID:
        params["locationId"] = LOCATION_ID
    appts = get("/v3/appointment", params=params).get("data", [])
    print(f"Fetched {len(appts)} recent appointments\n")

    if not appts:
        print("No appointments to inspect.")
        return 0

    # Show the field shape of the first appointment.
    print("== Appointment field set (redacted) ==")
    print(json.dumps(redact(appts[0], keep_keys={"name", "color", "allDay"}), indent=2))
    print()

    # Find one with orderId set (an OOTB-style appointment with a linked RO).
    appt_with_order = next((a for a in appts if a.get("orderId")), None)
    if not appt_with_order:
        print("No recent appointment has an orderId. Sample appointments WITHOUT order:")
        for a in appts[:5]:
            print(
                f"  - name={a.get('name', '')[:30]} startDate={a.get('startDate')} "
                f"orderId={a.get('orderId')}"
            )
        return 0

    print(f"Found appointment with orderId={appt_with_order['orderId'][:8]}...")
    # Try listing orders matching the id, since /v3/order/<id> 404s
    order_params: dict[str, Any] = {"where": json.dumps({"id": appt_with_order["orderId"]})}
    if LOCATION_ID:
        order_params["locationId"] = LOCATION_ID
    order_list = get("/v3/order", params=order_params).get("data", [])
    if not order_list:
        # Try a few other endpoint variants
        for endpoint in ("/v3/repair_order", "/v3/work_order", "/v3/ticket"):
            try:
                order_list = get(endpoint, params=order_params).get("data", [])
                if order_list:
                    print(f"Found order via {endpoint}")
                    break
            except httpx.HTTPStatusError as e:
                print(f"  {endpoint}: {e.response.status_code}")
        if not order_list:
            # Last resort: list recent orders for shape
            print("\nListing 5 most recent orders for shape inspection...")
            order_list = get("/v3/order", params={**order_params, "where": json.dumps({})}).get(
                "data", []
            )[:5]
    if not order_list:
        print("Could not locate order. Aborting.")
        return 1
    order = order_list[0]

    print("== Order field set (redacted) ==")
    print(
        json.dumps(
            redact(
                order,
                keep_keys={
                    "name",
                    "number",
                    "status",
                    "type",
                    "workflowStatusName",
                    "isInvoice",
                    "isEstimate",
                    "isWorkOrder",
                    "isReturn",
                    "isWarranty",
                    "isVoided",
                    "isCounterSale",
                    "isReturnInvoice",
                    "color",
                    "scheduledStartDate",
                    "scheduledEndDate",
                    "completedDate",
                    "shopRate",
                    "totalCost",
                    "grandTotal",
                },
            ),
            indent=2,
        )
    )

    # Print the small set of fields we actually need for creation parity.
    print("\n== Key creation-relevant fields (redacted) ==")
    for key in (
        "id",
        "name",
        "number",
        "status",
        "color",
        "customerId",
        "vehicleId",
        "appointmentId",
        "workflowStatusId",
        "scheduledStartDate",
        "scheduledEndDate",
        "repairOrderDate",
        "completedDate",
        "type",
    ):
        if key in order:
            v = order[key]
            if isinstance(v, str) and len(v) > 60:
                v = v[:60] + "..."
            print(f"  {key}: {v!r}")

    # Workflow status columns
    print("\n== Workflow statuses (likely candidates for OOTB starting column) ==")
    ws_params: dict[str, Any] = {}
    if LOCATION_ID:
        ws_params["locationId"] = LOCATION_ID
    try:
        statuses = get("/v3/workflow_status", params=ws_params).get("data", [])
        for ws in statuses:
            print(
                f"  - id={ws.get('id', '')[:8]}  name={ws.get('name')!r:30}  "
                f"sortPosition={ws.get('sortPosition')}  archived={ws.get('archived')}"
            )
    except httpx.HTTPStatusError as e:
        print(f"  could not list workflow_status: {e}")

    # Find a recent order in the "Scheduled" column - that's the OOTB landing
    # zone for online bookings.
    scheduled = next((s for s in statuses if s.get("name") == "Scheduled"), None)
    if scheduled:
        print("\n== Recent order(s) in 'Scheduled' column ==")
        sched_params: dict[str, Any] = {
            "where": json.dumps({"workflowStatusId": scheduled["id"]}),
            "limit": "5",
            "sort": json.dumps([{"createdDate": "desc"}]),
        }
        if LOCATION_ID:
            sched_params["locationId"] = LOCATION_ID
        recent = get("/v3/order", params=sched_params).get("data", [])
        print(f"  {len(recent)} recent orders in Scheduled column")
        for o in recent[:3]:
            keep = {
                "id": (o.get("id") or "")[:8],
                "number": o.get("number"),
                "status": o.get("status"),
                "name": (o.get("name") or "")[:60],
                "appointmentId": (o.get("appointmentId") or "")[:8],
                "color": o.get("color"),
                "scheduledStartDate": o.get("scheduledStartDate"),
                "scheduledEndDate": o.get("scheduledEndDate"),
                "completedDate": o.get("completedDate"),
                "repairOrderDate": o.get("repairOrderDate"),
            }
            print(f"  - {json.dumps(keep, default=str)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
