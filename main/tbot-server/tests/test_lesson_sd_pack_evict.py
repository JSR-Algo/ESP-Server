import asyncio
import json
from types import SimpleNamespace

import pytest

from core.activity_lease import (
    ActivityLeaseCoordinator,
    ActivityOperation,
    ExclusiveDisposition,
)
from core.lesson.sd_pack_evict import (
    EVICT_TIMEOUT_SEC,
    EVICT_TOOL,
    CacheEvictionRefused,
    evict_exact_cache_key,
    parse_firmware_result,
    protected_cache_keys,
    validate_cache_key,
)

CHECKSUM = "a" * 64
CANONICAL = f"pip-farm-3m/v1-{CHECKSUM}"
OTHER = f"lesson9/v42-{'0' * 64}"


@pytest.mark.parametrize("value", [CANONICAL, OTHER])
def test_validate_cache_key_accepts_canonical_values(value):
    assert validate_cache_key(value) == value


@pytest.mark.parametrize(
    "value",
    [
        f"{'a' * 128}/v1-{CHECKSUM}",
        f"slug/v9999999999-{CHECKSUM}",
        f"{'a' * 128}/v9999999999-{CHECKSUM}",
    ],
)
def test_validate_cache_key_accepts_exact_protocol_boundaries(value):
    assert len(value.encode("ascii")) <= 205
    assert validate_cache_key(value) == value


@pytest.mark.parametrize(
    "value",
    [
        f"{'a' * 129}/v1-{CHECKSUM}",
        f"slug/v99999999999-{CHECKSUM}",
    ],
)
def test_validate_cache_key_rejects_values_beyond_protocol_boundaries(value):
    with pytest.raises(CacheEvictionRefused, match="^invalid_cache_key$") as exc_info:
        validate_cache_key(value)
    assert value not in str(exc_info.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        7,
        b"pip",
        "",
        " " + CANONICAL,
        CANONICAL + " ",
        "Pip-farm/v1-" + CHECKSUM,
        "píp-farm/v1-" + CHECKSUM,
        "pip--farm/v1-" + CHECKSUM,
        "-pip/v1-" + CHECKSUM,
        "pip-/v1-" + CHECKSUM,
        "pip/v0-" + CHECKSUM,
        "pip/v01-" + CHECKSUM,
        "pip/v+1-" + CHECKSUM,
        "pip/v1-" + "A" * 64,
        "pip/v1-" + "a" * 63,
        "pip/v1-" + "g" * 64,
        "/pip/v1-" + CHECKSUM,
        "../pip/v1-" + CHECKSUM,
        "pip/../v1-" + CHECKSUM,
        "pip\\v1-" + CHECKSUM,
        "file://pip/v1-" + CHECKSUM,
        "pip%2fv1-" + CHECKSUM,
        CANONICAL + "/extra",
        "pip//v1-" + CHECKSUM,
        CANONICAL + "\n",
        "pip.v1/v1-" + CHECKSUM,
        "https://pip/v1-" + CHECKSUM,
        "pip/v1-" + CHECKSUM + "\x00",
    ],
)
def test_validate_cache_key_rejects_noncanonical_values(value):
    with pytest.raises(CacheEvictionRefused, match="^invalid_cache_key$"):
        validate_cache_key(value)


def _runtime(cache_key, state="IDLE"):
    return SimpleNamespace(state=state, asset_cache=SimpleNamespace(cache_key=cache_key))


def test_protected_cache_keys_collects_all_sources_in_stable_priority_order():
    conn = SimpleNamespace(
        lesson_runtime=_runtime("active"),
        lesson_runtime_candidate=_runtime("candidate"),
        lesson_preloading_cache_key="preloading",
        lesson_current_cache_key="current",
        lesson_previous_known_good_cache_key="pvg",
        lesson_sd_pack_activation=SimpleNamespace(
            current_cache_key="activation-current",
            previous_known_good_cache_key="activation-pvg",
            candidate_cache_key="activation-candidate",
        ),
    )
    assert protected_cache_keys(conn) == {
        "active": "protected-active",
        "candidate": "protected-candidate",
        "preloading": "protected-preloading",
        "current": "protected-current",
        "pvg": "protected-previous-known-good",
        "activation-current": "protected-activation-current",
        "activation-pvg": "protected-activation-previous-known-good",
        "activation-candidate": "protected-activation-candidate",
    }


