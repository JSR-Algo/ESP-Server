import json
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from core.handle import helloHandle
from core.providers.tts.dto.dto import SentenceType


class _Logger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        self.infos.append(_args[0] if _args else None)

    def warning(self, message, *args, **_kwargs):
        self.warnings.append((message, args))


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Dialogue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


class _Conn:
    def __init__(self):
        self.config = {
            "enable_wakeup_words_response_cache": True,
            "enable_stop_tts_notify": False,
            "tts_audio_send_delay": 0,
            "wakeup_words": ["hiesp"],
        }
        self.tts = types.SimpleNamespace(voice="default", tts_audio_first_sentence=True)
        self.websocket = _WebSocket()
        self.session_id = "session-1"
        self.sentence_id = "old-sentence"
        self.client_abort = False
        self.client_is_speaking = True
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0
        self.logger = _Logger()
        self.dialogue = _Dialogue()
        self.clear_speak_calls = 0
        self.sample_rate = 16000

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False


class WakeupWordsReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_false_when_tts_never_initializes(self):
        conn = _Conn()
        conn.tts = None

        with patch.object(helloHandle.time, "time", side_effect=[0, 3.1]):
            handled = await helloHandle.checkWakeupWords(conn, "hiesp")

        self.assertFalse(handled)

    async def test_waits_briefly_for_tts_before_checking_wakeup_word(self):
        conn = _Conn()
        delayed_tts = conn.tts
        conn.tts = None

        async def sleep(_delay):
            conn.tts = delayed_tts

        with patch.object(helloHandle.asyncio, "sleep", new=sleep), patch.object(
            helloHandle.time, "time", side_effect=[0.0, 0.1, 0.2]
        ):
            handled = await helloHandle.checkWakeupWords(conn, "not wake")

        self.assertFalse(handled)

    async def test_returns_false_when_cache_disabled_or_text_is_not_wakeup_word(self):
        conn = _Conn()
        conn.config["enable_wakeup_words_response_cache"] = False
        self.assertFalse(await helloHandle.checkWakeupWords(conn, "hiesp"))

        conn.config["enable_wakeup_words_response_cache"] = True
        self.assertFalse(await helloHandle.checkWakeupWords(conn, "hello"))

    async def test_google_live_wakeup_word_does_not_use_cached_local_audio(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}

        with patch.object(helloHandle, "audio_to_data", new=AsyncMock(return_value=[b"opus"])) as audio_to_data, patch.object(
            helloHandle, "sendAudioMessage", new=AsyncMock()
        ) as send_audio, patch.object(helloHandle, "send_tts_message", new=AsyncMock()) as send_tts, patch.object(
            helloHandle.wakeup_words_config,
            "get_wakeup_response",
            return_value={
                "file_path": "config/assets/wakeup_words_short.wav",
                "text": "I'm here!",
                "time": 10**12,
            },
        ):
            handled = await helloHandle.checkWakeupWords(conn, "hiesp")

        self.assertFalse(handled)
        audio_to_data.assert_not_awaited()
        send_audio.assert_not_awaited()
        send_tts.assert_not_awaited()
        self.assertFalse(getattr(conn, "just_woken_up", False))

    async def test_wakeup_reply_uses_fallback_audio_and_refreshes_stale_cache(self):
        conn = _Conn()
        conn.tts.voice = ""
        sent_audio = []
        created = []

        async def send_audio(_conn, sentence_type, opus_packets, text):
            sent_audio.append((sentence_type, opus_packets, text))

        def create_task(coro):
            created.append(coro)
            coro.close()
            return types.SimpleNamespace(done=lambda: True)

        with patch.object(helloHandle, "audio_to_data", new=AsyncMock(return_value=[b"opus"])), patch.object(
            helloHandle, "sendAudioMessage", new=send_audio
        ), patch.object(helloHandle.asyncio, "create_task", side_effect=create_task), patch.object(
            helloHandle.wakeup_words_config, "get_wakeup_response", return_value={}
        ), patch.object(helloHandle.uuid, "uuid4", return_value=types.SimpleNamespace(hex="new-sentence")), patch.object(
            helloHandle.time, "time", side_effect=[100.0, 100.1, 200.0]
        ):
            handled = await helloHandle.checkWakeupWords(conn, "hi esp!")

        self.assertTrue(handled)
        self.assertTrue(conn.just_woken_up)
        self.assertFalse(conn.client_abort)
        self.assertEqual(conn.sentence_id, "new-sentence")
        self.assertEqual(sent_audio[0], (SentenceType.FIRST, [b"opus"], "I'm here!"))
        self.assertEqual(sent_audio[1], (SentenceType.LAST, [], None))
        self.assertEqual(conn.dialogue.messages[-1].content, "I'm here!")
        self.assertEqual(len(created), 1)

    async def test_wakeup_reply_audio_failure_still_sends_tts_stop(self):
        conn = _Conn()

        async def failing_audio_to_data(*_args, **_kwargs):
            raise RuntimeError("cached wake reply audio unavailable")

        original_audio_to_data = helloHandle.audio_to_data
        original_wakeup_words_config = helloHandle.wakeup_words_config
        helloHandle.audio_to_data = failing_audio_to_data
        helloHandle.wakeup_words_config = types.SimpleNamespace(
            get_wakeup_response=lambda _voice: {
                "file_path": "config/assets/wakeup_words_short.wav",
                "text": "I'm here!",
                "time": 0,
            }
        )
        try:
            handled = await helloHandle.checkWakeupWords(conn, "hiesp")
        finally:
            helloHandle.audio_to_data = original_audio_to_data
            helloHandle.wakeup_words_config = original_wakeup_words_config

        self.assertTrue(handled)
        self.assertEqual(
            [json.loads(payload)["state"] for payload in conn.websocket.sent],
            ["stop"],
        )
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertTrue(conn.logger.warnings)

    async def test_wakeup_words_response_generates_and_persists_cached_audio(self):
        conn = _Conn()
        conn.tts = types.SimpleNamespace(voice="child", to_tts=Mock(return_value=[b"opus-a", b"opus-b"]))
        updates = []

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "wake.wav"
            config = types.SimpleNamespace(
                generate_file_path=Mock(return_value=str(output_path)),
                update_wakeup_response=lambda voice, file_path, text: updates.append((voice, file_path, text)),
            )

            with patch.object(helloHandle, "wakeup_words_config", config), patch.object(
                helloHandle.random, "choice", return_value="Ready"
            ), patch.object(helloHandle, "opus_datas_to_wav_bytes", return_value=b"wav-bytes") as opus_to_wav:
                await helloHandle.wakeupWordsResponse(conn)

            self.assertEqual(output_path.read_bytes(), b"wav-bytes")
            self.assertEqual(updates, [("child", str(output_path), "Ready")])
            opus_to_wav.assert_called_once_with([b"opus-a", b"opus-b"], sample_rate=16000)

    async def test_wakeup_words_response_returns_without_tts_or_without_generated_audio(self):
        no_tts = _Conn()
        no_tts.tts = None
        await helloHandle.wakeupWordsResponse(no_tts)

        no_result = _Conn()
        no_result.tts = types.SimpleNamespace(voice="child", to_tts=Mock(return_value=None))
        with patch.object(helloHandle.random, "choice", return_value="Ready"), patch.object(
            helloHandle.wakeup_words_config, "update_wakeup_response"
        ) as update:
            await helloHandle.wakeupWordsResponse(no_result)
        update.assert_not_called()

        empty_choice = _Conn()
        empty_choice.tts = types.SimpleNamespace(voice="child", to_tts=Mock(return_value=[b"opus"]))
        with patch.object(helloHandle.random, "choice", return_value=""), patch.object(
            helloHandle.wakeup_words_config, "update_wakeup_response"
        ) as update:
            await helloHandle.wakeupWordsResponse(empty_choice)
        update.assert_not_called()

    async def test_wakeup_words_response_returns_when_lock_cannot_be_acquired(self):
        conn = _Conn()
        conn.tts = types.SimpleNamespace(voice="child", to_tts=Mock(return_value=[b"opus"]))

        class LockedOut:
            async def acquire(self):
                return False

            def locked(self):
                return False

            def release(self):
                raise AssertionError("release should not be called")

        with patch.object(helloHandle, "_wakeup_response_lock", LockedOut()), patch.object(
            helloHandle.random, "choice"
        ) as choice:
            await helloHandle.wakeupWordsResponse(conn)

        choice.assert_not_called()


if __name__ == "__main__":
    unittest.main()
