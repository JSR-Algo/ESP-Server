import types
import unittest
from unittest.mock import patch

import httpx

from config import device_token_client
from config.voice_consent_client import VoiceConsentClient
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def info(self, *args, **_kwargs):
        self.messages.append(("info", args))

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
        self.client_ip = "192.168.0.103"
        self.sent = []
        self.voice_consent_client = types.SimpleNamespace(
            ensure_voice_allowed=self._ensure_voice_allowed,
        )
        self._allowed = allowed

    async def _ensure_voice_allowed(self, _conn):
        return self._allowed


class VoiceConsentGateTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        device_token_client._cache.clear()

    async def test_env_bypass_allows_voice_without_backend_consent(self):
        conn = _Conn(allowed=False)
        conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

        with patch.dict("os.environ", {"TBOT_BYPASS_VOICE_CONSENT": "true"}):
            allowed = await conn.voice_consent_client.ensure_voice_allowed(conn)

        self.assertTrue(allowed)

    async def test_factory_test_claimed_device_allows_voice_without_backend_consent(self):
        conn = _Conn(allowed=False)
        conn.device_id = "14:c1:9f:d1:a8:48"
        conn.config["server"]["factory_test_claimed_devices"] = ["14:C1:9F:D1:A8:48"]
        conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

        allowed = await conn.voice_consent_client.ensure_voice_allowed(conn)

        self.assertTrue(allowed)

    async def test_voice_consent_bypass_devices_allows_matching_mac_or_ip(self):
        for key, value in (("mac", "14:C1:9F:D1:A8:48"), ("ip", "192.168.0.103")):
            with self.subTest(key=key):
                conn = _Conn(allowed=False)
                conn.device_id = "14:c1:9f:d1:a8:48"
                conn.config["server"]["voice_consent_bypass_devices"] = [{key: value}]
                conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

                allowed = await conn.voice_consent_client.ensure_voice_allowed(conn)

                self.assertTrue(allowed)

    async def test_global_factory_test_claimed_allows_voice_without_backend_consent(self):
        conn = _Conn(allowed=False)
        conn.device_id = "cc:dd:ee:ff:00:11"  # in no allowlist
        conn.config["server"]["factory_test_claimed_all"] = True
        conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

        allowed = await conn.voice_consent_client.ensure_voice_allowed(conn)

        self.assertTrue(allowed)

    async def test_factory_test_claimed_device_bypass_is_cached_to_avoid_audio_frame_log_spam(self):
        conn = _Conn(allowed=False)
        conn.device_id = "14:c1:9f:d1:a8:48"
        conn.config["server"]["factory_test_claimed_devices"] = ["14:C1:9F:D1:A8:48"]
        conn.voice_consent_client = VoiceConsentClient(client=types.SimpleNamespace())

        self.assertTrue(await conn.voice_consent_client.ensure_voice_allowed(conn))
        self.assertTrue(await conn.voice_consent_client.ensure_voice_allowed(conn))

        warnings = [
            args[0]
            for level, args in conn.logger.messages
            if level == "info" and args
        ]
        self.assertEqual(
            warnings.count("voice consent bypass enabled for factory test claimed device"),
            1,
        )

    async def test_backend_consent_check_resolves_mac_to_uuid_and_sends_bearer_secret(self):
        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            def __init__(self):
                self.calls = []

            async def post(self, url, *, json, headers):
                self.calls.append(("POST", url, json, headers))
                return _Response({"data": {"deviceUuid": "device-uuid-1", "token": "jwt-1"}})

            async def get(self, url, *, headers):
                self.calls.append(("GET", url, None, headers))
                return _Response({"data": {"active": True}})

        conn = _Conn(allowed=False)
        conn.device_id = "14:c1:9f:d1:a8:48"
        client = _Client()
        consent = VoiceConsentClient(client=client)

        with patch.dict("os.environ", {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
            allowed = await consent.ensure_voice_allowed(conn)

        self.assertTrue(allowed)
        self.assertEqual(client.calls[0][0], "POST")
        self.assertEqual(client.calls[0][1], "https://backend.test/v1/internal/devices/mint-token")
        self.assertEqual(client.calls[0][2], {"mac": "14:c1:9f:d1:a8:48"})
        self.assertEqual(client.calls[0][3]["Authorization"], "Bearer mint-secret")
        self.assertEqual(client.calls[1][0], "GET")
        self.assertEqual(
            client.calls[1][1],
            "https://backend.test/v1/internal/devices/device-uuid-1/ai-voice-consent",
        )
        self.assertEqual(client.calls[1][3]["X-Mint-Secret"], "mint-secret")
        self.assertEqual(client.calls[1][3]["Authorization"], "Bearer mint-secret")

    async def test_active_consent_is_cached_to_avoid_stream_rate_limit_spam(self):
        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            def __init__(self):
                self.get_calls = 0

            async def post(self, *_args, **_kwargs):
                return _Response({"data": {"deviceUuid": "device-uuid-1", "token": "jwt-1"}})

            async def get(self, *_args, **_kwargs):
                self.get_calls += 1
                return _Response({"data": {"active": True}})

        conn = _Conn(allowed=False)
        client = _Client()
        consent = VoiceConsentClient(client=client)

        with patch.dict("os.environ", {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
            self.assertTrue(await consent.ensure_voice_allowed(conn))
            self.assertTrue(await consent.ensure_voice_allowed(conn))

        self.assertEqual(client.get_calls, 1)

    async def test_recent_active_consent_survives_backend_429(self):
        class _OkResponse:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _RateLimitedResponse:
            status_code = 429

            def raise_for_status(self):
                request = httpx.Request("GET", "https://backend.test/v1/internal/devices/device-uuid-1/ai-voice-consent")
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        class _Client:
            def __init__(self):
                self.get_calls = 0

            async def post(self, *_args, **_kwargs):
                return _OkResponse({"data": {"deviceUuid": "device-uuid-1", "token": "jwt-1"}})

            async def get(self, *_args, **_kwargs):
                self.get_calls += 1
                if self.get_calls == 1:
                    return _OkResponse({"data": {"active": True}})
                return _RateLimitedResponse()

        conn = _Conn(allowed=False)
        client = _Client()
        consent = VoiceConsentClient(client=client)

        env = {
            "TBOT_DEVICE_MINT_SECRET": "mint-secret",
            "TBOT_VOICE_CONSENT_CACHE_TTL_SECONDS": "0",
            "TBOT_VOICE_CONSENT_STALE_ON_429_TTL_SECONDS": "60",
        }
        with patch.dict("os.environ", env):
            self.assertTrue(await consent.ensure_voice_allowed(conn))
            self.assertTrue(await consent.ensure_voice_allowed(conn))

        self.assertEqual(client.get_calls, 2)

    async def test_start_session_ignores_consent_and_opens_live(self):
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

        self.assertTrue(opened)
        self.assertIs(conn.voice_provider, provider)
        self.assertTrue(handled)
        self.assertEqual(conn.sent, [])

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

    async def test_consent_withdrawal_no_longer_stops_active_voice(self):
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
        self.assertTrue(forwarded)
        self.assertEqual(conn.sent, [])


    async def test_consent_withdrawal_no_longer_stops_classic_fallback(self):
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
        self.assertTrue(forwarded)
        self.assertEqual(conn.sent, [])


if __name__ == "__main__":
    unittest.main()
