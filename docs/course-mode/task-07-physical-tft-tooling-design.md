# Task 07 Physical TFT Software Tooling Design

## Purpose

Add the missing software-only controls for an attended Course Mode physical-TFT
run without starting Docker services, opening serial, contacting a robot, sending
a lesson nudge, or changing production state. The tooling prepares and validates
evidence; an authorized adult operator remains solely responsible for any later
stack start, capture, voice trigger, stop, or rollback action.

This design covers only the local TFT sub-lane of Task 07. It cannot issue
`PHYSICAL_PASS`, unlock Task 08, or substitute for acoustic, power, thermal,
E-stop, rollback, recovery, comfort, or representative-lighting evidence.

## Existing Authority

- `docs/course-mode/production-ready/task-07-physical-robot-validation.md` is the
  physical release gate and supplies the mandatory stop conditions.
- `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md` records the
  current `PHYSICAL_BLOCKED` verdict, installed privacy-remediated candidate,
  safe-idle result, adult roles, and outstanding calibrated evidence.
- `docs/docker/docker-compose.course-mode-physical-tft.yml` and
  `docs/docker/course-mode-physical-tft/up.sh` define the isolated Compose
  project, exact-worktree image build, AC:20 allowlist, local backend port, and
  one-shot materializer.
- The backend local materializer emits a deterministic receipt for exactly one
  synthetic adult assignment and eight renderer-v4 cues.
- `scripts/course_mode_task07_evidence_validate.py` establishes the repository
  pattern for dependency-free, deterministic, non-executing evidence validation.

## Approaches Considered

### One end-to-end operator script

A single script could build, start, materialize, capture, trigger, validate, and
clean up. It would be convenient but would combine software validation with
Docker mutation, serial ownership, robot control, secrets, and destructive
cleanup. A validation bug could therefore become a physical action. Reject this
approach.

### One large offline validator

A single offline CLI could validate preflight, receipt, and attended evidence.
It would avoid device actions, but its schema and error handling would couple
three different lifecycles. Receipt changes could destabilize capture evidence,
and operators could not rerun the smallest relevant check. Reject this approach.

### Three focused fail-closed validators

Use separate CLIs for the materializer receipt, local software preflight, and
attended TFT evidence ledger. Each has one input contract, one deterministic
JSON result, no shared mutable state, and no robot-action capability. This is the
selected approach.

## Components

### Materializer receipt verifier

`main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py` reads one
JSON receipt file. It never invokes Docker or a database. It requires exactly:

- `result=pass`;
- device suffix `AC:20` and no full MAC address;
- lesson `course-mode-pilot-cat-ball`, version `1`;
- renderer `teebot-lesson-renderer.v4`;
- contract checksum
  `cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264`;
- layout checksum
  `e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c`;
- a 64-character lowercase manifest checksum;
- `cueCount=8` and `conversationPresent=false`.

The verifier also accepts an optional second receipt and requires byte-for-byte
semantic equality after JSON parsing. This proves idempotent materialization
without accepting timestamps, log prefixes, or extra fields. It emits only the
redacted expected identity and never echoes rejected input.

### No-device local preflight

`main/tbot-server/scripts/course_mode_physical_tft_preflight.py` validates the
software configuration immediately before an attended run. It may execute only
these locally bounded read-only commands, constructed internally rather than
accepted from user input:

1. `git -C BACKEND_ROOT rev-parse --show-toplevel`;
2. `git -C BACKEND_ROOT rev-parse HEAD`;
3. `git -C BACKEND_ROOT status --porcelain --untracked-files=all`;
4. `docker image inspect EXACT_SHA_TAGGED_IMAGE`;
5. `docker compose --project-name tbot-course-mode-physical-tft -f
   docker-compose.lesson-studio-e2e.yml -f
   docker-compose.course-mode-physical-tft.yml config --format json`.

It must not call Docker `up`, `run`, `start`, `restart`, `stop`, `down`, `rm`,
`exec`, `logs`, `pull`, or `build`. It must not enumerate or open `/dev`, import
serial libraries, create sockets, make HTTP requests, read process environments
whose names contain `SECRET`, `TOKEN`, `PASSWORD`, `PRIVATE`, or `KEY`, or write
outside the requested output beneath `task-artifacts/course-mode-task07/`.

