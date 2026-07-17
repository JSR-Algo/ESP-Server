# HIL Partial Eviction Recovery Design

## Context

The attended Task 14 storage matrix uses a preservation fixture with an attested
sentinel in the primary and sibling cache leaves. The
`evict-after-unlinks-fail` and `evict-before-rmdir-fail` seams intentionally
remove the primary sentinel and leave an empty primary directory.

This is the expected partial-eviction state, but fixture cleanup correctly
refuses to delete an empty directory that no longer contains its ownership
sentinel. On real hardware the scenario reached and consumed the fault with
numeric sequences, then failed at cleanup with
`status=unexpected_existing_node`. A normal retry eviction removed the empty
primary leaf, after which preservation cleanup succeeded.

The firmware cleanup must remain fail-closed. The missing behavior belongs in
the HIL orchestrator: prove the expected partial state, run the production retry
path, validate the retry result, and only then clean the remaining attested
fixture.

## Selected Approach

Add scenario-aware recovery to `lesson_studio_task14_hil_storage.py` after the
post-fault inspection and before fixture cleanup.

Only scenarios whose validated expected primary state is `directory_only` may
run recovery. The orchestrator must:

1. Preserve the initial trigger response and post-fault inspection unchanged.
2. Confirm the primary is exactly an empty directory and the sibling sentinel is
   unchanged through the existing bounded inspection validator.
3. Call the normal `self.lesson_assets.evict_cache_key` tool again with no new
   fault arm.
4. Require `evicted=true`, `notFound=false`, `fileCount=0`, and the exact cache
   key.
5. Inspect again and require the primary missing while protected paths and the
   sibling fixture remain byte-identical.
6. Run preservation cleanup and require the final clean inspection.

All other scenarios record that recovery was not attempted. Unknown response
states, a second partial result, protected-storage drift, sibling mutation, or a
retry that reports false success fails closed.

Rejected alternatives:

- Weakening firmware fixture cleanup to remove empty unattested directories
  would reduce the safety boundary and could delete foreign state.
- Treating the cleanup refusal as a scenario PASS would leave test data on the
  SD card and invalidate subsequent scenarios.
- Resetting or erasing NVS/SD between scenarios would bypass the production
  recovery path being tested.

## Evidence Contract

Add `recovery-response.json` to the ordinary HIL artifact set and to the
power-loss artifact set for a uniform matrix schema.

For partial-eviction scenarios it contains:

- `attempted: true`
- `operation: "evict"`
- `reason: "expected_partial_eviction"`
- the exact validated retry response
- the post-recovery inspection

For other scenarios it contains only the exact fixed fields with
`attempted: false`, `operation: null`, `reason: null`, `response: null`, and
`inspection: null`.

Add a `recovery` field with the same value to `result.json`, and add a
`recovery-trigger` plus `recovery-inspect` timeline event only when attempted.
The initial `trigger-response.json` remains the faulted operation response and
must never be overwritten by the retry.

Update the artifact constants and validators in both the orchestrator and
`lesson_studio_task14_build_identity.py`. Existing evidence without the new
artifact remains historical and must not be rewritten or accepted as a new
matrix run.

## Error Handling And Safety

- Recovery is permitted only after the scenario-specific inspection has proven
  the exact expected partial state.
- Recovery uses the ordinary production eviction MCP tool, not a new HIL-only
  deletion primitive.
- No NVS erase, SD format, recursive broad cleanup, or direct filesystem shell
  command is allowed.
- The controller is reset to idle only through the existing reboot boundary;
  recovery must not re-arm or mutate sequence evidence.
- Failed scenario directories are preserved and never reused.
- The main matrix directory remains empty until a full fresh run begins.

## Verification

Required RED/GREEN coverage:

- Orchestrator unit tests reproduce cleanup refusal after a validated empty
  primary leaf.
- Recovery is called exactly once for both directory-only eviction scenarios.
- Recovery is never called for the other seven scenarios.
- Retry false success, nonzero file count, wrong cache key, sibling drift,
  protected-path drift, and second partial outcome all fail closed.
- Artifact layout, checksum, matrix report, release ledger, and secret-redaction
  tests include `recovery-response.json`.
- Existing firmware fixture tests continue to prove empty unattested directories
  are refused.
- Full ESP Python tests and lesson-studio Node contracts pass.
- Fresh real-device smoke passes both partial-eviction scenarios, followed by a
  clean preflight showing the controller idle and no fixture residue.

## Rollout Gate

After software review, rebuild/recreate only the ESP server image; the frozen
firmware pair remains valid because firmware behavior is unchanged. Run the
complete nine-scenario matrix in a new evidence directory. Do not advance to
production reflash until every scenario validator and the matrix report pass.
