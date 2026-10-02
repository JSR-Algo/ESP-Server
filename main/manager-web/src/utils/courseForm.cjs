// BE-01 TEXT contract: preserve accepted text; no locale/key allowlist.
function validateCourseForm(form, clone = false) {
  const errors = {};
  for (const field of clone ? ['courseKey', 'title'] : ['courseKey', 'title', 'locale']) {
    if (typeof form[field] !== 'string' || !form[field].trim() || form[field].includes('\0')) errors[field] = 'course.invalidText';
  }
  if (!clone) {
    const match = typeof form.ageBand === 'string' && /^(\d+)-(\d+)$/.exec(form.ageBand.trim());
    if (!match || Number(match[1]) > Number(match[2]) || Number(match[2]) > 18) errors.ageBand = 'course.invalidAgeBand';
  }
  return errors;
}
function mutationDetails(response) {
  const body = response && (response.data || (response.response && response.response.data));
  return body && body.details || {};
}
function uncertainMutation(response) {
  const status = Number(response && (response.status ?? (response.response && response.response.status)));
  return !status || status >= 500;
}
module.exports = { validateCourseForm, mutationDetails, uncertainMutation };
