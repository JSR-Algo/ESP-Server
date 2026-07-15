import asyncio
import json
from types import SimpleNamespace

import pytest

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
        None,
        7,
        b"pip",
        "",
        " " + CANONICAL,
        CANONICAL + " ",
        "Pip-farm/v1-" + CHECKSUM,
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
    return SimpleNamespace(
        is_realtime_busy=lambda: voice_busy,
        lesson_runtime=_runtime("active", state),
        lesson_runtime_candidate=_runtime("candidate", candidate_state),
        mcp_client=object() if mcp_client else None,
        logger=RecordingLogger(),
    )


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
        (RuntimeError(f"Unknown tool: {EVICT_TOOL}"), "firmware-unknown-tool"),
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


@pytest.mark.parametrize("raw", [VALID_EVICTED, json.dumps(VALID_NOT_FOUND)])
def test_parse_firmware_result_accepts_only_coherent_successes(raw):
    expected = VALID_EVICTED if isinstance(raw, dict) else VALID_NOT_FOUND
    assert parse_firmware_result(CANONICAL, raw) == expected


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
    ],
)
def test_parse_firmware_result_rejects_malformed_payloads(raw):
    with pytest.raises(CacheEvictionRefused, match="^firmware-malformed-result$"):
        parse_firmware_result(CANONICAL, raw)


def test_parse_firmware_result_rejects_key_mismatch_separately():
    with pytest.raises(CacheEvictionRefused, match="^firmware-key-mismatch$"):
        parse_firmware_result(CANONICAL, {**VALID_EVICTED, "cacheKey": OTHER})


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
