const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { nestFetch } = require('./helpers/admin-api');

const nestApi = (page, path, options) => nestFetch(page, path, options);

test('real admin previews the exact espTft scene and all response paths', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  await loginAsLessonAuthor(page);

  const sourceLessonId = '00000006-0002-0000-0000-000000000001';
  const sourceCourseId = '00000006-0001-0000-0000-000000000001';
  const existing = await nestApi(page, `/courses/${sourceCourseId}/lessons`);
  for (const row of existing.filter((item) => item.lesson_key === 'w01-d01-barn-say-it' && item.status === 'draft')) {
    await nestApi(page, `/lessons/${row.id}`, { method: 'DELETE' });
  }
  const lesson = await nestApi(page, `/lessons/${sourceLessonId}/new-version`, { method: 'POST' });
  await nestApi(page, `/lessons/${lesson.id}`, { method: 'PATCH', body: { title: `Preview lesson ${runId}` } });
  const [step] = await nestApi(page, `/lessons/${lesson.id}/steps`);

  const visuals = [
    { slot: 'backgroundScene', category: 'scene', assetKey: `e2e.${runId}.background`, sha256: 'a'.repeat(64) },
    { slot: 'teachingObject', category: 'teachingObject', assetKey: `e2e.${runId}.object`, sha256: 'b'.repeat(64) },
    { slot: 'robotOverlay', category: 'robotPose', assetKey: `e2e.${runId}.robot`, sha256: 'c'.repeat(64) },
  ];
  const versionIdBySlot = {};
  for (const visual of visuals) {
    const version = await nestApi(page, `/lesson-visual-assets/${visual.assetKey}/versions`, {
      method: 'POST',
      body: {
        category: visual.category,
        title: `E2E ${visual.slot} ${runId}`,
        profile: 'espTft',
        storagePath: 'http://127.0.0.1:8102/favicon.ico',
        sha256: visual.sha256,
        mimeType: 'image/png',
        bytes: 5430,
        width: 64,
        height: 64,
        publicationState: 'published',
      },
    });
    versionIdBySlot[visual.slot] = version.id;
  }

  // Only robotOverlay is a per-step slot (PER_STEP_VISUAL_SLOTS); background and
  // teaching object are lesson-wide and must go through the visuals command.
  await nestApi(page, `/lessons/${lesson.id}/visuals`, {
    method: 'PUT',
    body: {
      backgroundAssetVersionId: versionIdBySlot.backgroundScene,
      objectAssetVersionId: versionIdBySlot.teachingObject,
    },
  });
  await nestApi(page, `/lessons/${lesson.id}/steps/${encodeURIComponent(step.step_key)}/visual-refs/robotOverlay`, {
    method: 'PUT', body: { assetVersionId: versionIdBySlot.robotOverlay },
  });

  await nestApi(page, `/lessons/${lesson.id}/steps/${encodeURIComponent(step.step_key)}`, {
    method: 'PATCH',
    body: {
      prompt: step.prompt,
      subject: step.subject,
      stepBody: {
        durationPreset: 3,
        teachingWord: { text: 'MOON', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' },
        interaction: { template: 'safeSpeaking', maxAttempts: 3, listenTimeoutSec: 6, correctThreshold: 0.85, braveTryThreshold: 0.7, funPattern: 'copyMyMove' },
        motion: { present: 'teach', listen: 'listen', correct: 'goodbye', nearMiss: 'thinking', incorrect: 'presentRight' },
        storyBeat: { goal: 'Help TeeBot find the moon.', successReaction: 'Celebrate the moon.', nextTease: 'What shines next?' },
      },
    },
  });

  // Register before navigating: the editor auto-generates the espTft preview as
  // soon as steps load, so the 200 can land before any click.
  const previewResponse = page.waitForResponse((response) =>
    response.url().includes(`/lessons/${lesson.id}/manifest-preview`) && response.status() === 200);
  await page.goto(`/login#/lesson-editor?lessonId=${lesson.id}`);
  await expect(page.getByRole('heading', { name: `Preview lesson ${runId}` })).toBeVisible();
  // "Generate preview" lives in the empty state (v-else on previewManifest), so
  // it is absent once the auto-preview has succeeded. Click it only if shown.
  const generatePreview = page.getByRole('button', { name: 'Generate preview' });
  if (await generatePreview.isVisible().catch(() => false)) await generatePreview.click();
  await previewResponse;

  const stage = page.getByTestId('esp-tft-stage');
  await expect(stage).toBeVisible();
  expect(await stage.evaluate((element) => ({ width: element.clientWidth, height: element.clientHeight }))).toEqual({ width: 480, height: 320 });
  await expect(stage.locator('.word-pill')).toHaveText('MOON');
  await expect(stage.locator('img.layer-background')).toBeVisible();
  await expect(stage.locator('img.layer-teachingObject')).toBeVisible();
  await expect(stage.locator('img.layer-robotOverlay')).toBeVisible();

  const expectations = [
    ['Correct', 'Slave command: goodbye'],
    ['Near miss', 'Slave command: thinking'],
    ['Incorrect', 'Slave command: presentRight'],
    ['Retry', 'Slave command: presentRight'],
    ['Timeout', 'Slave command: listen'],
    ['Brave try', 'Slave command: thinking'],
    ['Completion', 'Slave command: goodbye'],
    // silence resolves through PATH_STATE to the 'listen' visual state, and this
    // step authors motion.listen, so the authored value wins over DEFAULT_PATHS'
    // patient-wait. sttUnavailable resolves to 'teach', which the authored map
    // does not key (it authors `present`), so its calm-idle default stands.
    ['Silence', 'Slave command: listen'],
    ['STT unavailable', 'Slave command: calm-idle'],
    ['Missing visual', 'Slave command: teach'],
  ];
  // Scope to the response-path toolbar: labels like "Correct" also name
  // LessonSimulationPanel preset buttons, so an unscoped lookup is ambiguous.
  const responsePaths = page.getByLabel('Response paths');
  for (const [label, command] of expectations) {
    await responsePaths.getByRole('button', { name: label, exact: true }).click();
    await expect(page.getByRole('list', { name: 'Robot command timeline' })).toContainText(command);
    await expect(responsePaths.getByRole('button', { name: label, exact: true }))
      .toHaveAttribute('aria-pressed', 'true');
  }
  await expect(stage.locator('.missing-visual')).toContainText('MOON');
  await expect(stage.locator('img.layer-teachingObject')).toHaveCount(0);
  assertNoUnexpectedPageErrors();
});


