# T6.4 security re-verification - ESP server

Date: 2026-08-10
Branch: `lesson-prod/t64-security`
Base: `9f9fd6d8`
Prior evidence: `docs/qa/ad-hoc/2026-08-07-t64-security.md`

## Outcome

All seven T6.4 security boxes remain closed on current main. No ESP production code
changed in this re-verification.

- Anonymous and wrong-secret requests to `/internal/lesson-runtime/metrics` return
  401 on the running T5.3 stack.
- The anonymous assignment console receives `connectedDevices = []`; the stored-XSS,
  fleet-inventory, production-console, and loopback-deploy repros all remain green.
- All accumulated T6.4 repros passed: `t64` 10/10, `t64c` 9/9, `t64e` 6/6.
- Focused lesson HTTP/admin/nudge/SD handler coverage passed 220/220.
- Admin-proxy client wiring passed.
- Lesson content sink scans found only `redis.eval`, which executes fixed Lua and is
  not a Python/content eval sink. Manager-web and mobile lesson paths contain none
  of the reviewed HTML/WebView sinks.

## Deep-dive checklist

| Box | Result | Evidence |
| --- | --- | --- |
| 1. IDOR/authz matrix | PASS | backend route inventory and authz suites; live wrong-credential probes |
| 2. Asset URLs | ACCEPTED | F-T64-06 written risk acceptance unchanged |
| 3. Admin proxy key | PASS | `check-admin-proxy-key-wiring.mjs`; backend key/rotation tests and live rejection |
| 4. Content injection | PASS | sink scan clean; console XSS repro remains green |
| 5. ESP admin/API auth | PASS | `t64` and `t64e`; live anonymous/wrong-secret 401; focused handlers 220/220 |
| 6. Rate limits | PASS | backend `t64d` and rate-limit suite |
| 7. Secrets | PASS | tracked/history/source filename scans across backend, ESP, mobile, and firmware found no new credential material |

## Verification

```text
node scripts/check-admin-proxy-key-wiring.mjs       PASS
lesson-prod/repros/t64.sh                          10 passed
lesson-prod/repros/t64c.sh                          9 passed
lesson-prod/repros/t64e.sh                          6 passed
focused lesson HTTP/admin/nudge/SD handlers       220 passed
python3 -m pytest -q                                6 failed, 3772 passed, 9 skipped
```

The six full-suite failures are the remaining F-T64-09 classes and are outside
T6.4: two Google Live dependency/runtime tests and four cross-repository TVideo
fixture drift tests. The formerly seventh failure, lesson-studio compose, is now
green. No security-surface failure occurred.

## Change summary

Evidence only. No deployable ESP/admin-web code changed, so the standing T7.3
deployment lane remains the owner and no T6.4 deployment is needed.
