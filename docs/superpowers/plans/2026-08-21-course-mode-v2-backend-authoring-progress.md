# Course Mode V2 Backend Authoring and Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author, validate, publish, serve, and measure `courseCompanion.v2` lessons without exposing raw child conversation data or altering V1 TVideo behavior.

**Architecture:** Add a separate V2 domain under `src/lessons/course-mode`, persist the approved contract as validated JSONB tied to a lesson version, project it into the served manifest only behind a publish flag, and extend progress ingest with an allowlisted `word_evidence_recorded` event. Parent projections consume evidence levels, not transcripts.

**Tech Stack:** NestJS, TypeScript, PostgreSQL migration 122, Vitest, existing lesson manifest/authoring/progress services, OpenAPI tooling.

---

## File Map

- Create `src/lessons/course-mode/course-mode.types.ts`.
- Create `src/lessons/course-mode/course-mode.contract.ts` and tests.
- Create `src/lessons/course-mode/course-mode.repository.ts` and tests.
- Create migration `src/database/migrations/122_course_mode_v2.sql` and down migration.
- Modify lesson authoring DTO/logic/service/controller for V2 contract storage.
- Modify lesson manifest resolver/identity projection for V2.
- Modify lesson event ingest logic/service and parent progress projection.
- Modify `src/lessons/lesson-rollout.config.ts` and its spec to add
  `COURSE_MODE_V2_PUBLISH_ENABLED=false`.

### Task 1: Add V2 Type and Validator Domain

**Files:**
- Create: `src/lessons/course-mode/course-mode.types.ts`
- Create: `src/lessons/course-mode/course-mode.contract.ts`
- Create: `src/lessons/course-mode/course-mode.contract.spec.ts`

- [ ] **Step 1: Write failing contract tests**

Mirror the ESP exact-field rules and shared pilot fixture. Test target count,
unique IDs, transfer/delayed requirements, no answer reveal, approved embodied
intents, no raw servo values, no overclaiming praise, and opening interaction.

```ts
it('rejects an independent activity that reveals the answer', () => {
  const input = validCourseMode();
  input.wordTargets[0].delayedRecallChecks[0].revealsAnswer = true;
  expect(() => parseCourseModeV2(input)).toThrow(/ANSWER_LEAKAGE/);
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/course-mode/course-mode.contract.spec.ts
```

- [ ] **Step 3: Implement types and parser**

```ts
export type CourseEvidenceLevel =
  | 'EXPOSED'
  | 'UNDERSTOOD'
  | 'SUPPORTED_SPEECH'
  | 'INDEPENDENT_RECALL'
  | 'TRANSFERRED'
  | 'MASTERED_TODAY'
  | 'REVIEW_NEEDED';

export interface CourseModeV2 {
  readonly presetId: 'courseCompanion';
  readonly presetVersion: 2;
  readonly ageBand: '3-5';
  readonly durationPolicy: Readonly<{
    targetSeconds: 540;
    softMinimumSeconds: 420;
    softMaximumSeconds: 660;
  }>;
  readonly wordTargets: readonly [CourseWordTarget, CourseWordTarget?];
}
```

Use explicit key comparison and return frozen values.

- [ ] **Step 4: Run GREEN and commit**

```bash
npx vitest run src/lessons/course-mode/course-mode.contract.spec.ts

git add src/lessons/course-mode/course-mode.types.ts \
  src/lessons/course-mode/course-mode.contract.ts \
  src/lessons/course-mode/course-mode.contract.spec.ts
git commit -m "feat(course): validate course mode v2 content"
```

### Task 2: Persist Versioned V2 Contracts

**Files:**
- Create: `src/database/migrations/122_course_mode_v2.sql`
- Create: `src/database/migrations/122_course_mode_v2.down.sql`
- Create: `src/lessons/course-mode/course-mode.repository.ts`
- Create: `src/lessons/course-mode/course-mode.repository.spec.ts`
- Create: `tests/course-mode-v2.migration.spec.ts`

- [ ] **Step 1: Write failing migration/repository tests**

Use this schema:

