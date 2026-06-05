/**
 * Date helpers for tests that need to pick days relative to "now".
 *
 * Per the design (docs/e2e-test-harness-design.md), we don't freeze time.
 * Wrap all date math in these helpers so we can swap strategy later without
 * rewriting every spec.
 */

export function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function daysFromToday(n: number): Date {
  const d = new Date();
  d.setHours(12, 0, 0, 0); // noon avoids DST/midnight edge cases
  d.setDate(d.getDate() + n);
  return d;
}

/** Returns the next date (today + delta) that falls on a weekday (Mon-Fri). */
export function nextWeekday(minDaysAhead: number = 1): Date {
  let d = daysFromToday(minDaysAhead);
  while (d.getDay() === 0 || d.getDay() === 6) {
    d = new Date(d.getTime() + 24 * 60 * 60 * 1000);
  }
  return d;
}

/** The next business day (Mon-Fri) strictly after the given date. Mirrors
 * availability.get_next_business_day for the default config (no holidays). */
export function nextBusinessDayAfter(d: Date): Date {
  let next = new Date(d.getTime() + 24 * 60 * 60 * 1000);
  while (next.getDay() === 0 || next.getDay() === 6) {
    next = new Date(next.getTime() + 24 * 60 * 60 * 1000);
  }
  return next;
}

/** Today's date in YYYYMMDD form, for matching confirmation numbers SM-YYYYMMDD-XXXXXX. */
export function todayYYYYMMDD(): string {
  return toISODate(new Date()).replace(/-/g, '');
}
