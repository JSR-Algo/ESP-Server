# Course Mode Firmware cJSON Gitlink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the candidate-bound firmware handler archive cJSON from either a regular ESP-IDF Git tree or the exact cJSON gitlink commit used by ESP-IDF v5.5.4.

**Architecture:** Keep the current staged `CJSON_DIR` contract and candidate schema. Resolve the exact `components/json/cJSON` entry from the pinned ESP-IDF superproject commit; archive a normal tree from the superproject, or validate the initialized submodule repository and archive the gitlink commit tree through the same bounded Git-object copier. Never copy cJSON working-tree bytes.

**Tech Stack:** Python 3.11, pytest, Git tree/gitlink objects, POSIX filesystem metadata, Bash host-native firmware tests.

---

### Task 1: Support The Real ESP-IDF cJSON Gitlink

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Add a real gitlink fixture helper**

Create a standalone cJSON Git repository, remove the fixture's existing
regular-tree cJSON path, and add the repository at `components/json/cJSON` with
`git -c protocol.file.allow=always submodule add`. Commit the ESP-IDF
superproject, update `candidate["tools"]["espIdf"]["commit"]`, and assert:

```python
entry = _git(esp_idf, "ls-tree", "HEAD", "components/json/cJSON")
assert entry.split()[:2] == ["160000", "commit"]
assert entry.split()[2] == pinned_commit
```

- [ ] **Step 2: Write and verify the primary RED regression**

Add `test_firmware_handler_stages_exact_cjson_gitlink_commit`. Create commit A
containing `pinned gitlink bytes`, add it as the gitlink, then create and check
out commit B in the submodule with different bytes. Stage the handler and
assert the staged `cJSON.c` contains commit A's bytes, lives inside the stage,
and cleanup succeeds.

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'stages_exact_cjson_gitlink_commit'
```

Expected: FAIL because the current implementation accepts only object type
`tree`, while the real topology provides mode `160000`, type `commit`.

- [ ] **Step 3: Add fail-closed gitlink regressions while still RED**

Cover: missing initialized submodule directory; absent pinned commit in the
submodule object database; `.git` redirection away from the canonical
`<esp-idf>/.git/modules/components/json/cJSON`; symlinked or non-directory
canonical Git directory; and superproject entry mode/type pairs other than
`040000 tree` or `160000 commit`. Each test expects
`ValueError("candidate archive failed")` and proves no stage directory remains.

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'cjson_gitlink'
```

Expected: valid gitlink support remains RED; invalid fixtures fail for their
intended gate checks, not fixture setup or imports.

- [ ] **Step 4: Extend the bounded archive helper**

Add an optional keyword-only Git directory while keeping existing callers on
the current default path:

```python
def _archive_git_tree(
    source: Path,
    treeish: str,
    destination: Path,
    state: dict[str, int],
    *,
    git_dir: Path | None = None,
) -> None:
    base = [str(_manifest.TRUSTED_GIT_EXECUTABLE)]
    if git_dir is not None:
        base.append(f"--git-dir={git_dir}")
    base.extend([
        "--no-replace-objects", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "credential.helper=",
    ])
```

Preserve all shared path, mode, symlink, entry-count, per-file byte, and total
byte checks. `_archive_repository` must behave exactly as before.

- [ ] **Step 5: Resolve the exact tree or gitlink entry**

Replace the cJSON `rev-parse`/`cat-file -t` block with bounded
`git ls-tree -z <commit> -- components/json/cJSON`. Parse exactly one
NUL-terminated `<mode> <type> <40-hex>\t<exact path>` record. Accept only:

```text
040000 tree   -> archive from the ESP-IDF superproject
160000 commit -> archive from the initialized cJSON submodule repository
```

Reject duplicate or unterminated records, a different path, non-lowercase hex,
and every other mode/type combination.

- [ ] **Step 6: Implement the gitlink branch**

Derive:

```python
submodule_root = source / "components/json/cJSON"
submodule_git_file = submodule_root / ".git"
submodule_git_dir = source / ".git/modules/components/json/cJSON"
```

Require the root and Git directory to be absolute canonical real directories,
owned by the effective UID, and not group/world writable. Require `.git` to be
a regular non-symlink file with the same owner/mode restrictions, parse it with
a bounded read, and require its declared target to equal `submodule_git_dir`.

