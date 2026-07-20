import fs from 'node:fs';
import path from 'node:path';
import {
  createAuthoringDirtyHandle,
  scheduleAuthoringSafeCallback,
  scheduleServiceWorkerActivation,
} from '../src/utils/serviceWorkerUpdateSafety.mjs';

const repoRoot = path.resolve(process.cwd(), '../..');

function readFromRepo(rel) {
  return fs.readFileSync(path.join(repoRoot, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = readFromRepo(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = readFromRepo(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

const noStore = 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0';
const immutable = 'public, max-age=31536000, immutable';

expectRegex('docs/docker/nginx.conf', /root\s+\/usr\/share\/nginx\/html;/, 'static root should be server-level for all SPA routes');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/index\.html\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'index.html must not be cached across deploys');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/service-worker\.js\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'service worker must update immediately across deploys');
expectRegex('docs/docker/nginx.conf', /location\s+=\s+\/manifest\.json\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"/m, 'manifest should not pin stale app metadata');
expectRegex('docs/docker/nginx.conf', /location\s+~\*\s+\^\/\(js\|css\|img\|fonts\)\/[\s\S]*Cache-Control\s+"public, max-age=31536000, immutable"/m, 'hashed static assets should be cached immutably');
expectRegex('docs/docker/nginx.conf', /location\s+\/\s*{[\s\S]*Cache-Control\s+"no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"[\s\S]*try_files\s+\$uri\s+\$uri\/\s+\/index\.html;/m, 'SPA fallback must serve fresh shell');
expectRegex(
  'docs/docker/nginx.conf',
  /set\s+\$nest_auth\s+"__NESTJS_AUTH_HEADER__";[\s\S]*if\s*\(\$http_x_nest_authorization\)[\s\S]*rewrite\s+\^\/nestjs\/\(\.\*\)\$\s+\/\$1\s+break;/m,
  'Nest auth selection must run before the terminating rewrite break',
);

expectContains('docs/docker/nginx.conf', noStore, 'no-store policy must be explicit');
expectContains('docs/docker/nginx.conf', immutable, 'immutable policy must be explicit');

expectContains('main/manager-web/src/registerServiceWorker.js', 'registration.update()', 'registered service worker should proactively check for updates');
expectContains('main/manager-web/src/utils/serviceWorkerUpdateSafety.mjs', 'SKIP_WAITING', 'new service worker should activate without stale UI waiting');
expectContains('main/manager-web/src/registerServiceWorker.js', 'controllerchange', 'clients should reload once after controller changes');

expectRegex(
  'main/manager-web/vue.config.js',
  /exclude:\s*\[[^\]]*\/\^generator\\\//,
  'firmware generator payloads must stay on-demand instead of bloating the admin precache',
);
expectContains(
  'main/manager-web/src/service-worker.js',
  "manifest.filter(entry => entry.url !== 'cdn-mode')",
  'the synthetic CDN-mode marker must not be fetched as a precache URL',
);
expectContains(
  'main/manager-web/src/service-worker.js',
  'workbox.precaching.precacheAndRoute(precacheManifest)',
  'hashed admin and Lesson Studio bundles must be revisioned and available after service-worker install',
);
expectContains(
  'main/manager-web/src/service-worker.js',
  'workbox.precaching.cleanupOutdatedCaches()',
  'old precaches should be cleaned through Workbox without deleting the active precache',
);

expectContains(
  'main/manager-web/src/registerServiceWorker.js',
  'scheduleServiceWorkerActivation',
  'all waiting service workers should respect unsaved authoring state',
);
expectContains(
  'main/manager-web/src/registerServiceWorker.js',
  'scheduleAuthoringSafeCallback',
  'controller changes from another tab must not reload a dirty Lesson Studio tab',
);
expectContains(
  'main/manager-web/src/views/LessonEditor.vue',
  'hasPendingAuthoringChanges',
  'Lesson Studio should include open dialogs and in-flight writes in update safety',
);
expectContains(
  'main/manager-web/src/views/LessonEditor.vue',
  'lessonUpdateSafety.setDirty(value)',
  'Lesson Studio should publish its dirty state to the update safety gate',
);
expectContains(
  'main/manager-web/src/views/LessonEditor.vue',
  'lessonUpdateSafety.release()',
  'Lesson Studio should release dirty state when the editor is destroyed',
);

const immediateMessages = [];
scheduleServiceWorkerActivation({ postMessage: (message) => immediateMessages.push(message) });
if (JSON.stringify(immediateMessages) !== JSON.stringify([{ type: 'SKIP_WAITING' }])) {
  throw new Error('clean pages should activate a waiting service worker immediately');
}

const dirty = createAuthoringDirtyHandle();
dirty.setDirty(true);
const deferredMessages = [];
const deferredWorker = { postMessage: (message) => deferredMessages.push(message) };
scheduleServiceWorkerActivation(deferredWorker);
scheduleServiceWorkerActivation(deferredWorker);
if (deferredMessages.length !== 0) {
  throw new Error('unsaved Lesson Studio edits must defer service-worker activation');
}
dirty.setDirty(false);
dirty.setDirty(false);
if (JSON.stringify(deferredMessages) !== JSON.stringify([{ type: 'SKIP_WAITING' }])) {
  throw new Error('a deferred worker should activate exactly once after edits are saved');
}
dirty.release();

const firstEditor = createAuthoringDirtyHandle();
const secondEditor = createAuthoringDirtyHandle();
firstEditor.setDirty(true);
secondEditor.setDirty(true);
const sharedMessages = [];
scheduleServiceWorkerActivation({ postMessage: (message) => sharedMessages.push(message) });
firstEditor.release();
if (sharedMessages.length !== 0) {
  throw new Error('one editor becoming clean must not override another dirty editor');
}
secondEditor.release();
if (sharedMessages.length !== 1) {
  throw new Error('worker activation should resume when every editor is clean');
}

const dirtyReload = createAuthoringDirtyHandle();
dirtyReload.setDirty(true);
let reloadCount = 0;
scheduleAuthoringSafeCallback(() => { reloadCount += 1; });
if (reloadCount !== 0) {
  throw new Error('a controller change must not reload a dirty authoring page');
}
dirtyReload.release();
if (reloadCount !== 1) {
  throw new Error('a deferred controller reload should resume after the draft is safe');
}

console.log('web cache policy contract OK');
