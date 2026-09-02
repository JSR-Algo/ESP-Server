# Course Mode Firmware cJSON Environment Design

## Goal

Make the candidate-bound `firmware-handler` lane use the cJSON source from the
candidate's verified ESP-IDF checkout while retaining the release gate's
isolated `HOME=/nonexistent` environment.

## Design

The release gate remains the environment authority. When staging a
`firmware-handler` lane, it resolves the cJSON subtree object from the exact
`candidate.tools.espIdf.commit` and copies `components/json/cJSON` from Git
objects into the gate-owned execution stage. It does not copy cJSON bytes from
the mutable ESP-IDF working tree. The staged candidate's ESP-IDF root is
rebased to the staged tool root.

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

If the ESP-IDF commit, cJSON subtree object, staged root, cJSON directory, or
`cJSON.c` does not satisfy the contract, staging or `_child_environment` fails
closed before the handler command starts. Symlink aliases and noncanonical
paths are rejected. External working-tree mutation after staging cannot change
the bytes consumed by the handler.

## Tests

- The handler lane receives the exact candidate-derived canonical `CJSON_DIR`.
- A hostile operator `CJSON_DIR` is ignored.
- Non-handler lanes never receive `CJSON_DIR`.
- Missing, symlinked, non-regular, or noncanonical cJSON inputs fail closed.
- Staging uses the exact committed cJSON subtree even if the external working
  tree is changed after staging, and the staged path is removed with the lane.
- A candidate-bound handler regression proves the handler observes the staged
  injected directory without changing firmware files or reading the real home.

After source tests and two independent reviews pass, preserve `.23` as failure
evidence and freeze a new candidate `.24` before rerunning firmware-facing and
broad runtime gates.
