import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _FakeRequest:
    def __init__(self, *, device_id="device-1", secret="secret", body=None):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": secret}
        self._body = body if body is not None else {"toolName": "self_tool", "args": {"x": 1}}

    async def json(self):
        return self._body


async def _response_json(response):
    return json.loads(response.text)


class DeviceMCPAdminHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_raw_call_raises_privacy_safe_typed_unknown_tool(self):
        from core.api.device_mcp_admin_handler import (
            MCPUnknownToolError,
            _call_raw_mcp_tool,
        )

        tool_name = "self.lesson_assets.evict_cache_key"

        class _Client:
            async def get_next_id(self):
                return 42

            async def register_call_result_future(self, _call_id, future):
                self.future = future

            async def cleanup_call_result(self, _call_id):
                pass

        client = _Client()

        async def unknown_tool_result(_conn, _payload):
            client.future.set_result(
                {
                    "isError": True,
                    "error": f"Unknown tool: {tool_name} token=private-secret",
                }
            )

        with patch(
            "core.api.device_mcp_admin_handler.send_mcp_message",
            unknown_tool_result,
        ):
            with self.assertRaises(MCPUnknownToolError) as caught:
                await _call_raw_mcp_tool(
                    object(),
                    client,
                    tool_name,
                    {},
                )

        self.assertEqual(str(caught.exception), "mcp-unknown-tool")
        self.assertNotIn("private-secret", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    async def test_raw_call_cancellation_after_registration_cleans_result(self):
        from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

        sent = asyncio.Event()
        cleaned = []

        class _Client:
            async def get_next_id(self):
                return 42

            async def register_call_result_future(self, _call_id, _future):
                pass

            async def cleanup_call_result(self, call_id):
                cleaned.append(call_id)

        async def send_then_wait(_conn, _payload):
            sent.set()

        with patch(
            "core.api.device_mcp_admin_handler.send_mcp_message",
            send_then_wait,
        ):
            task = asyncio.create_task(
                _call_raw_mcp_tool(object(), _Client(), "self.tool", {}, timeout=30)
            )
            await asyncio.wait_for(sent.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(cleaned, [42])

    async def test_raw_call_marks_dispatch_immediately_before_send(self):
        from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

        events = []

        class _Client:
            async def get_next_id(self):
                events.append("id")
                return 42

            async def register_call_result_future(self, _call_id, _future):
                events.append("register")

            async def cleanup_call_result(self, _call_id):
                events.append("cleanup")

        async def failing_send(_conn, _payload):
            events.append("send")
            raise RuntimeError("transport failed")

        with patch("core.api.device_mcp_admin_handler.send_mcp_message", failing_send):
            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                await _call_raw_mcp_tool(
                    object(),
                    _Client(),
                    "self.lesson_assets.evict_cache_key",
                    {"cacheKey": "key"},
                    on_dispatched=lambda: events.append("dispatched"),
                )

        self.assertEqual(
            events,
            ["id", "register", "dispatched", "send", "cleanup"],
        )

    async def test_rejects_missing_or_wrong_mint_secret(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = DeviceMCPAdminHandler({}, {})

        response = await handler.handle_post(_FakeRequest(secret="wrong"))

        self.assertEqual(response.status, 401)

    async def test_calls_device_mcp_tool_with_request_args(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = SimpleNamespace(mcp_client=object())
        call = AsyncMock(return_value="ok")
        handler = DeviceMCPAdminHandler({}, {"device-1": conn})

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", call):
            response = await handler.handle_post(
                _FakeRequest(body={"toolName": "self_upgrade_firmware", "args": {"url": "http://fw.bin"}})
            )

        self.assertEqual(response.status, 202)
        self.assertEqual(await _response_json(response), {"data": {"called": True, "result": "ok"}})
        call.assert_awaited_once_with(
            conn,
            conn.mcp_client,
            "self_upgrade_firmware",
            {"url": "http://fw.bin"},
            timeout=30,
        )

    async def test_robot_motion_tool_uses_fast_timeout(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = SimpleNamespace(mcp_client=object())
        call = AsyncMock(return_value="true")
        handler = DeviceMCPAdminHandler({}, {"device-1": conn})

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", call):
            response = await handler.handle_post(
                _FakeRequest(body={"toolName": "self_robot_head_turn_left", "args": {}})
            )

        self.assertEqual(response.status, 202)
        self.assertEqual(await _response_json(response), {"data": {"called": True, "result": "true"}})
        self.assertLess(call.await_args.kwargs["timeout"], 1)

    async def test_robot_motion_timeout_returns_sent_unconfirmed(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = SimpleNamespace(mcp_client=object())
        call = AsyncMock(side_effect=TimeoutError("Tool call request timed out"))
        handler = DeviceMCPAdminHandler({}, {"device-1": conn})

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", call):
            response = await handler.handle_post(
                _FakeRequest(body={"toolName": "self_robot_head_turn_left", "args": {}})
            )

        self.assertEqual(response.status, 202)
        self.assertEqual(
            await _response_json(response),
            {"data": {"called": True, "result": "sent_unconfirmed"}},
        )
        self.assertLess(call.await_args.kwargs["timeout"], 1)

    async def test_can_call_unlisted_user_tool_with_raw_mcp_name(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        class _Client:
            def __init__(self):
                self.next_id = 41
                self.future = None

            async def get_next_id(self):
                self.next_id += 1
                return self.next_id

            async def register_call_result_future(self, call_id, future):
                self.future = (call_id, future)

            async def cleanup_call_result(self, call_id):
                self.cleaned = call_id

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        mcp_client = _Client()
        conn = SimpleNamespace(mcp_client=mcp_client)
        sent = []

        async def _send(_conn, payload):
            sent.append(payload)
            mcp_client.future[1].set_result({"content": [{"text": "true"}]})

        handler = DeviceMCPAdminHandler({}, {"device-1": conn})

        with patch("core.api.device_mcp_admin_handler.send_mcp_message", _send):
            response = await handler.handle_post(
                _FakeRequest(
                    body={
                        "toolName": "self.upgrade_firmware",
                        "allowUnlisted": True,
                        "args": {"url": "http://fw.bin"},
                    }
                )
            )

        self.assertEqual(response.status, 202)
        self.assertEqual(await _response_json(response), {"data": {"called": True, "result": "true"}})
        self.assertEqual(sent[0]["method"], "tools/call")
        self.assertEqual(sent[0]["id"], 42)
        self.assertEqual(
            sent[0]["params"],
            {"name": "self.upgrade_firmware", "arguments": {"url": "http://fw.bin"}},
        )

    async def test_unlisted_raw_robot_motion_tool_uses_fast_timeout(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = SimpleNamespace(mcp_client=object())
        raw_call = AsyncMock(return_value="true")
        handler = DeviceMCPAdminHandler({}, {"device-1": conn})

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", raw_call):
            response = await handler.handle_post(
                _FakeRequest(
                    body={
                        "toolName": "self.robot.head_turn_left",
                        "allowUnlisted": True,
                        "args": {},
                    }
                )
            )

        self.assertEqual(response.status, 202)
        self.assertEqual(await _response_json(response), {"data": {"called": True, "result": "true"}})
        self.assertLess(raw_call.await_args.kwargs["timeout"], 1)

    async def test_reports_offline_missing_tool_name_and_call_failures(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = DeviceMCPAdminHandler({}, {})

        offline = await handler.handle_post(_FakeRequest(device_id="offline"))
        missing = await handler.handle_post(_FakeRequest(body={"args": {}}))

        self.assertEqual(offline.status, 202)
        self.assertEqual((await _response_json(offline))["data"]["reason"], "device-offline")
        self.assertEqual(missing.status, 400)

        conn = SimpleNamespace(mcp_client=object())
        handler = DeviceMCPAdminHandler({}, {"device-1": conn})
        with patch(
            "core.api.device_mcp_admin_handler.call_mcp_tool",
            AsyncMock(side_effect=RuntimeError("MCP client not ready yet")),
        ):
            failed = await handler.handle_post(_FakeRequest())

        self.assertEqual(failed.status, 409)
        self.assertEqual((await _response_json(failed))["error"], "MCP_CALL_FAILED")


if __name__ == "__main__":
    unittest.main()
