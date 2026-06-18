import types
import unittest
from unittest.mock import patch

from config.voice_consent_client import VoiceConsentClient
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    warning = error = debug = info


class _Conn:
    def __init__(self, allowed):
        self.logger = _Logger()
        self.config = {"server": {"api_url": "https://backend.test/v1"}}
        self.device_id = "device-1"
        self.session_id = "session-1"
        self.voice_provider = None
        self.func_handler = None
        self.client_abort = False
        self.client_is_speaking = False
        self.sent = []
        self.voice_consent_client = types.SimpleNamespace(
            ensure_voice_allowed=self._ensure_voice_allowed,
        )
        self._allowed = allowed

    async def _ensure_voice_allowed(self, _conn):
        return self._allowed


class VoiceConsentGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_env_bypass_allows_voice_without_backend_consent(self):
        conn = _Conn(allowed=False)
        conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

        with patch.dict("os.environ", {"TBOT_BYPASS_VOICE_CONSENT": "true"}):
            allowed = await conn.voice_consent_client.ensure_voice_allowed(conn)

        self.assertTrue(allowed)

    async def test_start_session_denies_before_opening_live_without_consent(self):
        conn = _Conn(allowed=False)
        opened = False

        async def open_live_session():
            nonlocal opened
            opened = True

        provider = GoogleLiveProvider(conn)
        async def ensure_func_handler():
            return None

        provider._ensure_func_handler = ensure_func_handler
        provider._open_live_session = open_live_session

        await provider.start_session()
        handled = await provider.handle_audio_bytes(b"child-audio")

        self.assertFalse(opened)
        self.assertIs(conn.voice_provider, provider)
        self.assertTrue(handled)
        self.assertEqual(conn.sent[-1]["type"], "alert")
        self.assertEqual(conn.sent[-1]["status"], "voice_consent_required")
        self.assertIn("parent", conn.sent[-1]["message"])

    async def test_start_session_opens_live_with_active_consent(self):
        conn = _Conn(allowed=True)
        opened = False

        async def open_live_session():
            nonlocal opened
            opened = True

        provider = GoogleLiveProvider(conn)
        async def ensure_func_handler():
            return None

        provider._ensure_func_handler = ensure_func_handler
        provider._open_live_session = open_live_session

        await provider.start_session()

        self.assertTrue(opened)
        self.assertIs(conn.voice_provider, provider)

    async def test_withdrawal_stops_active_voice_before_forwarding_next_frame(self):
        conn = _Conn(allowed=True)
        forwarded = False

        async def open_live_session():
            return None

        async def forward_input_audio(_audio):
            nonlocal forwarded
            forwarded = True

        provider = GoogleLiveProvider(conn)
        async def ensure_func_handler():
            return None

        provider._ensure_func_handler = ensure_func_handler
        provider._open_live_session = open_live_session
        provider._bridge = types.SimpleNamespace(
            decode_input_audio=lambda audio: audio,
            forward_decoded_input_audio=forward_input_audio,
        )
        async def close_live_resources():
            return None

        provider._close_live_resources = close_live_resources

        await provider.start_session()
        conn._allowed = False
        handled = await provider.handle_audio_bytes(b"child-audio")

        self.assertTrue(handled)
        self.assertFalse(forwarded)
        self.assertEqual(conn.sent[-1]["type"], "alert")
        self.assertEqual(conn.sent[-1]["status"], "voice_consent_required")


    async def test_withdrawal_stops_classic_fallback_before_forwarding_next_frame(self):
        conn = _Conn(allowed=True)
        forwarded = False

        class FallbackProvider:
            async def handle_text_message(self, _message):
                return True

            async def handle_audio_bytes(self, _audio):
                nonlocal forwarded
                forwarded = True
                return True

            async def interrupt(self):
                return None

            async def close(self):
                return None

        provider = GoogleLiveProvider(conn)
        provider._fallback_provider = FallbackProvider()

        conn._allowed = False
        handled = await provider.handle_audio_bytes(b"child-audio")

        self.assertTrue(handled)
        self.assertFalse(forwarded)
        self.assertEqual(conn.sent[-1]["type"], "alert")
        self.assertEqual(conn.sent[-1]["status"], "voice_consent_required")


if __name__ == "__main__":
    unittest.main()
