# Task 07 Physical TFT Software Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three fail-closed, software-only validators for the Course Mode Task 07 local physical-TFT materializer receipt, preflight, and attended evidence ledger.

**Architecture:** Dependency-free Python CLIs validate distinct immutable JSON contracts. The preflight may run only exact local read-only `git`, `docker image inspect`, and `docker compose config` commands; the receipt and ledger validators execute no commands. None of the tools can open serial, contact a robot, trigger a lesson, start or stop containers, or change production state.

**Tech Stack:** Python 3.11+, pytest, JSON, SHA-256, Markdown, Docker Compose configuration rendering through a fixed argv

---

## File Map

- Create `main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py`: strict materializer receipt validation and idempotency comparison.
- Create `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`: receipt identity, schema, redaction, and deterministic-output tests.
- Create `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`: no-device local configuration and identity preflight.
- Create `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`: fake-command, fail-closed, path, secret, and mutation-denial tests.
- Modify `docs/docker/course-mode-physical-tft/up.sh`: label the exact-SHA image with the verified compiled materializer path.
- Modify `main/tbot-server/tests/test_course_mode_physical_tft_compose.py`: assert the immutable build label and unchanged config-only/start behavior.
- Create `main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py`: attended manifest validation and TFT sub-lane verdict.
- Create `main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py`: cue, artifact, redaction, rest, privacy, and stop-condition tests.
- Create `docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json`: redacted `TFT_BLOCKED` attended manifest template.
- Create `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`: operator-facing ledger and exact non-triggering commands.
- Modify `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md`: link the tooling while retaining `PHYSICAL_BLOCKED` and Task 08 lock.

### Task 1: Implement the Materializer Receipt Verifier

**Files:**
- Create: `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`
- Create: `main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py`

- [ ] **Step 1: Write strict receipt tests**

Define one valid receipt fixture with these exact fields and no extras:

```python
VALID_RECEIPT = {
    "result": "pass",
    "deviceSuffix": "AC:20",
    "lessonKey": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "rendererId": "teebot-lesson-renderer.v4",
    "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
    "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
    "manifestChecksum": "a" * 64,
    "cueCount": 8,
    "conversationPresent": False,
}
```

Test valid single receipt, two semantically identical receipts, every identity
field mismatch, uppercase/short manifest hash, extra/missing field, full MAC,
secret/token/transcript-shaped field, non-object JSON, and differing rerun
receipt. Assert failures contain stable field/reason codes but never rejected
values.

- [ ] **Step 2: Run the focused test and confirm RED**

Run from `main/tbot-server`:

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_receipt_verify.py
```

Expected: FAIL because the verifier module does not exist.

- [ ] **Step 3: Implement the dependency-free verifier**

Expose `validate_receipt(document: object) -> list[str]`,
`validate_receipt_pair(first: object, second: object | None) -> list[str]`, and
`main(argv: list[str] | None = None) -> int`. The validation functions return
sorted stable reason codes; `main` maps an empty reason list to exit `0` and any
reason to exit `1`.

CLI:

```text
course_mode_physical_tft_receipt_verify.py RECEIPT [--rerun-receipt RECEIPT]
```

Success output:

```json
{"cueCount":8,"deviceSuffix":"AC:20","lessonKey":"course-mode-pilot-cat-ball","rendererId":"teebot-lesson-renderer.v4","valid":true}
```

Invalid input exits `1` and emits sorted reason codes without echoing input.

- [ ] **Step 4: Run focused tests and compile check**

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_receipt_verify.py
python3 -m compileall -q scripts/course_mode_physical_tft_receipt_verify.py
```

Expected: all tests PASS; compile exits `0`.

### Task 2: Implement the No-Device Local Preflight

**Files:**
- Create: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`
- Create: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Modify: `docs/docker/course-mode-physical-tft/up.sh`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_compose.py`

- [ ] **Step 1: Write fake-command and schema tests**

Build temporary fake `git` and `docker` executables that log argv and return:

- exact clean backend root/full SHA;
- exact SHA-tagged image inspection containing a label
  `com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/course-mode-local-materializer.js`,
  exact `org.opencontainers.image.revision`, and
  `com.tbot.course-mode.build-source=reviewed-clean-git-worktree`;
- rendered Compose JSON matching the overlay contract.

The valid expected-input fixture must include exact synthetic IDs:

