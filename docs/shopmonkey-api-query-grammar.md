# Shopmonkey v3 API: query grammar and the recurring-appointment gap

Verified against the live account on 2026-08-20. Written up because several of
these behaviours are undocumented, silent, and cost real debugging time.

## `GET /v3/schema` — the full data model

Undocumented, takes no parameters, needs only the normal bearer token. Returns
JSON Schema for **all 110 database models** (~231 KB).

```bash
curl -s -H "Authorization: Bearer $SHOPMONKEY_API_TOKEN" \
  https://api.shopmonkey.cloud/v3/schema
```

This is the real Postgres model, not a hand-written API doc:

* `Appointment.modelVersion` (`85a536296f04f2e7`) matches the `meta.modelVersion`
  stamped on live appointment records.
* Some field `comment`s leak raw SQL — e.g. `recurringExceptionId`'s comment is a
  fragment of a generated-column expression, and `duration`'s ends in `))::bigint`.

Only **41 of the 110 models have REST routes**. The other 69 exist in the database
but are unreachable over HTTP, including `AppointmentUserConnection` (the
appointment↔technician join table), `Labor`, `Service`, `Part`, and `Payment`.

## Filter grammar (`where`)

The filter grammar is Prisma's `where` semantics. Saying that in one sentence
documents the whole surface, and explains *why* the near-misses fail — `eq`,
`ne` and `like` are correct almost everywhere else; Prisma spells them
`equals`, `not` and `contains`. Verified honored/ignored sets:

| Honored | Silently ignored |
| --- | --- |
| bare scalar (shorthand for `equals`), `equals`, `not`, `in`, `notIn` | `eq`, `ne`, `neq`, `like`, `is` |
| `gt`, `gte`, `lt`, `lte` | `$`-prefixed forms (`$eq`) |
| `contains`, `startsWith`, `endsWith`, `mode: "insensitive"` | lowercase `and` |
| `AND`, `OR`, `NOT` | unknown fields, relation filters, `deleted` |

A **bare scalar is honored** — it is Prisma's shorthand for `equals`, not a
dropped term: `{"name": "<a real name>"}` returns 1 of 4781,
`{"firstName": ..., "lastName": ...}` returns 1 of 4645, and
`{"bookable": true}` returns 21 of 194.

Everything in the right-hand column returns the *entire unfiltered set* with
HTTP 200 and no error. `{"name": {"eq": "Ethan Out"}}` returns all 4768 rows.

Type mismatches are handled inconsistently, and only one of the two is loud. A
**number** where a string is declared 400s: `Order.number` is `"type": "string"`
in `/v3/schema`, so `{"number": 8133}` returns
`Invalid filter value for field 'number': expected a string`, while
`{"number": "8133"}` returns the 1 matching row. A **boolean** mismatch does
not: `{"bookable": "true"}` returns 200 and the entire unfiltered table. The
400 is a type error, not evidence that bare scalars are dropped.

> **Always sanity-check a new filter against a row you know exists before trusting
> a zero result.** A silently-dropped filter and a genuine zero look identical in
> the response body — only the `meta.total` distinguishes them.

Unknown field names and unknown operators are also accepted and dropped without
error.

`startsWith` and `mode: "insensitive"` are capabilities we are not using — worth
considering for the customer name lookup, which currently matches
case-sensitively on the server and re-checks case-insensitively in memory.

## Pagination: `orderby` is lowercase, and camelCase `orderBy` is silently ignored

This is the single highest-impact quirk here. **The working parameter is lowercase
`orderby`, taking a JSON object.** camelCase `orderBy` is accepted with HTTP 200 and
silently ignored — and *that*, not the database, is the cause of the
"Shopmonkey pagination is non-deterministic" folklore.

Measured 2026-08-20, three identical full walks of `GET /v3/order` over a bounded
universe (`where={"createdDate":{"gte":"2026-04-15..."}}`), `skip`-paged at 100:

| sort param | rows | distinct | within-run dupes | union | intersection | runs identical |
| --- | --- | --- | --- | --- | --- | --- |
| `orderby` (lowercase) | 2262 | **2262** | **0** | 2262 | 2262 | **YES** |
| `orderBy` (camelCase) | 2262 | ~1500 | 724-809 | 2191 | 589 | no |

With camelCase the smallest run missed **738 records** and the three runs shared
only 589. With lowercase all three runs returned byte-identical complete sets.

Sorting works on every field tested, on both endpoints — including the date fields
often reported as unorderable. Inversions out of 100 rows:

| endpoint / field | `orderby` | `orderBy` |
| --- | --- | --- |
| appointment / startDate | **0** | 50 |
| appointment / createdDate | **0** | 43 |
| appointment / id | **0** | 42 |
| order / createdDate | **0** | 50 |
| order / id | **0** | 5 |

Note the camelCase `id` row: only 5 inversions, which reads as "nearly sorted" and
is exactly the trap. It is not sorting — it is incidental clustering — but it is
close enough to convince you `orderBy` works on `id` and not on dates.

```bash
# GET — lowercase param, JSON-object value, URL-encoded
/v3/appointment?limit=100&skip=0&orderby=%7B%22startDate%22%3A%22asc%22%7D
```

`orderby=startDate` (bare field name) returns `Invalid Usage: Unable to parse
orderby`. `sort`, `order`, and `sortBy` are silently ignored.

**Consequence:** multi-pass ETL strategies built to work around "non-deterministic
pagination" are likely unnecessary. A single `orderby` walk is complete and
repeatable.

On `POST` bodies the field IS camelCase `orderBy` (see the search endpoints below) —
the casing differs between query string and JSON body, which is probably how the
confusion started.

Page size is hard-capped at 100 regardless of `limit`.

## `POST /v3/export` — undocumented bulk export

