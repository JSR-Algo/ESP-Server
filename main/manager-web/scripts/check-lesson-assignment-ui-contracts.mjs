import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function source(path) {
  return readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
}

function expectContains(path, needle, message) {
  const text = source(path);
  if (!text.includes(needle)) {
    throw new Error(`${message}: expected ${path} to contain ${needle}`);
  }
}

function expectMatches(path, pattern, message) {
  const text = source(path);
  if (!pattern.test(text)) {
    throw new Error(`${message}: expected ${path} to match ${pattern}`);
  }
}

expectContains('src/views/CourseLessons.vue', "scope.row.status === 'published'", 'assignment CTA must be published-only');
expectContains('src/views/CourseLessons.vue', "lesson.assignToChild", 'assignment CTA must be localized');
expectContains('src/views/CourseLessons.vue', 'Api.courseInsights.listLearners', 'dialog must use learner search API');
expectContains('src/views/CourseLessons.vue', ':disabled="assignmentDialog.submitting"', 'child selection must be locked during assignment POST/reconcile');
expectContains('src/views/CourseLessons.vue', 'eligibleDevices', 'dialog must reload eligible devices after child selection');
expectContains('src/views/CourseLessons.vue', 'row.lessonKey', 'assignment must use lessonKey, not row UUID');
expectContains('src/views/CourseLessons.vue', "lessonId: submitContext.lessonKey", 'create payload must send captured lesson key');
expectContains('src/views/CourseLessons.vue', "profile: 'espTft'", 'create payload must use espTft profile');
expectContains('src/views/CourseLessons.vue', "device.availability === 'busy'", 'busy devices must be disabled');
expectContains('src/views/CourseLessons.vue', 'assignmentDialog.submitting', 'double-submit guard must be present');
expectContains('src/views/CourseLessons.vue', 'assignmentDialog.submitRequestId', 'assignment callbacks must be guarded by submit request id');
expectContains('src/views/CourseLessons.vue', 'assignmentDialog.submitContext', 'assignment callbacks must use immutable submit context');
expectContains('src/views/CourseLessons.vue', 'created === false', 'created:false must be treated as successful reuse');
expectContains('src/views/CourseLessons.vue', 'refreshAssignmentEligibility', '409 and uncertain results must refresh eligibility');
expectContains('src/views/CourseLessons.vue', 'reconcileAssignmentAfterUncertainCreate', 'network/timeout errors after POST must reconcile before retry');
expectContains('src/views/CourseLessons.vue', 'resetAssignmentDialog', 'dialog state must reset between rows/children');
expectContains('src/apis/module/lesson.js', 'eligibleDevices', 'lesson API must expose eligibleDevices');
expectContains('src/apis/module/lesson.js', '/lesson-assignments/eligible-devices', 'eligible API must use admin path');
expectContains('src/apis/module/lesson.js', 'createAssignment', 'lesson API must expose createAssignment');
expectContains('src/apis/module/lesson.js', '/lesson-assignments', 'create API must use admin lesson assignments path');
expectMatches('src/apis/module/lesson.js', /throw new Error\('INVALID_ELIGIBLE_DEVICES_RESPONSE'\)/, 'eligible response must be strictly rejected');
expectMatches('src/apis/module/lesson.js', /throw new Error\('INVALID_ASSIGNMENT_RESPONSE'\)/, 'create response must be strictly rejected');
expectContains('src/i18n/en.js', "'lesson.assignToChild'", 'English assignment label required');
expectContains('src/i18n/vi.js', "'lesson.assignToChild'", 'Vietnamese assignment label required');
expectContains('package.json', 'test:lesson-assignment-ui', 'focused assignment UI script must be wired');
expectContains('package.json', 'npm run test:lesson-assignment-ui', 'lesson-studio suite must include assignment UI script');

function extractFunctionDeclaration(sourceText, name) {
  const marker = `function ${name}(`;
  const start = sourceText.indexOf(marker);
  if (start < 0) throw new Error(`${name} function not found`);
  const braceStart = sourceText.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < sourceText.length; i += 1) {
    if (sourceText[i] === '{') depth += 1;
    if (sourceText[i] === '}' && --depth === 0) {
      return sourceText.slice(start, i + 1);
    }
  }
  throw new Error(`${name} function body not closed`);
}

function extractExportedFunction(sourceText, name) {
  return extractFunctionDeclaration(sourceText.replace(`export function ${name}`, `function ${name}`), name);
}

