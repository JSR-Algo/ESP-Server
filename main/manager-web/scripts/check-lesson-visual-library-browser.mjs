import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';

const managerRoot = normalize(join(dirname(fileURLToPath(import.meta.url)), '..'));
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ico': 'image/x-icon' };
let temp; let server; let chrome; let socket;

async function stopChild(child) { if (!child || child.exitCode !== null) return; child.kill('SIGTERM'); await Promise.race([new Promise((r) => child.once('exit', r)), new Promise((r) => setTimeout(r, 1500))]); if (child.exitCode === null) child.kill('SIGKILL'); }
async function waitForFile(path) { for (let i = 0; i < 200; i += 1) { if (existsSync(path)) return readFileSync(path, 'utf8'); await new Promise((r) => setTimeout(r, 50)); } throw new Error(`timeout: ${path}`); }

try {
  temp = await mkdtemp(join(tmpdir(), 'tbot-visual-library-'));
  const buildDir = join(temp, 'build'); const profileDir = join(temp, 'chrome'); await mkdir(profileDir, { recursive: true });
  const build = spawnSync(process.execPath, [join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'), 'build', '--dest', buildDir, '--no-clean', join(managerRoot, 'tests/browser/lesson-visual-library-main.js')], { cwd: managerRoot, encoding: 'utf8', timeout: 120000 });
  assert.equal(build.status, 0, `mounted harness build failed:\n${build.stdout}\n${build.stderr}`);
  const html = readFileSync(join(buildDir, 'index.html'), 'utf8').replace('<div id="app"></div>', '<div id="library"></div><div id="detail"></div><div id="impact"></div>');
  server = createServer((request, response) => { const requestPath = request.url.split('?')[0]; if (requestPath === '/') { response.writeHead(200, { 'content-type': 'text/html' }).end(html); return; } const path = normalize(join(buildDir, requestPath)); if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; } try { response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(readFileSync(path)); } catch { response.writeHead(404).end(); } });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const chromeBin = [process.env.CHROME_BIN, join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean).find(existsSync);
  assert.ok(chromeBin, 'Chromium is required');
  chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
  const [debugPort] = (await waitForFile(join(profileDir, 'DevToolsActivePort'))).trim().split('\n');
  const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT' }).then((r) => r.json()); socket = new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let id = 0; const pending = new Map(); socket.on('message', (raw) => { const message = JSON.parse(raw); if (message.id && pending.has(message.id)) { const p = pending.get(message.id); pending.delete(message.id); message.error ? p.reject(new Error(message.error.message)) : p.resolve(message.result); } });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => { const commandId = ++id; pending.set(commandId, { resolve, reject }); socket.send(JSON.stringify({ id: commandId, method, params })); });
  const evaluate = async (expression) => { const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.text); return result.result.value; };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  for (let i = 0; i < 100 && !(await evaluate('Boolean(window.__LESSON_VISUAL_READY__)')); i += 1) await new Promise((r) => setTimeout(r, 50));
  assert.equal(await evaluate('Boolean(window.__LESSON_VISUAL_READY__)'), true, `mounted components did not become ready: ${await evaluate('window.__LESSON_VISUAL_ERROR__ || document.body.innerText')}`);

  const library = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__;const before=t.library.assets.map(x=>[x.assetKey,x.usageCount]);t.library.filters.category='scene';await t.library.$nextTick();return{before,after:t.library.assets.map(x=>x.assetKey),listCalls:t.calls.list.length,metadata:[t.library.rows[0].width,t.library.rows[0].bytes,t.library.rows[0].sha256.slice(0,8)]}})()`);
  assert.deepEqual(library.before, [['object.apple', 5], ['scene.park', 1]]); assert.deepEqual(library.after, ['scene.park']); assert.equal(library.listCalls, 1); assert.deepEqual(library.metadata, [640, 9200, 'bbbbbbbb']);

  const detail = await evaluate(`(()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail;return{comparison:d.comparisonRows.map(x=>[x.source,x.robot]),lessons:d.affectedLessons.map(x=>[x.lessonId,x.lessonStatus,x.activeAssignmentCount]),selected:d.replacementLessons.map(x=>x.lessonId),activeText:[...document.querySelectorAll('.impact-grid div')].map(x=>x.textContent.trim())}})()`);
  assert.deepEqual(detail.comparison, [['640 × 480', '160 × 120'], ['9.0 KB', '1.2 KB'], ['bbbbbbbb', 'aaaaaaaa']]); assert.equal(detail.lessons.length, 2); assert.equal(detail.lessons[1][2], 4); assert.deepEqual(detail.selected, detail.lessons.map((x) => x[0])); assert.ok(detail.activeText[3].includes('4'));

  const replacement = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail;const sourceBefore=JSON.stringify(d.source);d.form.mode='global';await d.$nextTick();d.prepareReplacement();const globalBefore=[t.calls.impact.length,t.calls.replace.length,d.impactVisible];d.executeReplacement();const globalAfter=t.calls.replace.length;d.form.mode='selectedLessons';await d.$nextTick();d.form.lessonIds=[t.usages[1].lessonId];d.prepareReplacement();const selectedBefore=[t.calls.impact.length,t.calls.replace.length,d.impactVisible,t.calls.impact.at(-1).lessonIds];d.impactVisible=false;d.form.mode='cloneForLesson';await d.$nextTick();const cloneOptions=d.replacementLessons.map(x=>[x.lessonId,x.lessonStatus]);d.form.lessonIds=t.usages[0].lessonId;d.prepareReplacement();return{globalBefore,globalAfter,selectedBefore,cloneOptions,clonePayload:t.calls.replace.at(-1),sourceStable:sourceBefore===JSON.stringify(d.source)}})()`);
  assert.deepEqual(replacement.globalBefore, [1, 0, true]); assert.equal(replacement.globalAfter, 1); assert.deepEqual(replacement.selectedBefore.slice(0, 3), [2, 1, true]); assert.equal(replacement.selectedBefore[3].length, 1); assert.deepEqual(replacement.cloneOptions, [['00000000-0000-4000-8000-000000000011', 'draft']]); assert.equal(replacement.clonePayload.mode, 'cloneForLesson'); assert.deepEqual(replacement.clonePayload.lessonIds, ['00000000-0000-4000-8000-000000000011']); assert.equal(replacement.sourceStable, true);

  const safety = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail;const beforeReplace=t.calls.replace.length,beforeImpact=t.calls.impact.length;d.form.mode='global';await d.$nextTick();d.form.sourceVersionId=t.versions[0].versionId;d.form.targetVersionId=t.versions[0].versionId;const sameTarget=[d.replacementReady,0,0,d.targetVersions.map(v=>v.versionId)];d.prepareReplacement();sameTarget[1]=t.calls.impact.length-beforeImpact;sameTarget[2]=t.calls.replace.length-beforeReplace;d.form.targetVersionId=t.versions[1].versionId;d.prepareReplacement();const reviewed=structuredClone(t.calls.impact.at(-1));d.form.mode='selectedLessons';d.form.lessonIds=[t.usages[1].lessonId];d.form.targetVersionId='mutated-after-review';await d.$nextTick();d.executeReplacement();const confirmed=t.calls.replace.at(-1);d.cancelImpact();return{sameTarget,reviewed,confirmed,cleared:d.reviewedRequest===null}})()`);
  assert.equal(safety.sameTarget[0], false); assert.equal(safety.sameTarget[1], 0); assert.equal(safety.sameTarget[2], 0); assert.ok(!safety.sameTarget[3].includes('00000000-0000-4000-8000-000000000002')); assert.equal(safety.confirmed.mode, safety.reviewed.mode); assert.equal(safety.confirmed.targetVersionId, '00000000-0000-4000-8000-000000000001'); assert.deepEqual(safety.confirmed.lessonIds, []); assert.equal(safety.cleared, true);

  const stale = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail,pending=[];t.Api.lesson.getVisualAssetDetail=(assetKey,filters,success,error)=>pending.push({filters,success,error});d.load(t.versions[1].versionId);d.load(t.versions[0].versionId);pending[1].success({asset:{assetKey:d.assetKey},sourceVersionId:t.versions[0].versionId,versions:t.versions.slice(0,2),usages:t.usages});pending[0].success({asset:{assetKey:d.assetKey},sourceVersionId:t.versions[1].versionId,versions:t.versions.slice(0,2),usages:[]});await d.$nextTick();return{source:d.form.sourceVersionId,usageCount:d.usages.length,loading:d.loading}})()`);
  assert.deepEqual(stale, { source: '00000000-0000-4000-8000-000000000002', usageCount: 2, loading: false });

  const cloneRoute = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail,pending=[];t.Api.lesson.getVisualAssetDetail=(assetKey,filters,success,error)=>pending.push({filters,success,error});d.form.mode='global';d.form.lessonIds=[];d.applyRouteIntent({mode:'cloneForLesson',lessonId:t.usages[0].lessonId});d.load();pending[0].success({asset:{assetKey:d.assetKey},sourceVersionId:t.versions[0].versionId,versions:t.versions.slice(0,2),usages:t.usages});await d.$nextTick();const initialized={mode:d.form.mode,lessonIds:d.form.lessonIds,targetVersionId:d.form.targetVersionId,ready:d.replacementReady};d.form.sourceVersionId=t.versions[1].versionId;await d.$nextTick();return{initialized,lessonIdAfterSourceChange:d.form.lessonIds}})()`);
  assert.deepEqual(cloneRoute, { initialized: { mode: 'cloneForLesson', lessonIds: '00000000-0000-4000-8000-000000000011', targetVersionId: '00000000-0000-4000-8000-000000000002', ready: true }, lessonIdAfterSourceChange: '00000000-0000-4000-8000-000000000011' });

  const singleClone = await evaluate(`(async()=>{const t=window.__LESSON_VISUAL_TEST__,d=t.detail,only=structuredClone(t.versions[0]),sourceBefore=JSON.stringify(t.versions[0]),before=t.calls.replace.length;d.versions=[only];d.usages=[t.usages[0]];d.form.sourceVersionId=only.versionId;d.form.targetVersionId=only.versionId;d.form.mode='cloneForLesson';await d.$nextTick();d.form.lessonIds=t.usages[0].lessonId;await d.$nextTick();const ready=d.replacementReady,targets=d.targetVersions.map(v=>v.versionId);d.prepareReplacement();return{ready,targets,payload:t.calls.replace.at(-1),requestDelta:t.calls.replace.length-before,sourceStable:sourceBefore===JSON.stringify(t.versions[0])}})()`);
  assert.equal(singleClone.ready, true); assert.deepEqual(singleClone.targets, ['00000000-0000-4000-8000-000000000002']); assert.equal(singleClone.requestDelta, 1); assert.deepEqual(singleClone.payload, { sourceVersionId: '00000000-0000-4000-8000-000000000002', targetVersionId: '00000000-0000-4000-8000-000000000002', mode: 'cloneForLesson', lessonIds: ['00000000-0000-4000-8000-000000000011'] }); assert.equal(singleClone.sourceStable, true);

  for (const width of [320, 375]) { await cdp('Emulation.setDeviceMetricsOverride', { width, height: 720, deviceScaleFactor: 1, mobile: true }); const rect = await evaluate(`(()=>{const el=document.querySelector('.el-dialog');const r=el.getBoundingClientRect();return{left:r.left,right:r.right,width:r.width,viewport:innerWidth}})()`); assert.ok(rect.left >= 0 && rect.right <= rect.viewport && rect.width <= rect.viewport, `impact dialog overflows ${width}px viewport: ${JSON.stringify(rect)}`); }
  console.log('mounted lesson visual library/detail/impact browser behavior PASS');
} finally {
  if (socket) socket.close(); await stopChild(chrome); if (server) await new Promise((resolve) => server.close(resolve)); if (temp) await rm(temp, { recursive: true, force: true });
}
