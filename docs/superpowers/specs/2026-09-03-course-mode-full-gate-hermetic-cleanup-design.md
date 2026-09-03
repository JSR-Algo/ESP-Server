# Course Mode Full Gate Hermetic Cleanup Design

Date: 2026-09-03

Status: approved design direction, pending written-spec review. This document
does not authorize deployment, production database mutation, firmware flashing,
serial access, HIL, robot reset, power-cycle, or motion.

## Problem

Candidate `.27` proved that the canonical launcher now forwards the assignment
environment correctly, but its Full rerun exposed two independent release-gate
defects.

First, `_remove_owned_tree()` treats every still-linked regular-file inode as a
cleanup race. A test inside the staged candidate intentionally creates two hard
links to the same owned file. Removing the first path correctly reduces the
inode link count from two to one, but cleanup requires zero and retains the
otherwise removable staging tree.

Second, `esp-course-mode-full` discovers nearly every tracked
`test_course_mode*.py` module. That naming convention mixes runtime and lesson
contract tests with candidate-manifest, release-tooling, evidence, physical,
live-database, validation-script, and cross-repository fixture tests. The outer
candidate sandbox cannot correctly provide all of those authorities and
topologies, so the lane collected 690 tests and failed for reasons unrelated to
the Course Mode runtime contract.

Cleanup then replaced the underlying failed lane with `failedLane: cleanup`,
making the primary software failure harder to diagnose.

## Scope

Change only the existing canonical release gate and its tests. Keep one gate,
one Full lane inventory, the existing candidate schema, the current macOS
sandbox, and the current staged Python runtime.

The change will:

1. accept correct removal of an owned regular-file path when other hard links
   to the same inode remain;
2. retain the existing identity, ownership, path-recreation, and race checks;
3. replace filename-pattern ESP Full discovery with an explicit hermetic
   runtime/contract suite;
4. keep release-tooling, evidence, physical, live-database, validation-script,
   Python-version, and cross-repository authority tests in source qualification
   or their already dedicated gates;
5. preserve the primary failing lane when cleanup also fails, while still
   blocking the report and recording retained cleanup ownership.

The change will not provision the complete 690-test source suite inside the
candidate sandbox, weaken `reject_pytest_skips`, add a second release flow, or
change application runtime behavior.

## Cleanup Semantics

The existing rename-open-unlink sequence remains authoritative. For each
regular file, cleanup will:

1. capture the path metadata;
2. rename the path to a random quarantine name in the same directory;
3. verify owner, device, inode, and file type after the rename;
4. open the quarantined inode with `O_NOFOLLOW` and reverify its identity;
5. record the opened inode's link count immediately before unlink;
6. unlink the quarantine path;
7. prove the quarantine path is absent and has not been recreated; and
8. require the opened inode's link count to decrease by exactly one.

The final condition is `post_unlink_nlink == pre_unlink_nlink - 1`, with a
strictly positive pre-unlink count. It proves that the unlink affected the
identity-bound opened inode without incorrectly requiring every other hard link
to have already been visited. A swapped or moved original inode does not receive
the expected decrement and still fails closed.

Symlink, FIFO, socket, directory, ownership, root identity, descriptor-path,
and unsupported-file handling remain unchanged. Cleanup still returns failure
and retains the exact owned tree whenever identity or removal cannot be proven.
The same-UID malicious-process exclusion in the approved threat model remains
unchanged.

## Hermetic ESP Full Suite

`esp-course-mode-full` will use a source-controlled explicit tuple rather than
discovering tests by filename prefix. The tuple will contain only modules that
exercise the Course Mode lesson contract and runtime using the staged admin
source, staged candidate-bound backend snapshot, bundled Python dependencies,
and temporary lane directories:

- `tests/test_course_mode_contract.py`
- `tests/test_course_mode_curriculum.py`
- `tests/test_course_mode_curriculum_e2e.py`
- `tests/test_course_mode_e2e_journeys.py`
- `tests/test_course_mode_forwarder.py`
- `tests/test_course_mode_resource_soak.py`
- `tests/test_course_mode_runtime_compatibility.py`
- `tests/test_course_mode_runtime_integration.py`
- `tests/test_course_mode_task00_contract.py`
- `tests/test_google_live_course_mode.py`

