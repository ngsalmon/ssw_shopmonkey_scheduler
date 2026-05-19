/**
 * Service catalog: category tabs, search, "no results" state.
 */

import { expect, test } from '@playwright/test';
import { resetToDefaults, setScenario } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('category tabs render for each present category', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  // Default fixture has Window Tint, Bedliner, Alignment, Detail (only
  // Headlight Restoration via exception), plus a Vinyl-labeled service that
  // falls into "Other".
  await expect(widget.categoryTab('Window Tint')).toBeVisible();
  await expect(widget.categoryTab('Bedliner')).toBeVisible();
  await expect(widget.categoryTab('Alignment')).toBeVisible();
  await expect(widget.categoryTab('Detail')).toBeVisible();
});

test('search filters services across categories', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  await widget.search('xl suv');
  const titles = await widget.serviceCardTitles();
  expect(titles.length).toBeGreaterThan(0);
  // Only XL SUV/Van services should match (case-insensitive substring).
  expect(titles.every((t) => /xl suv\/?van/i.test(t))).toBe(true);
});

test('empty fixture renders no-services state gracefully', async ({ page, request }) => {
  await setScenario(request, { services: [] });
  // Register the response wait BEFORE navigating so we don't miss it.
  const servicesResponse = page.waitForResponse(
    (resp) => resp.url().includes('/services') && resp.status() === 200,
  );
  await page.goto('/');
  await servicesResponse;
  await expect(page.locator('#servicesContainer')).toContainText(/no/i, { timeout: 5_000 });
});
