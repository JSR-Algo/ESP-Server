# Course Mode Assignment Launcher Environment Design

## Problem

The canonical Course Mode launcher starts the Python release gate with
`env -i`. It forwards the live-database and operator-attestation variables,
but drops the ten operator-supplied variables required by the stateful
assignment lanes. A canonical Full run therefore blocks
`admin-course-mode-assignment-new` before starting its command, even when the
operator supplied a complete valid assignment environment.

## Scope

Change only the existing canonical launcher and its launcher regression tests.
Do not change the Python gate, assignment commands, candidate schema, runtime
capsule, Docker topology, or physical-device workflow.

## Design

Keep `env -i` as the trust boundary. Explicitly forward these existing
operator-supplied variables into the clean environment:

- `LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME`
- `LESSON_STUDIO_E2E_RESOURCE_PREFIX`
- `TASK4_ASSIGNMENT_RUNTIME_ROOT`
- `JWT_PUBLIC_KEY`
- `TBOT_DEVICE_MINT_SECRET`
- `LESSON_ASSET_ORIGIN_BASE`
- `ROBOT_ESP_BASE_URL`
- `LESSON_STUDIO_E2E_BACKEND_HOST_PORT`
- `LESSON_STUDIO_E2E_WEB_HOST_PORT`
- `TASK4_ASSIGNMENT_MEDIA_HOST_PORT`

The shell launcher will use the same explicit default-empty forwarding pattern
as `COURSE_MODE_ADMIN_E2E_READY`. Validation remains owned by the Python gate:
the launcher transports values but does not interpret ports, paths, URLs,
secrets, or project names.

No prefix-based forwarding is allowed. Variables outside the explicit
allowlist must remain absent after `env -i`.

## Testing

Add a black-box launcher test that replaces the Python gate with an environment
probe in the existing temporary Git fixture. The test supplies all ten
assignment variables plus similarly named unrelated variables and asserts:

1. Every canonical assignment variable reaches the probe unchanged.
2. Unrelated variables do not reach the probe.
3. Existing launcher trust checks and environment-forwarding tests still pass.

Use red-green-refactor: first observe the new test fail against the current
launcher, then add only the ten explicit assignments and rerun the focused and
full launcher test file.

## Release Consequences

The launcher is candidate-bound source, so the fix requires a new admin commit,
two independent reviews, renewed source qualification, and a newly frozen
candidate. Existing `.26` evidence remains immutable and BLOCKED. No physical
firmware action is authorized by this change.
