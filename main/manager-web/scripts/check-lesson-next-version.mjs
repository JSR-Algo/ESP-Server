import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();
const editorPath = path.join(root, 'src/views/LessonEditor.vue');
const source = fs.readFileSync(editorPath, 'utf8');

function expectMatch(pattern, reason) {
  if (!pattern.test(source)) throw new Error(reason);
}

function extractObjectMethod(name, targetSource = source) {
  const methodPattern = new RegExp(`\\n\\s{2,4}${name}\\(`);
  const match = methodPattern.exec(targetSource);
  if (!match) throw new Error(`${name} method not found`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = targetSource.indexOf('(', start);
  const paramsEnd = targetSource.indexOf(')', paramsStart);
  const braceStart = targetSource.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < targetSource.length; i += 1) {
    if (targetSource[i] === '{') depth += 1;
    if (targetSource[i] === '}' && --depth === 0) {
      return `function ${name}(${targetSource.slice(paramsStart + 1, paramsEnd)}) {${targetSource.slice(braceStart + 1, i)}}`;
    }
  }
  throw new Error(`${name} method body not closed`);
}

expectMatch(
  /<el-button[^>]*v-if="lesson\.status === 'published'"[^>]*data-testid="create-next-version"[^>]*@click="createNextVersion"[^>]*:loading="creatingNextVersion"[^>]*:disabled="creatingNextVersion"/m,
  'published lessons need a clear, single-submit next-version action',
);
expectMatch(
  /<el-button[^>]*v-if="canCreateCourseModeV5Version"[^>]*data-testid="create-course-mode-v5-version"[^>]*@click="createCourseModeV5Version"[^>]*:loading="creatingNextVersion"[^>]*:disabled="creatingNextVersion"/m,
  'eligible published Course Mode v4 lessons need a distinct renderer-v5 action',
);
expectMatch(/creatingNextVersion:\s*false/, 'next-version submission state must start idle');
const enSource = fs.readFileSync(path.join(root, 'src/i18n/en.js'), 'utf8');
if (!/'lesson\.createCourseModeV5Version':\s*'Create Course Mode v5 version'/.test(enSource)) {
  throw new Error('the renderer-v5 action needs a clear English label');
}
const viSource = fs.readFileSync(path.join(root, 'src/i18n/vi.js'), 'utf8');
if (!/'lesson\.createCourseModeV5Version':\s*'Tạo phiên bản Course Mode v5'/.test(viSource)) {
  throw new Error('the renderer-v5 action needs a complete Vietnamese label');
}

const calls = [];
const Api = {
  lesson: {
    createNextVersion(lessonId, dataOrSuccess, onSuccessOrError, onError) {
      const data = typeof dataOrSuccess === 'function' ? undefined : dataOrSuccess;
      const onSuccess = typeof dataOrSuccess === 'function' ? dataOrSuccess : onSuccessOrError;
      const resolvedOnError = typeof dataOrSuccess === 'function' ? onSuccessOrError : onError;
      calls.push({ lessonId, data, onSuccess, onError: resolvedOnError });
    },
  },
};
const createNextVersion = vm.runInNewContext(`(${extractObjectMethod('createNextVersion')})`, { Api });
const createCourseModeV5Version = vm.runInNewContext(`(${extractObjectMethod('createCourseModeV5Version')})`, { Api });
const submitNextVersion = vm.runInNewContext(`(${extractObjectMethod('submitNextVersion')})`, { Api });
const canCreateCourseModeV5Version = vm.runInNewContext(`(${extractObjectMethod('canCreateCourseModeV5Version')})`);
const notifications = [];
const navigations = [];
const context = {
  lesson: { lessonId: 'published-1', lessonVersion: 5, status: 'published' },
  creatingNextVersion: false,
  $route: {
    path: '/lesson-editor',
    query: { lessonId: 'published-1', courseId: 'course-1', demoSource: 'farm' },
  },
  $router: { replace(location) { navigations.push(location); } },
  $t(key) { return key; },
  $message: {
    success(message) { notifications.push({ type: 'success', message }); },
    error(message) { notifications.push({ type: 'error', message }); },
  },
  submitNextVersion(data) { return submitNextVersion.call(this, data); },
};

