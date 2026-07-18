const { defineConfig } = require('@playwright/test');
const path = require('node:path');

const artifactDir = process.env.REWARDS_ADMIN_ARTIFACT_DIR
  || path.resolve(__dirname, 'output/playwright/rewards-admin-roundtrip');

module.exports = defineConfig({
  testDir: './e2e',
  testMatch: /rewards-admin-roundtrip\.spec\.js/,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.join(artifactDir, 'test-results'),
  reporter: [['line']],
  use: {
    baseURL: process.env.REWARDS_ADMIN_BASE_URL,
    browserName: 'chromium',
    headless: true,
    viewport: { width: 1440, height: 1100 },
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    screenshot: 'off',
    trace: 'off',
    serviceWorkers: 'block',
  },
});