Use explicit `--git-dir=<submodule_git_dir>` after validation. Require
`<gitlink>^{commit}` to resolve exactly to the gitlink OID, resolve
`<gitlink>^{tree}` to a lowercase 40-hex tree ID, require type `tree`, then call:

```python
_archive_git_tree(
    submodule_root,
    tree_id,
    cjson_parent / "cJSON",
    state,
    git_dir=submodule_git_dir,
)
```

Do not inspect, compare, or check out the submodule working-tree HEAD. The
superproject gitlink is the authority.

- [ ] **Step 7: Verify GREEN and the real path**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'cjson_gitlink or firmware_handler or archive_repository'
```

Expected: all selected tree and gitlink tests pass with cleanup intact.

Then stage the real `.24` handler candidate without executing firmware:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
umask 022
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -I -s -c 'import json,pathlib,sys; sys.path.insert(0,str(pathlib.Path.cwd())); from scripts import course_mode_release_gate as m; c=json.loads(pathlib.Path("/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.24.json").read_text()); lane=next(x for x in m.FULL_LANES if x.name=="firmware-handler"); stage=m.stage_execution_candidate(c,(lane,)); print((pathlib.Path(stage.candidate["tools"]["espIdf"]["root"])/"components/json/cJSON/cJSON.c").is_file()); print(stage.cleanup())'
```

Expected: `True` twice. This diagnostic does not mutate `.24` evidence or run
firmware.

- [ ] **Step 8: Commit the minimal source fix**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): stage cjson gitlink commit"
```

Only those two files change. Firmware, production data, and `.24` remain
unchanged.

### Task 2: Qualify Source And Freeze Candidate `.25`

**Files:**
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.24.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.24/01-quick-gate.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/diagnostics/course-mode-2026-08-31.24-runtime/firmware-facing.json`
- Create: candidate `.25` and its software evidence

- [ ] **Step 1: Run source qualification**

Run the complete canonical 599-test command, combined 53-test Node command,
`py_compile`, relevant `node --check` commands, `git diff --check`, clean
`git status --short`, and `git fsck --no-progress`. Require zero failures and
zero skips; dangling objects are informational only.

- [ ] **Step 2: Obtain two independent reviews**

Reviewer one checks spec compliance, tree/gitlink behavior, candidate-schema
stability, and absence of firmware/production/physical changes. Reviewer two
checks Git argument injection, `.git` redirection, canonical paths,
ownership/modes, symlink and TOCTOU boundaries, object identity, archive limits,
cleanup, and missing tests. Resolve findings test-first and repeat both reviews.

- [ ] **Step 3: Freeze candidate `.25`**

Build the reviewed clean HEAD for `linux/arm64` with `WEB_NODE_IMAGE=node:20`,
`VUE_APP_NEST_AUTH_DISABLED=false`, and the existing OCI labels. Copy `.24`,
changing only candidate ID/timestamps/evidence root, admin SHA, and web image
reference/ID. Preserve backend, firmware, database, curriculum, and tool
identities. Validate, attest, and lock artifacts to `0444`, link count one.

- [ ] **Step 4: Resume gates sequentially with runtime umask `022`**

Run `.25` Quick; the exact ordered firmware-facing lanes `firmware-renderer`,
`firmware-handler`, `firmware-backward-compatibility`, and
`cross-contract-parity`; Full with all 20 lanes including real assignment NEW
then ROLLBACK through one shared capsule and isolated `.25` resources; and the
isolated PostgreSQL 16 live-db gate using two distinct loopback databases. Stop
at the first failure and preserve its report.

- [ ] **Step 5: Audit and issue the software verdict**

Require immutable validator, attestation, Quick, Full, and live-db evidence;
exact candidate/repository/tool/image/firmware identities; correct lane order;
matching attestation hashes; no retained paths/resources; and no secrets,
tokens, audio, or transcripts. Obtain two final independent reviews.

Only then may the verdict be `SOFTWARE_GO_FOR_ATTENDED_FLASH`. It does not
authorize serial access, flashing, reset, HIL, or robot motion; those still
require fresh point-of-use user confirmation.
