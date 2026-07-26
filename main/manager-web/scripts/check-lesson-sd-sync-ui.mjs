import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();
const checksum = 'a'.repeat(64);

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  assert.ok(read(file).includes(needle), `${file} missing ${needle}: ${reason}`);
}

function expectAbsent(file, needle, reason) {
  assert.ok(!read(file).includes(needle), `${file} contains forbidden ${needle}: ${reason}`);
}

function expectRegex(file, regex, reason) {
  assert.match(read(file), regex, `${file}: ${reason}`);
}

function extractNginxBlocks(source, marker) {
  const blocks = [];
  let cursor = 0;
  while ((cursor = source.indexOf(marker, cursor)) !== -1) {
    const blockStart = source.indexOf('{', cursor);
    assert.notEqual(blockStart, -1, `nginx block missing opening brace: ${marker}`);
    let depth = 0;
    let closed = false;
    for (let index = blockStart; index < source.length; index += 1) {
      if (source[index] === '{') depth += 1;
      if (source[index] === '}') depth -= 1;
      if (depth === 0) {
        blocks.push(source.slice(cursor, index + 1));
        cursor = index + 1;
        closed = true;
        break;
      }
    }
    assert.ok(closed, `unterminated nginx block: ${marker}`);
  }
  return blocks;
}

function loadLessonApi(context = {}) {
  const source = read('src/apis/module/lesson.js')
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];\n/g, '')
    .replace(/export function /g, 'function ')
    .replace('export default {', 'const lessonApi = {');
  return vm.runInNewContext(`${source}\n({ lessonApi, normalizeLessonAssetGenerationStatus });`, {
    URLSearchParams,
    Array,
    Date,
    Error,
    JSON,
    Math,
    Number,
    Object,
    Promise,
    RegExp,
    Set,
    String,
    ...context,
  });
}

function cmsFixture(overrides = {}) {
  const index = Array.from({ length: 27 }, (_, position) => ({
    lessonId: `lesson-${position + 1}`,
    classification: position < 26 ? 'curriculum' : 'demo',
  }));
  return {
    data: {
      generation: 8,
      curriculumLessonCount: 26,
      packCount: 27,
      indexChecksum: checksum,
      publishedAt: '2026-07-24T00:00:00Z',
      index,
      ...overrides,
    },
  };
}

const buildFixture = {
  data: {
    state: 'idle',
    pendingCount: 0,
    updatedAt: '2026-07-24T00:00:00Z',
    lastErrorCode: null,
  },
};
const espFixture = {
  acceptedGeneration: 8,
  indexChecksum: checksum,
  materializationState: 'ready',
  connections: { connected: 2, current: 2, retrying: 0, failed: 0 },
  lastPollAt: '2026-07-24T00:00:00Z',
  lastMaterializedAt: '2026-07-24T00:00:01Z',
  lastErrorCode: null,
};

expectContains('src/apis/module/lesson.js', 'normalizeLessonAssetGenerationStatus', 'aggregate status must be strictly normalized');
expectContains('src/apis/module/lesson.js', 'getLessonAssetGenerationStatus', 'lesson API must load the three aggregate sources');
expectAbsent('src/apis/module/lesson.js', 'getSdSyncStatus', 'per-lesson device status API is obsolete');
expectAbsent('src/apis/module/lesson.js', 'retrySdSync', 'rollout status is read-only');
expectAbsent('src/apis/module/lesson.js', '/sd-sync/retry', 'the obsolete retry POST endpoint must be removed');
expectContains('src/views/LessonEditor.vue', 'LessonSdSyncStatus', 'published lesson editor must render rollout status');
expectContains('src/views/LessonEditor.vue', 'loadLessonAssetGenerationStatus', 'editor must load rollout status');
expectContains('src/views/LessonEditor.vue', 'scheduleLessonAssetGenerationPoll', 'editor must poll incomplete rollouts');
expectContains('src/views/LessonEditor.vue', 'clearLessonAssetGenerationPoll', 'editor must clear polling during teardown');
expectAbsent('src/views/LessonEditor.vue', '@retry=', 'editor must not render a retry action');
expectAbsent('src/views/LessonEditor.vue', 'sdSyncRetrying', 'editor must not retain retry state');
expectRegex('src/views/LessonEditor.vue', /beforeDestroy\(\)[\s\S]*?clearLessonAssetGenerationPoll/, 'editor teardown must clear rollout polling');

