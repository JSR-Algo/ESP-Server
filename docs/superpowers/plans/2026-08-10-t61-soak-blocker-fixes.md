# T6.1 Soak Blocker Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ESP manager-API socket leak and make real SD materialization available in the T5.3 simulation stack so the complete T6.1 soak and Ship checklist can pass.

**Architecture:** Manager-API requests will own short-lived `httpx.AsyncClient` instances and close them deterministically in the request boundary; no WebSocket lifecycle code will manage shared HTTP transports. The lesson simulation compose will explicitly supply the production example SD per-file byte limit, protected by a compose contract test.

**Tech Stack:** Python 3, asyncio, httpx, pytest, Docker Compose, the existing T5.3 lesson simulation, and `robot/scripts/lesson_studio_task14_soak.py`.

---

## File Map

- Modify `main/tbot-server/config/manage_api_client.py`: remove per-event-loop request-client retention and close each client at the request boundary.
- Modify `main/tbot-server/tests/test_manage_api_client_edges.py`: specify fresh-client creation and success/failure closure behavior.
- Modify `main/tbot-server/tests/test_manage_api_client_cleanup.py`: retain shutdown/reset compatibility without depending on normal cached clients.
- Modify `docs/docker/docker-compose.lesson-e2e-sim.yml`: provide the bounded SD materialization file limit to the ESP service.
- Create `main/tbot-server/tests/test_lesson_e2e_sim_compose.py`: lock the simulation environment contract.
- Update `docs/qa/ad-hoc/2026-08-10-t61-soak.md`: record red-green proof, full soak results, and Ship evidence.
- Update workspace-level `robot/docs/evidence/lesson-memory-soak.md`, `lesson-prod/t61-soak.md`, and `LESSON_PRODUCTION_PLAN.md` after the passing run.

### Task 1: Prove request clients are retained

**Files:**
- Modify: `main/tbot-server/tests/test_manage_api_client_edges.py`
- Test: `main/tbot-server/tests/test_manage_api_client_edges.py`

- [ ] **Step 1: Replace the cache expectation with fresh-client ownership tests**

Replace `test_ensure_async_client_builds_one_client_per_loop` with:

```python
@pytest.mark.asyncio
async def test_ensure_async_client_builds_a_fresh_client_per_request(monkeypatch):
    mac = _load_module()
    created = []

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

    monkeypatch.setattr(mac.httpx, "AsyncClient", _FakeAsyncClient)
    mac.ManageApiClient.config = {"url": "http://manager.test", "timeout": 9}
    mac.ManageApiClient._secret = "secret"

    first = await mac.ManageApiClient._ensure_async_client()
    second = await mac.ManageApiClient._ensure_async_client()

    assert first is not second
    assert len(created) == 2
    assert first.kwargs["base_url"] == "http://manager.test"
    assert first.kwargs["timeout"] == 9
    assert first.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert first.kwargs["headers"]["Accept"] == "application/json"
    assert "PythonClient/2.0" in first.kwargs["headers"]["User-Agent"]
```

Extend `_RequestClient` with `closed = False` and an `aclose()` method, then add assertions to
`test_async_request_success_and_business_error_paths` that each installed client is closed after
success and after every business-error case.

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
python3 -m pytest -q main/tbot-server/tests/test_manage_api_client_edges.py \
  -k 'fresh_client_per_request or async_request_success_and_business_error_paths'
