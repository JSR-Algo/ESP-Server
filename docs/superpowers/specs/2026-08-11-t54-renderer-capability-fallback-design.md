# T5.4 Renderer Capability Fallback Design

## Problem

The production robot advertises renderer v1 through v4. The ESP rollout configuration enables
renderer v2 and v4 for the allowlisted device, but `_requested_renderer_capabilities()` currently
returns only the highest enabled renderer. It therefore requests v4 alone even when the active
assignment points to a renderer-v2 manifest. The backend correctly rejects that request with HTTP
422 `LESSON_VERSION_UNSUPPORTED`, so T5.4 stops before manifest fetch and preload.

The backend serve gate is behaving correctly: a manifest may be returned only when its exact
`manifestVersion` appears in the device capability set. The defect is the ESP request collapsing a
multi-renderer device into a single strict lane before it knows the assigned manifest version.

## Decision

The ESP server will advertise every rollout-enabled renderer that the connected firmware has fully
declared, in preference order, followed by the baseline v1 renderer when the firmware advertises it.
For the production device this produces `[v4, v2, v1]`, allowing the backend to serve the assigned
v2 manifest while preserving v4 preference for v4 assignments.

Renderer admission remains exact and fail-closed:

- a renderer is included only when the firmware advertises it;
- v3 and v4 still require their detailed feature declarations;
- rollout-controlled renderers still require the existing single-device allowlist;
- disabled renderers are not included;
- the backend still rejects manifests outside the declared set;
- the runtime still validates the returned manifest and enables behavior from its actual
  `manifestVersion`.

## Alternatives Considered

### Add `manifestVersion` to assignment/current

The ESP server could request only the assignment's exact renderer version. This provides a strong
identity contract but requires coordinated backend response, shared-contract, ESP client, and deploy
changes. It is unnecessary because the backend already performs exact set-membership admission.

### Keep strict highest-renderer selection

Production content could be regenerated and reassigned as renderer v4. This repairs one assignment
but leaves the same failure available for every lower-renderer lesson assigned to a rollout device.
It also turns a device capability rollout into a content migration requirement.

## Code Changes

Modify `main/tbot-server/core/lesson/runtime.py` so
`_requested_renderer_capabilities()` returns a de-duplicated ordered list of enabled, advertised
renderers instead of returning after the first match. Renderer preference order remains v4, v3,
v2, then v1.

Update `main/tbot-server/tests/test_lesson_runtime_branch_gaps.py` to cover:

- simultaneous v4 and v2 rollout returns `[v4, v2, v1]` when all are advertised;
- disabled or incompletely declared renderers remain excluded;
- v4-only firmware still returns `[v4]` when it does not advertise v1;
- no enabled rollout renderer falls back to advertised v1;
- the production-shaped pull can fetch and accept a v2 manifest while v4 is enabled.

## Data Flow

1. Firmware hello declares renderer versions and detailed feature capabilities.
2. ESP rollout gates determine which advertised versions are enabled for the device.
3. ESP sends the ordered compatible set to the manifest endpoint.
4. Backend resolves the assigned lesson version and checks its exact `manifestVersion` against the
   set.
5. Backend returns the manifest only on an exact match.
6. ESP validates manifest identity, checksum, assets, and renderer-specific requirements before
   prepare/preload.

## Error Handling

No backend or wire error behavior changes. If the assigned manifest is outside the compatible set,
the backend continues returning `LESSON_VERSION_UNSUPPORTED`. Empty or malformed manifests,
checksum mismatches, unsupported renderer details, and asset-pack failures continue to fail closed.

## Verification And Release

The regression test must fail against the current single-renderer implementation and pass after the
minimal change. Run the focused runtime tests, the task repro gate, and the ESP standard suite
required by T5.4. Merge through `lesson-prod/scripts/merge-task.sh`, deploy the ESP server using the
VPS backup/deploy/smoke sequence, and confirm the exact production device request fetches the v2
manifest without disabling v4. Then resume the physical T5.4 capture and its remaining manual gates.

No firmware, backend, mobile, shared wire schema, assignment, content, or production rollout toggle
change is part of this design.