createNextVersion.call(context);
createNextVersion.call(context);
if (calls.length !== 1 || calls[0].lessonId !== 'published-1' || !context.creatingNextVersion) {
  throw new Error('rapid clicks must submit exactly one next-version request for the published lesson');
}
if (calls[0].data !== undefined) {
  throw new Error(`generic editable-version creation must preserve the source renderer with an omitted body, got ${JSON.stringify(calls[0].data)}`);
}

calls[0].onSuccess({ lessonId: 'published-1', lessonVersion: 6, status: 'draft' });
if (context.creatingNextVersion) throw new Error('success must release the submission state');
if (notifications[0]?.type !== 'error' || notifications[0]?.message !== 'lesson.nextVersionInvalid') {
  throw new Error('a normalized same-ID response must be rejected as an invalid draft');
}
if (navigations.length) {
  throw new Error('an invalid same-ID draft must not navigate because the lesson route watcher would not reload');
}

createNextVersion.call(context);
calls[1].onSuccess({ lessonId: 'stale-draft', lessonVersion: 1, status: 'draft' });
if (context.creatingNextVersion) throw new Error('success must release the submission state');
if (notifications[1]?.type !== 'error' || notifications[1]?.message !== 'lesson.nextVersionInvalid') {
  throw new Error('a returned draft version must be newer than the published source version');
}
if (navigations.length) throw new Error('a stale returned draft must not navigate');

createNextVersion.call(context);
calls[2].onSuccess({ lessonId: 'draft-9', lessonVersion: 9, status: 'draft' });
if (context.creatingNextVersion) throw new Error('success must release the submission state');
if (notifications[2]?.type !== 'success' || notifications[2]?.message !== 'lesson.nextVersionCreated') {
  throw new Error('success must be visible to the admin');
}
if (JSON.stringify(navigations[0]) !== JSON.stringify({
  path: '/lesson-editor',
  query: { lessonId: 'draft-9', courseId: 'course-1', demoSource: 'farm' },
})) {
  throw new Error(`success must open the returned draft without losing preview context: ${JSON.stringify(navigations[0])}`);
}

createNextVersion.call(context);
calls[3].onError('draft already exists');
if (context.creatingNextVersion) throw new Error('errors must release the submission state');
if (notifications[3]?.type !== 'error' || notifications[3]?.message !== 'draft already exists') {
  throw new Error('API errors must be visible to the admin');
}

context.lesson = {
  lessonId: 'course-v4-published',
  lessonVersion: 8,
  status: 'published',
  manifestVersion: 'teebot-lesson-renderer.v4',
  courseModeContract: { version: 2 },
};
context.canCreateCourseModeV5Version = true;
createCourseModeV5Version.call(context);
if (JSON.stringify(calls[4].data) !== JSON.stringify({ rendererVersion: 'teebot-lesson-renderer.v5' })) {
  throw new Error(`the distinct Course Mode v5 action must send the exact renderer request, got ${JSON.stringify(calls[4].data)}`);
}

const eligibilityCases = [
  [{ lessonId: 'v4-course', status: 'published', manifestVersion: 'teebot-lesson-renderer.v4' }, 'v4-course', true, true],
  [{ lessonId: 'v1', status: 'published', manifestVersion: 'teebot-lesson-renderer.v1' }, 'v1', false, false],
  [{ lessonId: 'v3', status: 'published', manifestVersion: 'teebot-lesson-renderer.v3' }, 'v3', false, false],
  [{ lessonId: 'v4-plain', status: 'published', manifestVersion: 'teebot-lesson-renderer.v4' }, 'v4-plain', false, false],
  [{ lessonId: 'v5-course', status: 'published', manifestVersion: 'teebot-lesson-renderer.v5' }, 'v5-course', true, false],
  [{ lessonId: 'v4-draft', status: 'draft', manifestVersion: 'teebot-lesson-renderer.v4' }, 'v4-draft', true, false],
  [{ lessonId: 'stale-route', status: 'published', manifestVersion: 'teebot-lesson-renderer.v4' }, 'current-route', true, false],
];
for (const [lesson, lessonId, hasLoadedCourseModeAuthority, expected] of eligibilityCases) {
  const actual = canCreateCourseModeV5Version.call({ lesson, lessonId, hasLoadedCourseModeAuthority });
  if (actual !== expected) {
    throw new Error(`Course Mode v5 action eligibility mismatch for ${JSON.stringify({ lesson, lessonId, hasLoadedCourseModeAuthority })}`);
  }
}

