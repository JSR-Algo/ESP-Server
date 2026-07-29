# Single-Version Lesson SD Visual Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin select one existing background and one existing teaching object for a lesson, apply that pair to every step without creating a new lesson version, and automatically enqueue the refreshed lesson pack for SD synchronization.

**Architecture:** Add one lesson-level authoring command that validates the two published shared-visual versions, replaces both per-step pins transactionally, recomputes the current lesson's profile checksums in place, and requests the existing generation/fanout pipeline. Keep the manifest and firmware contract unchanged; the manager web derives one canonical pair from the first step, submits both values together, and displays the existing SD synchronization state.

**Tech Stack:** NestJS + TypeScript + PostgreSQL + Vitest (`tbot-backend`), Vue 2 + Element UI + Node contract tests (`main/manager-web`), existing lesson asset generation and SD fanout pipeline.

---

## File Map

- Modify `tbot-backend/src/lessons/authoring/lesson-authoring.dto.ts` — request DTO for the complete background/object pair.
- Modify `tbot-backend/src/lessons/authoring/lesson-authoring.controller.ts` — lesson-level `PUT /visuals` endpoint.
- Modify `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts` — atomic pair replacement, checksum refresh, generation request, and new-step inheritance.
- Create `tbot-backend/src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts` — focused backend behavior and failure coverage.
- Modify `tbot-backend/src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts` — HTTP/controller forwarding contract.
- Modify `robot/esp32-server/main/manager-web/src/apis/module/lesson.js` — client method for the lesson-level command and SD retry.
- Create `robot/esp32-server/main/manager-web/src/components/lesson/lesson-visual-selection.js` — pure canonical-pair and request-state helpers.
- Create `robot/esp32-server/main/manager-web/scripts/check-lesson-visual-selection.cjs` — fast unit contract for the helpers.
- Modify `robot/esp32-server/main/manager-web/src/views/LessonEditor.vue` — lesson-level selectors, immediate save, preview refresh, and sync polling.
- Modify `robot/esp32-server/main/manager-web/src/components/lesson/LessonSdSyncStatus.vue` — retry action for pending/failed synchronization.
- Modify `robot/esp32-server/main/manager-web/src/views/CourseLessons.vue` — remove the multi-version action and version column from the normal admin workflow.
- Modify `robot/esp32-server/main/manager-web/src/i18n/en.js` and `robot/esp32-server/main/manager-web/src/i18n/vi.js` — selector and synchronization copy.
- Modify `robot/esp32-server/main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs` — UI wiring regression checks.
- Modify `robot/docs/TEST_MATRIX.md` — record the new backend, browser, and live-SD proof rows.

### Task 1: Backend lesson-level visual command

**Files:**
- Create: `tbot-backend/src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`

- [ ] **Step 1: Write the failing service tests**

Create a Vitest harness with a transactional fake client and add these focused cases:

```ts
describe('LessonAuthoringService.applyLessonVisuals', () => {
  it('replaces the complete pair on every step and keeps lesson_version unchanged', async () => {
    const { service, statements, generationRepository } = makeHarness({
      lesson: publishedLesson({ lesson_version: 7 }),
      steps: [{ step_key: 's1' }, { step_key: 's2' }],
      versions: [publishedSceneVersion(BACKGROUND_ID), publishedObjectVersion(OBJECT_ID)],
    });

    const result = await service.applyLessonVisuals(
      LESSON_ID,
      { backgroundAssetVersionId: BACKGROUND_ID, objectAssetVersionId: OBJECT_ID },
      ADMIN,
      IP,
    );

    expect(result).toMatchObject({
      lessonId: LESSON_ID,
      lessonVersion: 7,
      backgroundAssetVersionId: BACKGROUND_ID,
      objectAssetVersionId: OBJECT_ID,
      syncState: 'pending',
    });
    expect(statements.filter((entry) => /INSERT INTO lesson_visual_refs/.test(entry.sql))).toHaveLength(1);
    expect(statements.find((entry) => /INSERT INTO lesson_visual_refs/.test(entry.sql))?.params)
      .toEqual([LESSON_ID, BACKGROUND_ID, OBJECT_ID]);
    expect(statements.some((entry) => /UPDATE lessons SET lesson_version/.test(entry.sql))).toBe(false);
    expect(generationRepository.requestRebuild).toHaveBeenCalledWith(
      expect.anything(),
      { reason: 'lesson.visuals.update', sourceLessonId: LESSON_ID },
    );
  });

  it.each([
    [draftSceneVersion(BACKGROUND_ID), publishedObjectVersion(OBJECT_ID), 'published'],
    [publishedObjectVersion(BACKGROUND_ID), publishedObjectVersion(OBJECT_ID), 'scene'],
    [publishedSceneVersion(BACKGROUND_ID), mobileObjectVersion(OBJECT_ID), 'espTft'],
  ])('rejects an invalid pair without changing refs', async (background, object, expected) => {
    const { service, statements } = makeHarness({ versions: [background, object] });
    await expect(service.applyLessonVisuals(
      LESSON_ID,
      { backgroundAssetVersionId: BACKGROUND_ID, objectAssetVersionId: OBJECT_ID },
      ADMIN,
      IP,
    )).rejects.toThrow(expected);
    expect(statements.some((entry) => /INSERT INTO lesson_visual_refs/.test(entry.sql))).toBe(false);
  });

  it('rolls back the whole pair when checksum persistence fails', async () => {
    const { service, statements } = makeHarness({ failOn: /UPDATE asset_bundles/ });
    await expect(service.applyLessonVisuals(
      LESSON_ID,
      { backgroundAssetVersionId: BACKGROUND_ID, objectAssetVersionId: OBJECT_ID },
      ADMIN,
      IP,
    )).rejects.toThrow('bundle checksum failed');
    expect(statements.some((entry) => entry.sql === 'ROLLBACK')).toBe(true);
    expect(statements.some((entry) => entry.sql === 'COMMIT')).toBe(false);
  });

  it('is idempotent for the same pair', async () => {
    const { service, generationRepository } = makeHarness({ currentPairMatches: true });
    const result = await service.applyLessonVisuals(
      LESSON_ID,
      { backgroundAssetVersionId: BACKGROUND_ID, objectAssetVersionId: OBJECT_ID },
      ADMIN,
      IP,
    );
    expect(result.syncState).toBe('current');
    expect(generationRepository.requestRebuild).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts
```

