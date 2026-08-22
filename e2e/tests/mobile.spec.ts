/**
 * Mobile-only specs (tagged @mobile so they only run on the mobile-chromium
 * project). Covers responsive layout sanity and the iframe height postMessage
 * channel used for the Shopify embed.
 */

import { expect, test } from '@playwright/test';
import { resetToDefaults } from '../helpers/scenarios';
import { WidgetPage } from '../helpers/widget';

test.beforeEach(async ({ request }) => {
  await resetToDefaults(request);
});

test('widget content does not overflow viewport at mobile width @mobile', async ({ page }) => {
  const widget = new WidgetPage(page);
  await widget.goto();

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  const widthCap = viewport!.width;

  const bodyWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  // Allow 1px for sub-pixel rendering rounding.
  expect(bodyWidth).toBeLessThanOrEqual(widthCap + 1);
});

test('widget posts iframe height messages to the parent @mobile', async ({ page, baseURL }) => {
  // Stand up an embedding page in-memory that captures postMessage calls.
  // The widget sends { type: 'scheduler-resize', height: <px> } on every
  // significant content change (see widget.js:notifyParentHeight).
  // This test never navigates, so page.url() is still 'about:blank' and can
  // never carry the server port (new URL('about:blank').port === ''). Derive
  // the origin from the config's baseURL so the embed follows E2E_PORT.
  // No literal fallback here on purpose: a hardcoded default is what silently
  // pinned this test to 8081 in the first place.
  if (!baseURL) throw new Error('baseURL must be set in playwright.config.ts');
  const widgetOrigin = new URL(baseURL).origin;
  const embedHtml = `
    <!doctype html>
    <html><body style="margin:0;padding:0;">
      <script>
        window.__messages = [];
        window.addEventListener('message', (e) => {
          window.__messages.push(e.data);
        });
      </script>
      <iframe id="f" src="${widgetOrigin}/" style="width:100%;height:600px;border:0;"></iframe>
    </body></html>
  `;
  await page.setContent(embedHtml);
  await page.waitForFunction(
    () => (window as unknown as { __messages: Array<{ type?: string }> }).__messages.some(
      (m) => typeof m === 'object' && m !== null && m.type === 'scheduler-resize',
    ),
    null,
    { timeout: 15_000 },
  );
  const messages = await page.evaluate(
    () => (window as unknown as { __messages: Array<{ type?: string; height?: number }> }).__messages,
  );
  const resizeMsg = messages.find(
    (m) => typeof m === 'object' && m !== null && m.type === 'scheduler-resize',
  );
  expect(resizeMsg).toBeDefined();
  expect(typeof resizeMsg!.height).toBe('number');
  expect(resizeMsg!.height).toBeGreaterThan(0);
});
