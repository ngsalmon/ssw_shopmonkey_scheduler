/**
 * Calendar + time slot rendering. Exercises availability.py end-to-end:
 * business hours, existing appointment conflicts, multi-day services.
 */

import { expect, test } from '@playwright/test';
import { nextWeekday, toISODate } from '../helpers/dates';
import { resetToDefaults, setScenario } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

const TINT_FULL_COUPE_CARBON = 'svc_tint_full_coupe_carbon';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('calendar renders, weekend dates are disabled', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectServiceById(TINT_FULL_COUPE_CARBON);
  await widget.clickNext();

  // Saturday and Sunday cells should be disabled per config.yaml.
  const disabled = page.locator('.calendar-day.disabled');
  expect(await disabled.count()).toBeGreaterThan(0);
});

test('picking a weekday loads time slots', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectServiceById(TINT_FULL_COUPE_CARBON);
  await widget.clickNext();

  const target = nextWeekday(1);
  await widget.pickCalendarDate(target);
  await widget.clickNext();

  await expect(widget.timeSlots().first()).toBeVisible({ timeout: 5_000 });
  expect(await widget.timeSlots().count()).toBeGreaterThan(0);
});

test('existing appointment removes overlapping slots', async ({ page, request }) => {
  const target = nextWeekday(1);
  const dateStr = toISODate(target);
  // Block the entire business day for the qualified tint tech.
  await setScenario(request, {
    appointments: [
      {
        technician_id: 'tech_alex',
        start_date: `${dateStr}T09:00:00.000-06:00`,
        end_date: `${dateStr}T17:30:00.000-06:00`,
        name: 'Blocking appointment',
      },
      // tech_cam also does Tint at priority 2, block them too.
      {
        technician_id: 'tech_cam',
        start_date: `${dateStr}T09:00:00.000-06:00`,
        end_date: `${dateStr}T17:30:00.000-06:00`,
        name: 'Blocking appointment',
      },
    ],
  });

  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById(TINT_FULL_COUPE_CARBON);
  await widget.clickNext();
  await widget.pickCalendarDate(target);
  await widget.clickNext();

  // No slots should be available because both qualified techs are booked solid.
  const slotCount = await widget.timeSlots().count();
  expect(slotCount).toBe(0);
});

test('multi-day service (>5 hours) renders availability', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  // svc_multiday_ceramic is a 6-hour service. tech_cam handles Vinyl.
  await widget.selectServiceById('svc_multiday_ceramic');
  await widget.clickNext();

  const target = nextWeekday(1);
  await widget.pickCalendarDate(target);

  // Wait for /availability to land before advancing - tells us the request
  // happened and didn't error.
  const availabilityResponse = page.waitForResponse(
    (resp) => resp.url().includes('/availability') && resp.status() === 200,
  );
  await widget.clickNext();
  await availabilityResponse;

  // Wait for the first time slot to render before asserting on count.
  await expect(widget.timeSlots().first()).toBeVisible({ timeout: 5_000 });
  expect(await widget.timeSlots().count()).toBeGreaterThanOrEqual(1);
});