Expected: FAIL because `LessonAuthoringService.applyLessonVisuals` does not exist.

- [ ] **Step 3: Add queryable manifest-loading helpers**

Refactor the existing profile loader so the new command can calculate checksums from uncommitted visual refs on the same client:

```ts
type LessonQueryable = Pick<Pool, 'query'> | Pick<PoolClient, 'query'>;

private async loadStepsWith(
  queryable: LessonQueryable,
  lessonId: string,
): Promise<StepRow[]> {
  const steps = await queryable.query<StepRow>(
    `SELECT step_key, step_index, step_type, entrance, robot_state, pose, expression, phase,
            prompt, subject, helper_text, l1_transfer_hint, choices, step_body
       FROM lesson_steps
      WHERE lesson_id = $1
      ORDER BY step_index`,
    [lessonId],
  );
  return steps.rows;
}

private async loadForProfileWith(
  queryable: LessonQueryable,
  lesson: LessonRowFull,
  profile: string,
): Promise<{ steps: StepRow[]; assets: AssetRow[]; visualRefs: SharedVisualRefRow[] }> {
  const bundle = await queryable.query<{ id: string }>(
    `SELECT id FROM asset_bundles WHERE lesson_id=$1 AND lesson_version=$2 AND profile=$3`,
    [lesson.id, lesson.lesson_version, profile],
  );
  if ((bundle.rowCount ?? 0) === 0) {
    throw lessonError(
      ErrorCode.ASSET_PROFILE_UNAVAILABLE,
      `No ${profile} asset bundle for this lesson`,
      422,
      false,
      { profile },
    );
  }
  const assets = await queryable.query<AssetRow>(
    `SELECT asset_key, layer, role, path, sha256, is_critical, media_type, bytes, width, height
       FROM assets WHERE bundle_id=$1`,
    [bundle.rows[0].id],
  );
  const visualRefs = await queryable.query<SharedVisualRefRow>(
    `SELECT r.step_key, r.slot, a.asset_key, v.version, v.profile, v.publication_state,
            v.storage_path, v.sha256, v.mime_type, v.bytes, v.width, v.height,
            v.compatibility_metadata
       FROM lesson_visual_refs r
       JOIN shared_visual_asset_versions v ON v.id=r.asset_version_id
       JOIN shared_visual_assets a ON a.id=v.asset_id
      WHERE r.lesson_id=$1 AND v.profile=$2
      ORDER BY r.step_key, r.slot, a.asset_key`,
    [lesson.id, profile],
  );
  return {
    steps: await this.loadStepsWith(queryable, lesson.id),
    assets: assets.rows,
    visualRefs: visualRefs.rows,
  };
}

private loadForProfile(lesson: LessonRowFull, profile: string) {
  return this.loadForProfileWith(this.pool, lesson, profile);
}
```

Keep the SQL column lists byte-for-byte equivalent to the current loader so preview and publish behavior do not change.

- [ ] **Step 4: Implement the minimal lesson-level command**

Add the input/result types and method to `LessonAuthoringService`:

