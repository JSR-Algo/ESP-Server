# Course Mode Firmware cJSON Environment Design

## Goal

Make the candidate-bound `firmware-handler` lane use the cJSON source from the
candidate's verified ESP-IDF checkout while retaining the release gate's
isolated `HOME=/nonexistent` environment.

## Design

The release gate remains the environment authority. When staging a
`firmware-handler` lane, it resolves `components/json/cJSON` from the exact
`candidate.tools.espIdf.commit`. The resolved entry may be either a regular Git
tree or the gitlink used by the pinned ESP-IDF v5.5.4 checkout.

For a regular tree, the gate archives that tree as before. For a gitlink, the
gate requires the exact superproject entry to have mode `160000`, type
`commit`, and one lowercase 40-hex object ID. It then opens the initialized
submodule repository at the canonical
`<esp-idf-root>/components/json/cJSON` path, verifies that the gitlink object is
a commit in that repository, and archives the exact commit tree from Git
objects. The submodule working tree is only a repository locator; its file
bytes and checked-out HEAD are not copied or trusted.

Both forms use the existing bounded archive machinery and its path, mode,
symlink, entry-count, file-size, and total-byte checks. The staged content is
written to `tools/esp-idf/components/json/cJSON`, and the staged candidate's
ESP-IDF root is rebased to the staged tool root. No candidate schema change is
required because the superproject commit already binds the gitlink object ID.

For the `firmware-handler` lane only, `_child_environment` reads that staged
root, requires it to be an absolute canonical real directory, derives
`components/json/cJSON`, and requires `cJSON.c` to be a regular non-symlink
file. It then injects only:

```text
CJSON_DIR=<verified ESP-IDF root>/components/json/cJSON
```

The value is never copied from the operator environment or the external
working-tree pathname. Other lanes receive no `CJSON_DIR`. The lane execution
may replace `HOME` with its private writable runtime as part of the existing
sandbox lifecycle; no real user home is exposed. The firmware repository and
handler script are unchanged, so the frozen firmware SHA and artifact hashes
remain valid.

## Failure Behavior

If the ESP-IDF commit, cJSON entry, gitlink mode/type/object ID, initialized
submodule repository, staged root, cJSON directory, or `cJSON.c` does not
satisfy the contract, staging or `_child_environment` fails closed before the
handler command starts. Symlink aliases, noncanonical paths, missing submodule
objects, and object-type mismatches are rejected. External mutation of either
working tree after staging cannot change the bytes consumed by the handler.

## Tests

- The handler lane receives the exact candidate-derived canonical `CJSON_DIR`.
- A hostile operator `CJSON_DIR` is ignored.
- Non-handler lanes never receive `CJSON_DIR`.
- Missing, symlinked, non-regular, or noncanonical cJSON inputs fail closed.
- Staging uses the exact committed cJSON subtree even if the external working
  tree is changed after staging, and the staged path is removed with the lane.
- A real gitlink fixture proves that the exact submodule commit is archived,
  regardless of the submodule working-tree HEAD or file contents.
- Missing submodule repositories, absent gitlink commits, non-commit objects,
  wrong entry modes, symlinked/noncanonical submodule roots, and repository
  redirection fail closed before the handler starts.
- A candidate-bound handler regression proves the handler observes the staged
  injected directory without changing firmware files or reading the real home.

Candidate `.24` and its firmware-facing `failedLane: snapshot` report remain
immutable failure evidence for the unsupported-gitlink discovery. After the
new RED/GREEN tests, full source qualification, and two independent reviews
pass, freeze candidate `.25` before rerunning Quick, all four firmware-facing
lanes, Full including real NEW-to-ROLLBACK assignment, and isolated live-db.
