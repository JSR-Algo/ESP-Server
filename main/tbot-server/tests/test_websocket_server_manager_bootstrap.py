import unittest

import core.websocket_server as websocket_server


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def warning(self, *args, **kwargs):
        return None


class WebSocketServerManagerBootstrapTest(unittest.TestCase):
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
