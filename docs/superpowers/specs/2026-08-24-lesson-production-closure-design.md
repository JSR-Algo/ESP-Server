# Lesson Production Closure Design

## Objective

Close the remaining lesson-production gaps with fresh, reproducible evidence and
declare production readiness only when no hardware or deployment waiver remains.
The release must be reversible and must preserve existing production data,
lesson contracts, and unrelated user work.

## Current Release Gaps

The campaign records contain contradictory completion states. Production
verification succeeded on 2026-08-21, while the backend deployment task remains
partial, T5.4/H1 remains waived, and `F-T25-01` records a lesson that can remain
non-terminal after a post-start failure. Historical evidence is useful for
diagnosis but is not sufficient to close these gates.

## Closure Strategy

Use a strict staged closure rather than accepting the existing waiver:

1. Freeze deployed identities, environment topology, database state, worker
   state, device identity, and rollback targets.
2. Reproduce and close `F-T25-01` test-first across ESP/backend ownership. Every
   lesson that has started must reach exactly one durable terminal disposition,
   including disconnect, restart, stale-session, and asset-recovery failures.
3. Resolve the backend migration/pre-deploy blocker without bypassing checksum or
   canonical-data validation. Back up first, repair only identified canonical
   rows, rerun preflight, then activate web and lesson workers deliberately.
4. Run the complete H1 physical gate with the approved robot, Android build,
   serial device, SD card, production assignment, and evidence collector.
5. Run production happy-path, mid-step transport fault, and power-cycle recovery
   checks against immutable deployed revisions.
6. Observe lesson metrics and logs for a bounded post-deploy window, then issue a
   GO/NO-GO verdict and retain tested rollback artifacts.

## Safety And Ownership

- Preserve all unrelated dirty files and branches.
- Acquire an exclusive robot/phone/deploy lease before physical or production
  mutation; abort if another operator, lesson, deploy, or serial holder appears.
- Do not weaken migration, checksum, authentication, capability, or rollout
  gates to obtain a green result.
- Take a named database backup before canonical-data repair or migration.
- Keep lesson workers disabled until web health, schema, and endpoint smoke pass.
- Stop on the first unexplained production or hardware anomaly and preserve the
  evidence before attempting another run.

## Required Evidence

- Exact local and deployed SHAs, image identifiers, configuration identities,
  database backup reference, migration output, and rollback commands.
- A red-green regression proving terminal disposition behavior for
  `F-T25-01`, plus focused and integration suites on repository `main`.
- Backend preflight, migration, health, authenticated lesson endpoint, worker,
  and observability evidence from the activated release.
- H1 capture containing assignment/session IDs, all lesson steps, audio/render
  markers, parent/mobile progress, completion, and safe return to conversation.
- Fault and power-cycle captures proving one terminal result, bounded reconnect,
  safe rest/display ownership, and a usable subsequent lesson.
- A post-deploy watch summary with error counters, correlation IDs, dropped-event
  metrics, and explicit GO/NO-GO rationale.

## Production-Ready Gate

The final verdict is GO only when all of the following are true:

- backend web and required lesson workers run the reviewed release;
- migrations and canonical data are verified with a tested rollback path;
- `F-T25-01` is closed by behavior and regression evidence;
- H1 passes without waiver on the physical robot and approved mobile build;
- happy, disconnect, restart, and power-cycle paths reach correct terminal state;
- production metrics and logs show no unresolved lesson regression during the
  bounded observation window;
- the previous deploy and database recovery procedure remain executable.

Any missing item produces NO-GO or CONDITIONAL-GO, never a production-ready
claim.
