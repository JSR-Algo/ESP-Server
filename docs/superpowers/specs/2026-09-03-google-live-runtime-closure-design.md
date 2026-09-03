# Google Live Runtime Closure Design

## Status

Approved in conversation on 2026-09-03. This design closes the remaining
Task 7 resource and dependency trust gaps without changing the public bytes or
grammar of `commands.jsonl` and `commands.txt`.

## Goal

Every approved Google Live evidence command must execute the exact candidate
source, tracked runtime resources, interpreter, and third-party dependency
bytes bound to the candidate Git SHA and approved platform profile. Mutable
worktree files or ambient site-packages must not influence produced evidence.

The existing model, voice, language, prompt, effective configuration,
fallback, conversation, command order, secret assignment, and evidence report
formats remain unchanged.

## Threat Model

The runner protects against tracked or untracked worktree drift, stale files,
path substitution, symlink and hardlink aliases, dependency drift, nested
Python subprocess escape, partial publication, crashes, and bounded-resource
exhaustion.

As explicitly selected by the operator, a malicious process running as the
same UID that deliberately bypasses the advisory locks or mutates the runner's
private mode-`0700` directories between adjacent filesystem operations is out
of scope. No privileged helper or root-owned launcher is introduced.

## Runtime Closure Model

Task 7 adds one internal, Git-tracked runtime-closure manifest. The manifest is
loaded from the candidate Git object, not from the worktree, and is bound to:

- schema version;
- approved platform tuple (`darwin-arm64-cp314` initially);
- candidate interpreter implementation and version;
- trusted interpreter executable SHA-256;
- approved third-party distributions, versions, import roots, file counts,
  total byte counts, and canonical per-file SHA-256 entries;
- the candidate resource snapshot policy and its canonical digest.

The initial dependency closure is derived from imports reachable by the eight
approved commands and their nested Python children. It includes, when actually
reachable, `numpy`, `websockets`, `PyYAML`, `opuslib-next`, Google client
packages, and their imported transitive distributions. Standard-library
modules are trusted through the pinned interpreter installation. Candidate
modules are trusted through the Git snapshot, never through site-packages.

An import whose origin is outside the standard library, immutable candidate
snapshot, or approved distribution roots fails closed. Namespace packages,
editable installs, `.pth` injections, user-site packages, and unapproved
module-mode loaders are rejected.

## Candidate Snapshot

The runner creates one private read-only snapshot from the candidate Git SHA.
It contains:

- all tracked Python source required by approved commands and nested children;
- tracked YAML, JSON, configuration, manifests, certificates, templates, and
  audio fixtures reachable through candidate-root-relative paths;
- directory structure required for package and resource lookup.

The snapshot excludes Git metadata, caches, bytecode, logs, credentials,
cookies, session data, generated evidence, and runtime outputs. File types,
path grammar, mode, size, count, depth, and total bytes are bounded. Symlinks,
hardlinks, devices, FIFOs, sockets, sparse expansion, duplicate paths, path
traversal, non-canonical names, and unsupported archive members are rejected.

Archive capture is bounded while reading Git output, not only after the child
process has returned. Materialization uses exclusive no-follow creation,
bounded writes, file `fsync`, read-only modes, and parent-directory `fsync`.
The snapshot is verified against the manifest before execution.

## Candidate Resources Versus Run Inputs

Tracked resources that define candidate behavior are read only from the
snapshot. Examples include checked-in default configuration, lesson manifests,
schema files, and canonical audio fixtures.

Run-specific data remains outside the snapshot and must be declared by the
immutable `CommandSpec` as an input, output, protected stdin source, or approved
environment source. This includes:

- effective configuration JSON captured by runner initialization;
- server log windows and transport reports;
- baseline, real-API, correlation, and reliability reports;
- candidate journey evidence and replay reports;
- physical transcript expectations supplied through protected stdin;
- all generated layer reports and evidence outputs.

The bootstrap rewrites only candidate source/resource paths. It never rewrites
declared run-input or output paths. A candidate-root-relative resource resolves
to the snapshot; an evidence-root or explicitly bound absolute artifact path
retains its original value.

