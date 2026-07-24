import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = read(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = read(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

function extractObjectMethod(source, name) {
  const methodPattern = new RegExp(`\\n\\s{2,4}${name}\\(`);
  const match = methodPattern.exec(source);
  if (!match) throw new Error(`${name} method not found`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        const params = source.slice(paramsStart + 1, paramsEnd);
        const body = source.slice(braceStart + 1, i);
        return `function ${name}(${params}) {${body}}`;
      }
    }
  }
  throw new Error(`${name} method body not closed`);
}

function loadLessonApiPrelude() {
  const source = read('src/apis/module/lesson.js');
  const end = source.indexOf('\nexport default');
  if (end < 0) throw new Error('lesson API default export not found');
  return vm.runInNewContext(`${source.slice(0, end)
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];\n/g, '')
    .replace(/export function /g, 'function ')}
    ({ normalizeLessonSdSyncStatus });
  `, { Array, Date, Number, RegExp, Set });
}

function loadLessonApiMethod(name) {
  const source = read('src/apis/module/lesson.js');
  const prelude = loadLessonApiPrelude();
  return vm.runInNewContext(`(${extractObjectMethod(source, name)})`, {
    getNestUrl: () => '/nestjs',
    nestRequest: (request) => { globalThis.__lastNestRequest = request; },
    normalizeLessonSdSyncStatus: prelude.normalizeLessonSdSyncStatus,
    Array,
    Number,
    RegExp,
    Date,
    Error,
  });
}

expectContains('src/apis/module/lesson.js', 'getSdSyncStatus', 'lesson API must expose status loading');
expectContains('src/apis/module/lesson.js', 'retrySdSync', 'lesson API must expose retry');
expectContains('src/apis/module/lesson.js', 'normalizeLessonSdSyncStatus', 'SD sync status must be normalized before UI use');
expectContains('src/apis/module/lesson.js', 'INVALID_SD_SYNC_STATUS_RESPONSE', 'malformed SD sync responses must fail structurally');
expectContains('src/views/LessonEditor.vue', 'LessonSdSyncStatus', 'published lesson editor must render SD sync status');
expectContains('src/views/LessonEditor.vue', 'loadSdSyncStatus', 'editor must load SD sync status for published lessons');
expectContains('src/views/LessonEditor.vue', 'scheduleSdSyncStatusPoll', 'editor must poll only incomplete sync states');
expectContains('src/views/LessonEditor.vue', 'clearSdSyncStatusPoll', 'editor must stop sync timers on teardown and transitions');
expectContains('src/views/LessonEditor.vue', 'retrySdSync', 'editor must wire retry action to the API');
expectContains('src/views/LessonEditor.vue', 'lesson.publishedOfflineSyncContinues', 'publish message must say offline sync continues asynchronously');
expectContains('src/components/lesson/LessonSdSyncStatus.vue', "lesson.sdSyncTitle", 'status component must use localized title');
expectContains('src/components/lesson/LessonSdSyncStatus.vue', "@click=\"$emit('retry'", 'retry action must be emitted by the component');
expectRegex(
  'src/components/lesson/LessonSdSyncStatus.vue',
  /complete\s*===\s*total\s*&&\s*total\s*>\s*0/,
  'UI must never infer offline availability for zero-device or partial sync states',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /beforeDestroy\(\)[\s\S]*?clearSdSyncStatusPoll/,
  'editor teardown must clear SD sync polling',
);

for (const locale of ['en', 'zh_CN', 'zh_TW', 'vi']) {
  for (const key of [
    'lesson.sdSyncComplete',
    'lesson.sdSyncSyncing',
    'lesson.sdSyncOfflinePending',
    'lesson.sdSyncFailed',
    'lesson.sdSyncRetry',
    'lesson.publishedOfflineSyncContinues',
  ]) {
    expectContains(`src/i18n/${locale}.js`, `'${key}'`, `${locale} locale must translate ${key}`);
  }
}

let request;
globalThis.__lastNestRequest = null;
const getSdSyncStatus = loadLessonApiMethod('getSdSyncStatus');
getSdSyncStatus('lesson-1', () => {}, () => {});
request = globalThis.__lastNestRequest;
if (!request || request.url !== '/nestjs/lessons/lesson-1/sd-sync' || request.method !== 'GET') {
  throw new Error('getSdSyncStatus must call GET /lessons/:lessonId/sd-sync');
}

globalThis.__lastNestRequest = null;
const retrySdSync = loadLessonApiMethod('retrySdSync');
retrySdSync('lesson-1', ['dev-a'], () => {}, () => {});
request = globalThis.__lastNestRequest;
if (!request || request.url !== '/nestjs/lessons/lesson-1/sd-sync/retry' || request.method !== 'POST') {
  throw new Error('retrySdSync must call POST /lessons/:lessonId/sd-sync/retry');
}
if (!request.data || JSON.stringify(request.data.deviceIds) !== JSON.stringify(['dev-a'])) {
  throw new Error('retrySdSync must include an array deviceIds payload when provided');
}
retrySdSync('lesson-1', 'dev-a', () => {}, () => {});
request = globalThis.__lastNestRequest;
if (!request.data || request.data.deviceIds !== undefined) {
  throw new Error('retrySdSync must not send a concrete deviceIds list for non-array input');
}

let successes = 0;
let failures = 0;
let errorPayload = null;
getSdSyncStatus('lesson-1', () => { successes += 1; }, (message, error) => {
  failures += 1;
  errorPayload = { message, error };
});
request = globalThis.__lastNestRequest;
const validStatus = {
  state: 'complete',
  total: 2,
  complete: 2,
  syncing: 0,
  offlinePending: 0,
  failed: 0,
  version: 3,
  checksum: 'a'.repeat(64),
  lastSuccessAt: '2026-07-24T00:00:00.000Z',
  lastErrorAt: null,
  devices: [
    { deviceId: 'dev-a', state: 'complete', version: 3, checksum: 'a'.repeat(64), lastSuccessAt: '2026-07-24T00:00:00.000Z', lastErrorAt: null, error: '' },
    { deviceId: 'dev-b', state: 'complete', version: 3, checksum: 'a'.repeat(64), lastSuccessAt: '2026-07-24T00:00:00.000Z', lastErrorAt: null, error: '' },
  ],
};
request.onSuccess(validStatus);
if (successes !== 1 || failures) throw new Error('valid SD sync response must pass unchanged after normalization');
for (const malformed of [
  {},
  { ...validStatus, complete: 3 },
  { ...validStatus, total: -1 },
  { ...validStatus, state: 'ready' },
  { ...validStatus, checksum: 'bad' },
  { ...validStatus, lastSuccessAt: 'not-a-date' },
  { ...validStatus, syncing: 1, complete: 1 },
  { ...validStatus, state: 'complete', complete: 1, failed: 1, devices: [{ ...validStatus.devices[0] }, { ...validStatus.devices[1], state: 'failed' }] },
  { ...validStatus, devices: [{ ...validStatus.devices[0], state: 'ready' }] },
  { ...validStatus, devices: [{ ...validStatus.devices[0], deviceId: '' }] },
]) {
  successes = 0;
  failures = 0;
  errorPayload = null;
  request.onSuccess(malformed);
  if (successes || failures !== 1 || !errorPayload.error || errorPayload.error.code !== 'INVALID_SD_SYNC_STATUS_RESPONSE') {
    throw new Error('malformed SD sync payloads must fail exactly once with INVALID_SD_SYNC_STATUS_RESPONSE');
  }
}

console.log('Lesson SD sync UI contracts passed');
