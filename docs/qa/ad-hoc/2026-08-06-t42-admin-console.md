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

## Ship checklist

1. **Re-verify at tip.** `main` moved twice during this session (other campaign
   sessions merging). Branch rebased onto `6326a899` (esp32-server) / `40f4353`
   (tbot-backend) and re-verified at tip before each merge attempt:
   both manager-web verify commands PASS, `npm run test:lesson-studio` EXIT=0,
   ESP console/nudge/retry/token suites 42 passed, backend `typecheck`+`lint` clean,
   `npm test` 5333 passed / 0 failed.
2. **Merge to main via the T0.4 gate.** Two gates, one per repo (the change spans
   `robot/esp32-server` and `tbot-backend`; `gate.sh` takes one repo):
   - `merge-task.sh t42 robot/esp32-server` → **GATE PASS: t42 VERIFIED**
     (RED@base rc=1 → GREEN@tip rc=0), no-ff merge `2c2e75cd`, **merge #17**.
   - `merge-task.sh t42-backend tbot-backend` → **GATE PASS: t42-backend VERIFIED**
     (RED@base = 5 failing tests → GREEN@tip = 13 passing), no-ff merge `9b8c83f`,
     **merge #18**. Neither triggered the every-5-merges integration sweep.
   Both repro scripts are pinned to their merge commit (`SOURCE_REV`), not to the
   branch, so the integration re-gate can still materialize them after the branch is
   deleted — the same correction T2.2 applied to `t22.sh`.
3. **Deploy — DEFERRED to T7.3, by explicit operator decision (asked and confirmed
   in-session).** T4.1 is still IN_PROGRESS on `manager-web` and T2.1 on
   `main/tbot-server`, so a per-task `redeploy-web.sh` / `deploy-vps.sh` would bounce
   production twice for one batched wave of admin changes — the same reasoning
   recorded for T2.3. Blast radius of deferring is small: everything shipped here is
   admin-console-only (monitoring view staleness guards, the ESP fallback operator
   page's device picker, two backend read endpoints). No lesson runtime, wire
   contract, or robot-facing path is touched, so nothing here changes what a live
   lesson does. `backup-db.sh`, `deploy-vps.sh`, `smoke-vps.sh`, the post-restart MCP
   port-pin re-check, the robot (MAC …`ac:20`) reconnect confirmation, and
   `redeploy-web.sh` + lesson-studio load all move to T7.3.
4. **Re-test on main.** After both merges, on the main checkouts:
   - manager-web: `check-lesson-assignment-ui-contracts.mjs` and
     `check-lesson-sd-sync-ui.mjs` PASS; `npm run test:lesson-studio` EXIT=0.
   - ESP server: console/nudge/local-sample-demo/generation-retry/device-token
     suites **42 passed**.
   - backend: `typecheck` + `lint` clean; `npm test` **5341 passed | 627 skipped,
     0 failed**; the T4.2 specs 16 passed.
   - both pinned repros re-run against the main checkouts: `t42` EXIT=0,
     `t42-backend` EXIT=0 (13 passed).
   - **full ESP server suite on main** (`python3 -m pytest` in `main/tbot-server`):
     **13 failed / 3587 passed / 7 skipped**. None of the 13 are T4.2's: the failing
     files are `scaleout_deploy_topology` (3), `tvideo_farm_cross_repo_fixture` (4),
     `http_server` nginx proxies (2), `nginx_generation_cache_runtime` (1),
     `google_live_client` (1), `flattened_cinematic_contract` (1) and
     `benchmark_google_live_audio_runtime` (1) — none import
     `config/device_token_client` or the console handler. Proven, not assumed: the
     same four files re-run at `6326a899` (the commit immediately BEFORE the T4.2
     merge) produce the identical 9 failures. The set differs from the T0.1 baseline
     (12 failed / 3454 passed) because of merges that landed between T0.1 and here,
     not because of this task.
   No production smoke was run — nothing was deployed (step 3).

## Post-merge close-out

- **Repro robustness.** `t42-backend.sh` originally hard-failed when the throwaway
  PostgreSQL database was absent, which would have made every future integration
  re-gate report T4.2 as a regression on any box that had not run it before. A
  missing database is an unprovisioned box, not a regression: the repro now creates
  the empty database (the spec builds and drops its own schema) and hard-fails only
  when the server itself is unreachable — so a skipped postgres suite still cannot
  be mistaken for a passing fix. Proven by dropping `tbot_t42_insights` and re-running
  from scratch: `provisioned empty test database: tbot_t42_insights`, 13 passed,
  exit 0. The pinned assertions (`SOURCE_REV`) are untouched, so the gate verdict
  still stands on the same specs.
- **Security finding routed to T6.4 (HIGH), not fixed here.**
  `LessonAssignmentConsoleHandler._html` interpolates the device list into an inline
  `<script>` with `json.dumps`, which does not escape `</`; the registry key is the
  raw `device-id` websocket header and nothing validates it as a MAC. Confirmed by
  execution — a device registering
  `device-id: x</script><img src=x onerror=alert(1)>` gets a literal `</script>` into
  the operator page. **Pre-existing**: the previous
  `json.dumps(self._connected_device_ids())` had the identical hole, and T4.2 changed
  the payload shape without changing the escaping. Injection review of the ESP
  `core/api/*` handlers is T6.4's stated remit, so it is routed there (§5) rather
  than widening a merged task's scope; the fix is one line
  (`.replace("</", "<\\/")`) plus a sweep of the sibling handlers.
5. **Remove the worktree.** Both worktrees verified clean and both branches verified
   merged (`git merge-base --is-ancestor <branch> main`) before removal; local
   branches deleted. No remote branch existed for either (never pushed;
   `merge-task.sh` deliberately does not push, and pushing `tbot-backend` main
   auto-deploys on Render — that stays with T7.2).
