# Course Mode Assignment Capsule JS Contract Design

## Goal

Allow the candidate-bound Task 4 NEW and ROLLBACK runners to consume the
gate-owned private runtime capsule without weakening the capsule's filesystem
ownership, identity, cleanup, or lane-isolation guarantees.

The change fixes the `.22` Full-gate failure where Python correctly injects a
private capsule under the system temporary directory, but the JavaScript runner
still requires `TASK4_ASSIGNMENT_RUNTIME_ROOT` to be below the staged
`main/manager-web/output` directory.

## Scope

Change only the canonical Course Mode release gate, the existing Task 4
assignment runner or a narrowly scoped validation helper, and their tests. Do
not change `_remove_owned_tree`, derivative materialization, assignment fixture
semantics, production services, firmware, serial access, HIL, or robot behavior.

Candidate `.22` remains failure evidence. A successful candidate must use the
next candidate identifier and bind the reviewed post-fix admin SHA and rebuilt
web image.

## Contract

The gate injects two related variables only into the stateful assignment lanes:

- `TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT`: the gate-owned outer directory.
- `TASK4_ASSIGNMENT_RUNTIME_ROOT`: the direct `runtime` child used for media and
  TLS state shared by ordered NEW then ROLLBACK.

The JavaScript runner requires both variables. It accepts the runtime only when
all of these conditions hold:

1. Both values are absolute paths with no NUL byte.
2. Both named paths exist as real directories and are not symbolic links.
3. `TASK4_ASSIGNMENT_RUNTIME_ROOT` is exactly
   `<TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT>/runtime` after lexical and real-path
   resolution.
4. The outer directory name starts with
   `course-mode-assignment-runtime-`.
5. The outer and runtime directories are owned by the effective user and have
   mode `0700`.
6. The capsule does not overlap the staged admin repository, candidate backend
   worktree, or firmware worktree.

Python remains the authority for captured device/inode identities, descriptor
lifetime, pre-lane usability checks, exact retained-path reporting, and cleanup.
JavaScript performs a defense-in-depth named-path check immediately before it
creates `media/` or `tls/` children and before any Docker command.

The old `manager-web/output` fallback is removed for candidate-bound Task 4
orchestration. A caller that does not provide the explicit capsule pair fails
closed before Docker execution.

## Data Flow

1. The release gate creates one private owner directory and its `runtime`
   child, captures both identities, and keeps their descriptors open.
2. `_child_environment` injects the owner and runtime paths only for NEW and
   ROLLBACK.
3. The JavaScript runner validates the pair and derives `media/` and `tls/`
   only from the validated runtime child.
4. The candidate-bound media-preparation script independently validates the
   same capsule pair and requires `TASK4_ASSIGNMENT_MEDIA_ROOT` to equal the
   validated `<runtime>/media` directory before it creates templates or invokes
   `ffmpeg`. This keeps direct invocation fail closed without restoring the old
   `manager-web/output` restriction.
5. NEW creates derivatives and assignment state. The lane snapshot is removed,
   while the capsule survives.
6. ROLLBACK receives the same two paths, verifies NEW state, then the gate
   removes the capsule using its existing identity-bound cleanup.

## Failure Behavior

- Missing, malformed, replaced, symlinked, incorrectly owned, writable, or
  overlapping capsule paths fail before Docker starts.
- Media preparation rejects a missing or invalid capsule pair and any media
  root other than the exact `media` child of the validated runtime. Rejection
  occurs before directory creation or `ffmpeg`/`ffprobe` execution.
- A path pair that is valid during JavaScript validation but is replaced later
  is still caught by the gate's descriptor-based post-lane usability and
  cleanup checks.
- Cleanup failure continues to override the lane verdict and reports every
  exact retained identity-bound path.
- Non-assignment lanes never receive either capsule variable.

## Tests

Testing is ordered by product risk, with runtime behavior and firmware-facing
results ahead of broad static coverage.

First, add test-first coverage for:

- Python injection of the exact owner/runtime pair into NEW and ROLLBACK.
- Absence of both variables from non-assignment lanes.
- Real-filesystem JavaScript acceptance of a private `0700` owner with direct
  `runtime` child.
- JavaScript rejection of missing pairs, mismatched children, symlinks, wrong
  modes, wrong ownership where testable, prefix violations, and repository
  overlap.
- A focused actual assignment runner regression proving the capsule contract
  passes through media preparation before Docker orchestration, with explicit
  traps proving Docker and Compose were not invoked.

Next, run the real candidate-bound runtime path in this order:

1. An isolated NEW-to-ROLLBACK assignment flow using one shared capsule, real
   Docker services, real derivative generation, and Playwright WebKit.
2. Firmware-facing renderer, handler, backward-compatibility, and
   cross-contract parity lanes, verifying the same lesson/media contract that
   the firmware consumes.
3. Existing capsule tamper, retained-path, process containment, copy-only, and
   full canonical source suites.

The runtime qualification must assert that generated media survives NEW lane
snapshot cleanup, ROLLBACK observes the same derivative bytes and database
state, all exact assignment Docker resources are removed, and the capsule has
no retained paths. Firmware-facing qualification must use the frozen firmware
SHA and artifact hashes from the candidate and must not rebuild, flash, reboot,
open serial, or move the robot.

After implementation, require independent spec and quality/security reviews,
the full canonical source suite, a clean committed repository, and a newly
frozen candidate. Do not continue to live-db or physical testing until the new
candidate's Quick and Full gates pass. Physical firmware validation remains a
separate attended phase that requires a fresh point-of-use confirmation after
the software verdict.