```ts
export type ApplyLessonVisualsInput = {
  backgroundAssetVersionId: string;
  objectAssetVersionId: string;
};

async applyLessonVisuals(
  lessonId: string,
  input: ApplyLessonVisualsInput,
  admin: AdminPrincipal,
  ip: string,
) {
  const client = await this.pool.connect();
  try {
    await client.query('BEGIN');
    const lessonResult = await client.query<LessonRowFull>(
      `SELECT l.*, c.course_key
         FROM lessons l
         JOIN courses c ON c.id = l.course_id
        WHERE l.id = $1
        FOR UPDATE OF l`,
      [lessonId],
    );
    if ((lessonResult.rowCount ?? 0) !== 1) {
      throw lessonError(ErrorCode.LESSON_NOT_FOUND, `Lesson not found: ${lessonId}`, 404, false);
    }
    const lesson = lessonResult.rows[0];
    const steps = await client.query<{ step_key: string }>(
      `SELECT step_key FROM lesson_steps WHERE lesson_id=$1 ORDER BY step_index FOR UPDATE`,
      [lessonId],
    );
    if (!steps.rows.length) {
      throw lessonError(ErrorCode.STEP_NOT_FOUND, 'Add the first lesson step before choosing visuals', 409, false);
    }

    const selected = await client.query<{
      id: string;
      profile: string;
      publication_state: string;
      category: string;
    }>(
      `SELECT v.id, v.profile, v.publication_state, a.category
         FROM shared_visual_asset_versions v
         JOIN shared_visual_assets a ON a.id=v.asset_id
        WHERE v.id = ANY($1::uuid[])
        FOR SHARE OF v,a`,
      [[input.backgroundAssetVersionId, input.objectAssetVersionId]],
    );
    const background = selected.rows.find((row) => row.id === input.backgroundAssetVersionId);
    const object = selected.rows.find((row) => row.id === input.objectAssetVersionId);
    this.assertLessonVisualSelection(background, 'scene', 'background');
    this.assertLessonVisualSelection(object, 'teachingObject', 'object');

    const current = await client.query<{ step_key: string; slot: string; asset_version_id: string }>(
      `SELECT step_key, slot, asset_version_id
         FROM lesson_visual_refs
        WHERE lesson_id=$1 AND slot IN ('backgroundScene','teachingObject')
        ORDER BY step_key, slot
        FOR UPDATE`,
      [lessonId],
    );
    const alreadyCurrent = steps.rows.every((step) =>
      current.rows.some((ref) => ref.step_key === step.step_key && ref.slot === 'backgroundScene'
        && ref.asset_version_id === input.backgroundAssetVersionId)
      && current.rows.some((ref) => ref.step_key === step.step_key && ref.slot === 'teachingObject'
        && ref.asset_version_id === input.objectAssetVersionId));
    if (alreadyCurrent) {
      await client.query('COMMIT');
      return {
        lessonId,
        lessonVersion: Number(lesson.lesson_version),
        ...input,
        syncState: 'current' as const,
      };
    }

    await client.query(
      `INSERT INTO lesson_visual_refs(lesson_id,step_key,slot,asset_version_id)
       SELECT $1, s.step_key, pair.slot, pair.asset_version_id
         FROM lesson_steps s
         CROSS JOIN (VALUES
           ('backgroundScene'::text, $2::uuid),
           ('teachingObject'::text, $3::uuid)
         ) AS pair(slot, asset_version_id)
        WHERE s.lesson_id=$1
       ON CONFLICT (lesson_id,step_key,slot)
       DO UPDATE SET asset_version_id=EXCLUDED.asset_version_id, updated_at=NOW()`,
      [lessonId, input.backgroundAssetVersionId, input.objectAssetVersionId],
    );

    const profiles = await client.query<{ profile: string }>(
      `SELECT DISTINCT profile FROM asset_bundles WHERE lesson_id=$1 AND lesson_version=$2 ORDER BY profile`,
      [lessonId, lesson.lesson_version],
    );
    const profileChecksums: Record<string, string> = {};
    const lessonRow = this.asLessonRow(lesson);
    const { renderMap, completionClassFor } = await this.renderContextFor(lessonRow.manifest_version);
    for (const { profile } of profiles.rows) {
      const loaded = await this.loadForProfileWith(client, lesson, profile);
      const validationAssets = [
        ...loaded.assets,
        ...sharedVisualRefsAsAssets(renderableSharedVisualRefs(profile, loaded.visualRefs)),
      ];
      validateResolvedLesson(lessonRow, profile, loaded.steps, validationAssets, renderMap);
      assertAuthoringExtraRules(loaded.steps, validationAssets);
      buildManifest(lessonRow, profile, loaded.steps, loaded.assets, completionClassFor, loaded.visualRefs);
      this.assertEspTftPublishBudget(lesson, profile, loaded.steps, validationAssets, loaded.visualRefs);
      profileChecksums[profile] = computeManifestChecksum(
        buildIdentityProjection(lessonRow, profile, loaded.steps, loaded.assets, loaded.visualRefs),
      );
      await client.query(
        `UPDATE asset_bundles SET manifest_checksum=$4
          WHERE lesson_id=$1 AND lesson_version=$2 AND profile=$3`,
        [lessonId, lesson.lesson_version, profile, profileChecksums[profile]],
      );
    }
    const fallbackProfile = profiles.rows.some((row) => row.profile === 'espTft')
      ? 'espTft'
      : profiles.rows[0].profile;
    const checksum = profileChecksums[fallbackProfile];
    await client.query(
      `UPDATE lessons SET manifest_checksum=$2, updated_at=NOW() WHERE id=$1`,
      [lessonId, checksum],
    );
    if (profileChecksums.espTft && lesson.status === 'published') {
      await this.generationRepository.requestRebuild(client, {
        reason: 'lesson.visuals.update',
        sourceLessonId: lessonId,
      });
      if (process.env.LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED === 'true') {
        await this.assetSync.enqueuePublished(client, {
          lessonId,
          lessonKey: lesson.lesson_key,
          lessonVersion: Number(lesson.lesson_version),
          profile: 'espTft',
          manifestChecksum: profileChecksums.espTft,
        });
      }
    }
    await this.auditTx(client, admin, ip, 'lesson.visuals.update', 'lesson', lessonId, {
      lesson_version: lesson.lesson_version,
      background_asset_version_id: input.backgroundAssetVersionId,
      object_asset_version_id: input.objectAssetVersionId,
      profile_checksums: profileChecksums,
    });
    await client.query('COMMIT');
    return {
      lessonId,
      lessonVersion: Number(lesson.lesson_version),
      ...input,
      checksum,
      profileChecksums,
      syncState: lesson.status === 'published' ? 'pending' as const : 'not-published' as const,
    };
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}
```

