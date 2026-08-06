# T4.2 admin assignment console & monitoring

Date: 2026-08-06
Branch: `lesson-prod/t42-admin-console` (both `robot/esp32-server` and `tbot-backend`)
Base: `0f44fa6e` (esp32-server) / `41103ee` (tbot-backend); rebased onto `8892a8ab` / `2df3a04`
Backend-side companion evidence: `tbot-backend/docs/qa/ad-hoc/2026-08-06-t42-admin-console.md`

## Where the console actually is

The task names four console actions. They are not all in one place, and two of them
have no operator surface at all — recorded here because the checklist verdicts below
depend on it:

| Action | Operator surface | Backing endpoint |
| --- | --- | --- |
| Assign | `manager-web` `CourseLessons.vue` assign-to-child dialog | `POST /v1/admin/lesson-assignments` |
| Retry generation | `manager-web` `LessonEditor.vue` + `LessonSdSyncStatus.vue` | `POST /v1/admin/lesson-assets/retry` → ESP `generation_retry_handler.py` |
| Monitor | `manager-web` `LessonMonitoring.vue`, `CourseInsights.vue` | `GET /v1/admin/lesson-monitoring/*`, `/v1/admin/course-insights/*` |
| Cancel | **none** — API only | `POST /v1/admin/lesson-assignment-operations/:id/cancel` |
| Nudge | **none** — backend-internal | ESP `POST /internal/devices/:id/lesson-nudge` |
| Assign (ESP fallback console) | ESP-served HTML page, `lesson_assignment_console_handler.py` | backend `/devices/:id/assignments`, `/courses/:id/enroll` |

Missing cancel/nudge UI is routed to the release gate, not invented here
(`LESSON_PRODUCTION_PLAN.md` §5, owning task T6.5).

## Reproduction

### 1. Monitoring console repainted with superseded responses

`LessonMonitoring.vue` had no request token on either read. Both callbacks assigned
unconditionally (`this.list = rows`, `this.events = rows`). The console is refreshed
while lessons are live, so two in-flight reads settle in arbitrary order.

Failing test-first run (new behavioural block in the task's own verify command,
`scripts/check-lesson-assignment-ui-contracts.mjs`, against the pre-patch view):

```text
node scripts/check-lesson-assignment-ui-contracts.mjs
Error: a superseded assignment-list response must not repaint the table
    at .../scripts/check-lesson-assignment-ui-contracts.mjs:433:11
EXIT=1
```

The assertions are behavioural, not name-based: the staleness predicates are
extracted with a fallback that means "no guard at all", so a console without them
still executes and fails on the repaint it produces rather than on a missing
identifier.

### 2. ESP operator console offered robot MACs where the API requires UUIDs

`LessonAssignmentConsoleHandler` published `lesson_connections.keys()` — the
websocket registry is keyed by the robot MAC (`core/websocket_server.py:239`, from
the `device-id` header) — as the device picker's values, and **prefilled** one when
exactly one robot was connected. Every assignment route is `/devices/{deviceId}/...`
behind `new ParseUUIDPipe()` (`device-assignment.controller.ts:114`), so a MAC there
can only 400. The nudge handler in the same directory already documents the split:
"WebSocket identity is the robot MAC; the backend nudge route carries the backend
device UUID" (`lesson_nudge_handler.py:194-196`).

Failing test-first run:

```text
python3 -m pytest tests/test_lesson_assignment_console.py
FAILED ...::test_connected_mac_is_published_as_its_backend_device_uuid
FAILED ...::test_unresolved_mac_is_not_offered_as_an_assignable_device
FAILED ...::test_expired_mint_cache_entry_is_treated_as_unresolved
```

### 3 + 4. Backend read side

Course-quality lesson counts were row-space, and monitoring id filters reached
`uuid` columns unvalidated. Both reproduced in the backend evidence file; the
campaign repro is `lesson-prod/repros/t42-backend.sh`.

## Fix diff summary

esp32-server (`f73cfd0d`):

- `main/manager-web/src/views/LessonMonitoring.vue`: monotonic `listRequestId` /
  `eventsRequestId`; `isListRequestCurrent` / `isEventsRequestCurrent` gate every
  success and error callback; `resetEvents` bumps the token and clears loading so a
  read in flight when the dialog closes can never arrive later.
- `main/manager-web/scripts/check-lesson-assignment-ui-contracts.mjs`: monitoring
  console block — out-of-order list responses, superseded failures (no toast, no
  premature loading clear), close/reopen timeline crossover, post-close failure.
- `main/tbot-server/config/device_token_client.py`: `cached_device_uuid(mac)`, a
  read-only, TTL-respecting view of the existing mint cache (no network I/O — the
  console renders synchronously).
- `main/tbot-server/core/api/lesson_assignment_console_handler.py`: picker offers
  `{mac, deviceId}`, values are backend UUIDs labelled by MAC, prefill only for a
  resolved UUID, and unresolved robots are named in a hint instead of being handed
  over as if assignable.
- `main/tbot-server/tests/test_lesson_assignment_console.py`: three regression
  tests (resolved, unresolved, expired cache entry).

tbot-backend (`89cd3e5`): see the backend evidence file.

## Passing re-runs

