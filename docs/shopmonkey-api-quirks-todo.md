# Shopmonkey API quirks — takeaways and TODO

**Status:** P0 fixed 2026-08-22 in `36cd9c0`; doc corrections pending
**Date:** 2026-08-21
**Evidence:** `../shopmonkey-api-quirks` — reproduction tests 09–13, raw request
logs under `evidence/`. Run with `pnpm test:all`.

---

## Why this exists

`docs/shopmonkey-api-query-grammar.md` was written from live probing on
2026-08-20. Re-verifying its claims against the API turned up three factual
errors in the doc **and** one live data-integrity bug in `shopmonkey_client.py`
that the doc's (incorrect) filter rules would have hidden. Everything below is
reproduced by a test in the harness repo, against this account's real data.

---

## P0 (FIXED) — `find_or_create_vehicle` attached bookings to the wrong customer's vehicle

> **Fixed** 2026-08-22 in `36cd9c0`: `customerId` is gone from the vehicle `where`
> clause and ownership is matched in memory against `owners[]`. Kept below as the
> record of the diagnosis. [`vehicle-misattribution-todo.md`](vehicle-misattribution-todo.md)
> carries the patch that was applied, the tests that changed and the
> verification steps.

**Where:** `shopmonkey_client.py:621` `find_or_create_vehicle`, reached from
`main.py:1152`; the resulting `vehicle_id` flows into order and appointment
creation at `main.py:1204` and `main.py:1305`.

**What happens.** The lookup sends:

```python
where = {"customerId": customer_id, "year": year, "make": make, "model": model}
```

`Vehicle` has no `customerId` column. `GET /v3/schema` shows exactly one
ownership field on the model — `ownerCount` — and the real relation lives in
`VehicleOwner` (`customerId`, `vehicleId`), which is one of the ~69 models with
**no REST route** (404 on `/vehicle_owner`, `/vehicleowner`, `/vehicle-owner`,
`/vehicleOwner`). The `owners[]` array that comes back on responses is hydrated
onto the payload, not a column, so it is not filterable either.

Unknown fields in `where` are dropped silently (harness test 09), so the query
degrades to `{year, make, model}` and the code takes `vehicles[0]`.

**Measured on this account** (harness test 11):

- `{customerId, year, make, model}` and `{year, make, model}` return the
  **byte-identical id set**. The `customerId` term never reaches the query.
- For one shared year/make/model, 4 sampled customers each own a different
  vehicle. All 4 get the same `data[0]`, so **3 of the 4 are handed a vehicle
  that is not theirs**.
- 21 of the year/make/model groups in a 300-vehicle sample are shared across
  more than one owner. This is not an edge case.
- Because these queries send no `orderby`, *which* wrong vehicle you get also
  varies between identical calls — 2 distinct first-ids across 3 identical
  requests. A retry can attach to a different stranger.

**Impact.** The booking, its order, and its service history are written against
another customer's vehicle record. Same class as the customer misattachment
Anne flagged on 2026-05-19 — fixed for customers, still live for vehicles.
Nothing in the response indicates a filter was partially discarded.

**Fix** (verified against live data in test 11 — all 4 owners resolve to their
own vehicle): keep the server-side filter to the columns that are real, and do
the ownership match in memory, exactly as `find_or_create_customer` already
does for emails and phone numbers.

```python
# Drop customerId from the where clause — it is not a column and is silently
# discarded, widening the query to every matching vehicle in the shop.
where_clause = json.dumps({"year": year, "make": make, "model": model})
params = {"where": where_clause, "limit": self.PAGE_SIZE}
...
vehicles = result.get("data", [])
# `owners` is a list of customer ids, hydrated onto the response. It is the
# only ownership signal the API exposes, and it is not filterable server-side.
owned = [v for v in vehicles if customer_id in (v.get("owners") or [])]
if owned:
    return owned[0]
# fall through to create
```

Notes for whoever picks this up:

- **Pass `limit` explicitly.** These calls currently omit it and get the default
  page of 100, which is also the hard cap (harness test 03). A year/make/model
  with more than 100 instances shop-wide could page the customer's own vehicle
  out of reach and cause a spurious create. Worth logging when a lookup returns
  a full page.
- **The VIN path above it is sound** — `vin` is a real column and the filter is
  honored. It is also the only lookup here that cannot be confused by a common
  vehicle, so prefer it whenever a VIN is available. Only ~7% of vehicles on
  this account carry one, so the year/make/model path takes most of the traffic.
- Consider whether the VIN branch should verify ownership too. It currently
  returns `vehicles[0]` unconditionally; a VIN is unique to a car but not to a
  *record*, and the record it finds may be attached to a previous owner.

**Regression test to add** (`tests/test_shopmonkey_client.py`): mock
`/v3/vehicle` returning two vehicles with the same year/make/model and
different `owners`, ordered so that `data[0]` belongs to the *other* customer.
Assert `find_or_create_vehicle` returns the one owned by the requested customer
and does not POST a new vehicle. That test fails against today's code.

---

## P1 — Corrections to `docs/shopmonkey-api-query-grammar.md`

The same file exists in `ssw_pl/docs/shopmonkey-api-query-grammar.md`; only the
framing headers differ, so all three corrections apply to both copies.

### 1. The base URL in the `/v3/schema` example is wrong

The doc's curl example uses `https://api.shopmonkey.io/v3/schema`. That host
does not resolve — curl fails to connect. The working host is
`https://api.shopmonkey.cloud`, which is what every codebase we have already
uses (`shopmonkey_client.py:99`, `ssw/backend/config.py:5`,
`ssw_pl/backend/src/integrations/shopmonkey.ts`).

### 2. "Bare scalar values are silently ignored" is backwards — they work

