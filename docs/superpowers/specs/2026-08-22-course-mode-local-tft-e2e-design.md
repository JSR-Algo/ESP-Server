# Course Mode Local Physical TFT E2E Design

## Goal

Run the exact immutable `course-mode-pilot-cat-ball@v1` lesson on the approved
AC:20 robot's 480x320 TFT through renderer `teebot-lesson-renderer.v4`, while
keeping all lesson publication, assignment, rollout flags, and child data out
of production.

## Scope and authority

This design authorizes a disposable local backend, local synthetic adult-only
identity records, an assignment bound only to device `14:c1:9f:d1:ac:20`, and
attended physical display testing. It does not authorize production publish,
production assignment, production flags, deployment, OTA, or child use. Those
remain separately gated by Tasks 08 and 09.

The installed candidate, its five-region readback evidence, and preserved NVS
remain authoritative. No ad-hoc NVS partition patch is permitted. The protected
ESP test `main/tbot-server/tests/test_lesson_voice_output_discipline.py` must
remain byte-identical at SHA-256
`08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3`.

## Architecture

Use a dedicated Docker Compose project with isolated PostgreSQL and Redis
volumes. Expose its Nest backend on host port 3000 because the existing local
ESP container already uses `http://host.docker.internal:3000` as its backend.
The backend must use a local-only mint secret shared with the ESP container and
must never reuse production credentials.

The physical-TFT stack must be entered through
`docs/docker/course-mode-physical-tft/up.sh`. The operator supplies the exact
backend worktree root and its reviewed full Git SHA. The script verifies that
binding and a clean tree, runs the backend compile, unconditionally builds a
SHA-tagged Docker image from that worktree, and checks the compiled Course Mode materializer
inside the resulting runtime image before Compose is rendered or started. The
overlay has no fallback backend image tag, so a stale pre-existing default image
cannot be selected silently. Mutable backend source is not mounted into either
runtime service; only canonical Course Mode fixture and web asset directories
remain read-only mounts.

Seed one synthetic adult operator identity, one synthetic course, the exact
pilot lesson version, and one assignment for AC:20. The seed must import the
canonical checked-in fixture and pilot asset metadata rather than reconstructing
the contract by hand. The seeded lesson remains local-only and may be marked
published inside this disposable database solely because runtime assignment
resolution requires a published lesson; that state has no route to production.

The existing ESP Docker service remains the robot WebSocket endpoint. Its
renderer-v4, runtime, motion preset, and one-device allowlist configuration must
remain scoped to AC:20. The robot must reach the local OTA/WS endpoints without
an ad-hoc NVS edit. If the installed firmware cannot accept a local OTA origin
through its reviewed provisioning surface, the test stops; production DNS,
traffic interception, and NVS binary patching are forbidden.

## Data flow

1. Run the exact-worktree image preflight and verify the SHA-tagged runtime
   contains the compiled local materializer.
2. Start the isolated backend and verify `/v1/health` on host port 3000.
3. Seed and read back the synthetic identity, exact pilot, version, manifest
   checksum, renderer version, and AC:20 assignment.
4. Verify local ESP OTA advertises
   `ws://192.168.100.183:8000/tbot/v1/` and the backend advertises no production
   URL or credential.
5. Establish authenticated AC:20 WebSocket and app-ready evidence.
6. Start the lesson through the normal assignment-backed runtime path using an
   adult operator trigger or the scoped internal lesson nudge.
7. Capture every pilot cue, renderer ACK, step transition, completion, stop,
   and quiescent-rest state with timestamps and redacted logs.
8. Stop the isolated backend after evidence capture. Retain its volumes until
   the report validates, then remove only the task-owned containers and volumes.

## TFT acceptance evidence

The operator or camera capture must inspect every authored pilot cue for:

- stable 480x320 display with no blank, corruption, flicker, or private content;
- correct background, cat, ball, robot pose, caption, and listening indicator;
- no crop, unintended overlap, z-order error, or unsafe focus placement;
- correct teach, listen, acknowledge, alternate-help, celebrate, and close
  states;
- deterministic reduced-motion fallback where applicable;
- clean completion and return to a centered, quiet rest screen.

Logs alone cannot prove visual PASS. Each cue needs a timestamped operator
verdict or image/video frame reference. No child voice, raw transcript, audio,
or free-form personal detail may be captured.

## Fail-closed behavior

Stop without claiming TFT PASS if any of these occur:

- fixture, checksum, renderer, lesson version, device, or assignment mismatch;
- any production host, credential, publication, assignment, or flag mutation;
- robot cannot connect through a reviewed local endpoint path;
- missing asset, fallback renderer, degraded render, ACK timeout, reset, crash,
  watchdog, privacy marker, unexpected motion, heat, odor, vibration, or power
  instability;
- capture omits any pilot cue or cannot bind a visual verdict to that cue.

## Verification

Before physical start, run backend fixture/manifest tests, ESP Course Mode and
runtime tests, firmware renderer/handler tests, local seed readback checks, and
the capture script preflight. During the run, verify exact identities and cue
order. Afterward, validate the evidence bundle, rerun privacy scanning, confirm
the protected test hash, and independently review the TFT verdict.

This lane can produce only a Task 07 TFT result. It cannot by itself prove
acoustic, power, current, thermal, lighting, E-stop timing, rollback rehearsal,
20-session soak, Task 08 GO, Task 09 authorization, or production readiness.
