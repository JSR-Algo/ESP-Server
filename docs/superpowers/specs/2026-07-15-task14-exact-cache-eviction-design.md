# Task 14 Exact Lesson Cache Eviction Design

Date: 2026-07-15
Status: Approved approach A; implementation pending written-spec review
Owners: ESP server and TBOT firmware

## Context

Task 14 cold-preload proof requires removing one exact lesson pack from the
attended robot before creating a fresh assignment. There is currently no
supported operation for this. Raw shell or filesystem deletion is not an
acceptable production or evidence path because it can delete the active,
current, previous-known-good, or candidate pack and cannot produce a bounded,
auditable result.

The observed canonical key is:

```text
pip-farm-3m/v1-521760af6bb2cecd244932cab52c3ca5badeaf3d67561da1e4323af3cb404a28
```

## Goals

- Expose a dedicated authenticated ESP operation for one exact cache key.
- Execute deletion through a user-only firmware MCP tool named
  `self.lesson_assets.evict_cache_key`.
- Validate the key independently in ESP and firmware.
- Refuse active, current, previous-known-good, candidate, or preloading packs.
- Delete only the exact leaf directory under
  `/sdcard/tbot/lesson-assets/<cacheKey>`.
- Return structured, privacy-safe evidence suitable for Task 14.
- Be idempotent when the exact target is already absent.

## Non-goals

- No arbitrary path deletion.
- No recursive deletion outside the exact canonical pack leaf.
- No caller-supplied protected-key override.
- No generic `allowUnlisted` MCP endpoint exposure.
- No eviction while lesson rendering, candidate preload, or realtime voice is
  active.
- No automatic creation of a replacement assignment.

## Considered Approaches

### A. Dedicated exact-key flow (selected)

Add an ESP domain service and authenticated endpoint, plus a firmware helper and
user-only MCP tool. Both sides validate the key and independently enforce their
own protection boundary.

Trade-off: more files, but the deletion contract is explicit, testable, and
fail-closed at both trust boundaries.

### B. Extend `sd_pack_sync.py`

Add eviction beside cached-pack fan-out and reuse its raw MCP helper.

Trade-off: fewer files, but it mixes distribution and destructive lifecycle
responsibilities and makes authorization and protected-pack reasoning harder.

### C. Call the generic raw MCP endpoint

Invoke a user-only tool through `/internal/devices/{deviceId}/mcp-call` with
`allowUnlisted=true`.

Rejected: it relies on a generic escape hatch, exposes an unnecessarily broad
surface, and does not own protected-pack computation.

## Canonical Cache-Key Contract

An accepted key is exactly:

```text
<lesson-slug>/v<positive-decimal-version>-<64-lowercase-hex-checksum>
```

Rules:

- `lesson-slug` contains lowercase ASCII letters, digits, and single hyphens;
  it starts and ends with an alphanumeric character.
- Version is a positive base-10 integer with no sign and no leading zero.
- Checksum is exactly 64 lowercase hexadecimal characters.
- The input must equal the value reconstructed from parsed fields.
- Reject leading/trailing whitespace, repeated separators, absolute paths,
  backslashes, URI schemes, percent encoding, dot segments, control characters,
  uppercase checksum characters, and extra path components.

ESP and firmware use equivalent tests but do not share validation state.

## ESP Architecture

Create `core/lesson/sd_pack_evict.py` with pure validation and an async
orchestrator. The orchestrator:

1. Validates the requested key.
2. Resolves the exact connected device through the existing authenticated
   internal-handler pattern.
3. Refuses when voice or rendering is busy.
4. Builds the protected set from:
   - active runtime cache key;
   - `lesson_runtime_candidate` cache key;
   - explicit preloading key;
   - connection current and previous-known-good keys;
   - persisted activation current, previous-known-good, and candidate keys.
5. Refuses when the requested key is protected.
6. Calls only `self.lesson_assets.evict_cache_key` through the internal raw MCP
   helper with `{ "cacheKey": canonicalKey }`.
7. Parses a structured firmware result and fails closed on timeout, malformed
   response, unknown tool, key mismatch, or firmware refusal.

Add a dedicated route, not a generic MCP call:

```text
POST /internal/devices/{deviceId}/lesson-assets/evict-cache-key
X-Mint-Secret: <existing internal secret>
Content-Type: application/json

{"cacheKey":"<canonical-key>"}
```

The response reports `evicted`, `not_found`, or a stable refusal code. It never
returns filesystem paths or an unmasked configuration value.

## Firmware Architecture

Create `lesson_asset_cache_evict.h/.cc` and register the user-only MCP tool in
`mcp_server.cc`.

The helper:

1. Revalidates the canonical key.
2. Refuses while `Application::IsLessonRuntimeActive()` is true.
3. Constructs the target exclusively as the fixed lesson root plus the
   canonical key.
4. Opens and scans only that exact leaf directory.
5. Rejects nested directories, symlinks, unexpected node types, and path/root
   mismatches.
6. Unlinks regular files in the leaf, then removes the empty leaf directory.
7. Never removes the lesson root, lesson-slug parent, sibling pack, shared asset
   store, or activation metadata.
8. Returns `not_found` successfully when the exact leaf is absent.

Logs contain only the canonical cache key, result code, and file count. They do
not contain asset URLs, tokens, child data, transcripts, or arbitrary paths.

## Error Handling

- Invalid request: HTTP 400, no MCP call.
- Unauthorized request: existing internal-auth response, no device lookup leak.
- Offline robot: HTTP 202 with `evicted=false`, `reason=device-offline`.
- Protected or busy pack: HTTP 409 with stable refusal code, no MCP call.
- Firmware timeout/error/malformed result: HTTP 409, deletion not claimed.
- Absent exact leaf: HTTP 200/202 with `notFound=true`, idempotent success.
- Any returned key mismatch: fail closed and log a protocol error.

## Test Design

### ESP tests

- Accept one canonical key and reject every malformed class above.
- Protect active, candidate, preloading, current, previous-known-good, and
  activation-state keys.
- Prove refused requests never call MCP.
- Prove the exact tool name and exact canonical argument.
- Prove busy voice/render, offline device, timeout, unknown tool, malformed
  result, and result-key mismatch fail closed.
- Prove authentication and privacy-safe structured logging.

### Firmware tests

- Mirror canonical/malicious key validation.
- Refuse while lesson runtime is active.
- Delete the exact flat leaf and preserve sibling/current/PVG directories.
- Reject nested directories, symlinks, and unexpected file types.
- Prove absent target is idempotent.
- Prove no parent/root removal and sanitized logs/results.

### Task 14 evidence integration

- Extend the fault driver to record the exact requested key and structured
  eviction result.
- Cold capture begins only after eviction success/not-found is bound to the
  expected key and before a fresh assignment is created.
- The subsequent run must still prove `downloadedCount > 0`; eviction success
  alone is never cold-preload proof.

## Rollout and Stop Conditions

- Tool remains user-only and the HTTP route remains internal-secret protected.
- Enable only for the attended internal robot until Task 14 cold/warm evidence
  passes.
- Stop immediately on key mismatch, protected-pack refusal, nested filesystem
  content, firmware reset, or any deletion outside the exact target.
- Preserve all raw evidence and hashes; do not promote Task 14 from NOT PASS
  until the existing strict validators pass.

