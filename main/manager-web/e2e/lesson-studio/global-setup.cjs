const { resetLessonStudioE2EState } = require('../../scripts/reset-lesson-studio-e2e-state.cjs');

module.exports = async function globalSetup() {
  resetLessonStudioE2EState();
};
