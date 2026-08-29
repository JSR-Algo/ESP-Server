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
