const { defineConfig, devices } = require('@playwright/test');
const path = require('node:path');
const { deployedReadOnlyTargets } = require('./e2e/admin-course-deployed/targets.cjs');

const targets = deployedReadOnlyTargets(process.env);
const output = process.env.ADMIN_COURSE_DEPLOYED_OUTPUT_ROOT;
if (!output || !path.isAbsolute(output)) throw new Error('An explicit absolute deployed output root is required');

// Anonymous supporting smoke only. No storageState, globalSetup, local auth
// helper, credential defaults or reset. Authenticated/mutating qualification
// remains blocked until a verified test account and isolated scope are available.
module.exports = defineConfig({
  testDir: './e2e/admin-course-deployed',
  testMatch: 'course-journey.spec.js',
  outputDir: path.join(output, 'results'),
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  reporter: [['list'], ['json', { outputFile: path.join(output, 'report.json') }]],
  use: {
    baseURL: targets.web,
    ignoreHTTPSErrors: false,
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'deployed-chromium-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'deployed-webkit-desktop', use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 900 } } },
    { name: 'deployed-chromium-mobile', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } } },
    { name: 'deployed-webkit-mobile', use: { ...devices['iPhone 13'], viewport: { width: 390, height: 844 } } },
  ],
});