test('@s08 saved v5 candidate exposes real media, phases and failures', async ({ page, browserName }, testInfo) => {
  const fs = require('node:fs');
  const { openS07Session, s07Api } = require('./helpers/s07-session');
  const config = await openS07Session(page);
  const original = await s07Api(page, 'GET', `/lessons/${config.source}/course-mode`);
  const visuals = await s07Api(page, 'GET', `/lessons/${config.source}/visuals`);
  const key = `cpr-s08-${Date.now()}-${browserName}`;
  const course = await s07Api(page, 'POST', '/courses', { courseKey:key,title:key,locale:'en-US',ageBand:'4-6' });
  const lesson = await s07Api(page, 'POST', `/courses/${course.id}/lessons`, { lessonKey:key,title:key,locale:'en-US',ageBand:'4-6',rendererVersion:'teebot-lesson-renderer.v5',estimatedDurationSec:480,durationPreset:8 });
  const initial = await s07Api(page, 'GET', `/lessons/${lesson.id}/visuals`);
  await s07Api(page, 'PUT', `/lessons/${lesson.id}/course-mode`, { contract:original.contract,expectedChecksum:initial.checksum,expectedVisualChecksum:initial.visualChecksum });
  const sourceAssets = await s07Api(page,'GET',`/lessons/${config.source}/assets?profile=espTft`);
  const image = sourceAssets.assets.find(a => (a.layer||a.slot)==='backgroundScene');
  await s07Api(page,'POST',`/lessons/${lesson.id}/assets`,{profile:'espTft',sourceAssetId:image.assetId});
  const phases = ['flyIn','walk','teach','listen','thinking','celebrate','exit'];
  const ids = {background:visuals.refs.find(r=>r.slot==='backgroundScene').assetVersionId,object:visuals.refs.find(r=>r.slot==='teachingObject').assetVersionId,...Object.fromEntries(phases.map(phase => [phase,visuals.refs.find(r=>r.slot===`robotOverlay.${phase}`).assetVersionId]))};
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  // Forward unchanged requests through the task-owned raw-container tunnel.
  // This preserves source bytes; image HTML and decoder failures remain failures.
  await page.route('http://127.0.0.1:8103/**', async route => {
    const response = await route.fetch({url:route.request().url().replace(':8103',':18103'),timeout:10000});
    await route.fulfill({response,headers:{...response.headers(),'access-control-allow-origin':'*'}});
  });
  await page.goto(`/#/lesson-editor?lessonId=${lesson.id}`);
  const panel = page.getByTestId('course-mode-visual-selection'); await expect(panel).toBeVisible();
  for (const [phase,id] of Object.entries(ids)) await panel.getByTestId(`course-visual-${phase}`).locator('select').selectOption(id);
  const saved = page.waitForResponse(r=>r.url().endsWith(`/lessons/${lesson.id}/visuals`) && r.request().method()==='PUT');
  await panel.getByRole('button',{name:'Save visual bindings',exact:true}).click(); expect((await saved).status()).toBe(200);
  await expect(panel).toContainText('Visual versions saved and read back.');
  const persisted = await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  const response = await s07Api(page,'GET',`/lessons/${lesson.id}/manifest-preview?profile=espTft`);
  expect(response.manifest.cinematicPhases).toEqual(persisted.cinematicPhases);
  await page.reload();
  const stage = page.getByTestId('esp-tft-stage'); await expect(stage).toBeVisible();
  await expect(page.getByTestId('preview-persistence-status')).toContainText('Saved draft preview');
  expect(await stage.evaluate(el=>[el.clientWidth,el.clientHeight])).toEqual([480,320]);
  const preview = page.locator('.robot-preview').first();
  await expect(preview).toContainText('image failed to load/decode');
  const evidence = {lessonId:lesson.id,checksum:response.checksum,manifest:response.manifest,persisted,transport:'8103 -> task-owned raw-container tunnel18103; unchanged bytes, explicit transport CORS',browserName,captures:[]};
  const robotLayer = stage.locator('.layer-robotOverlay');
  const mediaState = () => robotLayer.evaluate(el => el.__vue__.mediaPlaybackState());
    for (const phase of response.manifest.cinematicPhases.filter(p=>p.activityIds.includes(response.manifest.steps[0].activityId||response.manifest.steps[0].id))) {
      await preview.getByRole('button',{name:phase.phaseId,exact:true}).click();
      const robot = phase.layers.find(l=>l.slot==='robotOverlay');
      const asset = response.manifest.assets.find(a=>a.assetKey===robot.assetKey && a.version===robot.version && a.sha256===robot.sha256);
      await expect(robotLayer).toHaveAttribute('data-source-url',asset.url);
      await expect.poll(async()=> (await mediaState()).ready).toBe(true);
      expect(await stage.locator('.layer-robotOverlay').evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
      for(const [label,frame] of [['first',0],['middle',Math.floor(robot.metadata.frameCount/2)],['final',robot.metadata.frameCount-1]]) {
        const ms=Math.ceil(frame*1000/robot.metadata.fps);
        await preview.locator('input[type=range]').fill(String(ms));
        await expect.poll(()=>mediaState().then(state=>state.currentTimeSec)).toBeCloseTo(ms/1000,1);
        await page.waitForTimeout(150);
        // A blank character canvas cannot qualify a decoded storyboard checkpoint.
        // The exact exit tail is intentionally green before chroma keying.
        if (!(phase.phaseId === 'exit' && label === 'final')) {
          expect(await stage.locator('.cinematic-canvas').evaluate(canvas =>
            canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data.some((value,index) => index % 4 === 3 && value > 0))).toBe(true);
        }
        const file=`${phase.phaseId}-${label}.png`;await stage.screenshot({path:testInfo.outputPath(file)});
        evidence.captures.push({phaseId:phase.phaseId,label,frame,ms,file,sourceSha256:robot.sha256,assetVersionId:robot.assetVersionId,geometry:robot.metadata.rect});
      }
    }
    await preview.getByRole('button',{name:'Replay',exact:false}).click();
    await expect.poll(()=>mediaState().then(state=>state.currentTimeSec)).toBe(0);
  fs.writeFileSync(testInfo.outputPath('candidate-preview.json'),JSON.stringify(evidence,null,2));
  await page.goto('/#/courses');
  await page.unrouteAll({behavior:'wait'});
  expect(errors).toEqual([]);
});
