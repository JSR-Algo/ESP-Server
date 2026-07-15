# Task 14 Exact Lesson Cache Eviction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated, fail-closed exact lesson-cache eviction flow from the ESP internal API to a user-only firmware MCP tool, then bind its result to Task 14 cold-preload evidence.

**Architecture:** ESP independently validates the canonical key, resolves the attended connection, computes every protected identity, enforces voice/render safety, calls one fixed MCP tool, and attests the structured reply. Firmware independently validates the key, refuses active lesson runtime, scans and deletes only the exact flat leaf below `/sdcard/tbot/lesson-assets`, and returns privacy-safe structured evidence. The Task 14 cold validator accepts eviction only as a prerequisite; it still requires a fresh assignment and `downloadedCount > 0`.

**Tech Stack:** Python 3.10, aiohttp, asyncio, Pytest; ESP-IDF 5.5.2, C++17, cJSON, POSIX dirent/stat/unlink/rmdir; Docker Compose; ESP32-S3 attended-device validation.

---

## Stable Contract

```text
HTTP: POST /internal/devices/{deviceId}/lesson-assets/evict-cache-key
Auth: X-Mint-Secret
Body: {"cacheKey":"pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
MCP: self.lesson_assets.evict_cache_key
Args: {"cacheKey":"pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

Successful firmware replies have exactly these typed fields:

```json
{
  "cacheKey": "pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "evicted",
  "evicted": true,
  "notFound": false,
  "fileCount": 4,
  "reason": "evicted"
}
```

The other idempotent success is `status=not_found`, `evicted=false`, `notFound=true`, `fileCount=0`, and `reason=not_found`. Firmware refusal codes are `invalid_cache_key`, `lesson_runtime_active`, `path_mismatch`, `nested_directory`, `symlink_rejected`, `unexpected_node_type`, `scan_failed`, `unlink_failed`, and `rmdir_failed`. ESP-only refusal codes are `voice-busy`, `lesson-render-busy`, the named `protected-*` codes, `firmware-timeout`, `firmware-unknown-tool`, `firmware-malformed-result`, `firmware-key-mismatch`, and `firmware-refused`.

Responses and logs must not contain filesystem paths, asset URLs, tokens, child data, transcripts, arbitrary exception strings, or unmasked configuration values.

### Task 1: Implement the ESP Parser, Protected Set, and Orchestrator

**Files:**
- Create: `main/tbot-server/core/lesson/sd_pack_evict.py`
- Create: `main/tbot-server/tests/test_lesson_sd_pack_evict.py`

- [ ] **Step 1: Write RED tests for the canonical key parser**

Define the accepted examples and reject every malformed class in the approved design:

```python
CHECKSUM = "a" * 64
CANONICAL = f"pip-farm-3m/v1-{CHECKSUM}"

@pytest.mark.parametrize("value", [CANONICAL, f"lesson9/v42-{'0' * 64}"])
def test_validate_cache_key_accepts_canonical_values(value):
    assert validate_cache_key(value) == value

@pytest.mark.parametrize("value", [
    "", " " + CANONICAL, CANONICAL + " ",
    "Pip-farm/v1-" + CHECKSUM, "pip--farm/v1-" + CHECKSUM,
    "-pip/v1-" + CHECKSUM, "pip-/v1-" + CHECKSUM,
    "pip/v0-" + CHECKSUM, "pip/v01-" + CHECKSUM, "pip/v+1-" + CHECKSUM,
    "pip/v1-" + "A" * 64, "pip/v1-" + "a" * 63, "pip/v1-" + "g" * 64,
    "/pip/v1-" + CHECKSUM, "../pip/v1-" + CHECKSUM,
    "pip/../v1-" + CHECKSUM, "pip\\v1-" + CHECKSUM,
    "file://pip/v1-" + CHECKSUM, "pip%2fv1-" + CHECKSUM,
    CANONICAL + "/extra", "pip//v1-" + CHECKSUM, CANONICAL + "\n",
])
def test_validate_cache_key_rejects_noncanonical_values(value):
    with pytest.raises(CacheEvictionRefused, match="invalid_cache_key"):
        validate_cache_key(value)