## Execution And Nested Processes

The top-level Python command runs with the already approved interpreter bytes
and the verified candidate snapshot. Its import path contains only:

1. the immutable candidate snapshot;
2. standard-library roots belonging to the pinned interpreter;
3. approved distribution roots verified against the runtime-closure manifest.

The bootstrap disables current-directory imports, user site-packages, ambient
`.pth` processing, and other unapproved import roots.

Nested Python execution is intercepted before spawn:

- it must use the already bound interpreter;
- a script under the candidate repository or original approved command cwd is
  mapped to the immutable snapshot;
- absolute data and evidence arguments are preserved;
- `-m`, `-c`, alternate interpreters, and Python scripts outside approved
  source roots fail closed unless a future manifest version explicitly defines
  and binds them.

The real `google_live_robot_soak.py` `SERVER_ROOT` and nested
`analyze_google_live_log.py` path resolve inside the immutable snapshot. Their
log, report, configuration, and evidence arguments continue to point to the
declared run artifacts.

## Provenance And Release Validation

Public command provenance formats do not change. Existing command spec
digests continue to bind argv, cwd, inputs, outputs, environment and secret
sources, protected stdin, timeout, cleanup grace, and terminal policy.

The release gate additionally loads the internal runtime-closure manifest from
the candidate Git object and verifies that every approved command contract is
compatible with the same platform/runtime closure. The manifest itself is not
copied into public argv fields and contains no secrets or user content.

No transcript, prompt, normalized text, audio content, credential, key, MAC,
cookie, session handle, raw exception, stdout, or stderr is persisted.

## Failure And Cleanup

Any missing, changed, unsupported, oversized, ambiguously resolved, or
unapproved source/resource/dependency fails before child execution. A
pre-cancelled command creates no child and leaves no newly materialized output
or snapshot directories.

Timeout, cancellation, and interrupt behavior retains the existing bounded
TERM/KILL/drain process-group cleanup. Private executable and snapshot
directories are removed through the existing ownership-aware cleanup policy.
Failed provenance remains immutable; retry requires the normal new-run rules.

## Performance

The closure is validated once per evidence runner process and reused only while
the candidate Git SHA, platform tuple, interpreter identity, dependency file
identities, and private snapshot identities remain unchanged. Validation is
bounded and cached in memory; it never trusts a cache across processes or Git
SHAs.

The implementation must avoid hashing the same large audio fixture or package
tree once per command. Startup latency is measured by tests and remains bounded
without weakening revalidation at command spawn boundaries.

## Tests

The permanent deterministic suite must cover:

- tracked source, imported module, YAML/JSON/config, manifest, and WAV resource
  replacement in the worktree;
- missing, extra, oversized, malformed, aliased, and tampered snapshot members;
- package version, origin, file content, namespace, editable-install, `.pth`,
  and user-site drift;
- approved interpreter with changed verifier path and rejected interpreter
  substitution;
- nested relative and absolute candidate scripts, the real robot-soak analyzer
  path, preserved data arguments, and rejected outside-root scripts;
- rejected nested `-m`, `-c`, and alternate-interpreter execution;
- bounded Git archive stdout, file count, path depth, per-file size, total size,
  extraction time, and cleanup after injected failure;
- pre-cancel, timeout, repeated interrupt, process-group cleanup, privacy,
  provenance pair recovery, bounded retention, release publication, and all
  prior Task 7 regressions.

Controller verification retains the full Task 7 runner, release-gate,
reliability, deterministic-evidence, compilation, Ruff fatal-selector, and
`git diff --check` gates. The known deterministic opus-origin hang must be
re-evaluated; it may be reported as residual only with fresh baseline/current
A/B evidence under the same environment.

## Rollout

Only `darwin-arm64-cp314` is approved initially. An unsupported platform,
interpreter, dependency closure, or resource policy fails closed until a
separately reviewed manifest variant is added. No deployment, credentialed
Google Live call, or physical hardware action is part of this design or its
synthetic verification.
