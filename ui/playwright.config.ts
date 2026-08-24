import { defineConfig } from "@playwright/test";

/** End-to-end against the REAL stack — real API, real Temporal Cloud, real
 * Bedrock (docs/DECISIONS.md, 2026-08-21). Nothing here is mocked, which is
 * why the timeouts are generous: a flood job is a real model call.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 60_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    viewport: { width: 1920, height: 1080 },
    trace: "retain-on-failure",
  },
});