Add `assertLessonVisualSelection` as a private method that rejects a missing row, non-`espTft` profile, non-`published` publication state, or wrong category with the existing `ASSET_NOT_FOUND`/`VALIDATION_ERROR` envelopes.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts
```

Expected: PASS with all lesson-level pair tests green.

- [ ] **Step 6: Run publish/preview regression tests**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run \
  src/lessons/authoring/lesson-authoring.publish-happy-path.spec.ts \
  src/lessons/authoring/lesson-authoring.authored-checksum-roundtrip.spec.ts \
  src/lessons/lesson-manifest.shared-assets.spec.ts
```

Expected: PASS; the loader refactor does not alter existing manifests or publish checksums.

- [ ] **Step 7: Commit the backend service slice**

```bash
git add src/lessons/authoring/lesson-authoring.service.ts \
  src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts
git commit -m "feat(lessons): apply one visual pair across a lesson"
```

### Task 2: DTO and HTTP endpoint

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.dto.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts`

- [ ] **Step 1: Write the failing controller test**

```ts
it('applies the complete lesson visual pair without creating a new version', async () => {
  const body = {
    backgroundAssetVersionId: '10000000-0000-4000-8000-000000000001',
    objectAssetVersionId: '20000000-0000-4000-8000-000000000002',
  };
  await controller.applyLessonVisuals('lesson-1', body, req());
  expect(svc.applyLessonVisuals).toHaveBeenCalledWith(
    'lesson-1', body, ADMIN, expect.any(String),
  );
  expect(svc.createNextVersion).not.toHaveBeenCalled();
});
```

Add these validation-pipe assertions next to the forwarding test:

```ts
await request(app.getHttpServer())
  .put('/v1/admin/lessons/lesson-1/visuals')
  .send({ objectAssetVersionId: OBJECT_ID })
  .expect(400);
await request(app.getHttpServer())
  .put('/v1/admin/lessons/lesson-1/visuals')
  .send({ backgroundAssetVersionId: 'latest', objectAssetVersionId: OBJECT_ID })
  .expect(400);
expect(svc.applyLessonVisuals).not.toHaveBeenCalled();
```

- [ ] **Step 2: Run the controller test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts
```

Expected: FAIL because the DTO and controller method do not exist.

- [ ] **Step 3: Add the request DTO**

```ts
export class ApplyLessonVisualsDto {
  @IsUUID()
  @ApiProperty({ format: 'uuid' })
  backgroundAssetVersionId!: string;

  @IsUUID()
  @ApiProperty({ format: 'uuid' })
  objectAssetVersionId!: string;
}
```

- [ ] **Step 4: Add the controller route**

```ts
@Put('lessons/:lessonId/visuals')
@ApiOperation({ summary: 'Replace the current lesson background/object pair and refresh SD assets' })
@ApiBody({ type: ApplyLessonVisualsDto })
async applyLessonVisuals(
  @Param('lessonId') lessonId: string,
  @Body() body: ApplyLessonVisualsDto,
  @Req() req: AdminAuthedRequest,
) {
  return {
    data: await this.svc.applyLessonVisuals(lessonId, body, req.admin, clientIp(req)),
  };
}
```

Import `Put` and `ApplyLessonVisualsDto` in the controller.