```python
EXPECTED_IDS = {
    "courseId": "70000000-0000-4000-8000-000000000003",
    "lessonId": "70000000-0000-4000-8000-000000000004",
    "deviceId": "70000000-0000-4000-8000-000000000005",
    "assignmentId": "70000000-0000-4000-8000-000000000006",
    "adultOperatorId": "70000000-0000-4000-8000-000000000007",
}
```

Assert the subprocess log contains only the five approved command forms. Add
tests that fail on Docker mutation verbs, command override input, dirty/SHA
mismatch, default/stale image, missing label, wrong project/resource prefix,
backend port not loopback 3000, wrong ESP URL, non-local or production-like
origin, wrong materializer command, writable fixture mount, active generic seed,
wrong assignment identity, candidate/hash drift, protected hash drift, secret in
result, output-root escape, symlink escape, malformed JSON, and missing field.

Monkeypatch socket creation, URL opening, serial imports, and `/dev` enumeration
to raise immediately so the valid path proves none are attempted.

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py
```

Expected: FAIL because the preflight module does not exist.

- [ ] **Step 3: Label the exact-SHA image at build time**

Extend the existing `docker build` argv in `up.sh` with:

```bash
--label "com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/course-mode-local-materializer.js"
```

Update the existing fake-Docker assertion in
`tests/test_course_mode_physical_tft_compose.py` to require this exact label
between `--pull=false` and `-f`. Keep the existing in-image
`require('node:fs').accessSync('/app/dist/lessons/course-mode/course-mode-local-materializer.js')`
verification and both config-only/start assertions unchanged.

- [ ] **Step 4: Implement exact input and result contracts**

Use a required JSON input with this exact field/type contract:

```python
REQUIRED_INPUT_FIELDS = {
    "schemaVersion": int,              # exactly 1
    "backendWorktree": str,            # absolute reviewed root
    "backendSha": str,                 # 40 lowercase hex
    "backendImage": str,               # exact tag derived from backendSha
    "composeProject": str,             # tbot-course-mode-physical-tft
    "backendBaseUrl": str,             # http://127.0.0.1:3000
    "espHttpUrl": str,                 # http://host.docker.internal:8003
    "assetOrigin": str,                # approved local-lab HTTP URL ending /
    "otaUrl": str,                     # approved local-lab HTTP URL
    "websocketUrl": str,               # approved local-lab ws:// URL
    "endpointAuthority": str,          # approved-local-task07-lab-route
    "syntheticIds": dict,              # exactly EXPECTED_IDS
    "productionCandidateTarget": dict, # immutable reviewed production target
    "historicalInstallationProvenance": dict, # historical NVS evidence only
    "sessionNvsBaseline": dict,         # exact supplied pre-install NVS digest
    "activeLabApp": dict,              # exact supplied temporary lab identity
    "protectedTest": dict,             # exact path/hash from the design
    "outputDirectory": str,            # concrete timestamped task-artifact path
    "sessionStartedAt": str,           # ISO-8601 UTC
}
```

Require the caller to provide the concrete approved lab IP/URLs; never infer
them from current networking. `productionCandidateTarget` must equal the three
reviewed firmware/application/bundle values in the design.
`historicalInstallationProvenance` must retain the prior installation's exact
preserved-NVS digest but must not constrain the current session.
`sessionNvsBaseline` must contain only a caller-supplied lowercase
`beforeInstallSha256`; the preflight must not read a device or infer the value.
`activeLabApp` must bind exact firmware
main `3df15a712a9e7ed656a1a9f240bd2ac2bf8ba989` and caller-supplied lowercase
application/bundle SHA-256 values; preliminary artifact hashes must not be
hard-coded. `protectedTest` must equal the protected path/hash in the design.
The implementation validates the fixed argv, hashes the protected file
directly, captures Compose JSON only in memory, sets fixed sentinel values for
required secret-shaped Compose variables rather than reading them from the
environment, redacts secret-bearing keys, and atomically writes one JSON result.

The attended ledger separately requires `sessionNvsPreservation` with exact
fields `phase`, `beforeInstallSha256`, `afterInstallSha256`, and
`afterRestoreSha256`. Enforce the monotonic phase matrix: `NOT_OBSERVED` has
three null hashes; `PRE_INSTALL_BASELINE` has only before-install;
`POST_INSTALL` has equal before/after-install; and `POST_RESTORE` has all three
equal. Bind before-install to the preflight result and require `POST_RESTORE`
for `TFT_PASS`. Never change `task07Verdict` from `PHYSICAL_BLOCKED`.

- [ ] **Step 5: Run focused tests and static denial scans**

```bash
python3 -m pytest -q \
  tests/test_course_mode_physical_tft_preflight.py \
  tests/test_course_mode_physical_tft_compose.py
