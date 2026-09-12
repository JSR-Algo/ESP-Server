const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { gotoAppRoute, stabilizeStageMedia } = require('./helpers/navigation');
const { visitPersistedPhases } = require('./helpers/persisted-phases');
const {
  adminApi,
  createCourseModeDraft,
  createPublishableCourseModeVisuals,
} = require('./helpers/admin-api');

const gotoLessonEditor = (page, lessonId) => gotoAppRoute(page, `#/lesson-editor?lessonId=${lessonId}`);

test('@s07-assets persists seven phase bindings and preserves activity image versions on reselection', async ({page},testInfo)=>{
  const {s07Draft,s07Api,openS07Session}=require('./helpers/s07-session');
  const config=await openS07Session(page);
  const {lesson}=await s07Draft(page);
  const source=await s07Api(page,'GET',`/lessons/${config.source}/visuals`);
  const sourceAssets=await s07Api(page,'GET',`/lessons/${config.source}/assets?profile=espTft`);
  const sourceImage=sourceAssets.assets.find(a=>(a.layer||a.slot)==='backgroundScene');
  expect(sourceImage && sourceImage.assetId).toBeTruthy();
  await s07Api(page,'POST',`/lessons/${lesson.id}/assets`,{profile:'espTft',sourceAssetId:sourceImage.assetId});
  const phases=['flyIn','walk','teach','listen','thinking','celebrate','exit'];
  const first=slot=>source.refs.find(r=>r.slot===slot)?.assetVersionId;
  const ids=process.env.CPR_S07_SESSION_FILE
    ? {background:first('backgroundScene'),object:first('teachingObject'),...Object.fromEntries(phases.map(p=>[p,first(`robotOverlay.${p}`)]))}
    : (await require('./helpers/s07-session').publishedCourseModeSelection(page,lesson.id)).ids;
  expect(Object.values(ids).every(Boolean)).toBe(true);
  const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
  await gotoLessonEditor(page,lesson.id);
  const panel=page.getByTestId('course-mode-visual-selection');await expect(panel).toBeVisible();
  const libraryRoute='**/lesson-visual-assets?*';
  await page.route(libraryRoute,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'Injected library failure'})}));
  await panel.getByRole('button',{name:'Refresh asset library',exact:true}).click();
  await expect(panel.locator('.el-alert--error')).toBeVisible();
  await page.unroute(libraryRoute);
  await panel.getByRole('button',{name:'Refresh asset library',exact:true}).click();
  await expect(panel.locator('.el-alert--error')).toHaveCount(0);
  for(const [key,id]of Object.entries(ids))await panel.getByTestId(`course-visual-${key}`).locator('select').selectOption(id);
  const save=async()=>{const pending=page.waitForResponse(r=>r.url().endsWith(`/lessons/${lesson.id}/visuals`)&&r.request().method()==='PUT');await panel.getByRole('button',{name:'Save visual bindings',exact:true}).click();const response=await pending;expect(response.status(),await response.text()).toBe(200);await expect(panel).toContainText('Visual versions saved and read back.');return response.request().postDataJSON();};
  const request=await save();expect(Object.keys(request.robotAssetVersionIds).sort()).toEqual(phases.slice().sort());
  const before=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  const catalog=await s07Api(page,'GET','/lesson-visual-assets?category=robotPose&profile=espTft');
  const copyVersion=asset=>s07Api(page,'POST',`/lesson-visual-assets/${encodeURIComponent(asset.asset_key||asset.assetKey)}/versions`,{category:asset.category,title:asset.title,profile:'espTft',storagePath:asset.storage_path||asset.storagePath,sha256:asset.sha256,mimeType:asset.mime_type||asset.mimeType,bytes:Number(asset.bytes),width:Number(asset.width),height:Number(asset.height),publicationState:'published',compatibilityMetadata:asset.compatibility_metadata||asset.compatibilityMetadata});
  const selectedTalk=catalog.find(a=>(a.version_id||a.versionId)===ids.teach);
  expect(selectedTalk).toBeTruthy();const alternativeId=(await copyVersion(selectedTalk)).id;
  await panel.getByRole('button',{name:'Refresh asset library',exact:true}).click();
  await panel.getByTestId('course-visual-teach').locator('select').selectOption(alternativeId);
  const concurrent=await s07Api(page,'GET',`/lessons/${lesson.id}/course-mode`);
  concurrent.contract.activities[0].contextId='s07-visual-conflict';
  const {withCanonicalCourseModeChecksum}=require('./helpers/admin-api');
  await s07Api(page,'PUT',`/lessons/${lesson.id}/course-mode`,{contract:withCanonicalCourseModeChecksum(concurrent.contract),expectedChecksum:concurrent.checksum,expectedVisualChecksum:concurrent.visualChecksum});
  const conflictResponse=page.waitForResponse(r=>r.url().endsWith(`/lessons/${lesson.id}/visuals`)&&r.request().method()==='PUT');
  await panel.getByRole('button',{name:'Save visual bindings',exact:true}).click();
  expect((await conflictResponse).status()).toBe(409);
  await expect(panel.getByTestId('course-visual-teach').locator('select')).toHaveValue(alternativeId);
  await panel.getByRole('button',{name:'Read saved bindings',exact:true}).click();
  await panel.getByRole('button',{name:'Keep my selections with this saved version',exact:true}).click();
  const fresh=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  const second=await save();expect(second.expectedVisualChecksum).toBe(fresh.visualChecksum);
  const after=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  expect(after.refs.filter(r=>r.slot!=='robotOverlay.teach')).toEqual(before.refs.filter(r=>r.slot!=='robotOverlay.teach'));
  expect(after.refs.filter(r=>r.slot==='robotOverlay.teach').every(r=>r.assetVersionId===alternativeId)).toBe(true);
  for(const id of new Set(after.refs.map(r=>r.assetVersionId)))expect(JSON.stringify(after.cinematicPhases)).toContain(id);
  // New versions reuse the exact selected source bytes/metadata in this isolated database.
  const imageCatalog=await s07Api(page,'GET','/lesson-visual-assets?profile=espTft');
  const replacements={};let current=after;
  for(const [key,slot]of [['background','backgroundScene'],['object','teachingObject']]){
    const asset=imageCatalog.find(a=>(a.version_id||a.versionId)===ids[key]);expect(asset).toBeTruthy();
    const version=await copyVersion(asset);
    replacements[key]=version.id;
    await panel.getByRole('button',{name:'Refresh asset library',exact:true}).click();
    await panel.getByTestId(`course-visual-${key}`).locator('select').selectOption(version.id);
    await save();const next=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
    expect(next.refs.filter(r=>r.slot!==slot||r.assetVersionId!==version.id)).toEqual(current.refs.filter(r=>r.slot!==slot||r.assetVersionId!==ids[key]));
    expect(next.refs.some(r=>r.slot===slot&&r.assetVersionId===version.id)).toBe(true);current=next;
  }
  const timeline=page.getByTestId('course-mode-activity-timeline');
  const backgroundField=timeline.locator('.activity-card').first().locator('.el-form-item').filter({hasText:'Published background key'}).locator('input');
  await backgroundField.click();await page.locator('.el-select-dropdown:visible .el-select-dropdown__item').filter({hasText:'scene.classroom'}).first().click();
  await timeline.getByRole('button',{name:'Save Course Mode',exact:true}).click();await expect(timeline.getByRole('status')).toContainText('Saved');
  const override=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  const firstStep=current.refs[0].stepKey;
  expect(override.refs.filter(r=>r.slot!=='backgroundScene'||r.stepKey!==firstStep)).toEqual(current.refs.filter(r=>r.slot!=='backgroundScene'||r.stepKey!==firstStep));
  expect(override.refs.find(r=>r.slot==='backgroundScene'&&r.stepKey===firstStep).assetVersionId).not.toBe(replacements.background);
  // Delay the real catalog response until saved refs render, exercising dynamic option hydration.
  await page.route(libraryRoute,async route=>{const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,500));await route.fulfill({response});});
  await page.reload();await expect(panel.getByTestId('course-visual-teach').locator('select')).toHaveValue(alternativeId);
  await expect(panel).not.toContainText('Loading asset library...');
  await expect(panel.getByTestId('course-visual-teach').locator('select')).toHaveValue(alternativeId);
  await page.unroute(libraryRoute);
  await expect(panel.getByTestId('course-visual-background').locator('select')).toHaveValue(override.refs.find(r=>r.slot==='backgroundScene').assetVersionId);
  await expect(panel.getByTestId('course-visual-object').locator('select')).toHaveValue(replacements.object);
  for(const width of [1440,390]){await page.setViewportSize({width,height:width===390?844:900});await panel.scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await panel.screenshot({path:testInfo.outputPath(`bindings-${width}.png`)});}
  await require('node:fs/promises').writeFile(testInfo.outputPath('persisted-bindings.json'),JSON.stringify({lessonId:lesson.id,sourceLessonId:config.source,request,second,before,after,replacements,current,override,pageErrors},null,2));
  expect(pageErrors).toEqual([]);
});

