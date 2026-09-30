function changedActivityDuration(activity) {
  const shorter = activity.expectedDurationSec - 1;
  return shorter > activity.responseStartSec ? shorter : activity.expectedDurationSec + 1;
}

module.exports = { changedActivityDuration };
