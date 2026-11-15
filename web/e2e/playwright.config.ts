import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3101);
const BASE = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;

// Runs against mock mode (NEXT_PUBLIC_HEARTH_MOCK=1): no backend, no calls, no keys.
// Set E2E_BASE_URL to test an already running server instead.
export default defineConfig({
  testDir: ".",
  outputDir: "./.results",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0, // one focus test is timing-sensitive on shared runners
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: BASE,
    trace: "retain-on-failure",
    timezoneId: "America/New_York",
    locale: "en-US",
  },
  projects: [
    {
      name: "phone",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true },
    },
    {
      name: "laptop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `NEXT_PUBLIC_HEARTH_MOCK=1 pnpm exec next build && pnpm exec next start -p ${PORT} -H 127.0.0.1`,
        url: `${BASE}/login`,
        cwd: "..",
        timeout: 180_000,
        reuseExistingServer: true,
      },
});
