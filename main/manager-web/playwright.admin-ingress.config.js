const { defineConfig } = require('@playwright/test');
const lessonStudioConfig = require('./playwright.lesson-studio.config');

// Production-mode role qualification has different prerequisites from the
// diagnostic manager + Nest two-login suite. Keep both gates explicit.
module.exports = defineConfig({
  ...lessonStudioConfig,
  testDir: './e2e/admin-ingress',
});
