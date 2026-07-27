# Parent Learning Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give authorized parents near-real-time visibility into the child's active lesson and a truthful, privacy-safe report after each session.

**Architecture:** NestJS builds one child-scoped read model from assignments, authored steps, sessions, and committed scrubbed progress events. A dedicated parent WebSocket gateway publishes durable projection revisions through a shared outbox/broker. React Native uses one canonical query cache with realtime hints, polling fallback, a live Today screen, History drill-in, a dedicated parent report, and idempotent push deep links.

**Tech Stack:** NestJS/TypeScript/Vitest/PostgreSQL/Redis or existing shared broker, transactional outbox, Expo push, React Native/TypeScript/TanStack Query/Jest/WebSocket/navigation linking.

---

## Repository Map

- Backend: `/Users/manhhodinh/Documents/TBOT/tbot-backend`
- ESP server: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server`
- Mobile: `/Users/manhhodinh/Documents/TBOT/tbot-mobile`
- Preserve the existing dirty file `tbot-mobile/src/__env__.ts`; never stage or rewrite it.
- `/Users/manhhodinh/Documents/TBOT` is not a Git repository. Every command below must run from the repository named by the task. Use the shown `cd`/`git -C` form; never rely on an inherited working directory.

### Task 1: Add Durable Parent Progress Projection Tables

**Files:**
- Create: `tbot-backend/src/database/migrations/110_parent_learning_progress.sql`
- Create: `tbot-backend/src/database/migrations/110_parent_learning_progress.down.sql`
- Test: `tbot-backend/tests/parent-learning-progress.migration.spec.ts`
- Modify: `tbot-backend/src/workers/coppa-retention.worker.ts`
- Modify: `tbot-backend/src/modules/users/account-delete/prisma-account-deletion-executor-repository.ts`
- Test: the corresponding existing worker/repository specs plus the new migration spec.

Do not modify `scripts/migrate.js`; it already discovers and lexically sorts every up migration. Migration `110` follows the current `109_*` lesson migration family and avoids the existing `096_*` filename.

- [ ] **Step 1: Write a failing migration contract test**

Require:

```sql
parent_learning_projection(child_id PRIMARY KEY, projection_revision BIGINT, payload, updated_at)
parent_learning_outbox(id, child_id, session_id, event_type, projection_revision, payload,
  available_at, lease_owner, lease_expires_at, attempt_count, last_error, delivered_at, created_at)
parent_notification_jobs(id, child_id, session_id, notification_type, available_at,
  lease_owner, lease_expires_at, attempt_count, last_error, completed_at, created_at)
parent_notification_ledger(id, session_id, notification_type, guardian_id, status,
  attempt_count, lease_owner, lease_expires_at, provider_message_id, last_error,
  available_at, delivered_at, created_at, updated_at)
```

Require one current projection row per child, a unique outbox identity on `(child_id, projection_revision)`, a unique notification job identity on `(session_id, notification_type)`, and a unique guardian delivery identity on `(session_id, notification_type, guardian_id)`. Add claim indexes for due unfinished rows and retention indexes for delivered/completed rows. Cross-domain identifiers remain by-value, so the migration and cleanup tests must prove child/account deletion explicitly removes projection, outbox, notification jobs, and guardian delivery rows.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- tests/parent-learning-progress.migration.spec.ts
```

Expected: FAIL because migration 110 is absent.

- [ ] **Step 3: Implement the migration**

Use JSONB payloads only for the derived projection/outbox. Source lesson truth remains in assignments, sessions, authored steps, and progress events. The projection table stores only the current child snapshot; outbox history is leased with `FOR UPDATE SKIP LOCKED`, retried with bounded backoff, and garbage-collected after delivery. Define a finite delivered-row TTL and a poison-row policy that records the error without blocking later rows.

Wire the new by-value tables into the existing account-deletion and COPPA-retention paths. The down migration must remove only these new tables/indexes.