```sql
CREATE TABLE lesson_course_mode_contracts (
  lesson_id UUID PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
  preset_id TEXT NOT NULL CHECK (preset_id = 'courseCompanion'),
  preset_version INTEGER NOT NULL CHECK (preset_version = 2),
  contract JSONB NOT NULL CHECK (jsonb_typeof(contract) = 'object'),
  contract_checksum TEXT NOT NULL CHECK (contract_checksum ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

The service parser remains authoritative; SQL constraints guard identity and
shape only.

- [ ] **Step 2: Run RED**

```bash
npx vitest run \
  tests/course-mode-v2.migration.spec.ts \
  src/lessons/course-mode/course-mode.repository.spec.ts
```

- [ ] **Step 3: Implement migration and repository**

Repository methods:

```ts
save(client, lessonId, contract, checksum): Promise<void>
findByLessonId(client, lessonId): Promise<CourseModeV2 | null>
deleteByLessonId(client, lessonId): Promise<void>
```

Validate on write and read. Use canonical JCS serialization for checksum.

- [ ] **Step 4: Run GREEN and commit**

```bash
npx vitest run \
  tests/course-mode-v2.migration.spec.ts \
  src/lessons/course-mode/course-mode.repository.spec.ts

git add src/database/migrations/122_course_mode_v2.sql \
  src/database/migrations/122_course_mode_v2.down.sql \
  src/lessons/course-mode/course-mode.repository.ts \
  src/lessons/course-mode/course-mode.repository.spec.ts \
  tests/course-mode-v2.migration.spec.ts
git commit -m "feat(course): persist versioned course mode contracts"
```

### Task 3: Add Authoring API With Publish Gate

**Files:**
- Modify: `src/lessons/authoring/lesson-authoring.dto.ts`
- Modify: `src/lessons/authoring/lesson-authoring.controller.ts`
- Modify: `src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `src/lessons/authoring/lesson-authoring.logic.ts`
- Create: `src/lessons/authoring/lesson-authoring.course-mode.spec.ts`

- [ ] **Step 1: Write failing API/service tests**

Add an admin endpoint following existing lesson authoring auth patterns:

```text
PUT /v1/admin/lessons/:lessonId/course-mode
GET /v1/admin/lessons/:lessonId/course-mode
DELETE /v1/admin/lessons/:lessonId/course-mode
```

Tests cover auth, exact validation, transaction rollback, active assignment
publish protection, checksum update, and flag-off rejection.

- [ ] **Step 2: Implement DTO/controller/service**

The DTO carries `contract: Record<string, unknown>` so the domain parser is the
single exact validator. Store drafts with the flag off; block publishing/serving
until `COURSE_MODE_V2_PUBLISH_ENABLED=true`.

- [ ] **Step 3: Run tests and commit**

```bash
npx vitest run \
  src/lessons/authoring/lesson-authoring.course-mode.spec.ts \
  src/lessons/authoring/lesson-authoring.interaction-contract.spec.ts

git add src/lessons/authoring/lesson-authoring.dto.ts \
  src/lessons/authoring/lesson-authoring.controller.ts \
  src/lessons/authoring/lesson-authoring.service.ts \
  src/lessons/authoring/lesson-authoring.logic.ts \
  src/lessons/authoring/lesson-authoring.course-mode.spec.ts
git commit -m "feat(course): author course mode v2 lessons"
```

### Task 4: Project V2 Into the Served Manifest

**Files:**
- Modify: `src/lessons/lesson-manifest.logic.ts`
- Modify: `src/lessons/lesson-manifest.service.ts`
- Modify: `src/lessons/lesson-manifest.canonical.cjs`
- Create: `src/lessons/lesson-manifest.course-mode.spec.ts`
- Test: `src/lessons/lesson-manifest.tvideo-conversation.spec.ts`

- [ ] **Step 1: Write failing manifest tests**

Expected projection:

```json
{
  "conversation": {
    "presetId": "courseCompanion",
    "presetVersion": 2,
    "ageBand": "3-5",
    "durationPolicy": {},
    "languagePolicy": {},
    "sessionOpening": {},
    "wordTargets": [],
    "sessionClose": {}
  }
}
```