const component = 'src/components/lesson/LessonSdSyncStatus.vue';
for (const needle of ['generation', 'curriculumLessonCount', 'packCount', 'connected', 'current', 'retrying', 'failed', 'allConnectedCurrent']) {
  expectContains(component, needle, `aggregate component must render ${needle}`);
}
for (const forbidden of ['el-table', 'el-collapse', 'el-button', 'deviceId', "$emit('retry'", 'sdSyncChecksum']) {
  expectAbsent(component, forbidden, 'aggregate UI must not expose per-device details or retry controls');
}
expectContains(component, 'lesson.sdSyncOfflineDisclaimer', 'offline/never-seen robots require a permanent disclaimer');
expectContains(component, 'lesson.sdSyncAllConnectedCurrent', 'success wording must be conditional');

for (const locale of ['en', 'zh_CN', 'zh_TW', 'vi']) {
  for (const key of [
    'lesson.sdSyncTitle', 'lesson.sdSyncGeneration', 'lesson.sdSyncCurriculumLessons',
    'lesson.sdSyncTotalPacks', 'lesson.sdSyncConnected', 'lesson.sdSyncCurrent',
    'lesson.sdSyncRetrying', 'lesson.sdSyncFailed', 'lesson.sdSyncAllConnectedCurrent',
    'lesson.sdSyncOfflineDisclaimer', 'lesson.sdSyncBuildState',
    'lesson.sdSyncMaterializationState', 'lesson.sdSyncLastBuild', 'lesson.sdSyncLastPoll',
    'lesson.sdSyncLastMaterialized',
  ]) expectContains(`src/i18n/${locale}.js`, `'${key}'`, `${locale} locale must translate ${key}`);
}
expectContains('src/i18n/vi.js', 'Tất cả robot đang kết nối đã nhận thế hệ mới nhất.', 'Vietnamese success wording is product-approved');
expectContains('src/i18n/vi.js', 'Robot đang tắt hoặc chưa từng kết nối sẽ tự đồng bộ khi kết nối lại; trạng thái này không xác nhận các robot đó đã cập nhật.', 'Vietnamese disclaimer is product-approved');

const forbiddenClaims = /all robots (?:are )?updated|all robots have (?:been )?updated|tất cả robot đã (?:được )?cập nhật|所有机器人(?:均|都)?已更新|所有機器人(?:均|都)?已更新/i;
for (const file of [component, 'src/i18n/en.js', 'src/i18n/vi.js', 'src/i18n/zh_CN.js', 'src/i18n/zh_TW.js']) {
  assert.doesNotMatch(read(file), forbiddenClaims, `${file} must not claim powered-off robots are updated`);
}

const { normalizeLessonAssetGenerationStatus } = loadLessonApi();
const normalized = normalizeLessonAssetGenerationStatus(buildFixture, cmsFixture(), espFixture);
assert.deepEqual(JSON.parse(JSON.stringify(normalized)), {
  generation: 8,
  curriculumLessonCount: 26,
  packCount: 27,
  buildState: 'idle',
  pendingCount: 0,
  materializationState: 'ready',
  connected: 2,
  current: 2,
  retrying: 0,
  failed: 0,
  allConnectedCurrent: true,
  lastBuildAt: '2026-07-24T00:00:00Z',
  lastPollAt: '2026-07-24T00:00:00Z',
  lastMaterializedAt: '2026-07-24T00:00:01Z',
  lastErrorCode: null,
});
assert.equal(normalizeLessonAssetGenerationStatus(buildFixture, cmsFixture({ generation: 9 }), espFixture).allConnectedCurrent, false, 'generation mismatch must not be success');
assert.equal(normalizeLessonAssetGenerationStatus(buildFixture, cmsFixture({ indexChecksum: 'b'.repeat(64) }), espFixture).allConnectedCurrent, false, 'checksum mismatch must not be success');
assert.equal(normalizeLessonAssetGenerationStatus(buildFixture, cmsFixture(), { ...espFixture, connections: { connected: 0, current: 0, retrying: 0, failed: 0 } }).allConnectedCurrent, false, 'zero connected robots must not be success');