The supported-Python import probe currently embedded in
`test_course_mode_contract.py` depends on host `/opt/homebrew` interpreters and
can skip. It will move to a separately named source-qualification module so the
runtime/contract module remains fully hermetic and the Full lane can continue to
reject every pytest skip.

The following categories remain deliberately outside this lane:

- candidate manifest, operator attestation, evidence audit, and release-gate
  tests: canonical source qualification;
- task-specific validation and evidence scripts: canonical source
  qualification;
- physical TFT compose, ledger, preflight, and receipt tests: physical-contract
  qualification and the dedicated physical-preflight workflow;
- cross-process PostgreSQL tests: source qualification or an explicitly
  provisioned live-database workflow, never an implicit local database;
- renderer persistence cross-repository fixture checks: source qualification
  and existing firmware/cross-contract gates where both backend and firmware
  authorities are intentionally bound.

Adding a new `test_course_mode*.py` file will no longer silently expand the
candidate Full lane. A release-gate inventory test must be updated deliberately
when a new hermetic runtime/contract module should become release-critical.

## Environment and Data Flow

The lane continues to execute through the candidate's attested Python test
runtime with `-I -s`, the macOS sandbox, candidate-rebased paths, a writable
lane-local `HOME`, `TMPDIR`, cache, and report directory, plus the existing
backend snapshot authority and digest checks.

No live database URL, Docker authority, physical receipt, production secret, or
host interpreter path is required by the explicit suite. If a selected module
introduces one of those dependencies later, the canonical Full regression test
must fail until the module is moved to the correct dedicated gate or made truly
hermetic.

## Failure Reporting

Cleanup failure always keeps the overall verdict `BLOCKED` and records
`retainedOwner: current-process` plus the exact `retainedPaths`.

If the lane command passed but cleanup failed, `failedLane` remains `cleanup`.
If the lane command already failed, timed out, exceeded output bounds, lacked
authority, or contained a rejected pytest skip, `failedLane` remains the lane's
name and the report additionally records `cleanupFailed: true`. This preserves
the primary cause without treating retained staging as non-blocking.

Raw child stdout and JUnit XML remain ephemeral and are not copied into the
immutable release report. The explicit suite and preserved lane identity provide
the stable diagnostic boundary; a traced diagnostic run may retain detailed
test output separately when needed.

## Testing

Implementation follows red-green-refactor.

Cleanup regression tests will create two and three hard links within one owned
tree and verify complete removal. Existing unlink-swap tests will continue to
prove that moving or replacing the quarantined inode fails closed and preserves
the escaped original. Existing symlink, FIFO, socket, directory swap, root swap,
repeated-cleanup, and retained-path tests must remain green.

ESP selection tests will assert the exact ordered module tuple, prove that a
new prefix-matching release/meta/physical module is not auto-selected, prove
that every selected path exists in the candidate commit, and verify that the
generated pytest command still uses the attested runtime and rejects skips.

Reporting tests will cover both combinations: successful lane plus failed
cleanup reports `failedLane: cleanup`; failed lane plus failed cleanup preserves
the lane name and adds `cleanupFailed: true` with retained ownership paths.

Focused release-gate tests must pass before running the complete admin Python
qualification. After implementation and independent spec and quality/security
reviews, all canonical source qualification must pass with no skips or failures.

## Release Consequences

This source change invalidates `.27` as a release candidate but does not alter
or delete its evidence. After qualification and reviews, freeze `.28` from the
new admin commit and rerun Quick, firmware-facing, canonical Full, live-database,
and final evidence audits.

Only after all software gates pass may the workflow request fresh point-of-use
authorization for attended physical firmware testing. Previous reset,
bootloader, power-cycle, deployment, or physical approvals do not carry forward.