test('authors, saves, and reloads the canonical Course Mode contract', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  const fixture = await createCourseModeDraft(page);

  await gotoLessonEditor(page, fixture.lesson.id);
  const timeline = page.getByTestId('course-mode-activity-timeline');
  await expect(timeline).toBeVisible();
  await expect(timeline.locator('.activity-card')).toHaveCount(fixture.contract.activities.length);
  await expect(page.getByTestId('course-mode-projected-steps-read-only')).toBeVisible();
  await expect(page.getByTestId('lesson-step-subject')).toBeDisabled();

  const firstActivityType = timeline.locator('.activity-card .el-form-item')
    .filter({ hasText: 'Activity type' }).first().locator('input');
  await firstActivityType.fill(`${fixture.contract.activities[0].activityType}_e2e`);
  const savePreviewSettled = page.waitForResponse((response) => (
    response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`)
      && response.request().method() === 'GET'
  ));
  await timeline.getByRole('button', { name: 'Save Course Mode' }).click();
  await expect(timeline.getByRole('status')).toContainText('Saved');
  await savePreviewSettled;

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('course-mode-activity-timeline').locator('.activity-card'))
    .toHaveCount(fixture.contract.activities.length);
  await expect(page.getByTestId('course-mode-activity-timeline').locator('.activity-card .el-form-item')
    .filter({ hasText: 'Activity type' }).first().locator('input'))
    .toHaveValue(`${fixture.contract.activities[0].activityType}_e2e`);
  const persisted = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/course-mode`);
  expect(persisted.contract.activities[0].activityType).toBe(`${fixture.contract.activities[0].activityType}_e2e`);
  assertNoUnexpectedPageErrors();
});

