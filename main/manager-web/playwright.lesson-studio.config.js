const { defineConfig, devices } = require('@playwright/test');
const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');

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
    baseURL: lessonStudioWebOrigin(),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    serviceWorkers: 'block',
    ...devices['Desktop Chrome'],
    channel: process.env.LESSON_STUDIO_E2E_BROWSER_CHANNEL || undefined,
  },
});
