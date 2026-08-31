# Course Mode Release Gate Threat Model

Date: 2026-08-31

Status: approved design for the rootless attended release workflow. This
document does not authorize deployment, database mutation, firmware flashing,
serial access, HIL, or physical robot motion.

## Objective

Define the security boundary for the Course Mode release gate without claiming
an isolation guarantee that macOS cannot provide to processes running under the
same user identity.

The gate validates the exact candidate sources, staged tools, execution inputs,
and post-execution state used for `english-6month-4-6`. It runs on a trusted
operator account during an attended release session.

## Threat model

The gate protects against:

- candidate metadata drift or replacement;
- persistent source, dependency, tool, or authority mutation;
- accidental concurrent filesystem writes;
- writes attempted by the sandboxed Python lane;
- path replacement, symlink escape, hardlink, special-file, writable-entry,
  and retained-staging cleanup failures already covered by the gate tests;
- incomplete, skipped, timed-out, or non-zero software lanes.

The gate does not protect against an arbitrary malicious process already
running under the same effective UID as the gate. Such a process can change
owner-controlled permissions, signal the gate, force-detach user-mounted disk
images, and restore transiently modified bytes before endpoint digest checks.
A compromised operator account is therefore outside this rootless workflow's
threat model.

This exclusion is narrow: it does not weaken candidate identity, source-tree,
execution-tree, sandbox, cleanup, or point-of-use physical authorization checks.

## Architecture

The existing single canonical release-gate implementation remains in place.
No second gate version, privileged helper, VM path, or parallel release flow is
introduced.

For every selected lane, the gate:

1. validates candidate metadata and exact repository identities;
2. creates a fresh candidate-bound staging tree;
3. stages only attested runtimes and dependency installations required by the
   lane;
4. makes the verified base read-only and creates a separate writable lane copy;
5. binds backend source and full execution-tree digests before spawning the
   Python Course Mode lane;
6. runs the lane with the existing macOS sandbox write policy;
7. verifies the captured authority and full backend execution tree after the
   child exits;
8. destroys lane and base staging ownership, failing closed if cleanup cannot
   be proven;
9. rechecks candidate metadata before accepting the lane or final report.

Pre/post digests are integrity endpoint evidence. They must not be described as
proof against mutate-execute-restore by a malicious same-UID process.

## Operator precondition

Before Quick, Full, live-database, or physical-preflight execution, the attended
operator must use a trusted local account and close or stop untrusted automation
that can modify the candidate or `/private/tmp`. Evidence records the host,
effective UID, exact candidate ID, repository SHAs, tool descriptors, and gate
commit.

Failure to establish this precondition blocks the release. It is not replaced
by a warning or an automatic PASS.

## Verification

Automated coverage must continue to prove:

- candidate and authority replacement is rejected;
- persistent backend source and dependency mutation is rejected;
- the sandboxed child cannot write outside its lane root;
- every lane receives a fresh snapshot;
- repeated and garbage-collection cleanup closes descriptors and removes owned
  trees on the canonical Python runtime;
- Quick, Full, and live-database reports fail closed on missing authority,
  timeout, output overflow, skipped tests, cleanup failure, or metadata drift.

The release evidence must state the same-UID exclusion explicitly. Reviewers
must treat any claim of protection against a compromised operator account as a
release-blocking documentation defect.

## Physical safety boundary

Software PASS remains separate from physical authority. The workflow may issue
`SOFTWARE_GO_FOR_ATTENDED_FLASH` only after the new candidate passes all
software, isolated live-database, evidence, and independent-review gates.

At the flash point, fresh user confirmation is still required for the robot
identity, serial port, candidate SHA, application offset `0x20000`, binary size,
and preserved partitions. No earlier approval substitutes for that point-of-use
confirmation.

## Deferred stronger isolation

If protection from malicious same-UID processes becomes a requirement, this
rootless design is insufficient. A separate approved design must introduce a
minimal privileged broker or dedicated VM/service that owns a read-only
execution filesystem and runs the verifier under a distinct UID. That change is
not part of the current Course Mode candidate.
