const {
  preflightLessonStudioE2EStack,
  resetLessonStudioE2EState,
} = require('../../scripts/reset-lesson-studio-e2e-state.cjs');

module.exports = async function globalSetup() {
  preflightLessonStudioE2EStack();
  resetLessonStudioE2EState();
};