- [ ] **Step 4: Verify GREEN and idempotency**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- tests/parent-learning-progress.migration.spec.ts tests/migration.spec.ts
DATABASE_URL="$PARENT_PROGRESS_TEST_DATABASE_URL" npm run migrate
DATABASE_URL="$PARENT_PROGRESS_TEST_DATABASE_URL" npm run migrate
```

Expected: PASS when migrations run twice; cleanup tests remove all child-derived JSONB and guardian ledger rows.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/database/migrations/110_parent_learning_progress.sql src/database/migrations/110_parent_learning_progress.down.sql tests/parent-learning-progress.migration.spec.ts
git add src/workers/coppa-retention.worker.ts src/modules/users/account-delete/prisma-account-deletion-executor-repository.ts
git commit -m "feat(progress): add parent learning projection outbox"
```

### Task 2: Build the Truthful Child Learning Read Model

**Files:**
- Create: `tbot-backend/src/lessons/parent-learning-progress.types.ts`
- Create: `tbot-backend/src/lessons/parent-learning-progress.service.ts`
- Test: `tbot-backend/src/lessons/parent-learning-progress.service.spec.ts`
- Reuse: `tbot-backend/src/lessons/lesson-progress.service.ts`

- [ ] **Step 1: Write failing service tests**

Cover nullable pre-session assignment, one-based `stepNumber`, version-pinned authored activity title/subject, `positionPercent`, active duration excluding pause, terminal summary authority, paginated recent sessions, and backend-authoritative next lesson. Add two concurrent updates for one child, duplicate event replay, and revisions above JavaScript's safe-integer range.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-progress.service.spec.ts
```

Expected: FAIL because the aggregate service/types do not exist.

- [ ] **Step 3: Implement minimal DTOs**

```ts
type ParentLearningStatus = {
  activeLearning: ActiveLearning | null;
  recentSessions: { items: ParentSessionSummary[]; nextCursor: string | null };
  courseProgress: ParentCourseProgress[];
  projectionRevision: string;
};
```

Derive live position from latest committed `step_started` joined to authored `step_index`; do not use `COUNT(step_completed)` for passive-step percentage.

Return PostgreSQL `BIGINT` revisions as canonical unsigned decimal strings across backend JSON, WebSocket frames, and mobile comparisons. Never coerce them to JavaScript `number`.

Derive course completion and the suggested next lesson from version-pinned published lessons plus completed assignments. Do not trust `course_enrollments.current_lesson_key`, because course auto-advance is a separate best-effort post-commit side effect.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-progress.service.spec.ts
npm run typecheck
```

Expected: PASS; `stepNumber=4,total=9` yields `positionPercent=44` but not four completed outcomes; concurrent updates produce distinct ordered revision strings; duplicate retries do not advance a revision.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/lessons/parent-learning-progress.types.ts src/lessons/parent-learning-progress.service.ts src/lessons/parent-learning-progress.service.spec.ts
git commit -m "feat(progress): project parent learning status"
```

### Task 3: Define Lifecycle and Privacy-Safe Runtime Events

**Files:**
- Modify: `tbot-backend/src/lessons/lesson-event-ingest.logic.ts`
- Modify: `tbot-backend/src/lessons/lesson-event-ingest.service.ts`
- Test: `tbot-backend/src/lessons/lesson-event-ingest.service.spec.ts`
- Modify: `robot/esp32-server/main/tbot-server/core/lesson/runtime.py`
- Test: `robot/esp32-server/main/tbot-server/tests/test_lesson_forwarder.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover `runtime_phase_changed`, pause/resume, `lesson_failed -> FAILED`, abandonment, completion, active-duration accounting, and phase events emitted only after accepted visual/runtime transitions. Include duplicate retries, arbitrary future debug fields, and nested/case/punctuation variants of transcript/audio/confidence keys.

- [ ] **Step 2: Verify RED in both repositories**

Backend:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/lesson-event-ingest.service.spec.ts
```

ESP server:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
pytest -q main/tbot-server/tests/test_lesson_forwarder.py main/tbot-server/tests/test_lesson_e2e_flow.py
```

Expected: FAIL for absent phase events and missing failure transition.

- [ ] **Step 3: Implement the minimal event schema**

Add a dedicated allowlisted mapper for `runtime_phase_changed`; accept only the documented categorical state, authored `stepId`/`stepType`, bounded timestamps, and `totalAttempts` when the existing terminal event supplies it. Do not pass generic event payload keys through and do not build parent projections from raw `progress_events.payload`. Existing transcript/audio/confidence scrubbing remains defense in depth, not the primary privacy boundary.

