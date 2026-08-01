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
  let commandId = 0; const pending = new Map(); const runtimeErrors = []; const vueConsoleErrors = [];
  socket.on('message', (raw) => {
    const message = JSON.parse(raw);
    if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    if (message.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(message.params.type)) {
      const text = message.params.args.map((arg) => arg.value || arg.description || '').join(' ');
      if (/\bVue\b|\[Vue warn\]|Error in/.test(text)) vueConsoleErrors.push(text);
    }
    if (message.id && pending.has(message.id)) { const callbacks = pending.get(message.id); pending.delete(message.id); message.error ? callbacks.reject(new Error(message.error.message)) : callbacks.resolve(message.result); }
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => { const id = ++commandId; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => { const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text); return result.result.value; };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  for (let index = 0; index < 100 && !(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)')); index += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(await evaluate('Boolean(window.__LESSON_BUILDER_READY__)'), true, `LessonEditor fixture did not mount: ${runtimeErrors.join('; ')}`);

  const result = await evaluate(`(async()=>{
    const t=window.__LESSON_BUILDER_TEST__,e=t.editor,tick=()=>new Promise(r=>setTimeout(r,0)),wait=async(test)=>{for(let i=0;i<80&&!test();i+=1)await new Promise(r=>setTimeout(r,25));if(!test())throw new Error('journey fixture timeout')};
    e.previewManifest=null;
    const legacy={};
    for(const version of ['teebot-lesson-renderer.v1','teebot-lesson-renderer.v2','teebot-lesson-renderer.v3']){e.lesson.manifestVersion=version;await e.$nextTick();legacy[version]=Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]'))}
    e.lesson.manifestVersion='teebot-lesson-renderer.v4';await e.$nextTick();e.loadTVideoJourney();await wait(()=>!e.tvideoJourneyLoading&&Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]')));
    const journey=e.$children.find(child=>child.$options.name==='TVideoJourneyEditor');
    const conversation=journey.$children.find(child=>child.$options.name==='TVideoConversationPreview');const branchIds=['target','meaning_vi','related','silence','uncertain','retry_level_1','retry_level_2','retry_level_3'];const localized={};
    for(const locale of ['en','vi']){await window.__SET_TEST_LOCALE__(locale);journey.activeTab='path';journey.selectedPathPoint='safe-zone';await tick();const pathLabels=[...journey.$el.querySelectorAll('.path-tools .el-form-item__label')].map(label=>label.textContent.trim());journey.activeTab='conversation';await tick();const branchLabels=[];for(const branchId of branchIds){conversation.branch=branchId;await tick();branchLabels.push([branchId,conversation.$el.querySelector('.el-select input').value])}journey.activeTab='sources';await tick();const contentLabels=[...journey.$el.querySelectorAll('.journey-content .el-form-item__label')].map(label=>label.textContent.trim());const pronunciationInput=journey.$el.querySelector('.journey-content .el-select input');const segmentsLabel=pronunciationInput.value;journey.setPronunciationMode(journey.draft.steps[0],'approvedPhonemes');await tick();const phonemesLabel=pronunciationInput.value;journey.setPronunciationMode(journey.draft.steps[0],'approvedSegments');localized[locale]={pathLabels,contentLabels,segmentsLabel,phonemesLabel,branchLabels}}
    await window.__SET_TEST_LOCALE__('vi');
    const unconfigured=[journey.draft.steps.length,journey.draft.steps.map(step=>step.stepKey),journey.draft.boundedContext.maxTurns];
    const hardcodedVisible=['DETERMINISTIC SIMULATOR','PREVIEW · NOT PUBLISH AUTHORITY','BACKEND DERIVATIVES','EXACT TWO-STEP CONTENT'].filter(text=>document.body.innerText.includes(text));
    const tabs=[...document.querySelectorAll('.journey-editor__tabs [role="tab"]')];const tabStates=[];for(const tab of tabs){tab.click();await tick();tabStates.push([tab.textContent.trim(),tab.getAttribute('aria-selected'),journey.activeTab])}
    const rawTabKeys=tabStates.map(row=>row[0]).filter(label=>label.startsWith('lesson.tvideoJourney.'));
    const pinnedVersionIds=['10000000-0000-4000-8000-000000000001','30000000-0000-4000-8000-000000000001','30000000-0000-4000-8000-000000000002','20000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000002','20000000-0000-4000-8000-000000000003','20000000-0000-4000-8000-000000000004'];
    const pinnedAssets=pinnedVersionIds.map(versionId=>{const asset=t.sharedAssets.find(row=>row.versionId===versionId);return{versionId,url:asset&&asset.url,sha256:asset&&asset.sha256,mimeType:asset&&asset.mimeType}});
    tabs[0].click();await tick();document.querySelector('.journey-editor__tabs').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();const keyboard=[journey.activeTab,document.activeElement.id,document.activeElement.getAttribute('aria-selected')];
    await window.__SET_TEST_LOCALE__('en');
    journey.activeTab='sources';await tick();const sourceGroups=[...document.querySelectorAll('.source-selectors>div')];const clickAsset=async(group,text)=>{const button=[...sourceGroups[group].querySelectorAll('.asset-tile__select')].find(tile=>tile.textContent.includes(text));button.click();await tick()};await clickAsset(0,'journey.farm.alt');await clickAsset(1,'journey.barn');await clickAsset(2,'journey.robot.flight');const picker=[journey.draft.assets.background.assetVersionId,journey.selectedStep.teachingObject.assetVersionId,journey.selectedRobotClip.assetVersionId,e.tvideoJourneyDirty];
    journey.activeTab='path';await tick();const pathVideo=document.querySelector('.path-stage__background');await wait(()=>pathVideo.readyState>=2||journey.pathBackgroundError);const pathStage=journey.$refs.pathStage;const rect=pathStage.getBoundingClientRect();let emitted=null;journey.$on('input',value=>{emitted=value});journey.startPathDrag('flight-start',{pointerType:'touch',pointerId:9,currentTarget:{}});journey.movePathPoint({clientX:rect.right+80,clientY:rect.top-80});journey.stopPathDrag();await tick();const path=[journey.draft.scenePath.flightIngress.start.x,journey.draft.scenePath.flightIngress.start.y,emitted.scenePath.flightIngress.start.x,emitted.scenePath.flightIngress.start.y,Boolean(pathVideo),pathVideo?.muted,pathVideo?.hasAttribute('playsinline'),pathVideo?getComputedStyle(pathVideo).objectFit:null,pathVideo.readyState,journey.pathBackgroundError];
    journey.activeTab='conversation';journey.selectedStepIndex=0;await tick();conversation.branch='retry_level_3';await tick();const branch=[conversation.result.branch,conversation.result.coachingLevel,conversation.result.cueId,conversation.result.progressResult,conversation.$el.textContent.includes(conversation.result.nextIntent)];
    journey.activeTab='flattened';await tick();const robot=journey.$children.find(child=>child.$options.name==='TVideoRobotPreview');robot.toggle();await new Promise(r=>setTimeout(r,240));robot.toggle();const pausedClock=robot.clockMs;await new Promise(r=>setTimeout(r,160));const stableClock=robot.clockMs;robot.replay();const robotState=[pausedClock,pausedClock%100,stableClock,robot.clockMs,robot.playing];
    const sampleRobot=(effect,timeMs)=>{robot.cueId=robot.cues.find(cue=>cue.effect===effect).cueId;robot.clockMs=timeMs;const state=robot.previewFrameState;return{effect:state.effect,phase:state.phase,role:robot.robotRole,shadow:state.shadow.visible,puff:state.puff.visible,squashY:state.robot.squashY,dots:state.progressDots.map(dot=>dot.active),objectVisible:state.object.visible,cardVisible:state.card.visible,cardVariant:state.card.variant,listeningGlow:state.card.listeningGlow,thinking:state.card.thinking,correctChip:state.card.correctChip,confetti:state.confetti.visible,retryLevel:state.card.retryLevel,pulse:state.card.pulse,pulsePx:state.card.pulsePx,pulseCount:state.card.pulseCount,wordTransition:state.wordTransition}};
    const robotJourneyStates={flight:sampleRobot('opening',0),landing:sampleRobot('opening',3400),walk:sampleRobot('opening',4600),arrived:sampleRobot('opening',8800),listen:sampleRobot('listen',600),thinking:sampleRobot('thinking',600),correct:sampleRobot('correct',600),retry1:sampleRobot('retry-level-1',600),retry2:sampleRobot('retry-level-2',600),retry3:sampleRobot('retry-level-3',600),transition:sampleRobot('word-transition',600)};
    journey.draft.steps[1].teachingObject.assetVersionId='30000000-0000-4000-8000-000000000002';robot.cueId='hay-teach';robot.clockMs=400;const cueBinding=[robot.step.stepKey,robot.objectUrl,robot.cardCopy(robot.previewFrameState)];
    t.calls.deferNextJourneySave=true;const saveBefore=t.calls.journeySaves.length;const saveFirst=e.saveTVideoJourney(journey.normalizedDraft()),saveSecond=e.saveTVideoJourney(journey.normalizedDraft()),savingDuring=e.tvideoJourneySaving,saveDelta=t.calls.journeySaves.length-saveBefore;const pending=t.calls.pendingJourneySaves.shift();pending.ok({state:'configured',lessonId:'lesson-1',lessonVersion:2,sourceRevision:2,cinematicSourceRevision:2,journey:pending.payload,set:{state:'invalid',issues:[]},statuses:[],publishReady:false});await tick();const saveSuccess=[saveFirst,saveSecond,savingDuring,saveDelta,e.tvideoJourneySaving,e.tvideoJourneyResponse.state];
    t.calls.failNextJourneySave=true;e.saveTVideoJourney(journey.normalizedDraft());await tick();const saveError=[e.tvideoJourneySaving,e.tvideoJourneyError.includes('ASSET_PIN_INVALID'),e.tvideoJourneyError.includes('flight')];
    Object.assign(e,{editorDestroying:false,promptDirty:false,stepDialogVisible:false,renameVisible:false,sharedImpactVisible:false,sharedImpactReconciling:false,savingLessonVisuals:false,pendingLessonVisualPair:null,validating:false,previewing:false,rebindingSharedVisual:false,deletingStepKey:null,renaming:false,publishing:false,publishPreparing:false,assetMutationTokens:{},tvideoJourneyDirty:false,tvideoJourneySaving:false});
    e.readinessReady=true;e.validationResult={valid:true};e.validationProofVersion=e.proofVersion;e.previewManifest={checksum:'proof',manifest:{manifestVersion:'teebot-lesson-renderer.v4',cinematicPhases:[{phaseId:'opening'}]}};e.previewProofVersion=e.proofVersion;e.simulationEvidence={};e.simulationProofVersion=e.proofVersion;e.validSimulationEvidence=()=>true;e.flattenedDerivativeStatus={sourceRevision:5,phases:[{phaseId:'opening',sourceRevision:5,state:'ready',output:{url:'/opening.mp4'}}]};e.flattenedDerivativeCurrentSourceRevision=5;e.tvideoJourneyResponse={...e.tvideoJourneyResponse,publishReady:true};
    const proofButtons=()=>{const buttons=[...document.querySelectorAll('.right-operations button')];return['Validate','Preview','Publish'].map(label=>buttons.find(button=>button.textContent.includes(label)))};
    const gatedProof=async(flag)=>{e.tvideoJourneyDirty=false;e.tvideoJourneySaving=false;e[flag]=true;await tick();const [validateButton,previewButton,publishButton]=proofButtons();const before=[t.calls.validate,t.calls.preview];const started=[e.doValidate(),e.doPreview()];return[e.proofActionsDisabled,validateButton.disabled,previewButton.disabled,publishButton.disabled,started,t.calls.validate-before[0],t.calls.preview-before[1],e.canPublishCurrentProof()]};
    const dirtyProof=await gatedProof('tvideoJourneyDirty');const savingProof=await gatedProof('tvideoJourneySaving');e.tvideoJourneyDirty=false;e.tvideoJourneySaving=false;await tick();const restoredButtons=proofButtons();const restoredProof=[e.proofActionsDisabled,...restoredButtons.map(button=>button.disabled),e.canPublishCurrentProof()];
    e.tvideoJourneyResponse={...e.tvideoJourneyResponse,publishReady:false};await tick();const gateFalse=e.canPublishCurrentProof();e.tvideoJourneyResponse={...e.tvideoJourneyResponse,publishReady:true};await tick();const gateTrue=e.canPublishCurrentProof();
    e.tvideoJourneyDirty=true;e.tvideoJourneySaving=true;e.tvideoJourneyError='stale';e.tvideoJourneySaveMessage='stale';e.tvideoJourneyAssets=[{assetId:'stale'}];
    const routeReset=[];for(const version of ['v1','v2','v3']){await e.$router.push({path:'/',query:{lessonId:'legacy-'+version}});await wait(()=>e.lesson&&e.lesson.lessonId==='legacy-'+version&&!e.loading);e.validationResult={valid:true};e.validationProofVersion=e.proofVersion;e.previewManifest={checksum:'proof',manifest:{manifestVersion:'teebot-lesson-renderer.'+version,steps:[]}};e.previewProofVersion=e.proofVersion;e.simulationEvidence={};e.simulationProofVersion=e.proofVersion;e.validSimulationEvidence=()=>true;await tick();const buttons=proofButtons();routeReset.push([version,Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]')),e.tvideoJourneyPreset,e.tvideoJourneyLoading,e.tvideoJourneySaving,e.tvideoJourneyError,e.tvideoJourneyResponse.state,e.tvideoJourneyDraft,e.tvideoJourneyDirty,e.tvideoJourneyAssets.length,e.tvideoJourneySaveMessage,e.proofActionsDisabled,...buttons.map(button=>button.disabled),e.canPublishCurrentProof()])}
    const journeyLoadsBefore=t.calls.journeyLoads.length;await e.$router.push({path:'/',query:{lessonId:'journey-v4'}});await wait(()=>e.lesson&&e.lesson.lessonId==='journey-v4'&&!e.loading);for(let index=0;index<10;index+=1)await tick();const routeReload=[t.calls.journeyLoads.length-journeyLoadsBefore,t.calls.journeyLoads.at(-1),e.tvideoJourneyDirty,Boolean(e.tvideoJourneyPreset),Boolean(e.tvideoJourneyDraft),Boolean(document.querySelector('[data-testid="tvideo-journey-editor"]'))];
    return{legacy,localized,unconfigured,hardcodedVisible,tabStates,rawTabKeys,pinnedAssets,keyboard,picker,path,branch,robotState,robotJourneyStates,cueBinding,saveSuccess,saveError,dirtyProof,savingProof,restoredProof,gate:[gateFalse,gateTrue],routeReset,routeReload,runtimeText:document.body.innerText.slice(0,200)};
  })()`);

  assert.deepEqual(result.legacy, { 'teebot-lesson-renderer.v1': false, 'teebot-lesson-renderer.v2': false, 'teebot-lesson-renderer.v3': false });
  assert.deepEqual(result.localized.en.pathLabels, ['Horizontal position', 'Vertical position', 'Safe width', 'Safe height']);
  assert.ok(result.localized.en.contentLabels.includes('Step key'));
  assert.deepEqual([result.localized.en.segmentsLabel, result.localized.en.phonemesLabel], ['Approved segments', 'Approved phonemes']);
  assert.deepEqual(result.localized.en.branchLabels.map((row) => row[1]), ['Target word', 'Vietnamese meaning', 'Related concept', 'Silence', 'Uncertain contribution', 'Retry coaching · level 1', 'Retry coaching · level 2', 'Retry coaching · level 3']);
  assert.deepEqual(result.localized.vi.pathLabels, ['Vị trí ngang', 'Vị trí dọc', 'Chiều rộng an toàn', 'Chiều cao an toàn']);
  assert.ok(result.localized.vi.contentLabels.includes('Khóa bước'));
  assert.deepEqual([result.localized.vi.segmentsLabel, result.localized.vi.phonemesLabel], ['Âm đoạn đã duyệt', 'Âm vị đã duyệt']);
  assert.deepEqual(result.localized.vi.branchLabels.map((row) => row[1]), ['Từ mục tiêu', 'Nghĩa tiếng Việt', 'Khái niệm liên quan', 'Im lặng', 'Đóng góp chưa chắc chắn', 'Hướng dẫn thử lại · mức 1', 'Hướng dẫn thử lại · mức 2', 'Hướng dẫn thử lại · mức 3']);
  assert.deepEqual(result.unconfigured, [2, ['barn', 'hay'], 2]);
  assert.deepEqual(result.hardcodedVisible, [], 'new visible copy must route through i18n');
  assert.deepEqual(result.tabStates.map((row) => row[0]), ['3 nguồn', 'Đường đi hành trình', 'Hội thoại', 'Robot đã hợp nhất']);
  assert.deepEqual(result.rawTabKeys, []);
  assert.ok(result.tabStates.every((row) => row[1] === 'true'));
  assert.deepEqual(result.pinnedAssets, [
    { versionId: '10000000-0000-4000-8000-000000000001', url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', sha256: '53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1', mimeType: 'video/mp4' },
    { versionId: '30000000-0000-4000-8000-000000000001', url: '/tvideo-demo/assets/objects/barn.png', sha256: 'eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9', mimeType: 'image/png' },
    { versionId: '30000000-0000-4000-8000-000000000002', url: '/tvideo-demo/assets/objects/hay.png', sha256: 'f74c34f44459495062091d4d91bb8fef0a2501ff3fab78beddaf0f70f0bf2e11', mimeType: 'image/png' },
    { versionId: '20000000-0000-4000-8000-000000000001', url: '/tvideo-demo/assets/robot-alive/flight/flight-in.webm', sha256: '52091cdbfe5712e4afed800ce35e4a743e923c65b4034dd4c0a3c85d0f6c345c', mimeType: 'video/webm' },
    { versionId: '20000000-0000-4000-8000-000000000002', url: '/tvideo-demo/assets/robot-alive/flight/walk-toward.webm', sha256: '46a3058664949eaebf057f99309ee317bd36257178c187e83329fdbaf030cf5a', mimeType: 'video/webm' },
    { versionId: '20000000-0000-4000-8000-000000000003', url: '/tvideo-demo/assets/robot-alive/flight/greet-loop.webm', sha256: '249a86cea2456b41ee5344445b0b83777a0ee7217ce435e7e9294ed7f39b12e0', mimeType: 'video/webm' },
    { versionId: '20000000-0000-4000-8000-000000000004', url: '/tvideo-demo/assets/robot-alive/flight/celebrate.webm', sha256: '708d982dc9f2257a170ad1811c1afc230b5249a4139f975133a703ebdf39e105', mimeType: 'video/webm' },
  ]);
  assert.ok(result.pinnedAssets.every((asset) => !asset.url.startsWith('data:image')), 'pinned preview assets must use committed static URLs');
  assert.deepEqual(result.keyboard, ['path', 'tvideo-tab-path', 'true']);
  assert.deepEqual(result.picker, ['10000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', true]);
  assert.deepEqual(result.path.slice(0, 8), [1, 0, 1, 0, true, true, true, 'cover']);
  assert.ok(result.path[8] >= 2 && result.path[9] === false, `path MP4 did not load: ${JSON.stringify(result.path)}`);
  assert.deepEqual(result.branch, ['retry_level_3', 3, 'barn-retry-level-3', 'stay', true]);
  assert.ok(result.robotState[0] >= 100 && result.robotState[0] % 100 === 0);
  assert.deepEqual(result.robotState.slice(1), [0, result.robotState[0], 0, false]);
  assert.deepEqual(result.robotJourneyStates.flight, { effect: 'opening', phase: 'opening-flight', role: 'flight', shadow: false, puff: false, squashY: 1, dots: [true, false], objectVisible: false, cardVisible: false, cardVariant: 'opening', listeningGlow: false, thinking: false, correctChip: false, confetti: false, retryLevel: 0, pulse: 'none', pulsePx: 0, pulseCount: 0, wordTransition: null });
  assert.deepEqual(result.robotJourneyStates.landing, { effect: 'opening', phase: 'opening-landing', role: 'flight', shadow: true, puff: true, squashY: 0.94, dots: [true, false], objectVisible: false, cardVisible: false, cardVariant: 'opening', listeningGlow: false, thinking: false, correctChip: false, confetti: false, retryLevel: 0, pulse: 'none', pulsePx: 0, pulseCount: 0, wordTransition: null });
  assert.deepEqual(result.robotJourneyStates.walk, { effect: 'opening', phase: 'opening-walk', role: 'walking', shadow: true, puff: false, squashY: 1, dots: [true, false], objectVisible: false, cardVisible: false, cardVariant: 'greet', listeningGlow: false, thinking: false, correctChip: false, confetti: false, retryLevel: 0, pulse: 'none', pulsePx: 0, pulseCount: 0, wordTransition: null });
  assert.deepEqual([result.robotJourneyStates.arrived.phase,result.robotJourneyStates.arrived.role,result.robotJourneyStates.arrived.objectVisible,result.robotJourneyStates.arrived.cardVisible], ['opening-arrived','greeting-teaching',true,true]);
  assert.deepEqual([result.robotJourneyStates.listen.listeningGlow,result.robotJourneyStates.listen.pulse,result.robotJourneyStates.thinking.thinking,result.robotJourneyStates.correct.correctChip,result.robotJourneyStates.correct.confetti], [true,'gentle',true,true,true]);
  assert.deepEqual([result.robotJourneyStates.retry1.retryLevel,result.robotJourneyStates.retry1.pulsePx,result.robotJourneyStates.retry1.pulseCount,result.robotJourneyStates.retry2.retryLevel,result.robotJourneyStates.retry2.pulsePx,result.robotJourneyStates.retry2.pulseCount,result.robotJourneyStates.retry3.retryLevel,result.robotJourneyStates.retry3.pulsePx,result.robotJourneyStates.retry3.pulseCount], [1,2,1,2,4,2,3,6,3]);
  assert.deepEqual([result.robotJourneyStates.transition.role,result.robotJourneyStates.transition.wordTransition], ['greeting-teaching', { outgoing: 'barn', incoming: 'hay', progress: 0.6, displayWord: 'hay', displayStepKey: 'hay' }]);
  assert.deepEqual(result.cueBinding, ['hay', '/tvideo-demo/assets/objects/hay.png', 'Hay is dried grass that farmers store and feed to animals.']);
  assert.deepEqual(result.saveSuccess, [true, false, true, 1, false, 'configured']);
  assert.deepEqual(result.saveError, [false, true, true]);
  assert.deepEqual(result.dirtyProof, [true, true, true, true, [false, false], 0, 0, false]);
  assert.deepEqual(result.savingProof, [true, true, true, true, [false, false], 0, 0, false]);
  assert.deepEqual(result.restoredProof, [false, false, false, false, true]);
  assert.deepEqual(result.gate, [false, true]);
  assert.deepEqual(result.routeReset, [
    ['v1', false, null, false, false, '', 'not-configured', null, false, 0, '', false, false, false, false, true],
    ['v2', false, null, false, false, '', 'not-configured', null, false, 0, '', false, false, false, false, true],
    ['v3', false, null, false, false, '', 'not-configured', null, false, 0, '', false, false, false, false, true],
  ]);
  assert.deepEqual(result.routeReload, [1, 'journey-v4', false, true, true, true]);
  assert.deepEqual(runtimeErrors, [], `mounted journey runtime errors: ${runtimeErrors.join('; ')}`);
  assert.deepEqual(vueConsoleErrors, [], `mounted journey Vue console errors: ${vueConsoleErrors.join('; ')}`);
  console.log('mounted TVideo Journey tabs, media, editing, simulation, clock, save, gate, accessibility, and legacy isolation PASS (11 groups)');
} finally {
  if (socket) socket.close(); await stopChild(chrome); if (server) await new Promise((resolve) => server.close(resolve)); if (temp) await rm(temp, { recursive: true, force: true });
}