function extractConstLine(sourceText, name) {
  const pattern = new RegExp(`const ${name} = [^;]+;`);
  const match = pattern.exec(sourceText);
  if (!match) throw new Error(`${name} const not found`);
  return match[0];
}

function extractObjectMethod(sourceText, name) {
  const methodPattern = new RegExp(`\\n\\s{4}${name}\\(`);
  const match = methodPattern.exec(sourceText);
  if (!match) throw new Error(`${name} method not found`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = sourceText.indexOf('(', start);
  const paramsEnd = sourceText.indexOf(')', paramsStart);
  const braceStart = sourceText.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < sourceText.length; i += 1) {
    if (sourceText[i] === '{') depth += 1;
    if (sourceText[i] === '}' && --depth === 0) {
      return `function ${name}(${sourceText.slice(paramsStart + 1, paramsEnd)}) {${sourceText.slice(braceStart + 1, i)}}`;
    }
  }
  throw new Error(`${name} method body not closed`);
}

const lessonApiSource = source('src/apis/module/lesson.js');
const normalizeAssignmentResponse = vm.runInNewContext(`(() => {
${extractConstLine(lessonApiSource, 'TIMESTAMP_PATTERN')}
${extractConstLine(lessonApiSource, 'ACTIVE_ASSIGNMENT_STATES')}
${extractFunctionDeclaration(lessonApiSource, 'validSafeCount')}
${extractFunctionDeclaration(lessonApiSource, 'validPositiveSafeInteger')}
${extractFunctionDeclaration(lessonApiSource, 'validNullableTimestamp')}
${extractExportedFunction(lessonApiSource, 'normalizeAssignmentResponse')}
return normalizeAssignmentResponse;
})()`);

const validAssignmentPayload = {
  assignment: {
    assignmentId: 'assignment-1',
    assignmentVersion: 1,
    deviceId: 'device-1',
    childId: 'child-1',
    lessonId: 'w01-d01-barn-say-it',
    lessonTitle: 'Barn',
    lessonVersion: 2,
    profile: 'espTft',
    state: 'ASSIGNED',
    createdAt: '2026-07-30T06:00:00.000Z',
  },
  created: true,
};

for (const [label, payload] of [
  ['missing assignmentVersion', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, assignmentVersion: undefined } }],
  ['zero assignmentVersion', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, assignmentVersion: 0 } }],
  ['invalid profile', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, profile: 'mobile' } }],
  ['invalid state', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, state: 'COMPLETED' } }],
  ['failed state', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, state: 'FAILED' } }],
  ['invalid timestamp', { ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, createdAt: '2026-99-99T99:99:99Z' } }],
]) {
  let rejected = false;
  try {
    normalizeAssignmentResponse(payload);
  } catch (error) {
    rejected = error && error.message === 'INVALID_ASSIGNMENT_RESPONSE';
  }
  if (!rejected) throw new Error(`normalizeAssignmentResponse must reject ${label}`);
}
normalizeAssignmentResponse({ ...validAssignmentPayload, assignment: { ...validAssignmentPayload.assignment, createdAt: null } });

const courseLessonsSource = source('src/views/CourseLessons.vue');
const apiShim = {};
const methods = vm.runInNewContext(`(() => ({
  searchAssignmentLearners: ${extractObjectMethod(courseLessonsSource, 'searchAssignmentLearners')},
  handleAssignmentChildChange: ${extractObjectMethod(courseLessonsSource, 'handleAssignmentChildChange')},
  refreshAssignmentEligibility: ${extractObjectMethod(courseLessonsSource, 'refreshAssignmentEligibility')},
  isAssignmentSubmitCurrent: ${extractObjectMethod(courseLessonsSource, 'isAssignmentSubmitCurrent')},
  isAssignmentEligibilityCurrent: ${extractObjectMethod(courseLessonsSource, 'isAssignmentEligibilityCurrent')},
  isAssignmentResponseExact: ${extractObjectMethod(courseLessonsSource, 'isAssignmentResponseExact')},
  releaseAssignmentSubmit: ${extractObjectMethod(courseLessonsSource, 'releaseAssignmentSubmit')},
  createLessonAssignment: ${extractObjectMethod(courseLessonsSource, 'createLessonAssignment')},
  reconcileAssignmentAfterUncertainCreate: ${extractObjectMethod(courseLessonsSource, 'reconcileAssignmentAfterUncertainCreate')},
  resetAssignmentDialog: ${extractObjectMethod(courseLessonsSource, 'resetAssignmentDialog')},
}))()`, {
  Api: apiShim,
  isUncertainNestError: (error) => error && error.transport === true,
  blankAssignmentDialog: () => makeDialog({ visible: false }),
});

