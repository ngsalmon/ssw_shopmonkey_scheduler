# Support request: is `appointment/search_replacement` supported for API use?

Draft email to Shopmonkey. Company `a833751f-2094-4a60-b9ac-3ce22ba46070`
(Salmon SpeedWorx), location `55826aba-3443-416e-b37b-edadb114696e`.

Context: we found the answer ourselves, so this is no longer a "how do we do
this" question — it's confirming we're allowed to keep relying on it, plus a
heads-up about a sharp edge that cost us a real double-booking.

---

**Subject:** Recurring appointments missing from `GET /v3/appointment` — is `search_replacement` the supported path?

Hi,

We run an online scheduling widget against the Shopmonkey v3 API. It reads each
day's appointments to work out which technicians are free before offering a slot.

We hit a problem that caused real double-bookings, tracked it down, and have a fix
in place — but the fix relies on an endpoint we can't find any documentation for,
so we'd like to confirm we're on supported ground.

**The problem:** `GET /v3/appointment` does not return appointments created with
the "Repeat" option, and neither does `POST /v3/appointment/search`. They're
absent from the results and from the `total` those endpoints report. A complete
paged walk of all 4,768 rows `GET /v3/appointment` returns for our account finds
zero with `rruleset`, `isRecurringParent`, `recurringAppointmentId`,
`originalStartDate`, or `lastRecurrenceEndDate` populated.

For us that meant 4–5 entries were invisible on *every* day — including a
technician's standing "Office Time, No Teching" block (1:00–5:30pm daily) and a
"Reserve/Buffer time" block covering three technicians (4:30–5:30pm daily). On
2026-08-21 our widget booked a customer at 5:00pm onto a technician who was inside
his repeating block. On one sampled day the API showed us 1 of 5 entries.

**What we're now using:** `POST /v3/appointment/search_replacement`, which is what
the web calendar itself calls. It works with our normal API token and returns
recurring occurrences already expanded, with `rruleset` and `technicians[]`
included. Across a week of live data it's a strict superset of the list endpoint —
zero rows lost, all the recurring ones recovered.

Our questions:

1. **Is `search_replacement` supported for API-token clients, and stable to build
   on?** The name suggests a transitional replacement for `search`, which makes us
   nervous about depending on it. If there's a different documented endpoint we
   should be using instead, we'll switch.
2. **Is the omission of recurring appointments from `GET /v3/appointment`
   intentional?** If so, it would be worth a note in the docs — the endpoint
   returns HTTP 200 with a plausible-looking result set and no indication that a
   whole class of appointment has been filtered out. That silence is what made this
   cost us weeks and a customer-visible double-booking.
3. Any guidance on `search_replacement`'s contract would help. Two things we had to
   determine empirically: `technicians` appears to be required (omitting it returns
   only unassigned entries — 1 row instead of 21), and `meta.total` over-counts
   (23 reported for a day holding 21 distinct rows, with the last page repeating
   rows already returned).

Happy to provide request IDs or a screen recording of the calendar entry alongside
the API response.

Thanks,
Nathan Salmon
Salmon SpeedWorx
