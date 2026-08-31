# Course Mode Candidate 12 Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Enforce the approved trusted-operator precondition, freeze candidate course-mode-2026-08-31.12, and complete all software qualification required before an attended flash can be considered.

**Architecture:** Keep one canonical release gate. Add one candidate-bound operator-attestation artifact that production CLI runs validate before and after lane execution, then rebuild the exact web image and run Quick, Full, isolated PostgreSQL 16, evidence, and review gates.

**Tech Stack:** Python 3.11, pytest, macOS sandbox-exec, POSIX filesystem APIs, Git, Docker, PostgreSQL 16, JSON evidence.

**Constraint:** Work on /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main; do not introduce a second gate version or release worktree.

---

## File Map

- Modify main/tbot-server/scripts/course_mode_release_gate.py and its tests to bind operator evidence.
- Create main/tbot-server/scripts/course_mode_operator_attestation.py and its tests to create the evidence safely.
- Modify scripts/course_robot_e2e_gates.sh and tests/test_course_robot_e2e_gates_script.py to preserve one explicit evidence path.
- Generate candidate .12, its evidence tree, and docs/qa/ad-hoc/2026-08-31-course-mode-candidate-12-software.md.

### Task 1: Bind Operator Evidence to Production Gate Runs

**Files:**
- Modify: main/tbot-server/scripts/course_mode_release_gate.py
- Test: main/tbot-server/tests/test_course_mode_release_gate.py

- [ ] **Step 1: Write failing production-precondition tests**

Add tests equivalent to:

~~~python
def test_production_gate_blocks_without_operator_attestation(candidate_file: Path) -> None:
    result = gate.run_gate(candidate_file, "quick", runtime_root=_runtime_root(candidate_file))
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_accepts_exact_operator_attestation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: ())
    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )
    assert result["verdict"] == "PASS"
    assert result["operatorAttestationSha256"] == hashlib.sha256(attestation.read_bytes()).hexdigest()
~~~

Add cases for wrong candidate ID, host, effective UID, gate SHA, threat-model value, false confirmations, extra or missing keys, symlink, path outside evidenceRoot, writable path, and replacement after a lane.

- [ ] **Step 2: Verify RED on Python 3.11**

~~~bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py -k 'operator_attestation or operator_precondition'
~~~

Expected: the new cases fail because no binding exists.

- [ ] **Step 3: Implement the exact binding schema**

Add these definitions:

~~~python
OPERATOR_ATTESTATION_ENV = "COURSE_MODE_OPERATOR_ATTESTATION"
OPERATOR_ATTESTATION_KEYS = {
    "candidateId", "createdAt", "effectiveUid", "gateSha", "hostName",
    "sameUidThreatModel", "schemaVersion", "trustedOperatorAccountConfirmed",
    "untrustedAutomationStoppedConfirmed",
}


@dataclass(frozen=True)
class OperatorAttestationBinding:
    path: Path
    sha256: str
~~~

Implement _operator_attestation_binding(candidate, source) with read_secure_regular, exact keys, schemaVersion 1, current hostname and effective UID, exact candidate and admin SHA, sameUidThreatModel equal to malicious-process-excluded, and both confirmations exactly True. Require a one-link regular file owned by the effective UID beneath evidenceRoot with no group or other write bits.

- [ ] **Step 4: Enforce pre/post binding**

For production runs where lanes is None, bind before release_state_matches. Invalid evidence returns failedLane operator-precondition. Re-read after each lane and before report writing; require the same digest and semantic fields. Include operatorAttestationSha256 only in a bound PASS report. Keep custom injected lanes usable by unit tests.

- [ ] **Step 5: Verify GREEN on Python 3.11 and 3.14**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py -k 'operator_attestation or operator_precondition or candidate_metadata'
python3 -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py -k 'operator_attestation or operator_precondition or candidate_metadata'
~~~

Expected: both pass without skips.

- [ ] **Step 6: Commit**

~~~bash
git add main/tbot-server/scripts/course_mode_release_gate.py main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix: bind course mode gate to operator attestation"
~~~

### Task 2: Create the Attestation Safely

**Files:**
- Create: main/tbot-server/scripts/course_mode_operator_attestation.py
- Create: main/tbot-server/tests/test_course_mode_operator_attestation.py

- [ ] **Step 1: Write failing CLI tests**

Cover success and refusal when confirmations are missing, output exists, output escapes evidenceRoot, candidate validation fails, a parent is a symlink, or the candidate changes during creation. Output keys must exactly match Task 1 and bind current host, UID, UTC time, candidate ID, and admin SHA.

- [ ] **Step 2: Verify RED**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_operator_attestation.py
~~~

Expected: import or collection fails because the creator does not exist.

