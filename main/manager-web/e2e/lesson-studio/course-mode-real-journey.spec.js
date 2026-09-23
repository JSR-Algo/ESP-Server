const { test, expect } = require('@playwright/test');
const { readFileSync } = require('node:fs');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi, createCourseModeDraft, createVisualTriple } = require('./helpers/admin-api');
const { observeJourney, assertHttpMedia, assertDecodedStage, waitForPublishedPack } = require('./helpers/real-service-evidence');
const { visitPersistedPhases } = require('./helpers/persisted-phases');
const { changedActivityDuration } = require('./helpers/course-mode-edit-values');

test('real v5 next draft, edits, publication, new assignment, rollback and insights', async ({ page }, testInfo) => {
  test.setTimeout(180000);
  const fixturePath = process.env.LESSON_STUDIO_E2E_ASSIGNMENT_FIXTURE;
  expect(fixturePath, 'identified local assignment fixture is required').toBeTruthy();
  const assignmentFixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
  expect(assignmentFixture.composeProject).toBe(process.env.COMPOSE_PROJECT_NAME || process.env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME || 'tbot-ls-e2e');
  expect(assignmentFixture.scope).toBe('isolated-local-test');
  expect(assignmentFixture.physicalAcceptance).toBe(false);
  expect(assignmentFixture.curriculum, 'verified canonical catalog binding is required').toBeTruthy();
  const journal = observeJourney(page);
  const ownedAssignments = [];
  const ownedRetainedRequests = [];
  journal.evidence.packReadiness = [];
  journal.evidence.cleanup = [];
  try {
    await loginAsLessonAuthor(page);
    const fixture = await createCourseModeDraft(page, { curriculum: assignmentFixture.curriculum });
    await createVisualTriple(page, fixture.lesson.id, fixture.runId);
    await adminApi(page, 'POST', `/lessons/${fixture.lesson.id}/publish`);
    const source = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}`);
    const sourceManifest = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
    await page.goto(`/#/course-lessons?courseId=${fixture.course.id}`);
    await expect(page.getByText(source.title).first()).toBeVisible();
    await page.goto(`/#/lesson-editor?lessonId=${source.id}`);
    const nextResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/lessons/${source.id}/new-version`));
    await page.getByTestId('create-next-version').click();
    const response = await nextResponse;
    expect(response.status()).toBe(201);
    const next = (await response.json()).data;
    const nextId = next.id || next.lessonId;
    await expect(page).toHaveURL(new RegExp(`lessonId=${nextId}`));
    const before = await adminApi(page, 'GET', `/lessons/${nextId}/course-mode`);
    const timeline = page.getByTestId('course-mode-activity-timeline');
    const context = timeline.locator('.activity-card').first().locator('.el-form-item').filter({ hasText: 'Context' }).locator('input');
    await context.fill(`t08-${fixture.runId}`);
    const duration = timeline.locator('.activity-card').first().locator('.el-form-item').filter({ hasText: 'Duration (seconds)' }).getByRole('spinbutton');
    const durationValue = changedActivityDuration(before.contract.activities[0]);
    await duration.fill(String(durationValue)); await duration.blur();
    const meaning = timeline.locator('.target-card').first().locator('.el-form-item').filter({ hasText: 'Vietnamese meaning' }).locator('input');
    const meaningValue = before.contract.targets[0].vietnameseMeanings.join(', ') + ', t08';
    await meaning.fill(meaningValue);
    const save = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith(`/lessons/${nextId}/course-mode`));
    const saveButton = timeline.getByRole('button', { name: 'Save Course Mode', exact: true });
    await saveButton.scrollIntoViewIfNeeded();
    const box = await saveButton.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
    await saveButton.click();
    expect((await save).status()).toBe(200);
    await expect(timeline.getByRole('status')).toContainText('Saved');
    const edited = await adminApi(page, 'GET', `/lessons/${nextId}/course-mode`);
    expect(edited.contract.activities[0].contextId).toBe(`t08-${fixture.runId}`);
    expect(edited.contract.activities[0].expectedDurationSec).toBe(durationValue);
    expect(edited.contract.targets[0].vietnameseMeanings).toContain('t08');

    const panel = page.getByTestId('course-mode-visual-selection');
    const selected = await createVisualTriple(page, nextId, fixture.runId, { bind: false });
    const catalog = await adminApi(page, 'GET', '/lesson-visual-assets?profile=espTft');
    // A replacement version retains exactly the selected source bytes and metadata.
    for (const slot of ['background', 'object', 'teach']) {
      const current = catalog.find(asset => (asset.version_id || asset.versionId) === selected.ids[slot]);
      expect(current, `actual published ${slot} source`).toBeTruthy();
      const replacement = await adminApi(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(current.asset_key || current.assetKey)}/versions`, {
        category: current.category, title: current.title, profile: 'espTft',
        storagePath: current.storage_path || current.storagePath, sha256: current.sha256,
        mimeType: current.mime_type || current.mimeType, bytes: Number(current.bytes),
        width: Number(current.width), height: Number(current.height), publicationState: 'published',
        compatibilityMetadata: current.compatibility_metadata || current.compatibilityMetadata,
      });
      selected.ids[slot] = replacement.id;
    }
    await panel.getByRole('button', { name: 'Refresh asset library', exact: true }).click();
    for (const [slot, id] of Object.entries(selected.ids)) await panel.getByTestId(`course-visual-${slot}`).locator('select').selectOption(id);
    const bindingResponse = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith(`/lessons/${nextId}/visuals`));
    await panel.getByRole('button', { name: 'Save visual bindings', exact: true }).click();
    expect((await bindingResponse).status()).toBe(200);
    await expect(panel).toContainText('Visual versions saved and read back.');
    const bindings = await adminApi(page, 'GET', `/lessons/${nextId}/visuals`);
    await journal.waitForSettledRequests();
    await page.reload(); await expect(context).toHaveValue(`t08-${fixture.runId}`);
    for (const [slot, id] of Object.entries(selected.ids)) await expect(panel.getByTestId(`course-visual-${slot}`).locator('select')).toHaveValue(id);
    const preview = await adminApi(page, 'GET', `/lessons/${nextId}/manifest-preview?profile=espTft`);
    expect(preview.manifest.cinematicPhases).toEqual(bindings.cinematicPhases);
    await journal.checkpoint(testInfo, 'saved-reloaded', { nextId, edited, bindings, preview });
    await assertHttpMedia(page, preview.manifest);
    const stage = page.getByTestId('esp-tft-stage');
    await stage.scrollIntoViewIfNeeded();
    await visitPersistedPhases(page, preview.manifest, async ({ phase, activityId, stepIndex }) => {
      try { await assertDecodedStage(stage, phase); } catch (error) {
        await require('node:fs/promises').writeFile(testInfo.outputPath('decoder-failure.json'), JSON.stringify({phase, state:await stage.evaluate(el => {
          const layer=el.querySelector('.layer-robotOverlay');const vm=layer?.__vue__;const canvas=layer?.querySelector('canvas');
          return {vm:vm?.$options.name,error:vm?.errorMessage,src:vm?.src,identity:vm?.mjpegIdentity,state:vm?._mjpeg?.state(),index:vm?._mjpeg?.index,clock:vm?.clockMs,canvas:canvas&&{width:canvas.width,height:canvas.height},text:el.innerText};
        })},null,2)); throw error;
      }
      await journal.checkpoint(testInfo, `actual-${stepIndex}-${phase.phaseId}`, { activityId, phase }, stage);
    });
    await journal.checkpoint(testInfo, 'actual-preview', { nextId, checksum: preview.checksum });

    const validate = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/lessons/${nextId}/validate`));
    await page.getByRole('button', { name: 'Validate', exact: true }).click(); expect((await validate).status()).toBe(200);
    const simulation = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes(`/lessons/${nextId}/simulate`));
    await page.getByRole('button', { name: 'Simulate', exact: true }).click(); expect((await simulation).status()).toBe(200);
    await expect(page.locator('.simulation-result')).toContainText('lesson_completed');
    await page.getByRole('button', { name: 'Publish', exact: true }).click();
    const review = page.getByRole('dialog').filter({ has: page.getByTestId('immutable-ack') });
    await review.getByTestId('immutable-ack').click();
    const publication = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/lessons/${nextId}/publish`));
    await review.getByRole('button', { name: 'Publish reviewed version', exact: true }).click();
    expect((await publication).status()).toBe(200);
    const published = await adminApi(page, 'GET', `/lessons/${nextId}`);
    expect(published.status).toBe('published');
    expect(Number(published.lesson_version || published.lessonVersion)).toBe(Number(source.lesson_version || source.lessonVersion) + 1);
    expect(await adminApi(page, 'GET', `/lessons/${source.id}/manifest-preview?profile=espTft`)).toEqual(sourceManifest);
    await journal.checkpoint(testInfo, 'published', { published, sourceManifest });

    // The fixture is supplied by the reviewed isolated-stack setup; never select a real device by search order.
    const { deviceId, childId, childName } = assignmentFixture;
    expect(deviceId).toMatch(/^[0-9a-f-]{36}$/); expect(childId).toMatch(/^[0-9a-f-]{36}$/);
    expect(typeof childName).toBe('string'); expect(childName.trim().length).toBeGreaterThan(0);
    const lessonKey = published.lesson_key || published.lessonKey;
    await waitForPublishedPack(page, published, published.manifest_checksum || published.manifestChecksum, journal.evidence.packReadiness);
    await page.goto(`/#/course-lessons?courseId=${fixture.course.id}`);
    await page.locator('.filter-row input').first().fill(published.title);
    const lessonRow = page.locator('.el-table__body-wrapper tbody tr').filter({ hasText: lessonKey }).filter({ hasText: published.title });
    await expect(lessonRow).toHaveCount(1);
    // Element UI's fixed actions are duplicated outside the main body.
    await page.locator('.el-table__fixed-right tbody tr').filter({ hasText: lessonKey }).getByRole('button', { name: /assign to child/i }).click();
    const dialog = page.getByRole('dialog', { name: /assign lesson to child/i });
    await expect(dialog.locator('.assignment-lesson')).toContainText(`${lessonKey} · v${published.lesson_version || published.lessonVersion}`);
    await dialog.locator('.el-select').first().click();
    await dialog.locator('.el-select input').first().fill(childName);
    const learners = await adminApi(page, 'GET', `/course-insights/learners?keyword=${encodeURIComponent(childName)}&limit=20`);
    const learner = learners.learners.find(row => row.childId === childId);
    expect(learner, 'identified isolated child must be returned by the real service').toBeTruthy();
    expect(learner.childName).toBe(childName);
    expect(learners.learners.filter(row => row.childName === childName)).toHaveLength(1);
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: childName }).click();
    const eligibility = await adminApi(page, 'GET', `/lesson-assignments/eligible-devices?childId=${childId}&lessonId=${encodeURIComponent(lessonKey)}&lessonVersion=${published.lesson_version || published.lessonVersion}`);
    const device = eligibility.devices.find(row => row.deviceId === deviceId);
    expect(device).toBeTruthy(); expect(device.availability).toBe('available');
    expect(device.serialNumber).toBeTruthy();
    const deviceRow = dialog.locator('.assignment-devices tbody tr').filter({ has: page.getByText(device.serialNumber, { exact: true }) });
    const creationResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/lesson-assignments'));
    await deviceRow.getByRole('button', { name: /^assign$/i }).click();
    const creation = await creationResponse;
    expect(creation.status()).toBe(201);
    const created = (await creation.json()).data.assignment;
    ownedAssignments.push(created);
    expect(created).toMatchObject({ deviceId, childId, lessonId: lessonKey, state: 'ASSIGNED' });
    const readAssignments = () => adminApi(page, 'GET', `/lesson-monitoring/assignments?deviceId=${deviceId}&limit=20`);
    const active = (await readAssignments()).assignments.find(row => row.assignmentId === created.assignmentId);
    expect(active.lessonId).toBe(nextId);
    await adminApi(page, 'POST', `/lesson-assignment-operations/${active.assignmentId}/cancel`, { expectedAssignmentVersion: active.assignmentVersion, reason: 'CONTENT_REPLACED' });
    // Older versions are absent from the global latest-only generation.
    const rollbackInput = {
      deviceIds: [deviceId], childId, lessonId: lessonKey,
      lessonVersion: Number(source.lesson_version || source.lessonVersion), profile: 'espTft',
      idempotencyKey: require('node:crypto').randomUUID(),
    };
    journal.evidence.targetedRollbackInput = rollbackInput;
    const rollbackDeadline = Date.now() + 90000;
    let retained;
    for (;;) {
      const rollbackResult = await adminApi(page, 'POST', '/lesson-targeted-rollbacks', rollbackInput);
      journal.evidence.targetedRollback = rollbackResult;
      expect(rollbackResult.results).toHaveLength(1);
      retained = rollbackResult.results[0];
      if (retained.retainedRequestId && !ownedRetainedRequests.includes(retained.retainedRequestId)) ownedRetainedRequests.push(retained.retainedRequestId);
      if (retained.assignmentId && !ownedAssignments.some(item => item.assignmentId === retained.assignmentId)) {
        ownedAssignments.push({ assignmentId: retained.assignmentId, deviceId });
      }
      expect(retained.deviceId).toBe(deviceId);
      if (retained.outcome !== 'preparing' || retained.storeState === 'failed') break;
      expect(Date.now(), 'targeted preparation must complete within the journey budget').toBeLessThan(rollbackDeadline);
      await page.waitForTimeout(1000);
    }
    const rollback = { assignmentId: retained.assignmentId, deviceId };
    expect(retained, 'exact retained assignment and device selection receipt are required').toMatchObject({
      deviceId, mode: 'retained', outcome: 'admitted', retainedRequestState: 'CONSUMED',
      storeState: 'bound', assignmentState: 'ASSIGNED', error: null,
      manifestChecksum: source.manifest_checksum || source.manifestChecksum,
    });
    expect(retained.retainedRequestId).toBeTruthy();
    expect(rollback.assignmentId).toBeTruthy();
    const history = await readAssignments();
    expect(history.assignments).toEqual(expect.arrayContaining([
      expect.objectContaining({ assignmentId: active.assignmentId, state: 'CANCELLED', lessonId: nextId }),
      expect.objectContaining({ assignmentId: rollback.assignmentId, state: 'ASSIGNED', lessonId: source.id }),
    ]));
    const quality = await adminApi(page, 'GET', `/course-insights/course-quality?windowDays=30&courseId=${fixture.course.id}`);
    const course = quality.courses.find(row => row.courseId === fixture.course.id);
    expect(course).toMatchObject({ courseKey: fixture.course.course_key || fixture.course.courseKey });
    expect(course.assignments).toBeGreaterThanOrEqual(2);
    await page.goto('/#/course-insights'); await expect(page.getByRole('heading', { name: /learner & quality/i })).toBeVisible();
    await journal.checkpoint(testInfo, 'assignment-rollback-insights', { created, rollback, history, quality });
    journal.assertHappyPath();
  } finally {
    // A lost HTTP response can still leave durable preparation or an assignment.
    // Replay the recorded batch to recover ownership before supported cleanup.
    if (journal.evidence.targetedRollbackInput && ownedRetainedRequests.length === 0) {
      try {
        const recovered = await adminApi(page, 'POST', '/lesson-targeted-rollbacks', journal.evidence.targetedRollbackInput);
        expect(recovered.results).toHaveLength(1);
        const row = recovered.results[0];
        expect(row.deviceId).toBe(assignmentFixture.deviceId);
        expect(row.retainedRequestId, 'uncertain rollback ownership must be recovered').toBeTruthy();
        ownedRetainedRequests.push(row.retainedRequestId);
        if (row.assignmentId) ownedAssignments.push({ assignmentId: row.assignmentId, deviceId: row.deviceId });
        journal.evidence.cleanup.push({ requestId: row.retainedRequestId, status: 'recovered-idempotent-batch' });
      } catch (error) {
        journal.evidence.cleanup.push({ status: 'BLOCKED', batch: journal.evidence.targetedRollbackInput.idempotencyKey, error: error.message });
      }
    }
    for (const requestId of ownedRetainedRequests) {
      try {
        const request = await adminApi(page, 'GET', `/lesson-retained-requests/${requestId}`);
        expect(request.deviceId).toBe(assignmentFixture.deviceId);
        if (request.assignmentId && !ownedAssignments.some(item => item.assignmentId === request.assignmentId)) {
          ownedAssignments.push({ assignmentId: request.assignmentId, deviceId: request.deviceId });
        }
      } catch (error) {
        journal.evidence.cleanup.push({ requestId, status: 'BLOCKED', error: error.message });
      }
    }
    for (const assignment of ownedAssignments) {
      try {
        const rows = await adminApi(page, 'GET', `/lesson-monitoring/assignments?deviceId=${assignment.deviceId}&limit=100`);
        const row = rows.assignments.find(item => item.assignmentId === assignment.assignmentId);
        expect(row, 'owned assignment must remain readable for cleanup').toBeTruthy();
        if (['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED'].includes(row.state)) {
          await adminApi(page, 'POST', `/lesson-assignment-operations/${row.assignmentId}/cancel`, { expectedAssignmentVersion: row.assignmentVersion, reason: 'CONTENT_REPLACED' });
        }
        const after = await adminApi(page, 'GET', `/lesson-monitoring/assignments?deviceId=${assignment.deviceId}&limit=100`);
        const terminal = after.assignments.find(item => item.assignmentId === assignment.assignmentId);
        expect(terminal).toBeTruthy();
        expect(['CANCELLED', 'COMPLETED', 'FAILED']).toContain(terminal.state);
        journal.evidence.cleanup.push({ assignmentId: assignment.assignmentId, state: terminal.state, assignmentVersion: terminal.assignmentVersion, status: 'verified-terminal' });
      } catch (error) {
        journal.evidence.cleanup.push({ assignmentId: assignment.assignmentId, status: 'BLOCKED', error: error.message });
      }
    }
    for (const requestId of ownedRetainedRequests) {
      try {
        const route = `/lesson-retained-requests/${requestId}`;
        const before = await adminApi(page, 'GET', route);
        expect(before.deviceId).toBe(assignmentFixture.deviceId);
        if (['PREPARING', 'MATERIALIZED'].includes(before.state)) {
          await adminApi(page, 'POST', `${route}/cancel`, { requestRevision: Number(before.requestRevision) });
        }
        const after = await adminApi(page, 'GET', route);
        expect(['CANCELLED', 'CONSUMED']).toContain(after.state);
        journal.evidence.cleanup.push({ requestId, state: after.state,
          status: 'backend-request-readback', remoteReleaseVerified: false });
      } catch (error) {
        journal.evidence.cleanup.push({ requestId, status: 'BLOCKED', error: error.message });
      }
    }
    await testInfo.attach('journey-errors-and-readback', { body: JSON.stringify(journal.evidence, null, 2), contentType: 'application/json' });
    expect.soft(journal.evidence.cleanup.filter(item => item.status === 'BLOCKED'), 'task assignment cleanup').toEqual([]);
  }
});
