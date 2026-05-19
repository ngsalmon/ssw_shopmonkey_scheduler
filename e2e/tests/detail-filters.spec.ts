/**
 * Detail category filters: vehicle size + service type.
 *
 * The default config disables the Detail department except for Headlight
 * Restoration. To test the full Detail filter set we enable all departments.
 */

import { expect, test } from '@playwright/test';
import { enableAllDepartments, resetToDefaults } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
  await enableAllDepartments(request);
});

test('vehicle size filter narrows Detail services to that size', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Detail');
  await widget.applyFilter('vehicleSize', 'xlsuv');

  const titles = await widget.serviceCardTitles();
  expect(titles.length).toBeGreaterThan(0);
  // Detail services render as "Interior Level 1" etc, with the vehicle size
  // in a .vehicle-size element separate from the title.
  const sizes = await widget.serviceCardVehicleSizes();
  expect(sizes.every((s) => /xl suv\/?van/i.test(s))).toBe(true);
});

test('service type filter narrows by Interior vs Exterior', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Detail');
  await widget.applyFilter('serviceType', 'interior');

  const titles = await widget.serviceCardTitles();
  expect(titles.length).toBeGreaterThan(0);
  expect(titles.every((t) => /interior/i.test(t))).toBe(true);
});

test('combined filters: XL SUV/Van + Exterior', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.selectCategory('Detail');
  await widget.applyFilter('vehicleSize', 'xlsuv');
  await widget.applyFilter('serviceType', 'exterior');

  const sizes = await widget.serviceCardVehicleSizes();
  const titles = await widget.serviceCardTitles();
  expect(sizes.length).toBeGreaterThan(0);
  expect(sizes.every((s) => /xl suv\/?van/i.test(s))).toBe(true);
  expect(titles.every((t) => /exterior/i.test(t))).toBe(true);
});
