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
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
let temp; let server; let chrome; let socket;
async function stopChild(child) { if (!child || child.exitCode !== null) return; child.kill('SIGTERM'); await Promise.race([new Promise((resolve) => child.once('exit', resolve)), new Promise((resolve) => setTimeout(resolve, 1500))]); if (child.exitCode === null) child.kill('SIGKILL'); }
async function waitForFile(path) { for (let index = 0; index < 200; index += 1) { if (existsSync(path)) return readFileSync(path, 'utf8'); await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error(`timeout: ${path}`); }

try {
  temp = await mkdtemp(join(tmpdir(), 'tbot-tvideo-journey-'));
  const buildDir = join(temp, 'build'); const profileDir = join(temp, 'chrome'); await mkdir(profileDir, { recursive: true });
  const build = spawnSync(process.execPath, [join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'), 'build', '--dest', buildDir, '--no-clean', join(managerRoot, 'tests/browser/lesson-builder-main.js')], { cwd: managerRoot, encoding: 'utf8', timeout: 120000 });
  assert.equal(build.status, 0, `mounted journey harness build failed:\n${build.stdout}\n${build.stderr}`);
  server = createServer((request, response) => { const requestPath = request.url.split('?')[0]; const path = normalize(join(buildDir, requestPath === '/' ? 'index.html' : requestPath)); if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; } let body; try { body = readFileSync(path); } catch { response.writeHead(404).end(); return; } response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(body); });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const chromeBin = [process.env.CHROME_BIN, join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean).find(existsSync);
  assert.ok(chromeBin, 'Chromium is required');
  chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
  const [debugPort] = (await waitForFile(join(profileDir, 'DevToolsActivePort'))).trim().split('\n');
  const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT' }).then((response) => response.json());
  socket = new WebSocket(target.webSocketDebuggerUrl); await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let commandId = 0; const pending = new Map(); const runtimeErrors = [];
  socket.on('message', (raw) => { const message = JSON.parse(raw); if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text); if (message.id && pending.has(message.id)) { const callbacks = pending.get(message.id); pending.delete(message.id); message.error ? callbacks.reject(new Error(message.error.message)) : callbacks.resolve(message.result); } });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => { const id = ++commandId; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => { const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text); return result.result.value; };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  for (let index = 0; index < 100 && !(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)')); index += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)'), true, `LessonEditor fixture did not mount: ${runtimeErrors.join('; ')}`);

  const result = await evaluate(`(async()=>{
    const t=window.__LESSON_BUILDER_TEST__,e=t.editor,tick=()=>new Promise(r=>setTimeout(r,0)),wait=async(test)=>{for(let i=0;i<80&&!test();i+=1)await tick();if(!test())throw new Error('journey fixture timeout')};
    e.previewManifest=null;
    const legacy={};
    for(const version of ['teebot-lesson-renderer.v1','teebot-lesson-renderer.v2','teebot-lesson-renderer.v3']){e.lesson.manifestVersion=version;await e.$nextTick();legacy[version]=Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]'))}
    e.lesson.manifestVersion='teebot-lesson-renderer.v4';await e.$nextTick();e.loadTVideoJourney();await wait(()=>!e.tvideoJourneyLoading&&Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]')));
    const journey=e.$children.find(child=>child.$options.name==='TVideoJourneyEditor');
    const unconfigured=[journey.draft.steps.length,journey.draft.steps.map(step=>step.stepKey),journey.draft.boundedContext.maxTurns];
    const hardcodedVisible=['DETERMINISTIC SIMULATOR','PREVIEW · NOT PUBLISH AUTHORITY','BACKEND DERIVATIVES','EXACT TWO-STEP CONTENT'].filter(text=>document.body.innerText.includes(text));
    const tabs=[...document.querySelectorAll('.journey-editor__tabs [role="tab"]')];const tabStates=[];for(const tab of tabs){tab.click();await tick();tabStates.push([tab.textContent.trim(),tab.getAttribute('aria-selected'),journey.activeTab])}
    tabs[0].click();await tick();document.querySelector('.journey-editor__tabs').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();const keyboard=[journey.activeTab,document.activeElement.id,document.activeElement.getAttribute('aria-selected')];
    journey.activeTab='sources';await tick();const sourceGroups=[...document.querySelectorAll('.source-selectors>div')];const clickAsset=async(group,text)=>{const button=[...sourceGroups[group].querySelectorAll('.asset-tile__select')].find(tile=>tile.textContent.includes(text));button.click();await tick()};await clickAsset(0,'journey.farm.alt');await clickAsset(1,'journey.barn');await clickAsset(2,'journey.robot.flight');const picker=[journey.draft.assets.background.assetVersionId,journey.selectedStep.teachingObject.assetVersionId,journey.selectedRobotClip.assetVersionId,e.tvideoJourneyDirty];
    journey.activeTab='path';await tick();const pathVideo=document.querySelector('.path-stage__background');await wait(()=>pathVideo.readyState>=2||journey.pathBackgroundError);const pathStage=journey.$refs.pathStage;const rect=pathStage.getBoundingClientRect();let emitted=null;journey.$on('input',value=>{emitted=value});journey.startPathDrag('flight-start',{pointerType:'touch',pointerId:9,currentTarget:{}});journey.movePathPoint({clientX:rect.right+80,clientY:rect.top-80});journey.stopPathDrag();await tick();const path=[journey.draft.scenePath.flightIngress.start.x,journey.draft.scenePath.flightIngress.start.y,emitted.scenePath.flightIngress.start.x,emitted.scenePath.flightIngress.start.y,Boolean(pathVideo),pathVideo?.muted,pathVideo?.hasAttribute('playsinline'),pathVideo?getComputedStyle(pathVideo).objectFit:null,pathVideo.readyState,journey.pathBackgroundError];
    journey.activeTab='conversation';journey.selectedStepIndex=0;await tick();const conversation=journey.$children.find(child=>child.$options.name==='TVideoConversationPreview');conversation.branch='retry_level_3';await tick();const branch=[conversation.result.branch,conversation.result.coachingLevel,conversation.result.cueId,conversation.result.progressResult,conversation.$el.textContent.includes(conversation.result.nextIntent)];
    journey.activeTab='flattened';await tick();const robot=journey.$children.find(child=>child.$options.name==='TVideoRobotPreview');robot.toggle();await new Promise(r=>setTimeout(r,240));robot.toggle();const pausedClock=robot.clockMs;await new Promise(r=>setTimeout(r,160));const stableClock=robot.clockMs;robot.replay();const robotState=[pausedClock,pausedClock%100,stableClock,robot.clockMs,robot.playing];
    t.calls.deferNextJourneySave=true;const saveBefore=t.calls.journeySaves.length;const saveFirst=e.saveTVideoJourney(journey.normalizedDraft()),saveSecond=e.saveTVideoJourney(journey.normalizedDraft()),savingDuring=e.tvideoJourneySaving,saveDelta=t.calls.journeySaves.length-saveBefore;const pending=t.calls.pendingJourneySaves.shift();pending.ok({state:'configured',lessonId:'lesson-1',lessonVersion:2,sourceRevision:2,cinematicSourceRevision:2,journey:pending.payload,set:{state:'invalid',issues:[]},statuses:[],publishReady:false});await tick();const saveSuccess=[saveFirst,saveSecond,savingDuring,saveDelta,e.tvideoJourneySaving,e.tvideoJourneyResponse.state];
    t.calls.failNextJourneySave=true;e.saveTVideoJourney(journey.normalizedDraft());await tick();const saveError=[e.tvideoJourneySaving,e.tvideoJourneyError.includes('ASSET_PIN_INVALID'),e.tvideoJourneyError.includes('flight')];
    e.hasUnsafeProofState=()=>false;e.readinessReady=true;e.validationResult={valid:true};e.validationProofVersion=e.proofVersion;e.previewManifest={checksum:'proof',manifest:{manifestVersion:'teebot-lesson-renderer.v4',cinematicPhases:[{phaseId:'opening'}]}};e.previewProofVersion=e.proofVersion;e.simulationEvidence={};e.simulationProofVersion=e.proofVersion;e.validSimulationEvidence=()=>true;e.flattenedDerivativeStatus={sourceRevision:5,phases:[{phaseId:'opening',sourceRevision:5,state:'ready',output:{url:'/opening.mp4'}}]};e.flattenedDerivativeCurrentSourceRevision=5;
    e.tvideoJourneyResponse={...e.tvideoJourneyResponse,publishReady:false};await tick();const gateFalse=e.canPublishCurrentProof();e.tvideoJourneyResponse={...e.tvideoJourneyResponse,publishReady:true};await tick();const gateTrue=e.canPublishCurrentProof();
    return{legacy,unconfigured,hardcodedVisible,tabStates,keyboard,picker,path,branch,robotState,saveSuccess,saveError,gate:[gateFalse,gateTrue],runtimeText:document.body.innerText.slice(0,200)};
  })()`);

  assert.deepEqual(result.legacy, { 'teebot-lesson-renderer.v1': false, 'teebot-lesson-renderer.v2': false, 'teebot-lesson-renderer.v3': false });
  assert.deepEqual(result.unconfigured, [2, ['barn', 'hay'], 2]);
  assert.deepEqual(result.hardcodedVisible, [], 'new visible copy must route through i18n');
  assert.deepEqual(result.tabStates.map((row) => row[0]), ['lesson.tvideoJourney.tab.sources', 'lesson.tvideoJourney.tab.path', 'lesson.tvideoJourney.tab.conversation', 'lesson.tvideoJourney.tab.flattened']);
  assert.ok(result.tabStates.every((row) => row[1] === 'true'));
  assert.deepEqual(result.keyboard, ['path', 'tvideo-tab-path', 'true']);
  assert.deepEqual(result.picker, ['10000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', true]);
  assert.deepEqual(result.path.slice(0, 8), [1, 0, 1, 0, true, true, true, 'cover']);
  assert.ok(result.path[8] >= 2 && result.path[9] === false, `path MP4 did not load: ${JSON.stringify(result.path)}`);
  assert.deepEqual(result.branch, ['retry_level_3', 3, 'barn-retry-level-3', 'stay', true]);
  assert.ok(result.robotState[0] >= 100 && result.robotState[0] % 100 === 0);
  assert.deepEqual(result.robotState.slice(1), [0, result.robotState[0], 0, false]);
  assert.deepEqual(result.saveSuccess, [true, false, true, 1, false, 'configured']);
  assert.deepEqual(result.saveError, [false, true, true]);
  assert.deepEqual(result.gate, [false, true]);
  assert.deepEqual(runtimeErrors, [], `mounted journey runtime errors: ${runtimeErrors.join('; ')}`);
  console.log('mounted TVideo Journey tabs, media, editing, simulation, clock, save, gate, accessibility, and legacy isolation PASS (11 groups)');
} finally {
  if (socket) socket.close(); await stopChild(chrome); if (server) await new Promise((resolve) => server.close(resolve)); if (temp) await rm(temp, { recursive: true, force: true });
}