Assert flag-off omission/rejection, V1 byte stability, checksum inclusion, and
one contract authority per lesson.

- [ ] **Step 2: Implement resolver branch**

Join `lesson_course_mode_contracts` only for V2 lessons. Include the validated
contract in canonical identity; do not mix TVideo V1 normalized tables into V2.

- [ ] **Step 3: Run GREEN, vector check, and commit**

```bash
npx vitest run \
  src/lessons/lesson-manifest.course-mode.spec.ts \
  src/lessons/lesson-manifest.tvideo-conversation.spec.ts \
  src/lessons/lesson-manifest.checksum.spec.ts
npm run lesson:contract-vectors:check

git add src/lessons/lesson-manifest.logic.ts \
  src/lessons/lesson-manifest.service.ts \
  src/lessons/lesson-manifest.canonical.cjs \
  src/lessons/lesson-manifest.course-mode.spec.ts
git commit -m "feat(course): serve course mode v2 manifests"
```

### Task 5: Ingest Privacy-Safe Word Evidence

**Files:**
- Modify: `src/lessons/lesson-event-ingest.logic.ts`
- Modify: `src/lessons/lesson-event-ingest.service.ts`
- Create: `src/lessons/lesson-event-ingest.course-mode.spec.ts`
- Test: `src/lessons/lesson-event-ingest.logic.spec.ts`

- [ ] **Step 1: Write failing event allowlist tests**

Add `word_evidence_recorded`. Allow only:

```text
targetId, evidenceLevel, activityId, contextId,
supportCodesSinceLastModel, fullModelCount,
elapsedSinceFullModelMs, interveningActivityCount,
assessmentConfidenceBand, reviewNeeded
```

Use confidence bands `low|medium|high`, never raw numeric confidence. Recursively
strip transcript/audio/score/pronunciation/story/debug fields.

- [ ] **Step 2: Implement mapping**

Set `stepId` to the bounded `targetId`, `outcome` null, and payload to the exact
allowlist. Reject unsupported evidence levels rather than storing arbitrary
strings.

- [ ] **Step 3: Run tests and commit**

```bash
npx vitest run \
  src/lessons/lesson-event-ingest.course-mode.spec.ts \
  src/lessons/lesson-event-ingest.logic.spec.ts \
  src/lessons/lesson-event-ingest.service.spec.ts

git add src/lessons/lesson-event-ingest.logic.ts \
  src/lessons/lesson-event-ingest.service.ts \
  src/lessons/lesson-event-ingest.course-mode.spec.ts
git commit -m "feat(course): ingest privacy-safe word evidence"
```

### Task 6: Project Parent Word Learning Summary

**Files:**
- Modify: `src/lessons/parent-learning-progress.types.ts`
- Modify: `src/lessons/parent-learning-progress.service.ts`
- Modify: `src/lessons/parent-learning-progress.controller.ts`
- Create: `src/lessons/parent-learning-progress.course-mode.spec.ts`

- [ ] **Step 1: Write failing projection tests**

Expected summary type:

```ts
interface CourseModeWordSummary {
  targetId: string;
  displayWord: string;
  level:
    | 'encountered'
    | 'understood'
    | 'said_with_support'
    | 'recalled_independently'
    | 'mastered_today'
    | 'review_needed';
  reviewNeeded: boolean;
}
```

Assert no transcript, audio, pronunciation score, emotional share, family topic,
or child label appears in the controller response.

- [ ] **Step 2: Implement aggregate query and mapping**

Select the highest evidence level per `(child, lesson, targetId, session)` and
project friendly labels. A later miss does not erase earlier evidence but may set
`reviewNeeded=true`.

- [ ] **Step 3: Run tests and commit**

```bash
npx vitest run \
  src/lessons/parent-learning-progress.course-mode.spec.ts \
  src/lessons/parent-learning-progress.service.spec.ts \
  src/lessons/parent-learning-progress.controller.spec.ts

git add src/lessons/parent-learning-progress.types.ts \
  src/lessons/parent-learning-progress.service.ts \
  src/lessons/parent-learning-progress.controller.ts \
  src/lessons/parent-learning-progress.course-mode.spec.ts
git commit -m "feat(course): report truthful word learning progress"
```

