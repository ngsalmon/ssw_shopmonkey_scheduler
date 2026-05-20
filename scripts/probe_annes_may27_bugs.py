"""Diagnose the three bugs Anne reported on 2026-05-20 testing.

1. Double-booking: examines all appointments on 2026-05-27 to see whether our
   /availability check would have detected Anne's first booking when Maria
   subsequently booked. Also probes the default page size for /v3/appointment.

2. No parts / wrong total: examines the canned service "Window Tint - Full
   Sedan/Truck - Ceramic" to see what sub-resources (labors, parts, fees,
   discount) exist, and looks at one of the tickets the scheduler created
   (#7414 / #7415) to see what was actually attached vs what's missing.

3. Compares with an OOTB-created order (or the Shopmonkey native online
   booking flow) to see what fields/endpoints we should be using.

Read-only. Print everything we need to make the fix decision.
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


def loc(params: dict[str, Any] | None = None) -> dict[str, Any]:
    p = dict(params or {})
    if LOCATION_ID:
        p["locationId"] = LOCATION_ID
    return p


def get(c: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = c.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def trunc(s: Any, n: int = 80) -> str:
    text = str(s) if s is not None else ""
    return text if len(text) <= n else text[: n - 1] + "…"


def header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # -----------------------------------------------------------------
        # Theory 1: page-size default on /v3/appointment GETs
        # -----------------------------------------------------------------
        header("1A. Test page size: GET /v3/appointment without limit (default page)")
        no_limit = get(c, "/v3/appointment", params=loc()).get("data", [])
        print(f"  default page size returned {len(no_limit)} rows")

        with_limit_200 = get(c, "/v3/appointment", params=loc({"limit": "200"})).get("data", [])
        print(f"  limit=200 returned {len(with_limit_200)} rows")

        with_limit_500 = get(c, "/v3/appointment", params=loc({"limit": "500"})).get("data", [])
        print(f"  limit=500 returned {len(with_limit_500)} rows")

        # -----------------------------------------------------------------
        # Theory 1: appointments on May 27, 2026
        # -----------------------------------------------------------------
        header("1B. All appointments on 2026-05-27 (UTC range), look for Anne & Maria")
        where = {
            "startDate": {
                "$gte": "2026-05-27T00:00:00Z",
                "$lt": "2026-05-27T23:59:59Z",
            }
        }
        params = loc({"where": json.dumps(where), "limit": "500"})
        may27 = get(c, "/v3/appointment", params=params).get("data", [])
        print(f"  Found {len(may27)} appointments on 2026-05-27")
        for a in may27:
            print(
                f"    - id={a.get('id', '')[:8]} "
                f"name={trunc(a.get('name'), 50)!r} "
                f"start={a.get('startDate')} "
                f"end={a.get('endDate')} "
                f"techId={(a.get('technicianId') or '')[:8] or '<none>'} "
                f"userId={(a.get('userId') or '')[:8] or '<none>'} "
                f"orderId={(a.get('orderId') or '')[:8] or '<none>'}"
            )

        # Now repeat the SAME query but WITHOUT a limit param (production
        # behavior). If this returns fewer rows than 500-limit, that's the bug.
        no_limit_may27 = get(
            c,
            "/v3/appointment",
            params=loc({"where": json.dumps(where)}),
        ).get("data", [])
        print(
            f"\n  Same query WITHOUT limit param: {len(no_limit_may27)} rows "
            f"(vs {len(may27)} with limit=500)"
        )

        # -----------------------------------------------------------------
        # Theory 3: inspect canned service for Window Tint Ceramic
        # -----------------------------------------------------------------
        header("3A. Find canned service 'Window Tint - Full Sedan/Truck - Ceramic'")
        all_canned = get(c, "/v3/canned_service", params=loc({"limit": "500"})).get("data", [])
        print(f"  Total canned services: {len(all_canned)}")
        wt_ceramic = next(
            (
                s
                for s in all_canned
                if "ceramic" in (s.get("name") or "").lower()
                and "full sedan" in (s.get("name") or "").lower()
            ),
            None,
        )
        if not wt_ceramic:
            # Fallback: any ceramic Window Tint
            wt_ceramic = next(
                (
                    s
                    for s in all_canned
                    if "ceramic" in (s.get("name") or "").lower()
                    and "window tint" in (s.get("name") or "").lower()
                ),
                None,
            )
        if not wt_ceramic:
            print("  Could not find Window Tint Ceramic canned service.")
            return 1
        cs_id = wt_ceramic["id"]
        print(f"  id={cs_id} name={wt_ceramic.get('name')!r}")

        # Fetch the canned service detail (sub-resources)
        detail = get(c, f"/v3/canned_service/{cs_id}").get("data") or {}
        print("\n  Detail field set (top-level keys):")
        for k in sorted(detail.keys()):
            v = detail[k]
            if isinstance(v, list):
                print(f"    {k}: list[{len(v)}]")
            elif isinstance(v, dict):
                print(f"    {k}: dict({len(v)} keys)")
            elif isinstance(v, str) and len(v) > 60:
                print(f"    {k}: '{v[:60]}…'")
            else:
                print(f"    {k}: {v!r}")

        print("\n  Labors:")
        for labor in detail.get("labors") or []:
            print(f"    {json.dumps(labor, default=str)}")

        print("\n  Parts (full structure for first 3):")
        for part in (detail.get("parts") or [])[:3]:
            print(f"    {json.dumps(part, default=str)}")
        if len(detail.get("parts") or []) > 3:
            print(f"    ... and {len(detail['parts']) - 3} more parts")

        print("\n  Fees:")
        for fee in detail.get("fees") or []:
            print(f"    {json.dumps(fee, default=str)}")

        print("\n  Discount-related fields:")
        for k in (
            "discountCents",
            "discountPercent",
            "shopSuppliesPercent",
            "epaPercent",
            "totalCents",
            "priceCents",
            "epaCents",
            "calculatedLaborCents",
            "calculatedPartsCents",
            "calculatedTotalCents",
            "calculatedDiscountCents",
            "taxable",
            "epaTaxable",
            "isTaxable",
        ):
            if k in detail:
                print(f"    {k}: {detail[k]!r}")

        # -----------------------------------------------------------------
        # Theory 3: inspect our created tickets #7414, #7415
        # -----------------------------------------------------------------
        header("3B. Look at our recently created orders (#7414, #7415)")
        for num in ("7414", "7415"):
            print(f"\n  Looking for order number {num}")
            order_search = get(
                c,
                "/v3/order",
                params=loc({"where": json.dumps({"number": int(num)})}),
            ).get("data", [])
            if not order_search:
                # number might be string
                order_search = get(
                    c,
                    "/v3/order",
                    params=loc({"where": json.dumps({"number": num})}),
                ).get("data", [])
            if not order_search:
                print(f"    Order #{num} not found via where filter; fetching recent orders")
                recent = get(
                    c,
                    "/v3/order",
                    params=loc({"sort": json.dumps([{"createdDate": "desc"}]), "limit": "20"}),
                ).get("data", [])
                order_search = [o for o in recent if str(o.get("number")) == num]
            if not order_search:
                print(f"    Could not locate order #{num}")
                continue
            order = order_search[0]
            order_id = order["id"]
            print(
                f"    id={order_id} number={order.get('number')} name={trunc(order.get('name'), 50)}"
            )
            print(
                f"    grandTotal={order.get('grandTotal')} "
                f"totalCost={order.get('totalCost')} "
                f"calculatedLaborCents={order.get('calculatedLaborCents')} "
                f"calculatedPartsCents={order.get('calculatedPartsCents')}"
            )

            # Get the services attached to this order
            try:
                services = get(c, f"/v3/order/{order_id}/service").get("data", [])
            except httpx.HTTPStatusError as e:
                print(f"    Cannot fetch order services: {e.response.status_code}")
                services = []
            print(f"    Services attached: {len(services)}")
            for s in services:
                print(f"      service id={s.get('id', '')[:8]} name={trunc(s.get('name'), 50)}")
                print(
                    f"        cannedServiceId={(s.get('cannedServiceId') or '')[:8]} "
                    f"calculatedLaborCents={s.get('calculatedLaborCents')} "
                    f"calculatedPartsCents={s.get('calculatedPartsCents')}"
                )
                labors_count = len(s.get("labors") or [])
                parts_count = len(s.get("parts") or [])
                fees_count = len(s.get("fees") or [])
                print(f"        labors={labors_count} parts={parts_count} fees={fees_count}")

        # -----------------------------------------------------------------
        # Theory 3: find a recent OOTB-created order (not via our scheduler)
        # to see what they attach.
        # -----------------------------------------------------------------
        header("3C. Find an OOTB-created order with same canned service for comparison")
        # OOTB orders have name like "Customer N. / Year Make Model / Service"
        # in the Scheduled workflow column. Get recent orders attached to this
        # canned service.
        # We'll look at recent orders that contain a service with cannedServiceId == cs_id.
        recent_orders = get(
            c,
            "/v3/order",
            params=loc({"sort": json.dumps([{"createdDate": "desc"}]), "limit": "50"}),
        ).get("data", [])
        ootb_candidate = None
        for o in recent_orders:
            try:
                svcs = get(c, f"/v3/order/{o['id']}/service").get("data", [])
            except httpx.HTTPStatusError:
                continue
            if any(s.get("cannedServiceId") == cs_id for s in svcs):
                ootb_candidate = (o, svcs)
                if (o.get("name") or "").count("/") >= 2 and o.get("id") not in (None,):
                    break
        if ootb_candidate:
            o, svcs = ootb_candidate
            print(
                f"  Found order id={o['id'][:8]} number={o.get('number')} "
                f"name={trunc(o.get('name'), 60)}"
            )
            print(
                f"    grandTotal={o.get('grandTotal')} "
                f"calculatedLaborCents={o.get('calculatedLaborCents')} "
                f"calculatedPartsCents={o.get('calculatedPartsCents')}"
            )
            for s in svcs:
                if s.get("cannedServiceId") != cs_id:
                    continue
                print(f"  Service attached: name={trunc(s.get('name'), 60)}")
                print(
                    f"    calculatedLaborCents={s.get('calculatedLaborCents')} "
                    f"calculatedPartsCents={s.get('calculatedPartsCents')} "
                    f"calculatedTotalCents={s.get('calculatedTotalCents')}"
                )
                print(f"    Service field keys: {sorted(s.keys())}")
                labors = s.get("labors") or []
                parts = s.get("parts") or []
                fees = s.get("fees") or []
                print(f"    labors={len(labors)} parts={len(parts)} fees={len(fees)}")
                for labor in labors:
                    print(f"      labor: {json.dumps(labor, default=str)}")
                for part in parts[:5]:
                    print(f"      part: {json.dumps(part, default=str)}")
                if len(parts) > 5:
                    print(f"      ... and {len(parts) - 5} more parts")
        else:
            print("  Did not find an existing order with this canned service for comparison.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
