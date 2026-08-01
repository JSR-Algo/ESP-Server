# TVideo Farm Software Validation

Status: **SOFTWARE ONLY**

Hardware gate: **PENDING_ATTENDED_HARDWARE**

This evidence does not claim ESP32-S3 N16R8 readiness. It proves the canonical farm contract through the backend builder, ESP validation/materialization/projection, and the host-native firmware handler/renderer boundary.

## Fixture provenance

- Backend source commit: `f78f8eae312616d7d1a30bf350404e9d8028bab0` (`feature/google-live-tvideo-journey`), tree `1115da00ba248749db0ef4cbdb669c7b23ed9cbb`. The backend worktree remained read-only; its authorized untracked `pnpm-lock.yaml` and `pnpm-workspace.yaml` were untouched and are excluded from the pinned Git archive.
- Backend build-input SHA-256: `f47ac8c0d0dca550dfb47262037a22a6f1f9e354a7b1d6299cfb43648fdb8be1` over 1,088 tracked `src`, `package.json`, `package-lock.json`, and TypeScript configuration inputs, sorted by path with length-delimited contents. The installed TypeScript compiler SHA-256 is `3ae902c92cc44dace175c0e69e13a4b0899f6983c6121d76b9ab8dd5795e7675`.
- Relevant source SHA-256 values: farm golden `f5826f62a3b876f6266ca702037943f4ef118184ec6d599068207fceadbd1eb0`; cue derivation `22856732ec7d59e19092999e82c0faffabd61bc03b9badf8a2e32afe57398f59`; authoring derivative builder `f6930f54987351ce2cd41266dba179855d06691c4e9545789f4c046de784f705`; manifest builder `95b694505c629ba19c0145aaacd05d5e7003a40f2c8d5af5f9cc11ba6a1f88fc`; checksum constants/canonical bridge `8b1ff0b9b4e873f6151c8c1845e84fe4102d488b4760822040ab01193ecb5dd2`.
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
  --expected-backend-head f78f8eae312616d7d1a30bf350404e9d8028bab0 \
  --output tests/fixtures/tvideo_farm_manifest_v2.json \
  --provenance-output tests/fixtures/tvideo_farm_manifest_v2.provenance.json
python3 scripts/project_tvideo_farm_firmware_fixture.py \
  --manifest tests/fixtures/tvideo_farm_manifest_v2.json \
  --output /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey/tests/fixtures/tvideo_farm_command_v2.json
```

The wrapper rejects a mismatched backend HEAD or modified tracked build input, archives the exact approved Git tree into a path-validated temporary source root, compiles that snapshot into an isolated temporary build, copies tracked runtime `.cjs` inputs such as the canonical manifest serializer, and loads only that temporary build. Backend `dist`, ignored files, and untracked files cannot enter the fixture. Temporary source/build trees are removed automatically. The sidecar `tests/fixtures/tvideo_farm_manifest_v2.provenance.json` pins the full commit/tree, aggregate build-input hash, relevant source hashes, generator hashes, and manifest hashes/checksum.

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
  tests/test_tvideo_farm_cross_repo_fixture.py \
  tests/test_tvideo_farm_fixture_generator.py
```

Result: `428 passed, 1 skipped`. The skip is the existing credential-gated Google Live smoke. The farm test downloads and verifies all 19 deterministic fixture payloads, commits the SD pack atomically, reloads its attestation, and projects every cue with exact identity, playback, timing, derivative, SHA, bytes, and SD path. The generator tests also prove that stale backend `dist`, ignored/untracked source, a wrong commit pin, and modified tracked build inputs cannot influence fixture generation.

An expanded runtime/SD regression gate passed `316` tests, including the v2 startup prepare path that shares the same wire schema as conversation cue preparation. The separate legacy `tests/test_lesson_runtime.py` run returned `238 passed, 6 failed`; all six require backend canonical seed files that are absent from this checkout at the paths named by the failures, and none exercise the changed conversation prepare ownership path.

Additional checks:

```bash
python3 -m ruff check scripts/generate_tvideo_farm_manifest_fixture.py \
  scripts/project_tvideo_farm_firmware_fixture.py tests/test_tvideo_farm_fixture_generator.py \
  tests/test_tvideo_farm_cross_repo_fixture.py
python3 -m mypy --follow-imports=skip --ignore-missing-imports \
  scripts/generate_tvideo_farm_manifest_fixture.py \
  scripts/project_tvideo_farm_firmware_fixture.py tests/test_tvideo_farm_fixture_generator.py \
  tests/test_tvideo_farm_cross_repo_fixture.py
python3 -m compileall -q core/lesson/runtime.py scripts tests/test_lesson_conversation_integration.py \
  tests/test_tvideo_farm_fixture_generator.py tests/test_tvideo_farm_cross_repo_fixture.py
git diff --check
```

Result: new-file Ruff and isolated mypy pass; compileall and diff-check pass. Repository-wide Ruff/mypy still report pre-existing legacy debt outside this slice.

## Firmware proof

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey
./scripts/run_host_native_lesson_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_handler_test.sh
```

Result: legacy and flattened renderer sanitizer tests passed; real handler test passed with `1917 checks`. The handler parses all 38 fixture frames, opens exactly 19 flattened streams, returns `frameZeroReady` for every prepare and `phaseReady` for every strict control-start, and rejects leaked prepare metadata on start. Control-frame `start` is accepted only for an active renderer-v4/template-v2 session; renderer-v3 and renderer-v4/template-v1 reject it while preserving their legacy nested `lesson_start` path.

The supplemental firmware Python proof returned `25 passed, 1 failed`; the failure is a pre-existing source-text delimiter assertion in `tests/test_lesson_sd_sync_attestation_contract.py`. `main/mcp_server.cc` is unchanged from firmware base commit `32d7e9b18cf26b024bb75e0d0b720c5e6b1f248e` at the referenced symbols.

## Backend test environment limitation

The requested backend Vitest command cannot start in this local environment because macOS rejects `@rollup/rollup-darwin-arm64` with a Team-ID code-signature mismatch. No backend files or dependencies were changed to bypass it. The fixture generator instead compiled and executed the exact pinned backend Git source snapshot with the backend's installed TypeScript compiler and produced the checksum above.

## Remaining gate

Run the attended ESP32-S3 N16R8 soak with real MJPEG derivatives, SD storage, display, Google Live credentials, interruption/reconnect, and repeated once/loop seams. Until that evidence is recorded, status remains **PENDING_ATTENDED_HARDWARE**.