### Task 7: Add Rollout, Metrics, and Cross-Contract Fixture

**Files:**
- Modify: `src/lessons/lesson-rollout.config.ts`
- Modify: `src/lessons/lesson-rollout.config.spec.ts`
- Modify: `src/lessons/lesson-operations-metrics.ts`
- Modify: `src/lessons/lesson-operations-metrics.spec.ts`
- Create: `src/lessons/fixtures/course-mode-v2-pilot.json`
- Create: `src/lessons/course-mode/course-mode.cross-contract.spec.ts`
- Modify: `scripts/generate-lesson-contract-vectors.mjs`

- [ ] **Step 1: Add failing flag and metric tests**

Metrics:

```text
course_mode_sessions_total{result,preset_version}
course_mode_evidence_total{level}
course_mode_answer_leakage_rejections_total{reason}
course_mode_context_branches_total{type,close_reason}
course_mode_embodied_actions_total{intent,outcome}
```

Do not label metrics with child, household, target word, or free-form content.

- [ ] **Step 2: Add fixture/vector parity**

Generate the same normalized fixture consumed by ESP server and firmware tests.
The check command fails on drift.

- [ ] **Step 3: Run backend gates**

```bash
npx vitest run \
  src/lessons/course-mode/course-mode.cross-contract.spec.ts \
  src/lessons/lesson-operations-metrics.spec.ts
npm run lesson:contract-vectors:check
npm run typecheck
npm run lint
```

- [ ] **Step 4: Commit**

```bash
git add src/lessons/fixtures/course-mode-v2-pilot.json \
  src/lessons/course-mode/course-mode.cross-contract.spec.ts \
  scripts/generate-lesson-contract-vectors.mjs \
  src/lessons/lesson-rollout.config.ts \
  src/lessons/lesson-rollout.config.spec.ts \
  src/lessons/lesson-operations-metrics.ts \
  src/lessons/lesson-operations-metrics.spec.ts
git commit -m "feat(course): gate and observe course mode v2"
```

### Task 8: Run Backend Regression and Migration Proof

**Files:**
- Create: `docs/qa/ad-hoc/2026-08-21-course-mode-v2-backend.md`

- [ ] **Step 1: Run focused V2 suite**

```bash
npx vitest run \
  src/lessons/course-mode/course-mode.contract.spec.ts \
  src/lessons/course-mode/course-mode.repository.spec.ts \
  src/lessons/authoring/lesson-authoring.course-mode.spec.ts \
  src/lessons/lesson-manifest.course-mode.spec.ts \
  src/lessons/lesson-event-ingest.course-mode.spec.ts \
  src/lessons/parent-learning-progress.course-mode.spec.ts \
  src/lessons/course-mode/course-mode.cross-contract.spec.ts
```

- [ ] **Step 2: Run existing lesson regression**

```bash
npx vitest run \
  src/lessons/lesson-manifest.tvideo-conversation.spec.ts \
  src/lessons/authoring/lesson-authoring.tvideo-journey.spec.ts \
  src/lessons/lesson-event-ingest.logic.spec.ts \
  src/lessons/lesson-event-ingest.service.spec.ts \
  src/lessons/parent-learning-progress.service.spec.ts
npm run typecheck
npm run lint
npm run lesson:contract-vectors:check
```

- [ ] **Step 3: Run PostgreSQL migration proof**

Use a disposable test database following existing migration-test environment
patterns. Apply through migration 122, insert valid/invalid contracts, roll back
122, and confirm existing lesson tables remain unchanged.

- [ ] **Step 4: Record evidence and commit**

```bash
git add docs/qa/ad-hoc/2026-08-21-course-mode-v2-backend.md
git commit -m "docs(course): record backend v2 verification"
```

## Phase Exit Gate

Keep `COURSE_MODE_V2_PUBLISH_ENABLED=false` until ESP runtime, physical firmware,
privacy review, educator review, and supervised child-pilot prerequisites are
recorded. Mobile UI for the parent summary is a separate follow-on plan after
the backend response contract is stable.
