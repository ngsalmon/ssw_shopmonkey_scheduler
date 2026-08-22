# TODO: `find_or_create_vehicle` attaches bookings to the wrong customer's vehicle

**Status:** Open, live in production
**Severity:** P0 — data integrity, customer-visible
**Date:** 2026-08-21
**Evidence:** `../shopmonkey-api-quirks` test `11-vehicle-owner-filter-dropped`
(run `pnpm test:all -- --only 11-vehicle-owner-filter-dropped`; raw request log in
`evidence/11-vehicle-owner-filter-dropped.json`)

This file is written to be executed cold. Everything needed is below; you should
not need the conversation it came from.

---

## The bug

`shopmonkey_client.py:643` sends:

```python
where_clause = json.dumps(
    {
        "customerId": customer_id,
        "year": year,
        "make": make,
        "model": model,
    }
)
```

**`Vehicle` has no `customerId` column.** `GET /v3/schema` shows exactly one
ownership field on the model — `ownerCount` — and the real relation lives in
`VehicleOwner` (`customerId`, `vehicleId`), which has **no REST route** (404 on
`/vehicle_owner`, `/vehicleowner`, `/vehicle-owner`, `/vehicleOwner`). The
`owners[]` array that comes back on responses is hydrated onto the payload, not
a stored column, so it is not filterable either.

Shopmonkey silently drops unknown fields from `where` — it does not error. So
the filter degrades to `{year, make, model}`, returns **every matching vehicle in
the shop**, and `shopmonkey_client.py:658` takes `vehicles[0]`.

### Measured on the live account

- `{customerId, year, make, model}` and `{year, make, model}` return the
  **byte-identical id set**. The `customerId` term never reaches the query.
- For one shared year/make/model, 4 sampled customers each own a different
  vehicle. All 4 receive the same `data[0]`, so **3 of the 4 are handed a
  vehicle that is not theirs**.
- 21 of the year/make/model groups in a 300-vehicle sample are shared across
  more than one owner. Common vehicles collide constantly.
- These queries send no `orderby`, so *which* wrong vehicle you get also varies
  between identical calls — 2 distinct first-ids across 3 identical requests.

The booking, its order and its service history are written against another
customer's vehicle record. Same class as the customer misattachment Anne flagged
on 2026-05-19 — fixed for customers, still open for vehicles.

---

## The fix

Filter server-side on the columns that are real, then match ownership in memory
— exactly what `find_or_create_customer` already does for emails and phones.

Replace the year/make/model block at `shopmonkey_client.py:642-658`:

```python
        # Try to find by year/make/model, then match the owner in memory.
        #
        # `customerId` is deliberately NOT in this where clause: it is not a
        # column on Vehicle (the relation lives in VehicleOwner, which has no
        # REST route), and Shopmonkey drops unknown filter fields silently
        # rather than erroring. Including it does not narrow the query - it
        # returns every matching vehicle in the shop, and taking [0] attaches
        # the booking to whichever stranger's car sorts first.
        where_clause = json.dumps({"year": year, "make": make, "model": model})
        params = {"where": where_clause, "limit": str(self.PAGE_SIZE)}
        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/vehicle", params=params)
        vehicles = result.get("data", [])

        # `owners` is a list of customer ids hydrated onto the response. It is
        # the only ownership signal the API exposes for a vehicle.
        owned = [v for v in vehicles if customer_id in (v.get("owners") or [])]
        if owned:
            return owned[0]

        if len(vehicles) >= self.PAGE_SIZE:
            # The page cap is 100 and there is no way to filter by owner
            # server-side, so a very common year/make/model could page this
            # customer's own vehicle out of reach and cause a spurious create.
            logger.warning(
                "vehicle_lookup_page_full",
                year=year,
                make=make,
                model=model,
                returned=len(vehicles),
            )
```

Leave the VIN branch above it alone — `vin` is a real column and that filter is
honored. Leave the create block below it alone; `POST /v3/vehicle` with
`customerId` **does** populate `owners[]` (verified: 97 of the 100 most recently
created vehicles carry an owner, including one created the same day).

---

## Existing tests that will fail, and why they are wrong

Two tests in `tests/test_shopmonkey_client.py::TestFindOrCreateVehicle` assert
the buggy behavior. **Do not weaken the new behavior to keep them green** —
update them.

**`test_no_vin_skips_the_vin_lookup_entirely`** asserts

