import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

const PORT = Number(process.env.E2E_PORT ?? 8081);
const BASE_URL = `http://127.0.0.1:${PORT}`;
const REPO_ROOT = path.resolve(__dirname, '..');

export default defineConfig({
  testDir: './tests',
  fullyParallel: false, // mock state is shared across tests in a run
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 30_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chromium',
      // Pixel 7 is a chromium-based mobile device descriptor (touch, mobile
      // viewport, mobile UA). We use it instead of iPhone 14 because the
      // iPhone device's `defaultBrowserType` is webkit, which we don't
      // install on CI per the harness design decision.
      use: { ...devices['Pixel 7'] },
      // Mobile only runs the specs explicitly tagged @mobile.
      grep: /@mobile/,
    },
  ],

  webServer: {
    // Use the project venv if it exists (matches scripts/setup-dev.sh), then
    // fall back to whichever python is on PATH. The shell glob expansion
    // happens because we wrap in `sh -c` via Playwright.
    command: `sh -c '[ -x "${REPO_ROOT}/.venv/bin/python" ] && PY="${REPO_ROOT}/.venv/bin/python" || PY=python3; exec "$PY" -m uvicorn main:app --port ${PORT} --log-level warning'`,
    cwd: REPO_ROOT,
    url: `${BASE_URL}/health`,
    timeout: 30_000,
    reuseExistingServer: !process.env.CI,
    env: {
      E2E_MODE: '1',
      // The clients are never created in E2E_MODE, but the lifespan still
      // checks the env vars exist on import. Provide stubs.
      SHOPMONKEY_API_TOKEN: 'e2e-stub',
      GOOGLE_SHEETS_ID: 'e2e-stub',
      PYTHONPATH: REPO_ROOT,
    },
  },
});
