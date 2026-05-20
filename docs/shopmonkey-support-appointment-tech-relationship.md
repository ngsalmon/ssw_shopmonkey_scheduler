# Email draft: REST API gap for appointment → technician relationship

Subject: **REST API: AppointmentUserConnection isn't reachable — need read access for online-scheduler availability checks**

---

Hi [Shopmonkey contact],

We're building an in-house online scheduling widget on top of the v3 REST API (`api.shopmonkey.cloud`) and have run into a gap that's actively causing double-bookings in production. Wanted to share what we've found and ask how you'd recommend we solve it.

### What we need

For our availability check to be correct, we need to know which technician(s) an existing appointment is assigned to. In the Shopmonkey calendar UI each appointment appears in its tech's swimlane, so the data clearly exists — we just can't find a way to read it.

### What we tried

Account: Salmon SpeedWorx (companyId `a833751f-…`). Bearer token (full-access).

1. **`GET /v3/appointment/{id}`** — dumped every key on the response. The appointment has `customerId`, `vehicleId`, `orderId`, `locationId`, `companyId`, etc., but no `technicianId`, `userId`, `users[]`, `technicians[]`, `assignedToUserId`, or anything similar.

2. **`POST /v3/appointment`** with `technicianId`, `userId`, `userIds[]`, `technicianIds[]`, and `assignedToUserId` — request returns 200 and the appointment appears on the calendar in some tech's swimlane (so something is consuming these), but immediately reading the appointment back with `GET /v3/appointment/{id}` returns the same flat record with none of those fields.

3. **Filter variants** — `?where={"technicianId": <real-uuid>}`, `userId`, `userIds`, `technicianIds`, `assignedToUserId`, `technician.id` — every variant returns the full unfiltered set (`meta.total = 3937`), confirming Shopmonkey treats these as unknown fields and ignores the filter.

4. **Expansion/include hints** — `?expand=users`, `?expand=technicians`, `?include=user,technician,users,technicians`, `?embed=users,technicians`, `?with=users,technicians`, `?_expand=user`, `?_embed=user`, `?fields=*` — all return HTTP 200 with the same flat shape, no nested user/tech keys.

5. **Sub-resource URLs** — `/v3/appointment/{id}/user`, `/users`, `/technician`, `/technicians`, `/assignees`, `/assignedUsers`, `/schedule`, `/swimlane`, `/calendar` — all return 404.

6. **Join-table endpoints** — `/v3/appointment_user`, `/v3/appointment_technician`, `/v3/appointment_assignee`, `/v3/calendar_event`, `/v3/scheduled_event`, `/v3/user_appointment`, `/v3/appointment_assignment`, `/v3/userAppointment`, `/v3/assignment`, `/v3/integration/appointment*` — all 404.

7. **User-side endpoints** — `/v3/user/{id}/appointments`, `/appointment`, `/schedule`, `/calendar`, `/labors` — all 404. The `User` record has `assignedTechnician`/`assignedServiceWriter` booleans but no appointment relationship.

8. **GraphQL / OpenAPI** — `/graphql`, `/v3/graphql`, `/api/graphql`, `/openapi.json`, `/swagger.json`, `/docs` — all 404.

### The clue we did find: `/v3/schema`

`GET /v3/schema` returns the full JSON-Schema dump of your data model. Inside it there's a model named **`AppointmentUserConnection`**:

```json
{
  "$id": "https://shopmonkey.dev/v3/AppointmentUserConnection",
  "table": "appointment_user_connection",
  "primaryKeys": ["id"],
  "properties": {
    "id":            { "type": "string" },
    "appointmentId": { "type": "string" },
    "userId":        { "type": "string" },
    "companyId":     { "type": "string" },
    "locationId":    { "type": "string" },
    "createdDate":   { "type": "string", "format": "date-time" },
    "updatedDate":   { "type": "string", "format": "date-time", "nullable": true },
    "metadata":      { "type": "object", "nullable": true }
  },
  "required": ["appointmentId", "companyId", "createdDate", "id", "locationId", "userId"]
}
```

