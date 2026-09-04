# Google Live Runtime Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind every approved Google Live command to immutable candidate source, tracked resources, interpreter, and dependency bytes while preserving public provenance and existing Google Live behavior.

**Architecture:** Extend the existing Git-SHA source bootstrap into a bounded candidate snapshot containing approved Python and non-Python tracked resources. Add an internal runtime-closure manifest that binds interpreter and dependency file digests; execute only from verified snapshot/import roots, while preserving evidence paths and run-specific inputs. Keep the public `commands.jsonl` and `commands.txt` formats unchanged.

**Tech Stack:** Python 3.14, `subprocess`, `tarfile`, `importlib`, Git archive/object reads, SHA-256, pytest, Ruff.

---

### Task 1: Define Closure Manifest And Canonical Resource Inventory

**Files:**
- Create: `main/tbot-server/tests/fixtures/google_live_runtime_closure_manifest.json`
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing manifest tests**

Add tests that load the manifest from a candidate Git SHA and reject a missing file, wrong Git blob, unsupported platform, duplicate member, path traversal, symlink, non-regular member, or digest mismatch. Add one passing fixture entry for a Python script and one tracked WAV/config resource.

- [ ] **Step 2: Run the manifest tests to verify RED**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py -k 'runtime_closure_manifest or resource_inventory' -q` and confirm the new loader is absent or rejects the expected fixture.

- [ ] **Step 3: Implement canonical manifest loading**

Add a strict loader that reads the manifest with `git show <candidate_sha>:<manifest_path>`, verifies canonical JSON, platform/runtime identity, sorted unique relative paths, bounded counts/sizes, and SHA-256 values. Reuse the existing trusted Git session and candidate-head checks; never read the worktree copy as authority.

- [ ] **Step 4: Run focused GREEN tests**

Run the same focused command and then `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py -q`.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/scripts/google_live_command_runner.py main/tbot-server/tests/test_google_live_command_runner.py main/tbot-server/tests/fixtures/google_live_runtime_closure_manifest.json
git commit -m "feat: define Git-bound Google Live runtime closure"
```

### Task 2: Snapshot Tracked Resources From Git

**Files:**
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing snapshot tests**

Add tests for WAV/config/JSON fixture replacement, missing member, oversized member, archive byte/file/depth limits, duplicate member, and symlink/device rejection. The test mutates the worktree resource after the Git archive is captured and asserts the child reads the archived bytes.

- [ ] **Step 2: Run RED**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py -k 'resource_snapshot or archive_limit or resource_swap' -q` and verify the child still reads mutable worktree content.

- [ ] **Step 3: Implement bounded Git resource archive**

Extend the archive command to include only manifest-listed tracked paths, stream stdout into a hard-bounded buffer, reject malformed tar members before extraction, materialize with `O_EXCL|O_NOFOLLOW`, mode `0400`, file and directory `fsync`, and verify each member digest against the Git-bound manifest. Keep logs, evidence, credentials, generated outputs, and caches excluded.

- [ ] **Step 4: Implement path resolution rules**

In the bootstrap, resolve candidate-root-relative source/resource paths under the immutable snapshot; preserve evidence-root and declared absolute input/output paths. Set `__file__` to the immutable project-root path for archived candidate scripts so `SERVER_ROOT` resolves inside the snapshot.

- [ ] **Step 5: Run GREEN and regression tests**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py -q` and the real nested-soak/analyzer regression tests.

- [ ] **Step 6: Commit**

```bash
git add main/tbot-server/scripts/google_live_command_runner.py main/tbot-server/tests/test_google_live_command_runner.py
git commit -m "feat: snapshot Google Live candidate resources from Git"
```

### Task 3: Bind Dependency Closure And Import Origins

**Files:**
- Modify: `main/tbot-server/tests/fixtures/google_live_python_executable_manifest.json`
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Modify: `main/tbot-server/scripts/google_live_release_gate.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_release_gate.py`

- [ ] **Step 1: Write failing dependency-origin tests**

Add tests that install or expose a changed `websockets`, `numpy`, `PyYAML`, or `opuslib_next` module on an ambient site path and assert execution fails or uses only the manifest-bound file digest. Add tests for user-site, `.pth`, editable-install, namespace-package, and changed-version metadata drift.

