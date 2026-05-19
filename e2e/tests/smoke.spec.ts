/**
 * Smoke test: harness boots, /health works, /services returns data, /test/state
 * is reachable, and the widget renders. Establishes the floor for all other
 * specs.
 */

import { expect, test } from '@playwright/test';
import { getState, resetToDefaults } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('health endpoint responds', async ({ request }) => {
  const response = await request.get('/health');
  expect(response.ok()).toBeTruthy();
  expect(await response.json()).toMatchObject({ status: 'healthy' });
});

test('test/state endpoint reports default fixture', async ({ request }) => {
  const state = await getState(request);
  expect(state.services.length).toBeGreaterThan(0);
  expect(state.techs.length).toBeGreaterThan(0);
});

test('widget loads and renders category tabs', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();
  // Default fixture has Window Tint + Bedliner + Alignment + Headlight enabled.
  await expect(widget.categoryTab('Window Tint')).toBeVisible();
});
