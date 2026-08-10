# T6.2 ESP Ship prerequisite repair

**Date:** 2026-08-10  
**Branch:** `lesson-prod/t62-prereq-suite`  
**Findings:** F-T64-09, F-T62-09

## Repro

The standard suite initially reported seven failures. Two Google Live failures were setup
errors: the current interpreter lacked the repository-declared `psutil`, `google-genai`, and
`google-generativeai` packages. With those pins installed, the Google Live and Gemini coverage
passed without source changes.

Four TVideo failures reproduced stale generated-contract expectations:

```text
test_tvideo_farm_cross_repo_fixture.py: 4 failed, 4 passed
expected compatibilityMetadata, but the firmware ExactObjectKeys contract uses flat TRGB fields
expected firmware fixture SHA c821f431..., generated 05452a877...
```

The nginx runtime test also reproduced intermittently on its second isolated run:

```text
abusive statuses: {200, 429, 502}
expected subset: {200, 429}
```

The fake `ThreadingHTTPServer` inherited Python's small default listen backlog while nginx was
allowed a burst of 180 requests, so the harness could refuse an otherwise permitted upstream
connection and manufacture a 502.

## Fix diff

- Updated the TVideo test to assert the current exact flat TRGB prepare-asset keys and values.
- Regenerated the authoritative firmware fixture SHA in the checked-in provenance file.
- Raised only the nginx test origin's listen backlog to 512, above the permitted edge burst.
- Made no Google Live or Gemini source changes; their failures were resolved by using the
  declared Python dependencies.

## Passing re-run

```text
Google Live + Gemini + TVideo + nginx + route-registration targets: 56 passed
TVideo fixture suite: 8 passed
nginx abuse test repeated after fix: 5/5 passed

cd main/tbot-server && ../../.venv-t62-py314/bin/python -m pytest -q
3,779 passed, 8 skipped
```

The gate, merge SHA, and post-merge verification are recorded during T6.2 close-out.