```python
assert where == {"customerId": "cust-1", "year": 2020, "make": "Subaru", "model": "WRX"}
```

Change the expected clause to `{"year": 2020, "make": "Subaru", "model": "WRX"}`.
Keep the `assert "vin" not in where` — that part is still exactly right.

**`test_falls_back_to_year_make_model_when_vin_misses`** asserts

```python
# The fallback search is scoped to this customer, not the whole shop.
where = json.loads(mock_client.request.call_args_list[1].kwargs["params"]["where"])
assert where["customerId"] == "cust-1"
```

That comment is the false belief this bug rests on — the search was never
scoped to the customer. Replace the assertion with one that the *returned
vehicle* belongs to the customer, and give the `existing` fixture an
`"owners": ["cust-1"]` key (its current `"customerId": "cust-1"` is a field the
API does not put on vehicles, so the fixture is unrealistic as well as unused by
the new code path).

---

## Regression test to add

In `tests/test_shopmonkey_client.py::TestFindOrCreateVehicle`, using the
module's existing `_ok` helper:

```python
    @pytest.mark.asyncio
    async def test_returns_this_customers_vehicle_not_a_strangers(self):
        """Shopmonkey drops `customerId` from a vehicle `where` - it is not a
        column on Vehicle - so the year/make/model search returns every matching
        vehicle in the shop. Taking data[0] hands the booking to whoever sorts
        first, which on live data was a different customer 3 times out of 4."""
        client = ShopmonkeyClient(api_token="test-token")
        stranger = {
            "id": "veh-stranger",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
            "owners": ["cust-other"],
        }
        ours = {
            "id": "veh-ours",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
            "owners": ["cust-1"],
        }
        mock_client = AsyncMock()
        # The stranger's vehicle sorts first, as it did against live data.
        mock_client.request = AsyncMock(return_value=_ok([stranger, ours]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_vehicle(
                customer_id="cust-1", year=2020, make="Subaru", model="WRX"
            )
        assert result == ours
        assert mock_client.request.call_count == 1  # matched, so no create
        await client.close()

    @pytest.mark.asyncio
    async def test_creates_when_no_returned_vehicle_belongs_to_the_customer(self):
        """A shop full of 2020 WRXs owned by other people must still produce a
        new vehicle for this customer, not reuse one of theirs."""
        client = ShopmonkeyClient(api_token="test-token")
        stranger = {
            "id": "veh-stranger",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
            "owners": ["cust-other"],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([stranger]), _ok({"id": "veh-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_vehicle(
                customer_id="cust-1", year=2020, make="Subaru", model="WRX"
            )
        assert result == {"id": "veh-new"}
        assert mock_client.request.call_args_list[-1].kwargs["method"] == "POST"
        await client.close()
```

The first test fails against today's code (it returns `veh-stranger`). Confirm
that before applying the fix — a regression test that passes on the broken code
is testing nothing.

---

## Known edge, accept and log

About 3% of vehicles on this account (10 of 300 sampled) have an empty
`owners[]`. Those can never match the in-memory check, so a booking for one will
create a duplicate vehicle rather than reuse it.

That is the right trade: a duplicate vehicle record splits one customer's
service history, while the current behavior writes a booking onto a *different
customer's* car. Prefer the duplicate. The `vehicle_lookup_page_full` warning
above and normal create logging are enough to spot it if it becomes common.

---

## Verification

```bash
pytest tests/test_shopmonkey_client.py::TestFindOrCreateVehicle -v   # all green
pytest                                                               # full suite
ruff format . && ruff check .
```

Then confirm the whole booking path still hangs together — `main.py:1152` is the
call site, and the resulting `vehicle_id` is passed into order and appointment
creation at `main.py:1205` and `main.py:1306`.

---

## Out of scope

- The VIN branch. `vin` is a real column and its filter is honored. It does
  return `vehicles[0]` without checking ownership, which is arguably wrong for a
  resold car, but a VIN identifies one vehicle and this is a separate question.
  Only ~7% of vehicles on this account carry a VIN, so the year/make/model path
  takes most of the traffic regardless.
- `locationId`, which is a silent no-op on this endpoint. Tracked separately in
  `shopmonkey-api-quirks-todo.md`.
- The corrections to `docs/shopmonkey-api-query-grammar.md`, also tracked in
  `shopmonkey-api-quirks-todo.md`. **In particular, do not "fix"
  `find_or_create_customer`** — its bare-scalar `where` filter is honored and
  that code is correct.
