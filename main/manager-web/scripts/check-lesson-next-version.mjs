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
expectMatch(/creatingNextVersion:\s*false/, 'next-version submission state must start idle');

const calls = [];
const Api = {
  lesson: {
    createNextVersion(lessonId, data, onSuccess, onError) {
      calls.push({ lessonId, data, onSuccess, onError });
    },
  },
};
const createNextVersion = vm.runInNewContext(`(${extractObjectMethod('createNextVersion')})`, { Api });
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
};

createNextVersion.call(context);
createNextVersion.call(context);
if (calls.length !== 1 || calls[0].lessonId !== 'published-1' || !context.creatingNextVersion) {
  throw new Error('rapid clicks must submit exactly one next-version request for the published lesson');
}
if (JSON.stringify(calls[0].data) !== JSON.stringify({ rendererVersion: 'teebot-lesson-renderer.v5' })) {
  throw new Error('Course Mode next-version creation must request renderer v5 explicitly');
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
