# T5.4 Server-Only Deploy Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a self-contained, fail-closed ESP server-only release/deploy path that preserves database and web containers.

**Architecture:** Package a secret-safe dotenv validator, reviewed backup helper, and remote transaction helper with the server image. Keep `deploy-vps.sh` as the transport layer and make the remote helper own ordered preflight, bounded retention, backup, server-only Compose recreation, and invariant verification.

**Tech Stack:** Bash 3-compatible orchestration, Python 3 standard library, Docker Compose CLI, pytest fixtures.

---

### Task 1: Executable acceptance fixtures

**Files:**
- Create: `deploy/tests/test_deploy_safety.py`
- Create: `deploy/tests/fixtures/valid.env`
- Create: `deploy/tests/fixtures/invalid-bare-token.env`

- [ ] **Step 1: Write parser tests** that require quoted/multiline dotenv values to pass, a bare trailing token to fail by line/key without echoing the value, and command substitution to fail.
- [ ] **Step 2: Run `python3 -m pytest deploy/tests/test_deploy_safety.py -v`** and verify failure because `deploy/validate-env.py` does not exist.
- [ ] **Step 3: Write remote transaction fixtures** with fake Docker/Compose state for active/rollback images, disk thresholds, and stable DB/web IDs.
- [ ] **Step 4: Run the focused suite again** and verify failure because `deploy/server-only-remote.sh` does not exist.

### Task 2: Secret-safe env validation

**Files:**
- Create: `deploy/validate-env.py`
- Modify: `deploy/deploy-vps.sh`

- [ ] **Step 1: Implement a standard-library parser** that recognizes dotenv assignments without evaluation and emits only structural diagnostics.
- [ ] **Step 2: Validate `--env-file` locally before transport** and validate the existing remote env before any remote mutation.
- [ ] **Step 3: Run `python3 -m pytest deploy/tests/test_deploy_safety.py -v`** and verify parser and pre-mutation tests pass.

### Task 3: Self-contained package and backup

**Files:**
- Modify: `deploy/package-release.sh`
- Modify: `deploy/backup-db.sh`
- Test: `deploy/tests/test_deploy_safety.py`

- [ ] **Step 1: Add a server-only packaging mode** that requires/saves only the server image and copies `backup-db.sh`, `validate-env.py`, and `server-only-remote.sh` into the release.
- [ ] **Step 2: Make database backup read the password only inside the DB container** and retain dry-run output without secret values.
- [ ] **Step 3: Run the focused suite** and verify package contents and backup command behavior pass.

### Task 4: Ordered remote transaction

**Files:**
- Create: `deploy/server-only-remote.sh`
- Modify: `deploy/deploy-vps.sh`
- Test: `deploy/tests/test_deploy_safety.py`

- [ ] **Step 1: Implement checksum, env, and disk preflight** before backup, image load, symlink switch, or Compose recreation.
- [ ] **Step 2: Implement bounded server-image cleanup** preserving the active image ID and one rollback ID, then recheck byte and percentage thresholds.
- [ ] **Step 3: Snapshot DB/web IDs, run backup, load the server archive, switch current, and execute** `compose up -d --no-deps tbot-esp32-server`.
- [ ] **Step 4: Compare protected IDs and fail if either changed.**
- [ ] **Step 5: Run the focused suite** and verify retention, low-space, exact targeting, and invariant tests pass.

### Task 5: Documentation, evidence, and gate

**Files:**
- Modify: `deploy/README.md`
- Create: `docs/qa/ad-hoc/2026-08-20-t54-deploy-safety.md`

- [ ] **Step 1: Document server-only packaging/deploy commands, thresholds, retention, env syntax, and the no-deploy-before-H1 restriction.**
- [ ] **Step 2: Run `bash -n deploy/*.sh`, `python3 -m py_compile deploy/validate-env.py`, the focused pytest suite, dry-run fixtures, and `git diff --check`.**
- [ ] **Step 3: Record exact commands/results and RED-to-GREEN evidence.**
- [ ] **Step 4: Commit the branch, request code review, fix blocking findings, and rerun the gate.**