- [ ] **Step 5: Run the controller tests and verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts
```

Expected: PASS.

- [ ] **Step 6: Commit the HTTP slice**

```bash
git add src/lessons/authoring/lesson-authoring.dto.ts \
  src/lessons/authoring/lesson-authoring.controller.ts \
  src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts
git commit -m "feat(lessons): expose lesson-level visual update"
```

### Task 3: New-step visual inheritance

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.coverage.spec.ts`

- [ ] **Step 1: Write the failing inheritance test**

```ts
it('copies the canonical lesson visual pair when adding a later step', async () => {
  const { pool, statements } = makePool([
    [/FROM lessons l JOIN courses/, draftLesson],
    [/SELECT COUNT\(\*\)::text AS c/, () => ({ rowCount: 1, rows: [{ c: '2' }] })],
    [/INSERT INTO lesson_steps/, () => ({ rowCount: 1, rows: [{ id: 'step-3', step_key: 's3' }] })],
    [/INSERT INTO lesson_visual_refs/, () => ({ rowCount: 2, rows: [] })],
  ]);

  await svcOf(pool).createStep('l1', validStepInput(), ADMIN, IP);

  const inherit = statements.find((entry) =>
    /INSERT INTO lesson_visual_refs/.test(entry.sql)
    && /ORDER BY source_step.step_index/.test(entry.sql));
  expect(inherit?.params).toEqual(['l1', 's3']);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.service.coverage.spec.ts -t "copies the canonical lesson visual pair"
```

Expected: FAIL because `createStep` does not insert inherited refs.

- [ ] **Step 3: Insert the canonical pair after step creation**

Run the step insertion and inheritance in the same client transaction. After inserting the new step, execute:

```ts
await client.query(
  `INSERT INTO lesson_visual_refs(lesson_id,step_key,slot,asset_version_id)
   SELECT $1, $2, refs.slot, refs.asset_version_id
     FROM lesson_steps source_step
     JOIN lesson_visual_refs refs
       ON refs.lesson_id=source_step.lesson_id
      AND refs.step_key=source_step.step_key
      AND refs.slot IN ('backgroundScene','teachingObject')
    WHERE source_step.lesson_id=$1
      AND source_step.step_key<>$2
    ORDER BY source_step.step_index, refs.slot
    LIMIT 2
   ON CONFLICT (lesson_id,step_key,slot) DO NOTHING`,
  [lessonId, stepKey],
);
```

Keep the existing draft guard and audit inside that transaction. The first step inserts zero inherited rows by design.

- [ ] **Step 4: Run create/update step regression coverage**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/authoring/lesson-authoring.service.coverage.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit inheritance**

```bash
git add src/lessons/authoring/lesson-authoring.service.ts \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts
git commit -m "feat(lessons): inherit lesson visuals on new steps"
```

### Task 4: Manager API and pure selection state

**Files:**
- Modify: `robot/esp32-server/main/manager-web/src/apis/module/lesson.js`
- Create: `robot/esp32-server/main/manager-web/src/components/lesson/lesson-visual-selection.js`
- Create: `robot/esp32-server/main/manager-web/scripts/check-lesson-visual-selection.cjs`
- Modify: `robot/esp32-server/main/manager-web/package.json`

- [ ] **Step 1: Write the failing pure helper contract**

```js
const assert = require('assert');
const {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
} = require('../src/components/lesson/lesson-visual-selection.js');

const steps = [{
  stepKey: 's1',
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'bg-v1', assetKey: 'scene.farm' },
    { slot: 'teachingObject', assetVersionId: 'obj-v1', assetKey: 'object.barn' },
  ],
}, {
  stepKey: 's2',
  visualRefs: [],
}];

assert.deepStrictEqual(canonicalLessonVisualPair(steps), {
  backgroundAssetVersionId: 'bg-v1',
  backgroundAssetKey: 'scene.farm',
  objectAssetVersionId: 'obj-v1',
  objectAssetKey: 'object.barn',
});
assert.deepStrictEqual(buildLessonVisualRequest(
  canonicalLessonVisualPair(steps),
  { objectAssetVersionId: 'obj-v2', objectAssetKey: 'object.seed' },
), {
  backgroundAssetVersionId: 'bg-v1',
  objectAssetVersionId: 'obj-v2',
});
assert.throws(() => buildLessonVisualRequest({}, {}), /complete background and object pair/);
```

- [ ] **Step 2: Run the helper contract and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node scripts/check-lesson-visual-selection.cjs
```

Expected: FAIL because the helper module does not exist.

- [ ] **Step 3: Implement the pure helper module**

```js
function visualRef(step, slot) {
  return step && Array.isArray(step.visualRefs)
    ? step.visualRefs.find((ref) => ref.slot === slot)
    : null;
}

