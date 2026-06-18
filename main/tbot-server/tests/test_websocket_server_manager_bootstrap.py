import unittest
import asyncio
import json
from types import SimpleNamespace

import core.websocket_server as websocket_server


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _FakeWebSocket:
    def __init__(self, headers=None, path="/tbot/v1/"):
        self.request = SimpleNamespace(path=path, headers=headers or {})
        self.sent = []
        self.close_calls = []

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.close_calls.append((code, reason))


class WebSocketServerManagerBootstrapTest(unittest.IsolatedAsyncioTestCase):
    def _build_server(self, config):
        original_initialize_modules = websocket_server.initialize_modules
        original_setup_logging = websocket_server.setup_logging
        try:
            websocket_server.initialize_modules = lambda *args, **kwargs: {}
            websocket_server.setup_logging = lambda: _DummyLogger()
            return websocket_server.WebSocketServer(config)
        finally:
            websocket_server.initialize_modules = original_initialize_modules
            websocket_server.setup_logging = original_setup_logging

    def test_manager_bootstrap_skips_classic_modules_until_private_config(self):
        calls = []
        original_initialize_modules = websocket_server.initialize_modules
        original_setup_logging = websocket_server.setup_logging

        def fake_initialize_modules(
            logger,
            config,
            init_vad=False,
            init_asr=False,
            init_llm=False,
            init_tts=False,
            init_memory=False,
            init_intent=False,
        ):
            calls.append(
                (
                    init_vad,
                    init_asr,
                    init_llm,
                    init_tts,
                    init_memory,
                    init_intent,
                )
            )
            return {"vad": object()}

        config = {
            "read_config_from_api": True,
            "selected_module": {
                "VAD": "VAD_SileroVAD",
                "ASR": "ASR_FunASR",
                "LLM": "GeminiProLLM",
                "Memory": "nomem",
                "Intent": "nointent",
            },
            "server": {
                "auth_key": "test-secret",
                "auth": {"enabled": False},
            },
        }

        try:
            websocket_server.initialize_modules = fake_initialize_modules
            websocket_server.setup_logging = lambda: _DummyLogger()

            server = websocket_server.WebSocketServer(config)
        finally:
            websocket_server.initialize_modules = original_initialize_modules
            websocket_server.setup_logging = original_setup_logging

        self.assertIsNotNone(server._vad)
        self.assertEqual(
            calls,
            [
                (False, False, False, False, False, False),
            ],
        )

    def test_manager_bootstrap_retry_avoids_optional_classic_modules(self):
        calls = []
        original_initialize_modules = websocket_server.initialize_modules
        original_setup_logging = websocket_server.setup_logging

        def fake_initialize_modules(
            logger,
            config,
            init_vad=False,
            init_asr=False,
            init_llm=False,
            init_tts=False,
            init_memory=False,
            init_intent=False,
        ):
            calls.append(
                (
                    init_vad,
                    init_asr,
                    init_llm,
                    init_tts,
                    init_memory,
                    init_intent,
                )
            )
            if len(calls) == 1:
                raise RuntimeError("VAD bootstrap failed")
            return {"vad": object()}

        config = {
            "read_config_from_api": True,
            "selected_module": {
                "VAD": "VAD_SileroVAD",
                "ASR": "ASR_FunASR",
                "LLM": "GeminiProLLM",
                "Memory": "nomem",
                "Intent": "nointent",
            },
            "server": {
                "auth_key": "test-secret",
                "auth": {"enabled": False},
            },
        }

        try:
            websocket_server.initialize_modules = fake_initialize_modules
            websocket_server.setup_logging = lambda: _DummyLogger()

            server = websocket_server.WebSocketServer(config)
        finally:
            websocket_server.initialize_modules = original_initialize_modules
            websocket_server.setup_logging = original_setup_logging

        self.assertIsNotNone(server._vad)
        self.assertEqual(
            calls,
            [
                (False, False, False, False, False, False),
                (False, False, False, False, False, False),
            ],
        )

    def test_query_authorization_is_used_when_device_headers_exist(self):
        websocket = SimpleNamespace(
            request=SimpleNamespace(
                path="/tbot/v1/?authorization=Bearer%20token-from-query",
                headers={
                    "device-id": "3c:0f:02:de:c2:e0",
                    "client-id": "agent-1",
                },
            )
        )

        websocket_server.WebSocketServer._copy_query_identity_headers(websocket)

        self.assertEqual(
            websocket.request.headers["authorization"],
            "Bearer token-from-query",
        )

    def test_query_authorization_does_not_override_header(self):
        websocket = SimpleNamespace(
            request=SimpleNamespace(
                path="/tbot/v1/?authorization=Bearer%20token-from-query",
                headers={
                    "device-id": "3c:0f:02:de:c2:e0",
                    "client-id": "agent-1",
                    "authorization": "Bearer token-from-header",
                },
            )
        )

        websocket_server.WebSocketServer._copy_query_identity_headers(websocket)

        self.assertEqual(
            websocket.request.headers["authorization"],
            "Bearer token-from-header",
        )

    def test_audio_admission_cap_is_derived_from_measured_stream_cost(self):
        server = self._build_server(
            {
                "selected_module": {},
                "server": {
                    "auth_key": "test-secret",
                    "auth": {"enabled": False},
                    "audio_admission": {
                        "enabled": True,
                        "cpu_cores": 4,
                        "headroom_fraction": 0.75,
                        "measured_cost_per_stream_core_fraction": 0.5,
                    },
                },
            }
        )

        self.assertEqual(server.accept_cap, 6)

    async def test_new_device_websocket_over_audio_admission_cap_is_rejected(self):
        server = self._build_server(
            {
                "selected_module": {},
                "server": {
                    "auth_key": "test-secret",
                    "auth": {"enabled": False},
                    "audio_admission": {
                        "enabled": True,
                        "hard_accept_cap": 1,
                        "retry_after_ms": 1500,
                    },
                },
            }
        )
        server._active_device_connections = 1
        websocket = _FakeWebSocket(
            headers={"device-id": "device-2", "client-id": "client-2"}
        )

        await server._handle_connection(websocket)

        self.assertEqual(json.loads(websocket.sent[0])["reason"], "active_stream_cap")
        self.assertEqual(websocket.close_calls, [(1013, "active stream cap reached")])

    async def test_new_device_websocket_is_rejected_while_draining(self):
        server = self._build_server(
            {
                "selected_module": {},
                "server": {
                    "auth_key": "test-secret",
                    "auth": {"enabled": False},
                },
            }
        )
        server.begin_drain()
        websocket = _FakeWebSocket(
            headers={"device-id": "device-2", "client-id": "client-2"}
        )

        await server._handle_connection(websocket)

        self.assertEqual(json.loads(websocket.sent[0])["reason"], "server_draining")
        self.assertEqual(websocket.close_calls, [(1012, "server draining")])

    async def test_drain_waits_for_active_connection_tasks_before_returning(self):
        server = self._build_server(
            {
                "selected_module": {},
                "server": {
                    "auth_key": "test-secret",
                    "auth": {"enabled": False},
                },
            }
        )
        active = asyncio.create_task(asyncio.sleep(0.01))
        server._connection_tasks.add(active)

        await server.drain(timeout=1.0)

        self.assertTrue(server.is_draining)
        self.assertTrue(active.done())