- [ ] **Step 2: Run RED**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py main/tbot-server/tests/test_google_live_release_gate.py -k 'dependency or import_origin or site_packages' -q` and confirm ambient imports are currently accepted.

- [ ] **Step 3: Inventory and record dependency files**

Update the internal manifest with the actual reachable distribution names, versions, import roots, file counts, byte totals, and canonical per-file SHA-256 entries for the approved `darwin-arm64-cp314` environment. Do not add credentials or user data.

- [ ] **Step 4: Enforce import closure in the bootstrap**

Build the child import path from the immutable candidate snapshot, pinned standard-library roots, and manifest-approved distribution roots only. Disable user site and `.pth` injection, reject origins outside those roots, and fail before the command can make network or hardware calls.

- [ ] **Step 5: Bind closure in release validation**

Require the candidate-Git manifest digest and runtime profile in release validation, and reject command provenance produced with a different dependency closure while preserving public provenance fields and grammar.

- [ ] **Step 6: Run GREEN and commit**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py main/tbot-server/tests/test_google_live_release_gate.py -q`, then commit:

```bash
git add main/tbot-server/scripts/google_live_command_runner.py main/tbot-server/scripts/google_live_release_gate.py main/tbot-server/tests/test_google_live_command_runner.py main/tbot-server/tests/test_google_live_release_gate.py main/tbot-server/tests/fixtures/google_live_python_executable_manifest.json
git commit -m "feat: bind Google Live dependency closure"
```

### Task 4: Close Nested Execution And Resource Escape Paths

**Files:**
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing nested-process tests**

Add tests for the real `google_live_robot_soak.py` absolute analyzer path, nested relative scripts, alternate interpreter, `-m`, `-c`, outside-root script, mutable imported resource, and absolute evidence/data arguments. Assert candidate source/resource bytes are used and outside/module/alternate paths fail closed.

- [ ] **Step 2: Run RED**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_command_runner.py -k 'nested or analyzer or alternate_interpreter or module_escape' -q` and verify the escape cases fail on current code.

- [ ] **Step 3: Implement nested mapping**

Intercept only the approved interpreter and Python script forms. Map scripts under the original command cwd or candidate project root to immutable snapshot paths, preserve non-source arguments, and reject alternate interpreters, `-m`, `-c`, and paths outside approved roots. Ensure `SERVER_ROOT` points to the snapshot for archived candidate scripts.

- [ ] **Step 4: Run GREEN**

Run the focused nested command and the complete command-runner suite.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/scripts/google_live_command_runner.py main/tbot-server/tests/test_google_live_command_runner.py
git commit -m "fix: close Google Live nested source escapes"
```

### Task 5: End-To-End Closure Gate And Documentation

**Files:**
- Modify: `main/tbot-server/scripts/google_live_release_gate.py`
- Modify: `main/tbot-server/tests/test_google_live_release_gate.py`
- Modify: `main/tbot-server/docs/google-live-smoke.md`
- Modify: `main/tbot-server/docs/google-live-robot-validation.md`

- [ ] **Step 1: Write failing release and privacy tests**

Add synthetic tests that mutate each tracked resource, dependency file, manifest, and source member between snapshot and execution; assert release `FAIL` with a safe integrity code and no raw path/content leakage. Add a passing synthetic dry-run with the complete closure manifest.

- [ ] **Step 2: Run RED**

Run `python3 -m pytest main/tbot-server/tests/test_google_live_release_gate.py -k 'closure or resource or dependency' -q` and verify mutations are currently accepted.

- [ ] **Step 3: Implement final closure binding**

Bind closure digest/support paths through the existing release gate, verify every artifact and manifest from the candidate Git SHA, reject aliases and changed closure identities, and retain the existing exact command order, secret assignment, stdin policy, privacy scanner, and rollback behavior.

- [ ] **Step 4: Update runbooks**

Document the internal closure manifest, approved `darwin-arm64-cp314` profile, synthetic-only verification, fail-closed unsupported platforms, and the known baseline-equivalent deterministic opus-origin hang with its A/B evidence requirement. Do not document live credentials or hardware actions as automated steps.

- [ ] **Step 5: Run final controller gate**

```bash
python3 -m pytest \
  tests/test_google_live_command_runner.py \
  tests/test_google_live_release_gate.py \
  tests/test_google_live_reliability.py \
  tests/test_google_live_deterministic_evidence.py -q
python3 -m py_compile scripts/google_live_command_runner.py scripts/google_live_release_gate.py
python3 -m ruff check scripts/google_live_command_runner.py scripts/google_live_release_gate.py --select E9,F63,F7,F82
git diff --check
git status --short
```

Expected result: all relevant tests pass; if the known opus-origin node still hangs, record exact nodeid plus baseline/current timeout evidence and exclude only that node with an explicit residual-risk note.

- [ ] **Step 6: Commit**

```bash
git add main/tbot-server/scripts/google_live_release_gate.py main/tbot-server/tests/test_google_live_release_gate.py main/tbot-server/docs/google-live-smoke.md main/tbot-server/docs/google-live-robot-validation.md
git commit -m "feat: enforce Google Live runtime closure at release"
```
