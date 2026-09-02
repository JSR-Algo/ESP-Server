# Course Mode Assignment Copy-Only Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent successful Task 4 assignment lanes from leaving hard-linked media that blocks candidate snapshot cleanup.

**Architecture:** Keep the secure release-gate deleter unchanged. Change only the disposable Task 4 fixture's derivative replacement operation from hard-link-first to copy-only, then prove the source contract and candidate-bound Full gate clean up without retained paths.

**Tech Stack:** Node.js CommonJS, `node:fs/promises`, Node test runner, Python/pytest release-gate tests, Docker Compose local E2E.

---

### Task 1: Make Task 4 derivative replacement copy-only

**Files:**
- Modify: `main/manager-web/scripts/task4-assignment-fixture.test.cjs`
- Modify: `docs/docker/task4-admin-assignment/bootstrap.cjs`

- [ ] **Step 1: Write the failing source-contract test**

Extend `Task 4 assignment fixture declares exact graph and READY derivative invariants` with assertions that the fixture imports `copyFile` but not `link`, calls `replaceWithCopy` for both preview and device derivatives, and implements replacement as remove-then-copy without a hard-link fallback:

```js
assert.match(source, /const \{ copyFile, mkdir, open, rm, stat \} = require\('node:fs\/promises'\);/);
assert.doesNotMatch(source, /\blink\b|replaceWithLink/);
assert.equal((source.match(/await replaceWithCopy\(/g) || []).length, 2);
assert.match(source, /async function replaceWithCopy\(source, destination\) \{[\s\S]*await stat\(source\);[\s\S]*await rm\(destination, \{ force: true \}\);[\s\S]*await copyFile\(source, destination\);[\s\S]*\}/);
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test --test-name-pattern='READY derivative invariants' scripts/task4-assignment-fixture.test.cjs
```

Expected: FAIL because `bootstrap.cjs` still imports/calls `link` and defines `replaceWithLink`.

- [ ] **Step 3: Implement copy-only replacement**

In `bootstrap.cjs`, change the import and both call sites:

```js
const { copyFile, mkdir, open, rm, stat } = require('node:fs/promises');

await replaceWithCopy(join(MEDIA_ROOT, 'templates', `${durationMs}.mp4`), previewPath);
await replaceWithCopy(trgbTemplate, devicePath);
```

Replace the helper with:

```js
async function replaceWithCopy(source, destination) {
  await stat(source);
  await rm(destination, { force: true });
  await copyFile(source, destination);
}
```

- [ ] **Step 4: Run focused and related Node tests**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test --test-name-pattern='READY derivative invariants' scripts/task4-assignment-fixture.test.cjs
node --test scripts/lesson-studio-e2e-environment.test.cjs scripts/lesson-studio-compose.test.cjs scripts/task4-assignment-fixture.test.cjs
```

Expected: focused test PASS; related suite reports 22 tests plus the new assertions with zero failures.

- [ ] **Step 5: Run release-gate regression tests**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
python3 -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py
python3 -m py_compile main/tbot-server/scripts/course_mode_release_gate.py
git diff --check
```

Expected: all release-gate tests PASS; compile and diff checks exit zero.

- [ ] **Step 6: Commit the implementation**

```bash
git add docs/docker/task4-admin-assignment/bootstrap.cjs \
  main/manager-web/scripts/task4-assignment-fixture.test.cjs
git commit -m "fix(course-mode): copy assignment derivatives"
```

### Task 2: Review and verify candidate cleanup end to end

**Files:**
- Preserve: `task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.20/02-full-gate.json`
- Preserve or quarantine: `/private/tmp/course-mode-lane-rtmjn_70`
- Create: next immutable candidate JSON and evidence directory under `task-artifacts/course-mode-production-readiness/`

- [ ] **Step 1: Run independent spec and quality reviews**

Review the implementation commit for copy-only scope, absence of hard-link fallback, byte/hash validation preservation, and no relaxation of `_remove_owned_tree`. Resolve every actionable finding with test-first follow-up commits and repeat both reviews.

- [ ] **Step 2: Build and inspect the exact candidate web image**

Archive the clean reviewed Git SHA into a fresh `/private/tmp/tbot-web-<sha>.*` build root, build `Dockerfile-web` with `VUE_APP_NEST_AUTH_DISABLED=false`, and pin these labels:

```text
org.opencontainers.image.revision=<reviewed full SHA>
org.opencontainers.image.source=https://github.com/JSR-Algo/ESP-Server.git
com.tbot.course-mode.build-source=reviewed-clean-git-worktree
```

Expected: `linux/arm64`, exact revision/source/authority labels, and a new immutable image ID.

- [ ] **Step 3: Assemble and validate the next immutable candidate**

Copy the prior candidate contract values, changing only candidate ID/timestamps/evidence root, reviewed admin SHA, and exact web image reference/ID. Run `course_mode_candidate_manifest.py`, create a fresh operator attestation, require validator PASS, then set candidate/validator/attestation files to mode `0444`.

- [ ] **Step 4: Run Quick and Full gates**

Run Quick first. For Full, use the healthy standard stack on `3100/8102` and distinct assignment ports derived for the new candidate, with all three port variables, runtime root, candidate-bound images/worktrees, author E2E readiness, local asset origin, and local robot bridge placeholder set explicitly.

Expected Full report: exactly 20 ordered lanes, every `exitCode` is `0`, `failedLane` is `null`, `verdict` is `PASS`, and neither `retainedPaths` nor `retainedOwner` exists. Lock successful reports to `0444`.

- [ ] **Step 5: Prove cleanup behavior and remove disposable resources**

After Full, require no Docker containers, volumes, or networks for the assignment project and no gate-owned retained path in the report. Hash the `.20` failure report and retained-tree metadata, move the retained `.20` tree into the existing Course Mode quarantine area if preservation is still required, and never delete unrelated `/private/tmp` or workspace content.

- [ ] **Step 6: Continue production-readiness gates**

Only after Full PASS, run the isolated PostgreSQL 16 live-db gate with two distinct numeric-loopback database identities, audit immutable software evidence, and obtain two independent final reviews. Issue `SOFTWARE_GO_FOR_ATTENDED_FLASH` only after all software gates pass; request fresh point-of-use confirmation before any flash, serial, HIL, reboot, or robot motion.