function makeDialog(overrides = {}) {
  return {
    visible: true,
    lesson: { lessonKey: 'lesson-a', lessonVersion: 2, lessonId: 'lesson-uuid-a', title: 'Lesson A' },
    learners: [],
    childId: 'child-a',
    devices: [],
    loadingLearners: false,
    loadingDevices: false,
    submitting: false,
    submittingDeviceId: '',
    statusMessage: '',
    statusType: 'info',
    dialogSessionId: 0,
    learnerRequestToken: 0,
    deviceRequestToken: 0,
    submitRequestId: 0,
    submitContext: null,
    ...overrides,
  };
}

function makeContext(api) {
  const messages = [];
  return {
    assignmentDialog: makeDialog(),
    $t: (key) => key,
    $message: { error: (message) => messages.push({ type: 'error', message }) },
    setAssignmentStatus(type, key) {
      this.assignmentDialog.statusType = type;
      this.assignmentDialog.statusMessage = key || '';
    },
    isAssignmentSubmitCurrent: methods.isAssignmentSubmitCurrent,
    isAssignmentEligibilityCurrent: methods.isAssignmentEligibilityCurrent,
    isAssignmentResponseExact: methods.isAssignmentResponseExact,
    releaseAssignmentSubmit: methods.releaseAssignmentSubmit,
    refreshAssignmentEligibility: methods.refreshAssignmentEligibility,
    reconcileAssignmentAfterUncertainCreate: methods.reconcileAssignmentAfterUncertainCreate,
    resetAssignmentDialog: methods.resetAssignmentDialog,
    ...api,
    messages,
  };
}

{
  const submitContext = {
    childId: 'child-a',
    lessonKey: 'lesson-a',
    lessonVersion: 2,
    deviceId: 'device-a',
  };
  const assignment = {
    childId: 'child-a',
    lessonId: 'lesson-a',
    lessonVersion: 2,
    deviceId: 'device-a',
    profile: 'espTft',
    state: 'ASSIGNED',
  };
  if (!methods.isAssignmentResponseExact.call({}, submitContext, assignment)) {
    throw new Error('exact active assignment response must be accepted');
  }
  for (const [label, candidate] of [
    ['device', { ...assignment, deviceId: 'device-b' }],
    ['child', { ...assignment, childId: 'child-b' }],
    ['lesson', { ...assignment, lessonId: 'lesson-b' }],
    ['version', { ...assignment, lessonVersion: 3 }],
    ['profile', { ...assignment, profile: 'mobile' }],
    ['state', { ...assignment, state: 'FAILED' }],
  ]) {
    if (methods.isAssignmentResponseExact.call({}, submitContext, candidate)) {
      throw new Error(`mismatched ${label} assignment response must be rejected`);
    }
  }
}

{
  const calls = [];
  apiShim.lesson = {
    createAssignment(payload, onSuccess, onError) {
      calls.push({ payload, onSuccess, onError });
    },
    eligibleDevices(params, onSuccess) {
      calls.push({ eligible: params });
      onSuccess([]);
    },
  };
  const context = makeContext({
  });
  methods.createLessonAssignment.call(context, { deviceId: 'device-a', availability: 'available' });
  context.assignmentDialog.childId = 'child-b';
  methods.handleAssignmentChildChange.call(context);
  if (!context.assignmentDialog.submitting || calls.some((call) => call.eligible)) {
    throw new Error('child change during assignment POST must be ignored and keep submit lock active');
  }
  context.assignmentDialog.lesson = { lessonKey: 'lesson-b', lessonVersion: 9, lessonId: 'lesson-uuid-b', title: 'Lesson B' };
  calls[0].onError('timeout', { transport: true });
  const eligibleCall = calls.find((call) => call.eligible);
  if (JSON.stringify(eligibleCall?.eligible) !== JSON.stringify({
    childId: 'child-a',
    lessonId: 'lesson-a',
    lessonVersion: 2,
  })) {
    throw new Error(`uncertain reconcile must use captured submit context, got ${JSON.stringify(eligibleCall?.eligible)}`);
  }
}

