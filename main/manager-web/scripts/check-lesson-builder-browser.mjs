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
  server = createServer((request, response) => { const requestPath = request.url.split('?')[0]; const path = normalize(join(buildDir, requestPath === '/' ? 'index.html' : requestPath)); if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; } try { const body = readFileSync(path); response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(body); } catch { response.writeHead(404).end(); } });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const chromeBin = [process.env.CHROME_BIN, join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean).find(existsSync);
  assert.ok(chromeBin, 'Chromium is required');
  chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
  const [debugPort] = (await waitForFile(join(profileDir, 'DevToolsActivePort'))).trim().split('\n');
  const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT' }).then((r) => r.json()); socket = new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let id = 0; const pending = new Map(); const runtimeErrors = []; socket.on('message', (raw) => { const message = JSON.parse(raw); if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text); if (message.id && pending.has(message.id)) { const p = pending.get(message.id); pending.delete(message.id); message.error ? p.reject(new Error(message.error.message)) : p.resolve(message.result); } });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => { const commandId = ++id; pending.set(commandId, { resolve, reject }); socket.send(JSON.stringify({ id: commandId, method, params })); });
  const evaluate = async (expression) => { const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text); return result.result.value; };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  const editorReady = 'Boolean(window.__LESSON_BUILDER_READY__) && document.querySelectorAll(".step-nav__item").length >= 2';
  for (let i = 0; i < 100 && !(await evaluate(editorReady)); i += 1) await new Promise((r) => setTimeout(r, 50));
  const editorDiagnostics = await evaluate('({ signaled: Boolean(window.__LESSON_BUILDER_READY__), lessonId: window.__LESSON_BUILDER_TEST__?.editor?.lessonId, lesson: window.__LESSON_BUILDER_TEST__?.editor?.lesson, editorSteps: window.__LESSON_BUILDER_TEST__?.editor?.steps?.length, route: window.__LESSON_BUILDER_TEST__?.editor?.$route?.fullPath, navItems: document.querySelectorAll(".step-nav__item").length, bodyText: document.body.innerText.slice(0, 500) })');
  assert.equal(await evaluate(editorReady), true, `mounted LessonEditor did not become ready: ${JSON.stringify(editorDiagnostics)}; ${runtimeErrors.join('; ')}`);
  const result = await evaluate(`(async()=>{
    const t=window.__LESSON_BUILDER_TEST__,e=t.editor, tick=()=>new Promise(r=>setTimeout(r,0)),waitFor=async(test)=>{for(let i=0;i<50&&!test();i+=1)await tick();if(!test())throw new Error('browser fixture condition timed out')};
    const setInput=(input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))};
    const testInput=(id)=>{const root=document.querySelector('[data-testid="'+id+'"]')||document.getElementById(id);return root&&root.matches('input,textarea')?root:root?.querySelector('input,textarea')};
    const formItem=(label)=>[...document.querySelectorAll('.interaction-panel .el-form-item')].find(x=>x.querySelector('.el-form-item__label')?.textContent.trim()===label);
    const choose=async(label,text)=>{const item=formItem(label);if(!item)throw new Error('missing form item '+label);item.querySelector('.el-select input').click();await tick();const options=[...document.querySelectorAll('body .el-select-dropdown__item')].filter(x=>x.textContent.trim()===text);const option=options.at(-1);if(!option)throw new Error('missing option '+label+':'+text);option.click();await tick()};
    document.querySelectorAll('.step-nav__item')[1].click();await tick();
    e.doValidate(null,null,{allowUnsafe:true});await tick();
    const readiness=e.$children.find(c=>c.$options.name==='LessonPublishReadiness');
    const readyBeforeEdit=!readiness.metrics.estimateOnly&&readiness.metrics.offlineReady;
    setInput(testInput('lesson-step-prompt'),'Say the red barn');await tick();
    setInput(testInput('lesson-step-subject'),'red barn');await tick();
    setInput(testInput('lesson-step-helper'),'Listen, then speak');await tick();
    setInput(testInput('lesson-step-l1-hint'),'Chỉ dùng khi trẻ cần giúp');await tick();
    document.querySelector('.asset-tile__select').click();await tick();e.keepSharedVisual();await tick();
    [...document.querySelectorAll('.interaction-panel .el-radio-button')].find(x=>x.textContent.includes('8 min')).click();await tick();
    setInput(formItem('English teaching word').querySelector('input'),'barn');await tick();
    await choose('Fun pattern','Mini Story Rescue');
    setInput(formItem('Goal').querySelector('input'),'Help Pip find a home');await tick();
    setInput(formItem('Success reaction').querySelector('input'),'pet.entersBarn');await tick();
    setInput(formItem('Next tease').querySelector('input'),'What comes next?');await tick();
    await choose('Present','Present Left');
    const staleAfterEdit=e.validationResult===null&&readiness.metrics.estimateOnly;
    t.calls.deferNextValidate=true;e.doValidate(null,null,{allowUnsafe:true});await tick();
    document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    t.calls.pendingValidations.shift().ok(t.validation);await tick();
    const validateBeforeSaveIgnored=e.validationResult===null;
    const selectedAfterReload=e.selectedObjectKey;
    const selectedTilePersisted=Boolean(document.querySelector('.asset-tile.selected'));
    e.doValidate(null,null,{allowUnsafe:true});await tick();
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('lesson.previewManifest')).click();await tick();
    const preview=e.$children.find(c=>c.$options.name==='RobotLessonPreview');
    const cinematicVisible=Boolean(document.querySelector('[data-testid="cinematic-design-reference"] iframe'));
    const exactVisible=Boolean(document.querySelector('[data-testid="exact-robot-renderer"] [data-testid="esp-tft-stage"]'));
    const previewText=document.querySelector('[data-testid="exact-robot-renderer"]')?.textContent||'';
    const rendererContractVisible=previewText.includes('teebot-lesson-renderer.v2')&&previewText.includes('Renderer v2')&&previewText.includes('oncePerLessonSession')&&previewText.includes('server');
    const capabilityVisible=previewText.includes('Renderer v2 supported');
    const truthfulExactLabel=previewText.includes('Browser transitions only illustrate timing');
    const stateControls=[...document.querySelectorAll('[data-testid="visual-state-control"]')].map(x=>x.dataset.state);
    const degradedControls=[...document.querySelectorAll('[data-testid="degraded-reason-control"]')].map(x=>x.dataset.reason);
    const stateMotionClasses=[];for(const button of document.querySelectorAll('[data-testid="visual-state-control"]')){button.click();await tick();stateMotionClasses.push([button.dataset.state,document.querySelector('.layer-robotOverlay')?.className||'missing'])}
    [...document.querySelectorAll('.preview-toolbar button')].find(x=>x.textContent.trim()==='Near miss').click();await tick();
    setInput(formItem('English teaching word').querySelector('input'),'barns');await tick();
    const previewClearedOnEdit=e.previewManifest===null;
    e.doValidate(null,null,{allowUnsafe:true});await tick();
    t.calls.failNextUpdate=true;document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    const staleAfterFailure=e.validationResult===null&&readiness.metrics.estimateOnly;
    document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    await waitFor(()=>!e.savingStep);
    t.calls.deferNextValidate=true;e.doValidate(null,null,{allowUnsafe:true});await tick();
    setInput(formItem('English teaching word').querySelector('input'),'race');await tick();
    t.calls.pendingValidations.shift().ok(t.validation);await tick();
    const deferredValidationIgnored=e.validationResult===null;
    t.calls.deferNextUpdate=true;document.querySelector('.lesson-studio__toolbar .el-button').click();await tick();
    // Model the post-update/pre-refresh window where controls are editable again.
    e.savingStep=false;await tick();
    setInput(formItem('English teaching word').querySelector('input'),'race new');await tick();
    const pending=t.calls.pendingUpdates.shift();pending.ok({...pending.payload,stepKey:pending.stepKey});await tick();
    const newerDraftDirty=e.selectedStepDirty,newerDraftWord=e.selectedAuthoring.teachingWord.text;
    e.validationResult={budgets:{espTft:{errors:[{code:'branch-termination',message:'Step s2 has a non-terminating branch',stepKey:'s2'}],warnings:[{code:'background-budget',message:'Background exceeds recommendation',assetKey:'scene.farm'}],metrics:t.validation.budgets.espTft.metrics}}};await tick();
    const readinessText=document.querySelector('.readiness').textContent;
    const validationIssuesRendered=readinessText.includes('branch-termination')&&readinessText.includes('Step s2 has a non-terminating branch')&&readinessText.includes('background-budget')&&readinessText.includes('scene.farm');
    return{selected:e.selectedStepIndex,filters:t.calls.visualFilters,patch:t.calls.update[0],failedPatch:t.calls.update[1],updateCount:t.calls.update.length,metrics:t.validation.budgets.espTft.metrics,preview:[preview.stepIndex,preview.manifest.manifestVersion,preview.manifest.profile,preview.initialPath,e.previewPath.path],cinematicVisible,exactVisible,rendererContractVisible,capabilityVisible,truthfulExactLabel,stateControls,stateMotionClasses,degradedControls,readyBeforeEdit,staleAfterEdit,staleAfterFailure,selectedAfterReload,selectedTilePersisted,errors:t.calls.errors,previewClearedOnEdit,deferredValidationIgnored,newerDraftDirty,newerDraftWord,validateBeforeSaveIgnored,validationIssuesRendered}
  })()`);
  assert.equal(result.selected, 1); assert.deepEqual(result.filters, [{ category: 'teachingObject', profile: 'espTft' }, { category: 'teachingObject', profile: 'espTft' }]);
  assert.deepEqual(result.patch, { lessonId: 'lesson-1', stepKey: 's2', payload: { stepKey: 's2', stepType: 'repeat', prompt: 'Say the red barn', subject: 'red barn', helperText: 'Listen, then speak', l1TransferHint: 'Chỉ dùng khi trẻ cần giúp', visualRefs: [], stepBody: { durationSec: 12, durationPreset: 8, teachingWord: { text: 'BARN', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' }, interaction: { template: 'safeSpeaking', maxAttempts: 3, listenTimeoutSec: 6, correctThreshold: 0.85, braveTryThreshold: 0.7, funPattern: 'miniStoryRescue' }, motion: { present: 'presentLeft', listen: 'listen', correct: 'celebrate', nearMiss: 'encourage', incorrect: 'tryAgain' }, storyBeat: { goal: 'Help Pip find a home', successReaction: 'pet.entersBarn', nextTease: 'What comes next?' }, teachingObject: { primaryWord: 'BARN', asset: { key: 'object.barn', src: 'sd://shared/barn.png', sha256: 'abc123', version: 2, bytes: 60000 } } } } });
  assert.equal(result.updateCount, 4); assert.deepEqual(result.failedPatch.payload.visualRefs, []); assert.equal(result.failedPatch.payload.stepBody.teachingWord.text, 'BARNS');
  assert.equal(result.metrics.packBytes, 222000); assert.equal(result.metrics.uniqueAssetCount, 7); assert.equal(result.metrics.sharedAssetCount, 2); assert.equal(result.metrics.estimatedVisualPeakBytes, 640000); assert.equal(result.metrics.offlineReady, true); assert.equal(result.metrics.allPathsTerminate, true); assert.deepEqual(result.preview, [1, 'teebot-lesson-renderer.v2', 'espTft', 'correct', 'nearMiss']);
  assert.equal(result.cinematicVisible, true); assert.equal(result.exactVisible, true); assert.equal(result.rendererContractVisible, true); assert.equal(result.capabilityVisible, true); assert.equal(result.truthfulExactLabel, true);
  assert.deepEqual(result.stateControls, ['teach', 'listen', 'thinking', 'correct', 'nearMiss', 'incorrect', 'retry', 'celebrate', 'completion']);
  assert.deepEqual(result.stateMotionClasses.map(([state,className])=>[state,className.split(' ').find(name=>/^motion-(?:tilt|nod|shake)$/.test(name))]), [['teach','motion-tilt'],['listen','motion-tilt'],['thinking','motion-tilt'],['correct','motion-nod'],['nearMiss','motion-nod'],['incorrect','motion-shake'],['retry','motion-shake'],['celebrate','motion-nod'],['completion','motion-nod']]);
  assert.deepEqual(result.degradedControls, ['missingOverlay', 'animationStartFailed', 'phaseTimeout', 'reducedMotion', 'unsupportedContract', 'assetIdentityMismatch', 'insufficientHeap']);
  assert.equal(result.readyBeforeEdit, true, 'readiness should be authoritative before edits');
  assert.equal(result.staleAfterEdit, true, 'editing should stale validation');
  assert.equal(result.staleAfterFailure, true, 'failed save should keep validation stale');
  assert.equal(result.selectedAfterReload, 'object.barn', 'selected object should survive reload');
  assert.equal(result.selectedTilePersisted, true, 'selected object tile should remain selected');
  assert.deepEqual(result.errors, ['forced update failure']);
  assert.equal(result.previewClearedOnEdit, true, 'editing should clear the generated preview');
  assert.equal(result.deferredValidationIgnored, true, 'stale deferred validation should be ignored');
  assert.equal(result.newerDraftDirty, true, `a newer draft should stay dirty (word: ${result.newerDraftWord})`);
  assert.equal(result.newerDraftWord, 'RACE NEW', 'a newer draft should survive an older save response');
  assert.equal(result.validateBeforeSaveIgnored, true);
  assert.equal(result.validationIssuesRendered, true);

  const disabledResult = await evaluate('window.__MOUNT_DISABLED_LESSON_EDITOR__()');
  assert.deepEqual(disabledResult, {
    visualCalls: 0,
    previewCalls: 0,
    sharedPickerVisible: false,
    previewButtonVisible: false,
  });
  assert.deepEqual(await evaluate('window.__TEST_CAPABILITY_ROUTE_LOGOUT_RACE__()'), {
    route: 'login',
    visualLibraryLoaded: false,
  });
  console.log('mounted visual LessonEditor selection, authoring PATCH, readiness, and preview props PASS');
} finally {
  if (socket) socket.close(); await stopChild(chrome); if (server) await new Promise((resolve) => server.close(resolve)); if (temp) await rm(temp, { recursive: true, force: true });
}
