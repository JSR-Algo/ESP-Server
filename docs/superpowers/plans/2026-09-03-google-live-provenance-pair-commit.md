# Google Live Provenance Pair Commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mixed-time public provenance reads with pointer-bound immutable generation commits while preserving exact `commands.jsonl` and `commands.txt` bytes.

**Architecture:** Store each complete pair in a hidden generation directory and atomically publish one canonical JSON pointer as the commit identity. Preflight, final verification, CAS, and rollback bind the pointer before and after reading immutable generation files, then validate public compatibility views against that generation.

**Tech Stack:** Python 3, `os.open`/`dir_fd`, `fcntl.flock`, SHA-256, canonical JSON, pytest fault injection.

---

### Task 1: Pair pointer and secure generation directory

**Files:**
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing pointer tests**

Test canonical valid pointer bytes and rejection of non-canonical JSON, unsafe generation names, mismatched fixed filenames, invalid hashes, wrong schema, invalid entry count, invalid spec summary, pointer replacement, hardlink, and ABA inode restoration.

- [ ] **Step 2: Verify RED**

Run `python3 -m pytest tests/test_google_live_command_runner.py -q -k 'pair_pointer or pointer_aba'`. Expect failure because pointer helpers do not exist.

- [ ] **Step 3: Implement pointer records and parser**

Add `PAIR_SCHEMA = "google-live-command-provenance-pair.v1"`, a `[0-9a-f]{32}` generation grammar, immutable `PairPointer` and pointer-snapshot records, plus canonical `_render_pair_pointer()` and `_parse_pair_pointer()`. Require exact fields, fixed filenames derived from the generation, valid SHA-256 values, non-negative entry count, and a SHA-256 spec-digest summary.

- [ ] **Step 4: Bind the generation directory securely**

Create/open `.commands.jsonl.generations` relative to the retained parent FD using `mkdirat`, `O_DIRECTORY|O_NOFOLLOW`, exact-name enumeration, and owner/mode/inode validation. Never use `Path.mkdir` or an absolute reopen.

- [ ] **Step 5: Verify GREEN**

Re-run the command from Step 2 and require all selected tests to pass.

### Task 2: Immutable generation and pointer-last publication

**Files:**
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing crash-boundary tests**

Parameterize failures at `after_generation_jsonl`, `after_generation_projection`, `after_public_jsonl`, `after_public_projection`, `before_pointer`, and `after_pointer`. A committed read must return exactly the old or new complete pair, public bytes must retain their old format, and no mixed pair may authorize success.

- [ ] **Step 2: Verify RED**

Run `python3 -m pytest tests/test_google_live_command_runner.py -q -k 'generation_commit_crash or public_view_format'`. Expect failure because publication has no generation pointer.

- [ ] **Step 3: Implement immutable generation writes**

Create both generation files with `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0400`, bounded writes, file `fsync`, and post-write regular/owner/mode/link validation. Return pinned identity and SHA-256; never overwrite a generation name.

- [ ] **Step 4: Implement pointer-last publication and bootstrap**

Under both locks, write/fsync the generation pair, replace/fsync public views, then atomically replace/fsync `.commands.jsonl.pair` as the commit point. Record mutation identity across post-rename `fsync` errors. If no pointer exists, bootstrap only from a stable complete legacy public pair or both absent. Add pointer and generation paths to control-path alias checks.

- [ ] **Step 5: Verify GREEN**

Re-run the command from Step 2 and require all selected tests to pass.

### Task 3: Pointer-bound reads, CAS rollback, recovery, and cleanup

**Files:**
- Modify: `main/tbot-server/scripts/google_live_command_runner.py`
- Test: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Write failing authority-read tests**

Mutate JSONL after its last public check, mutate projection at the inverse boundary, continuously alternate views, swap/unlink/hardlink/ABA the pointer, and install a newer valid pointer before rollback. Assert preflight never executes the child, final verification never publishes success, and rollback never overwrites a newer generation.

- [ ] **Step 2: Verify RED**

Run `python3 -m pytest tests/test_google_live_command_runner.py -q -k 'committed_pair or pointer_swap or alternating_public or newer_generation'`. Expect the mixed-time cases to fail.

- [ ] **Step 3: Implement `_read_committed_pair_at()`**

Snapshot the pointer, open both fixed generation files relative to the pinned generation FD with `O_NOFOLLOW|O_NONBLOCK`, enforce size/regular/owner/mode/link constraints, verify pointer hashes and entry metadata, then reopen the pointer and require identical bytes and full inode/ctime/mode/owner/link identity. Validate both public views equal the generation contents.

- [ ] **Step 4: Replace every authoritative pair read**

Use the helper in preflight, initial commit state, final publish verification, rollback expected-current CAS, and rollback final verification. Pointer CAS mismatch is a rollback conflict; never move pointer or public views backward over a newer generation.

- [ ] **Step 5: Implement bounded repair and cleanup**

Under both locks, repair public views only from a valid current pointer generation. Delete only fixed-grammar files proven unreachable from old and current pointers; preserve unknown files. Retain at most current plus rollback generation during a transaction.

- [ ] **Step 6: Verify runner GREEN**

Run `python3 -m pytest tests/test_google_live_command_runner.py -q`. Require all lock, concurrency, `fsync`, rollback, legacy, and new generation tests to pass.

### Task 4: Production verification and commit

**Files:**
- Verify: `main/tbot-server/scripts/google_live_command_runner.py`
- Verify: `main/tbot-server/tests/test_google_live_command_runner.py`

- [ ] **Step 1: Run combined regression**

Run `python3 -m pytest tests/test_google_live_command_runner.py tests/test_google_live_release_gate.py tests/test_google_live_reliability.py -q`.

- [ ] **Step 2: Run full Task 7 regression**

Run `python3 -m pytest tests/test_google_live_deterministic_evidence.py tests/test_google_live_command_runner.py tests/test_google_live_release_gate.py tests/test_google_live_reliability.py -q`.

- [ ] **Step 3: Run static checks**

Run `python3 -m py_compile scripts/google_live_command_runner.py scripts/google_live_release_gate.py tests/test_google_live_command_runner.py tests/test_google_live_release_gate.py`, then `python3 -m ruff check --select E9,F63,F7,F82 scripts/google_live_command_runner.py scripts/google_live_release_gate.py tests/test_google_live_command_runner.py tests/test_google_live_release_gate.py`, then `git diff --check` and `git status --short`.

- [ ] **Step 4: Review and commit**

Review public-format compatibility, pointer order, FD cleanup, crash recovery, generation retention, privacy, and control-path aliasing. Commit runner and focused tests with `git commit -m "fix: commit provenance pairs atomically"`.