def test_protected_cache_keys_keeps_first_reason_and_ignores_invalid_sources():
    conn = SimpleNamespace(
        lesson_runtime=_runtime(CANONICAL),
        lesson_runtime_candidate=_runtime(CANONICAL),
        lesson_preloading_cache_key=CANONICAL,
        lesson_current_cache_key="",
        lesson_previous_known_good_cache_key=None,
        lesson_sd_pack_activation=SimpleNamespace(
            current_cache_key=CANONICAL,
            previous_known_good_cache_key=5,
            candidate_cache_key=OTHER,
        ),
    )
    assert protected_cache_keys(conn) == {
        CANONICAL: "protected-active",
        OTHER: "protected-activation-candidate",
    }


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))


def _conn(*, voice_busy=False, state="IDLE", candidate_state="IDLE", mcp_client=True):
    coordinator = ActivityLeaseCoordinator(asyncio.get_running_loop())
    tasks = set()
    conn = SimpleNamespace(
        is_realtime_busy=lambda: voice_busy,
        lesson_runtime=_runtime("active", state),
        lesson_runtime_candidate=_runtime("candidate", candidate_state),
        mcp_client=object() if mcp_client else None,
        logger=RecordingLogger(),
        activity_leases=coordinator,
        _lesson_pull_lock=asyncio.Lock(),
        mcp_background_tasks=tasks,
    )

    def task_done(task):
        tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def schedule_mcp_background_task(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(task_done)
        return task

    conn.schedule_mcp_background_task = schedule_mcp_background_task
    return conn


def _finder(conn, calls=None):
    async def find_connection(device_id):
        if calls is not None:
            calls.append(device_id)
        return conn

    return find_connection


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PRELOADING", "RUNNING", "PAUSED"])
@pytest.mark.parametrize("candidate", [False, True])
async def test_orchestrator_refuses_render_busy_before_mcp(state, candidate):
    conn = _conn(state="IDLE" if candidate else state, candidate_state=state if candidate else "IDLE")
    raw_calls = []

    async def raw_call(*args, **kwargs):
        raw_calls.append((args, kwargs))

    with pytest.raises(CacheEvictionRefused, match="^lesson-render-busy$"):
        await evict_exact_cache_key({}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call)
    assert raw_calls == []


@pytest.mark.asyncio
async def test_orchestrator_refuses_voice_busy_before_mcp():
    conn = _conn(voice_busy=True)
    raw_calls = []

    async def raw_call(*args, **kwargs):
        raw_calls.append((args, kwargs))

    with pytest.raises(CacheEvictionRefused, match="^voice-busy$"):
        await evict_exact_cache_key({}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call)
    assert raw_calls == []


@pytest.mark.asyncio
async def test_orchestrator_refuses_protected_key_before_mcp():
    conn = _conn()
    conn.lesson_current_cache_key = CANONICAL
    raw_calls = []

    async def raw_call(*args, **kwargs):
        raw_calls.append((args, kwargs))

    with pytest.raises(CacheEvictionRefused, match="^protected-current$"):
        await evict_exact_cache_key({}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call)
    assert raw_calls == []


@pytest.mark.asyncio
async def test_orchestrator_returns_structured_offline_result_without_mcp():
    lookups = []
    result = await evict_exact_cache_key(
        {"ignored": object()},
        "device-1",
        CANONICAL,
        find_connection=_finder(None, lookups),
        raw_mcp_call=lambda *_args, **_kwargs: pytest.fail("MCP must not be called"),
    )
    assert lookups == ["device-1"]
    assert result == {
        "cacheKey": CANONICAL,
        "status": "device-offline",
        "evicted": False,
        "notFound": False,
        "fileCount": 0,
        "reason": "device-offline",
    }


@pytest.mark.asyncio
async def test_orchestrator_refuses_missing_mcp_client():
    conn = _conn(mcp_client=False)
    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        await evict_exact_cache_key({}, "device-1", CANONICAL, find_connection=_finder(conn))
    await asyncio.sleep(0)
    assert not conn.activity_leases.has_exclusive_lease()
    assert conn.mcp_background_tasks == set()


@pytest.mark.asyncio
async def test_orchestrator_calls_only_fixed_tool_with_exact_arguments():
    conn = _conn()
    calls = []

    async def raw_call(call_conn, mcp_client, tool_name, args, *, timeout):
        assert call_conn is conn
        assert mcp_client is conn.mcp_client
        calls.append({"tool_name": tool_name, "args": args, "timeout": timeout})
        return json.dumps(
            {
                "cacheKey": CANONICAL,
                "status": "evicted",
                "evicted": True,
                "notFound": False,
                "fileCount": 4,
                "reason": "evicted",
            }
        )

    result = await evict_exact_cache_key(
        {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
    )
    assert calls == [
        {
            "tool_name": EVICT_TOOL,
            "args": {"cacheKey": CANONICAL},
            "timeout": EVICT_TIMEOUT_SEC,
        }
    ]
    assert result["status"] == "evicted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (asyncio.TimeoutError("private timeout details"), "firmware-timeout"),
        (RuntimeError(f"Unknown tool: {EVICT_TOOL}"), "firmware-refused"),
        (RuntimeError("private transport payload /sdcard/secret"), "firmware-refused"),
    ],
)
async def test_orchestrator_maps_mcp_failures_to_stable_codes(error, code):
    conn = _conn()

    async def raw_call(*_args, **_kwargs):
        raise error

    with pytest.raises(CacheEvictionRefused, match=f"^{code}$"):
        await evict_exact_cache_key({}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call)
    logs = " ".join(message for _, message in conn.logger.messages)
    assert "/sdcard" not in logs
    assert "secret" not in logs


