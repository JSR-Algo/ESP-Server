const test = require('node:test');
const assert = require('node:assert/strict');
const { changedActivityDuration } = require('../../e2e/lesson-studio/helpers/course-mode-edit-values');

test('a repeated real-journey edit preserves the response window', () => {
  const activity = { expectedDurationSec: 11, responseStartSec: 10 };
  for (let edit = 0; edit < 30; edit += 1) {
    const duration = changedActivityDuration(activity);
    assert.ok(duration > activity.responseStartSec, 'response must start strictly before activity end');
    assert.notEqual(duration, activity.expectedDurationSec, 'the journey must exercise an actual edit');
    assert.ok(Number.isSafeInteger(duration));
    activity.expectedDurationSec = duration;
  }
});

test('an activity with spare response time is shortened by one second', () => {
  assert.equal(changedActivityDuration({ expectedDurationSec: 30, responseStartSec: 10 }), 29);
  assert.equal(changedActivityDuration({ expectedDurationSec: 30, responseStartSec: 0 }), 29);
});
