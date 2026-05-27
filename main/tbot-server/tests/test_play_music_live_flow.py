import asyncio
import json
import sys
import types
import unittest

from plugins_func.functions import play_music_live

class _Logger:
    def bind(self, **_):
        return self

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

class _Conn:
    def __init__(self):
        self.config = {"live_music_start_delay_sec": 0}
        self.websocket = _WebSocket()
        self.session_id = "s-1"
        self.sample_rate = 24000
        self.client_abort = False
        self.client_is_speaking = False
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0
        self.logger = _Logger()

class _Controller:
    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1

class PlayMusicLiveFlowTest(unittest.IsolatedAsyncioTestCase):
    def test_generic_random_music_descriptors_pick_random_song(self):
        cache = {"music_files": ["song-a.mp3", "song-b.mp3"]}

        selected, status = play_music_live._pick_song("random remix", cache)

        self.assertEqual(status, "random")
        self.assertIn(selected, cache["music_files"])

    def test_specific_unknown_song_still_reports_not_found(self):
        cache = {"music_files": ["song-a.mp3", "song-b.mp3"]}

        selected, status = play_music_live._pick_song("totally different title", cache)

        self.assertIsNone(selected)
        self.assertEqual(status, "not_found")

    async def test_music_stream_signals_device_without_shared_audio_queue(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)

        send_audio_calls = []
        tts_messages = []

        async def fake_send_audio(_conn, audios, frame_duration=60):
            send_audio_calls.append((audios, frame_duration))

        async def fake_send_tts_message(_conn, state, text=None):
            tts_messages.append((state, text))

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"frame-1")
            callback(b"frame-2")

        fake_send_audio_module = types.ModuleType("core.handle.sendAudioHandle")
        fake_send_audio_module.sendAudio = fake_send_audio
        fake_send_audio_module.send_tts_message = fake_send_tts_message
        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()

        originals = {
            name: sys.modules.get(name)
            for name in (
                "core.handle.sendAudioHandle",
                "core.utils.util",
                "core.utils.opus_encoder_utils",
            )
        }
        sys.modules["core.handle.sendAudioHandle"] = fake_send_audio_module
        sys.modules["core.utils.util"] = fake_util_module
        sys.modules["core.utils.opus_encoder_utils"] = fake_encoder_module
        try:
            await play_music_live._stream_music_loop(session)
        finally:
            for name, module in originals.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(send_audio_calls, [])
        self.assertEqual(tts_messages, [])
        self.assertEqual(
            conn.websocket.sent,
            [
                json.dumps(
                    {"type": "tts", "state": "start", "session_id": "s-1"}
                ),
                b"frame-1",
                b"frame-2",
                json.dumps(
                    {"type": "tts", "state": "stop", "session_id": "s-1"}
                ),
            ],
        )
        self.assertFalse(conn.client_is_speaking)

    async def test_stop_and_pause_music_do_not_reset_shared_voice_queue(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.audio_rate_controller = _Controller()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn._music_session = session

        stop_response = play_music_live.stop_music(conn, "Đã tắt nhạc.")

        self.assertEqual(stop_response.result, "stopped")
        self.assertEqual(conn.audio_rate_controller.reset_calls, 0)
        self.assertTrue(conn.client_is_speaking)

        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn._music_session = session
        pause_response = play_music_live.pause_music(conn, "Đã tạm dừng.")

        self.assertEqual(pause_response.result, "paused")
        self.assertEqual(conn.audio_rate_controller.reset_calls, 0)
        self.assertTrue(conn.client_is_speaking)

    async def test_stale_music_session_cannot_stop_new_music_playback(self):
        conn = _Conn()
        old_session = play_music_live._MusicSession(conn, "/tmp/old.mp3", 24000)
        new_session = play_music_live._MusicSession(conn, "/tmp/new.mp3", 24000)

        await play_music_live._send_music_playback_state(new_session, "start")
        await play_music_live._send_music_playback_state(old_session, "stop")

        self.assertEqual(
            conn.websocket.sent,
            [
                json.dumps(
                    {"type": "tts", "state": "start", "session_id": "s-1"}
                )
            ],
        )
        self.assertIs(conn._music_playback_owner, new_session)

if __name__ == "__main__":
    unittest.main()