Only an accepted insert or an actual idempotent lifecycle state change may request a projection revision. Duplicate event retries must not create another revision, realtime frame, or notification.

- [ ] **Step 4: Verify GREEN**

Run both focused suites from their repository roots using the commands above.

Expected: PASS; parent-visible phase always has a committed source.

- [ ] **Step 5: Commit separately**

Backend:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/lessons/lesson-event-ingest.logic.ts src/lessons/lesson-event-ingest.service.ts src/lessons/lesson-event-ingest.service.spec.ts
git commit -m "feat(progress): ingest parent-safe lesson lifecycle"
```

ESP server:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
git add main/tbot-server/core/lesson/runtime.py main/tbot-server/tests/test_lesson_forwarder.py main/tbot-server/tests/test_lesson_e2e_flow.py
git commit -m "feat(server): forward parent-safe lesson phases"
```

### Task 4: Add Parent Aggregate and Report APIs with IDOR Protection

**Files:**
- Create: `tbot-backend/src/lessons/parent-learning-progress.controller.ts`
- Modify: `tbot-backend/src/lessons/lessons.module.ts`
- Modify: `tbot-backend/src/lessons/parent-learning-progress.service.ts`
- Test: `tbot-backend/src/lessons/parent-learning-progress.controller.spec.ts`
- Test: `tbot-backend/src/lessons/parent-learning-report.service.spec.ts`

- [ ] **Step 1: Write failing authorization/contract tests**

Endpoints:

```text
GET /v1/mobile/children/:childId/learning-status
GET /v1/mobile/children/:childId/learning-sessions/:sessionId/report
```

Assert accepted guardian access, device/admin token rejection, session constrained through the same child assignment, constant non-disclosing denial, pagination, and incomplete-session duration. The report query must join `sessionId -> lesson_sessions.assignment_id -> lesson_assignments.child_id` and match the path `childId`; possession of a valid session UUID alone never grants access.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-progress.controller.spec.ts src/lessons/parent-learning-report.service.spec.ts
```

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Implement controller and factual report projection**

Report categories are `presented`, `attempted`, `accepted`, and `needsReview`. Do not claim mastery. Intermediate retry/near-miss counts appear only when explicit events exist.

Build aggregate/report DTOs from an explicit allowlist of authored columns, lifecycle columns, dedicated outcome fields, and reward rows. Never serialize or spread raw `progress_events.payload`; arbitrary future keys must remain absent even when they are not named in the denylist.

- [ ] **Step 4: Verify GREEN and privacy**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-progress.controller.spec.ts src/lessons/parent-learning-report.service.spec.ts src/lessons/lesson-event-ingest.logic.spec.ts
```

Expected: PASS; serialized responses contain no transcript/audio/confidence/debug keys.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/lessons/parent-learning-progress.controller.ts src/lessons/lessons.module.ts src/lessons/parent-learning-progress.service.ts src/lessons/parent-learning-progress.controller.spec.ts src/lessons/parent-learning-report.service.spec.ts
git commit -m "feat(progress): expose parent learning APIs"
```

### Task 5: Publish Durable Projection Revisions and Parent WebSocket Frames

**Files:**
- Create: `tbot-backend/src/lessons/parent-learning-projection.service.ts`
- Create: `tbot-backend/src/lessons/parent-learning-outbox.worker.ts`
- Create: `tbot-backend/src/gateway/parent-progress-broker.ts`
- Create: `tbot-backend/src/gateway/parent-progress.gateway.ts`
- Modify: `tbot-backend/src/lessons/lesson-event-ingest.service.ts`
- Modify: `tbot-backend/src/lessons/lessons.module.ts`
- Modify: `tbot-backend/src/prod-posture.ts`
- Test: `tbot-backend/src/lessons/parent-learning-projection.service.spec.ts`
- Test: `tbot-backend/src/lessons/parent-learning-outbox.worker.spec.ts`
- Test: `tbot-backend/src/gateway/parent-progress.gateway.spec.ts`

- [ ] **Step 1: Write failing commit-order and scale-out tests**

Assert projection revision is backend-owned and monotonic under two concurrent ingests, duplicate retries do not advance it, projection/outbox rows roll back with a failed ingest, the worker cannot observe a row before commit, replica A outbox reaches a socket registered on replica B, stale child subscription is removed, and token/membership revocation closes an already-open subscription as well as rejecting resubscribe.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-projection.service.spec.ts src/lessons/parent-learning-outbox.worker.spec.ts src/gateway/parent-progress.gateway.spec.ts
```

