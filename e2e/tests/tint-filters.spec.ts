/**
 * Regression coverage for Anne's report (2026-05-19):
 *   "It doesn't show XL SUV/Van under the Full Vehicle, but does show it under Any."
 *
 * Root cause: the Full Vehicle chip filtered by a single specific tintArea
 * string (e.g. "Full Sedan") rather than by the group. The fix introduced
 * getTintAreaGroup() and made applyFilters compare by group label.
 *
 * These tests lock in the fix so it can't silently regress.
 */

import { expect, test } from '@playwright/test';
import { resetToDefaults } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('Full Vehicle filter shows every vehicle size including XL SUV/Van', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Window Tint');
  await widget.applyFilter('tintArea', 'Full Vehicle');

  const titles = await widget.serviceCardTitles();

  // Every Full-* variant from the default fixture should be visible.
  expect(titles.some((t) => /full coupe/i.test(t))).toBe(true);
  expect(titles.some((t) => /full sedan/i.test(t))).toBe(true);
  expect(titles.some((t) => /full suv/i.test(t)) && !titles.every((t) => /xl/i.test(t))).toBe(true);
  expect(titles.some((t) => /full xl suv\/?van/i.test(t))).toBe(true);

  // And no non-Full areas should leak through (e.g. Windshield, Front Doors).
  expect(titles.every((t) => /full/i.test(t))).toBe(true);
});

test('Any filter shows every tint area (control case)', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Window Tint');
  // "Any" is the active state when no tintArea value is set; just verify
  // every area is present without clicking a chip.
  const titles = await widget.serviceCardTitles();

  expect(titles.some((t) => /full coupe/i.test(t))).toBe(true);
  expect(titles.some((t) => /full xl suv\/?van/i.test(t))).toBe(true);
  expect(titles.some((t) => /windshield/i.test(t))).toBe(true);
  expect(titles.some((t) => /sunstrip/i.test(t))).toBe(true);
  expect(titles.some((t) => /front doors|front/i.test(t))).toBe(true);
});

test('Windshield filter excludes Sunstrip even though both contain that word in display', async ({ page }) => {
  // getTintAreaGroup() has explicit !lower.includes('strip') guard.
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Window Tint');
  await widget.applyFilter('tintArea', 'Windshield');

  const titles = await widget.serviceCardTitles();
  expect(titles.length).toBeGreaterThan(0);
  expect(titles.every((t) => /windshield/i.test(t) && !/sunstrip/i.test(t))).toBe(true);
});

test('Ceramic tint type filter combines with Full Vehicle filter', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Window Tint');
  await widget.applyFilter('tintArea', 'Full Vehicle');
  await widget.applyFilter('tintType', 'ceramic');

  const titles = await widget.serviceCardTitles();
  // All four sizes should still be present but only Ceramic, no Carbon.
  expect(titles.some((t) => /full xl suv\/?van/i.test(t))).toBe(true);
  expect(titles.some((t) => /full coupe/i.test(t))).toBe(true);

  // Check the card-level badge to confirm Ceramic, not Carbon.
  const cards = widget.serviceCards();
  const count = await cards.count();
  for (let i = 0; i < count; i++) {
    const ceramicBadge = cards.nth(i).locator('.badge-ceramic');
    const carbonBadge = cards.nth(i).locator('.badge-carbon');
    expect(await ceramicBadge.count()).toBe(1);
    expect(await carbonBadge.count()).toBe(0);
  }
});

test('Selecting deeplink to an XL SUV/Van service pre-applies Full Vehicle filter', async ({ page }) => {
  // The fix also normalizes the parsed tintArea to a group label when used
  // by deeplink preselect. Verify that filtering is correct after deeplink.
  const widget = new WidgetPage(page);
  await widget.goto('?service=svc_tint_full_xl_suv_van_7_window_ceramic');

  // After preselect, Window Tint should be the active category and the Full
  // Vehicle chip should be visually active.
  await expect(widget.categoryTab('Window Tint')).toHaveClass(/active/);
  const fullVehicleChip = widget.filterChip('tintArea', 'Full Vehicle');
  await expect(fullVehicleChip).toHaveClass(/active/);

  // The XL SUV/Van card should be visible (regression-proof).
  const titles = await widget.serviceCardTitles();
  expect(titles.some((t) => /full xl suv\/?van/i.test(t))).toBe(true);
});