```text
# task verify commands (manager-web)
node scripts/check-lesson-assignment-ui-contracts.mjs   -> lesson assignment UI contracts passed
node scripts/check-lesson-sd-sync-ui.mjs                -> Lesson generation rollout UI contracts passed

# full admin-web suite
npm run test:lesson-studio                              -> EXIT=0 (17 groups PASS)

# ESP server
python3 -m pytest tests/test_lesson_assignment_console.py tests/test_lesson_nudge_handler.py \
  tests/test_local_sample_demo_nudge.py tests/test_generation_retry_handler.py \
  tests/test_device_token_client.py                     -> 42 passed
# every other suite importing config/device_token_client
python3 -m pytest tests/test_lesson_runtime.py tests/test_lesson_sd_pack_fanout.py \
  tests/test_lesson_runtime_on_connect_branch_gaps.py tests/test_google_live_audio_bridge_edges.py \
  tests/test_voice_consent_gate.py tests/test_lesson_e2e_flow.py \
  tests/test_cache_output_asset_consent_helpers.py      -> 331 passed

# backend
npm run typecheck && npm run lint                       -> clean
npm test                                                -> 5295 passed | 633 skipped (0 failed)
```

Gate runs (`lesson-prod/GATE_LOG.md`): `t42` VERIFIED (esp32-server, RED@base rc=1 →
GREEN@tip rc=0) and `t42-backend` VERIFIED (tbot-backend, RED@base 5 failing tests →
GREEN@tip).

## Deep-dive checklist

| Case | Evidence | Verdict |
| --- | --- | --- |
| Console device status matches ESP truth (no stale green) | `listEligibleDevices` derives `availability` only from `lesson_assignments` state and never reads presence, so a powered-off robot renders as a green "Available" row. Backend-owned presence exists one layer down (`LessonHandoffService.isDeviceOnline` → `device_heartbeat_snapshots.connectivity_state`) but is not in the eligibility payload. File is `admin-lesson-assignment-*`, owned by T1.2 and IN_PROGRESS | **FAIL / ROUTED (T1.2)** |
| Nudge to offline robot: queued or failed visibly, never silent success | ESP side is honest: `lesson_nudge_handler` returns `202 {nudged:false, reason:"device-offline"}` and never fabricates a nudge. Backend side is not: `createAssignment` awaits `handoffOnAssign` and discards its `{delivered, reason}` (and swallows throws), so the console shows unqualified success for an assignment that will not start until the robot reconnects. `lesson-assignment.service.ts` / `lesson-handoff.service.ts` are T1.2 files | **PARTIAL — ESP PASS, backend FAIL / ROUTED (T1.2)** |
| Cancel of already-completed assignment: correct no-op message | `AdminLessonAssignmentOperationService.cancel`: `CANCELLED` → `200 {cancelled:false}` (true idempotent no-op, version not re-checked); `COMPLETED`/`FAILED` → `409 ASSIGNMENT_CONFLICT` carrying `{state, expectedAssignmentVersion, currentAssignmentVersion}`. Correct semantics, no fabricated success. No console surface exists to display it | PASS (API) / no UI |
| Retry generation twice rapidly: single job, single UI state | ESP `GlobalGenerationPoller.trigger_retry` is single-flight (`_retry_task` guard + `_run_lock`); the second call returns `{"state":"not_modified"}` with no second job. UI duplicate-submit protection asserted in `check-lesson-sd-sync-ui.mjs`: "duplicate retry clicks must send only one POST", and an overlapping status poll may not settle the retry | PASS |
| Bulk operations partial failure: per-row result | No bulk assign/cancel/nudge exists in the assignment console or monitoring view (`selection-change`/batch controls appear only in unrelated admin views and the T4.1 TVideo batch panel). Nothing can lie all-or-nothing because nothing is bulk | NOT APPLICABLE |
| Assignment list pagination + filters stable under live updates | Fixed: both monitoring reads are request-token guarded, proven by the new contract block. No pagination exists — the view requests the backend cap (200, `clampLimit`) and marks the count `is-capped` with a "refine the filters" tooltip when the cap is hit, so a truncated list is never presented as complete | PASS (after fix) |
| Timestamps displayed in expected timezone consistently | Every lesson-console timestamp column is `TIMESTAMPTZ` (`progress_events.occurred_at`, `lesson_sessions.started_at/completed_at`, `lesson_assignments.created_at/updated_at`, migrations 076/017), serialized as ISO-8601 UTC, and rendered through the same `new Date(v).toLocaleString()` in `LessonMonitoring.vue`, `CourseInsights.vue` and `LessonSdSyncStatus.vue` — one browser-local rendering, no mixed naive/aware columns | PASS |
| Every admin action visible in audit trail with actor | Only `lesson_assignment.cancel` writes `admin_audit_log` with `admin_user_id` + `ip_address`. `POST /v1/admin/lesson-assignments`, `:id/repair-manifest-checksum` and `:id/release-preflight-failure` mutate state and write no audit row — `AdminLessonAssignmentCreateController` never receives the `AdminPrincipal`, so the actor is not even available to log | **FAIL / ROUTED (T1.6)** |

## Findings routed (LESSON_PRODUCTION_PLAN.md §5)

| Owning task | Severity | Finding |
| --- | --- | --- |
| T1.2 | HIGH | Stale green: eligible-devices `availability` ignores device connectivity |
| T1.2 | HIGH | Assign reports success when the hand-off reported `device-offline`/`handoff-failed` |
| T1.6 | MED | Admin assign / repair-checksum / release-preflight write no audit row and get no `AdminPrincipal` |
| T6.5 | MED | Cancel and nudge have no operator console anywhere; product call for the release gate |

The T1.1-routed row ("admin course-quality lesson counts use distinct row ids") is
resolved by this task and marked RESOLVED in §5.
