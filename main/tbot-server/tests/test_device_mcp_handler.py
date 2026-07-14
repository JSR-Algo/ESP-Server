import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.providers.tools.device_mcp import mcp_handler


class _WebSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send(self, message):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(message)


class _ToolManager:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_tools(self):
        self.refresh_calls += 1


class _FuncHandler:
    def __init__(self):
        self.tool_manager = _ToolManager()
        self.support_calls = 0

    def current_support_functions(self):
        self.support_calls += 1


class _Conn(SimpleNamespace):
    def __init__(self, *, mcp=True, websocket=None):
        super().__init__(
            features={"mcp": mcp},
            websocket=websocket or _WebSocket(),
            config={"server": {"auth_key": "secret"}},
            headers={"device-id": "device-1"},
            func_handler=_FuncHandler(),
        )


class _CapturingLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def _capture(self, message, *_args, **_kwargs):
        self.messages.append(str(message))

    debug = _capture
    info = _capture
    warning = _capture
    error = _capture


class DeviceMCPHandlerClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_tracks_tools_cache_and_result_futures(self):
        client = mcp_handler.MCPClient()
        await client.set_ready(True)
        await client.add_tool(
            {
                "name": "self.audio.speaker.set_volume",
                "description": "Set volume",
                "inputSchema": {"properties": {"volume": {"type": "integer"}}},
            }
        )

        available = client.get_available_tools()
        self.assertIs(available, client.get_available_tools())
        self.assertTrue(await client.is_ready())
        self.assertTrue(client.has_tool("self_audio_speaker_set_volume"))
        self.assertEqual(available[0]["function"]["parameters"]["type"], "object")
        self.assertEqual(await client.get_next_id(), 1)

        done = asyncio.Future()
        await client.register_call_result_future(5, done)
        await client.resolve_call_result(5, "ok")
        self.assertEqual(done.result(), "ok")

        failed = asyncio.Future()
        await client.register_call_result_future(6, failed)
        await client.reject_call_result(6, RuntimeError("bad"))
        with self.assertRaisesRegex(RuntimeError, "bad"):
            failed.result()

        pending = asyncio.Future()
        await client.register_call_result_future(7, pending)
        await client.cleanup_call_result(7)
        self.assertNotIn(7, client.call_results)


