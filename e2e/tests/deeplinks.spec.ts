/**
 * Deeplink preselection via ?service=<id> or ?service_name=<partial>.
 */

import { expect, test } from '@playwright/test';
import { resetToDefaults } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('?service= preselects by ID and switches to the right category', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto('?service=svc_tint_full_xl_suv_van_7_window_carbon');

  await expect(widget.categoryTab('Window Tint')).toHaveClass(/active/);
  const selectedCard = page.locator(
    '.service-card.selected[data-service-id="svc_tint_full_xl_suv_van_7_window_carbon"]',
  );
  await expect(selectedCard).toBeVisible();
});

test('?service_name= matches by partial case-insensitive name', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto('?service_name=xl%20suv');

  // The widget will select the first matching service. Just verify some
  // tint card got selected and the tab is right.
  await expect(widget.categoryTab('Window Tint')).toHaveClass(/active/);
  const selected = page.locator('.service-card.selected');
  await expect(selected).toBeVisible();
});

test('?service= with an unknown ID falls back to the catalog', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto('?service=does_not_exist');

  // No card should be selected, but the catalog should still render.
  await expect(page.locator('.service-card.selected')).toHaveCount(0);
  await expect(widget.serviceCards().first()).toBeVisible();
});