{
  const calls = [];
  let eligibilityRefreshes = 0;
  apiShim.lesson = {
    createAssignment(payload, onSuccess, onError) {
      calls.push({ payload, onSuccess, onError });
    },
    eligibleDevices(params, onSuccess) {
      eligibilityRefreshes += 1;
      onSuccess([]);
    },
  };
  const context = makeContext({});
  methods.createLessonAssignment.call(context, { deviceId: 'device-a', availability: 'available' });
  calls[0].onSuccess({
    created: false,
    assignment: {
      assignmentId: 'stale-active-assignment',
      assignmentVersion: 4,
      deviceId: 'device-a',
      childId: 'different-child',
      lessonId: 'lesson-a',
      lessonVersion: 2,
      profile: 'espTft',
      state: 'ASSIGNED',
      createdAt: null,
    },
  });
  if (context.assignmentDialog.statusType === 'success') {
    throw new Error('created:false must not accept an active assignment for a different child');
  }
  if (context.assignmentDialog.submitting || eligibilityRefreshes !== 1) {
    throw new Error('mismatched assignment response must release submit lock and refresh eligibility');
  }
}

{
  const calls = [];
  apiShim.lesson = {
    createAssignment(payload, onSuccess, onError) {
      calls.push({ payload, onSuccess, onError });
    },
    eligibleDevices() {
      throw new Error('stale callback after reset must not reconcile');
    },
  };
  const context = makeContext({
  });
  methods.createLessonAssignment.call(context, { deviceId: 'device-a', availability: 'available' });
  methods.resetAssignmentDialog.call(context);
  calls[0].onSuccess({ created: true });
  if (context.assignmentDialog.statusMessage || context.assignmentDialog.submittingDeviceId) {
    throw new Error('stale success callback after dialog reset must be ignored');
  }
}

{
  const calls = [];
  apiShim.courseInsights = {
    listLearners(params, onSuccess, onError) {
      calls.push({ type: 'learners', params, onSuccess, onError });
    },
  };
  apiShim.lesson = {
    eligibleDevices(params, onSuccess, onError) {
      calls.push({ type: 'devices', params, onSuccess, onError });
    },
  };
  const context = makeContext({});

  context.assignmentDialog.visible = true;
  context.assignmentDialog.lesson = { lessonKey: 'lesson-a', lessonVersion: 1, lessonId: 'lesson-a-id', title: 'Lesson A' };
  context.assignmentDialog.childId = 'child-a';
  methods.searchAssignmentLearners.call(context, 'a');
  methods.refreshAssignmentEligibility.call(context);
  const staleLearners = calls.find((call) => call.type === 'learners');
  const staleDevices = calls.find((call) => call.type === 'devices');

  methods.resetAssignmentDialog.call(context);
  context.assignmentDialog.visible = true;
  context.assignmentDialog.lesson = { lessonKey: 'lesson-b', lessonVersion: 2, lessonId: 'lesson-b-id', title: 'Lesson B' };
  context.assignmentDialog.childId = 'child-b';
  methods.searchAssignmentLearners.call(context, 'b');
  methods.refreshAssignmentEligibility.call(context);
  const freshLearners = calls.filter((call) => call.type === 'learners')[1];
  const freshDevices = calls.filter((call) => call.type === 'devices')[1];

  freshLearners.onSuccess([{ childId: 'child-b', childName: 'Bee' }]);
  freshDevices.onSuccess([{ deviceId: 'device-b', availability: 'available', currentAssignment: null }]);
  staleLearners.onSuccess([{ childId: 'child-a', childName: 'Aye' }]);
  staleDevices.onSuccess([{ deviceId: 'device-a', availability: 'available', currentAssignment: null }]);

  if (context.assignmentDialog.learners[0]?.childId !== 'child-b') {
    throw new Error('stale learner callback after close/reopen must not overwrite fresh dialog learners');
  }
  if (context.assignmentDialog.devices[0]?.deviceId !== 'device-b') {
    throw new Error('stale eligibility callback after close/reopen must not overwrite fresh dialog devices');
  }
}

// ---------------------------------------------------------------------------
// Monitoring console (T4.2): the assignment list and the per-assignment event
// timeline are read while lessons are live, so overlapping reads settle out of
// order. A superseded response must never repaint the view — a stale repaint is
// indistinguishable from truth to the operator.
// ---------------------------------------------------------------------------
expectContains('src/apis/module/monitoring.js', '/lesson-monitoring/assignments', 'monitoring API must use the admin monitoring path');

// The staleness guards are asserted by behaviour, not by name: a view that has no
// guard at all still runs here (the predicates fall back to "always current"), and
// fails on the out-of-order repaint it produces.
function extractGuardPredicate(sourceText, name) {
  try {
    return extractObjectMethod(sourceText, name);
  } catch {
    return `function ${name}() { return true; }`;
  }
}