VALID_EVICTED = {
    "cacheKey": CANONICAL,
    "status": "evicted",
    "evicted": True,
    "notFound": False,
    "fileCount": 4,
    "reason": "evicted",
}
VALID_NOT_FOUND = {
    "cacheKey": CANONICAL,
    "status": "not_found",
    "evicted": False,
    "notFound": True,
    "fileCount": 0,
    "reason": "not_found",
}
VALID_PARTIAL = {
    "cacheKey": CANONICAL,
    "status": "partial_evict_recovery_required",
    "evicted": False,
    "notFound": False,
    "fileCount": 2,
    "reason": "partial_evict_recovery_required",
}


@pytest.mark.parametrize("raw", [VALID_EVICTED, json.dumps(VALID_NOT_FOUND)])
def test_parse_firmware_result_accepts_only_coherent_successes(raw):
    expected = VALID_EVICTED if isinstance(raw, dict) else VALID_NOT_FOUND
    assert parse_firmware_result(CANONICAL, raw) == expected


def test_parse_firmware_result_preserves_coherent_partial_recovery_result():
    assert parse_firmware_result(CANONICAL, VALID_PARTIAL) == VALID_PARTIAL


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "not json",
        {},
        {**VALID_EVICTED, "extra": "private"},
        {**VALID_EVICTED, "cacheKey": None},
        {**VALID_EVICTED, "status": 1},
        {**VALID_EVICTED, "evicted": 1},
        {**VALID_EVICTED, "notFound": 0},
        {**VALID_EVICTED, "fileCount": True},
        {**VALID_EVICTED, "fileCount": -1},
        {**VALID_EVICTED, "reason": 1},
        {**VALID_EVICTED, "evicted": False},
        {**VALID_EVICTED, "notFound": True},
        {**VALID_EVICTED, "reason": "not_found"},
        {**VALID_NOT_FOUND, "fileCount": 1},
        {**VALID_NOT_FOUND, "evicted": True},
        {**VALID_PARTIAL, "evicted": True},
        {**VALID_PARTIAL, "notFound": True},
        {**VALID_PARTIAL, "fileCount": -1},
        {**VALID_PARTIAL, "fileCount": True},
        {**VALID_PARTIAL, "reason": "private remote text"},
        {**VALID_PARTIAL, "status": "unknown_partial_code"},
    ],
)
def test_parse_firmware_result_rejects_malformed_payloads(raw):
    with pytest.raises(CacheEvictionRefused, match="^firmware-malformed-result$"):
        parse_firmware_result(CANONICAL, raw)