test('renders persisted seven-phase bindings in the exact 480x320 renderer-v5 projection', async ({ page }, testInfo) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  const fixture = await createCourseModeDraft(page);
  const { publishedCourseModeSelection } = require('./helpers/s07-session');
  const selection = await publishedCourseModeSelection(page, fixture.lesson.id);
  await createPublishableCourseModeVisuals(page, fixture.lesson.id);
  const initial = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/visuals`);
  expect(initial.refs).toEqual([]);
  await gotoLessonEditor(page, fixture.lesson.id);
  const panel = page.getByTestId('course-mode-visual-selection');
  await expect(panel).toBeVisible();
  for (const [key, id] of Object.entries(selection.ids)) {
    await panel.getByTestId(`course-visual-${key}`).locator('select').selectOption(id);
  }
  const visualSave = page.waitForResponse(response => (
    response.url().endsWith(`/lessons/${fixture.lesson.id}/visuals`)
      && response.request().method() === 'PUT'
  ));
  const recoveredPreview = page.waitForResponse(response => (
    response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`)
      && response.request().method() === 'GET' && response.status() === 200
  ));
  await panel.getByRole('button', { name: 'Save visual bindings', exact: true }).click();
  const visualSaveResponse = await visualSave;
  expect(visualSaveResponse.status(), await visualSaveResponse.text()).toBe(200);
  expect(visualSaveResponse.request().postDataJSON()).toEqual({
    expectedChecksum: initial.checksum, expectedVisualChecksum: initial.visualChecksum,
    backgroundAssetVersionId: selection.ids.background,
    objectAssetVersionId: selection.ids.object,
    robotAssetVersionIds: selection.robotAssetVersionIds,
  });
  const persisted = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/visuals`);
  for (const [phase, id] of Object.entries(selection.robotAssetVersionIds)) {
    const refs = persisted.refs.filter(ref => ref.slot === `robotOverlay.${phase}`);
    expect(refs.length).toBeGreaterThan(0);
    expect(refs.every(ref => ref.assetVersionId === id)).toBe(true);
  }
  for (const id of [selection.ids.background, selection.ids.object]) {
    expect(persisted.refs.some(ref => ref.assetVersionId === id)).toBe(true);
    expect(JSON.stringify(persisted.cinematicPhases)).toContain(id);
  }
  const response = await recoveredPreview;
  expect(response.status(), await response.text()).toBe(200);
  const { manifest } = (await response.json()).data;
  const stage = page.getByTestId('esp-tft-stage');
  await expect(stage).toBeAttached();
  await stage.scrollIntoViewIfNeeded();
  await expect(stage).toBeVisible();
  expect(await stage.evaluate((element) => ({ width: element.clientWidth, height: element.clientHeight })))
    .toEqual({ width: 480, height: 320 });
  if (testInfo.project.name.includes('mobile')) {
    const box = await stage.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
  }
  await expect(stage.locator('.layer-background')).toBeVisible();
  await expect(stage.locator('.layer-teachingObject')).toBeVisible();
  await expect(stage.locator('.layer-robotOverlay')).toBeVisible();
  await expect(stage.locator('.layer-robotOverlay video')).toHaveClass(/hidden/);
  await expect(stage.locator('.layer-robotOverlay canvas')).toBeVisible();
  if (testInfo.project.name.includes('webkit')) {
    await stabilizeStageMedia(stage);
    await expect(stage.locator('.layer-robotOverlay video')).toHaveClass(/hidden/);
    const keyedCorner = await stage.locator('.layer-robotOverlay canvas').evaluate((canvas) => (
      Array.from(canvas.getContext('2d').getImageData(2, 2, 1, 1).data)
    ));
    expect(keyedCorner[3]).toBe(0);
    const videoState = await stage.locator('video').evaluateAll((videos) => videos.map((video) => ({
      readyState: video.readyState,
      duration: video.duration,
      currentTime: video.currentTime,
    })));
    expect(videoState.length).toBeGreaterThan(0);
    for (const video of videoState) {
      expect(video.readyState).toBeGreaterThanOrEqual(1);
      expect(Number.isFinite(video.duration)).toBe(true);
      expect(video.duration).toBeGreaterThan(0);
      expect(video.currentTime).toBeGreaterThan(0.3);
    }
    await stabilizeStageMedia(stage, 0.7);
    const progressedTimes = await stage.locator('video').evaluateAll((videos) => (
      videos.map((video) => video.currentTime)
    ));
    for (let index = 0; index < progressedTimes.length; index += 1) {
      expect(progressedTimes[index]).toBeGreaterThan(videoState[index].currentTime);
    }
    await stabilizeStageMedia(stage);
    await expect(stage).toHaveScreenshot('course-mode-step-1.png', {
      animations: 'disabled',
      // WebKit canvas/video decoding varies only inside animated antialiased pixels.
      maxDiffPixels: 1200,
      maxDiffPixelRatio: 0.01,
    });
  }
  const geometry = await stage.locator('.stage-layer').evaluateAll((elements) => elements.map((element) => ({
    className: element.className,
    left: element.offsetLeft,
    top: element.offsetTop,
    right: element.offsetLeft + element.offsetWidth,
    bottom: element.offsetTop + element.offsetHeight,
    zIndex: Number(getComputedStyle(element).zIndex || 0),
  })));
  expect(geometry).toEqual(expect.arrayContaining([
    expect.objectContaining({ className: expect.stringContaining('layer-background'), left: 0, top: 0, right: 480, bottom: 320 }),
    expect.objectContaining({ className: expect.stringContaining('layer-teachingObject') }),
    expect.objectContaining({ className: expect.stringContaining('layer-robotOverlay') }),
  ]));
  for (const layer of geometry) {
    expect(layer.left, layer.className).toBeGreaterThanOrEqual(0);
    expect(layer.top, layer.className).toBeGreaterThanOrEqual(0);
    expect(layer.right, layer.className).toBeLessThanOrEqual(480);
    expect(layer.bottom, layer.className).toBeLessThanOrEqual(320);
  }
  const zByLayer = Object.fromEntries(geometry.map((layer) => [
    ['background', 'teachingObject', 'robotOverlay'].find((name) => layer.className.includes(`layer-${name}`)),
    layer.zIndex,
  ]));
  expect(zByLayer.background).toBeLessThan(zByLayer.teachingObject);
  expect(zByLayer.teachingObject).toBeLessThan(zByLayer.robotOverlay);
  const preview = page.locator('.robot-preview').first();
  await visitPersistedPhases(page, manifest, async ({ phase, stepIndex }) => {
    await require('./helpers/real-service-evidence').assertDecodedStage(stage);
    await stage.screenshot({ path: testInfo.outputPath(`actual-${stepIndex}-${phase.phaseId}.png`) });
  });
  await preview.getByRole('button', { name: /play cinematic/i }).click();
  await expect.poll(() => stage.locator('video').evaluate(video => video.currentTime)).toBeGreaterThan(0);
  await preview.getByRole('button', { name: /replay/i }).click();
  await expect.poll(() => stage.locator('video').evaluate(video => video.currentTime)).toBeLessThan(0.2);
  assertNoUnexpectedPageErrors();
});

// Scoped real-service proof uses an identified local author session and task-owned course.
// Full manager login/publication matrix remains in the standard T08 journey.
test('@s07 persists consecutive edits, reloads, compares conflicts, and preserves failed drafts', async ({ page, context }, testInfo) => {
  const { s07Draft, s07Api, openS07Session } = require('./helpers/s07-session');
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const {lesson,course}=await s07Draft(page);
  await page.goto(`/#/course-lessons?courseId=${course.id}&title=${course.title}`);
  await expect(page.getByText(lesson.title).first()).toBeVisible();
  await page.goto(`/#/lesson-editor?lessonId=${lesson.id}`);
  const timeline=page.getByTestId('course-mode-activity-timeline');
  await expect(timeline).toBeVisible();
  const field=timeline.locator('.el-form-item').filter({hasText:'Context'}).first().locator('input');
  const read=()=>s07Api(page,'GET',`/lessons/${lesson.id}/course-mode`);
  console.log('S07 editor loaded');
  for(const value of ['s07-first-edit','s07-second-edit']) {
    const before=await read(); await field.fill(value);
    const request=page.waitForRequest(r=>r.method()==='PUT'&&r.url().endsWith(`/lessons/${lesson.id}/course-mode`));
    await timeline.getByRole('button',{name:'Save Course Mode'}).click();
    const sent=(await request).postDataJSON();expect(sent.expectedChecksum).toBe(before.checksum);expect(sent.expectedVisualChecksum).toBe(before.visualChecksum);
    await expect(timeline.getByRole('status')).toContainText('Saved');
    expect((await read()).contract.activities[0].contextId).toBe(value);
  }
  console.log('S07 consecutive saves verified');
  await page.reload();await expect(field).toHaveValue('s07-second-edit');
  const other=await context.newPage();await openS07Session(other);await other.goto(`/#/lesson-editor?lessonId=${lesson.id}`);
  await expect(other.getByTestId('course-mode-activity-timeline').locator('.el-form-item').filter({hasText:'Context'}).first().locator('input')).toHaveValue('s07-second-edit');
  console.log('S07 second tab readback verified');
  await field.fill('s07-local-conflict');
  const concurrent=await read();concurrent.contract.activities[0].contextId='s07-concurrent';
  const {withCanonicalCourseModeChecksum}=require('./helpers/admin-api');
  await s07Api(page,'PUT',`/lessons/${lesson.id}/course-mode`,{expectedChecksum:concurrent.checksum,expectedVisualChecksum:concurrent.visualChecksum,contract:withCanonicalCourseModeChecksum(concurrent.contract)});
  await timeline.getByRole('button',{name:'Save Course Mode'}).click();
  const conflict=page.getByTestId('course-mode-conflict');await expect(conflict).toBeVisible();await expect(field).toHaveValue('s07-local-conflict');
  await conflict.getByRole('button',{name:'Read saved version'}).click();await expect(conflict.getByRole('button',{name:'Keep my draft for editing'})).toBeVisible();
  expect((await read()).contract.activities[0].contextId).toBe('s07-concurrent');
  await conflict.getByRole('button',{name:'Keep my draft for editing'}).click();await timeline.getByRole('button',{name:'Save Course Mode'}).click();await expect(timeline.getByRole('status')).toContainText('Saved');
  console.log('S07 conflict recovered');
  for(const status of [403,500,0]) {
    await field.fill(`s07-failure-${status}`);
    const matcher=`**/lessons/${lesson.id}/course-mode`;
    await page.route(matcher,route=>route.request().method()==='PUT'?(status?route.fulfill({status,contentType:'application/json',body:JSON.stringify({message:`Injected ${status}`})}):route.abort('internetdisconnected')):route.continue());
    await timeline.getByRole('button',{name:'Save Course Mode'}).click();await expect(timeline.locator('.el-alert--error')).toContainText('retained');await expect(field).toHaveValue(`s07-failure-${status}`);await page.unroute(matcher);
    if(status!==403){await conflict.getByRole('button',{name:'Read saved version'}).click();await conflict.getByRole('button',{name:'Keep my draft for editing'}).click();}
  }
  console.log('S07 failure drafts verified');
  await timeline.getByRole('button',{name:'Undo edits'}).click();await page.getByRole('button',{name:'OK',exact:true}).click();await expect(page.locator('.el-message-box')).toBeHidden();await expect(field).toHaveValue('s07-local-conflict');
  await field.fill('s07-do-not-leave');await page.evaluate(()=>{location.hash='#/lesson-editor?lessonId=another-lesson'});await expect(page.getByRole('dialog',{name:'Unsaved changes'})).toBeVisible();await page.getByRole('button',{name:'Stay',exact:true}).click();await expect(field).toHaveValue('s07-do-not-leave');
  await page.screenshot({path:testInfo.outputPath('draft-retained.png'),fullPage:true});expect(errors).toEqual([]);
  await testInfo.attach('persisted-readback',{body:JSON.stringify(await read(),null,2),contentType:'application/json'});
  await other.close();
});

