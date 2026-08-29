const base = require('./playwright.config');

module.exports = {
  ...base,
  testMatch: ['assignment-rollback-phase.spec.js'],
  use: { ...base.use, ignoreHTTPSErrors: true },
  projects: base.projects.filter((project) => project.name === 'course-mode-webkit-desktop'),
};