- [ ] **Step 3: Implement the fail-closed CLI**

Accept only:

~~~text
--candidate PATH
--output PATH
--confirm-trusted-operator-account
--confirm-untrusted-automation-stopped
~~~

Validate the candidate, require a new path beneath evidenceRoot, and write canonical JSON using directory-FD traversal, O_NOFOLLOW, O_CREAT plus O_EXCL, mode 0444, fsync, and a final candidate metadata recheck. Remove only a newly owned incomplete output on failure.

- [ ] **Step 4: Verify and commit**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_operator_attestation.py
$PY311 main/tbot-server/scripts/course_mode_operator_attestation.py --help
git add main/tbot-server/scripts/course_mode_operator_attestation.py main/tbot-server/tests/test_course_mode_operator_attestation.py
git commit -m "feat: record course mode operator precondition"
~~~

Expected: tests pass and help contains no secrets or production endpoints.

### Task 3: Preserve Evidence Through the Canonical Launcher

**Files:**
- Modify: scripts/course_robot_e2e_gates.sh
- Modify: tests/test_course_robot_e2e_gates_script.py

- [ ] **Step 1: Write and run a failing launcher test**

Assert COURSE_MODE_OPERATOR_ATTESTATION survives env -i while OPERATOR_ATTESTATION, COURSE_OPERATOR_CONFIRMATION, and TBOT_OPERATOR_ATTESTATION are discarded.

~~~bash
python3 -m pytest -q tests/test_course_robot_e2e_gates_script.py -k operator_attestation
~~~

Expected: FAIL because the path is currently dropped.

- [ ] **Step 2: Add the one allowed variable**

~~~sh
COURSE_MODE_OPERATOR_ATTESTATION="${COURSE_MODE_OPERATOR_ATTESTATION-}" \
~~~

Do not pass the ambient environment wholesale.

- [ ] **Step 3: Verify and commit**

~~~bash
python3 -m pytest -q tests/test_course_robot_e2e_gates_script.py
git add scripts/course_robot_e2e_gates.sh tests/test_course_robot_e2e_gates_script.py
git commit -m "fix: preserve operator evidence in course gate launcher"
~~~

### Task 4: Qualify and Review the Source

- [ ] **Step 1: Run release and manifest qualification**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_candidate_manifest.py main/tbot-server/tests/test_course_mode_release_gate.py main/tbot-server/tests/test_course_mode_operator_attestation.py
~~~

Expected: zero failures and zero skips.

- [ ] **Step 2: Run Course Mode runtime qualification**

~~~bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
COURSE_MODE_BACKEND_ROOT=/Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342
COURSE_MODE_BACKEND_SHA=c9a0fe08f6e30004a6b193a9a7cbf715ab699634
export COURSE_MODE_BACKEND_ROOT COURSE_MODE_BACKEND_SHA
$PY311 -m pytest -q tests/test_course_mode_curriculum_e2e.py tests/test_course_mode_runtime_integration.py
~~~

Expected: every focused Course Mode test passes with no skips.

- [ ] **Step 3: Run repository checks**

~~~bash
git diff --check
git status --short
git fsck --no-progress
~~~

Expected: clean committed source and no object errors.

- [ ] **Step 4: Run independent spec and security reviews**

Spec review checks docs/superpowers/specs/2026-08-31-course-mode-release-gate-threat-model-design.md. Security review checks traversal, replacement, endpoint binding, Python 3.11, and ensures no claim says hashes stop malicious same-UID mutation. Every finding requires a fix and re-review.

### Task 5: Build the Web Image and Freeze Candidate .12

**Files:**
- Generate: /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.12.json
- Generate: /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.12/00-candidate-validator.json

- [ ] **Step 1: Confirm exact clean repositories**

~~~bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server status --short
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock status --short
~~~

Expected: no output.

- [ ] **Step 2: Build and inspect the committed web image**

Use docs/docker/course-mode-physical-tft/up.sh --config-only with the existing reviewed backend and local-only required environment. It may build images and validate Compose but must not start services.

~~~bash
ESP_SHA=$(git rev-parse HEAD)
WEB_IMAGE="local/tbot-server-web:course-mode-physical-tft-${ESP_SHA}"
docker inspect --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}} {{index .Config.Labels "org.opencontainers.image.source"}}' "$WEB_IMAGE"
~~~

Expected: immutable ID, exact revision, approved source URL.

- [ ] **Step 3: Create .12 from .11**

Update only candidate ID, real UTC creation and expiry, exact admin SHA, web image reference and ID, and .12 evidence root. Preserve reviewed backend, firmware, database, curriculum, Node, browser, Python, and ESP-IDF descriptors unless validation proves real drift.