Exists and is the supported-ish way to get a guaranteed-complete dump. `GET` on it
404s, which is why an earlier probe of ours wrongly concluded there was no bulk API —
it is **POST only**.

```
POST /v3/export                {"tables": [...]}   -> {"fileName": "abc-123"}
POST /v3/export/presigned_url  {"fileName": "..."} -> {"url": "https://storage.googleapis.com/..."}
```

Returns JSONL-in-ZIP, 18 tables, 100% coverage including soft-deleted rows (filter
`deleted: true`), with a ~4-5 hour freshness lag. Field renames to watch:
`fee.percentValue` -> `fee.percent`, `subcontract.wholesaleCostCents` ->
`subcontract.costCents`. Missing vs REST: `part.coreChargeCents`,
`part.coreReturned`, `fee.taxable`, `fee.note`.

Credit: discovered and productionised by the SSW P&L ETL — see
`ssw_pl/docs/issues.md` §2h, which has the full field-level notes.

Note this is a *lagging* full dump, so it does not replace `search_replacement`
for live availability, and it is unclear whether it includes recurring appointment
rows.

## `POST /v3/appointment/search`

Supports a genuine filter grammar that the GET endpoint lacks — notably a
**per-technician filter**, which `GET /v3/appointment` has no equivalent for:

```json
{
  "where": {
    "technicians": ["<userId>", "..."],
    "startDate": {"gte": "2026-08-21T05:00:00.000Z"},
    "endDate":   {"lte": "2026-08-22T05:00:00.000Z"},
    "customerId": "...",
    "orderId": "...",
    "includeUnassigned": true
  },
  "orderBy": {"startDate": "asc"},
  "limit": 100,
  "skip": 0
}
```

Note this endpoint wants `gte`/`lte`. It validates `limit` (`body/limit must be
number`) but not `where`: `where` accepts a **fixed allowlist of fields and
silently drops everything else**. Its own fields work — `customerId` narrows 4781
to 8, an unmatchable one to 0, `startDate.gte` in the far future to 0 — but
`name`, a real column that `GET /v3/appointment` filters on correctly, is
discarded and returns all 4781 rows. Note also that dates go *inside* `where`
here, while `search_replacement` requires top-level `dateMin`/`dateMax` and
ignores `where`.

## Recurring appointments: use `search_replacement`

**`GET /v3/appointment` silently omits every recurring appointment**, and so does
`POST /v3/appointment/search`. Anything created with the web app's "Repeat"
checkbox is absent from both, and from the `total` they report. A stable, sorted,
complete walk of all 4,768 rows the list endpoint returns finds **zero** with any
of `rruleset`, `isRecurringParent`, `recurringAppointmentId`, `originalStartDate`,
or `lastRecurrenceEndDate` populated. `{"name": {"contains": "Office"}}` returns 0
while `{"name": {"contains": "Ethan Out"}}` returns 1 — the filter works, the row
simply isn't there.

The endpoint that DOES return them — found by watching what the Shopmonkey web
calendar itself calls — is:

```
POST /v3/appointment/search_replacement
```

It works with a normal API token. Recurring occurrences come back **already
expanded** to concrete start/end times, each carrying its `rruleset` and
`recurringAppointmentId`, with `technicians[]` hydrated. No rrule handling needed
on our side.

Its request shape differs from the rest of the API:

```json
{
  "dateMin": "2026-08-21T00:00:00.000-05:00",
  "dateMax": "2026-08-21T23:59:59.999-05:00",
  "includeUnassigned": true,
  "technicians": ["<userId>", "..."],
  "orderBy": {"startDate": "asc"},
  "limit": 100,
  "skip": 0
}
```

* Dates are **top-level `dateMin`/`dateMax`** as local ISO with offset — not a
  `where` clause, not UTC.
* **`technicians` is required.** Omit it and only unassigned entries come back
  (1 row instead of 21 on a live day). Pass every user, including inactive ones:
  a deactivated tech can't take a booking, but a ticket still assigned to them
  occupies a bay and must keep counting against department concurrency.
* `meta.total` **over-counts** — 23 reported for a day holding 21 distinct rows —
  and the tail page repeats rows already returned. Dedupe by `id`; don't trust
  the count or assume pages are disjoint.

### What this was hiding

Measured across a week of live data, `search_replacement` is a strict superset —
zero rows lost, 4-5 recurring entries recovered *every single day*:

| Date | `GET /v3/appointment` | `search_replacement` | hidden |
| --- | --- | --- | --- |
| 2026-08-21 | 16 | 21 | 5 |
| 2026-08-24 | 11 | 15 | 4 |
| 2026-08-26 | 6 | 10 | 4 |
| 2026-09-01 | 1 | 5 | 4 |

The hidden entries are exactly the ones that matter for availability:

* `Chandler - Office Time, No Teching` — 13:00-17:30, daily, one technician
* `Reserve/Buffer time` — 16:30-17:30, daily, across **three** technicians
* `Grant out see CR` — 09:00-09:30, daily
* `Lunch - All employees` — 12:00-12:30, daily, shop-wide
* `Danny Bartow SxS`, `E&L Saved time...` — multi-day and weekly job blocks

This caused a confirmed double-booking on 2026-08-21 and meant the widget was
overriding the shop's deliberate end-of-day reserve every single day.

### Caveats

`search_replacement` is undocumented, and the name suggests a transitional
replacement for `search`. If Shopmonkey retires it, our client raises and
`/availability` returns 502 — loud, not silent, so we'd notice immediately rather
than quietly resuming double-bookings. Worth confirming supported status with
Shopmonkey; see `shopmonkey-support-recurring-appointments.md`.

It also takes no `locationId`. That is fine for this single-location account but
would need checking before use on a multi-location one.
