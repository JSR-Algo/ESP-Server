# Course Mode Stable Asset Mount Lifecycle Design

## Problem

The Course Mode release gate stages each lane in a temporary snapshot. The
stateful assignment lanes recreate the Lesson Studio `web` container while
`TBOT_BACKEND_WORKTREE` and `TBOT_FIRMWARE_WORKTREE` point at that snapshot.
Docker therefore binds the web lesson assets from the temporary paths. Gate
cleanup removes the snapshot but leaves the running container behind with
stale mounts. A later Playwright lane correctly fails preflight with
`started web container lesson asset mounts mismatch`.

This makes a successful NEW to ROLLBACK assignment sequence non-repeatable and
leaves the shared candidate stack unusable for subsequent software or physical
preflight.

Runtime verification exposed a second retained-state problem after the stable
mount fix: the ROLLBACK lane leaves `backend` and `web` configured through the
assignment override. The backend then continues publishing
`https://task4-media.localhost/...` asset URLs. That temporary CA is trusted by
the assignment-specific Playwright session only, so the normal Course Mode
browser suite fails with `ERR_CERT_AUTHORITY_INVALID` after ROLLBACK.

## Decision

Keep two explicit path roles:

- `TBOT_BACKEND_WORKTREE` and `TBOT_FIRMWARE_WORKTREE` remain the lane's staged
  execution inputs.
- `TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT` and
  `TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT` identify the stable, clean source
  repositories declared by the frozen candidate.

The base Lesson Studio Compose file will use the stable mount-root variables
for the four web lesson asset bind mounts, with the existing worktree variables
as compatibility fallbacks. The release gate will provide both stable roots to
Playwright and stateful assignment lanes. Assignment-specific media copying
continues to use the staged worktrees, preserving snapshot isolation.

This is preferred over restoring the stack after every assignment because the
container remains valid throughout the lifecycle and no cleanup operation has
to reconstruct state after evidence has already been collected.

After the ROLLBACK assertions and readback complete, the orchestrator will run
one explicit base-Compose restore for `backend` and `web`. It will use the same
candidate-pinned images, stable mount roots, ports, database volumes, and
caller-provided base asset origin, but omit the assignment override. The
restore uses `--no-deps --force-recreate --wait`, so PostgreSQL, Redis, and
MySQL state remain intact while the temporary assignment URL/configuration is
removed. NEW does not restore because ROLLBACK must consume its state.

## Data Flow

1. The gate validates the canonical backend, admin, and firmware repositories
   against the candidate manifest.
2. It creates a private snapshot for the current lane.
3. The lane executes scripts and assignment media preparation from the private
   snapshot.
4. Compose resolves the four web lesson asset mounts from the canonical
   candidate roots supplied through the dedicated mount variables.
5. The gate deletes the lane snapshot without invalidating the running web
   container.
6. After ROLLBACK verification, base Compose recreates only `backend` and
   `web`, retaining database/cache volumes and removing assignment-only service
   configuration from those services.
7. A following Playwright preflight observes the exact canonical sources,
   `bind` type, and read-only mode.

## Safety And Compatibility

- No production database, firmware, serial port, robot reboot, or physical HIL
  action is part of this change.
- Existing local callers that do not set the dedicated mount variables retain
  the current Compose behavior through fallbacks.
- The post-ROLLBACK restore does not run after NEW and does not remove the
  derivative-media container; exact test-namespace cleanup remains owned by
  the release workflow.
- The stable paths must come from `source_candidate`, never caller-controlled
  free-form values or the staged execution candidate.
- All four mounts remain read-only.
- Failed `.35` evidence remains immutable. Because the admin SHA changes, the
  fix requires a new frozen candidate and a web image carrying the new exact
  OCI revision even though runtime application content is unchanged.

## Testing

Implementation follows red-green-refactor:

1. Add a Python contract test proving stateful assignment lanes receive stable
   mount roots from the original candidate while their worktree variables still
   point at the staged candidate.
2. Add a Compose/source contract test proving all four web lesson mounts use the
   dedicated stable variables and remain read-only.
3. Run the focused Python and Node contract suites.
4. Add a launcher contract proving ROLLBACK performs its evidence readback
   before an exact base-Compose `backend`/`web` restore with
   `--no-deps --force-recreate --wait`, while NEW does not restore.
5. Run a real candidate-bound NEW to ROLLBACK sequence, clean the lane snapshot,
   confirm the backend asset origin is restored, then run Chromium and WebKit
   preflight/E2E against the same stack.
6. Freeze the next candidate, rebuild the exact-revision web image, and rerun
   validator, Quick, Full, live PostgreSQL, evidence audit, and physical
   preflight gates before requesting fresh authorization for robot interaction.

## Success Criteria

- NEW to ROLLBACK completes without replacing stable web asset sources with
  temporary snapshot paths.
- Removing the lane snapshot does not break any web lesson asset mount.
- After ROLLBACK, the backend no longer publishes assignment-only HTTPS asset
  URLs to regular Course Mode browser sessions.
- Consecutive Course Mode browser gates pass against the same candidate stack.
- The final software evidence contains no retained staging metadata and all
  expected lanes pass before any physical action is requested.