Expected: FAIL because the parent gateway/outbox do not exist.

- [ ] **Step 3: Implement the separate parent namespace**

Do not modify the device gateway auth contract. Register the parent gateway as its own provider/namespace and authenticate the parent through an authorization header or first-message challenge, not a JWT query parameter. Parent frames contain `childId`, nullable `sessionId`, decimal-string `projectionRevision`, `occurredAt`, `publishedAt`, and the current aggregate fragment.

Subscriptions have a short authorization lease. Revalidate parent token expiry and current owner/membership before lease renewal and before delivering after expiry; close with `4401` for invalid/expired auth and `4403` for revoked child access. A household/child switch removes the previous subscription immediately.

- [ ] **Step 4: Implement shared fan-out**

Inside `LessonEventIngestService`, call a projection method that accepts the active `PoolClient`. For each accepted event batch or real lifecycle state change, atomically increment the child's current projection row and insert the matching outbox row before the ingest transaction commits. Do not open a second projection transaction.

The worker leases only committed due rows with `FOR UPDATE SKIP LOCKED`, then publishes through Redis Pub/Sub. Use distinct `ioredis` publisher and subscriber connections; register the broker, worker, projection service, and gateway in `LessonsModule`, start the worker through Nest lifecycle hooks, and close timers plus both Redis connections on shutdown. The gateway on every replica subscribes to the same channel. Unit tests inject deterministic in-memory publisher/subscriber adapters. Polling remains recovery because Pub/Sub delivery is transient.

Marking an outbox row delivered after publish may create duplicate frames after a crash. That is acceptable: frames are realtime hints, and clients dedupe decimal-string revisions. It must never create a missing durable aggregate revision.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-projection.service.spec.ts src/lessons/parent-learning-outbox.worker.spec.ts src/gateway/parent-progress.gateway.spec.ts
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/lessons/parent-learning-projection.service.ts src/lessons/parent-learning-outbox.worker.ts src/gateway/parent-progress-broker.ts src/gateway/parent-progress.gateway.ts src/lessons/lesson-event-ingest.service.ts src/lessons/lessons.module.ts src/lessons/parent-learning-projection.service.spec.ts src/lessons/parent-learning-outbox.worker.spec.ts src/gateway/parent-progress.gateway.spec.ts
git add src/prod-posture.ts
git commit -m "feat(progress): stream committed parent updates"
```

### Task 6: Add Idempotent Guardian Push and Exact Deep Links

**Files:**
- Create: `tbot-backend/src/lessons/parent-learning-notification.service.ts`
- Modify: `tbot-backend/src/lessons/lesson-event-ingest.service.ts`
- Modify: `tbot-backend/src/lessons/lessons.module.ts`
- Modify: `tbot-backend/src/modules/notifications/config/notification-type-config-service.ts`
- Test: `tbot-backend/src/lessons/parent-learning-notification.service.spec.ts`
- Test: `tbot-backend/src/modules/notifications/config/notification-type-config-service.spec.ts`

- [ ] **Step 1: Write failing outbox/recipient tests**

Assert one durable notification job per `(sessionId, notificationType)`, current accepted-guardian resolution at dispatch time, idempotent guardian-ledger enqueue, retry after provider/network failure, mobile dedupe by stable `notificationId`, report availability independent of course-advance success, and no step-by-step push.

Do not claim transport-level exactly-once delivery. The contract is durable/idempotent enqueue with provider at-least-once attempts; a crash after provider acceptance but before ledger acknowledgement may duplicate a push. The payload's stable `notificationId` lets mobile suppress duplicate presentation/navigation.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-notification.service.spec.ts
```

Expected: FAIL because durable jobs, current guardian resolution, and delivery semantics are absent.