```

Run from `main/tbot-server`:

```bash
python3 -m pytest tests/test_lesson_sd_pack_evict.py -k validate_cache_key -q
```

Expected RED: import failure because `core.lesson.sd_pack_evict` does not exist.

- [ ] **Step 2: Implement the reconstruction-based parser**

Use these exact public names and signature:

```python
CACHE_KEY_RE = re.compile(
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"v(?P<version>[1-9][0-9]*)-(?P<checksum>[0-9a-f]{64})"
)

class CacheEvictionRefused(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

```

Add `def validate_cache_key(value: Any) -> str`. It must require a string, use `fullmatch`, reconstruct `<slug>/v<int(version)>-<checksum>`, require byte equality with the input, and otherwise raise `CacheEvictionRefused("invalid_cache_key")`. It must not trim, normalize, URL-decode, or share firmware validation state.

- [ ] **Step 3: Write RED tests for every protected identity and busy state**

Build fake connections and assert `protected_cache_keys(conn)` maps:

```text
lesson_runtime.asset_cache.cache_key -> protected-active
lesson_runtime_candidate.asset_cache.cache_key -> protected-candidate
lesson_preloading_cache_key -> protected-preloading
lesson_current_cache_key -> protected-current
lesson_previous_known_good_cache_key -> protected-previous-known-good
lesson_sd_pack_activation.current_cache_key -> protected-activation-current
lesson_sd_pack_activation.previous_known_good_cache_key -> protected-activation-previous-known-good
lesson_sd_pack_activation.candidate_cache_key -> protected-activation-candidate
```

Also assert duplicate keys preserve the first/strongest refusal, unrelated keys are absent, `is_realtime_busy()` refuses with `voice-busy`, and runtime/candidate states `PRELOADING`, `RUNNING`, or `PAUSED` refuse with `lesson-render-busy` before MCP.

```bash
python3 -m pytest tests/test_lesson_sd_pack_evict.py -k 'protected or busy' -q
```

Expected RED: missing policy helpers.

- [ ] **Step 4: Implement the protected set and busy policy**

Use the exact signature `def protected_cache_keys(conn: Any) -> Dict[str, str]`.

Collect sources in the exact order listed in Step 3 and insert only the first non-empty string for each key. Treat both the active runtime and candidate runtime as render-busy when their state is `PRELOADING`, `RUNNING`, or `PAUSED`.

- [ ] **Step 5: Write RED async tests for orchestration and firmware attestation**

Use injected async fakes and cover:

```text
offline -> evicted=false, notFound=false, reason=device-offline, no MCP
protected/busy -> exact stable refusal, no MCP
missing MCP client -> firmware-refused
ready request -> exact tool name, exact canonical argument, timeout=30
asyncio timeout -> firmware-timeout
Unknown tool message -> firmware-unknown-tool
non-JSON/non-dict/missing/wrong types -> firmware-malformed-result
returned key absent/non-string -> firmware-malformed-result
returned key different -> firmware-key-mismatch
known firmware refusal status -> firmware-refused
evicted/not_found -> normalized result only
sanitized log -> key, stable code, file count only
```

Assert the call record equals:

```python
{
    "tool_name": "self.lesson_assets.evict_cache_key",
    "args": {"cacheKey": CANONICAL},
    "timeout": 30,
}
```

```bash
python3 -m pytest tests/test_lesson_sd_pack_evict.py -k 'orchestrator or firmware_result or privacy' -q
```

Expected RED: missing orchestrator/parser.

- [ ] **Step 6: Implement the async orchestrator and strict result parser**

Use these exact constants and signatures:

```python
EVICT_TOOL = "self.lesson_assets.evict_cache_key"
EVICT_TIMEOUT_SEC = 30
```

`async def evict_exact_cache_key(connections: Any, device_id: str, requested_cache_key: Any, *, find_connection: Callable[[str], Awaitable[Any]], raw_mcp_call: Optional[Callable[..., Awaitable[Any]]] = None) -> Dict[str, Any]`

`def parse_firmware_result(expected_key: str, raw: Any) -> Dict[str, Any]`

The default raw caller is a lazy import of `core.api.device_mcp_admin_handler._call_raw_mcp_tool`. Do not use `call_mcp_tool`, the generic HTTP MCP endpoint, or `allowUnlisted`. Parse JSON strings or dictionaries only. Accept only coherent `evicted` and `not_found` results; map known refusal statuses to `firmware-refused`; fail closed on all other shapes. Never log raw firmware payloads or exception text.

- [ ] **Step 7: Run GREEN tests and commit Task 1**

```bash
python3 -m pytest tests/test_lesson_sd_pack_evict.py -q
git add main/tbot-server/core/lesson/sd_pack_evict.py \
  main/tbot-server/tests/test_lesson_sd_pack_evict.py
git commit -m "feat(server): orchestrate exact lesson cache eviction"
```

Expected: all focused tests pass with no unawaited-task/resource warnings; commit contains only the two Task 1 files.

### Task 2: Add the ESP Internal Route and Task 14 Cold Evidence Contract

**Files:**
- Create: `main/tbot-server/core/api/lesson_sd_evict_handler.py`
- Create: `main/tbot-server/tests/test_lesson_sd_evict_handler.py`
- Modify: `main/tbot-server/core/http_server.py`
- Modify: `main/tbot-server/scripts/lesson_studio_task14_fault_driver.py`
- Modify: `main/tbot-server/tests/test_lesson_studio_task14_evidence.py`
- Modify: `main/tbot-server/docs/lesson-studio-task14-live-matrix.md`
- Modify: `main/tbot-server/docs/TEST_MATRIX_TASK14.md`

- [ ] **Step 1: Write RED handler/auth/status tests**

Compose with `LessonNudgeHandler` so the route reuses `_authorize` and `_find_connection`. Prove:

```text
secret not configured -> 503, service never called
missing/wrong X-Mint-Secret -> 401, no lookup/service call and no device leak
invalid JSON/non-object/missing cacheKey -> 400
invalid canonical key -> 400, no MCP
offline -> 202 with data.evicted=false and reason=device-offline
busy/protected/firmware failure -> 409 with stable reason and no deletion claim
evicted -> 200 normalized data
not_found -> 200 idempotent normalized data
response excludes paths, tokens, config, raw payload, arbitrary exception text
route appears exactly once in core/http_server.py
```

```bash
python3 -m pytest tests/test_lesson_sd_evict_handler.py -q
```

Expected RED: missing handler and route.

- [ ] **Step 2: Implement the handler and exact route**

Use class `LessonSdEvictHandler` with `__init__(self, config: dict, connections: Any)` storing `LessonNudgeHandler(config, connections)` as `_shared`, and the exact handler signature:

```python
async def handle_post(self, request: web.Request) -> web.Response:
```

Register only:

```python
web.post(
    "/internal/devices/{deviceId}/lesson-assets/evict-cache-key",
    self.lesson_sd_evict_handler.handle_post,
),
```

Do not add a GET/batch/query variant, local-demo bypass, caller-supplied protected override, or generic MCP exposure.

- [ ] **Step 3: Run GREEN route and adjacent integration tests**

```bash
python3 -m pytest \
  tests/test_lesson_sd_evict_handler.py \
  tests/test_lesson_sd_pack_evict.py \
  tests/test_lesson_sd_pack_fanout.py \
  tests/test_lesson_nudge_handler.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Write RED cold-evidence tests**

Require these cold-only fields:

```json
{
  "evictionRequestedCacheKey": "pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "evictionResult": {
    "cacheKey": "pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "status": "evicted",
    "evicted": true,
    "notFound": false,
    "fileCount": 4,
    "reason": "evicted"
  },
  "evictionCompletedUtc": "2026-07-15T00:01:00Z",
  "coldCaptureStartedUtc": "2026-07-15T00:01:01Z",
  "assignmentCreatedUtc": "2026-07-15T00:01:02Z"
}
```

Add failures for missing fields, requested/result/cold key mismatch, refusal or malformed result, contradictory booleans, `not_found` with non-zero file count, timestamps outside the evidence interval, wrong ordering, foreign-key log marker, and `bytesDownloaded == 0`. Accept coherent `evicted` and coherent `not_found`, but always retain existing cold checksum and positive-download gates.

```bash
python3 -m pytest tests/test_lesson_studio_task14_evidence.py -k 'cold or eviction' -q
```

Expected RED: invalid eviction evidence currently passes or required fields are ignored.

- [ ] **Step 5: Implement fail-closed evidence validation and update the runbook**

Add the exact signature `def _cold_eviction_errors(result: Dict[str, Any], raw_logs: str) -> List[str]`.

Require:

```text
evictionRequestedCacheKey == evictionResult.cacheKey == result.cacheKey
status/booleans/reason/fileCount form a coherent evicted or not_found result
utcStart <= evictionCompletedUtc < coldCaptureStartedUtc < assignmentCreatedUtc < utcEnd
raw log contains lesson_cache_evict with the same cacheKey/status/fileCount
cold preload still has downloadedCount > 0 and checksum verification
```

Replace manual/raw filesystem deletion in `lesson-studio-task14-live-matrix.md` with:

```bash
curl --fail-with-body --silent --show-error \
  -X POST \
  -H "X-Mint-Secret: ${TBOT_DEVICE_MINT_SECRET}" \
  -H 'Content-Type: application/json' \
  --data "{\"cacheKey\":\"${CACHE_KEY}\"}" \
  "http://127.0.0.1:8003/internal/devices/${DEVICE_ID}/lesson-assets/evict-cache-key" \
  | tee "$EVIDENCE_ROOT/cold/eviction-response.json"
```

The runbook order is bounded log capture, exact eviction, reply/key attestation, cold capture start, fresh non-terminal assignment creation, lesson execution, then strict validation. `command.txt` must contain `${TBOT_DEVICE_MINT_SECRET}` literally, never its value. `TEST_MATRIX_TASK14.md` remains `NOT PASS - live evidence required`.

- [ ] **Step 6: Run GREEN evidence gates and commit Task 2**

```bash
python3 -m pytest \
  tests/test_lesson_sd_evict_handler.py \
  tests/test_lesson_sd_pack_evict.py \
  tests/test_lesson_studio_task14_evidence.py -q
python3 scripts/lesson_studio_task14_fault_driver.py --self-test
python3 scripts/lesson_studio_task14_soak.py --self-test
python3 scripts/lesson_studio_task14_log_audit.py --self-test
git add main/tbot-server/core/api/lesson_sd_evict_handler.py \
  main/tbot-server/core/http_server.py \
  main/tbot-server/scripts/lesson_studio_task14_fault_driver.py \
  main/tbot-server/tests/test_lesson_sd_evict_handler.py \
  main/tbot-server/tests/test_lesson_studio_task14_evidence.py \
  main/tbot-server/docs/lesson-studio-task14-live-matrix.md \
  main/tbot-server/docs/TEST_MATRIX_TASK14.md
git commit -m "feat(server): expose exact cache eviction evidence flow"
```

Expected: all commands exit `0`; no Task 14 live row is promoted.

### Task 3: Implement the Firmware Parser, Exact Leaf Deletion, and User-Only MCP Tool

**Files:**
- Create: `main/lesson_asset_cache_evict.h`
- Create: `main/lesson_asset_cache_evict.cc`
- Create: `tests/native/lesson_asset_cache_evict_host_test.cc`
- Create: `scripts/run_host_native_lesson_asset_cache_evict_test.sh`
- Create: `tests/test_lesson_asset_cache_evict_contract.py`
- Modify: `main/mcp_server.cc`
- Modify: `main/CMakeLists.txt`

- [ ] **Step 1: Write RED static and native tests**

The Python contract must assert:

```python
assert 'AddUserOnlyTool("self.lesson_assets.evict_cache_key"' in mcp_body
assert 'Property("cacheKey", kPropertyTypeString' in mcp_body
assert "Application::GetInstance().IsLessonRuntimeActive()" in mcp_body
assert "EvictLessonAssetCacheKey(cache_key, lesson_runtime_active)" in mcp_body
assert '"lesson_asset_cache_evict.cc"' in main_cmake
assert '"/sdcard/tbot/lesson-assets"' in helper_source
assert "allowUnlisted" not in mcp_body
assert "assetPack" not in mcp_body
```

The native test must mirror all ESP parser cases and prove active-runtime refusal, absent idempotence, exact flat-leaf deletion, sibling/current/PVG/root/slug-parent preservation, nested-directory refusal, symlink refusal, unexpected-node refusal, and no success claim on unlink/rmdir failure.

Use these exact public types:

```cpp
enum class LessonAssetCacheEvictCode {
    kEvicted, kNotFound, kInvalidCacheKey, kLessonRuntimeActive,
    kPathMismatch, kNestedDirectory, kSymlinkRejected,
    kUnexpectedNodeType, kScanFailed, kUnlinkFailed, kRmdirFailed,
};

struct LessonAssetCacheEvictResult {
    LessonAssetCacheEvictCode code;
    std::string cache_key;
    int file_count;
    bool evicted;
    bool not_found;
};

bool IsCanonicalLessonCacheKey(const std::string& value);
const char* LessonAssetCacheEvictCodeName(LessonAssetCacheEvictCode code);
LessonAssetCacheEvictResult EvictLessonAssetCacheKey(
    const std::string& cache_key,
    bool lesson_runtime_active
);
```

- [ ] **Step 2: Add the host runner and confirm RED**

Create `scripts/run_host_native_lesson_asset_cache_evict_test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tbot-lesson-cache-evict.XXXXXX")"
trap 'rm -rf "${BUILD_DIR}" /tmp/tbot-lesson-asset-cache-evict-host' EXIT
"${CXX:-clang++}" -std=c++17 -Wall -Wextra -Werror \
  -DTBOT_LESSON_ASSET_ROOT='"/tmp/tbot-lesson-asset-cache-evict-host"' \
  -I"${ROOT}/main" \
  "${ROOT}/main/lesson_asset_cache_evict.cc" \
  "${ROOT}/tests/native/lesson_asset_cache_evict_host_test.cc" \
  -o "${BUILD_DIR}/lesson_asset_cache_evict_host_test"
"${BUILD_DIR}/lesson_asset_cache_evict_host_test"
```

```bash
chmod +x scripts/run_host_native_lesson_asset_cache_evict_test.sh
scripts/run_host_native_lesson_asset_cache_evict_test.sh
python3 -m pytest tests/test_lesson_asset_cache_evict_contract.py -q
```

Expected RED: missing helper and MCP registration.

- [ ] **Step 3: Implement independent validation and two-pass deletion**

The source uses:

```cpp
#ifndef TBOT_LESSON_ASSET_ROOT
#define TBOT_LESSON_ASSET_ROOT "/sdcard/tbot/lesson-assets"
#endif
```

Parse slug/version/checksum character-by-character, reconstruct the key, and require byte equality. Do not trim, normalize, URL-decode, call `realpath` to accept input, or share ESP state.

Deletion order is exact: validate key; refuse active runtime; construct and verify fixed-root prefix; `lstat` exact leaf; return `not_found` on `ENOENT`; require a non-symlink directory; first scan every direct child with `lstat` and require regular files only; close without mutation on any nested/symlink/unexpected node; reopen/unlink only validated direct names; `rmdir` only the exact leaf; claim `evicted` only after `rmdir` succeeds. Never recursively delete or remove root, slug parent, siblings, shared assets, or activation metadata.

- [ ] **Step 4: Register the fixed user-only tool and build source**

Add `"lesson_asset_cache_evict.cc"` to `main/CMakeLists.txt`. Register inside `McpServer::AddUserOnlyTools`:

```cpp
AddUserOnlyTool("self.lesson_assets.evict_cache_key",
    "Evict one exact inactive lesson asset cache key.",
    PropertyList({Property("cacheKey", kPropertyTypeString)}),
    [](const PropertyList& properties) -> ReturnValue {
        const auto cache_key = properties["cacheKey"].value<std::string>();
        const bool lesson_runtime_active =
            Application::GetInstance().IsLessonRuntimeActive();
        const auto result = EvictLessonAssetCacheKey(cache_key, lesson_runtime_active);
        cJSON* json = cJSON_CreateObject();
        cJSON_AddStringToObject(json, "cacheKey", result.cache_key.c_str());
        cJSON_AddStringToObject(json, "status", LessonAssetCacheEvictCodeName(result.code));
        cJSON_AddBoolToObject(json, "evicted", result.evicted);
        cJSON_AddBoolToObject(json, "notFound", result.not_found);
        cJSON_AddNumberToObject(json, "fileCount", result.file_count);
        cJSON_AddStringToObject(json, "reason", LessonAssetCacheEvictCodeName(result.code));
        return json;
    });
```

For invalid input, `result.cache_key` must be empty rather than echoing a path-like value. Log only a validated canonical key, stable code, and file count; omit the key entirely when validation fails.

- [ ] **Step 5: Run GREEN firmware tests and commit Task 3**

```bash
scripts/run_host_native_lesson_asset_cache_evict_test.sh
python3 -m pytest \
  tests/test_lesson_asset_cache_evict_contract.py \
  tests/test_lesson_sd_sync_attestation_contract.py \
  tests/test_mcp_tools_pagination_contract.py \
  tests/test_realtime_voice_state.py -q
git add main/lesson_asset_cache_evict.h \
  main/lesson_asset_cache_evict.cc \
  main/mcp_server.cc \
  main/CMakeLists.txt \
  tests/native/lesson_asset_cache_evict_host_test.cc \
  tests/test_lesson_asset_cache_evict_contract.py \
  scripts/run_host_native_lesson_asset_cache_evict_test.sh
git commit -m "feat(firmware): evict exact lesson cache leaf safely"
```

Expected: native test reports a non-zero check count, selected Pytest suite passes, and the commit contains only Task 3 files.

### Task 4: Verify, Deploy, Flash, Smoke-Test, and Hand Off Task 14 Cold Proof

**Files:**
- Modify: `robot/docs/TEST_MATRIX.md` only after real-device validators pass.
- Do not modify rewards-owned code or `manager-mobile`.

- [ ] **Step 1: Run complete software verification before deployment**

ESP worktree:

```bash
cd /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio/main/tbot-server
python3 -m pytest tests -q
```

Firmware worktree:

```bash
cd /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio
scripts/run_host_native_lesson_asset_cache_evict_test.sh
scripts/run_host_native_lesson_coverage.sh
python3 -m pytest tests -q
```

Expected: every command exits `0`; record exact passed/skipped/check counts. Fix any reproduced defect through a new focused RED/GREEN commit; do not amend Tasks 1-3.

- [ ] **Step 2: Build firmware with pinned ESP-IDF 5.5.2**

```bash
source "$HOME/esp/esp-idf-v5.5.2/export.sh"
idf.py -B build-task14-cache-evict reconfigure build
shasum -a 256 build-task14-cache-evict/xiaozhi.bin
```

Expected: build exit `0`, release partition headroom remains acceptable, and binary SHA-256 is recorded. Inspect any `dependencies.lock` change; accept only an intentional IDF 5.5.2/local-override change in a separate commit.

- [ ] **Step 3: Check commit and worktree boundaries**

```bash
git -C /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio log -3 --oneline
git -C /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio log -2 --oneline
git -C /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio diff --check
git -C /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio diff --check
git -C /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio status --short
git -C /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio status --short
```

Expected: intentional commits exist, diffs are whitespace-clean, and `main/manager-web/output/` plus firmware build directories are not staged.

- [ ] **Step 4: Deploy the committed ESP service with Docker Compose**

```bash
cd /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio
docker compose -f docs/docker/docker-compose.lesson-studio-e2e.yml up -d --build
docker compose -f docs/docker/docker-compose.lesson-studio-e2e.yml ps
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -X POST -H 'Content-Type: application/json' \
  --data '{"cacheKey":"pip-farm-3m/v1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' \
  http://127.0.0.1:8003/internal/devices/28:84:85:85:1a:80/lesson-assets/evict-cache-key
```

Expected: services are healthy; unauthenticated smoke returns `401` and cannot call MCP.

- [ ] **Step 5: Flash the exact verified firmware image**

```bash
cd /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio
source "$HOME/esp/esp-idf-v5.5.2/export.sh"
idf.py -B build-task14-cache-evict -p /dev/cu.usbmodem1101 flash
```

Expected: flash exits `0`. Capture at least 90 seconds of passive boot/heartbeat logs before eviction. Stop on reset, WDT, panic, reconnect storm, or `passive_ws_pong_timeout`.

- [ ] **Step 6: Run bounded exact-eviction smoke tests on the attended robot**

Use robot `28:84:85:85:1a:80` only. In order:

```text
invalid key -> HTTP 400, no MCP
known active/current/PVG/candidate/preloading key -> HTTP 409 exact protected code, no MCP
valid absent key -> HTTP 200 not_found, fileCount=0, no reset
disposable synced but inactive/unprotected key -> HTTP 200 evicted with exact file count
repeat same disposable key -> HTTP 200 not_found
```

After success, verify active/current/PVG/sibling directories, shared store, and activation metadata remain intact. Preserve server/serial logs and stop immediately on key mismatch, nested-content refusal, deletion outside the exact target, or firmware instability.

- [ ] **Step 7: Hand off the exact cold sequence to Task 14 evidence capture**

Follow `main/tbot-server/docs/lesson-studio-task14-live-matrix.md` with canonical fixture `production-farm-english-358` version `2026-07-11.1` and `pip-farm-3m`. Bind the exact eviction response and timestamps before creating a fresh non-terminal assignment. The cold validator must prove the same cache key, `downloadedCount > 0`, zero failed count, READY/checksum equality, and no reset. Then run a fresh warm assignment without eviction and require `downloadedCount=0` plus `asset_cache_hit`.

Eviction success alone never promotes T14-LIVE-02. Continue preview parity, offline/fault matrix, rollback, 100+ transitions, soak, and audit using the existing strict validators.

- [ ] **Step 8: Record sanitized evidence without a false production claim**

Update `/Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md` only after validators pass. Record exact commands/exit codes, backend/ESP/firmware commits, firmware version and binary SHA-256, device identity, UTC interval, requested key and normalized result, assignment/session/version/checksums, SRAM/PSRAM metrics, operator, artifact paths, and artifact SHA-256 hashes.

`robot/` is not a Git repository, so verify the evidence file directly:

```bash
test -s /Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md
shasum -a 256 /Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md
git -C /Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio diff --check
git -C /Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio diff --check
```

Keep raw/private logs in the approved evidence directory and reference immutable hashes. Do not commit credentials, build outputs, `main/manager-web/output/`, rewards-session changes, or raw serial/server logs. Task 14 remains `NOT PASS` until every existing live row and the hardware SRAM release threshold have real-device proof.
