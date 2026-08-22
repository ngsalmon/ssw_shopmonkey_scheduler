---
description: Fix the P0 where find_or_create_vehicle attaches bookings to another customer's vehicle
---

Fix the open P0 in `find_or_create_vehicle`. The full task — evidence, the exact
patch, the existing tests that must change, and the regression tests to add — is
in `docs/vehicle-misattribution-todo.md`. Read it first and follow it.

In short: `shopmonkey_client.py:643` filters vehicles on `customerId`, which is
not a column on Shopmonkey's `Vehicle` model. Shopmonkey drops unknown `where`
fields silently, so the query returns every matching year/make/model in the shop
and the code takes `data[0]` — another customer's car.

Work in this order:

1. **Prove the bug first.** Add the `test_returns_this_customers_vehicle_not_a_strangers`
   test from the doc and watch it fail against the current code. A regression
   test that passes before the fix is testing nothing.
2. Apply the patch to `find_or_create_vehicle`.
3. Update the two existing tests the doc names. They currently assert the buggy
   `where` clause and one carries a comment claiming the search is "scoped to
   this customer" — it never was. **Update them to the corrected behavior; do
   not weaken the fix to keep them green.**
4. `pytest` and `ruff format . && ruff check .`.

Leave the VIN branch, `locationId`, and `find_or_create_customer` alone — the
doc's "Out of scope" section says why. `find_or_create_customer` in particular
looks like it has the same bug and does not; its filter is honored.

Report what you changed, the before/after of the failing test, and anything in
the doc that turned out not to match the code.