The operator supplies only non-secret expected values:

- reviewed clean backend worktree root and full SHA;
- exact SHA-tagged backend image;
- Compose project `tbot-course-mode-physical-tft`;
- local backend `http://127.0.0.1:3000`;
- local ESP HTTP `http://host.docker.internal:8003`;
- approved robot-reachable asset origin ending `/`;
- expected OTA/WS URLs, both explicitly classified as local-lab endpoints;
- synthetic UUIDs for course, lesson, device, assignment, and adult operator;
- immutable production-candidate target firmware SHA, application SHA-256, and
  bundle-root SHA-256;
- historical installation provenance containing the previously established
  preserved-NVS SHA-256, explicitly not a current-session prerequisite;
- current-session pre-install NVS SHA-256 supplied from authorized observation,
  without reading or inferring it in this tool;
- active temporary local-lab app source SHA, application SHA-256, and bundle-root
  SHA-256, separately from the production target, plus device suffix `AC:20`;
- protected test path and SHA-256
  `08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3`;
- output directory and UTC session identifier.

The tool parses rendered Compose JSON in memory and emits a redacted receipt. It
requires the exact project/resource prefix, backend image on both runtime and
materializer services, loopback-only backend port 3000, local PostgreSQL,
AC:20-only assignment scope, one-shot compiled materializer command, read-only
fixture/assets, disabled generic seed services, pinned ESP fan-out, and no
production-like host. The preflight supplies a fixed non-secret PEM-shaped
validation fixture solely to render and validate the Compose shape; it never reads
the operator's runtime private key or real secret environment. `up.sh`
separately refuses to start unless the local runtime public and private PEMs
are a cryptographically matching pair. Secret-bearing rendered keys are
represented only as `present-redacted` in retained output.

`up.sh` adds fixed image labels naming the compiled materializer path, exact Git
revision, and reviewed-clean-worktree build provenance during the exact-SHA
build. Preflight requires the inspected image ID, the exact sole repository tag,
and every label to match, and binds the immutable image ID into its result.
Image absence, dirty or mismatched source, missing or stale image provenance,
retagging/aliasing, endpoint mismatch, wrong assignment identity, protected hash
drift, output-path escape, symlink escape, or any secret detected in output is a
preflight failure. The preflight never claims that Docker services are running
or that the robot is connected.

### Attended evidence ledger validator

`main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py` validates
a human-authored JSON manifest after an attended capture. It never starts a
capture, opens logs in follow mode, sends HTTP, invokes a nudge, plays audio,
opens serial, or controls Docker. The manifest template lives at
`docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json`.

For a complete attended lane, the manifest binds:

- the preflight result SHA-256 and two matching materializer receipt SHA-256s;
- operator and independent observer names, adult-only assertion, sole-lease
  assertion, clear-motion-envelope assertion, immediate-power-isolation
  assertion, and UTC start/end times;
- both exact firmware identities, protected hash, local endpoint identities,
  synthetic assignment UUIDs, renderer identity, and manifest checksum;
- historical installation provenance separately from the current session's
  monotonic before-install, after-install, and after-restore NVS evidence;
- capture artifact relative paths, byte counts, SHA-256s, and redaction status;
- ordered runtime markers for authenticated AC:20 WebSocket, app-ready,
  `lesson_prepare`, `lesson_start`, eight cue transitions/ACKs, completion, stop,
  and quiescent rest;
- one row for each cue, containing timestamp, cue/activity ID, expected focus and
  visual mode, operator verdict, observer verdict, and at least one image/video
  frame reference;
- final stable screen, centered head, lowered arms, no continued movement,
  chatter, binding, vibration, odor, unusual heat, unstable power, private data,
  or privacy-uplink marker.

The ordered cue contract is:

| Cue | Focus | Visual mode | Assessment |
| --- | --- | --- | --- |
| `cat-discover` | center | teach/model | no |
| `cat-meaning` | left | listen | no |
| `cat-joint-speech` | center | teach/repeat | no |
| `cat-recall` | center | listen | yes |
| `cat-transfer` | right | listen | yes |
| `ball-discover` | center | teach/model | no |
| `ball-meaning` | right | listen | no |
| `cat-delayed` | center | listen | yes |

Every cue row must explicitly assess background, teaching object, robot overlay,
caption, listening indicator, crop, overlap, z-order, focus anchor, flicker,
corruption, and reduced-motion behavior. Logs alone cannot satisfy a cue row.

For an early physical stop, `TFT_FAIL` accepts only the observed prefix of the
ordered markers and cues. It requires an explicit `stopPhase` and one
`unavailableEvidence` row with a stable nonempty reason for every preflight or
receipt artifact that phase could not have produced. `PRE_PREFLIGHT` requires no
preflight/receipt bindings; `POST_PREFLIGHT` and `POST_STACK_START` require only preflight;
`POST_FIRST_RECEIPT` adds the first receipt; `POST_RERUN_RECEIPT` and
`DURING_ATTENDED_CAPTURE` require all three. Every artifact that does exist is
still path-contained, hashed, size-checked, redaction-checked, and privacy
scanned. FAIL always requires named operator/session identity, privacy outcome,
a nonempty stop condition, and explicit `safeState` and `powerIsolation`
outcomes. It does not misrepresent unobserved cues or require final-rest PASS
assertions after a stop.

The two firmware identities cannot be conflated. `productionCandidateTarget`
remains the immutable reviewed production target. `activeLabApp` records the
temporary app actually active for the local lab run. Its source must be exact
firmware main `3df15a712a9e7ed656a1a9f240bd2ac2bf8ba989`; its application and
bundle-root SHA-256 values are supplied as exact immutable preflight inputs and
validated for lowercase SHA-256 shape, rather than hard-coded while qualification
is still in progress. BLOCKED/pre-preflight evidence may leave only those two
not-yet-qualified hashes null. PASS and every phase after preflight require them.

Mutable NVS state is not firmware identity. The historical preserved-NVS digest
`a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e`
lives only in `historicalInstallationProvenance` and describes the retained
2026-08-22 installation evidence. `sessionNvsBaseline.beforeInstallSha256` is a
caller-supplied exact lowercase digest for the current authorized session; it
may legitimately differ from the historical digest. The ledger binds that value
as `sessionNvsPreservation.beforeInstallSha256` and uses the monotonic phase enum
`NOT_OBSERVED`, `PRE_INSTALL_BASELINE`, `POST_INSTALL`, or `POST_RESTORE`.
Each phase requires all earlier observations, forbids later observations, and
requires every after-install or after-restore digest to equal the before-install
digest. `TFT_PASS` requires `POST_RESTORE`. The BLOCKED template uses
`NOT_OBSERVED` with three null hashes and remains `PHYSICAL_BLOCKED`.

Hash bindings are necessary but not sufficient. Every phase-available preflight
and receipt artifact is parsed as JSON. Preflight must report `valid=true`,
`result=PASS`, and the exact backend SHA/image/image ID, both firmware
identities, synthetic identity, endpoint set, Compose project, and redacted
secret-presence markers recorded by the ledger. Receipt documents use the shared
materializer receipt verifier and the first/rerun receipts must be semantically
equal after parsing. Arbitrary bytes with self-consistent hashes fail closed.

Physical-action declarations are verdict and phase specific. `TFT_PASS` requires
an explicitly operator-authorized attended stack start and capture/trigger.
`TFT_FAIL` may record those actions only when its stop phase follows the
corresponding action; `PRE_PREFLIGHT` and `POST_PREFLIGHT` cannot claim a stack
start, while only `DURING_ATTENDED_CAPTURE` can claim capture/trigger. Operator
start/end timestamps must parse as ISO-8601 UTC and end cannot precede start.

