# Shopmonkey v3 REST API conventions (verified against prod)

Hard-won conventions that differ from common REST expectations. Each item
was verified against the live Salmon SpeedWorx account; dates and probe
scripts noted. Read this before probing or building against a new entity.

## 1. The LIST endpoint is the hydrated surface, not the detail endpoint

`GET /v3/<entity>` (list) returns rows with related objects embedded;
`GET /v3/<entity>/{id}` (detail) returns the bare table row.

Verified for `appointment` (2026-06-05): list rows carry `technicians`,
`customer`, `vehicle`, `order`, reminders, campaign objects; the detail
response has none of them. This is the inverse of the usual detail ⊇ list
convention and is how we missed the appointment→technician relation for
two weeks (see `shopmonkey-support-appointment-tech-relationship.md`).

**Rule: when probing an entity's shape, dump keys from the LIST response.**

## 2. Every entity appears to have a `POST /v3/<entity>/search` endpoint

Documented per-resource at `https://shopmonkey.dev/resources/<entity>`.
The search endpoint takes a JSON body (`where`, `orderBy`, `limit`,
`skip`) and supports **filters the GET `where` param does not** — for
appointments that includes relation filtering:

```http
POST /v3/appointment/search
{
  "where": {
    "startDate": {"gte": "...", "lte": "..."},
    "technicians": ["<userId>", ...],   // ANY-match across assigned techs
    "includeUnassigned": false
  },
  "limit": 100,
  "skip": 0
}
```

Verified 2026-06-05 (`scripts/probe_appointment_search_by_tech.py`):
any-match semantics confirmed; `includeUnassigned: true` adds rows whose
`technicians` array is empty.

**Future optimization:** we have NOT yet surveyed the search endpoints of
other entities (`order`, `customer`, `vehicle`, `canned_service`, ...).
Each resource page documents its own search `where` schema — check there
first; it may unlock server-side filters (e.g. `updatedDate`-based
incremental pulls) that the GET `where` silently ignores. Relevant to the
ssw_pl ETL ingest as well.

## 3. `where` operators use Mongo style WITHOUT the `$` prefix

`{"startDate": {"gte": ..., "lt": ...}}` works.
`{"startDate": {"$gte": ...}}` is **silently ignored** → full unfiltered set.

## 4. Unknown filter fields fail SILENTLY with HTTP 200 + full dataset

There is no 400 for an unrecognized `where` field or operator — you get
the complete unfiltered collection. Always validate a new filter by
checking that (a) the row count differs from an unfiltered baseline and
(b) every returned row actually satisfies the predicate.
(11 relation-filter syntaxes on GET /v3/appointment all "succeeded" this
way — `scripts/probe_filter_appts_by_tech.py`.)

## 5. Pagination is `skip`-based; page size hard-capped at 100

`limit` caps at 100; use `skip` for subsequent pages; `meta.hasMore`
signals overflow. The `page` param is accepted and **silently ignored**
(same rows back every time — a probe looping on `page` rate-limited at
429 before noticing).

## 6. Write-field names ≠ read-field names for relations

`POST /v3/appointment` accepts a flat `technicianId: <uuid>`; it is not
echoed in the create response, never appears on detail GET, and reads
back only as the hydrated `technicians: [User]` relation on the list/search
endpoints. A 200 that doesn't echo a field does NOT mean the field was
dropped.

## 7. Rate limiting

429s appear quickly under bursty probing (~100+ rapid calls). Back off a
few seconds and retry; see `get()` helpers in `scripts/probe_*.py`.
