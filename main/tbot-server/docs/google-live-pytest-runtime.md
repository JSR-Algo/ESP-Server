# Google Live deterministic pytest runtime

The deterministic evidence producer does not trust the installed wheel
`RECORD`. It loads
`tests/fixtures/google_live_pytest_runtime_manifest.json` from the exact
candidate Git object and verifies every pytest runtime file against that
manifest before creating a private snapshot.

The current manifest is platform-independent because all pinned files are
Python source or package data. The generator rejects native files; if a future
dependency adds `.so`, `.dylib`, `.dll`, or `.pyd` content, add an explicit
platform section and matching runtime selection instead of marking it `any`.

To update the pin deliberately:

1. Install the reviewed pytest dependency versions in the evidence-runner
   environment.
2. Run `python3 scripts/update_google_live_pytest_runtime_manifest.py`.
3. Review every distribution version, path addition/removal, and hash change.
4. Run the focused deterministic/release tests and the exact 783-node isolated
   matrix before committing the generated manifest with the dependency change.

Collection and execution use separate snapshots. Each snapshot is sealed
read-only and checked before and after its child process using inode, size,
mode, link count, timestamps, and SHA-256 bindings. A collection test therefore
cannot poison the runtime used by execution.
