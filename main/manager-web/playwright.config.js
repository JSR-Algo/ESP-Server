const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e/lesson-studio',
  globalSetup: './e2e/lesson-studio/global-setup.cjs',
  outputDir: './output/playwright-e2e/results',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: './output/playwright-e2e/report', open: 'never' }]],
  use: {
    baseURL: process.env.LESSON_STUDIO_E2E_BASE_URL || 'http://127.0.0.1:8102',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    serviceWorkers: 'block',
    ...devices['Desktop Chrome'],
  },
});