const monitoringSource = source('src/views/LessonMonitoring.vue');
const monitoringApi = {};
const monitoringMethods = vm.runInNewContext(`(() => ({
  isListRequestCurrent: ${extractGuardPredicate(monitoringSource, 'isListRequestCurrent')},
  isEventsRequestCurrent: ${extractGuardPredicate(monitoringSource, 'isEventsRequestCurrent')},
  fetchList: ${extractObjectMethod(monitoringSource, 'fetchList')},
  openEvents: ${extractObjectMethod(monitoringSource, 'openEvents')},
  resetEvents: ${extractObjectMethod(monitoringSource, 'resetEvents')},
}))()`, { Api: monitoringApi });

function makeMonitoringContext() {
  const errors = [];
  return {
    list: [],
    loading: false,
    limit: 200,
    filters: { keyword: '', deviceId: '', childId: '', lessonId: '', state: '' },
    listRequestId: 0,
    eventsRequestId: 0,
    eventsVisible: false,
    eventsLoading: false,
    events: [],
    eventsSource: {},
    $t: (key) => key,
    $message: { error: (message) => errors.push(message) },
    isListRequestCurrent: monitoringMethods.isListRequestCurrent,
    isEventsRequestCurrent: monitoringMethods.isEventsRequestCurrent,
    errors,
  };
}

{
  const listCalls = [];
  monitoringApi.monitoring = {
    listAssignments(params, onSuccess, onError) { listCalls.push({ params, onSuccess, onError }); },
    getAssignmentEvents() {},
  };
  const context = makeMonitoringContext();

  context.filters.state = 'RUNNING';
  monitoringMethods.fetchList.call(context);
  context.filters.state = 'FAILED';
  monitoringMethods.fetchList.call(context);
  if (listCalls[0].params.state !== 'RUNNING' || listCalls[1].params.state !== 'FAILED') {
    throw new Error('each refresh must send the filters captured at call time');
  }

  listCalls[1].onSuccess([{ assignmentId: 'fresh', state: 'FAILED' }]);
  listCalls[0].onSuccess([{ assignmentId: 'stale', state: 'RUNNING' }]);
  if (context.list.length !== 1 || context.list[0].assignmentId !== 'fresh') {
    throw new Error('a superseded assignment-list response must not repaint the table');
  }
  if (context.loading !== false) throw new Error('the current list response must clear loading');

  monitoringMethods.fetchList.call(context);
  const superseded = listCalls[2];
  monitoringMethods.fetchList.call(context);
  superseded.onError('boom');
  if (context.errors.length !== 0) {
    throw new Error('a superseded assignment-list failure must not raise an operator error toast');
  }
  if (context.loading !== true) {
    throw new Error('a superseded failure must not clear the loading state of the in-flight refresh');
  }
}

{
  const eventCalls = [];
  monitoringApi.monitoring = {
    listAssignments() {},
    getAssignmentEvents(assignmentId, onSuccess, onError) { eventCalls.push({ assignmentId, onSuccess, onError }); },
  };
  const context = makeMonitoringContext();

  monitoringMethods.openEvents.call(context, { assignmentId: 'asg-a', lessonId: 'lesson-a' });
  monitoringMethods.resetEvents.call(context);
  monitoringMethods.openEvents.call(context, { assignmentId: 'asg-b', lessonId: 'lesson-b' });
  eventCalls[1].onSuccess([{ eventType: 'lesson_started' }]);
  eventCalls[0].onSuccess([{ eventType: 'lesson_failed' }]);
  if (context.events.length !== 1 || context.events[0].eventType !== 'lesson_started') {
    throw new Error('a stale timeline response must not overwrite the timeline of the open assignment');
  }
  if (context.eventsSource.assignmentId !== 'asg-b') {
    throw new Error('the dialog subtitle must track the assignment whose timeline is shown');
  }

  monitoringMethods.openEvents.call(context, { assignmentId: 'asg-c' });
  const closedCall = eventCalls[2];
  context.eventsVisible = false;
  monitoringMethods.resetEvents.call(context);
  closedCall.onError('load failed');
  if (context.errors.length !== 0) {
    throw new Error('a timeline failure that arrives after close must not raise an error toast');
  }
  if (context.eventsLoading !== false) throw new Error('closing the dialog must clear its loading state');
}

console.log('lesson assignment UI contracts passed');
