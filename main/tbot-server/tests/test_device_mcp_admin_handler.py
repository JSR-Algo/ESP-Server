import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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
    HIL_MAC = "28:84:85:85:1a:80"
    HIL_CACHE_KEY = f"hil-task14/v1-{'d' * 64}"
    HIL_TOOLS = (
        "self.lesson_assets.hil.arm_fault",
        "self.lesson_assets.hil.status",
        "self.lesson_assets.hil.stage_fixture",
        "self.lesson_assets.hil.cleanup_fixture",
        "self.lesson_assets.hil.inspect",
    )

    def _hil_handler(self, *, allowlist=None, conn_mac=None):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        conn = SimpleNamespace(
            device_id=conn_mac or self.HIL_MAC,
            mcp_client=object(),
        )
        config = {
            "lesson": {
                "storage_hil_device_allowlist": (
                    [self.HIL_MAC] if allowlist is None else allowlist
                )
            }
        }
        return DeviceMCPAdminHandler(config, {"route-device-uuid": conn}), conn

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

    async def test_exact_hil_tool_without_resolved_live_connection_fails_allowlist(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = DeviceMCPAdminHandler(
            {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
            {},
        )

        response = await handler.handle_post(
            _FakeRequest(
                device_id="route-device-uuid",
                body={
                    "toolName": self.HIL_TOOLS[0],
                    "allowUnlisted": True,
                    "args": {},
                },
            )
        )

        self.assertEqual(response.status, 403)
        self.assertEqual(
            await _response_json(response),
            {
                "error": "HIL_DEVICE_NOT_ALLOWLISTED",
                "message": "HIL MCP request rejected",
            },
        )

    async def test_hil_connection_and_mac_gate_precede_timeout_and_key_validation(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        offline = DeviceMCPAdminHandler(
            {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
            {},
        )
        nonallowlisted, _ = self._hil_handler(allowlist=[])
        requests = (
            {
                "toolName": self.HIL_TOOLS[0],
                "allowUnlisted": True,
                "timeoutSeconds": "invalid-before-gate",
                "args": {},
            },
            {
                "toolName": "self.lesson_assets.evict_cache_key",
                "timeoutSeconds": "invalid-before-gate",
                "args": {"cacheKey": "not-canonical"},
            },
        )

        for handler in (offline, nonallowlisted):
            timeout_validation = Mock(
                side_effect=AssertionError("timeout validation ran before device gate")
            )
            key_validation = Mock(
                side_effect=AssertionError("key validation ran before device gate")
            )
            with patch(
                "core.api.device_mcp_admin_handler._hil_timeout",
                timeout_validation,
            ), patch(
                "core.api.device_mcp_admin_handler._has_canonical_hil_cache_key",
                key_validation,
            ):
                for body in requests:
                    response = await handler.handle_post(
                        _FakeRequest(device_id="route-device-uuid", body=body)
                    )
                    self.assertEqual(response.status, 403)
                    self.assertEqual(
                        (await _response_json(response))["error"],
                        "HIL_DEVICE_NOT_ALLOWLISTED",
                    )

            timeout_validation.assert_not_called()
            key_validation.assert_not_called()

    async def test_valid_hil_paths_without_mcp_client_return_sanitized_hil_failure(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, conn = self._hil_handler()
        conn.mcp_client = None
        requests = (
            {
                "toolName": self.HIL_TOOLS[0],
                "allowUnlisted": True,
                "args": {},
            },
            {
                "toolName": "self.lesson_assets.evict_cache_key",
                "timeoutSeconds": 75,
                "args": {"cacheKey": self.HIL_CACHE_KEY},
            },
        )

        for body in requests:
            with self.subTest(tool_name=body["toolName"]):
                response = await handler.handle_post(
                    _FakeRequest(device_id="route-device-uuid", body=body)
                )

                self.assertEqual(response.status, 409)
                self.assertEqual(
                    await _response_json(response),
                    {
                        "error": "HIL_MCP_FAILED",
                        "message": "HIL MCP request rejected",
                    },
                )

    async def test_exact_hil_tools_use_raw_dispatch_for_resolved_live_mac(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        raw_call = AsyncMock(return_value="ok")
        listed_call = AsyncMock(side_effect=AssertionError("listed dispatch forbidden"))
        handler, conn = self._hil_handler(conn_mac=self.HIL_MAC.upper())

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", raw_call), patch(
            "core.api.device_mcp_admin_handler.call_mcp_tool", listed_call
        ):
            for tool_name in self.HIL_TOOLS:
                with self.subTest(tool_name=tool_name):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": tool_name,
                                "allowUnlisted": True,
                                "args": {},
                            },
                        )
                    )
                    self.assertEqual(response.status, 202)
                    self.assertEqual(
                        await _response_json(response),
                        {"data": {"called": True, "result": "ok"}},
                    )

        self.assertEqual(raw_call.await_count, len(self.HIL_TOOLS))
        for call in raw_call.await_args_list:
            self.assertIs(call.args[0], conn)
            self.assertEqual(call.kwargs["timeout"], 30)
        listed_call.assert_not_awaited()

    async def test_hil_firmware_client_identity_resolves_live_mac_connection(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        firmware_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"

        for identity_attributes in (
            {"client_id": firmware_uuid, "headers": {}},
            {"headers": {"client-id": firmware_uuid}},
            {"headers": {"Client-Id": firmware_uuid}},
        ):
            with self.subTest(identity_attributes=identity_attributes):
                conn = SimpleNamespace(
                    device_id=self.HIL_MAC,
                    mcp_client=object(),
                    **identity_attributes,
                )
                handler = DeviceMCPAdminHandler(
                    {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
                    {self.HIL_MAC: conn},
                )
                raw_call = AsyncMock(return_value="ok")
                backend_lookup = AsyncMock(
                    side_effect=AssertionError("backend identity fallback must not run")
                )

                with patch(
                    "core.api.device_mcp_admin_handler._call_raw_mcp_tool",
                    raw_call,
                ), patch.object(
                    handler._shared,
                    "_find_connection",
                    backend_lookup,
                ):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id=firmware_uuid,
                            body={
                                "toolName": "self.lesson_assets.hil.status",
                                "allowUnlisted": True,
                                "args": {},
                            },
                        )
                    )

                self.assertEqual(response.status, 202)
                self.assertEqual(
                    await _response_json(response),
                    {"data": {"called": True, "result": "ok"}},
                )
                raw_call.assert_awaited_once()
                self.assertIs(raw_call.await_args.args[0], conn)
                backend_lookup.assert_not_awaited()

    async def test_hil_duplicate_firmware_client_identity_fails_closed(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        firmware_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"
        connections = {
            self.HIL_MAC: SimpleNamespace(
                device_id=self.HIL_MAC,
                client_id=firmware_uuid,
                mcp_client=object(),
            ),
            "28:84:85:85:1a:81": SimpleNamespace(
                device_id="28:84:85:85:1a:81",
                headers={"client-id": firmware_uuid},
                mcp_client=object(),
            ),
        }
        handler = DeviceMCPAdminHandler(
            {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
            connections,
        )
        raw_call = AsyncMock(return_value="ok")
        backend_lookup = AsyncMock(
            side_effect=AssertionError("backend identity fallback must not run")
        )

        with patch(
            "core.api.device_mcp_admin_handler._call_raw_mcp_tool",
            raw_call,
        ), patch.object(
            handler._shared,
            "_find_connection",
            backend_lookup,
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id=firmware_uuid,
                    body={
                        "toolName": "self.lesson_assets.hil.status",
                        "allowUnlisted": True,
                        "args": {},
                    },
                )
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            await _response_json(response),
            {
                "error": "MCP_CLIENT_IDENTITY_AMBIGUOUS",
                "message": "Device MCP connection identity is ambiguous",
            },
        )
        raw_call.assert_not_awaited()
        backend_lookup.assert_not_awaited()

    async def test_hil_exact_uuid_key_does_not_bypass_duplicate_client_identity_guard(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        firmware_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"
        connections = {
            firmware_uuid: SimpleNamespace(
                device_id=self.HIL_MAC,
                client_id=firmware_uuid,
                mcp_client=object(),
            ),
            "28:84:85:85:1a:81": SimpleNamespace(
                device_id="28:84:85:85:1a:81",
                headers={"Client-Id": firmware_uuid},
                mcp_client=object(),
            ),
        }
        handler = DeviceMCPAdminHandler(
            {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
            connections,
        )
        raw_call = AsyncMock(return_value="ok")
        backend_lookup = AsyncMock(
            side_effect=AssertionError("backend identity fallback must not run")
        )

        with patch(
            "core.api.device_mcp_admin_handler._call_raw_mcp_tool",
            raw_call,
        ), patch.object(
            handler._shared,
            "_find_connection",
            backend_lookup,
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id=firmware_uuid,
                    body={
                        "toolName": "self.lesson_assets.hil.status",
                        "allowUnlisted": True,
                        "args": {},
                    },
                )
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            await _response_json(response),
            {
                "error": "MCP_CLIENT_IDENTITY_AMBIGUOUS",
                "message": "Device MCP connection identity is ambiguous",
            },
        )
        raw_call.assert_not_awaited()
        backend_lookup.assert_not_awaited()

    async def test_hil_exact_uuid_namespace_conflict_with_client_identity_fails_closed(self):
        from core.api.device_mcp_admin_handler import DeviceMCPAdminHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        firmware_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"
        connections = {
            firmware_uuid: SimpleNamespace(
                device_id=self.HIL_MAC,
                client_id="different-firmware-client",
                mcp_client=object(),
            ),
            "28:84:85:85:1a:81": SimpleNamespace(
                device_id="28:84:85:85:1a:81",
                client_id=firmware_uuid,
                mcp_client=object(),
            ),
        }
        handler = DeviceMCPAdminHandler(
            {"lesson": {"storage_hil_device_allowlist": [self.HIL_MAC]}},
            connections,
        )
        raw_call = AsyncMock(return_value="ok")
        backend_lookup = AsyncMock(
            side_effect=AssertionError("backend identity fallback must not run")
        )

        with patch(
            "core.api.device_mcp_admin_handler._call_raw_mcp_tool",
            raw_call,
        ), patch.object(
            handler._shared,
            "_find_connection",
            backend_lookup,
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id=firmware_uuid,
                    body={
                        "toolName": "self.lesson_assets.hil.status",
                        "allowUnlisted": True,
                        "args": {},
                    },
                )
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            await _response_json(response),
            {
                "error": "MCP_CLIENT_IDENTITY_AMBIGUOUS",
                "message": "Device MCP connection identity is ambiguous",
            },
        )
        raw_call.assert_not_awaited()
        backend_lookup.assert_not_awaited()

    async def test_hil_prefix_unknown_tool_and_non_exact_allow_unlisted_are_forbidden(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        raw_call = AsyncMock(side_effect=AssertionError("must not dispatch"))
        handler, _ = self._hil_handler()
        cases = (
            ("self.lesson_assets.hil.destroy", True),
            (self.HIL_TOOLS[0], False),
            (self.HIL_TOOLS[0], 1),
            (self.HIL_TOOLS[0], "true"),
        )

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", raw_call):
            for tool_name, allow_unlisted in cases:
                with self.subTest(tool_name=tool_name, allow_unlisted=allow_unlisted):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": tool_name,
                                "allowUnlisted": allow_unlisted,
                                "args": {},
                            },
                        )
                    )
                    payload = await _response_json(response)
                    self.assertEqual(response.status, 403)
                    self.assertEqual(payload["error"], "HIL_TOOL_FORBIDDEN")
        raw_call.assert_not_awaited()

    async def test_hil_tools_require_exactly_allowlisted_resolved_mac_not_route_uuid(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        raw_call = AsyncMock(side_effect=AssertionError("must not dispatch"))
        cases = (
            ([], self.HIL_MAC),
            (["not-a-mac"], self.HIL_MAC),
            ([self.HIL_MAC, "28:84:85:85:1a:81"], self.HIL_MAC),
            (["28:84:85:85:1a:81"], self.HIL_MAC),
            (["route-device-uuid"], self.HIL_MAC),
        )

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", raw_call):
            for allowlist, conn_mac in cases:
                with self.subTest(allowlist=allowlist):
                    handler, _ = self._hil_handler(
                        allowlist=allowlist,
                        conn_mac=conn_mac,
                    )
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": self.HIL_TOOLS[1],
                                "allowUnlisted": True,
                                "args": {},
                            },
                        )
                    )
                    payload = await _response_json(response)
                    self.assertEqual(response.status, 403)
                    self.assertEqual(payload["error"], "HIL_DEVICE_NOT_ALLOWLISTED")
        raw_call.assert_not_awaited()

    async def test_hil_timeout_override_accepts_only_exact_int_between_five_and_seventy_five(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        raw_call = AsyncMock(return_value="ok")
        handler, _ = self._hil_handler()

        with patch("core.api.device_mcp_admin_handler._call_raw_mcp_tool", raw_call):
            for timeout in (5, 75):
                response = await handler.handle_post(
                    _FakeRequest(
                        device_id="route-device-uuid",
                        body={
                            "toolName": self.HIL_TOOLS[1],
                            "allowUnlisted": True,
                            "timeoutSeconds": timeout,
                            "args": {},
                        },
                    )
                )
                self.assertEqual(response.status, 202)
                self.assertEqual(raw_call.await_args.kwargs["timeout"], timeout)

            for timeout in (True, False, 0, -1, 4, 76, 5.0, "5", None):
                with self.subTest(timeout=timeout):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": self.HIL_TOOLS[1],
                                "allowUnlisted": True,
                                "timeoutSeconds": timeout,
                                "args": {},
                            },
                        )
                    )
                    self.assertEqual(response.status, 403)
                    self.assertEqual(
                        (await _response_json(response))["error"],
                        "HIL_TOOL_FORBIDDEN",
                    )

        self.assertEqual(raw_call.await_count, 2)

    async def test_hil_trigger_timeout_requires_exact_tool_and_canonical_hil_cache_key(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        listed_call = AsyncMock(return_value="ok")
        handler, _ = self._hil_handler()
        valid = (
            ("self.lesson_assets.evict_cache_key", {"cacheKey": self.HIL_CACHE_KEY}),
            (
                "self.lesson_assets.sync_to_sd",
                {"assetPack": {"cacheKey": self.HIL_CACHE_KEY, "assets": []}},
            ),
        )

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", listed_call):
            for tool_name, args in valid:
                response = await handler.handle_post(
                    _FakeRequest(
                        device_id="route-device-uuid",
                        body={
                            "toolName": tool_name,
                            "timeoutSeconds": 75,
                            "args": args,
                        },
                    )
                )
                self.assertEqual(response.status, 202)
                self.assertEqual(listed_call.await_args.kwargs["timeout"], 75)

            invalid = (
                ("self.lesson_assets.evict_cache_key", {"cacheKey": f"normal/v1-{'d' * 64}"}),
                ("self.lesson_assets.evict_cache_key", {"cacheKey": "hil-bad/../secret"}),
                ("self.lesson_assets.evict_cache_key", {"foreignKey": self.HIL_CACHE_KEY}),
                ("self.lesson_assets.sync_to_sd", {"cacheKey": self.HIL_CACHE_KEY}),
                ("self.lesson_assets.sync_to_sd", {"assetPack": {"cacheKey": "hil-bad/../secret"}}),
                ("self.lesson_assets.sync_to_sd.other", {"assetPack": {"cacheKey": self.HIL_CACHE_KEY}}),
                ("self.tool", {"cacheKey": self.HIL_CACHE_KEY}),
            )
            for tool_name, args in invalid:
                with self.subTest(tool_name=tool_name, args=args):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": tool_name,
                                "timeoutSeconds": 75,
                                "args": args,
                            },
                        )
                    )
                    self.assertEqual(response.status, 403)
                    self.assertEqual(
                        (await _response_json(response))["error"],
                        "HIL_TOOL_FORBIDDEN",
                    )

        self.assertEqual(listed_call.await_count, len(valid))

    async def test_hil_trigger_timeout_rejects_bool_and_out_of_range_values(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, _ = self._hil_handler()
        listed_call = AsyncMock(side_effect=AssertionError("must not dispatch"))

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", listed_call):
            for timeout in (True, False, 4, 76, 5.0, "5"):
                with self.subTest(timeout=timeout):
                    response = await handler.handle_post(
                        _FakeRequest(
                            device_id="route-device-uuid",
                            body={
                                "toolName": "self.lesson_assets.evict_cache_key",
                                "timeoutSeconds": timeout,
                                "args": {"cacheKey": self.HIL_CACHE_KEY},
                            },
                        )
                    )
                    self.assertEqual(response.status, 403)
                    self.assertEqual(
                        (await _response_json(response))["error"],
                        "HIL_TOOL_FORBIDDEN",
                    )

        listed_call.assert_not_awaited()

    async def test_hil_trigger_timeout_also_requires_allowlisted_resolved_mac(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, _ = self._hil_handler(allowlist=[])
        listed_call = AsyncMock(side_effect=AssertionError("must not dispatch"))

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", listed_call):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id="route-device-uuid",
                    body={
                        "toolName": "self.lesson_assets.evict_cache_key",
                        "timeoutSeconds": 75,
                        "args": {"cacheKey": self.HIL_CACHE_KEY},
                    },
                )
            )

        self.assertEqual(response.status, 403)
        self.assertEqual(
            (await _response_json(response))["error"],
            "HIL_DEVICE_NOT_ALLOWLISTED",
        )
        listed_call.assert_not_awaited()

    async def test_non_hil_calls_without_override_keep_existing_dispatch_and_timeout(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, conn = self._hil_handler(allowlist=[])
        listed_call = AsyncMock(return_value="ok")

        with patch("core.api.device_mcp_admin_handler.call_mcp_tool", listed_call):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id="route-device-uuid",
                    body={"toolName": "self.tool", "args": {"x": 1}},
                )
            )

        self.assertEqual(response.status, 202)
        listed_call.assert_awaited_once_with(
            conn,
            conn.mcp_client,
            "self.tool",
            {"x": 1},
            timeout=30,
        )

    async def test_non_hil_call_failure_preserves_existing_exception_message(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, _ = self._hil_handler(allowlist=[])
        leak = "Bearer jwt.secret /Users/private/key.pem password=hunter2"

        with patch(
            "core.api.device_mcp_admin_handler.call_mcp_tool",
            AsyncMock(side_effect=RuntimeError(leak)),
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id="route-device-uuid",
                    body={"toolName": "self.tool", "args": {}},
                )
            )

        payload = await _response_json(response)
        self.assertEqual(response.status, 409)
        self.assertEqual(
            payload,
            {"error": "MCP_CALL_FAILED", "message": leak},
        )

    async def test_non_hil_timeout_preserves_existing_exception_message(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, _ = self._hil_handler(allowlist=[])
        message = "Tool call request timed out"

        with patch(
            "core.api.device_mcp_admin_handler.call_mcp_tool",
            AsyncMock(side_effect=TimeoutError(message)),
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id="route-device-uuid",
                    body={"toolName": "self.tool", "args": {}},
                )
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            await _response_json(response),
            {"error": "MCP_CALL_FAILED", "message": message},
        )

    async def test_hil_failures_and_timeouts_never_echo_exception_secrets_or_paths(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler, _ = self._hil_handler()
        leaks = "Bearer jwt.secret /Users/private/key.pem password=hunter2"

        for exception, expected_error in (
            (TimeoutError(leaks), "HIL_MCP_TIMEOUT"),
            (RuntimeError(leaks), "HIL_MCP_FAILED"),
        ):
            with self.subTest(expected_error=expected_error), patch(
                "core.api.device_mcp_admin_handler._call_raw_mcp_tool",
                AsyncMock(side_effect=exception),
            ):
                response = await handler.handle_post(
                    _FakeRequest(
                        device_id="route-device-uuid",
                        body={
                            "toolName": self.HIL_TOOLS[0],
                            "allowUnlisted": True,
                            "args": {"cacheKey": self.HIL_CACHE_KEY},
                        },
                    )
                )
                payload = await _response_json(response)
                rendered = json.dumps(payload)
                self.assertEqual(response.status, 409)
                self.assertEqual(payload["error"], expected_error)
                for secret in ("jwt.secret", "/Users/private", "hunter2"):
                    self.assertNotIn(secret, rendered)

    async def test_real_asyncio_wait_for_timeout_maps_to_hil_timeout(self):
        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        cleaned = []

        class _NeverResolvingClient:
            async def get_next_id(self):
                return 42

            async def register_call_result_future(self, _call_id, _future):
                return None

            async def cleanup_call_result(self, call_id):
                cleaned.append(call_id)

        handler, conn = self._hil_handler()
        conn.mcp_client = _NeverResolvingClient()

        with patch(
            "core.api.device_mcp_admin_handler._hil_timeout",
            return_value=0.001,
        ), patch(
            "core.api.device_mcp_admin_handler.send_mcp_message",
            AsyncMock(return_value=None),
        ):
            response = await handler.handle_post(
                _FakeRequest(
                    device_id="route-device-uuid",
                    body={
                        "toolName": self.HIL_TOOLS[1],
                        "allowUnlisted": True,
                        "timeoutSeconds": 5,
                        "args": {},
                    },
                )
            )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            (await _response_json(response))["error"],
            "HIL_MCP_TIMEOUT",
        )
        self.assertEqual(cleaned, [42])


if __name__ == "__main__":
    unittest.main()
