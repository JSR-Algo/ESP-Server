# TVideo Farm Software Validation

Status: **SOFTWARE ONLY**

Hardware gate: **PENDING_ATTENDED_HARDWARE**

This evidence does not claim ESP32-S3 N16R8 readiness. It proves the canonical farm contract through the backend builder, ESP validation/materialization/projection, and the host-native firmware handler/renderer boundary.

## Fixture provenance

- Backend source commit: `f78f8eae` (`feature/google-live-tvideo-journey`). The backend worktree remained read-only; its authorized untracked `pnpm-lock.yaml` and `pnpm-workspace.yaml` were untouched.
- Backend manifest checksum: `bb7d4dcdf6318096c0b9224dc48bcdcb3ff78b325706cdc9c5d39bd4e7da94e4`.
- Canonical manifest JSON SHA-256: `44f1dd88f44acd903c7196b7ad1245e5d2177c18f5dd7de49e137a045bf4d50f`.
- Manifest file SHA-256: `77f196f20c488aa215fc0051dcdbe490a154f651d8edb060c1b098fba7dc846a`.
- Canonical firmware command JSON SHA-256: `6ae4f029a18ba82b01fc3e20da0f78cfb0a5a18c67e43c86a4f4cc78630c316d`.
- Firmware command file SHA-256: `caaffcb293ed243acea2577c83f17bec2403708b4c2003622b33a667f1222f91`.
- Derivative output bytes and SHA values are fixture-only software attestations over `TBOT_TVIDEO_FARM_SOFTWARE_FIXTURE_V1:<cueId>\n`. Derivative IDs come from the backend's real v2 source identity builder using the approved farm asset-version pins, source revision `7`, renderer SHA `88...88`, and font SHA `99...99`. They are not production render outputs.

The manifest contains the exact 19-cue order and two-step conversation contract. The firmware fixture contains 38 frames: one exact `lesson_prepare` with full cue metadata followed by one strict metadata-free `lesson_cinematic_control` start for every cue.

## Generation

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
python3 scripts/generate_tvideo_farm_manifest_fixture.py \
  --node /Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node \
  --backend-root /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey \
  --output tests/fixtures/tvideo_farm_manifest_v2.json
python3 scripts/project_tvideo_farm_firmware_fixture.py \
  --manifest tests/fixtures/tvideo_farm_manifest_v2.json \
  --output /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey/tests/fixtures/tvideo_farm_command_v2.json
```

The wrapper transpiles the checked-in TypeScript generator with the backend's pinned TypeScript compiler, then loads the backend's compiled modules. This avoids changing backend dependencies while the local Rollup native addon is blocked by macOS code-signature policy.

## ESP proof

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
python3 -m pytest -q \
  tests/test_google_live_tool_calls.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_lesson_conversation_runtime.py \
  tests/test_lesson_conversation_integration.py \
  tests/test_flattened_cinematic_contract.py \
  tests/test_lesson_sd_pack_sync.py \
  tests/test_lesson_sd_pack_materializer.py \
  tests/test_tvideo_farm_cross_repo_fixture.py
```

Result: `422 passed, 1 skipped`. The skip is the existing credential-gated Google Live smoke. The farm test downloads and verifies all 19 deterministic fixture payloads, commits the SD pack atomically, reloads its attestation, and projects every cue with exact identity, playback, timing, derivative, SHA, bytes, and SD path.

An expanded runtime/SD regression gate passed `316` tests, including the v2 startup prepare path that shares the same wire schema as conversation cue preparation. The separate legacy `tests/test_lesson_runtime.py` run returned `238 passed, 6 failed`; all six require backend canonical seed files that are absent from this checkout at the paths named by the failures, and none exercise the changed conversation prepare ownership path.

Additional checks:

```bash
python3 -m ruff check scripts/generate_tvideo_farm_manifest_fixture.py \
  scripts/project_tvideo_farm_firmware_fixture.py tests/test_tvideo_farm_cross_repo_fixture.py
python3 -m mypy --follow-imports=skip --ignore-missing-imports \
  scripts/generate_tvideo_farm_manifest_fixture.py \
  scripts/project_tvideo_farm_firmware_fixture.py tests/test_tvideo_farm_cross_repo_fixture.py
python3 -m compileall -q core/lesson/runtime.py scripts tests/test_lesson_conversation_integration.py \
  tests/test_tvideo_farm_cross_repo_fixture.py
git diff --check
```

Result: new-file Ruff and isolated mypy pass; compileall and diff-check pass. Repository-wide Ruff/mypy still report pre-existing legacy debt outside this slice.

## Firmware proof

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_handler_test.sh
```

Result: flattened renderer sanitizer test passed; real handler test passed with `1912 checks`. The handler parses all 38 fixture frames, opens exactly 19 flattened streams, returns `frameZeroReady` for every prepare and `phaseReady` for every strict control-start, and rejects leaked prepare metadata on start.

The supplemental firmware Python proof returned `25 passed, 1 failed`; the failure is a pre-existing source-text delimiter assertion in `tests/test_lesson_sd_sync_attestation_contract.py`. `main/mcp_server.cc` is unchanged from firmware base commit `32d7e9b18cf26b024bb75e0d0b720c5e6b1f248e` at the referenced symbols.

## Backend test environment limitation

The requested backend Vitest command cannot start in this local environment because macOS rejects `@rollup/rollup-darwin-arm64` with a Team-ID code-signature mismatch. No backend files or dependencies were changed to bypass it. The fixture generator still executed the current compiled backend contract/builder and produced the checksum above.

## Remaining gate

Run the attended ESP32-S3 N16R8 soak with real MJPEG derivatives, SD storage, display, Google Live credentials, interruption/reconnect, and repeated once/loop seams. Until that evidence is recorded, status remains **PENDING_ATTENDED_HARDWARE**.
