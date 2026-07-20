import sys
import types

import pytest
from websockets.datastructures import Headers


def test_esp_header_adapter_preserves_duplicates_and_redacts_sensitive_values():
    from core.connection_headers import preserve_request_headers, sanitize_headers_for_log

    headers = preserve_request_headers(
        Headers(
            [
                ("client-id", "one"),
                ("set-cookie", "a=1"),
                ("set-cookie", "b=2"),
                ("authorization", "Bearer secret"),
            ]
        )
    )
    assert headers.get_all("set-cookie") == ["a=1", "b=2"]
    assert sanitize_headers_for_log(headers)["authorization"] == "<redacted>"


def test_esp_header_adapter_rejects_duplicate_single_value_identity():
    from core.connection_headers import preserve_request_headers, single_header

    headers = preserve_request_headers(Headers([("client-id", "one"), ("Client-Id", "one")]))
    with pytest.raises(ValueError, match="duplicate client-id"):
        single_header(headers, "client-id")


def test_websocket_connection_headers_reach_lesson_runtime_trace_context():
    server_mcp = types.ModuleType("core.providers.tools.server_mcp")
    server_mcp.ServerMCPExecutor = type("ServerMCPExecutor", (), {})
    sys.modules.setdefault("core.providers.tools.server_mcp", server_mcp)
    sys.modules.setdefault("cnlunar", types.ModuleType("cnlunar"))
    from core.connection import ConnectionHandler
    from core.connection_headers import preserve_request_headers
    from core.lesson.runtime import LessonRuntime

    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    tracestate = "rojo=00f067aa0ba902b7"
    conn = ConnectionHandler.__new__(ConnectionHandler)
    conn.headers = preserve_request_headers(
        Headers(
            [
                ("device-id", "robot"),
                ("traceparent", traceparent),
                ("tracestate", tracestate),
            ]
        )
    )
    conn.logger = None
    conn.features = None

    runtime = LessonRuntime(
        conn,
        assignment={"assignmentId": "assignment", "lessonId": "lesson"},
        manifest={},
        asset_cache=object(),
        forwarder=object(),
    )

    assert runtime._trace_context == {
        "traceparent": traceparent,
        "tracestate": tracestate,
    }