python3 -m compileall -q scripts/course_mode_physical_tft_preflight.py
rg -n "serial|list_ports|/dev/|socket\.|urlopen|requests\.|httpx|docker.*(up|run|start|restart|stop|down|rm|exec|logs|pull|build)" scripts/course_mode_physical_tft_preflight.py
```

Expected: tests PASS; compile exits `0`; scan matches only explicit forbidden
validation constants/tests and no executable device/network/mutation path.

### Task 3: Implement the Attended TFT Ledger Validator

**Files:**
- Create: `main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py`
- Create: `main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py`
- Create: `docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json`

- [ ] **Step 1: Write BLOCKED, FAIL, and TFT PASS tests**

Create a complete synthetic fixture under a temporary
`task-artifacts/course-mode-task07/tft-20260822T000000Z-ac20/` directory. Include two receipt
files, preflight result, redacted capture artifacts, and one direct frame per cue.

Test:

- the committed empty template validates as `TFT_BLOCKED`;
- a complete attended fixture validates as `TFT_PASS` while retaining
  `task07Verdict=PHYSICAL_BLOCKED`;
- arbitrary hash-valid preflight/receipt JSON fails semantic validation, while
  two validator-valid semantically equal receipts pass;
- PASS/FAIL physical-action declarations match the verdict and stop phase, and
  malformed or reversed UTC operator timestamps fail closed;
- any physical stop marker yields `TFT_FAIL`, including an early-stop prefix
  with explicit phase, unavailable-evidence reasons, safe-state, and
  power-isolation outcomes; preflight/receipt bindings are required only when
  the recorded stop phase could have produced them;
- missing/misordered cue, log-only cue, one-adult verdict, disagreement, missing
  visual checklist field, missing rest assertion, privacy marker, full MAC,
  secret/transcript/audio content, invalid or production-like local-lab
  endpoint, absolute external path, symlink escape,
  missing file, hash/size drift, wrong preflight/receipt binding, or unsupported
  numeric-limit claim fails closed.

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_ledger_validate.py
```

Expected: FAIL because the ledger validator and template do not exist.

- [ ] **Step 3: Add the redacted ledger template**

The committed template must use `TFT_BLOCKED`, empty artifact/cue evidence,
the production-target identity, the active lab source with null unqualified
application/bundle hashes, and synthetic identities from the design, all
physical-action fields
`NOT_PERFORMED`, and outstanding blockers for attended capture, direct visual
evidence, calibrated instruments, approved limits, E-stop/TP_EN, rollback,
recovery, and the remaining Task 07 lanes. It must contain no full MAC, secrets,
child data, raw audio, or transcript.

- [ ] **Step 4: Implement deterministic ledger validation**

Expose
`validate_ledger(document: object, *, repository_root: Path) -> dict[str, object]`
and `main(argv: list[str] | None = None) -> int`. The library result contains
`valid`, `tftVerdict`, `task07Verdict`, and sorted `reasons`; `main` exits `0`
only for a structurally valid BLOCKED, FAIL, or TFT PASS document.

CLI:

```text
course_mode_physical_tft_ledger_validate.py LEDGER --repository-root ROOT
```

The tool hashes existing files only, never follows a symlink outside the task
artifact root, never copies evidence, never executes a command, and emits sorted
stable reasons. `TFT_PASS` requires all eight ordered cues and two matching adult
verdicts with direct frame references. `TFT_FAIL` validates every retained
artifact but applies phase-aware preflight/receipt binding requirements and
requires explicit reasons for unavailable evidence. Available preflight and
receipt files are parsed and semantically validated through the shared receipt
and endpoint contracts; hashes alone are insufficient. Physical actions and UTC
operator chronology are verdict/phase checked. It must always emit
`task07Verdict=PHYSICAL_BLOCKED` unless a future separately reviewed design
changes the broader gate.

- [ ] **Step 5: Run focused tests and validate the committed template**

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_ledger_validate.py
python3 scripts/course_mode_physical_tft_ledger_validate.py \
  ../../docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json \
  --repository-root ../..
