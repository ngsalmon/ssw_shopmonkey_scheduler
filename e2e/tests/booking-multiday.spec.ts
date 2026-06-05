/**
 * Multi-day (overnight) booking flow.
 *
 * Regression for Anne's June 3 report: a Window Tint Ceramic starting 30
 * minutes before close was booked as a single 5:00-5:30 PM stub and never
 * rolled onto June 4. /book must create one appointment per spanned
 * business day, and /availability must check continuation-day conflicts
 * for ANY service that rolls past close (not just >5h ones).
 *
 * Default fixture facts these tests rely on (tests/e2e_mocks/state.py):
 *   - svc_multiday_ceramic: 6h labor, "Vinyl" dept, tech_cam is the ONLY
 *     qualified tech.
 *   - config.yaml business hours 09:00-17:30, hourly slot starts
 *     (09:00..17:00). A 6h service is overnight for starts after 11:30.
 */

import { expect, test } from '@playwright/test';
import { nextBusinessDayAfter, nextWeekday, toISODate } from '../helpers/dates';
import { getState, resetToDefaults, setScenario } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

const MULTIDAY_CERAMIC = 'svc_multiday_ceramic';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('overnight booking creates one appointment per spanned business day', async ({
  page,
  request,
}) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectServiceById(MULTIDAY_CERAMIC);
  await widget.clickNext();

  const day1 = nextWeekday(1);
  const day2 = nextBusinessDayAfter(day1);
  await widget.pickCalendarDate(day1);
  await widget.clickNext();

  // The 17:00 slot leaves only 30 min before the 17:30 close - the widget
  // must flag it as overnight, exactly like Anne's 5:00 PM booking.
  await expect(widget.slotByStart('17:00')).toHaveClass(/overnight/);
  await widget.selectSlotByStart('17:00');
  await widget.clickNext();

  await widget.fillCustomerForm({
    firstName: 'Javion',
    lastName: 'Cotton',
    email: 'javion@example.com',
    phone: '816-441-2152',
  });
  await widget.clickNext();
  await widget.fillVehicleForm({ year: 2026, make: 'Honda', model: 'Accord' });
  await widget.clickNext();
  // Step 6: review → final Book Appointment click.
  await widget.clickNext();
  await expect(widget.successPanel()).toBeVisible({ timeout: 10_000 });

  const state = await getState(request);

  // One appointment per business day, both persisted in mock Shopmonkey.
  expect(state.recorded_create_appointment_payloads.length).toBe(2);
  expect(state.appointments.length).toBe(2);

  const [d1, d2] = state.recorded_create_appointment_payloads as Array<
    Record<string, string | null>
  >;

  // Day 1: 17:00 through close (30 minutes - the old code stopped here).
  expect(d1!.start_date).toContain(`${toISODate(day1)}T17:00:00`);
  expect(d1!.end_date).toContain(`${toISODate(day1)}T17:30:00`);
  expect(d1!.title).toMatch(/\(Day 1 of 2\)$/);

  // Day 2: open until the remaining 5.5 hours are used (09:00-14:30).
  expect(d2!.start_date).toContain(`${toISODate(day2)}T09:00:00`);
  expect(d2!.end_date).toContain(`${toISODate(day2)}T14:30:00`);
  expect(d2!.title).toMatch(/\(Day 2 of 2\)$/);

  // Same tech, same linked order, same confirmation across both segments.
  expect(d1!.technician_id).toBe('tech_cam');
  expect(d2!.technician_id).toBe('tech_cam');
  expect(d1!.order_id).toBeTruthy();
  expect(d2!.order_id).toBe(d1!.order_id);
  const confirmation = /SM-\d{8}-[A-F0-9]{6}/.exec(d1!.notes ?? '')?.[0];
  expect(confirmation).toBeTruthy();
  expect(d2!.notes).toContain(confirmation!);
  expect(d1!.notes).toContain('Multi-day service: 2 days');
  expect(d2!.notes).toContain('Day 2 of 2');
});

test('overnight slots disappear when the continuation day is already booked', async ({
  page,
  request,
}) => {
  const day1 = nextWeekday(1);
  const day2 = nextBusinessDayAfter(day1);

  // tech_cam (the only Vinyl tech) is busy all of day 2. The wide window
  // with a fixed -06:00 offset covers the morning in both CST and CDT.
  await setScenario(request, {
    appointments: [
      {
        technician_id: 'tech_cam',
        start_date: `${toISODate(day2)}T08:00:00.000-06:00`,
        end_date: `${toISODate(day2)}T16:00:00.000-06:00`,
        name: 'Existing day-2 booking',
      },
    ],
  });

  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById(MULTIDAY_CERAMIC);
  await widget.clickNext();
  await widget.pickCalendarDate(day1);
  await widget.clickNext();

  // Morning starts still fit entirely within day 1 (e.g. 09:00-15:00).
  await expect(widget.slotByStart('09:00')).toBeVisible();
  // Every overnight start needs day-2 morning, which tech_cam can't take.
  await expect(widget.slotByStart('17:00')).toHaveCount(0);
  await expect(widget.overnightSlots()).toHaveCount(0);
});

test('short service rolling past close also checks day-2 conflicts', async ({
  page,
  request,
}) => {
  // Regression for the old `> 5 hours` gate: a 2h service starting at
  // 16:00 or 17:00 rolls past the 17:30 close, so day-2 conflicts must
  // hide those starts too. One qualified tech makes the conflict binary.
  const day1 = nextWeekday(1);
  const day2 = nextBusinessDayAfter(day1);

  await setScenario(request, {
    services: [
      {
        id: 'svc_tint_short',
        name: 'Window Tint - Full Coupe - Carbon',
        totalCents: 25000,
        labels: ['Tint'],
        laborHours: 2.0,
      },
    ],
    techs: [
      {
        tech_id: 'tech_solo',
        tech_name: 'Solo Tint',
        departments: { Tint: 1 },
      },
    ],
    appointments: [
      {
        technician_id: 'tech_solo',
        start_date: `${toISODate(day2)}T08:00:00.000-06:00`,
        end_date: `${toISODate(day2)}T16:00:00.000-06:00`,
        name: 'Existing day-2 booking',
      },
    ],
  });

  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById('svc_tint_short');
  await widget.clickNext();
  await widget.pickCalendarDate(day1);
  await widget.clickNext();

  // 09:00 fits in day 1 (ends 11:00) - still offered.
  await expect(widget.slotByStart('09:00')).toBeVisible();
  // 16:00 (90 min day 1 + 30 min day 2) and 17:00 (30 + 90) both need the
  // blocked day-2 morning - they must not be offered.
  await expect(widget.slotByStart('16:00')).toHaveCount(0);
  await expect(widget.slotByStart('17:00')).toHaveCount(0);
});
