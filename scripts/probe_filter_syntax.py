"""Find a working filter+pagination syntax for /v3/appointment.

We've confirmed the standard JSON $gte/$lt where filter is silently ignored.
Try the remaining likely variants, and also look at whether there's a
different appointment endpoint (calendar / events / schedule).
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["SHOPMONKEY_API_TOKEN"]
BASE_URL = os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud").rstrip("/")
LOCATION_ID = os.getenv("SHOPMONKEY_LOCATION_ID")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def loc(p=None):
    out = dict(p or {})
    if LOCATION_ID:
        out["locationId"] = LOCATION_ID
    return out


def try_endpoint(c, endpoint, params, label):
    try:
        r = c.get(endpoint, params=params)
        if r.status_code != 200:
            print(f"   {label}: HTTP {r.status_code}")
            return None
        data = r.json()
        rows = data.get("data") if isinstance(data, dict) else None
        meta = data.get("meta") if isinstance(data, dict) else None
        n = len(rows) if isinstance(rows, list) else "?"
        in_range = (
            sum(1 for a in (rows or []) if (a.get("startDate") or "").startswith("2026-05-27"))
            if isinstance(rows, list)
            else "?"
        )
        print(f"   {label}: {n} rows, {in_range} on 2026-05-27, meta={meta}")
        return rows
    except Exception as e:
        print(f"   {label}: error={e}")
        return None


def header(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        header("1. Alternative where operator syntaxes")
        # No $ prefix
        try_endpoint(
            c,
            "/v3/appointment",
            loc(
                {
                    "where": json.dumps(
                        {"startDate": {"gte": "2026-05-27T00:00:00Z", "lt": "2026-05-28T00:00:00Z"}}
                    )
                }
            ),
            "where (no $ prefix)",
        )
        # gt / lt (not gte)
        try_endpoint(
            c,
            "/v3/appointment",
            loc(
                {
                    "where": json.dumps(
                        {
                            "startDate": {
                                "$gt": "2026-05-26T23:59:59Z",
                                "$lt": "2026-05-28T00:00:00Z",
                            }
                        }
                    )
                }
            ),
            "where ($gt / $lt)",
        )
        # Exact match
        try_endpoint(
            c,
            "/v3/appointment",
            loc({"where": json.dumps({"startDate": "2026-05-27T18:00:00.000Z"})}),
            "where exact-match startDate",
        )
        # Test if customerId where works
        # First grab a customer id
        any_appt = c.get("/v3/appointment", params=loc({"limit": "1"})).json().get("data", [])
        if any_appt:
            cust = any_appt[0].get("customerId")
            print(f"\n   Using customerId={cust[:8] if cust else None} for filter tests")
            try_endpoint(
                c,
                "/v3/appointment",
                loc({"where": json.dumps({"customerId": cust})}),
                "where customerId (exact scalar)",
            )

        header("2. Alternative endpoints for scheduling data")
        for endpoint in (
            "/v3/calendar",
            "/v3/calendar_event",
            "/v3/scheduled_event",
            "/v3/appointment/search",
            "/v3/schedule",
            "/v3/booking",
            "/v3/online_booking",
        ):
            try_endpoint(c, endpoint, loc({"limit": "5"}), f"GET {endpoint}")

        header("3. Pagination: try cursor-based")
        # Get first page, look for nextCursor or similar
        r1 = c.get("/v3/appointment", params=loc({"limit": "100"}))
        body = r1.json() if r1.status_code == 200 else {}
        print(f"   First page meta: {body.get('meta')}")
        print(f"   Top-level keys: {list(body.keys())}")

        # Try cursor params on second request
        for cursor_field in ("cursor", "nextCursor", "after", "startingAfter", "pageToken"):
            data = body.get("data") or []
            if not data:
                continue
            last_id = data[-1]["id"]
            try_endpoint(
                c,
                "/v3/appointment",
                loc({"limit": "100", cursor_field: last_id}),
                f"{cursor_field}=<last_id>",
            )

        header("4. Higher limits")
        for lim in ("250", "500", "1000", "10000"):
            try_endpoint(c, "/v3/appointment", loc({"limit": lim}), f"limit={lim}")

        header("5. Try the 'count only' route to confirm total")
        try:
            r = c.get("/v3/appointment", params=loc({"limit": "1"}))
            print(f"   total per meta: {r.json().get('meta', {}).get('total')}")
        except Exception as e:
            print(f"   error: {e}")

        header("6. Try fetching by ID range (sequential probe)")
        # Maybe IDs are time-ordered uuids and we can find recent ones differently
        # Just sample
        recent = (
            c.get(
                "/v3/appointment",
                params=loc({"limit": "100", "sort": json.dumps([{"createdDate": "desc"}])}),
            )
            .json()
            .get("data", [])
        )
        print("   Most-recently-created appointments (top 10):")
        for a in recent[:10]:
            print(
                f"     created={a.get('createdDate')} start={a.get('startDate')} "
                f"name={(a.get('name') or '')[:50]!r}"
            )

        header("7. Try a date-tagged query that worked before (probe_annes_records)")
        # That probe got results for May 19-20 by fetching all and filtering by note.
        # Let's just confirm: fetch 100 most recent and look for May 27 starts.
        recent_500 = []
        seen = set()
        for skip in (0, 100, 200, 300, 400):
            page = (
                c.get(
                    "/v3/appointment",
                    params=loc(
                        {
                            "limit": "100",
                            "skip": str(skip),
                            "sort": json.dumps([{"createdDate": "desc"}]),
                        }
                    ),
                )
                .json()
                .get("data", [])
            )
            for a in page:
                if a["id"] not in seen:
                    seen.add(a["id"])
                    recent_500.append(a)
        may27 = [a for a in recent_500 if (a.get("startDate") or "").startswith("2026-05-27")]
        print(f"   In recent {len(recent_500)} dedupe'd, {len(may27)} are on 2026-05-27")
        for a in may27:
            print(
                f"     - id={a.get('id', '')[:8]} created={a.get('createdDate')} "
                f"start={a.get('startDate')} name={(a.get('name') or '')[:60]!r}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