- [ ] **Step 3: Implement the notification worker**

Insert the terminal notification job with the same `PoolClient` and transaction that commits the authoritative completion, attention-worthy pause, or failure transition. The worker later resolves current household owner and accepted memberships, creates unique guardian ledger rows, rechecks current access immediately before each provider attempt, and uses the canonical modular notification dispatch path.

Register the notification service/worker in `LessonsModule`, inject the existing modular notification dispatch service, and close the lesson-notification poller on shutdown. Add the three push-only types `parent_lesson_completed`, `parent_lesson_attention`, and `parent_lesson_failed` to `notification-type-config-service.ts`; keep their scope `household`, their throttle key session/guardian-specific through the stable idempotency key, and their payload data-only except for localized template variables. A course-advance failure must not suppress report access or notification processing.

Use:

```text
TJBot://parent/children/:childId/sessions/:sessionId/report
```

Send completion, attention-worthy pause, and terminal failure only. Include stable `notificationId`, `childId`, `sessionId`, and `deepLink`; never include the projection/report payload.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm test -- src/lessons/parent-learning-notification.service.spec.ts
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
git add src/lessons/parent-learning-notification.service.ts src/lessons/parent-learning-notification.service.spec.ts src/modules/notifications/config/notification-type-config-service.ts src/modules/notifications/config/notification-type-config-service.spec.ts
git add src/lessons/lesson-event-ingest.service.ts src/lessons/lessons.module.ts
git commit -m "feat(progress): notify guardians after lesson sessions"
```

### Task 7: Add Mobile Contracts, Canonical Query, Realtime, and Poll Fallback

**Files:**
- Create: `tbot-mobile/src/services/api/parentLearning.api.ts`
- Create: `tbot-mobile/src/features/parent/hooks/useParentLearningStatusQuery.ts`
- Create: `tbot-mobile/src/features/parent/hooks/useParentLearningHistoryQuery.ts`
- Create: `tbot-mobile/src/features/parent/hooks/useParentSessionReportQuery.ts`
- Create: `tbot-mobile/src/services/ws/parentProgressRealtime.ts`
- Modify: `tbot-mobile/src/services/ws/realtime.ts`
- Test: `tbot-mobile/tests/api/parent-learning-api.test.ts`
- Test: `tbot-mobile/tests/features/parent/use-parent-learning-status-query.test.tsx`
- Test: `tbot-mobile/tests/features/parent/use-parent-learning-history-query.test.tsx`
- Test: `tbot-mobile/tests/features/parent/use-parent-session-report-query.test.tsx`
- Test: `tbot-mobile/tests/services/parent-progress-realtime.test.ts`
- Test: `tbot-mobile/tests/services/ws-realtime.test.ts`

- [ ] **Step 1: Write failing normalizer/realtime tests**

Cover nullable active learning, report normalization, History cursor merge/dedup, dropped unsafe fields, decimal-string revision comparison beyond `Number.MAX_SAFE_INTEGER`, duplicate/out-of-order/gapped projection revisions, child switch close, reconnect refetch, and bounded polling lifecycle.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
npm test -- --runInBand tests/api/parent-learning-api.test.ts tests/features/parent/use-parent-learning-status-query.test.tsx tests/features/parent/use-parent-learning-history-query.test.tsx tests/features/parent/use-parent-session-report-query.test.tsx tests/services/parent-progress-realtime.test.ts tests/services/ws-realtime.test.ts
```

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement the API and canonical key**

Mobile paths are `/mobile/children/...` because `API_BASE_URL` already includes `/v1`.

```ts
export const parentLearningStatusKey = (childId: string) =>
  ['parent-learning-status', childId] as const;

export const parentSessionReportKey = (childId: string, sessionId: string) =>
  ['parent-session-report', childId, sessionId] as const;
```

- [ ] **Step 4: Implement the dedicated child-scoped socket**

Extract a tested `createReconnectingSocket()` primitive from `realtime.ts` without changing the existing session observer behavior. Parent protocol is exact:

```text
URL: ${WS_BASE_URL}/parent-progress
Header: Authorization: Bearer PARENT_JWT
Client subscribe: {"type":"subscribe","childId":"...","lastProjectionRevision":"12"}
Server snapshot: {"type":"lesson.progress.snapshot","childId":"...","projectionRevision":"13","status":{...}}
Server update: {"type":"lesson.progress.updated","childId":"...","sessionId":"...","projectionRevision":"14","occurredAt":"...","publishedAt":"...","activeLearning":{...}}
Close 4401: expired/invalid parent JWT
Close 4403: child membership revoked or unauthorized
```

Compare revisions as normalized decimal strings without numeric coercion. On revision gap or invalid frame, invalidate/refetch instead of merging.

- [ ] **Step 5: Implement bounded polling fallback**

Fetch immediately on mount, foreground resume, and reconnect. While an active assignment/session exists, the app is foregrounded, and the WebSocket has failed three consecutive reconnects, poll every 10 seconds. Suspend polling while the socket is healthy. Stop polling on terminal/no-active state, background, child switch, or unmount.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
npm test -- --runInBand tests/api/parent-learning-api.test.ts tests/features/parent/use-parent-learning-status-query.test.tsx tests/features/parent/use-parent-learning-history-query.test.tsx tests/features/parent/use-parent-session-report-query.test.tsx tests/services/parent-progress-realtime.test.ts tests/services/ws-realtime.test.ts
npm run typecheck
```

- [ ] **Step 7: Commit without `src/__env__.ts`**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
git add src/services/api/parentLearning.api.ts src/features/parent/hooks/useParentLearningStatusQuery.ts src/features/parent/hooks/useParentLearningHistoryQuery.ts src/features/parent/hooks/useParentSessionReportQuery.ts src/services/ws/parentProgressRealtime.ts src/services/ws/realtime.ts tests/api/parent-learning-api.test.ts tests/features/parent/use-parent-learning-status-query.test.tsx tests/features/parent/use-parent-learning-history-query.test.tsx tests/features/parent/use-parent-session-report-query.test.tsx tests/services/parent-progress-realtime.test.ts tests/services/ws-realtime.test.ts
git commit -m "feat(parent): add live learning data client"
```

### Task 8: Build Parent Today, History, and Session Report UX

**Files:**
- Modify: `tbot-mobile/src/features/parent/screens/ParentTodayScreen.tsx`
- Modify: `tbot-mobile/src/features/parent/screens/ParentHistoryScreen.tsx`
- Create: `tbot-mobile/src/features/parent/screens/ParentSessionReportScreen.tsx`
- Modify: `tbot-mobile/src/features/parent/screens/ParentSummaryScreen.tsx`
- Modify: `tbot-mobile/src/features/progress/screens/TodayProgressScreen.tsx`
- Modify: `tbot-mobile/src/features/progress/hooks/useChildProgressDashboardQuery.ts`
- Modify: `tbot-mobile/src/features/progress/hooks/useChildLessonProgressQuery.ts`
- Modify: `tbot-mobile/src/features/parent/navigation.ts`
- Modify: `tbot-mobile/src/navigation/routes.ts`
- Modify: `tbot-mobile/src/services/i18n/locales/en.json`
- Modify: `tbot-mobile/src/services/i18n/locales/vi.json`
- Test: `tbot-mobile/tests/features/parent/parent-today-screen.test.tsx`
- Test: `tbot-mobile/tests/features/parent/parent-history-screen.test.tsx`
- Create: `tbot-mobile/tests/features/parent/parent-session-report-screen.test.tsx`

- [ ] **Step 1: Write failing persona/UX tests**

Today shows state, lesson, authored activity, step position, active duration, last update, reconnect status, and optional avatar fallback. History implements cursor-based load-more with session-ID dedup and navigates with exact required child/session IDs. Report shows evidence labels without child-facing reward CTAs or mastery claims. Parent Summary and progress dashboards consume the canonical aggregate or invalidate/refetch it rather than maintaining contradictory totals.

- [ ] **Step 2: Verify RED**

Run from `/Users/manhhodinh/Documents/TBOT/tbot-mobile`; expect missing report route and old polling-only Today UI.

- [ ] **Step 3: Implement the screens**

Use `presented`, `attempted`, `accepted`, and `needsReview` copy. Suggested next lesson comes only from backend response.

