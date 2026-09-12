const { defineConfig } = require('@playwright/test');
const path = require('node:path');
const courseModeConfig = require('./playwright.config');
const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');
const outputRoot = path.resolve(process.env.LESSON_STUDIO_E2E_OUTPUT_ROOT || './output', 'playwright-e2e');

module.exports = defineConfig({
  testDir: './e2e/lesson-studio',
  globalSetup: './e2e/lesson-studio/global-setup.cjs',
  outputDir: path.join(outputRoot, 'results'),
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: path.join(outputRoot, 'report'), open: 'never' }]],
  projects: courseModeConfig.projects.map(project => ({ ...project, name: project.name.replace('course-mode-', 'lesson-studio-') })),
  use: {
    baseURL: lessonStudioWebOrigin(),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    serviceWorkers: 'block',
  },
});
