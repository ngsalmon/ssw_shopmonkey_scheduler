# Repair-Order ("Ticket") Creation Design

**Status:** Draft for review
**Author:** Claude (with Nathan)
**Date:** 2026-05-19

## Goal

When a customer books online, our scheduler should create the same
Shopmonkey records the OOTB scheduler creates so staff can route the
booking through their existing workflow without manually building a
ticket. Today we create only an appointment; staff have to hand-build
the repair-order, which Anne flagged as a workflow regression.

## Findings (from API + production probe)

`scripts/probe_ootb_ticket.py` against the live SSW Shopmonkey instance
turned up:

1. **OOTB online bookings land in the workflow column `Scheduled`**
   (workflow_status id `1362307b-...`). All five recent orders in that
   column have `status: "Estimate"` and no `name`/`scheduledStartDate`
   set on the order itself.

2. **The link direction is `Appointment.orderId → Order.id`.** Orders
   don't track an `appointmentId` (the field exists but is empty on
   the Scheduled-column samples). Appointments carry the FK.

3. **Order fields we'll set on creation**
   - `customerId`, `vehicleId` — required, FKs to existing records.
   - `workflowStatusId` — the `Scheduled` column id (config-driven).
   - `status` — `"Estimate"` matches OOTB.
   - `color`, `name` — optional, can mirror the appointment's display
     name for staff readability.
   - `number` — auto-assigned by Shopmonkey; don't pass it.
   - `repairOrderDate`, `completedDate` — leave null.

4. **Service item attachment** is via `POST /v3/service_item` with at
   minimum `orderId` and the canned service ID. We don't yet have
   confirmed shape for the POST body — the docs page hit the WebFetch
   size limit and the GET-only probe couldn't show us. We'll either:
   - Make a one-off test POST against staging to verify shape, or
   - Defer service-item attachment to v2 and ship the order without
     the line item (staff still see the appointment notes that name
     the requested service).

## Proposed flow

```
existing flow:                     proposed flow:
─────────────────                   ─────────────────────────
find_or_create_customer             find_or_create_customer
find_or_create_vehicle              find_or_create_vehicle
                                  + create_order  ─────────────────┐
                                  + attach_service_item (v2 maybe) │
create_appointment    ───────────►  create_appointment(orderId=…) ─┘
                                    (orderId links the records)
```

New backend code:

- `ShopmonkeyClient.create_order(customer_id, vehicle_id, workflow_status_id,
   status="Estimate", color=None, name=None, location_id=…) -> dict`
- `ShopmonkeyClient.list_workflow_statuses() -> list[dict]` (so we can
  resolve the column name to its id on startup and cache it).
- `main.py:/book` orchestrates: customer → vehicle → order → appointment
  (passing `orderId`).
- `config.yaml` gains:
  ```yaml
  online_booking:
    workflow_status_name: "Scheduled"   # OOTB convention
    order_status: "Estimate"            # OOTB convention
    create_order: true                  # feature flag
  ```
- The mock `MockShopmonkeyClient` gains `create_order` and
  `list_workflow_statuses` so the e2e suite still runs against the
  full flow.

## Feature flag

Default `create_order: true`. If something goes wrong in production,
flipping the flag to `false` reverts to appointment-only behavior with
no code change. The flag also disables the workflow_status resolution
at startup, so the app doesn't fail to boot if Shopmonkey is
unreachable.

## Service-item attachment — defer or include?

Two options:

- **A. Include in v1** (parity with OOTB): one extra POST after the
  order is created. Requires confirmed POST shape; we'd verify against
  a single test booking first.
- **B. Defer to v2**: ship order creation without the service item.
  Staff see the requested service in the appointment notes
  (`Service requested: …`) and can add the line item manually. Faster
  to ship and easier to roll back.

Recommendation: **B (defer)**. Order creation alone fixes the missing
ticket; staff can finish populating the line item from notes. Once we
confirm the service-item POST shape (separate one-off test), we
revisit.

## Naming convention

Order `name` and appointment `name` should mirror the OOTB pattern
seen in the probe:

```
{Customer last name initial}. / {Year} {Make} {Model} / {Service name}
```

e.g. `"Russ N. / 2016 Toyota RAV4 / Window Tint - Two Door Tint -
Carbon - 20%"`. We have all the inputs in the booking payload, so this
is a string-format change in `main.py`.

## Verification plan

- **Unit tests** in `tests/test_endpoints.py`:
  - new test asserts `create_order` is called with the right shape
    when the flag is on
  - test asserts `create_appointment` is called with `orderId=<the
    new order id>`
  - test asserts the flag-off path skips order creation entirely
- **Surface parity test** updated for the new mock method.
- **E2E spec** (`booking.spec.ts`): assert `recorded_create_order_payloads`
  in `/test/state` reflects the order shape.
- **Manual staging probe**: one real booking against the SSW
  Shopmonkey instance with the new code; confirm the order appears
  in the Scheduled column and the appointment links to it.

## Risks / open questions

1. **workflow_status name drift.** If SSW renames the "Scheduled"
   column, the startup resolution fails. Mitigation: graceful fallback
   to a configured `workflow_status_id` override (skip resolution).
2. **Order creation may have required fields we haven't seen.** Probe
   showed many fields default to null/0 on Scheduled-column orders;
   if Shopmonkey rejects our minimal POST we'll iterate.
3. **Double-creation on retry.** Our `_request` retries on network
   errors. If create_order succeeds but the response is lost on the
   wire, the retry creates a duplicate. Mitigation: pass a client-side
   `external_id` if Shopmonkey supports it, or remove order creation
   from the retry path.
4. **Permissions.** The API token might not have order-create
   permission. Probe showed we can read /v3/order, but not POST. Need
   to confirm before the first real booking.

## Decisions I need from you

1. **Defer service-item attachment to v2?** I recommend yes (option B
   above). Faster ship; staff still see the requested service in
   notes.
2. **OK to do ONE real booking against the production Shopmonkey** to
   verify the POST shape during development? I'd guard it behind a
   `--dry-run` flag and only flip dry-run off once for the test, then
   delete the test order via the API.
3. **Naming convention.** Use the OOTB pattern shown above, or
   simpler (e.g. always just `"Online Booking: <service name>"`)?

## Rollout plan

After your approval:

1. Add `online_booking` block to `config.yaml`, default flag on.
2. Implement `create_order` + `list_workflow_statuses` on
   `ShopmonkeyClient` and `MockShopmonkeyClient`.
3. Wire into `main.py:/book` behind the flag.
4. Pytest + e2e tests + surface-parity update.
5. One probe-script test booking against the live env to verify
   shape; clean up the test record.
6. Commit; deploy.