function canonicalLessonVisualPair(steps) {
  const first = Array.isArray(steps) && steps.length ? steps[0] : null;
  const background = visualRef(first, 'backgroundScene');
  const object = visualRef(first, 'teachingObject');
  return {
    backgroundAssetVersionId: background ? background.assetVersionId : '',
    backgroundAssetKey: background ? background.assetKey : '',
    objectAssetVersionId: object ? object.assetVersionId : '',
    objectAssetKey: object ? object.assetKey : '',
  };
}

function buildLessonVisualRequest(current, patch) {
  const merged = { ...current, ...patch };
  if (!merged.backgroundAssetVersionId || !merged.objectAssetVersionId) {
    throw new Error('A complete background and object pair is required');
  }
  return {
    backgroundAssetVersionId: merged.backgroundAssetVersionId,
    objectAssetVersionId: merged.objectAssetVersionId,
  };
}

module.exports = { canonicalLessonVisualPair, buildLessonVisualRequest };
```

- [ ] **Step 4: Add manager API methods**

```js
applyLessonVisuals(lessonId, data, onSuccess, onError) {
  nestRequest({
    url: `${getNestUrl()}/lessons/${lessonId}/visuals`,
    method: 'PUT',
    data,
    onSuccess,
    onError,
  });
},

retrySdSync(lessonId, onSuccess, onError) {
  nestRequest({
    url: `${getNestUrl()}/lessons/${lessonId}/sd-sync/retry`,
    method: 'POST',
    data: {},
    onSuccess,
    onError,
  });
},
```

Add `test:lesson-visual-selection` to `package.json` using `node scripts/check-lesson-visual-selection.cjs`.

- [ ] **Step 5: Run the helper and API contract checks**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-editor-ui
```

Expected: helper PASS; existing editor contracts remain PASS before UI rewiring.

- [ ] **Step 6: Commit the client-state slice**

```bash
git add main/manager-web/src/apis/module/lesson.js \
  main/manager-web/src/components/lesson/lesson-visual-selection.js \
  main/manager-web/scripts/check-lesson-visual-selection.cjs \
  main/manager-web/package.json
git commit -m "feat(admin): add lesson visual update client"
```

### Task 5: Lesson-level selectors and automatic save

**Files:**
- Modify: `robot/esp32-server/main/manager-web/src/views/LessonEditor.vue`
- Modify: `robot/esp32-server/main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/en.js`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/vi.js`

- [ ] **Step 1: Add failing UI contract assertions**

Extend the contract script to require:

```js
expectContains('src/views/LessonEditor.vue', 'data-testid="lesson-background-selector"', 'lesson-level background selector');
expectContains('src/views/LessonEditor.vue', 'data-testid="lesson-object-selector"', 'lesson-level object selector');
expectContains('src/views/LessonEditor.vue', 'applyLessonVisualSelection', 'complete-pair save handler');
expectContains('src/views/LessonEditor.vue', 'Api.lesson.applyLessonVisuals', 'lesson-level API call');
expectContains('src/views/LessonEditor.vue', 'canonicalLessonVisualPair', 'step-independent canonical selection');
expectNotContains('src/views/LessonEditor.vue', "this.selectedStep.stepKey, 'backgroundScene'", 'background must not save per step');
expectNotContains('src/views/LessonEditor.vue', "this.selectedStep.stepKey, 'teachingObject'", 'object must not save per step');
```

- [ ] **Step 2: Run the UI contract and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-editor-ui
```

Expected: FAIL on missing lesson-level selectors/API call.

- [ ] **Step 3: Add lesson-level state and computed selection**

Import the pure helpers and add state:

```js
import {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
} from '@/components/lesson/lesson-visual-selection';

data() {
  return {
    // existing fields
    savingLessonVisuals: false,
    pendingLessonVisualPair: null,
  };
},

computed: {
  lessonVisualPair() {
    return this.pendingLessonVisualPair || canonicalLessonVisualPair(this.steps);
  },
  selectedBackgroundKey() {
    return this.lessonVisualPair.backgroundAssetKey;
  },
  pickedObjectKey() {
    return this.lessonVisualPair.objectAssetKey;
  },
}
```

Replace the mutable `selectedBackgroundKey` and `pickedObjectKey` data fields with these computed values.

- [ ] **Step 4: Replace both picker click handlers with one complete-pair save path**