- [ ] **Step 4: Validate before execution**

~~~bash
CANDIDATE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.12.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.12
mkdir -p "$EVIDENCE"
$PY311 main/tbot-server/scripts/course_mode_candidate_manifest.py "$CANDIDATE" > "$EVIDENCE/00-candidate-validator.json"
/usr/bin/jq -e '.status == "pass" and .reasons == []' "$EVIDENCE/00-candidate-validator.json"
~~~

Expected: exit 0 and reasons is empty.

### Task 5A: Bind Candidate Creation to Real Assembly Time

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_candidate_manifest.py`
- Modify: `main/tbot-server/scripts/course_mode_candidate_manifest.py`
- Regenerate: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.12.json`
- Regenerate: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.12/00-candidate-validator.json`

- [ ] **Step 1: Write failing firmware-evidence ordering tests**

Add focused cases proving that evidence created before the candidate is
accepted, evidence created exactly at candidate creation is accepted, and
evidence created after candidate creation is rejected with
`firmware.evidenceManifestPath`.

- [ ] **Step 2: Verify RED on Python 3.11 and 3.14**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_candidate_manifest.py -k firmware_evidence_creation
python3 -m pytest -q main/tbot-server/tests/test_course_mode_candidate_manifest.py -k firmware_evidence_creation
~~~

Expected: the before-candidate case fails and the after-candidate case passes
under the reversed legacy comparison.

- [ ] **Step 3: Implement the assembly-time ordering**

Change the firmware evidence time predicate to require:

~~~python
evidence_created <= candidate_created < candidate_expires
~~~

Keep all existing canonical timestamp, manifest identity, source, artifact,
reproducibility, safety, and expiry checks unchanged.

- [ ] **Step 4: Verify and commit the validator change**

~~~bash
$PY311 -m pytest -q main/tbot-server/tests/test_course_mode_candidate_manifest.py
python3 -m pytest -q main/tbot-server/tests/test_course_mode_candidate_manifest.py -k firmware_evidence_creation
git diff --check
git add main/tbot-server/scripts/course_mode_candidate_manifest.py main/tbot-server/tests/test_course_mode_candidate_manifest.py
git commit -m "fix: bind candidate creation after firmware evidence"
~~~

Expected: zero failures and zero skips.

- [ ] **Step 5: Regenerate candidate .12 with truthful UTC creation**

Set `createdAt` to the actual UTC freeze time after the exact admin commit and
image IDs already bound in `.12`. Set `expiresAt` later than `createdAt` using
the existing release validity window. Do not change firmware bytes or forge a
new firmware manifest.

- [ ] **Step 6: Regenerate and verify the validator evidence**

~~~bash
$PY311 main/tbot-server/scripts/course_mode_candidate_manifest.py "$CANDIDATE" > "$EVIDENCE/00-candidate-validator.json"
/usr/bin/jq -e '.status == "pass" and .reasons == []' "$EVIDENCE/00-candidate-validator.json"
~~~

Expected: exit 0, `reasons` is empty, candidate `createdAt` is later than the
firmware evidence timestamp and the exact admin commit timestamp, and the
candidate/evidence hashes are recorded again before Task 6.

### Task 6: Run Quick and Full Gates

- [ ] **Step 1: Obtain fresh software-gate confirmation**

Ask the user to confirm the current account is trusted and untrusted automation capable of changing the candidate or /private/tmp is stopped. This does not authorize flash, serial, HIL, or motion.

- [ ] **Step 2: Create operator evidence**

~~~bash
$PY311 main/tbot-server/scripts/course_mode_operator_attestation.py --candidate "$CANDIDATE" --output "$EVIDENCE/00-operator-attestation.json" --confirm-trusted-operator-account --confirm-untrusted-automation-stopped
export COURSE_MODE_OPERATOR_ATTESTATION="$EVIDENCE/00-operator-attestation.json"
~~~

- [ ] **Step 3: Run Quick without competing staging jobs**

~~~bash
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode quick --report "$EVIDENCE/01-quick-gate.json"
/usr/bin/jq -e '.verdict == "PASS" and .failedLane == null' "$EVIDENCE/01-quick-gate.json"
~~~

- [ ] **Step 4: Run Full in isolation**

~~~bash
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode full --report "$EVIDENCE/02-full-gate.json"
/usr/bin/jq -e '.verdict == "PASS" and .failedLane == null' "$EVIDENCE/02-full-gate.json"
~~~

Expected: all lanes pass, no skips or retained paths, and candidate and attestation digests remain stable.

### Task 7: Run Isolated PostgreSQL 16

- [ ] **Step 1: Start a task-owned cluster**

Use one isolated PostgreSQL 16 server and two distinct databases. Refuse to start if the selected loopback port is already occupied.

~~~bash
PG_PORT=55471
PG_ROOT="$EVIDENCE/postgres"
PG_DATA="$PG_ROOT/data"
PG_SOCKET="$PG_ROOT/socket"
mkdir -p "$PG_SOCKET"
if /usr/sbin/lsof -nP -iTCP:"$PG_PORT" -sTCP:LISTEN | /usr/bin/grep -q .; then
  echo "selected PostgreSQL test port is already occupied" >&2
  exit 1
fi
/opt/homebrew/bin/initdb -D "$PG_DATA" --auth=trust --username=operator --no-locale --encoding=UTF8
/opt/homebrew/bin/pg_ctl -D "$PG_DATA" -l "$PG_ROOT/postgres.log" \
  -o "-h 127.0.0.1 -p $PG_PORT -k $PG_SOCKET" start
/opt/homebrew/bin/createdb -h 127.0.0.1 -p "$PG_PORT" -U operator course_mode_primary
/opt/homebrew/bin/createdb -h 127.0.0.1 -p "$PG_PORT" -U operator course_mode_materializer
~~~

Do not use an existing developer or production database.

- [ ] **Step 2: Set distinct numeric-loopback URLs**

~~~bash
export COURSE_MODE_V2_TEST_DATABASE_URL="postgresql://operator@127.0.0.1:${PG_PORT}/course_mode_primary"
export COURSE_MODE_TEST_DATABASE_URL="$COURSE_MODE_V2_TEST_DATABASE_URL"
export COURSE_MODE_ROLLBACK_TEST_DATABASE_URL="postgresql://operator@127.0.0.1:${PG_PORT}/course_mode_materializer"
export DATABASE_URL="$COURSE_MODE_ROLLBACK_TEST_DATABASE_URL"
unset PRODUCTION_DATABASE_URL
~~~

This matches the gate topology: V2 and curriculum share one identity; materializer and rollback share the second identity; the two database identities differ. Save only redacted host, port, database, PostgreSQL version, and backend PID evidence.

- [ ] **Step 3: Run live-db**

~~~bash
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode live-db --report "$EVIDENCE/03-live-db-gate.json"
/usr/bin/jq -e '.verdict == "PASS" and .failedLane == null' "$EVIDENCE/03-live-db-gate.json"
~~~

- [ ] **Step 4: Stop the task-owned cluster**

~~~bash
/opt/homebrew/bin/pg_ctl -D "$PG_DATA" -m fast stop
if /usr/sbin/lsof -nP -iTCP:"$PG_PORT" -sTCP:LISTEN | /usr/bin/grep -q .; then
  echo "PostgreSQL test listener remains after shutdown" >&2
  exit 1
fi
~~~

Keep only review evidence.

### Task 8: Audit Evidence and Decide Software GO

- [ ] **Step 1: Index redacted evidence**

Record SHA-256 and size for candidate, attestation, validator, Quick, Full, live-db, web and backend images, firmware manifest and app, and threat-model spec. Reject links, special files, secrets, tokens, child audio, and raw transcripts.

- [ ] **Step 2: Run final independent reviews**

Verify one unused .12 identity, common binding, no skips or retained paths, exact image labels, firmware offset 0x20000, size 3,637,200, partition 4,128,768, explicit same-UID exclusion, and no deploy, production, serial, HIL, or flash action.

- [ ] **Step 3: Write the redacted software record**

Create docs/qa/ad-hoc/2026-08-31-course-mode-candidate-12-software.md with exact commits, image IDs, test counts, PostgreSQL topology, evidence hashes, limitations, and findings. Never claim 100% bug-free.

- [ ] **Step 4: Issue GO or NO-GO**

Only if validator, Quick, Full, live-db, evidence audit, and both reviews are clean, report SOFTWARE_GO_FOR_ATTENDED_FLASH. Otherwise report SOFTWARE_NO_GO with exact blockers. Software GO does not authorize flashing.

- [ ] **Step 5: Require fresh flash-point confirmation**

Before esptool, serial, reboot, or robot action, ask the user to confirm robot MAC, serial port, candidate SHA, offset 0x20000, app size, and preserved partitions.

---

## Plan Self-Review

- Spec coverage includes trusted account, same-UID exclusion, operator evidence, endpoint wording, all gates, live-db, evidence, and physical separation.
- Scope remains one gate plus one evidence creator; no broker, VM, second version, deployment, or robot mutation.
- Names are consistent: COURSE_MODE_OPERATOR_ATTESTATION, operatorAttestationSha256, and operator-precondition.
- Candidate history .8 through .11 remains unchanged; only .12 is created.