for (const [build, cms, esp] of [
  [{ data: { ...buildFixture.data, state: 'complete' } }, cmsFixture(), espFixture],
  [{ data: { ...buildFixture.data, pendingCount: -1 } }, cmsFixture(), espFixture],
  [{ data: { ...buildFixture.data, updatedAt: 'yesterday' } }, cmsFixture(), espFixture],
  [{ data: { ...buildFixture.data, updatedAt: undefined } }, cmsFixture(), espFixture],
  [buildFixture, cmsFixture({ generation: 0 }), espFixture],
  [buildFixture, cmsFixture({ packCount: 26 }), espFixture],
  [buildFixture, cmsFixture({ curriculumLessonCount: 25 }), espFixture],
  [buildFixture, cmsFixture({ indexChecksum: checksum.toUpperCase() }), espFixture],
  [buildFixture, cmsFixture({ publishedAt: 'invalid' }), espFixture],
  [buildFixture, cmsFixture({ publishedAt: '2026-02-30T00:00:00Z' }), espFixture],
  [buildFixture, cmsFixture(), { ...espFixture, acceptedGeneration: 0 }],
  [buildFixture, cmsFixture(), { ...espFixture, materializationState: 'complete' }],
  [buildFixture, cmsFixture(), { ...espFixture, connections: { connected: 2, current: 2, retrying: 1, failed: 0 } }],
  [buildFixture, cmsFixture(), { ...espFixture, lastPollAt: 'invalid' }],
  [buildFixture, cmsFixture(), { ...espFixture, lastPollAt: undefined }],
  [buildFixture, cmsFixture(), { ...espFixture, lastErrorCode: 'Secret: token' }],
]) assert.equal(normalizeLessonAssetGenerationStatus(build, cms, esp), null, 'corrupt aggregate source must be rejected');

const calls = [];
let adminRequest;
const fetchResolvers = new Map();
const fetchMock = (url, options) => {
  calls.push({ url, options });
  return new Promise((resolve, reject) => fetchResolvers.set(url, { resolve, reject }));
};
const { lessonApi } = loadLessonApi({
  getNestUrl: () => '/nestjs/v1/admin',
  fetch: fetchMock,
  nestRequest: (request) => { adminRequest = request; },
  nestUpload: () => {},
  normalizeLesson: (value) => value,
  normalizeStep: (value) => value,
  normalizeStepType: (value) => value,
});
let successes = 0;
let failures = 0;
let result;
lessonApi.getLessonAssetGenerationStatus((value) => { successes += 1; result = value; }, () => { failures += 1; });
assert.equal(adminRequest.url, '/nestjs/v1/admin/lesson-assets/generation-status');
assert.equal(adminRequest.method, 'GET');
assert.deepEqual(calls.map(({ url }) => url).sort(), ['/public/lesson-assets/generation', '/v1/public/lesson-assets/latest']);
for (const { options } of calls) {
  assert.equal(options.method, 'GET');
  assert.equal(options.credentials, 'omit');
  assert.ok(!Object.keys(options.headers || {}).some((key) => key.toLowerCase() === 'authorization'), 'public GET must not send Authorization');
}
adminRequest.onSuccess(buildFixture.data);
fetchResolvers.get('/v1/public/lesson-assets/latest').resolve({ status: 200, json: async () => cmsFixture() });
fetchResolvers.get('/public/lesson-assets/generation').resolve({ status: 200, json: async () => espFixture });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(successes, 1);
assert.equal(failures, 0);
assert.equal(result.allConnectedCurrent, true);

let failureMeta;
adminRequest = null;
calls.length = 0;
fetchResolvers.clear();
lessonApi.getLessonAssetGenerationStatus(() => { successes += 1; }, (message, meta) => { failures += 1; failureMeta = { message, meta }; });
fetchResolvers.get('/v1/public/lesson-assets/latest').resolve({ status: 503, json: async () => ({ secret: 'must-not-leak' }) });
await new Promise((resolve) => setTimeout(resolve, 0));
adminRequest.onError('upstream failed', { status: 500, url: 'https://secret', token: 'secret' });
fetchResolvers.get('/public/lesson-assets/generation').reject(new Error('https://secret/token'));
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(failures, 1, 'first terminal failure must call onError exactly once');
assert.equal(successes, 1, 'late failures must not call onSuccess');
assert.equal(failureMeta.meta.status, 503);
assert.ok(!JSON.stringify(failureMeta).includes('secret'), 'errors must not leak bodies, URLs, or tokens');