def test_parse_firmware_result_rejects_key_mismatch_separately():
    with pytest.raises(CacheEvictionRefused, match="^firmware-key-mismatch$"):
        parse_firmware_result(CANONICAL, {**VALID_EVICTED, "cacheKey": OTHER})

    with pytest.raises(CacheEvictionRefused, match="^firmware-key-mismatch$"):
        parse_firmware_result(CANONICAL, {**VALID_PARTIAL, "cacheKey": OTHER})


@pytest.mark.parametrize(
    "status",
    [
        "invalid_cache_key",
        "lesson_runtime_active",
        "path_mismatch",
        "nested_directory",
        "symlink_rejected",
        "unexpected_node_type",
        "scan_failed",
        "unlink_failed",
        "rmdir_failed",
    ],
)
def test_parse_firmware_result_maps_known_refusals(status):
    raw = {
        "cacheKey": CANONICAL,
        "status": status,
        "evicted": False,
        "notFound": False,
        "fileCount": 0,
        "reason": status,
    }
    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        parse_firmware_result(CANONICAL, raw)


def test_parse_firmware_result_rejects_incoherent_known_refusal():
    raw = {
        "cacheKey": CANONICAL,
        "status": "lesson_runtime_active",
        "evicted": True,
        "notFound": False,
        "fileCount": 1,
        "reason": "evicted",
    }
    with pytest.raises(CacheEvictionRefused, match="^firmware-malformed-result$"):
        parse_firmware_result(CANONICAL, raw)


@pytest.mark.asyncio
async def test_orchestrator_logs_only_validated_key_stable_result_and_file_count():
    conn = _conn()

    async def raw_call(*_args, **_kwargs):
        return VALID_EVICTED

    await evict_exact_cache_key(
        {},
        "parent@example.com",
        CANONICAL,
        find_connection=_finder(conn),
        raw_mcp_call=raw_call,
    )
    logs = " ".join(message for _, message in conn.logger.messages)
    assert CANONICAL in logs
    assert "evicted" in logs
    assert "file_count=4" in logs
    assert "parent@example.com" not in logs


@pytest.mark.asyncio
async def test_voice_lease_first_refuses_eviction_before_mcp():
    conn = _conn()
    voice = conn.activity_leases.try_acquire_voice(ActivityOperation.CLASSIC_AUDIO)
    calls = []
    assert voice is not None

    async def raw_call(*args, **kwargs):
        calls.append((args, kwargs))

    with pytest.raises(CacheEvictionRefused, match="^voice-busy$"):
        await evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )

    assert calls == []
    assert not conn.activity_leases.has_exclusive_lease()
    voice.release()


