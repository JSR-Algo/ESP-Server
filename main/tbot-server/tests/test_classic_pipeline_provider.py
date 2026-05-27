import unittest

from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider


class DummyConn:
    def __init__(self):
        self.client_abort = False
        self.start_calls = 0

    async def _start_classic_pipeline_session(self):
        self.start_calls += 1


class ClassicPipelineProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_wraps_classic_path_without_consuming_messages(self):
        conn = DummyConn()
        provider = ClassicPipelineProvider(conn)

        self.assertIsInstance(provider, ClassicPipelineProvider)

        await provider.start_session()
        self.assertEqual(conn.start_calls, 1)

        self.assertFalse(await provider.handle_text_message('{"type":"listen"}'))
        self.assertFalse(await provider.handle_audio_bytes(b"opus-frame"))

        await provider.interrupt()
        self.assertTrue(conn.client_abort)

        self.assertIsNone(await provider.close())
