import os
import unittest

from core.voice.google_live.client import GoogleLiveClient


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


@unittest.skipUnless(
    os.environ.get("RUN_GOOGLE_LIVE_SMOKE") == "1"
    and os.environ.get("GOOGLE_API_KEY"),
    "Set RUN_GOOGLE_LIVE_SMOKE=1 and GOOGLE_API_KEY to run live smoke test",
)
class GoogleLiveSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_and_close_live_session(self):
        client = GoogleLiveClient(
            {
                "api_key": "${GOOGLE_API_KEY}",
                "model": os.environ.get(
                    "GOOGLE_LIVE_MODEL",
                    "gemini-3.1-flash-live-preview",
                ),
                "enable_audio_input": True,
                "enable_audio_output": True,
                "native_voice": False,
                "connect_timeout_sec": 15,
                "recv_timeout_sec": 5,
            },
            _DummyLogger(),
        )

        await client.connect()
        self.assertTrue(client.connected)
        await client.close()
        self.assertFalse(client.connected)
