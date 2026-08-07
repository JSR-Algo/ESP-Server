# Lesson E2E verification harness — version-controlled snapshot

`robot/` is **not a git repository**, so `robot/scripts/lesson_e2e_log_verify.py` (8131 lines)
and `robot/tests/test_lesson_e2e_log_verify.py` (15783 lines) — the gate for BOTH T5.3 (simulated)
and T5.4 (live hardware) — have been unversioned, unreviewable and unrecoverable (F-T53-03).

That is not theoretical: editing the verifier broke it twice during T5.3, and both times it was
only recoverable because a scratch-directory backup happened to exist.

This directory is a snapshot of both files at the point they were brought under version control,
so that any future edit has a diffable baseline and a revert path.

**The canonical copy is still `robot/scripts/` / `robot/tests/`** — this snapshot does not
relocate the tool, because where the harness should permanently live (and whether `robot/` should
become a repo of its own) is a T0.4 campaign-structure decision, not a T5.3 one.

To compare the live harness against this baseline:

    diff -u harness/lesson-e2e/lesson_e2e_log_verify.py \
            ../../scripts/lesson_e2e_log_verify.py