python3 -m compileall -q scripts/course_mode_physical_tft_ledger_validate.py
```

Expected: tests PASS; template result is `valid=true`, `tftVerdict=TFT_BLOCKED`,
`task07Verdict=PHYSICAL_BLOCKED`; compile exits `0`.

### Task 4: Add the Non-Triggering Attended Run Ledger

**Files:**
- Create: `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`
- Modify: `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md`

- [ ] **Step 1: Document exact software-only commands**

Record these phases separately:

1. config-only exact-image build through `up.sh --config-only`;
2. no-device preflight input creation and validation;
3. operator-authorized stack start as an explicitly out-of-tooling action;
4. capture of the first and idempotent materializer receipt;
5. receipt verification;
6. attended capture/trigger as explicitly out-of-tooling actions;
7. ledger completion and offline validation;
8. task-owned cleanup only after evidence review, also out of tooling.

Do not include a serial command, nudge command, spoken-trigger automation, Docker
start/stop/removal command, rollback command, or secret value. Instead, point to
the master prompt and require fresh point-of-use authorization for those steps.

- [ ] **Step 2: Add exact validator commands**

From `main/tbot-server`:

```bash
TFT_SESSION_DIR="/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07/tft-$(date -u +%Y%m%dT%H%M%SZ)-ac20"

python3 scripts/course_mode_physical_tft_preflight.py \
  --input "$TFT_SESSION_DIR/preflight-input.json" \
  --output "$TFT_SESSION_DIR/preflight-result.json"

python3 scripts/course_mode_physical_tft_receipt_verify.py \
  "$TFT_SESSION_DIR/materialize-first.json" \
  --rerun-receipt "$TFT_SESSION_DIR/materialize-rerun.json"

python3 scripts/course_mode_physical_tft_ledger_validate.py \
  "$TFT_SESSION_DIR/tft-ledger.json" \
  --repository-root /Users/manhhodinh/Documents/TBOT/robot/esp32-server
```

The runbook must instruct the operator to create the directory only after a
separate attended-session authorization. Validators reject missing directories
and never create a session directory implicitly.

- [ ] **Step 3: Record stop conditions and retained blockers**

Copy the stable stop categories from the design without adding numeric limits.
State that any physical stop requires the human safe-state procedure, while the
validator only records `STOP_REQUIRED`. Retain `PHYSICAL_BLOCKED` and Task 08
lock in both reports.

### Task 5: Run Cross-Tool Regression and Privacy Gates

**Files:**
- Verify all Task 1-4 files only.

- [ ] **Step 1: Run the complete focused suite**

From `main/tbot-server`:

```bash
python3 -m pytest -q \
  tests/test_course_mode_physical_tft_receipt_verify.py \
  tests/test_course_mode_physical_tft_preflight.py \
  tests/test_course_mode_physical_tft_ledger_validate.py \
  tests/test_course_mode_physical_tft_compose.py \
  tests/test_course_mode_task07_evidence_validate.py \
  tests/test_physical_smoke_audit.py \
  tests/test_course_mode_contract.py \
  tests/test_course_mode_runtime_integration.py
```

Expected: all tests PASS without Docker service start, serial, device, or network
access. The Compose test may render configuration only.

- [ ] **Step 2: Run compile, redaction, and protected-file checks**

From `robot/esp32-server`:

```bash
python3 -m compileall -q \
  main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/scripts/course_mode_physical_tft_preflight.py \
  main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py

rg -n -i "authorization|bearer|jwt|secret|password|private.?key|transcript|utterance|raw.?speech|audio.?data|pronunciation.?score|(?:[0-9a-f]{2}:){5}[0-9a-f]{2}" \
  docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md \
  docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json

test "$(shasum -a 256 main/tbot-server/tests/test_lesson_voice_output_discipline.py | awk '{print $1}')" = \
  08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3

git diff --check
```

Expected: compile exits `0`; redaction scan contains only explicit policy text
and no value-bearing secret/private-data record; protected hash check and diff
check exit `0`.

- [ ] **Step 3: Review containment**

Confirm the diff contains only the planned scripts, tests, and documentation.
Do not modify the protected test or inspect/change Farm, T54, T65, external
worktrees, firmware, Docker runtime state, serial/device state, production
configuration, assignment, flags, or secrets. Commit only if the supervising
operator separately authorizes a commit.
