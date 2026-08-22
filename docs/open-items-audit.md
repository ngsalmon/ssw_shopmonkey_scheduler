# Open-items audit: department capacity, service naming, API quirks

Audited 2026-08-22. Code references are against `36cd9c0`; two sections (C4 and
Adjacent) were overtaken by later commits the same day and are annotated inline.

Answers the questions "what is still open in the remaining TODO docs" and "do
the API open questions imply action items". Items are sorted by whether someone
can act on them today, not by severity.

---

## A. Actionable now

### A1. Rename script is ready but will silently skip half the catalog — fix before running
`scripts/rename_services.py` already exists and already contains **all 20 renames**, with a dry-run mode. The doc's "To Execute: run a script that…" (`docs/service-naming-todo.md:72-78`) describes work that is done.

Two defects to fix first:

- **Pagination.** `fetch_all_canned_services` (`scripts/rename_services.py:53-63`) issues one unpaged `GET /v3/canned_service`. Per `docs/shopmonkey-api-quirks-todo.md:88-89` the API's default *and hard cap* is 100 rows per page, and `docs/shopmonkey-api-quirks-todo.md:199` records **194 canned services** on this account. The script sees at most 100 of them and prints the other ~94 under "expected services not found" — indistinguishable from "already renamed". Add paging (or `limit` + `skip` loop) before `--apply`.
- **Success is not verified.** `update_service_name` (`scripts/rename_services.py:66-69`) treats HTTP 200 as success. This API is known to return 200 for writes it silently drops (label attach is the documented case). Re-fetch and diff names after the apply rather than trusting the count.

Also: the doc's headline says "16 service renames" (`docs/service-naming-todo.md:10`) but its own tables and the script both hold **20**.

### A2. "No code changes needed" is wrong for the Express Detail renames
`docs/service-naming-todo.md:82-83` claims the widget parser handles all patterns. It does not handle group 5.

The Detail parser splits on a bare hyphen — `const parts = serviceName.split('-')` (`static/widget.js:138`). A name containing an internal hyphen splits into an extra part:

- `Detail - Express Exterior - 2-Row Vehicle` → parts `["Detail", "Express Exterior", "2", "Row Vehicle"]`
- `vehicleSizeRaw` becomes `"Row Vehicle"` (`static/widget.js:140`), which is what the card renders in the size chip (`static/widget.js:790-791`).

So after the rename the widget shows **"Row Vehicle"**, not "2-Row Vehicle". Same for the two other group-5 names. Fix: split on `' - '`, or handle the size suffix explicitly. (Card *titles* are unaffected — Detail titles are rebuilt from type+level at `static/widget.js:783-787`.)

Two mitigating facts worth knowing before prioritising this:
- **Detail is currently disabled in the widget.** `config.yaml` `disabled_departments: Detail` (except "Headlight Restoration"), enforced at `main.py:643-655`. All 11 Detail renames are invisible to customers today.
- **Widget assets are Cloudflare-cached** (4h, unversioned). Any `widget.js` fix needs a CF purge to take effect on `scheduler.salmonspeedworx.com`.

The other 9 renames (Bedliner, Consultations, both Window Tint groups) parse cleanly and are safe.

### A3. Backend is rename-safe — confirmed, not assumed
Department routing reads Shopmonkey **labels**, not names (`main.py:658-668`), and buffers key off labels too (`availability.py:927-947`). The only name-sensitive backend logic is the disabled-department exception match (`main.py:654`), and no rename touches "Headlight Restoration". Renames cannot break scheduling.

### A4. Send the drafted Shopmonkey support email
`docs/shopmonkey-support-recurring-appointments.md` is a complete, ready-to-send draft asking exactly Open Question 1 ("is `search_replacement` supported or transitional?"). Nothing in the repo records it as sent. This question is **not blocked** — it is blocked only on someone hitting send.

Recommend adding Open Question 2 (`locationId` on a multi-location account) to the same email; see B1.

### A5. We have no fallback if `search_replacement` goes away
`POST /v3/appointment/search_replacement` (`shopmonkey_client.py:327`) is the **only** appointment read path. A 404/410 there takes down `/availability` and `/book` outright. Regardless of Shopmonkey's answer, there is a decision to make and it is executable today: fail closed (current behavior — safe, total outage) versus fall back to `GET /v3/appointment` with a loud alarm (available but recurring-blind, i.e. the exact condition that caused the 2026-08-21 double-booking). Harness tests `14-search-replacement-contract` and `15-search-replacement-pagination` already pin the contract and would flag a change.

### A6. Open Question 3 is cheaper to close than the doc implies
The doc says the harness "is worth re-running before each release" (`docs/shopmonkey-api-quirks-todo.md:235-237`). It understates what already exists: `/home/nathan/git/shopmonkey-api-quirks` compares each run against recorded baselines in `evidence/*.json` and **exits non-zero when a finding stops reproducing** (`src/runner.ts:211-226`), printing which claims changed. It is already a regression detector; nothing is wired to it.