```js
applyLessonVisualSelection(patch) {
  if (this.savingLessonVisuals || !this.steps.length) return;
  let request;
  try {
    request = buildLessonVisualRequest(this.lessonVisualPair, patch);
  } catch (error) {
    this.$message.warning(this.$t('lesson.visualPairRequired'));
    return;
  }
  const background = this.backgroundLibrary.find((item) => item.versionId === request.backgroundAssetVersionId);
  const object = this.objectLibrary.find((item) => item.versionId === request.objectAssetVersionId);
  this.savingLessonVisuals = true;
  this.pendingLessonVisualPair = {
    ...this.lessonVisualPair,
    ...patch,
    backgroundAssetKey: background ? background.assetKey : this.lessonVisualPair.backgroundAssetKey,
    objectAssetKey: object ? object.assetKey : this.lessonVisualPair.objectAssetKey,
  };
  Api.lesson.applyLessonVisuals(this.lessonId, request, () => {
    this.savingLessonVisuals = false;
    this.previewManifest = null;
    this.fetchSteps({
      onSuccess: () => {
        this.pendingLessonVisualPair = null;
        this.doPreview();
        this.loadLessonAssetGenerationStatus({ silent: true });
      },
    });
    this.$message.success(this.$t('lesson.visualPairSaved'));
  }, (message) => {
    this.savingLessonVisuals = false;
    this.pendingLessonVisualPair = null;
    this.$message.error(message);
  });
},
selectBackground(background) {
  this.applyLessonVisualSelection({
    backgroundAssetVersionId: background.versionId,
    backgroundAssetKey: background.assetKey,
  });
},
selectTeachObject(object) {
  this.applyLessonVisualSelection({
    objectAssetVersionId: object.versionId,
    objectAssetKey: object.assetKey,
  });
},
```

Remove both calls to `Api.lesson.setVisualRef`. Disable both selector grids when `savingLessonVisuals`, when the lesson has no steps, or when either library item lacks a `versionId`.

- [ ] **Step 5: Mark up the lesson-level panel**

Add `data-testid="lesson-background-selector"` and `data-testid="lesson-object-selector"`, replace copy that says the choice applies to the selected step with localized copy that says it applies to the whole lesson, and show an Element loading overlay while saving.

- [ ] **Step 6: Add localized copy**

Add these exact keys in English and Vietnamese:

```js
'lesson.visualPairTitle': 'Lesson background and object',
'lesson.visualPairWholeLesson': 'Applies automatically to every step in this lesson.',
'lesson.visualPairRequired': 'Choose both a background and an object before saving.',
'lesson.visualPairSaved': 'Lesson visuals saved. SD synchronization has started.',
'lesson.visualPairNoSteps': 'Add the first step before choosing lesson visuals.',
```

```js
'lesson.visualPairTitle': 'Background và object của bài học',
'lesson.visualPairWholeLesson': 'Tự động áp dụng cho tất cả step trong bài học.',
'lesson.visualPairRequired': 'Cần chọn đủ background và object trước khi lưu.',
'lesson.visualPairSaved': 'Đã lưu hình ảnh bài học và bắt đầu đồng bộ xuống SD.',
'lesson.visualPairNoSteps': 'Hãy tạo step đầu tiên trước khi chọn hình ảnh bài học.',
```

- [ ] **Step 7: Run UI contracts and build**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-editor-ui
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit the lesson editor slice**

```bash
git add main/manager-web/src/views/LessonEditor.vue \
  main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs \
  main/manager-web/src/i18n/en.js \
  main/manager-web/src/i18n/vi.js
git commit -m "feat(admin): save visuals for the whole lesson"
```

### Task 6: SD retry and single-version admin presentation

**Files:**
- Modify: `robot/esp32-server/main/manager-web/src/components/lesson/LessonSdSyncStatus.vue`
- Modify: `robot/esp32-server/main/manager-web/src/views/LessonEditor.vue`
- Modify: `robot/esp32-server/main/manager-web/src/views/CourseLessons.vue`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/en.js`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/vi.js`
- Modify: `robot/esp32-server/main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Add failing retry and single-version UI assertions**

```js
expectContains('src/components/lesson/LessonSdSyncStatus.vue', '@click="$emit(\'retry\')"', 'SD retry event');
expectContains('src/views/LessonEditor.vue', '@retry="retryLessonSdSync"', 'editor SD retry wiring');
expectContains('src/views/LessonEditor.vue', 'Api.lesson.retrySdSync', 'SD retry API call');
expectNotContains('src/views/CourseLessons.vue', 'createNextVersion(scope.row)', 'multi-version action hidden');
expectNotContains('src/views/CourseLessons.vue', 'prop="lessonVersion"', 'version column hidden');
expectNotContains('src/views/LessonEditor.vue', 'v{{ lesson.lessonVersion }}', 'version badge hidden');
```

- [ ] **Step 2: Run the UI contract and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-editor-ui
```

Expected: FAIL on retry wiring and multi-version presentation.

- [ ] **Step 3: Add the retry button and event**

In `LessonSdSyncStatus.vue`, render the button for `Failed`, `Retrying`, `GenerationMismatch`, or `RollingOut`:

```vue
<el-button
  v-if="canRetry"
  size="mini"
  type="warning"
  plain
  @click="$emit('retry')"
>
  {{ $t('lesson.sdSyncRetryAction') }}
</el-button>
```

```js
canRetry() {
  return ['Failed', 'Retrying', 'GenerationMismatch', 'RollingOut'].includes(this.stateKey);
}
```