The three local-lab evidence endpoints use the same shared validator in preflight
and ledger validation: the asset origin
must be credential-free private `http://` and end in `/`; OTA must be private
credential-free `http://`; WebSocket must be private credential-free `ws://`;
all three must use the same concrete private host and reject production-like
names.

The validator produces `TFT_PASS`, `TFT_FAIL`, or `TFT_BLOCKED`. `TFT_PASS` is a
sub-lane result only and is automatically accompanied by
`task07Verdict=PHYSICAL_BLOCKED` until the authoritative Task 07 evidence
validator independently clears all other lanes and approved numeric limits.

## Redaction Contract

Committed or retained validator output must not contain:

- a full MAC, device serial number, bearer token, mint secret, JWT, private key,
  password, authorization header, raw speech, transcript, utterance, audio data,
  pronunciation score, child name, child birth data, or free-form personal data;
- absolute paths outside `/Users/manhhodinh/Documents/TBOT/task-artifacts/
  course-mode-task07/`;
- arbitrary log excerpts.

Device identity is represented only as `AC:20`. Adult operators may be named in
the attended ledger because the HIL report already establishes that requirement;
no child identity is permitted. Validation errors identify schema fields and
stable reason codes, never rejected values.

## Fail-Closed Stop Conditions

The tooling records `STOP_REQUIRED` and refuses `TFT_PASS` for any of these:

- dirty/SHA-mismatched backend, missing exact image/materializer, stale/default
  image, wrong Compose project/resource name, unexpected service/profile/volume,
  or non-loopback backend exposure;
- wrong fixture, contract/layout/manifest checksum, renderer, lesson version,
  cue order, synthetic UUID, device suffix, assignment cardinality, or protected
  hash;
- production-like URL, credential leakage, production publication/assignment/
  flag evidence, or any result that cannot prove local-only scope;
- serial ownership conflict, reset without app-ready, wrong/unreviewed OTA or WS
  path, unauthenticated/wrong-device connection, fallback/sample lesson, missing
  asset, degraded render, ACK timeout, crash, watchdog, or incomplete capture;
- any queued, sent, or server-accepted microphone packet before an explicit
  owned listen start; raw audio/transcript/private content in evidence;
- assessment while motion continues; unexpected or forceful motion, collision,
  binding, failure to center/lower/rest, heat, odor, vibration, unstable power,
  or inability to stop/power-isolate/rollback immediately;
- missing direct visual evidence or disagreement between operator and observer
  for any cue;
- output path/symlink escape, missing artifact, size/hash mismatch, or redaction
  failure.

On a physical stop, the human operator follows the master prompt: end the test,
restore safe pose/power state, preserve evidence, mark `TFT_FAIL`, and do not
waive the issue. The software tooling never attempts the physical response.

## Numeric Limits

This tooling defines no acoustic, current, voltage, temperature, motor-noise,
leakage, E-stop-latency, or comfort thresholds. Those fields carry one of
`NOT_MEASURED`, `MEASURED_PENDING_APPROVED_LIMIT`, or
`EVALUATED_AGAINST_APPROVED_LIMIT`, plus an authority reference. A TFT sub-lane
cannot convert either of the first two states into Task 07 PASS.

## Test Strategy

Each CLI receives focused pytest coverage for valid BLOCKED/PASS sub-lane data,
every stable stop reason, strict schemas, path containment, symlink escapes,
hash/size drift, redaction, and deterministic output. Preflight tests replace
`git` and `docker` with fixture executables and assert the exact allowed argv;
tests fail if any network, serial, device enumeration, Docker mutation, or
secret-value read is attempted.

Integration coverage validates the committed BLOCKED ledger template, checks
the current physical evidence template remains valid, runs the existing Compose
contract tests, and verifies the protected test hash without editing the file.

## Deliverables

- Three dependency-free Python CLIs and focused tests.
- One redacted attended ledger template and one attended Markdown run ledger.
- Updated Task 07 HIL report references that retain `PHYSICAL_BLOCKED`.
- Exact operator commands that validate inputs and artifacts but never trigger a
  robot action.

No implementation may edit the protected test, Farm/T54/T65 or external
worktrees, firmware, production configuration, or device state.
