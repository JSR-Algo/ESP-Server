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
  const auditLayoutAt = async (width) => {
    await cdp('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await evaluate('new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    return evaluate(`(()=>{const overflowX=(selector)=>{const element=document.querySelector(selector);return element?getComputedStyle(element).overflowX:null};const root=document.documentElement;return{width:${width},clientWidth:root.clientWidth,scrollWidth:root.scrollWidth,assetPickerOverflowX:overflowX('.asset-picker__grid'),advancedStepsOverflowX:overflowX('.advanced-steps-scroll'),stepNavOverflowX:overflowX('.step-nav')}})()`);
  };
  const assertNoPageOverflow = (audit) => assert.ok(audit.scrollWidth <= audit.clientWidth + 1, `viewport ${audit.width}px should not overflow horizontally: ${JSON.stringify(audit)}`);
  const assertResponsiveOverflowControls = (audit, options = {}) => {
    if (options.assetPickerRequired !== false) assert.equal(audit.assetPickerOverflowX, 'auto', `asset picker should be horizontally scrollable at ${audit.width}px: ${JSON.stringify(audit)}`);
    assert.equal(audit.advancedStepsOverflowX, 'auto', `advanced steps table scroller should be horizontally scrollable at ${audit.width}px: ${JSON.stringify(audit)}`);
    if (audit.width <= 760) assert.equal(audit.stepNavOverflowX, 'auto', `step nav should be horizontally scrollable at ${audit.width}px: ${JSON.stringify(audit)}`);
  };
  await cdp('Page.enable'); await cdp('Runtime.enable'); await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  const editorReady = 'Boolean(window.__LESSON_BUILDER_READY__) && document.querySelectorAll(".step-nav__item").length >= 2 && [...document.querySelectorAll(".right-operations button")].some((button)=>button.textContent.includes("Preview"))';
  for (let i = 0; i < 100 && !(await evaluate(editorReady)); i += 1) await new Promise((r) => setTimeout(r, 50));
  const editorDiagnostics = await evaluate('({ signaled: Boolean(window.__LESSON_BUILDER_READY__), lessonId: window.__LESSON_BUILDER_TEST__?.editor?.lessonId, lesson: window.__LESSON_BUILDER_TEST__?.editor?.lesson, editorSteps: window.__LESSON_BUILDER_TEST__?.editor?.steps?.length, route: window.__LESSON_BUILDER_TEST__?.editor?.$route?.fullPath, navItems: document.querySelectorAll(".step-nav__item").length, previewButtonVisible: [...document.querySelectorAll(".right-operations button")].some((button)=>button.textContent.includes("Preview")), bodyText: document.body.innerText.slice(0, 500) })');
  assert.equal(await evaluate(editorReady), true, `mounted LessonEditor did not become ready: ${JSON.stringify(editorDiagnostics)}; ${runtimeErrors.join('; ')}`);
  const initialLayoutAudits = [];
  for (const width of [1440, 1024, 768, 390]) {
    initialLayoutAudits.push(await auditLayoutAt(width));
  }
  await auditLayoutAt(1440);
  for (const audit of initialLayoutAudits) {
    assertNoPageOverflow(audit);
    assertResponsiveOverflowControls(audit);
  }
  const result = await evaluate(`(async()=>{
    const t=window.__LESSON_BUILDER_TEST__,e=t.editor, tick=()=>new Promise(r=>setTimeout(r,0)),waitFor=async(test)=>{for(let i=0;i<50&&!test();i+=1)await tick();if(!test())throw new Error('browser fixture condition timed out')};
    const setInput=(input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))};
    const testInput=(id)=>{const root=document.querySelector('[data-testid="'+id+'"]')||document.getElementById(id);return root&&root.matches('input,textarea')?root:root?.querySelector('input,textarea')};
    const formItem=(label)=>[...document.querySelectorAll('.interaction-panel .el-form-item')].find(x=>x.querySelector('.el-form-item__label')?.textContent.trim()===label);
    const choose=async(label,text)=>{const item=formItem(label);if(!item)throw new Error('missing form item '+label);item.querySelector('.el-select input').click();await tick();const options=[...document.querySelectorAll('body .el-select-dropdown__item')].filter(x=>x.textContent.trim()===text);const option=options.at(-1);if(!option)throw new Error('missing option '+label+':'+text);option.click();await tick()};
    document.querySelectorAll('.step-nav__item')[1].click();await tick();
    const selectedVersionsBefore=Object.fromEntries(['backgroundScene','teachingObject','robotOverlay'].map(slot=>[slot,e.selectedVisualVersionId(slot)]));
    const backgroundVideos=[...document.querySelectorAll('[data-slot="backgroundScene"] video')];
    const teachingImages=[...document.querySelectorAll('[data-slot="teachingObject"] img')];
    const committedPickerMedia={
      background:backgroundVideos.length===2&&backgroundVideos.every(video=>video.muted&&video.hasAttribute('playsinline')&&video.getAttribute('preload')==='metadata'&&String(video.getAttribute('src')||'').endsWith('/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4')),
      teaching:teachingImages.length===2&&teachingImages.every(image=>String(image.getAttribute('src')||'').endsWith('/tvideo-demo/assets/objects/barn.png')),
      robot:e.cinematicLibraries.robotOverlay.length===2&&e.cinematicLibraries.robotOverlay.every(asset=>asset.mimeType==='video/webm'&&String(asset.url||'').endsWith('/tvideo-demo/assets/robot-alive/flight/greet-loop.webm')),
    };
    e.$set(e.cinematicLibraryLoading,'backgroundScene',true);await tick();const loadingVisible=Boolean(document.querySelector('[data-slot="backgroundScene"] .asset-picker__loading'));e.$set(e.cinematicLibraryLoading,'backgroundScene',false);await tick();
    e.$set(e.cinematicLibraryErrors,'robotOverlay','robot library unavailable');await tick();const errorVisible=document.querySelector('[data-slot="robotOverlay"] .asset-picker__error')?.textContent.includes('robot library unavailable');e.$set(e.cinematicLibraryErrors,'robotOverlay','');await tick();
    const teachingAssets=e.rawCinematicLibraries.teachingObject;e.$set(e.rawCinematicLibraries,'teachingObject',[]);await tick();const emptyVisible=Boolean(document.querySelector('[data-slot="teachingObject"] .asset-picker__empty'));e.$set(e.rawCinematicLibraries,'teachingObject',teachingAssets);await tick();
    for(const slot of ['backgroundScene','teachingObject','robotOverlay']){[...document.querySelectorAll('[data-slot="'+slot+'"] .asset-tile__select')].at(-1).click();await waitFor(()=>!e.savingLessonVisuals&&!e.cinematicRefSaving);await tick()}
    const lessonVisualSets=t.calls.lessonVisualSets.slice(-2),robotRefSets=t.calls.visualRefSets.slice(-1),hydratedVersions=Object.fromEntries(['backgroundScene','teachingObject','robotOverlay'].map(slot=>[slot,e.selectedVisualVersionId(slot)])),selectedVersionTiles=Object.fromEntries(['backgroundScene','teachingObject','robotOverlay'].map(slot=>[slot,Boolean(document.querySelector('[data-slot="'+slot+'"] .asset-tile.selected'))]));
    e.previewManifest={marker:'immutable-preview'};const previewBeforePublished=e.previewManifest,refsBeforePublished=JSON.stringify(e.selectedStep.visualRefs),setCountBeforePublished=t.calls.visualRefSets.length,lessonVisualSetCountBeforePublished=t.calls.lessonVisualSets.length;e.lesson.status='published';await tick();e.selectCinematicLayer({slot:'backgroundScene',assetVersionId:'scene-v3',asset:t.sharedAssets[0]});e.selectCinematicLayer({slot:'teachingObject',assetVersionId:'teach-v2',asset:t.sharedAssets[2]});e.selectCinematicLayer({slot:'robotOverlay',assetVersionId:'robot-v4',asset:t.sharedAssets[4]});await tick();
    const publishedMessage=document.querySelector('[data-testid="immutable-version-message"]')?.textContent||'';
    const publishedSelectorsDisabled=[...document.querySelectorAll('[data-testid="lesson-background-selector"] .asset-tile__select,[data-testid="lesson-object-selector"] .asset-tile__select')].every(button=>button.disabled);
    const publishedImmutable=publishedMessage.includes('read-only')&&publishedMessage.includes('editable version')&&publishedSelectorsDisabled&&t.calls.lessonVisualSets.length===lessonVisualSetCountBeforePublished&&t.calls.visualRefSets.length===setCountBeforePublished&&e.previewManifest===previewBeforePublished&&JSON.stringify(e.selectedStep.visualRefs)===refsBeforePublished;
    e.lesson.status='draft';e.previewManifest=null;await tick();
    e.doValidate(null,null,{allowUnsafe:true});await tick();
    const readiness=e.$children.find(c=>c.$options.name==='LessonPublishReadiness');
    const readyBeforeEdit=!readiness.metrics.estimateOnly&&readiness.metrics.offlineReady;
    setInput(testInput('lesson-step-prompt'),'Say the red barn');await tick();
    setInput(testInput('lesson-step-subject'),'red barn');await tick();
    setInput(testInput('lesson-step-helper'),'Listen, then speak');await tick();
    setInput(testInput('lesson-step-l1-hint'),'Chỉ dùng khi trẻ cần giúp');await tick();
    e.selectSharedAsset(t.sharedAssets.find(asset=>asset.versionId==='teach-v2'));await tick();
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
    await waitFor(()=>!e.proofActionsDisabled);
    [...document.querySelectorAll('.right-operations button')].find(x=>x.textContent.includes('Preview')).click();await tick();
    const preview=e.$children.find(c=>c.$options.name==='RobotLessonPreview');
    const cinematicVisible=Boolean(document.querySelector('[data-testid="cinematic-design-reference"] iframe'));
    const exactVisible=Boolean(document.querySelector('[data-testid="exact-robot-renderer"] [data-testid="esp-tft-stage"]'));
    const previewText=document.querySelector('[data-testid="exact-robot-renderer"]')?.textContent||'';
    const rendererContractVisible=previewText.includes('teebot-lesson-renderer.v2')&&previewText.includes('Renderer v2')&&previewText.includes('oncePerLessonSession')&&previewText.includes('server');
    const capabilityVisible=previewText.includes('Renderer v2 supported');
    const truthfulExactLabel=previewText.includes('Browser transitions only illustrate timing');
    const openingGeometryVisible=previewText.includes('walkTowardMidpoint')&&previewText.includes('234,150 · 104×70')&&previewText.includes('content visible');
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
    return{selected:e.selectedStepIndex,filters:t.calls.visualFilters,patch:t.calls.update[0],failedPatch:t.calls.update[1],updateCount:t.calls.update.length,metrics:t.validation.budgets.espTft.metrics,preview:[preview.stepIndex,preview.manifest.manifestVersion,preview.manifest.profile,preview.initialPath,e.previewPath.path,preview.rendererMetadata.features.lessonRendererV2.physicalMotionOwner],cinematicVisible,exactVisible,rendererContractVisible,capabilityVisible,truthfulExactLabel,openingGeometryVisible,stateControls,stateMotionClasses,degradedControls,readyBeforeEdit,staleAfterEdit,staleAfterFailure,selectedAfterReload,selectedTilePersisted,errors:t.calls.errors,previewClearedOnEdit,deferredValidationIgnored,newerDraftDirty,newerDraftWord,validateBeforeSaveIgnored,validationIssuesRendered,selectedVersionsBefore,committedPickerMedia,loadingVisible,errorVisible,emptyVisible,lessonVisualSets,robotRefSets,hydratedVersions,selectedVersionTiles,publishedImmutable,warnings:t.calls.warnings}
  })()`);
  assert.equal(result.selected, 1); assert.deepEqual(result.filters, [{ category: 'scene', profile: 'espTft' }, { category: 'teachingObject', profile: 'espTft' }, { category: 'robotPose', profile: 'espTft' }]);
  assert.deepEqual(result.selectedVersionsBefore, { backgroundScene: 'scene-v3', teachingObject: 'teach-v2', robotOverlay: 'robot-v4' });
  assert.deepEqual(result.committedPickerMedia, { background: true, teaching: true, robot: true }); assert.equal(result.loadingVisible, true); assert.equal(result.errorVisible, true); assert.equal(result.emptyVisible, true);
  assert.deepEqual(result.lessonVisualSets, [
    { lessonId: 'lesson-1', payload: { backgroundAssetVersionId: 'scene-v4', objectAssetVersionId: 'teach-v2' } },
    { lessonId: 'lesson-1', payload: { backgroundAssetVersionId: 'scene-v4', objectAssetVersionId: 'teach-v3' } },
  ]);
  assert.deepEqual(result.robotRefSets, [{ lessonId: 'lesson-1', stepKey: 's2', slot: 'robotOverlay', assetVersionId: 'robot-v5' }]);
  assert.deepEqual(result.hydratedVersions, { backgroundScene: 'scene-v4', teachingObject: 'teach-v3', robotOverlay: 'robot-v5' });
  assert.deepEqual(result.selectedVersionTiles, { backgroundScene: true, teachingObject: true, robotOverlay: true });
  assert.equal(result.publishedImmutable, true); assert.equal(result.warnings.length, 1);
  assert.deepEqual(result.patch, { lessonId: 'lesson-1', stepKey: 's2', payload: { stepKey: 's2', stepType: 'repeat', prompt: 'Say the red barn', subject: 'red barn', helperText: 'Listen, then speak', l1TransferHint: 'Chỉ dùng khi trẻ cần giúp', stepBody: { durationSec: 12, durationPreset: 8, teachingWord: { text: 'BARN', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' }, interaction: { template: 'safeSpeaking', maxAttempts: 3, listenTimeoutSec: 6, correctThreshold: 0.85, braveTryThreshold: 0.7, funPattern: 'miniStoryRescue' }, motion: { present: 'presentLeft', listen: 'listen', correct: 'celebrate', nearMiss: 'encourage', incorrect: 'tryAgain' }, storyBeat: { goal: 'Help Pip find a home', successReaction: 'pet.entersBarn', nextTease: 'What comes next?' }, teachingObject: { primaryWord: 'BARN', asset: { key: 'object.barn', src: '/tvideo-demo/assets/objects/barn.png', sha256: 'abc123', version: 2, bytes: 60000 } } } } });
  assert.equal(result.updateCount, 4); assert.equal('visualRefs' in result.failedPatch.payload, false); assert.equal(result.failedPatch.payload.stepBody.teachingWord.text, 'BARNS');
  assert.equal(result.metrics.packBytes, 222000); assert.equal(result.metrics.uniqueAssetCount, 7); assert.equal(result.metrics.sharedAssetCount, 2); assert.equal(result.metrics.estimatedVisualPeakBytes, 640000); assert.equal(result.metrics.offlineReady, true); assert.equal(result.metrics.allPathsTerminate, true); assert.deepEqual(result.preview, [1, 'teebot-lesson-renderer.v2', 'espTft', 'correct', 'nearMiss', 'server']);
  assert.equal(result.cinematicVisible, true); assert.equal(result.exactVisible, true); assert.equal(result.rendererContractVisible, true); assert.equal(result.capabilityVisible, true); assert.equal(result.truthfulExactLabel, true);
  assert.equal(result.openingGeometryVisible, true);
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

  const layoutAudits = [];
  for (const width of [1440, 1024, 768, 390]) {
    layoutAudits.push(await auditLayoutAt(width));
  }
  await auditLayoutAt(1440);
  for (const audit of layoutAudits) {
    assertNoPageOverflow(audit);
    assertResponsiveOverflowControls(audit);
  }
  await evaluate(`(async()=>{const t=window.__LESSON_BUILDER_TEST__,e=t.editor,tick=()=>new Promise(r=>setTimeout(r,0));t.layoutStress={status:e.lesson.status,backgroundLoading:e.cinematicLibraryLoading.backgroundScene,teachingObject:e.rawCinematicLibraries.teachingObject,robotOverlayError:e.cinematicLibraryErrors.robotOverlay};e.lesson.status='published';e.$set(e.cinematicLibraryLoading,'backgroundScene',true);e.$set(e.rawCinematicLibraries,'teachingObject',[]);e.$set(e.cinematicLibraryErrors,'robotOverlay','robot library unavailable');await tick()})()`);
  const stressedAudit = await auditLayoutAt(390);
  await auditLayoutAt(1440);
  await evaluate(`(async()=>{const t=window.__LESSON_BUILDER_TEST__,e=t.editor,tick=()=>new Promise(r=>setTimeout(r,0)),state=t.layoutStress;e.lesson.status=state.status;e.$set(e.cinematicLibraryLoading,'backgroundScene',state.backgroundLoading);e.$set(e.rawCinematicLibraries,'teachingObject',state.teachingObject);e.$set(e.cinematicLibraryErrors,'robotOverlay',state.robotOverlayError);delete t.layoutStress;await tick()})()`);
  assertNoPageOverflow(stressedAudit);
  assertResponsiveOverflowControls(stressedAudit, { assetPickerRequired: false });

  const courseModeResult = await evaluate('window.__MOUNT_COURSE_MODE_EDITOR__()');
  assert.equal(courseModeResult.timeline, true);
  assert.match(courseModeResult.duration, /7:00 \/ 8:00/);
  assert.equal(courseModeResult.readOnly, true);
  assert.equal(courseModeResult.addStep, false);
  assert.equal(courseModeResult.actionButtons, 0);
  assert.equal(courseModeResult.leakageWarning, true);
  assert.equal(courseModeResult.saveCount, 1);
  assert.match(courseModeResult.savedChecksum, /^[a-f0-9]{64}$/);
  const courseModeMobileAudit = await auditLayoutAt(390);
  assertNoPageOverflow(courseModeMobileAudit);
  assertResponsiveOverflowControls(courseModeMobileAudit);

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
