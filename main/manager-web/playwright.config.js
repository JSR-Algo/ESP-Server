const { defineConfig, devices } = require('@playwright/test');
const path = require('node:path');
const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');
const outputRoot = path.resolve(process.env.LESSON_STUDIO_E2E_OUTPUT_ROOT || './output', 'playwright-course-mode');

module.exports = defineConfig({
  testDir: "./e2e/lesson-studio",
  globalSetup: "./e2e/lesson-studio/global-setup.cjs",
  testMatch: ["course-mode-authoring.spec.js", "course-mode-insights.spec.js", "course-mode-lifecycle.spec.js", "course-mode-real-journey.spec.js"],
  outputDir: path.join(outputRoot, 'results'),
  timeout: 60000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", {"open": "never", "outputFolder": path.join(outputRoot, 'report')}]],
  use: {
    baseURL: lessonStudioWebOrigin(),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    serviceWorkers: "block",
  },
  projects: [
    {
      name: "course-mode-chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "course-mode-webkit-desktop",
      use: {
        ...devices["Desktop Safari"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "course-mode-chromium-mobile",
      use: {
        ...devices["Pixel 7"],
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: "course-mode-webkit-mobile",
      use: {
        ...devices["iPhone 13"],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
});
