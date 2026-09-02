# Course Mode Assignment Copy-Only Cleanup Design

## Context

The candidate-bound Course Mode Full gate stages each lane in a private tree and
removes that tree with a fail-closed, identity-checked deleter. The Task 4
assignment fixture currently hard-links generated preview and device derivatives
to reusable media templates. Those legitimate internal hard links leave the
opened inode with a non-zero link count after the first unlink, so secure cleanup
reports retained state and blocks the gate after a successful assignment-new
lane.

## Decision

Task 4 derivative materialization will create independent destination files with
`copyFile` instead of attempting `link` first. The secure staging deleter remains
unchanged and continues rejecting retained hard links, sockets, ownership drift,
and path races.

This behavior is limited to the disposable Task 4 assignment fixture. Production
materialization, Course Mode lesson content, Docker topology, image authority,
and physical-test behavior do not change.

## Data Flow

For each derivative, the fixture validates the source template, removes any stale
destination, copies the source bytes to the destination, and then performs the
existing size, hash, media-format, database, and served-byte checks. NEW and
ROLLBACK continue to share the same deterministic fixture data and candidate-bound
images.

## Failure Handling

- A missing source or failed copy fails the assignment lane immediately.
- Existing destination files are replaced, preserving rerun determinism.
- The fixture must not fall back to hard links.
- Gate cleanup remains fail closed; no special exception is added for multi-link
  files.

## Verification

Tests will prove the fixture uses copy-only replacement, creates distinct inodes
for source and destination on the host filesystem, preserves identical bytes, and
does not call the hard-link API. Existing assignment fixture, release-gate,
manager-web, Quick, Full, and live-database gates remain required. The historical
candidate `.20` report and retained tree remain immutable evidence of the original
cleanup failure.

## Cleanup Scope

After the fix is verified and independently reviewed, the retained `.20` lane tree
may be moved into the existing Course Mode quarantine area or removed only after
its evidence hashes and failure report are preserved. No production data, robot,
firmware, or unrelated worktree is part of this cleanup.
