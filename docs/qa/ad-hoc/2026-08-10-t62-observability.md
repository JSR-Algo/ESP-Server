# T6.2 observability re-validation (ESP server)

**Date:** 2026-08-10  
**Branch:** `lesson-prod/t62-observability`  
**Rebased base:** `61d217c5` (`main`)
**Scope:** lesson log assignment/session correlation and reconnect counters

## Repro / audit

The production correlation audit remains green:

```text
tests/test_lesson_observability_t62.py                 20 passed
lesson-prod/repros/t62-esp.sh                          20 passed
lesson-prod/repros/t62b-esp.sh                         20 passed
```

The audit covers the lesson runtime, connection/forwarder surfaces, and Google
Live lesson messages. Session-less background workers continue to use device
identity as documented; no correlation exception was broadened.

## Diff summary

No deployable ESP code changed. Current `main` already satisfies the T6.2 log
correlation and reconnect-counter scope. This evidence-only commit records the
fresh verification.

## Standard-suite prerequisite resolution

F-T64-09 and F-T62-09 were resolved on the isolated
`lesson-prod/t62-prereq-suite` branch. Missing declared Google dependencies were
supplied to the test environment without source changes; the TVideo fixture was
realigned with the firmware ExactObjectKeys TRGB contract; and the nginx test
origin's listen backlog was raised above the permitted edge burst so the harness
cannot manufacture 502 responses. Gate `t62-prereq-esp` passed RED at `9f9fd6d8`
and GREEN at `d22cb6ed`; merge `61d217c5` is included in this branch's rebased
base. See `2026-08-10-t62-ship-prerequisites.md`.

## Ship checklist

Ship step 1 now passes at the rebased branch tip:

```text
tests/test_lesson_observability_t62.py: 20 passed
t62-esp.sh: 20 passed
t62b-esp.sh: 20 passed
cd main/tbot-server && ../../.venv-t62-ship/bin/python -m pytest -q
3,779 passed, 8 skipped
```

The product changes were previously RED->GREEN gated as `t62-esp` and
`t62b-esp` in `lesson-prod/GATE_LOG.md`. This re-validation branch is evidence
only, so its rebased base is already green and cannot produce a legitimate new
RED phase. The historical verified gates remain the merge authority.

## Final Ship result

- Merged the evidence branch with a no-ff commit at ESP main `fea0faca`, retaining
  the verified historical T6.2 gate lineage.
- Deployment was not needed: the T6.2 branch and prerequisite repair changed only
  tests, generated test provenance, and evidence.
- `verify-on-main.sh` created a throwaway worktree at `fea0faca`; the 20-test
  observability audit plus `t62-esp` and `t62b-esp` all passed.
- Branch-tip standard verification passed before merge: 3,779 tests passed with
  8 skipped.
- The concurrent untracked T6.1 evidence file was preserved across both merges
  and restored with unchanged SHA-256
  `75e32a68350a7d70b657aea64df9136752b9093e95ee2d49b014581f8ccb3260`.