The doc lists bare scalars alongside `eq`/`ne`/`like` as silently dropped. They
are not: a bare scalar is Prisma's shorthand for `equals` and is honored.
Verified — `{"name":"<a real name>"}` returns 1 of 4781, `{"firstName":...,
"lastName":...}` returns 1 of 4645, `{"bookable":true}` returns 21 of 194.

**This matters for us directly:** `find_or_create_customer` relies on bare
scalars, and the doc as written implies that lookup is broken and returning
unfiltered pages. It isn't. That path is fine — do not "fix" it.

### 3. `{"number": 8133}` → 400 is a type error, not a bare-scalar rule

`Order.number` is declared `"type": "string"` in `/v3/schema`, so the numeric
form returns `Invalid filter value for field 'number': expected a string`.
`{"number":"8133"}` returns the 1 matching row. Worth recording the
inconsistency the doc misses: a **number** type mismatch 400s loudly, while a
**boolean** one (`{"bookable":"true"}`) returns 200 and the entire unfiltered
table (harness test 10).

### 4. Replace the operator table with the actual grammar

The filter grammar is Prisma's `where` semantics. Saying that in one sentence
documents the whole surface, and explains *why* the near-misses fail — `eq`,
`ne` and `like` are correct almost everywhere else; Prisma spells them
`equals`, `not` and `contains`. Verified honored/ignored sets (harness test 09):

| Honored | Silently ignored |
| --- | --- |
| bare scalar (shorthand for `equals`), `equals`, `not`, `in`, `notIn` | `eq`, `ne`, `neq`, `like`, `is` |
| `gt`, `gte`, `lt`, `lte` | `$`-prefixed forms (`$eq`) |
| `contains`, `startsWith`, `endsWith`, `mode: "insensitive"` | lowercase `and` |
| `AND`, `OR`, `NOT` | unknown fields, relation filters, `deleted` |

`startsWith` and `mode: "insensitive"` are new capabilities we are not using —
worth considering for the customer name lookup, which currently matches
case-sensitively on the server and re-checks case-insensitively in memory.

### 5. Sharpen the `POST /v3/appointment/search` note

The doc says it "validates `limit` but not `where`". More precisely: `where`
accepts a fixed allowlist of fields and silently drops everything else
(harness test 12). Its own fields work — `customerId` narrows 4781 to 8, an
unmatchable one to 0, `startDate.gte` in the far future to 0 — but `name`, a
real column that `GET /v3/appointment` filters on correctly, is discarded and
returns all 4781 rows. Note also that dates go *inside* `where` here, while
`search_replacement` requires top-level `dateMin`/`dateMax` and ignores `where`.

---

## P2 — `locationId` is a no-op on this account

We pass `locationId` on customer, vehicle and order queries believing it scopes
them. A `locationId` matching no location at all returns the full unfiltered
table on all four resources tested — no 4xx, no empty result (harness test 13).

This account has one location, so we cannot tell whether a *valid* `locationId`
filters; what is certain is that an invalid one is accepted silently. Harmless
today. It stops being harmless the day the shop opens a second location, at
which point the same code silently starts returning both. Either drop the
parameter or add a startup assertion that the configured id is in
`GET /v3/location`.

---

## Confirmed still true — no action needed

- **`search_replacement` remains a strict superset.** Re-measured 2026-08-21:
  21 distinct rows vs 16 from `GET /v3/appointment`, 5 hidden, all 5 carrying an
  `rruleset`, 0 rows lost. Our dedupe-by-id is correct — `meta.total` reported
  23 for 21 distinct rows.
- **Recurring rows really are absent, not merely unsorted.** Now that we know
  `not` is a working operator, this was re-tested directly:
  `{"rruleset":{"not":null}}`, `{"isRecurringParent":true}` and
  `{"recurringAppointmentId":{"not":null}}` all return **0** against
  `GET /v3/appointment`. The rows are not in the collection.
- **`find_or_create_customer`** — see correction 2 above. Working as intended.
- **`{"bookable": true}`** on `/v3/canned_service` filters correctly (21 of 194).
  Keep it a JSON boolean; the string `"true"` is silently ignored.

---

## Cross-repo: `ssw_pl` ETL is running the broken sort form

Not this repo's code, but it came out of the same investigation and someone
should carry it over.

`ssw_pl/backend/src/integrations/shopmonkey.ts:839` `getOrdersWithOrderBy` sends
camelCase `orderBy` with a **string** value (`'updatedDate:desc'`). Both halves
are wrong: the working query-string form is lowercase `orderby` taking a JSON
object. So every "sort strategy" pass in `sync-orders.ts` is paging an unsorted
result set. `getCustomers`, `getVehicles` and `getVendors` send no sort at all.

Consequently `syncOrdersWithMultiPass` (multi-pass + dedupe + bail after 10
consecutive zero-new pages) and `getAllVendors` (bail after 3) are giving
*heuristic* completeness, not guaranteed completeness. The lowercase fix should
make a single deterministic walk sufficient — but that needs to be **measured
against the `POST /v3/export` dump before the multi-pass safety net is removed**,
not assumed.

The comments at `shopmonkey.ts:795` and `:809` ("`sort` and `orderBy` on date
fields are silently ignored by the API") are now known to be false and will
mislead the next reader.

---

## Open questions

- Is `search_replacement` supported, or transitional? Still unanswered — see
  `docs/shopmonkey-support-recurring-appointments.md`. Unchanged by this round.
- Does a *valid* `locationId` filter on a multi-location account? Untestable
  here.
- Do any of the silently-ignored forms change behavior without notice? Every
  one of them is a 200 today, so a future release that starts honoring `eq`
  would change our result sets with no error and no version bump. The harness
  is the early-warning system for that; it is worth re-running before each
  release.
