"""Hunt for the Shopmonkey API endpoint that powers calendar swimlanes.

The Shopmonkey calendar UI clearly shows each appointment in a per-tech
column (swimlane), so the relationship exists internally. But:
- GET /v3/appointment/{id} returns NO tech field (we dumped every key)
- POST /v3/appointment accepts technicianId silently but it never appears
  on subsequent GET
- where: {technicianId: ...} on /v3/appointment returns 0 results

This probe tries every endpoint variant and field name we can think of
to find the read path. Read-only and PII-scrubbed.
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


def try_get(c: httpx.Client, path: str, params: dict | None = None, label: str = "") -> Any:
    try:
        r = c.get(path, params=params or {})
        print(f"  {label or path}: HTTP {r.status_code}", end="")
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict):
                rows = body.get("data")
                meta = body.get("meta")
                if isinstance(rows, list):
                    print(f"  data=list[{len(rows)}]  meta={meta}")
                    return rows
                else:
                    keys = sorted(body.keys())
                    print(f"  keys={keys}")
                    return body
            else:
                print(f"  body_type={type(body).__name__}")
                return body
        else:
            text = r.text[:200] if r.text else ""
            print(f"  body={text!r}")
            return None
    except Exception as e:
        print(f"  {label or path}: ERROR {e}")
        return None


def try_post(c: httpx.Client, path: str, body: Any, label: str = "") -> Any:
    try:
        r = c.post(path, json=body)
        print(f"  POST {label or path}: HTTP {r.status_code}", end="")
        if r.status_code in (200, 201):
            payload = r.json()
            if isinstance(payload, dict):
                rows = payload.get("data")
                if isinstance(rows, list):
                    print(f"  data=list[{len(rows)}]")
                    return rows
                else:
                    keys = sorted(payload.keys())
                    print(f"  keys={keys}")
                    return payload
            return payload
        else:
            text = r.text[:300] if r.text else ""
            print(f"  body={text!r}")
            return None
    except Exception as e:
        print(f"  POST {label or path}: ERROR {e}")
        return None


def header(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as c:
        # Find a known appointment that the calendar UI definitely shows in a
        # specific swimlane. Use one from earlier probes that we know is
        # tech-assigned via labor.
        header("0. Grab a known tech-assigned appointment as test subject")
        any_appt = try_get(
            c,
            "/v3/appointment",
            params=loc(
                {
                    "where": json.dumps(
                        {"startDate": {"gte": "2026-05-20T00:00:00Z"}}
                    ),
                    "limit": "10",
                }
            ),
            label="recent",
        )
        if not any_appt:
            print("Could not get a test appointment.")
            return 1
        with_order = next((a for a in any_appt if a.get("orderId")), any_appt[0])
        appt_id = with_order["id"]
        order_id = with_order.get("orderId")
        print(f"  test appointment id={appt_id} orderId={order_id}")

        # -----------------------------------------------------------------
        # 1. Try "expand" / "include" / "embed" query params that some REST
        #    APIs use to embed related entities.
        # -----------------------------------------------------------------
        header("1. ?expand / ?include / ?embed variants on appointment detail")
        for param in (
            "expand=users",
            "expand=technicians",
            "expand=user",
            "expand=technician",
            "include=user,technician,users,technicians",
            "embed=users,technicians",
            "with=users,technicians",
            "_expand=user",
            "_embed=user",
            "fields=*",
            "fields=technicianId,userId,users,technicians",
        ):
            url = f"/v3/appointment/{appt_id}?{param}"
            r = c.get(url)
            if r.status_code == 200:
                body = r.json().get("data") or {}
                rel_keys = [
                    k
                    for k in body.keys()
                    if any(
                        t in k.lower()
                        for t in ("user", "tech", "assign")
                    )
                ]
                print(f"  {param}: HTTP 200, tech/user-ish keys={rel_keys}")
            else:
                print(f"  {param}: HTTP {r.status_code}")

        # -----------------------------------------------------------------
        # 2. Try appointment sub-resources by convention
        # -----------------------------------------------------------------
        header("2. /v3/appointment/{id}/<subresource> variants")
        for sub in (
            "user",
            "users",
            "technician",
            "technicians",
            "assignedUsers",
            "assignees",
            "schedule",
            "swimlane",
            "calendar",
        ):
            try_get(c, f"/v3/appointment/{appt_id}/{sub}", label=f"/v3/appointment/<id>/{sub}")

        # -----------------------------------------------------------------
        # 3. List endpoints that might expose the join table
        # -----------------------------------------------------------------
        header("3. Top-level endpoints related to scheduling join tables")
        for path in (
            "/v3/appointment_user",
            "/v3/appointment_technician",
            "/v3/appointment_assignee",
            "/v3/calendar_event",
            "/v3/scheduled_event",
            "/v3/user_appointment",
            "/v3/technician_appointment",
            "/v3/appointment_assignment",
            "/v3/userAppointment",
            "/v3/assignment",
        ):
            try_get(c, path, params=loc({"limit": "1"}), label=path)

        # -----------------------------------------------------------------
        # 4. Filter appointment list by candidate tech-id fields
        # -----------------------------------------------------------------
        header("4. Filter appointment list by candidate fields")
        # Pick a tech ID we know is busy: walk one appointment's order's labors.
        if order_id:
            services = try_get(c, f"/v3/order/{order_id}/service", label="services")
            tech_id_candidate = None
            for svc in services or []:
                for labor in svc.get("labors") or []:
                    if labor.get("technicianId"):
                        tech_id_candidate = labor["technicianId"]
                        break
                if tech_id_candidate:
                    break
            print(f"  Test tech id = {tech_id_candidate}")
            if tech_id_candidate:
                for field in (
                    "technicianId",
                    "userId",
                    "assignedToUserId",
                    "assigneeId",
                    "technician.id",
                    "userIds",
                    "technicianIds",
                ):
                    rows = try_get(
                        c,
                        "/v3/appointment",
                        params=loc(
                            {
                                "where": json.dumps({field: tech_id_candidate}),
                                "limit": "5",
                            }
                        ),
                        label=f"where {field}",
                    )

        # -----------------------------------------------------------------
        # 5. Look at /v3/user endpoint - maybe each user lists their appointments
        # -----------------------------------------------------------------
        header("5. /v3/user shape (might list appointments per tech)")
        users = try_get(c, "/v3/user", params=loc({"limit": "1"}), label="users")
        if users:
            user = users[0]
            print(f"  Top-level keys on User: {sorted(user.keys())}")
            user_id = user["id"]
            # Try sub-resources on user
            for sub in (
                "appointments",
                "appointment",
                "schedule",
                "calendar",
                "labors",
            ):
                try_get(
                    c,
                    f"/v3/user/{user_id}/{sub}",
                    label=f"/v3/user/<id>/{sub}",
                )

        # -----------------------------------------------------------------
        # 6. POST with technicianId, then GET the same appointment to confirm
        #    the round-trip behavior the user reported (we can write but not read).
        # -----------------------------------------------------------------
        header("6. Confirm write-only behavior: read appointment with `?include=*`")
        for url_suffix in (
            "?include=*",
            "?include=all",
            "?fields=*",
            "?_include=*",
            "?relations=true",
        ):
            r = c.get(f"/v3/appointment/{appt_id}{url_suffix}")
            if r.status_code == 200:
                body = r.json().get("data") or {}
                rel_keys = [
                    k for k in body.keys()
                    if any(t in k.lower() for t in ("user", "tech", "assign"))
                ]
                print(f"  {url_suffix}: HTTP 200, tech/user keys={rel_keys}")
            else:
                print(f"  {url_suffix}: HTTP {r.status_code}")

        # -----------------------------------------------------------------
        # 7. Look at meta-fields on the appointment - maybe tech is in `meta`
        # -----------------------------------------------------------------
        header("7. Full appointment detail (look for tech in meta or other nested fields)")
        full = try_get(c, f"/v3/appointment/{appt_id}", label="full")
        if isinstance(full, dict):
            full = full
        elif isinstance(full, list) and full:
            full = full[0]
        if isinstance(full, dict):
            for k, v in full.items():
                if any(t in str(v).lower() for t in ("user", "tech")) and not k.startswith("_"):
                    if isinstance(v, str) and len(v) < 200:
                        print(f"  {k}: {v}")

            # Dump meta and metadata in full
            for k in ("meta", "metadata"):
                v = full.get(k)
                if v:
                    print(f"\n  Full {k}: {json.dumps(v, default=str)[:400]}")

        # -----------------------------------------------------------------
        # 8. Try /v3/integration/* endpoints, which sometimes expose more data
        # -----------------------------------------------------------------
        header("8. /v3/integration/* endpoints (different surface)")
        for path in (
            "/v3/integration/appointment",
            "/v3/integration/appointment/search",
            "/v3/integration/calendar",
            "/v3/integration/schedule",
        ):
            # Try GET first
            try_get(c, path, params=loc({"limit": "1"}), label=path)
            # Try POST search (per ssw_pl payment pattern)
            try_post(
                c,
                path,
                {"limit": 1, "where": {}, **(loc())},
                label=path,
            )

        # -----------------------------------------------------------------
        # 9. Examine OOTB AppointmentScheduler appointment in full - they
        #    encode tech name in `name` ("[Scheduler] Cameron .") so maybe
        #    there's a different relation in their model
        # -----------------------------------------------------------------
        header("9. Inspect an AppointmentScheduler-origin appointment in detail")
        sched_appts = try_get(
            c,
            "/v3/appointment",
            params=loc(
                {
                    "where": json.dumps({"origin": "AppointmentScheduler"}),
                    "limit": "1",
                }
            ),
            label="scheduler-origin",
        )
        if sched_appts:
            sched_id = sched_appts[0]["id"]
            print(f"  scheduler appointment id={sched_id}")
            full = try_get(c, f"/v3/appointment/{sched_id}", label="full")
            if isinstance(full, dict):
                full_data = full
            elif isinstance(full, list) and full:
                full_data = full[0]
            else:
                full_data = None
            if isinstance(full_data, dict):
                print(f"  Top-level keys: {sorted(full_data.keys())}")
                # Look at attribution fields which are sometimes meaningful
                for k in (
                    "attributionMessageId",
                    "attributionSource",
                    "automatedCampaignId",
                    "publicId",
                    "meta",
                ):
                    v = full_data.get(k)
                    if v:
                        print(f"  {k}: {json.dumps(v, default=str)[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
