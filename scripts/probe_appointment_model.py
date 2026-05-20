"""Discover the real Shopmonkey appointment data model.

Critical questions:
1. Where is the tech assignment stored? (sub-resource? users array? appointment.userIds?)
2. Do `where` operators ($gte/$lt) work? If not, what filters DO work?
3. How do we paginate past 100 rows? (skip, page, offset?)
4. What does an OOTB-scheduler-created appointment actually look like?
5. What does the Anne/Maria test booking look like (we created those)?
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


def get(c: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
    r = c.get(path, params=params or {})
    r.raise_for_status()
    return r.json()


def header(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # -----------------------------------------------------------------
        # 1. Get one appointment in full detail
        # -----------------------------------------------------------------
        header("1. Single appointment full detail (get one with orderId set)")
        any_appt = get(c, "/v3/appointment", params=loc()).get("data", [])
        with_order = next((a for a in any_appt if a.get("orderId")), None)
        if not with_order:
            with_order = any_appt[0]
        appt_id = with_order["id"]
        print(f"   appt id={appt_id} name={with_order.get('name')!r}")
        detail = get(c, f"/v3/appointment/{appt_id}").get("data", {})
        print(f"\n   Top-level keys: {sorted(detail.keys())}")
        for k in sorted(detail.keys()):
            v = detail[k]
            if isinstance(v, list):
                print(f"     {k}: list[{len(v)}]")
                if v and len(v) <= 5:
                    for item in v:
                        print(f"        - {json.dumps(item, default=str)[:120]}")
            elif isinstance(v, dict):
                print(f"     {k}: dict({list(v.keys())})")
            else:
                vs = str(v)
                if len(vs) > 60:
                    vs = vs[:60] + "..."
                print(f"     {k}: {vs}")

        # -----------------------------------------------------------------
        # 2. Try different where filter syntaxes
        # -----------------------------------------------------------------
        header("2. Where filter syntax experiments")

        # Bracket URL notation
        print("\n   2a. URL bracket notation: ?startDate[$gte]=2026-05-27T00:00:00Z")
        try:
            r = c.get(
                "/v3/appointment",
                params={
                    "startDate[$gte]": "2026-05-27T00:00:00Z",
                    "startDate[$lt]": "2026-05-28T00:00:00Z",
                    **({"locationId": LOCATION_ID} if LOCATION_ID else {}),
                },
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            in_range = sum(1 for a in data if (a.get("startDate") or "").startswith("2026-05-27"))
            print(f"      Got {len(data)} rows; {in_range} on 2026-05-27")
        except Exception as e:
            print(f"      error: {e}")

        # Try the from/to convention
        print("\n   2b. from/to params: ?from=2026-05-27&to=2026-05-28")
        try:
            r = c.get("/v3/appointment", params=loc({"from": "2026-05-27", "to": "2026-05-28"}))
            r.raise_for_status()
            data = r.json().get("data", [])
            in_range = sum(1 for a in data if (a.get("startDate") or "").startswith("2026-05-27"))
            print(f"      Got {len(data)} rows; {in_range} on 2026-05-27")
        except Exception as e:
            print(f"      error: {e}")

        # Try MongoDB syntax in URL query parameter style
        print("\n   2c. Underscore convention: ?startDate_gte=...")
        try:
            r = c.get(
                "/v3/appointment",
                params=loc(
                    {
                        "startDate_gte": "2026-05-27T00:00:00Z",
                        "startDate_lt": "2026-05-28T00:00:00Z",
                    }
                ),
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            in_range = sum(1 for a in data if (a.get("startDate") or "").startswith("2026-05-27"))
            print(f"      Got {len(data)} rows; {in_range} on 2026-05-27")
        except Exception as e:
            print(f"      error: {e}")

        # Try with sort, maybe sorted-asc-by-startDate skips back to old data, sorted-desc-by-startDate gives newest
        print("\n   2d. Sort by startDate desc (we may be getting some other order)")
        try:
            data = get(
                c,
                "/v3/appointment",
                params=loc({"sort": json.dumps([{"startDate": "desc"}]), "limit": "100"}),
            ).get("data", [])
            print(f"      Got {len(data)} rows; first 5 startDates:")
            for a in data[:5]:
                print(f"        - {a.get('startDate')} {a.get('name', '')[:50]}")
            last_5 = data[-5:]
            print("      Last 5 startDates:")
            for a in last_5:
                print(f"        - {a.get('startDate')} {a.get('name', '')[:50]}")
        except Exception as e:
            print(f"      error: {e}")

        # Sort by startDate asc to see if range narrows
        print("\n   2e. Sort by startDate asc + where (does sort interact with filter?)")
        try:
            data = get(
                c,
                "/v3/appointment",
                params=loc(
                    {
                        "where": json.dumps({"startDate": {"$gte": "2026-05-20T00:00:00Z"}}),
                        "sort": json.dumps([{"startDate": "asc"}]),
                        "limit": "100",
                    }
                ),
            ).get("data", [])
            print(f"      Got {len(data)} rows; first 5 startDates:")
            for a in data[:5]:
                print(f"        - {a.get('startDate')} {a.get('name', '')[:50]}")
            print("      Last 5 startDates:")
            for a in data[-5:]:
                print(f"        - {a.get('startDate')} {a.get('name', '')[:50]}")
        except Exception as e:
            print(f"      error: {e}")

        # -----------------------------------------------------------------
        # 3. Pagination
        # -----------------------------------------------------------------
        header("3. Pagination - try skip / page / cursor")

        # skip
        print("\n   3a. ?skip=100")
        try:
            page1 = get(c, "/v3/appointment", params=loc({"limit": "100"})).get("data", [])
            page2 = get(c, "/v3/appointment", params=loc({"limit": "100", "skip": "100"})).get(
                "data", []
            )
            ids1 = {a["id"] for a in page1}
            ids2 = {a["id"] for a in page2}
            print(
                f"      page1: {len(page1)} rows, page2: {len(page2)} rows, "
                f"overlap: {len(ids1 & ids2)}"
            )
        except Exception as e:
            print(f"      error: {e}")

        # page (1-indexed?)
        print("\n   3b. ?page=2")
        try:
            page1 = get(c, "/v3/appointment", params=loc({"limit": "100", "page": "1"})).get(
                "data", []
            )
            page2 = get(c, "/v3/appointment", params=loc({"limit": "100", "page": "2"})).get(
                "data", []
            )
            ids1 = {a["id"] for a in page1}
            ids2 = {a["id"] for a in page2}
            print(
                f"      page1: {len(page1)} rows, page2: {len(page2)} rows, "
                f"overlap: {len(ids1 & ids2)}"
            )
        except Exception as e:
            print(f"      error: {e}")

        # offset
        print("\n   3c. ?offset=100")
        try:
            page1 = get(c, "/v3/appointment", params=loc({"limit": "100"})).get("data", [])
            page2 = get(c, "/v3/appointment", params=loc({"limit": "100", "offset": "100"})).get(
                "data", []
            )
            ids1 = {a["id"] for a in page1}
            ids2 = {a["id"] for a in page2}
            print(
                f"      page1: {len(page1)} rows, page2: {len(page2)} rows, "
                f"overlap: {len(ids1 & ids2)}"
            )
        except Exception as e:
            print(f"      error: {e}")

        # Look at meta in response
        print("\n   3d. Response meta (look for nextCursor, totalCount, hasMore)")
        try:
            r = get(c, "/v3/appointment", params=loc({"limit": "100"}))
            top = {k: v for k, v in r.items() if k != "data"}
            print(f"      Top-level keys (excl data): {list(top.keys())}")
            for k, v in top.items():
                if isinstance(v, (dict, list)):
                    print(f"        {k}: {json.dumps(v)[:200]}")
                else:
                    print(f"        {k}: {v}")
        except Exception as e:
            print(f"      error: {e}")

        # -----------------------------------------------------------------
        # 4. Look for appointments Anne/Maria booked on May 27
        # -----------------------------------------------------------------
        header("4. Hunt for Anne W. and Maria S. test appointments on May 27, 2026")
        # Pull as many as we can (paginate if possible)
        all_appts: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for skip in (0, 100, 200, 300, 400, 500, 1000, 2000, 5000):
            page = get(
                c,
                "/v3/appointment",
                params=loc(
                    {
                        "limit": "100",
                        "skip": str(skip),
                        "sort": json.dumps([{"createdDate": "desc"}]),
                    }
                ),
            ).get("data", [])
            new_ones = [a for a in page if a["id"] not in seen_ids]
            for a in new_ones:
                seen_ids.add(a["id"])
                all_appts.append(a)
            if not new_ones:
                print(f"   skip={skip} returned no new rows; stopping")
                break
            print(f"   skip={skip}: {len(page)} rows ({len(new_ones)} new)")
        print(f"\n   Total unique appointments retrieved: {len(all_appts)}")

        # Filter for May 27 2026 in any TZ
        may27 = [a for a in all_appts if (a.get("startDate") or "").startswith("2026-05-27")]
        print(f"   On 2026-05-27 (UTC): {len(may27)}")
        for a in may27:
            print(
                f"     - {a.get('startDate')} - {a.get('endDate')} name={a.get('name', '')[:60]!r}"
            )

        # Find ones with "Anne" or "Maria"
        named = [
            a
            for a in all_appts
            if "anne w" in (a.get("name") or "").lower()
            or "maria s" in (a.get("name") or "").lower()
        ]
        print(f"\n   Appointments with 'Anne W' or 'Maria S': {len(named)}")
        for a in named[:10]:
            print(
                f"     - id={a.get('id', '')[:8]} startDate={a.get('startDate')} name={a.get('name', '')[:60]!r}"
            )

        # If we found one, look at its detail to see tech assignment field
        if named:
            test_id = named[0]["id"]
            detail = get(c, f"/v3/appointment/{test_id}").get("data", {})
            print(f"\n   Anne/Maria test appointment {test_id[:8]} detail:")
            print(f"     keys: {sorted(detail.keys())}")
            for k in (
                "userIds",
                "users",
                "technicians",
                "technicianIds",
                "assignedTo",
                "assignedUserId",
            ):
                if k in detail:
                    print(f"     {k}: {detail[k]!r}")

        # Look at an OOTB-style appointment for tech fields
        print("\n   Checking OOTB appointments (orderId set, '/' separator) for tech fields:")
        ootb = [
            a
            for a in all_appts
            if a.get("orderId") and "/" in (a.get("name") or "") and " / " in (a.get("name") or "")
        ]
        if ootb:
            ootb_detail = get(c, f"/v3/appointment/{ootb[0]['id']}").get("data", {})
            print(f"     id={ootb[0]['id'][:8]} keys: {sorted(ootb_detail.keys())}")
            for k in (
                "userIds",
                "users",
                "technicians",
                "technicianIds",
                "assignedTo",
                "assignedUserId",
                "userId",
                "technicianId",
            ):
                if k in ootb_detail:
                    v = ootb_detail[k]
                    if isinstance(v, list) and v:
                        print(f"     {k}: list[{len(v)}] e.g. {v[0]}")
                    else:
                        print(f"     {k}: {v!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