@pytest.mark.asyncio
async def test_eviction_operation_task_owns_lock_exclusive_and_tracking_before_dispatch():
    conn = _conn()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def raw_call(*_args, **_kwargs):
        assert conn._lesson_pull_lock.locked()
        assert conn.activity_leases.has_exclusive_lease()
        assert asyncio.current_task() in conn.mcp_background_tasks
        entered.set()
        await release.wait()
        return VALID_EVICTED

    caller = asyncio.create_task(
        evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert len(conn.mcp_background_tasks) == 1
    release.set()

    assert (await caller)["status"] == "evicted"
    await asyncio.sleep(0)
    assert not conn.activity_leases.has_exclusive_lease()
    assert conn.mcp_background_tasks == set()


@pytest.mark.asyncio
async def test_double_caller_cancellation_does_not_cancel_tracked_eviction_operation():
    conn = _conn()
    entered = asyncio.Event()
    release = asyncio.Event()
    raw_cancelled = asyncio.Event()

    async def raw_call(*_args, **_kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            raw_cancelled.set()
            raise
        return VALID_EVICTED

    operation = None
    caller = asyncio.create_task(
        evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert len(conn.mcp_background_tasks) == 1
        operation = next(iter(conn.mcp_background_tasks))
        caller.cancel()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        assert not operation.cancelled()
        assert not raw_cancelled.is_set()
        assert conn.activity_leases.has_exclusive_lease()
    finally:
        release.set()
        if operation is not None:
            await asyncio.wait_for(operation, timeout=1)
        if not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
    await asyncio.sleep(0)
    assert not conn.activity_leases.has_exclusive_lease()


@pytest.mark.asyncio
async def test_protected_key_is_rechecked_after_waiting_for_lesson_lock():
    conn = _conn()
    await conn._lesson_pull_lock.acquire()
    calls = []

    async def raw_call(*args, **kwargs):
        calls.append((args, kwargs))

    caller = asyncio.create_task(
        evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    )
    await asyncio.sleep(0)
    conn.lesson_current_cache_key = CANONICAL
    conn._lesson_pull_lock.release()

    with pytest.raises(CacheEvictionRefused, match="^protected-current$"):
        await caller
    assert calls == []
    assert not conn.activity_leases.has_exclusive_lease()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("busy_kind", "code"),
    [
        ("render", "lesson-render-busy"),
        ("voice-probe", "voice-busy"),
        ("voice-lease", "voice-busy"),
    ],
)
async def test_busy_state_is_rechecked_after_waiting_for_lesson_lock(busy_kind, code):
    conn = _conn()
    await conn._lesson_pull_lock.acquire()
    calls = []
    voice_lease = None

    async def raw_call(*args, **kwargs):
        calls.append((args, kwargs))

    caller = asyncio.create_task(
        evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    )
    try:
        await asyncio.sleep(0)
        if busy_kind == "render":
            conn.lesson_runtime.state = "RUNNING"
        elif busy_kind == "voice-probe":
            conn.is_realtime_busy = lambda: True
        else:
            voice_lease = conn.activity_leases.try_acquire_voice(
                ActivityOperation.CLASSIC_AUDIO
            )
            assert voice_lease is not None
        conn._lesson_pull_lock.release()

        with pytest.raises(CacheEvictionRefused, match=f"^{code}$"):
            await caller
    finally:
        if conn._lesson_pull_lock.locked():
            conn._lesson_pull_lock.release()
        if voice_lease is not None:
            voice_lease.release()
        if not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
    assert calls == []
    assert not conn.activity_leases.has_exclusive_lease()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (asyncio.TimeoutError(), "firmware-timeout"),
        ("not-json", "firmware-malformed-result"),
        ({**VALID_EVICTED, "cacheKey": None}, "firmware-malformed-result"),
        ({**VALID_EVICTED, "cacheKey": OTHER}, "firmware-key-mismatch"),
        (RuntimeError("transport disconnected token=secret"), "firmware-refused"),
        (RuntimeError("Unknown tool without correlation"), "firmware-refused"),
    ],
)
async def test_ambiguous_remote_outcome_stays_exclusive_and_blocks_retry(raw, code):
    conn = _conn()
    calls = []

    async def raw_call(*_args, **_kwargs):
        calls.append("first")
        if isinstance(raw, BaseException):
            raise raw
        return raw

    with pytest.raises(CacheEvictionRefused, match=f"^{code}$"):
        await evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    assert conn.activity_leases.has_exclusive_lease()

    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        await evict_exact_cache_key(
            {},
            "device-1",
            CANONICAL,
            find_connection=_finder(conn),
            raw_mcp_call=lambda *_args, **_kwargs: pytest.fail("sticky lease must block retry"),
        )
    assert calls == ["first"]
    conn.activity_leases.close()
    assert not conn.activity_leases.has_exclusive_lease()


@pytest.mark.asyncio
async def test_synchronous_pre_dispatch_failure_releases_exclusive_lease():
    conn = _conn()

    def raw_call(*_args, **_kwargs):
        raise RuntimeError("local invocation failure token=secret")

    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        await evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )

    await asyncio.sleep(0)
    assert not conn.activity_leases.has_exclusive_lease()
    assert conn.mcp_background_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["get-id", "register"])
async def test_default_raw_call_async_setup_failure_is_pre_dispatch(
    monkeypatch,
    failure_stage,
):
    conn = _conn()
    sends = []

    class RegistrationFailsBeforeSend:
        async def get_next_id(self):
            if failure_stage == "get-id":
                raise RuntimeError("local id allocation failed token=secret")
            return 41

        async def register_call_result_future(self, _tool_call_id, _future):
            if failure_stage == "register":
                raise RuntimeError("local registration failed token=secret")

        async def cleanup_call_result(self, _tool_call_id):
            pass

    async def forbidden_send(*args, **kwargs):
        sends.append((args, kwargs))

    conn.mcp_client = RegistrationFailsBeforeSend()
    monkeypatch.setattr(
        "core.api.device_mcp_admin_handler.send_mcp_message",
        forbidden_send,
    )

    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        await evict_exact_cache_key(
            {},
            "device-1",
            CANONICAL,
            find_connection=_finder(conn),
        )

    await asyncio.sleep(0)
    assert sends == []
    assert not conn.activity_leases.has_exclusive_lease()
    assert conn.mcp_background_tasks == set()