- [ ] **Step 4: Verify GREEN and accessibility**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
npm test -- --runInBand tests/features/parent/parent-today-screen.test.tsx tests/features/parent/parent-history-screen.test.tsx tests/features/parent/parent-session-report-screen.test.tsx
npm run i18n:check
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
git add src/features/parent/screens/ParentTodayScreen.tsx src/features/parent/screens/ParentHistoryScreen.tsx src/features/parent/screens/ParentSessionReportScreen.tsx src/features/parent/screens/ParentSummaryScreen.tsx src/features/progress/screens/TodayProgressScreen.tsx src/features/progress/hooks/useChildProgressDashboardQuery.ts src/features/progress/hooks/useChildLessonProgressQuery.ts src/features/parent/navigation.ts src/navigation/routes.ts src/services/i18n/locales/en.json src/services/i18n/locales/vi.json tests/features/parent/parent-today-screen.test.tsx tests/features/parent/parent-history-screen.test.tsx tests/features/parent/parent-session-report-screen.test.tsx
git commit -m "feat(parent): show live lesson and session report"
```

### Task 9: Wire Push Navigation, Cache Invalidation, and Release Gates

**Files:**
- Modify: `tbot-mobile/src/hooks/usePushNotifications.ts`
- Modify: `tbot-mobile/src/services/notifications/deepLink.ts`
- Modify: `tbot-mobile/src/navigation/linking.ts`
- Modify: `tbot-mobile/src/App.tsx`
- Create: `tbot-mobile/src/features/parent/ParentNotificationCoordinator.tsx`
- Test: `tbot-mobile/tests/hooks/usePushNotifications.test.ts`
- Test: `tbot-mobile/tests/navigation/notification-linking.test.ts`
- Modify: `tbot-mobile/src/features/parent/screens/ParentSettingsScreen.tsx`

- [ ] **Step 1: Write failing navigation tests**

Cover cold/background/foreground taps, stable `notificationId` dedupe, queued navigation before auth/household hydration, logout before replay, unauthorized child deep link, active-child cache invalidation, household switch, and removal of legacy independent progress caches.

- [ ] **Step 2: Verify RED**

Run from `/Users/manhhodinh/Documents/TBOT/tbot-mobile`; expect tap callback to stop before navigation.

- [ ] **Step 3: Implement exact route parsing and invalidation**

Mount `ParentNotificationCoordinator` inside `HouseholdProvider`. It queues the parsed target until auth and active-household state are ready, rechecks child membership, then navigates to `ParentSessionReportScreen({ childId, sessionId })`. It drops queued targets on logout or household/child mismatch. Foreground notifications invalidate aggregate, report, and course progress keys.

Persist a bounded set of handled stable `notificationId` values so provider at-least-once retries do not show or navigate the same notification twice. Dedupe does not bypass backend authorization: the report request must still succeed for the current parent/child before protected data renders.

- [ ] **Step 4: Run full mobile gates**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
npm run typecheck
npm run lint
npm test -- --runInBand
npm run test:navigation -- --runInBand
```

Before mobile work, save `git hash-object src/__env__.ts` and `git diff -- src/__env__.ts`. After every mobile task, require the same hash/diff and require `git diff --cached --name-only | rg '^src/__env__\.ts$'` to return no match.

- [ ] **Step 5: Run backend gates and production smoke**

Backend:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm run typecheck
npm run build
DATABASE_URL="$PARENT_PROGRESS_TEST_DATABASE_URL" npm run migrate
DATABASE_URL="$PARENT_PROGRESS_TEST_DATABASE_URL" npm run migrate
```

Production smoke: authorized parent sees live update; unauthorized parent/device/admin token is denied; completion push opens the correct report; realtime disconnect falls back to polling.

- [ ] **Step 6: Commit mobile integration/evidence**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-mobile
git add src/hooks/usePushNotifications.ts src/services/notifications/deepLink.ts src/navigation/linking.ts src/App.tsx src/features/parent/ParentNotificationCoordinator.tsx src/features/parent/screens/ParentSettingsScreen.tsx tests/hooks/usePushNotifications.test.ts tests/navigation/notification-linking.test.ts
git commit -m "feat(parent): deep link completed lesson reports"
```