expectContains('vue.config.js', "'/public/lesson-assets/generation'", 'dev server must proxy ESP aggregate status same-origin');
expectContains('vue.config.js', "'/v1/public/lesson-assets/latest'", 'dev server must proxy CMS latest same-origin');
expectContains('vue.config.js', 'ESP_STATUS_TARGET', 'ESP proxy target must be configurable');
expectAbsent('src/apis/module/lesson.js', 'esp.tjbot.vn', 'browser API must not use a cross-origin ESP URL');
const nginx = read('../../deploy/nginx/tjbot.vn.conf');
const adminServer = nginx.slice(nginx.indexOf('server_name admin.tjbot.vn'), nginx.indexOf('server_name esp.tjbot.vn'));
for (const location of ['location = /public/lesson-assets/generation', 'location = /v1/public/lesson-assets/latest']) {
  assert.ok(adminServer.includes(location), `admin.tjbot.vn missing ${location}`);
}
for (const requirement of ['Authorization ""', 'Cookie ""', 'Cf-Access-Jwt-Assertion ""']) {
  assert.ok(adminServer.includes(requirement), `admin public proxies missing ${requirement}`);
}
assert.doesNotMatch(nginx, /zone=lesson_public_read\b/, 'obsolete shared cloudflared origin bucket must be removed');
assert.match(
  nginx,
  /limit_req_zone\s+"\$uri"\s+zone=lesson_public_egress:1m\s+rate=100r\/s;/,
  'public egress must use a bounded canonical URI bucket shared across hostnames',
);
assert.doesNotMatch(nginx, /\$host\|\$uri/, 'attacker-controlled Host must not create new egress buckets');
assert.match(
  nginx,
  /proxy_cache_path\s+\/var\/cache\/nginx\/lesson-generation[^;]*keys_zone=lesson_generation:1m[^;]*;/,
  'CMS latest reads must have a dedicated shared cache zone',
);
const publicGenerationLocations = [
  ...extractNginxBlocks(nginx, 'location = /public/lesson-assets/generation'),
  ...extractNginxBlocks(nginx, 'location = /v1/public/lesson-assets/latest'),
];
assert.equal(publicGenerationLocations.length, 4, 'both public generation routes must exist on admin and ESP hosts');
for (const location of publicGenerationLocations) {
  assert.match(location, /limit_req\s+zone=lesson_public_egress\s+burst=180\s+nodelay;/, 'public generation reads must use the bounded egress bucket');
  assert.match(location, /limit_req_status\s+429;/, 'public generation egress overflow must return 429');
}
const latestLocations = extractNginxBlocks(nginx, 'location = /v1/public/lesson-assets/latest');
const statusLocations = extractNginxBlocks(nginx, 'location = /public/lesson-assets/generation');
assert.equal(latestLocations.length, 2, 'CMS latest route must exist on both public hostnames');
assert.equal(statusLocations.length, 2, 'ESP generation status route must exist on both public hostnames');
for (const location of latestLocations) {
  for (const requirement of [
    'proxy_cache lesson_generation',
    'proxy_cache_key "lesson-assets-latest"',
    'proxy_cache_valid 200 15s',
    'proxy_cache_lock on',
    'proxy_cache_background_update on',
    'proxy_cache_use_stale updating error timeout http_500 http_502 http_503 http_504',
    'proxy_ignore_headers Vary',
    'proxy_hide_header Vary',
    'proxy_hide_header Access-Control-Allow-Origin',
    'proxy_hide_header Access-Control-Allow-Credentials',
    'proxy_set_header Origin ""',
    'add_header Access-Control-Allow-Origin "*" always',
    'proxy_set_header If-None-Match ""',
    'proxy_set_header Accept-Encoding "identity"',
  ]) assert.ok(location.includes(requirement), `CMS latest proxy missing ${requirement}`);
}
for (const location of statusLocations) {
  assert.doesNotMatch(location, /proxy_cache(?:_key)?\s/, 'ESP status must remain uncached');
}
assert.match(adminServer, /proxy_pass http:\/\/127\.0\.0\.1:8003/);
assert.match(adminServer, /proxy_pass http:\/\/127\.0\.0\.1:3300/);
assert.doesNotMatch(adminServer, /proxy_pass http:\/\/127\.0\.0\.1:3000/);
assert.equal((adminServer.match(/if \(\$request_method !~ \^\(GET\|HEAD\)\$\) \{ return 405; \}/g) || []).length, 2, 'admin public proxies must reject mutations');
assert.ok(!/auth_basic|auth_request/.test(adminServer), 'admin public proxies must bypass interactive and subrequest auth');

console.log('Lesson generation rollout UI contracts passed');
