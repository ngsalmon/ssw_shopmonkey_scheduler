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

test('widget posts iframe height messages to the parent @mobile', async ({ page }) => {
  // Stand up an embedding page in-memory that captures postMessage calls.
  // The widget sends { type: 'scheduler-resize', height: <px> } on every
  // significant content change (see widget.js:notifyParentHeight).
  const port = new URL(page.url() || 'http://127.0.0.1:8081/').port || '8081';
  const embedHtml = `
    <!doctype html>
    <html><body style="margin:0;padding:0;">
      <script>
        window.__messages = [];
        window.addEventListener('message', (e) => {
          window.__messages.push(e.data);
        });
      </script>
      <iframe id="f" src="http://127.0.0.1:${port}/" style="width:100%;height:600px;border:0;"></iframe>
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
