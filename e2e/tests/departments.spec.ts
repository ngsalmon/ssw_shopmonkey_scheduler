/**
 * Department behavior: disabling, exceptions, deferred-active state for techs.
 */

import { expect, test } from '@playwright/test';
import { nextWeekday } from '../helpers/dates';
import { resetToDefaults, setScenario } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('disabled Detail department hides services except Headlight Restoration', async ({
  page,
}) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Detail');
  const titles = await widget.serviceCardTitles();
  // Only Headlight Restoration should appear under Detail by default.
  expect(titles.length).toBe(1);
  expect(titles[0]).toMatch(/headlight restoration/i);
});

test('clearing disabled_departments brings back all Detail services', async ({
  page,
  request,
}) => {
  await setScenario(request, { config: { disabled_departments: {} } });
  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectCategory('Detail');
  const titles = await widget.serviceCardTitles();
  expect(titles.length).toBeGreaterThan(1);
  expect(titles.some((t) => /interior/i.test(t))).toBe(true);
  expect(titles.some((t) => /exterior/i.test(t))).toBe(true);
});

test('tech inactive in Shopmonkey is excluded from availability even if active in sheet', async ({
  page,
  request,
}) => {
  // Deactivate every Tint-qualified tech in Shopmonkey. The widget should
  // report no availability when checking a tint service.
  await setScenario(request, {
    techs: [
      {
        tech_id: 'tech_alex',
        tech_name: 'Alex Tint',
        departments: { Tint: 1 },
        status: 'Active',
        active_in_shopmonkey: false,
      },
      {
        tech_id: 'tech_cam',
        tech_name: 'Cam Multi',
        departments: { Tint: 2 },
        status: 'Active',
        active_in_shopmonkey: false,
      },
    ],
  });

  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById('svc_tint_full_coupe_carbon');
  await widget.clickNext();
  // /availability is called when a calendar date is picked. Backend returns
  // 404 "No availability for this service" because no tech is qualified+active.
  await widget.pickCalendarDate(nextWeekday(1));
  // Advance to step 3 (Time slots) - this triggers /availability, which
  // returns 404 because no tech is qualified+active. fetchAvailability shows
  // a toast on error.
  await widget.clickNext();
  await expect(page.locator('#toastContainer .toast')).toBeVisible({ timeout: 5_000 });
});

test('sheet Status=Inactive excludes tech even when active in Shopmonkey', async ({
  page,
  request,
}) => {
  await setScenario(request, {
    techs: [
      {
        tech_id: 'tech_alex',
        tech_name: 'Alex Tint',
        departments: { Tint: 1 },
        status: 'Inactive',
        active_in_shopmonkey: true,
      },
      {
        tech_id: 'tech_cam',
        tech_name: 'Cam Multi',
        departments: { Tint: 2 },
        status: 'Inactive',
        active_in_shopmonkey: true,
      },
    ],
  });

  const widget = new WidgetPage(page);
  await widget.goto();
  await widget.selectServiceById('svc_tint_full_coupe_carbon');
  await widget.clickNext();
  await widget.pickCalendarDate(nextWeekday(1));
  // Advance to step 3 (Time slots) - this triggers /availability, which
  // returns 404 because no tech is qualified+active. fetchAvailability shows
  // a toast on error.
  await widget.clickNext();
  await expect(page.locator('#toastContainer .toast')).toBeVisible({ timeout: 5_000 });
});
