import { defineConfig, devices } from "@playwright/test";
const port = process.env.PLAYWRIGHT_PORT ?? "5173";
export default defineConfig({
  testDir: "tests/e2e",
  outputDir: "test-results/e2e",
  fullyParallel: true,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.env.CI ? undefined : "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run dev -- --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