context.canCreateCourseModeV5Version = false;
if (createCourseModeV5Version.call(context) !== false || calls.length !== 5) {
  throw new Error('ineligible lessons must not dispatch the explicit renderer-v5 action');
}

const lessonApiSource = fs.readFileSync(path.join(root, 'src/apis/module/lesson.js'), 'utf8');
let nextVersionRequest;
const apiCreateNextVersion = vm.runInNewContext(`(${extractObjectMethod('createNextVersion', lessonApiSource)})`, {
  getNestUrl: () => '/nestjs',
  nestRequest(request) { nextVersionRequest = request; },
  normalizeLesson(raw) {
    return {
      lessonId: raw.id ?? raw.lesson_id ?? raw.lessonId ?? '',
      status: raw.status ?? 'draft',
      lessonVersion: Number(raw.lesson_version ?? raw.lessonVersion ?? 0),
    };
  },
  normalizeAuthoringLesson(raw) {
    return {
      lessonId: raw.id ?? raw.lesson_id ?? raw.lessonId ?? '',
      status: raw.status ?? 'draft',
      lessonVersion: Number(raw.lesson_version ?? raw.lessonVersion ?? 0),
      manifestVersion: raw.manifest_version ?? raw.manifestVersion ?? '',
      courseModeContract: raw.course_mode_contract ?? raw.courseModeContract ?? null,
    };
  },
});
let apiSuccesses = 0;
let apiFailure;
apiCreateNextVersion.call({}, 'published-1', () => { apiSuccesses += 1; }, (message, error) => {
  apiFailure = { message, error };
});
if ('data' in nextVersionRequest) {
  throw new Error(`legacy createNextVersion signature must not invent a renderer body, got ${JSON.stringify(nextVersionRequest.data)}`);
}
apiCreateNextVersion.call({}, 'published-1', { rendererVersion: 'teebot-lesson-renderer.v5' }, () => { apiSuccesses += 1; }, (message, error) => {
  apiFailure = { message, error };
});
if (JSON.stringify(nextVersionRequest.data) !== JSON.stringify({ rendererVersion: 'teebot-lesson-renderer.v5' })) {
  throw new Error(`explicit createNextVersion body must be preserved, got ${JSON.stringify(nextVersionRequest.data)}`);
}
for (const malformed of [
  { id: 'different-id', lesson_version: 2 },
  { id: 'different-id', status: 'draft' },
  { id: '', status: 'draft', lesson_version: 2 },
]) {
  apiSuccesses = 0;
  apiFailure = null;
  nextVersionRequest.onSuccess(malformed);
  if (apiSuccesses || apiFailure?.error?.code !== 'INVALID_NEXT_VERSION_RESPONSE') {
    throw new Error('createNextVersion must reject malformed 2xx payloads before normalizeLesson can invent draft state');
  }
}
apiSuccesses = 0;
apiFailure = null;
nextVersionRequest.onSuccess({ id: 'draft-2', status: 'draft', lesson_version: 2 });
if (apiSuccesses !== 1 || apiFailure) throw new Error('an explicit draft identity and version must pass API validation');

const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
if (packageJson.scripts['test:lesson-next-version'] !== 'node scripts/check-lesson-next-version.mjs') {
  throw new Error('package scripts must expose the focused lesson next-version check');
}
if (!packageJson.scripts['test:lesson-studio'].includes('npm run test:lesson-next-version')) {
  throw new Error('the normal lesson-studio suite must include the next-version regression check');
}

console.log('lesson next-version UI contracts PASS');
