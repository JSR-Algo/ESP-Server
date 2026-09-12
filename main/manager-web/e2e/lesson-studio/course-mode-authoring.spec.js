const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { gotoAppRoute, installCinematicTestRoutes, stabilizeStageMedia } = require('./helpers/navigation');
const {
  adminApi,
  createCourseModeDraft,
  createPublishableCourseModeVisuals,
  createVisualTriple,
} = require('./helpers/admin-api');

const gotoLessonEditor = (page, lessonId) => gotoAppRoute(page, `#/lesson-editor?lessonId=${lessonId}`);

test('authors, saves, and reloads the canonical Course Mode contract', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  await installCinematicTestRoutes(page);
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

test('renders the persisted asset triple in the exact 480x320 renderer-v5 projection', async ({ page }, testInfo) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  await installCinematicTestRoutes(page);
  const fixture = await createCourseModeDraft(page);
  const visuals = await createVisualTriple(page, fixture.lesson.id, fixture.runId, { bind: false });
  await createPublishableCourseModeVisuals(page, fixture.lesson.id);

  const initialSteps = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/steps`);
  const initialVersionIds = (initialSteps[0].visualRefs || []).map((reference) => (
    reference.assetVersionId || reference.asset_version_id
  ));
  expect(initialVersionIds).not.toEqual(expect.arrayContaining(Object.values(visuals).map((visual) => visual.id)));

  const initialPreviewGate = page.waitForResponse((response) => (
    response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`)
      && response.request().method() === 'GET'
      && [400, 422].includes(response.status())
  ));
  await gotoLessonEditor(page, fixture.lesson.id);
  await expect(page.getByTestId('course-mode-activity-timeline')).toBeVisible();
  expect([400, 422]).toContain((await initialPreviewGate).status());
  const selectVisual = async (testId, assetKey) => {
    const tile = page.getByTestId(testId).locator('.asset-tile').filter({ hasText: assetKey }).first();
    await expect(tile).toBeVisible();
    await tile.locator('.asset-tile__select').click();
  };
  const visualSave = page.waitForResponse((response) => (
    response.url().endsWith(`/lessons/${fixture.lesson.id}/visuals`)
      && response.request().method() === 'PUT'
      && response.request().postDataJSON().robotAssetVersionId === visuals.robotOverlay.id
  ));
  await selectVisual('lesson-robot-selector', `robot.e2e.${fixture.runId}`);
  await selectVisual('lesson-background-selector', 'scene.playground-park');
  const recoveredPreview = page.waitForResponse((response) => (
    response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`)
      && response.request().method() === 'GET'
      && response.status() === 200
  ));
  await selectVisual('lesson-object-selector', 'object.no');
  const visualSaveResponse = await visualSave;
  expect(visualSaveResponse.status(), await visualSaveResponse.text()).toBe(200);
  expect(visualSaveResponse.request().postDataJSON()).toEqual({
    backgroundAssetVersionId: visuals.backgroundScene.id,
    objectAssetVersionId: visuals.teachingObject.id,
    robotAssetVersionId: visuals.robotOverlay.id,
  });
  const persistedSteps = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/steps`);
  const persistedBySlot = Object.fromEntries((persistedSteps[0].visualRefs || []).map((reference) => [
    reference.slot,
    reference.assetVersionId || reference.asset_version_id,
  ]));
  expect(persistedBySlot).toMatchObject({
    backgroundScene: visuals.backgroundScene.id,
    teachingObject: visuals.teachingObject.id,
    robotOverlay: visuals.robotOverlay.id,
  });
  const response = await recoveredPreview;
  expect(response.status(), await response.text()).toBe(200);
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
  const backgroundIdentityBefore = await stage.locator('.layer-background').evaluateAll((elements) =>
    elements.map((element) => `${element.className}|${element.getAttribute('src')}|${element.getAttribute('style')}`));
  await expect(page.getByText(/entrance:/i)).toBeVisible();
  const transitionStartedAt = Date.now();
  await page.getByRole('button', { name: /play lesson/i }).click();
  await expect(page.getByText(/Step 2 \/ /)).toBeVisible();
  if (testInfo.project.name.includes('webkit')) {
    expect(Date.now() - transitionStartedAt).toBeLessThan(5000);
    await stabilizeStageMedia(stage);
    await expect(stage).toHaveScreenshot('course-mode-step-2.png', {
      animations: 'disabled',
      maxDiffPixels: 1200,
      maxDiffPixelRatio: 0.01,
    });
  }
  await expect(stage.locator('.layer-robotOverlay')).toBeVisible();
  expect(await stage.locator('.layer-background').evaluateAll((elements) =>
    elements.map((element) => `${element.className}|${element.getAttribute('src')}|${element.getAttribute('style')}`)))
    .toEqual(backgroundIdentityBefore);
  await expect(stage.locator('.layer-teachingObject')).toHaveCount(0);
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
