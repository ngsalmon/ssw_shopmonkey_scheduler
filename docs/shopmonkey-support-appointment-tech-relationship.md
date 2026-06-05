# Appointment → technician relationship over REST

**Status: RESOLVED 2026-06-05** — the data exists and we can read it. The
original email below (2026-05-20) framed this as a missing feature; it is
actually a discoverability/consistency issue in the API surface. Keep this
section current; the original email is preserved at the bottom for history.

## The answer

`GET /v3/appointment` (the **list** endpoint) returns each appointment with a
hydrated **`technicians: [User, ...]`** relation — along with `customer`,
`vehicle`, `order`, reminders, and campaign objects. Verified against prod on
2026-06-05: 33/33 appointments in the June 2–4 window carry a populated
`technicians` array, including our own online bookings (e.g. Javion Cotton's
June 3 booking shows Mina Vang, proving the `technicianId` we send on
`POST /v3/appointment` does create the `AppointmentUserConnection` row).

Shopmonkey support confirmed the same from their side ("There should be a
Technicians object returned from a GET call to the Appointments endpoint").

## Why we missed it on 2026-05-20

Two REST-convention inversions, compounded by silent-success behavior:

1. **List ⊃ detail (read/read asymmetry).** `GET /v3/appointment/{id}` (the
   *detail* endpoint) returns the bare table row with **no** `technicians`
   key — still true as of 2026-06-05. Every exhaustive key-dump and every
   `?expand=`/`?include=` experiment in our original probes ran against the
   detail endpoint (see `scripts/probe_appointment_model.py`,
   `scripts/probe_calendar_swimlane_endpoint.py` line ~142); the list
   endpoint was only used to harvest ids. We assumed the near-universal
   convention detail ⊇ list. Shopmonkey inverts it: the list endpoint is the
   hydrated surface.

2. **Write name ≠ read name (POST/GET asymmetry).** You write a flat
   `technicianId: <uuid>` on POST; it is not echoed in the create response,
   never appears on detail GET, and reads back only on the list GET as
   `technicians: [User]` — different name, different shape, different
   endpoint. A 200 response that doesn't echo the field is indistinguishable
   from an ignored unknown field, which is exactly what we concluded.

The kicker: `ShopmonkeyClient.get_appointments_for_date()` already uses the
list endpoint, so `technicians` has been present in every availability
payload we fetch — the code just never read that key.

## Filtering appointments by assigned tech: use `POST /v3/appointment/search`

Server-side tech filtering **works today**, but only via the search
endpoint — documented at https://shopmonkey.dev/resources/appointment and
verified against prod 2026-06-05
(`scripts/probe_appointment_search_by_tech.py`):

```http
POST /v3/appointment/search
{
  "where": {
    "startDate": {"gte": "2026-06-02T00:00:00Z", "lte": "2026-06-04T23:59:59Z"},
    "technicians": ["<userId>", "<userId>", ...],
    "includeUnassigned": false
  },
  "limit": 100,
  "skip": 0
}
```

- `technicians` is **ANY-match**: a row is returned when any of its
  assigned techs appears in the list (verified: Mina-only → 7/33 rows;
  Mina+Gus → 11/33, every returned row matching at least one).
- `includeUnassigned: true` additionally returns appointments whose
  `technicians` array is empty — exactly the "unattributed" bucket the
  availability check treats conservatively.
- Pagination on both list and search is `skip`-based (the docs list
  `where/orderby/limit/skip` for the GET endpoint too). This also explains
  why the `page` param was silently ignored in our earlier probes.

The GET list `where` param does **not** support any tech/relation filter.
Tested against prod 2026-06-05 (`scripts/probe_filter_appts_by_tech.py`):
all of the following return HTTP 200 with the **full unfiltered set**
(unknown filter fields are silently ignored):

```text
{"technicians": {"some":  {"id": X}}}            # Prisma to-many some
{"technicians": {"some":  {"id": {"equals": X}}}}
{"technicians": {"every": {"id": X}}}
{"technicians": {"id": X}}
{"technicians.id": X}
{"users": {"some": {"id": X}}}
{"appointmentUserConnection":  {"some": {"userId": X}}}
{"appointmentUserConnections": {"some": {"userId": X}}}
{"technicianIds": {"has": X}}
{"userIds": {"has": X}}
{"technicianId": X}
```

For our availability flow the date-window list fetch + client-side read of
`appt["technicians"]` remains fine (zero extra calls); the search endpoint
is the right tool for "all upcoming appointments for tech X" queries.

## Plan of record for the scheduler

1. **Primary**: read busy techs from `appt["technicians"]` (zero extra
   calls; reflects calendar-swimlane truth, including staff drag/reassign —
   e.g. the Lauren T. June 15 booking shows Zack Salmon after his manual
   reassignment, while the order labors still say Nikki Turner).
2. **Fallback**: keep the Appointment → Order → Service.labors →
   technicianId walk, but only for orderId-bearing appointments whose
   `technicians` array is empty (can only shrink the ~4% unattributed
   bucket, never grow it).
3. **Shadow compare** (config-flagged): while enabled, run both sources and
   log `tech_source_mismatch` events; after a couple of weeks decide whether
   the union of both is warranted, then flip the flag off and drop the N+1
   latency.
4. Our own `/book` stamps both sides (`technicianId` on the appointment
   create and on the attached labor lines), so both sources agree for
   widget bookings.

## Other where-filter quirks (verified, still current)

- Mongo-style operators must be sent **without** the `$` prefix:
  `{"startDate": {"gte": ...}}` works; `{"$gte": ...}` is silently ignored
  and returns ~100 unfiltered rows.
- Unknown filter fields are silently ignored (full set returned) instead of
  erroring — see the table above.
- Pagination is `skip`-based (documented). The `page` param is silently
  ignored (same rows back every page). Page size is hard-capped at 100;
  `meta.hasMore` signals overflow.

---

## Reply email draft (send in the existing support thread)

Subject: **Re: REST API: appointment → technician relationship — resolved, plus a filter request and some DX feedback**

Hi [Shopmonkey contact],

Thank you — that was exactly the pointer we needed. Confirmed on our side:
the `technicians` array comes back on `GET /v3/appointment` (the list
endpoint), it's populated for every appointment we checked, and it correctly
reflects assignments created via your scheduler, manual calendar edits, and
our own API bookings. We're switching our availability checks over to it,
and you can disregard the `AppointmentUserConnection` exposure request from
our earlier email.

For what it's worth, here's how we managed to miss it — sharing because a
couple of small changes would make this much harder to get wrong:

1. **The detail endpoint doesn't include it.** `GET /v3/appointment/{id}`
   returns the flat row with no `technicians` key (verified again today),
   while the list endpoint returns it hydrated. We did all our deep
   inspection on the detail endpoint assuming it was the richer view.
   Including `technicians` on the detail response — or documenting that the
   list endpoint is the hydrated surface — would have saved us a week.

2. **The write field and read field don't match.** `POST /v3/appointment`
   accepts `technicianId` (singular, flat) but doesn't echo it in the
   create response, and it reads back only as the `technicians` relation on
   the list endpoint. Since the response looked identical to sending an
   unknown field, we concluded the field was being dropped. Echoing accepted
   relation fields in the create response, and documenting `technicianId`
   (and whether a multi-tech variant like `technicianIds[]` exists), would
   close that loop.

One more ask while we have the thread open:

3. **Loud failures on unknown filters.** Unknown `where` fields and
   `$`-prefixed operators on the GET endpoint are silently ignored and the
   API returns the complete unfiltered dataset with a 200. For scheduling
   use-cases that's a correctness hazard — an unfiltered appointment list
   interpreted as "the day's schedule" produces wrong availability. A 400
   on unrecognized filter fields/operators (or a `meta.warnings` entry)
   would make these mistakes visible immediately. Same for unsupported
   pagination params (`page` is accepted but ignored; `skip` is the real
   one).

The API has otherwise held up well under our scheduler load — the inline
relations on the list endpoint in particular mean we can do per-tech
availability in a single round-trip per day, which is great. Happy to share
our probe scripts for any of the above.

Thanks again,
[Your name]
Salmon SpeedWorx (companyId `a833751f-2094-4a60-b9ac-3ce22ba46070`)

---

## Original email draft (2026-05-20, kept for history — superseded above)

Subject: **REST API: AppointmentUserConnection isn't reachable — need read access for online-scheduler availability checks**

Hi [Shopmonkey contact],

We're building an in-house online scheduling widget on top of the v3 REST API (`api.shopmonkey.cloud`) and have run into a gap that's actively causing double-bookings in production. Wanted to share what we've found and ask how you'd recommend we solve it.

### What we need

For our availability check to be correct, we need to know which technician(s) an existing appointment is assigned to. In the Shopmonkey calendar UI each appointment appears in its tech's swimlane, so the data clearly exists — we just can't find a way to read it.

### What we tried

Account: Salmon SpeedWorx (companyId `a833751f-…`). Bearer token (full-access).

1. **`GET /v3/appointment/{id}`** — dumped every key on the response. The appointment has `customerId`, `vehicleId`, `orderId`, `locationId`, `companyId`, etc., but no `technicianId`, `userId`, `users[]`, `technicians[]`, `assignedToUserId`, or anything similar.
   *(2026-06-05 note: accurate for the detail endpoint — but the LIST endpoint returns `technicians` inline. See resolution above.)*

2. **`POST /v3/appointment`** with `technicianId`, `userId`, `userIds[]`, `technicianIds[]`, and `assignedToUserId` — request returns 200 and the appointment appears on the calendar in some tech's swimlane (so something is consuming these), but immediately reading the appointment back with `GET /v3/appointment/{id}` returns the same flat record with none of those fields.

3. **Filter variants** — `?where={"technicianId": <real-uuid>}`, `userId`, `userIds`, `technicianIds`, `assignedToUserId`, `technician.id` — every variant returns the full unfiltered set (`meta.total = 3937`), confirming Shopmonkey treats these as unknown fields and ignores the filter.

4. **Expansion/include hints** — `?expand=users`, `?expand=technicians`, `?include=user,technician,users,technicians`, `?embed=users,technicians`, `?with=users,technicians`, `?_expand=user`, `?_embed=user`, `?fields=*` — all return HTTP 200 with the same flat shape, no nested user/tech keys.
   *(2026-06-05 note: these were all run against the DETAIL endpoint.)*

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
   *(2026-06-05 note: this already existed — `technicians` is inlined on the list endpoint.)*

3. **OR a list endpoint with a server-side date filter** like `GET /v3/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD` that returns each appointment with its assigned user(s) inline — basically the same data the calendar UI receives.

If any of these already exist and we missed them in our probing, please point us at the right endpoint and we'll happily switch over. Also happy to share the probe scripts we used if that would help reproduce.

One more thing while we have your attention: the `where` filter on `GET /v3/appointment` silently ignores MongoDB-style `$gte`/`$lt` operators (we discovered they have to be sent **without** the `$` prefix), and unknown filter fields silently return the full unfiltered result set instead of a 400. Both of those bit us before we noticed. Would be helpful if the API rejected unknown filters or documented the supported syntax — happy to file these separately if that's preferred.

Thanks!
[Your name]

---

## Engineering appendix (2026-05-20, updated 2026-06-05)

**Account:** Salmon SpeedWorx (`a833751f-2094-4a60-b9ac-3ce22ba46070`), location `55826aba-…`.

**Probe scripts (in our repo):**
- `scripts/probe_appointment_model.py` — original discovery (detail-endpoint key dumps)
- `scripts/probe_calendar_swimlane_endpoint.py` — endpoint/URL-convention sweep (detail-based)
- `scripts/probe_swimlane_part2.py` — POST/GET round-trip test + `/v3/schema` discovery
- `scripts/probe_labor_tech_assignment.py` — the 96%/4% labor-tech coverage stat
- `scripts/probe_appt_technicians_field.py` — **2026-06-05: proves `technicians` on the list endpoint**, incl. on our own bookings
- `scripts/probe_filter_appts_by_tech.py` — **2026-06-05: proves no GET where-filter by tech works (11 variants)**
- `scripts/probe_appointment_search_by_tech.py` — **2026-06-05: proves `POST /v3/appointment/search` filters by tech (any-match) + `includeUnassigned`**
