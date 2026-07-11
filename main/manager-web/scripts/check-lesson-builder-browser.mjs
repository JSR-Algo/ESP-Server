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
  temp = await mkdtemp(join(tmpdir(), 'tbot-lesson-builder-'));
  const buildDir = join(temp, 'build'); const profileDir = join(temp, 'chrome'); await mkdir(profileDir, { recursive: true });
  const build = spawnSync(process.execPath, [join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'), 'build', '--dest', buildDir, '--no-clean', join(managerRoot, 'tests/browser/lesson-builder-main.js')], { cwd: managerRoot, encoding: 'utf8', timeout: 120000 });
  assert.equal(build.status, 0, `mounted harness build failed:\n${build.stdout}\n${build.stderr}`);
  server = createServer((request, response) => { const requestPath = request.url.split('?')[0]; const path = normalize(join(buildDir, requestPath === '/' ? 'index.html' : requestPath)); if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; } try { response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(readFileSync(path)); } catch { response.writeHead(404).end(); } });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const chromeBin = [process.env.CHROME_BIN, join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean).find(existsSync);
  assert.ok(chromeBin, 'Chromium is required');
  chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
  const [debugPort] = (await waitForFile(join(profileDir, 'DevToolsActivePort'))).trim().split('\n');
  const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT' }).then((r) => r.json()); socket = new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let id = 0; const pending = new Map(); const runtimeErrors = []; socket.on('message', (raw) => { const message = JSON.parse(raw); if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text); if (message.id && pending.has(message.id)) { const p = pending.get(message.id); pending.delete(message.id); message.error ? p.reject(new Error(message.error.message)) : p.resolve(message.result); } });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => { const commandId = ++id; pending.set(commandId, { resolve, reject }); socket.send(JSON.stringify({ id: commandId, method, params })); });
  const evaluate = async (expression) => { const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.text); return result.result.value; };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  for (let i = 0; i < 100 && !(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)')); i += 1) await new Promise((r) => setTimeout(r, 50));
  assert.equal(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)'), true, `mounted LessonEditor did not become ready: ${runtimeErrors.join('; ')}`);
  const result = await evaluate(`(async()=>{
    const t=window.__LESSON_BUILDER_TEST__,e=t.editor, tick=()=>new Promise(r=>setTimeout(r,0));
    const setInput=(input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))};
    const formItem=(label)=>[...document.querySelectorAll('.interaction-panel .el-form-item')].find(x=>x.querySelector('.el-form-item__label')?.textContent.trim()===label);
    const choose=async(label,text)=>{const item=formItem(label);if(!item)throw new Error('missing form item '+label);item.querySelector('.el-select input').click();await tick();const options=[...document.querySelectorAll('body .el-select-dropdown__item')].filter(x=>x.textContent.trim()===text);const option=options.at(-1);if(!option)throw new Error('missing option '+label+':'+text);option.click();await tick()};
    document.querySelectorAll('.step-nav__item')[1].click();await tick();
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('lesson.validate')).click();await tick();
    const readiness=e.$children.find(c=>c.$options.name==='LessonPublishReadiness');
    const readyBeforeEdit=!readiness.metrics.estimateOnly&&readiness.metrics.offlineReady;
    document.querySelector('.asset-tile__select').click();await tick();
    [...document.querySelectorAll('.interaction-panel .el-radio-button')].find(x=>x.textContent.includes('8 min')).click();await tick();
    setInput(formItem('English teaching word').querySelector('input'),'barn');await tick();
    await choose('Fun pattern','Mini Story Rescue');
    setInput(formItem('Goal').querySelector('input'),'Help Pip find a home');await tick();
    setInput(formItem('Success reaction').querySelector('input'),'pet.entersBarn');await tick();
    setInput(formItem('Next tease').querySelector('input'),'What comes next?');await tick();
    await choose('Present','Present Left');
    const staleAfterEdit=e.validationResult===null&&readiness.metrics.estimateOnly;
    document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    const selectedAfterReload=e.selectedObjectKey;
    const selectedTilePersisted=Boolean(document.querySelector('.asset-tile.selected'));
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('lesson.validate')).click();await tick();
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('lesson.previewManifest')).click();await tick();
    const preview=e.$children.find(c=>c.$options.name==='RobotLessonPreview');
    [...document.querySelectorAll('.preview-toolbar button')].find(x=>x.textContent.trim()==='Near miss').click();await tick();
    setInput(formItem('English teaching word').querySelector('input'),'barns');await tick();
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('lesson.validate')).click();await tick();
    t.calls.failNextUpdate=true;document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    const staleAfterFailure=e.validationResult===null&&readiness.metrics.estimateOnly;
    return{selected:e.selectedStepIndex,filters:t.calls.visualFilters,patch:t.calls.update[0],failedPatch:t.calls.update[1],updateCount:t.calls.update.length,metrics:t.validation.budgets.espTft.metrics,preview:[preview.stepIndex,preview.manifest.profile,preview.initialPath,e.previewPath.path],readyBeforeEdit,staleAfterEdit,staleAfterFailure,selectedAfterReload,selectedTilePersisted,errors:t.calls.errors}
  })()`);
  assert.equal(result.selected, 1); assert.deepEqual(result.filters, [{ category: 'teachingObject', profile: 'espTft' }]);
  assert.deepEqual(result.patch, { lessonId: 'lesson-1', stepKey: 's2', payload: { stepKey: 's2', stepType: 'repeat', prompt: 'Say barn', subject: 'barn', visualRefs: [{ slot: 'teachingObject', assetVersionId: '00000000-0000-4000-8000-000000000002' }], stepBody: { durationSec: 12, durationPreset: 8, teachingWord: { text: 'BARN', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' }, interaction: { template: 'safeSpeaking', maxAttempts: 3, listenTimeoutSec: 6, correctThreshold: 0.85, braveTryThreshold: 0.7, funPattern: 'miniStoryRescue' }, motion: { present: 'presentLeft', listen: 'listen', correct: 'celebrate', nearMiss: 'encourage', incorrect: 'tryAgain' }, storyBeat: { goal: 'Help Pip find a home', successReaction: 'pet.entersBarn', nextTease: 'What comes next?' } } } });
  assert.equal(result.updateCount, 2); assert.equal(result.failedPatch.payload.visualRefs, undefined); assert.equal(result.failedPatch.payload.stepBody.teachingWord.text, 'BARNS');
  assert.equal(result.metrics.packBytes, 222000); assert.equal(result.metrics.uniqueAssetCount, 7); assert.equal(result.metrics.sharedAssetCount, 2); assert.equal(result.metrics.estimatedVisualPeakBytes, 640000); assert.equal(result.metrics.offlineReady, true); assert.equal(result.metrics.allPathsTerminate, true); assert.deepEqual(result.preview, [1, 'espTft', 'correct', 'nearMiss']);
  assert.equal(result.readyBeforeEdit, true); assert.equal(result.staleAfterEdit, true); assert.equal(result.staleAfterFailure, true); assert.equal(result.selectedAfterReload, 'object.barn'); assert.equal(result.selectedTilePersisted, true); assert.deepEqual(result.errors, ['forced update failure']);
  console.log('mounted visual LessonEditor selection, authoring PATCH, readiness, and preview props PASS');
} finally {
  if (socket) socket.close(); await stopChild(chrome); if (server) await new Promise((resolve) => server.close(resolve)); if (temp) await rm(temp, { recursive: true, force: true });
}
