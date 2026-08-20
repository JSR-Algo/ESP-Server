# T6.5 ESP Operations/Admin Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear the owned T6.5 ESP simulation, assignment-console identity, and deploy-safety blockers without changing lesson runtime, renderer-v5, firmware, hardware, production, or release status.

**Architecture:** Make the simulation runtime image a two-stage local build from the checked-out `Dockerfile-server-base` and `Dockerfile-server`, with explicit override support but no implicit private-registry dependency. Keep assignment-console identity a synchronous, read-only projection of the existing mint cache, honoring expiry and never inventing presence or authentication. Integrate only R5's two deploy commits, then verify that candidate env parsing never evaluates or prints secrets and that remote compose operations target only the server service while preserving database/web container identities and rollback images.

**Tech Stack:** Bash, Docker Compose, Dockerfiles, Python 3, pytest, manager-web Node contract scripts.

---

### Task 1: Reproduce and lock the simulation image-supply failure

**Files:**
- Modify: `docs/docker/lesson-e2e-sim/up.sh`
- Test: `main/tbot-server/tests/test_lesson_e2e_sim_image_supply.py`

- [ ] Run the exact compose teardown and `./up.sh --rebuild` path to record the unavailable historical base-image failure.
- [ ] Add tests asserting that the default path builds a checkout-local dependency image before the runtime overlay, while an explicit `TBOT_SERVER_BASE_IMAGE` remains supported.
- [ ] Run the focused test and confirm it fails against `65138dbc`.
- [ ] Implement the smallest shell change: derive a local base tag, build `Dockerfile-server-base` when no override is supplied, then pass that exact tag to `Dockerfile-server`.
- [ ] Re-run the focused test, exact build/up/run path, and verify no private pull is attempted by the default path.

### Task 2: Repair assignment-console identity projection

**Files:**
- Modify: `main/tbot-server/config/device_token_client.py`
- Modify: `main/tbot-server/core/api/lesson_assignment_console_handler.py`
- Test: `main/tbot-server/tests/test_lesson_assignment_console.py`
- Test: `main/tbot-server/tests/test_device_token_client.py`

- [ ] Run `/Users/manhhodinh/Documents/TBOT/lesson-prod/repros/t42.sh` and retain the pinned resolved/unresolved/expired failures.
- [ ] Trace the live mint-cache value and expiry representation through `mint_device_token`, `cached_device_uuid`, and console rendering.
- [ ] Add a regression for the current cache representation if the pinned repro does not isolate it.
- [ ] Implement a read-only TTL-respecting lookup that returns only a valid backend UUID; leave websocket presence, auth, and network behavior unchanged.
- [ ] Re-run t42 and the focused console/token suites.

### Task 3: Independently integrate and review R5 deploy safety

**Files:**
- Integrate: commit `9d2e5669`
- Integrate: commit `0b9ee6a4`
- Review/modify only if required: `deploy/*.sh`, `deploy/validate-env.py`, `deploy/tests/test_deploy_safety.py`, `deploy/README.md`

- [ ] Inspect both commits for scope, secret exposure, shell evaluation, protected-service recreation, rollback image retention, and branch-ancestry contamination.
- [ ] Cherry-pick the two commits in order and confirm the resulting diff contains only their commit-local deploy/docs changes.
- [ ] Run deploy safety tests first; add failing tests for any uncovered secret-safe or service-only preflight gap.
- [ ] Apply only bounded fixes supported by a red regression.
- [ ] Run connect/deploy preflight and smoke dry-runs exclusively with fixtures/fake commands; do not contact production.

### Task 4: Release-grade verification and handoff

**Files:**
- Update: this plan's checkboxes only if useful; do not edit release status.

- [ ] Run the exact T5.3 build/up/run path from zero scenario containers.
- [ ] Run t42, deploy-script tests, focused ESP/admin suites, manager contract suites, and the full `main/tbot-server` pytest suite.
- [ ] Run `py_compile` on changed Python files, shell syntax checks, `git diff --check`, and an independent final diff review.
- [ ] Commit the bounded changes on `codex/t65-esp-ops-admin` and hand off the branch/commit, local image strategy, exact results, residuals, and T7.3 prerequisites.