class DeviceMCPHandlerMessageTest(unittest.IsolatedAsyncioTestCase):
    async def test_hostile_request_ids_are_ignored_without_disconnect_or_log_leak(self):
        sentinel = "SENTINEL-ID-SECRET"
        hostile_ids = (
            "not-numeric",
            "07",
            {"token": sentinel},
            10**1000,
            "9" * 10000,
            "7\nchecksum_verified\x1b[31m",
        )
        logger = _CapturingLogger()
        conn = _Conn()

        with patch.object(mcp_handler, "logger", logger):
            for hostile_id in hostile_ids:
                with self.subTest(hostile_id=type(hostile_id).__name__):
                    result_client = mcp_handler.MCPClient()
                    result_future = asyncio.Future()
                    await result_client.register_call_result_future(7, result_future)

                    await mcp_handler.handle_mcp_message(
                        conn, result_client, {"id": hostile_id, "result": "ignored"}
                    )
                    await mcp_handler.handle_mcp_message(
                        conn,
                        result_client,
                        {"id": hostile_id, "error": {"message": "ignored"}},
                    )

                    self.assertFalse(result_future.done())
                    self.assertIn(7, result_client.call_results)
                    await result_client.cleanup_call_result(7)

        messages = "\n".join(logger.messages)
        self.assertNotIn(sentinel, messages)
        self.assertNotIn("checksum_verified", messages)
        self.assertTrue(all(len(message) <= 400 for message in logger.messages))

    async def test_canonical_numeric_string_request_id_resolves_pending_call(self):
        conn = _Conn()
        client = mcp_handler.MCPClient()
        future = asyncio.Future()
        await client.register_call_result_future(7, future)

        await mcp_handler.handle_mcp_message(conn, client, {"id": "7", "result": "ok"})

        self.assertEqual(future.result(), "ok")
        self.assertNotIn(7, client.call_results)

    async def test_logged_metadata_is_bounded_and_cannot_inject_evidence_markers(self):
        forged = "\nlesson_preload_ready checksum_verified asset_cache_hit\r\n\u2028"
        hostile_method = "tools/call" + forged + ("m" * 300)
        hostile_tool = "self.hostile" + forged + ("t" * 300)
        hostile_id = "77" + forged + ("i" * 300)
        logger = _CapturingLogger()
        conn = _Conn()
        hostile_type = type("Result" + forged + ("r" * 300), (), {})
        hostile_error_type = type("Error" + forged + ("e" * 300), (RuntimeError,), {})
        payload = {
            "jsonrpc": "2.0",
            "id": hostile_id,
            "method": hostile_method,
            "params": {"name": hostile_tool, "arguments": {"value": "unchanged"}},
        }

        result_client = mcp_handler.MCPClient()
        result_future = asyncio.Future()
        await result_client.register_call_result_future(78, result_future)

        class _HostileFailingWebSocket:
            async def send(self, _message):
                raise hostile_error_type("must not be logged")

        async def no_sleep(_delay):
            return None

        async def ignore_send(_conn, _payload):
            return None

        with patch.object(mcp_handler, "logger", logger), patch.object(
            mcp_handler.asyncio, "sleep", new=no_sleep
        ), patch.object(mcp_handler, "send_mcp_message", new=ignore_send):
            await mcp_handler.handle_mcp_message(
                conn,
                mcp_handler.MCPClient(),
                {
                    "id": 1,
                    "result": {"serverInfo": {"name": hostile_tool, "version": hostile_method}},
                },
            )

        with patch.object(mcp_handler, "logger", logger):
            await mcp_handler.handle_mcp_message(
                conn, result_client, {"id": 78, "result": hostile_type()}
            )
            await mcp_handler.send_mcp_message(conn, payload)
            await mcp_handler.handle_mcp_message(
                conn, mcp_handler.MCPClient(), {"method": hostile_method}
            )
            await mcp_handler.send_mcp_message(
                _Conn(websocket=_HostileFailingWebSocket()), payload
            )

        wire_payload = json.loads(conn.websocket.sent[0])["payload"]
        self.assertEqual(wire_payload, payload)
        self.assertTrue(logger.messages)
        for message in logger.messages:
            self.assertNotIn("\n", message)
            self.assertNotIn("\r", message)
            self.assertNotIn("\u2028", message)
            self.assertNotIn("lesson_preload_ready", message)
            self.assertNotIn("checksum_verified", message)
            self.assertNotIn("asset_cache_hit", message)
            self.assertLessEqual(len(message), 400)

    async def test_device_mcp_logs_only_metadata_and_never_payload_secrets(self):
        sentinel = "SENTINEL-DEVICE-MCP-SECRET"
        logger = _CapturingLogger()
        conn = _Conn()
        client = mcp_handler.MCPClient()
        response = asyncio.Future()
        await client.register_call_result_future(77, response)

        with patch.object(mcp_handler, "logger", logger):
            await mcp_handler.send_mcp_message(
                conn,
                {
                    "jsonrpc": "2.0",
                    "id": 76,
                    "method": "tools/call",
                    "params": {
                        "name": "self.private.tool",
                        "arguments": {"token": sentinel, "content": sentinel},
                    },
                },
            )
            await mcp_handler.handle_mcp_message(
                conn,
                client,
                {
                    "id": 77,
                    "result": {"content": [{"text": sentinel}], "token": sentinel},
                },
            )
            await mcp_handler.handle_mcp_message(
                conn,
                client,
                {"id": 78, "error": {"message": sentinel}},
            )

        self.assertEqual(
            json.loads(conn.websocket.sent[0])["payload"]["params"]["arguments"]["token"],
            sentinel,
        )
        self.assertEqual(response.result()["content"][0]["text"], sentinel)
        self.assertTrue(logger.messages)
        self.assertNotIn(sentinel, "\n".join(logger.messages))

    async def test_send_mcp_message_respects_feature_flag_and_send_errors(self):
        disabled = _Conn(mcp=False)
        failing = _Conn(websocket=_WebSocket(fail=True))
        ok = _Conn()

        await mcp_handler.send_mcp_message(disabled, {"method": "x"})
        await mcp_handler.send_mcp_message(failing, {"method": "x"})
        await mcp_handler.send_mcp_message(ok, {"method": "x"})

        self.assertEqual(disabled.websocket.sent, [])
        self.assertEqual(failing.websocket.sent, [])
        self.assertEqual(json.loads(ok.websocket.sent[0])["payload"], {"method": "x"})

    async def test_handle_mcp_message_initializes_lists_tools_and_marks_ready(self):
        conn = _Conn()
        client = mcp_handler.MCPClient()
        sent = []

        async def fake_sleep(_delay):
            return None

        async def capture_send(conn_arg, payload):
            sent.append(payload)

        with patch.object(mcp_handler.asyncio, "sleep", new=fake_sleep), patch.object(
            mcp_handler, "send_mcp_message", new=capture_send
        ):
            await mcp_handler.handle_mcp_message(
                conn,
                client,
                {"id": 1, "result": {"serverInfo": {"name": "srv", "version": "1"}}},
            )
            await mcp_handler.handle_mcp_message(
                conn,
                client,
                {
                    "id": 2,
                    "result": {
                        "tools": [
                            "skip-me",
                            {
                                "name": "self.audio.speaker.set_volume",
                                "description": "Calls self.audio.speaker.set_volume",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"volume": {"type": "integer"}},
                                    "required": ["volume", 1],
                                },
                            },
                        ]
                    },
                },
            )

        self.assertEqual(sent[0]["method"], "tools/list")
        self.assertTrue(await client.is_ready())
        tool = client.tools["self_audio_speaker_set_volume"]
        self.assertIn("self_audio_speaker_set_volume", tool["description"])
        self.assertEqual(tool["inputSchema"]["required"], ["volume"])
        self.assertEqual(conn.func_handler.tool_manager.refresh_calls, 1)
        self.assertEqual(conn.func_handler.support_calls, 1)

    async def test_handle_mcp_message_continues_invalid_tool_lists_methods_and_errors(self):
        conn = _Conn()
        client = mcp_handler.MCPClient()
        sent = []

        async def capture_send(conn_arg, payload):
            sent.append(payload)

        future = asyncio.Future()
        await client.register_call_result_future(9, future)
        error_future = asyncio.Future()
        await client.register_call_result_future(10, error_future)

        with patch.object(mcp_handler, "send_mcp_message", new=capture_send):
            await mcp_handler.handle_mcp_message(conn, client, "bad")
            await mcp_handler.handle_mcp_message(conn, client, {"id": 2, "result": {"tools": "bad"}})
            await mcp_handler.handle_mcp_message(
                conn,
                client,
                {"id": 2, "result": {"tools": [], "nextCursor": "next"}},
            )
            await mcp_handler.handle_mcp_message(conn, client, {"method": "ping"})
            await mcp_handler.handle_mcp_message(conn, client, {"id": 9, "result": "ok"})
            await mcp_handler.handle_mcp_message(
                conn, client, {"id": 10, "error": {"message": "nope"}}
            )

        self.assertEqual(sent[-1]["params"], {"cursor": "next"})
        self.assertEqual(future.result(), "ok")
        with self.assertRaisesRegex(Exception, "MCP error: nope"):
            error_future.result()

    async def test_send_initialize_and_list_requests_build_expected_payloads(self):
        conn = _Conn()
        sent = []

        class FakeAuth:
            def __init__(self, key):
                self.key = key

            def generate_token(self, device_id):
                return f"token:{self.key}:{device_id}"

        async def capture_send(conn_arg, payload):
            sent.append(payload)

        with patch.object(mcp_handler, "get_vision_url", return_value="https://vision"), patch.object(
            mcp_handler, "AuthToken", new=FakeAuth
        ), patch.object(mcp_handler, "send_mcp_message", new=capture_send):
            await mcp_handler.send_mcp_initialize_message(conn)
            await mcp_handler.send_mcp_tools_list_request(conn)
            await mcp_handler.send_mcp_tools_list_continue_request(conn, "cursor-1")

        self.assertEqual(sent[0]["method"], "initialize")
        self.assertEqual(sent[0]["params"]["capabilities"]["vision"]["url"], "https://vision")
        self.assertEqual(sent[0]["params"]["capabilities"]["vision"]["token"], "token:secret:device-1")
        self.assertEqual(sent[1]["method"], "tools/list")
        self.assertEqual(sent[2]["params"], {"cursor": "cursor-1"})