```

Expected: FAIL because `_ensure_async_client()` returns the same cached object and
`_async_request()` does not close the client.

- [ ] **Step 3: Commit the failing regression tests**

```bash
git add main/tbot-server/tests/test_manage_api_client_edges.py
git commit -m "test: reproduce manager API client retention"
```

### Task 2: Close every manager-API request client

**Files:**
- Modify: `main/tbot-server/config/manage_api_client.py`
- Modify: `main/tbot-server/tests/test_manage_api_client_cleanup.py`
- Test: `main/tbot-server/tests/test_manage_api_client_edges.py`
- Test: `main/tbot-server/tests/test_manage_api_client_cleanup.py`

- [ ] **Step 1: Remove the per-loop cache from client creation**

Change `_ensure_async_client()` to validate that it runs in an async context and return a newly
configured client directly:

```python
@classmethod
async def _ensure_async_client(cls):
    """Create a request-owned async client in the current event loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:
        raise Exception("Must be called in async context") from exc

    limits = httpx.Limits(max_keepalive_connections=0)
    return httpx.AsyncClient(
        base_url=cls.config.get("url"),
        headers={
            "User-Agent": f"PythonClient/2.0 (PID:{os.getpid()})",
            "Accept": "application/json",
            "Authorization": "Bearer " + cls._secret,
        },
        timeout=cls.config.get("timeout", 30),
        limits=limits,
    )
```

- [ ] **Step 2: Close the request client in all paths**

In `_async_request()`, initialize `client = None`, obtain it inside `try`, retain response closure,
and close the client in the same `finally` block:

```python
client = None
response = None
try:
    client = await cls._ensure_async_client()
    response = await client.request(method, endpoint.lstrip("/"), **kwargs)
    response.raise_for_status()
    result = response.json()

    if result.get("code") == 10041:
        raise DeviceNotFoundException(result.get("msg"))
    if result.get("code") == 10042:
        raise DeviceBindException(result.get("msg"))
    if result.get("code") != 0:
        raise Exception(f"APIReturnError: {result.get('msg', 'Unknown error')}")

    return result.get("data") if result.get("code") == 0 else None
finally:
    if response is not None:
        await response.aclose()
    if client is not None:
        await client.aclose()
```

Keep `_async_clients` and shutdown helpers temporarily as compatibility state because existing
process-shutdown callers and tests may populate it, but normal requests must never add entries.

- [ ] **Step 3: Update cleanup compatibility test wording**

Rename the cleanup test to
`test_safe_close_closes_legacy_clients_when_called_inside_running_loop`; retain its assertions so
shutdown still drains any legacy/test-injected client and resets the singleton.

- [ ] **Step 4: Run manager-client tests and observe GREEN**

Run:

```bash
python3 -m pytest -q \
  main/tbot-server/tests/test_manage_api_client_edges.py \
  main/tbot-server/tests/test_manage_api_client_cleanup.py \
  main/tbot-server/tests/test_manage_api_client_branch_gaps.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the runtime fix**

```bash
git add main/tbot-server/config/manage_api_client.py \
  main/tbot-server/tests/test_manage_api_client_cleanup.py
git commit -m "fix: close manager API clients per request"
```

### Task 3: Prove the simulation omits the SD materialize limit

**Files:**
- Create: `main/tbot-server/tests/test_lesson_e2e_sim_compose.py`
- Test: `main/tbot-server/tests/test_lesson_e2e_sim_compose.py`

- [ ] **Step 1: Add the failing compose contract test**

```python
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docs/docker/docker-compose.lesson-e2e-sim.yml"


def test_lesson_e2e_sim_sets_bounded_sd_materialize_limit():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["esp-server"]["environment"]

    assert str(environment["LESSON_SD_MAX_FILE_BYTES"]) == "33554432"
```

- [ ] **Step 2: Run the test and observe RED**

Run:

```bash
python3 -m pytest -q main/tbot-server/tests/test_lesson_e2e_sim_compose.py
```

Expected: FAIL with missing `LESSON_SD_MAX_FILE_BYTES`.

- [ ] **Step 3: Commit the failing fixture test**

```bash
git add main/tbot-server/tests/test_lesson_e2e_sim_compose.py
git commit -m "test: require SD limit in lesson simulation"
```

### Task 4: Configure real SD materialization

**Files:**
- Modify: `docs/docker/docker-compose.lesson-e2e-sim.yml`
- Test: `main/tbot-server/tests/test_lesson_e2e_sim_compose.py`

- [ ] **Step 1: Set the production example limit on the ESP service**

Add this entry beside the existing lesson runtime environment variables:

```yaml
LESSON_SD_MAX_FILE_BYTES: "33554432"
```

- [ ] **Step 2: Run the fixture and materializer tests**

Run:

```bash
python3 -m pytest -q \
  main/tbot-server/tests/test_lesson_e2e_sim_compose.py \
  main/tbot-server/tests/test_lesson_sd_pack_materializer.py
```

Expected: all tests pass.

- [ ] **Step 3: Commit the fixture fix**

```bash
git add docs/docker/docker-compose.lesson-e2e-sim.yml
git commit -m "fix: configure SD materialization in lesson simulation"
```

### Task 5: Verify the live blocker fixes

**Files:**
- No code changes expected.
- Use: `robot/scripts/lesson_studio_task14_soak.py`

- [ ] **Step 1: Run the focused regression suite**

```bash
python3 -m pytest -q \
  main/tbot-server/tests/test_manage_api_client_edges.py \
  main/tbot-server/tests/test_manage_api_client_cleanup.py \
  main/tbot-server/tests/test_manage_api_client_branch_gaps.py \
  main/tbot-server/tests/test_connection_edges.py \
  main/tbot-server/tests/test_lesson_e2e_sim_compose.py \
  main/tbot-server/tests/test_lesson_sd_pack_materializer.py
```

Expected: zero failures.

- [ ] **Step 2: Rebuild and start the branch simulation stack**

Run from the worktree:

```bash
TBOT_ROOT=/Users/manhhodinh/Documents/TBOT \
  ./docs/docker/lesson-e2e-sim/up.sh
```

Expected: `tbot-ls-e2e-esp` is rebuilt from the task branch and becomes healthy.

- [ ] **Step 3: Repeat the isolated WebSocket churn reproduction**

Restart the ESP container, sample `/proc/1/status`, `/proc/1/fd`, and `/proc/1/net/tcp`, run at
least 100 churn connections through the T6.1 driver, wait 30 seconds, and sample again.

Expected: successful churn, FD delta <= 8, thread delta <= 4, RSS delta <= 32 MiB, and no
monotonically accumulating established sockets to port 8002.

- [ ] **Step 4: Exercise the real materialize endpoint**

Use the T6.1 driver's SD cycle against the rebuilt stack.

Expected: no `INVALID_LIMIT`; all materialize/evict cycles succeed and disk use returns within
4 KiB of baseline.

### Task 6: Run the complete T6.1 gate

**Files:**
- Modify after results: `docs/qa/ad-hoc/2026-08-10-t61-soak.md`
- Modify after results: `/Users/manhhodinh/Documents/TBOT/robot/docs/evidence/lesson-memory-soak.md`

- [ ] **Step 1: Run the release-sized soak**

From `/Users/manhhodinh/Documents/TBOT/robot`:

```bash
python3 scripts/lesson_studio_task14_soak.py --run-soak \
  --lessons 20 \
  --idle-seconds 3600 \
  --ws-churn 100 \
  --sd-cycles 10 \
  --generation-requests 20 \
  --artifact-dir evidence/t61-soak-2026-08-10
```

Expected: exit 0 and report status `PASS`; every `done_checks` value is true.

- [ ] **Step 2: Record exact full-soak evidence**

Update both evidence files with the command, timestamps, assignment completion count, resource
series, idle deltas, churn results, generation queue depth and latency, ingest count/latency, SD
usage, container restart count, and report artifact checksum. Do not summarize a failing metric as
passing.

- [ ] **Step 3: Resolve findings only after live proof**

Change F-T61-01 and F-T61-02 from OPEN to RESOLVED in
`/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`, with the exact regression/full-soak
evidence link.

### Task 7: Run the Ship checklist

**Files:**
- Update: `docs/qa/ad-hoc/2026-08-10-t61-soak.md`
- Update: `/Users/manhhodinh/Documents/TBOT/lesson-prod/t61-soak.md`
- Update: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`

- [ ] **Step 1: Rebase on current main and re-verify branch tip**

Run the task verify command with `python3`, the focused suite from Task 5, and the repository's
standard suite. Record exact pass/fail counts. If the literal `python` command still fails because
the host has no shim, record it without hiding the successful `python3` equivalent.

- [ ] **Step 2: Commit evidence and run the repository gate**

Copy the completed evidence into the worktree if it was initially written in canonical `main`,
commit all branch-owned files, then use the T0.4 gate/merge scripts for branch
`lesson-prod/t61-soak-fixes`. Do not push unless the repository protocol explicitly requires it.

- [ ] **Step 3: Deploy decision**

Record `no deploy`: the runtime code is included in future ESP releases, while the simulation
compose is local QA infrastructure. Do not run VPS deployment scripts as part of T6.1.

- [ ] **Step 4: Verify on main**

Run through the workspace helper:

```bash
bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/verify-on-main.sh \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server -- \
  python3 -m pytest -q main/tbot-server/tests/test_manage_api_client_edges.py \
    main/tbot-server/tests/test_manage_api_client_cleanup.py \
    main/tbot-server/tests/test_lesson_e2e_sim_compose.py
```

Also rerun `python3 scripts/lesson_studio_task14_soak.py --help` from the workspace-level robot
directory and record the result.

- [ ] **Step 5: Remove the task worktree and branch**

Verify the worktree is clean and `lesson-prod/t61-soak-fixes` is an ancestor of `main`, then remove
`.worktrees/t61-soak-fixes` and delete the merged branch. Do not touch the existing T6.2
worktrees.

- [ ] **Step 6: Set DONE**

Set T6.1 to DONE with the final evidence link in both status locations only after main verification
and worktree cleanup succeed.
