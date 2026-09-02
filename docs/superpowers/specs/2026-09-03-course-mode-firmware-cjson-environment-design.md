# Course Mode Firmware cJSON Environment Design

## Goal

Make the candidate-bound `firmware-handler` lane use the cJSON source from the
candidate's verified ESP-IDF checkout while retaining the release gate's
isolated `HOME=/nonexistent` environment.

## Design

The release gate remains the environment authority. For the
`firmware-handler` lane only, `_child_environment` reads
`candidate.tools.espIdf.root`, requires it to be an absolute canonical real
directory, derives `components/json/cJSON`, and requires `cJSON.c` to be a
regular non-symlink file. It then injects only:

```text
CJSON_DIR=<verified ESP-IDF root>/components/json/cJSON
```

The value is never copied from the operator environment. Other lanes receive
no `CJSON_DIR`, and `HOME` remains `/nonexistent`. The firmware repository and
handler script are unchanged, so the frozen firmware SHA and artifact hashes
remain valid.

## Failure Behavior

If the ESP-IDF descriptor, root, cJSON directory, or `cJSON.c` does not satisfy
the contract, `_child_environment` returns `None` and the lane is blocked before
the handler command starts. Symlink aliases and noncanonical paths are rejected.

## Tests

- The handler lane receives the exact candidate-derived canonical `CJSON_DIR`.
- A hostile operator `CJSON_DIR` is ignored.
- Non-handler lanes never receive `CJSON_DIR`.
- Missing, symlinked, non-regular, or noncanonical cJSON inputs fail closed.
- A candidate-bound handler regression runs with `HOME=/nonexistent` and proves
  the handler observes the injected directory without changing firmware files.

After source tests and two independent reviews pass, preserve `.23` as failure
evidence and freeze a new candidate `.24` before rerunning firmware-facing and
broad runtime gates.