@pytest.mark.asyncio
async def test_default_raw_call_send_failure_is_post_dispatch_ambiguous(monkeypatch):
    conn = _conn()
    sends = []

    class SendFailsAfterRegistration:
        async def get_next_id(self):
            return 42

        async def register_call_result_future(self, _tool_call_id, _future):
            pass

        async def cleanup_call_result(self, _tool_call_id):
            pass

    async def failing_send(*args, **kwargs):
        sends.append((args, kwargs))
        raise RuntimeError(f"Unknown tool: {EVICT_TOOL} token=secret")

    conn.mcp_client = SendFailsAfterRegistration()
    monkeypatch.setattr(
        "core.api.device_mcp_admin_handler.send_mcp_message",
        failing_send,
    )

    with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
        await evict_exact_cache_key(
            {},
            "device-1",
            CANONICAL,
            find_connection=_finder(conn),
        )

    assert len(sends) == 1
    assert conn.activity_leases.has_exclusive_lease()
    conn.activity_leases.close()


@pytest.mark.asyncio
async def test_default_correlated_unknown_tool_is_definitive_and_private(monkeypatch):
    conn = _conn()

    class UnknownToolClient:
        async def get_next_id(self):
            return 42

        async def register_call_result_future(self, _tool_call_id, future):
            self.future = future

        async def cleanup_call_result(self, _tool_call_id):
            pass

    client = UnknownToolClient()
    conn.mcp_client = client

    async def unknown_tool_result(_conn, _payload):
        client.future.set_result(
            {
                "isError": True,
                "error": f"Unknown tool: {EVICT_TOOL} token=private-secret",
            }
        )

    monkeypatch.setattr(
        "core.api.device_mcp_admin_handler.send_mcp_message",
        unknown_tool_result,
    )

    with pytest.raises(CacheEvictionRefused, match="^firmware-unknown-tool$"):
        await evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn)
        )

    logs = " ".join(message for _, message in conn.logger.messages)
    assert "private-secret" not in logs
    assert not conn.activity_leases.has_exclusive_lease()


@pytest.mark.asyncio
async def test_injected_unknown_tool_exceptions_remain_ambiguous():
    from core.api.device_mcp_admin_handler import MCPUnknownToolError

    for raw in (
        RuntimeError(f"Unknown tool: {EVICT_TOOL}"),
        MCPUnknownToolError(),
    ):
        conn = _conn()

        async def raw_call(*_args, **_kwargs):
            raise raw

        with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
            await evict_exact_cache_key(
                {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
            )
        assert conn.activity_leases.has_exclusive_lease()
        conn.activity_leases.close()


@pytest.mark.asyncio
async def test_coherent_firmware_refusal_is_definitive():
    refusal = {
        "cacheKey": CANONICAL,
        "status": "lesson_runtime_active",
        "evicted": False,
        "notFound": False,
        "fileCount": 0,
        "reason": "lesson_runtime_active",
    }
    conn = _conn()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def raw_call(*_args, **_kwargs):
        assert conn._lesson_pull_lock.locked()
        assert conn.activity_leases.has_exclusive_lease()
        assert asyncio.current_task() in conn.mcp_background_tasks
        entered.set()
        await release.wait()
        return refusal

    caller = asyncio.create_task(
        evict_exact_cache_key(
            {}, "device-1", CANONICAL, find_connection=_finder(conn), raw_mcp_call=raw_call
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert len(conn.mcp_background_tasks) == 1
        assert conn.activity_leases.has_exclusive_lease()
        release.set()
        with pytest.raises(CacheEvictionRefused, match="^firmware-refused$"):
            await caller
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        await asyncio.gather(*tuple(conn.mcp_background_tasks), return_exceptions=True)
    assert not conn.activity_leases.has_exclusive_lease()
    assert conn.mcp_background_tasks == set()