- [ ] **Step 4: Wire retry in the editor**

```js
retryLessonSdSync() {
  if (this.lessonAssetGenerationLoading) return;
  this.lessonAssetGenerationLoading = true;
  Api.lesson.retrySdSync(this.lessonId, () => {
    this.lessonAssetGenerationLoading = false;
    this.loadLessonAssetGenerationStatus({ silent: true });
    this.$message.success(this.$t('lesson.sdSyncRetryQueued'));
  }, (message) => {
    this.lessonAssetGenerationLoading = false;
    this.$message.error(message);
  });
},
```

Bind `@retry="retryLessonSdSync"` on `LessonSdSyncStatus`.

- [ ] **Step 5: Remove multi-version controls from the normal admin workflow**

Delete the `lessonVersion` table column and the `createNextVersion` action/button from `CourseLessons.vue`. Remove the version badge from `LessonEditor.vue`. Do not delete the backend compatibility endpoint in this feature.

- [ ] **Step 6: Add retry translations**

```js
'lesson.sdSyncRetryAction': 'Retry synchronization',
'lesson.sdSyncRetryQueued': 'SD synchronization retry queued.',
```

```js
'lesson.sdSyncRetryAction': 'Thử đồng bộ lại',
'lesson.sdSyncRetryQueued': 'Đã đưa yêu cầu đồng bộ SD vào hàng đợi.',
```

- [ ] **Step 7: Run manager verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-editor-ui
npm run test:lesson-sd-sync-ui
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit retry and presentation changes**

```bash
git add main/manager-web/src/components/lesson/LessonSdSyncStatus.vue \
  main/manager-web/src/views/LessonEditor.vue \
  main/manager-web/src/views/CourseLessons.vue \
  main/manager-web/src/i18n/en.js \
  main/manager-web/src/i18n/vi.js \
  main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs
git commit -m "feat(admin): retry SD sync without lesson versions"
```

### Task 7: Cross-layer verification and evidence

**Files:**
- Modify: `robot/docs/TEST_MATRIX.md`

- [ ] **Step 1: Run the focused backend suite**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run \
  src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts \
  src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts \
  src/lessons/authoring/lesson-authoring.publish-happy-path.spec.ts \
  src/lessons/authoring/lesson-authoring.authored-checksum-roundtrip.spec.ts \
  src/lessons/lesson-asset-generation-build.spec.ts \
  src/lessons/lesson-asset-sync.controller.spec.ts
```

Expected: PASS with zero failed tests.

- [ ] **Step 2: Run backend type/build validation**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm run build
```

Expected: exit 0.

- [ ] **Step 3: Run the complete manager checks**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-editor-ui
npm run test:lesson-sd-sync-ui
npm run build
```

Expected: every command exits 0.

- [ ] **Step 4: Run browser authoring coverage when its environment is available**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npx playwright test e2e/lesson-studio/authoring.spec.js --project=chromium
```

Expected: the lesson authoring journey passes, including selecting a background/object pair and observing the saved state after reload. If the external E2E environment is unavailable, record the exact blocker and do not claim browser proof.

- [ ] **Step 5: Record test-matrix evidence**

Add a dated section with these rows:

```markdown
| Requirement | Software proof | Live proof | Status |
|---|---|---|---|
| One pair applies to all steps without incrementing lesson version | focused service + controller Vitest | inspect admin/API response | software PASS / live pending |
| New steps inherit the canonical pair | service coverage | create step then inspect manifest | software PASS / live pending |
| Published change requests generation and SD fanout | generation/sync repository assertions | connected robot receives refreshed pack | software PASS / live pending |
| Pair activation is atomic | rollback test | failed-object fault injection keeps prior pair | software PASS / live pending |
```

- [ ] **Step 6: Run repository hygiene checks**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
git diff --check
git status --short
```

Expected: no whitespace errors; only intended task files are modified. The pre-existing untracked `.superpowers/` directory remains untouched.

- [ ] **Step 7: Preserve the evidence update**

`robot/docs/TEST_MATRIX.md` belongs to the workspace harness outside the nested
`esp32-server` Git repository. Keep the edit in the workspace and report it
separately; do not stage it in either `esp32-server` or `tbot-backend`.

## Completion Checklist

- [ ] The admin submits a complete background/object pair once per lesson.
- [ ] All existing steps receive identical pins in one transaction.
- [ ] New steps inherit the canonical pair.
- [ ] `lesson_version` is unchanged and the normal admin UI exposes no new-version action.
- [ ] Published lessons request generation/fanout automatically after the checksum changes.
- [ ] Preview reloads from authoritative steps after save.
- [ ] Pending/failed SD synchronization is visible and retryable.
- [ ] Backend focused tests, manager contracts, and builds pass.
- [ ] Live device proof is reported honestly as passed or pending with evidence.