Concrete: `.github/workflows/ci-cd.yml` has no reference to the harness. It needs live credentials so it does not belong in PR CI — add it as a documented pre-release step or a scheduled job. Note the credential name differs: the harness reads `SHOPMONKEY_API_KEY` (`src/client.ts:4`), this repo uses `SHOPMONKEY_API_TOKEN`.

Run with: `cd /home/nathan/git/shopmonkey-api-quirks && pnpm test:all`

---

## B. Blocked

### B1. Does a *valid* `locationId` filter on a multi-location account?
Genuinely untestable from here — this account has one location (`docs/shopmonkey-api-quirks-todo.md:172-178`, harness test `13-locationid-param-ignored`). We send `locationId` on nine call sites in `shopmonkey_client.py` (lines 231, 460, 579, 615, 634, 652, 687, 732, 741) believing it scopes reads; it currently filters nothing.

Not fully dead-ended: it is a question a Shopmonkey engineer can answer in one sentence, so fold it into A4 rather than leaving it as "untestable". The risk direction also matters — the dangerous state (we think it scopes, it doesn't) is the state we are already in and already documented; a future release that *starts* honoring it would narrow our reads, which is what we intended anyway.

### B2. The bedliner appointment-duration decision (Option A/B/C)
`docs/department-capacity-todo.md:66-76` leaves this "TBD". It is a business decision, not an engineering one — nobody can execute it until the shop says what a bedliner appointment should look like on the Shopmonkey calendar. See C2 for what the code already implies about the answer.

---

## C. Stale doc — nothing actually open

### C1. `department-capacity-todo.md` status line and config proposal are obsolete
Status still reads "Partially implemented" (`docs/department-capacity-todo.md:3`), and lines 49-62 still propose a `department_capacity:` section in `config.yaml`. **That design was never built and should not be** — `grep -rn department_capacity` across the repo matches the doc and nothing else. `config.yaml` has no such section, and the shipped design puts the cap in the Google Sheet instead.

Already in code, fully wired and covered (41 test references to `max_concurrency`):
- `sheets_client.py:36` `MAX_CONCURRENCY_ROW_NAMES`; `:400` `_sync_get_department_concurrency`; `:443/:447` `get_max_concurrency_for_department`
- `availability.py:450` `cap_by_concurrency`, threaded through `slot_capacity` (`:479`), `count_multiday_overlap_capacity` (`:517`), `check_slot_availability_for_duration` (`:631`), `calculate_available_slots` (`:744`), `is_slot_available` (`:870`)
- `main.py:744` reads the cap, `:897` applies it on `/availability`, `:1057` on the `/book` re-check

Anyone reading this doc cold would build the wrong thing. Trim lines 43-62 to a pointer at the Update section.

### C2. What is *genuinely* still open in that doc: one thing, the tech/bay split
The two-dimensional capacity model is real and unimplemented. Both paths compute a single duration and use it for everything:

- `/availability`: `slot_duration = labor_duration + buffer_minutes` (`main.py:864-865`)
- `/book`: `duration_minutes = labor_duration + buffer_minutes` (`main.py:1026-1027`)
- every calendar segment over that span is created with the assigned tech stamped on it (`main.py:1304-1312`)

So a 2h bedliner with 3h cure holds the technician for the full 5h.

Worth noting for whoever picks this up: the **department dimension is already correct**. Because appointments span labor+buffer, `cap_by_concurrency` counts the bay as occupied through cure time, which is the desired behavior. Only the **tech dimension over-blocks**. The work is narrower than the doc's "two-dimensional capacity model" framing suggests: `slot_capacity` needs a tech-blocking window distinct from the bay-blocking window; today one duration flows through every call site.

Also, the Open Question is half-answered by shipped behavior — today's appointment duration *is* Option B (full 5h on the calendar), just without Option B's payoff ("tech can be reassigned after labor"). The remaining decision is narrower than the table implies.

### C3. `service-naming-todo.md` is untracked
`git status` shows `?? docs/service-naming-todo.md`. It exists only in Nathan's working tree — it is not on any branch, so no one else can see the decision they are being asked to approve, and it will be lost with the worktree. Commit it or discard it.

### C4. ~~`shopmonkey-api-quirks-todo.md` has uncommitted edits~~ — RESOLVED
Those were the P0-fixed marker and the P1 corrections, committed in `9f90072`
and `776cf19`. The status line now reads "P0 fixed … P1 doc corrections applied".

---

## Adjacent — STALE, corrected 2026-08-22
That cross-repo note described a defect already fixed in `ssw_pl` (`04a284d`):
`getOrdersWithOrderBy` is gone and six call sites now send lowercase `orderby`
with a JSON object. What actually survives is the completeness measurement the
sort fix was assumed to make unnecessary but which was never run against the
`POST /v3/export` dump — now an executable task at
`ssw_pl/docs/shopmonkey-sort-completeness-todo.md`. See `8155434`.
