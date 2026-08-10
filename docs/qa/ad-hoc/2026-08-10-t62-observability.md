# T6.2 observability re-validation (ESP server)

**Date:** 2026-08-10  
**Branch:** `lesson-prod/t62-observability`  
**Base:** `9f9fd6d8` (`main`)  
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

## Standard-suite status and findings routing

The first full server run reported 3,772 passed, 9 skipped, and 6 failures. All
six match existing out-of-scope finding F-T64-09: the Google Live
benchmark/client environment and four renderer cross-repo fixture drift checks.

The fresh branch-tip run reported 3,771 passed, 9 skipped, and 7 failures. It
repeated those six and intermittently failed the real-nginx public generation
runtime test with an unexpected 502. That regression is routed as F-T62-09 to
T6.4/T0.1. No T6.2 test failed, so those owners' files were not changed here.

## Ship checklist

Ship step 1 was repeated at branch tip `1706da08`. The scoped 20-test audit
passed; the standard suite remained red as described above. The branch was not
merged, deployed, or removed. T6.2 remains blocked until the standard-suite
gate can pass.