test('@s07 delayed responses and expired login retain the active draft', async ({page},testInfo)=>{
 const {s07Draft,openS07Session}=require('./helpers/s07-session');const fixture=await s07Draft(page);const second=await s07Draft(page);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const url=id=>`/#/lesson-editor?lessonId=${id}`;
 let completed;const finished=new Promise(resolve=>{completed=resolve});
 let release;const held=new Promise(resolve=>{release=resolve});let intercepted;
 const entered=new Promise(resolve=>{intercepted=resolve});
 await page.route(`**/lessons/${fixture.lesson.id}/course-mode`,async route=>{const response=await route.fetch();intercepted();await held;await route.fulfill({response});completed();});
 await page.goto(url(fixture.lesson.id));await entered;await page.evaluate(id=>{location.hash=`#/lesson-editor?lessonId=${id}`},second.lesson.id);
 const timeline=page.getByTestId('course-mode-activity-timeline');await expect(timeline).toBeVisible();release();await finished;await page.unroute(`**/lessons/${fixture.lesson.id}/course-mode`);
 await expect(page.locator('.page-title')).toContainText(second.lesson.title);
 const field=timeline.locator('.el-form-item').filter({hasText:'Context'}).first().locator('input');await field.fill('s07-expired-draft');
 await page.route(`**/lessons/${second.lesson.id}/course-mode`,route=>route.request().method()==='PUT'?route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({message:'Injected expired author session'})}):route.continue());
 await timeline.getByRole('button',{name:'Save Course Mode'}).click();await expect(timeline.locator('.el-alert--error')).toContainText('Sign in again');await expect(field).toHaveValue('s07-expired-draft');
 const dialog=page.getByRole('dialog',{name:/sign in as author/i});await expect(dialog).toBeVisible();await page.unroute(`**/lessons/${second.lesson.id}/course-mode`);
 await page.screenshot({path:testInfo.outputPath('expired-draft.png'),fullPage:true});expect(errors).toEqual([]);
});
