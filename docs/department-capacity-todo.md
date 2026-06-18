# Department Capacity Enhancement - TODO

**Status:** Partially implemented
**Date:** 2026-01-27 (updated 2026-06-18)

---

## Update 2026-06-18 - Concurrency ceiling shipped

The **department service-concurrency ceiling** is now implemented, configured
in the Google Sheet rather than `config.yaml`:

- A `MAX CONCURRENCY` row in the **Tech/Dept** tab holds a per-department cap
  (each department column carries its limit; a blank cell means no cap).
- `SheetsClient.get_max_concurrency_for_department()` reads it; `availability.py`
  applies a true occupancy ceiling:
  `effective = min(free_qualified_techs, max_concurrency - overlapping_dept_bookings)`
  via `cap_by_concurrency()`, threaded through `slot_capacity`,
  `count_multiday_overlap_capacity`, `calculate_available_slots`, and the
  `/book` re-check `check_slot_availability_for_duration`.

**Still deferred:** the two-dimensional split below (freeing the tech after
labor while the vehicle keeps occupying a department bay during cure time).
Today buffer time still blocks the assigned tech for the full labor+buffer
window. The Open Question about appointment duration remains unanswered.

---

## Summary

Implement a two-dimensional capacity model where tech labor capacity and department service capacity are tracked separately. This allows techs to be freed up after labor while the vehicle still occupies department space (e.g., bedliner cure time).

---

## Current Behavior

- Buffer time (e.g., 3hr bedliner cure time) is added to service duration
- Shopmonkey appointment includes full duration (labor + buffer)
- Tech is blocked for entire duration (labor + buffer)

---

## Requirements

### Two-Dimensional Capacity Model
1. **Tech labor capacity**: Should only be blocked by actual labor hours
2. **Department service capacity**: Should be blocked by labor + buffer (vehicle occupies space during cure)

### Configuration
- **Location**: `config.yaml` with `department_capacity` section
- **Bedliner capacity**: 2 concurrent services
- **Other departments**: Default to tech count (no artificial limit)

### Proposed Config Structure
```yaml
department_capacity:
  Bedliner: 2      # Max concurrent services (including vehicles curing)
  Tint: 1          # One tinter
  Vinyl: 2         # Two vinyl bays
  # Lifts: 4 total (includes alignment rack) - may be relevant for future services
  # Other departments default to available tech count
```

---

## Open Question

**What should the Shopmonkey appointment duration be for a bedliner (2hr labor + 3hr buffer)?**

| Option | Appointment Duration | Notes |
|--------|---------------------|-------|
| A | Labor only (2hr) | Tech assigned for labor; buffer tracked internally for capacity |
| B | Full duration (5hr) | Shows vehicle on-site time; tech can be reassigned after labor |
| C | Two appointments | Labor appointment + separate "cure time" placeholder |

**Decision**: TBD - circle back when implementing

---

## Implementation Notes

- Will require changes to `availability.py` slot calculation logic
- May need new data structure to track department occupancy separately from tech assignments
- Consider how this displays in Shopmonkey calendar view