class DeviceMCPHandlerCallToolTest(unittest.IsolatedAsyncioTestCase):
    async def _ready_client(self):
        client = mcp_handler.MCPClient()
        await client.set_ready(True)
        await client.add_tool(
            {"name": "self.tool", "description": "tool", "inputSchema": {}}
        )
        return client

    async def _call_with_result(self, args, raw_result, *, timeout=30):
        conn = _Conn()
        client = await self._ready_client()
        sent = []

        async def resolving_send(conn_arg, payload):
            sent.append(payload)
            await client.resolve_call_result(payload["id"], raw_result)

        with patch.object(mcp_handler, "send_mcp_message", new=resolving_send):
            result = await mcp_handler.call_mcp_tool(conn, client, "self_tool", args, timeout=timeout)
        return result, sent, client

    async def test_call_mcp_tool_validates_ready_tool_and_arguments(self):
        conn = _Conn()
        not_ready = mcp_handler.MCPClient()
        ready = await self._ready_client()

        with self.assertRaisesRegex(RuntimeError, "not ready"):
            await mcp_handler.call_mcp_tool(conn, not_ready, "self_tool")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            await mcp_handler.call_mcp_tool(conn, ready, "missing")
        with self.assertRaisesRegex(ValueError, "Parameter JSON parse failed"):
            await mcp_handler.call_mcp_tool(conn, ready, "self_tool", "not-json")
        with self.assertRaisesRegex(ValueError, "Cannot parse any valid JSON object"):
            await mcp_handler.call_mcp_tool(conn, ready, "self_tool", "{bad} {still_bad}")
        with self.assertRaisesRegex(ValueError, "Parameters must be dictionary"):
            await mcp_handler.call_mcp_tool(conn, ready, "self_tool", "[]")
        with self.assertRaisesRegex(ValueError, "Parameter type error"):
            await mcp_handler.call_mcp_tool(conn, ready, "self_tool", 1)
        with patch.object(mcp_handler.json, "loads", side_effect=RuntimeError("parser boom")):
            with self.assertRaisesRegex(ValueError, "Parameter processing failed"):
                await mcp_handler.call_mcp_tool(conn, ready, "self_tool", "{}")

    async def test_call_mcp_tool_sends_blank_dict_and_merged_json_arguments(self):
        blank, blank_sent, _ = await self._call_with_result("", {"content": [{"text": "ok"}]})
        merged, merged_sent, _ = await self._call_with_result(
            '{"a": 1} noise {"b": 2}', {"content": [{"text": "done"}]}
        )
        direct, direct_sent, _ = await self._call_with_result({"c": 3}, "raw")

        self.assertEqual(blank, "ok")
        self.assertEqual(blank_sent[0]["params"]["arguments"], {})
        self.assertEqual(merged, "done")
        self.assertEqual(merged_sent[0]["params"]["arguments"], {"a": 1, "b": 2})
        self.assertEqual(direct, "raw")
        self.assertEqual(direct_sent[0]["params"]["arguments"], {"c": 3})
        self.assertEqual(direct_sent[0]["params"]["name"], "self.tool")

    async def test_call_mcp_tool_logs_metadata_without_arguments_or_result_content(self):
        sentinel = "SENTINEL-CALL-SECRET"
        logger = _CapturingLogger()

        with patch.object(mcp_handler, "logger", logger):
            result, sent, _ = await self._call_with_result(
                {"token": sentinel, "content": sentinel},
                {"content": [{"text": sentinel}]},
            )

        self.assertEqual(result, sentinel)
        self.assertEqual(sent[0]["params"]["arguments"]["token"], sentinel)
        self.assertTrue(logger.messages)
        self.assertNotIn(sentinel, "\n".join(logger.messages))

    async def test_call_mcp_tool_handles_error_results_timeouts_and_send_exceptions(self):
        conn = _Conn()
        client = await self._ready_client()

        with self.assertRaisesRegex(RuntimeError, "Tool call error: bad"):
            await self._call_with_result("{}", {"isError": True, "error": "bad"})

        async def no_response(_conn, _payload):
            return None

        with patch.object(mcp_handler, "send_mcp_message", new=no_response):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await mcp_handler.call_mcp_tool(conn, client, "self_tool", "{}", timeout=0.001)
        self.assertEqual(client.call_results, {})

        async def raising_send(_conn, _payload):
            raise RuntimeError("send boom")

        with patch.object(mcp_handler, "send_mcp_message", new=raising_send):
            with self.assertRaisesRegex(RuntimeError, "send boom"):
                await mcp_handler.call_mcp_tool(conn, client, "self_tool", "{}")
        self.assertEqual(client.call_results, {})


if __name__ == "__main__":
    unittest.main()