This is exactly the join table we need. But we can't reach it — we tried every URL convention we could think of for it (`/v3/appointment_user_connection`, `/v3/appointmentUserConnection`, `/v3/AppointmentUserConnection`, `/v3/appointment-user-connection`, `/v3/appointment_user`, `/v3/appointmentUser`) and they all 404. We also confirmed that the two sibling join models — `CustomerLocationConnection` and `VehicleLocationConnection` — are not exposed under any URL convention either. So this looks like a categorical gap: `*Connection` schema models exist server-side but aren't routed through the REST surface.

### Current workaround (and its limits)

To ship something Monday we worked around it by walking the labor chain:

```
GET /v3/appointment?where={"startDate":{"gte":...,"lt":...}}  → list of appointments for the day
for each appointment with orderId:
    GET /v3/order/{orderId}/service                            → services with labors
    collect each labor.technicianId
```

We measured 96% of upcoming appointments on our account have at least one labor with `technicianId` populated, so this gets us most of the way. But:

- It's N+1 HTTP calls per `/availability` request (one extra per overlapping appointment). On a busy day that's noticeable latency for an interactive widget.
- It misses the ~4% of bookings where labors don't carry `technicianId` yet (e.g. fresh online bookings that haven't been triaged).
- Our own POSTs through `/v3/order/{id}/service` now stamp `technicianId` on the labor so they get counted on the next availability check — but if we ever want to read back which tech is on an appointment created via your OOTB scheduler or via the desktop calendar drag-drop, we'd have to fetch the order/services every time.

### Asks (in rough preference order)

1. **Expose `AppointmentUserConnection` via REST** at whatever URL convention you prefer (`/v3/appointment_user_connection` would match the table name). Filterable by `appointmentId` and `userId` would let us batch-fetch in one round-trip per `/availability` instead of N.

2. **OR inline the relationship on `GET /v3/appointment`**, e.g. an `assignedUserIds: [uuid, ...]` array, or honor `?expand=users` to embed the join. Either would be ideal for our use case.

3. **OR a list endpoint with a server-side date filter** like `GET /v3/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD` that returns each appointment with its assigned user(s) inline — basically the same data the calendar UI receives.

If any of these already exist and we missed them in our probing, please point us at the right endpoint and we'll happily switch over. Also happy to share the probe scripts we used if that would help reproduce.

One more thing while we have your attention: the `where` filter on `GET /v3/appointment` silently ignores MongoDB-style `$gte`/`$lt` operators (we discovered they have to be sent **without** the `$` prefix), and unknown filter fields silently return the full unfiltered result set instead of a 400. Both of those bit us before we noticed. Would be helpful if the API rejected unknown filters or documented the supported syntax — happy to file these separately if that's preferred.

Thanks!
[Your name]

---

## Engineering appendix (paste-in or omit)

**Account:** Salmon SpeedWorx (`a833751f-2094-4a60-b9ac-3ce22ba46070`), location `55826aba-…`.

**Date verified:** 2026-05-20.

**Repro scripts (in our repo):**
- `scripts/probe_appointment_model.py` — initial discovery that appointments have no tech field
- `scripts/probe_calendar_swimlane_endpoint.py` — exhaustive endpoint/URL-convention sweep
- `scripts/probe_swimlane_part2.py` — round-trip POST/GET test + `/v3/schema` discovery
- `scripts/probe_labor_tech_assignment.py` — measures the 96%/4% labor-tech coverage stat

**Round-trip test result:** POSTing `{technicianId, userId, userIds, technicianIds, assignedToUserId}` returns HTTP 200 with `success: true` and `data: {…flat appointment…}` (no tech fields echoed). Immediately GETting the same `id` returns the same flat shape. The appointment does show up in the calendar UI under some tech's swimlane though, so a write path clearly persisted somewhere.

**Schema URL:** `GET /v3/schema` (Bearer auth). Returns ~210 KB of JSON-Schema definitions including the `AppointmentUserConnection` and sibling `*Connection` models listed above.
