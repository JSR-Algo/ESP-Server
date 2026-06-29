import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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

    async def wait_for_run_loop(self):
        return None

class _Controller:
    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1

class PlayMusicLiveFlowTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        play_music_live.MUSIC_CACHE.clear()

    def test_scan_music_files_and_cache_respect_config(self):
        with tempfile.TemporaryDirectory() as music_dir:
            os.makedirs(os.path.join(music_dir, "kids"))
            open(os.path.join(music_dir, "kids", "Song.MP3"), "wb").close()
            open(os.path.join(music_dir, "ignore.txt"), "wb").close()
            conn = _Conn()
            conn.config["plugins"] = {
                "play_music": {"music_dir": music_dir, "music_ext": [".mp3"]}
            }

            cache = play_music_live._ensure_music_cache(conn)
            second = play_music_live._ensure_music_cache(conn)

        self.assertEqual(cache["music_files"], [os.path.join("kids", "Song.MP3")])
        self.assertIs(cache, second)
        self.assertEqual(play_music_live._scan_music_files("/missing/music", (".mp3",)), [])

    def test_generic_random_music_descriptors_pick_random_song(self):
        cache = {"music_files": ["song-a.mp3", "song-b.mp3"]}

        selected, status = play_music_live._pick_song("random remix", cache)

        self.assertEqual(status, "random")
        self.assertIn(selected, cache["music_files"])

    def test_pick_song_handles_empty_library_and_exactish_match(self):
        self.assertEqual(play_music_live._pick_song("anything", {"music_files": []}), (None, "empty_library"))
        selected, status = play_music_live._pick_song(
            "Hai con than lan", {"music_files": ["kids/Hai Con Than Lan.mp3"]}
        )

        self.assertEqual(selected, "kids/Hai Con Than Lan.mp3")
        self.assertEqual(status, "matched")
        self.assertEqual(
            play_music_live._list_titles({"music_files": ["a/One.mp3", "Two.wav"]}, limit=1),
            ["One"],
        )

    def test_specific_unknown_song_still_reports_not_found(self):
        cache = {"music_files": ["song-a.mp3", "song-b.mp3"]}

        selected, status = play_music_live._pick_song("totally different title", cache)

        self.assertIsNone(selected)
        self.assertEqual(status, "not_found")

    def test_music_session_state_helpers(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn._music_session = session

        session.pause()
        self.assertTrue(session.is_paused())
        session.resume()
        self.assertFalse(session.is_paused())
        session.stop()
        self.assertTrue(session.stop_event.is_set())
        play_music_live._clear_session(conn, session)

        self.assertIsNone(getattr(conn, "_music_session", None))

    async def test_wait_for_voice_output_handles_invalid_delay_and_stop(self):
        conn = _Conn()
        conn.config["live_music_start_delay_sec"] = "bad"
        conn.google_live_audio_out_started_at = 1
        conn.client_is_speaking = True
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)

        async def stop_soon():
            await asyncio.sleep(0.01)
            session.stop()

        task = asyncio.create_task(stop_soon())
        await play_music_live._wait_for_voice_output_to_settle(session)
        await task

        self.assertTrue(session.stop_event.is_set())

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

    async def test_stream_loop_exits_when_encoding_produces_no_frames(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)

        def fake_audio_to_data_stream(*_args, **_kwargs):
            return None

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
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

        self.assertEqual(conn.websocket.sent, [])

    async def test_stream_loop_stops_before_start_and_interrupts_encode(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        session.stop()

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"late-frame")

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
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

        self.assertEqual(session.frames, [])
        self.assertEqual(conn.websocket.sent, [])

    async def test_stream_loop_handles_pause_and_resume(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        session.pause()

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"frame-1")

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
        sys.modules["core.utils.util"] = fake_util_module
        sys.modules["core.utils.opus_encoder_utils"] = fake_encoder_module
        try:
            task = asyncio.create_task(play_music_live._stream_music_loop(session))
            await asyncio.sleep(0.01)
            session.resume()
            await task
        finally:
            for name, module in originals.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(session.frame_index, 1)

    async def test_stream_loop_breaks_when_stopped_while_paused(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        session.pause()

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"frame-1")

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
        sys.modules["core.utils.util"] = fake_util_module
        sys.modules["core.utils.opus_encoder_utils"] = fake_encoder_module
        try:
            task = asyncio.create_task(play_music_live._stream_music_loop(session))
            await asyncio.sleep(0.01)
            session.stop()
            await task
        finally:
            for name, module in originals.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(session.frame_index, 0)

    async def test_stream_loop_stops_on_duration_deadline(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        session.deadline = 0.0

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"frame-1")

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
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

        self.assertEqual(session.frame_index, 1)

    async def test_stream_loop_cancellation_sets_stop_and_swallows_wait_timeout(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)

        def fake_audio_to_data_stream(*_args, **_kwargs):
            import time as real_time

            real_time.sleep(0.05)

        async def fake_wait_for(_future, timeout=None):
            raise asyncio.TimeoutError

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
        sys.modules["core.utils.util"] = fake_util_module
        sys.modules["core.utils.opus_encoder_utils"] = fake_encoder_module
        try:
            with patch.object(play_music_live.asyncio, "wait_for", new=fake_wait_for):
                task = asyncio.create_task(play_music_live._stream_music_loop(session))
                await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await asyncio.sleep(0.06)
        finally:
            for name, module in originals.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertTrue(session.stop_event.is_set())

    async def test_stream_loop_logs_send_errors_and_clears_session(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn._music_session = session

        def fake_audio_to_data_stream(*_args, callback=None, **_kwargs):
            callback(b"frame-1")

        async def raising_send(_session, _frame):
            raise RuntimeError("send failed")

        fake_util_module = types.ModuleType("core.utils.util")
        fake_util_module.audio_to_data_stream = fake_audio_to_data_stream
        fake_encoder_module = types.ModuleType("core.utils.opus_encoder_utils")
        fake_encoder_module.OpusEncoderUtils = lambda *args, **kwargs: object()
        originals = {
            name: sys.modules.get(name)
            for name in ("core.utils.util", "core.utils.opus_encoder_utils")
        }
        sys.modules["core.utils.util"] = fake_util_module
        sys.modules["core.utils.opus_encoder_utils"] = fake_encoder_module
        try:
            with patch.object(play_music_live, "_send_music_frame", new=raising_send):
                await play_music_live._stream_music_loop(session)
        finally:
            for name, module in originals.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertIsNone(getattr(conn, "_music_session", None))

    async def test_stream_loop_wait_branch_can_stop_after_recheck(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        session.frames = [b"frame-1"]
        calls = {"paused": 0}

        def fake_is_paused():
            calls["paused"] += 1
            return calls["paused"] == 1

        async def no_wait(_session):
            return None

        async def fake_sleep(_delay):
            session.stop()

        session.is_paused = fake_is_paused
        with patch.object(play_music_live.asyncio, "sleep", new=fake_sleep), patch.object(
            play_music_live, "_wait_for_voice_output_to_settle", new=no_wait
        ):
            with patch("core.utils.opus_encoder_utils.OpusEncoderUtils", new=lambda *a, **k: object()), patch(
                "core.utils.util.audio_to_data_stream", new=lambda *a, callback=None, **k: callback(b"frame-1")
            ):
                await play_music_live._stream_music_loop(session)

        self.assertTrue(session.stop_event.is_set())

    async def test_wait_for_voice_output_returns_when_voice_is_idle(self):
        conn = _Conn()
        conn.config["live_music_start_delay_sec"] = 0.1
        conn.google_live_audio_out_started_at = None
        conn.client_is_speaking = False
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)

        await play_music_live._wait_for_voice_output_to_settle(session)

        self.assertFalse(session.stop_event.is_set())

    async def test_send_music_frame_handles_abort_and_mqtt_gateway(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn.client_abort = True

        await play_music_live._send_music_frame(session, b"ignored")

        self.assertEqual(conn.websocket.sent, [])
        conn.client_abort = False
        conn.conn_from_mqtt_gateway = True
        await play_music_live._send_music_frame(session, b"abc")

        payload = conn.websocket.sent[0]
        self.assertEqual(payload[0], 1)
        self.assertEqual(int.from_bytes(payload[2:4], "big"), 3)
        self.assertEqual(payload[16:], b"abc")

    async def test_playback_state_ignores_missing_socket_and_stale_owner(self):
        conn = _Conn()
        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        no_socket_conn = _Conn()
        no_socket_conn.websocket = None
        no_socket = play_music_live._MusicSession(no_socket_conn, "/tmp/song.mp3", 24000)

        await play_music_live._send_music_playback_state(no_socket, "start")
        await play_music_live._send_music_playback_state(session, "start")
        await play_music_live._send_music_playback_state(session, "stop")

        self.assertIsNone(getattr(no_socket_conn, "_music_playback_owner", None))
        self.assertIsNone(getattr(conn, "_music_playback_owner", None))
        self.assertEqual(len(conn.websocket.sent), 2)

    def test_format_reply_replaces_fields_and_allows_empty_template(self):
        self.assertEqual(play_music_live._format_reply("", title="Song"), "")
        self.assertEqual(
            play_music_live._format_reply("Phát {title}/{song}", title="A", song="B"),
            "Phát A/B",
        )

    async def test_play_music_validates_loop_library_and_unknown_titles(self):
        conn = _Conn()
        conn.loop = None

        self.assertEqual(play_music_live.play_music(conn, "random").action, play_music_live.Action.ERROR)

        conn.loop = asyncio.get_running_loop()
        with patch.object(play_music_live, "_ensure_music_cache", return_value={"music_dir": "/tmp", "music_files": []}):
            empty = play_music_live.play_music(conn, "random")
        with patch.object(
            play_music_live,
            "_ensure_music_cache",
            return_value={"music_dir": "/tmp", "music_files": ["One.mp3"]},
        ):
            unknown = play_music_live.play_music(conn, "Missing")

        self.assertEqual(empty.action, play_music_live.Action.ERROR)
        self.assertIn("Thư mục nhạc trống", empty.response)
        self.assertEqual(unknown.action, play_music_live.Action.ERROR)
        self.assertIn("One", unknown.response)

    async def test_play_music_handles_empty_library_pick_status(self):
        conn = _Conn()
        conn.loop = asyncio.get_running_loop()

        with patch.object(
            play_music_live,
            "_ensure_music_cache",
            return_value={"music_dir": "/tmp", "music_files": ["ghost.mp3"]},
        ), patch.object(play_music_live, "_pick_song", return_value=(None, "empty_library")):
            result = play_music_live.play_music(conn, "random")

        self.assertEqual(result.action, play_music_live.Action.ERROR)
        self.assertIn("chưa có bài hát", result.response)

    async def test_play_music_starts_session_replaces_existing_and_formats_reply(self):
        conn = _Conn()
        conn.loop = asyncio.get_running_loop()
        existing = play_music_live._MusicSession(conn, "/tmp/old.mp3", 24000)
        conn._music_session = existing

        async def fake_stream(session):
            await asyncio.sleep(0)

        with patch.object(play_music_live, "_stream_music_loop", new=fake_stream), patch.object(
            play_music_live,
            "_ensure_music_cache",
            return_value={"music_dir": "/music", "music_files": ["kids/Song.mp3"]},
        ):
            result = play_music_live.play_music(
                conn,
                "Song",
                duration_minutes="0.001",
                response_success="Đang phát {title}",
            )
            await conn._music_session.stream_task

        self.assertEqual(result.action, play_music_live.Action.RESPONSE)
        self.assertEqual(result.result, "Song")
        self.assertEqual(result.response, "Đang phát Song")
        self.assertTrue(existing.stop_event.is_set())
        self.assertIsNotNone(conn._music_session.deadline)

    async def test_play_music_ignores_invalid_duration(self):
        conn = _Conn()
        conn.loop = asyncio.get_running_loop()

        async def fake_stream(session):
            await asyncio.sleep(0)

        with patch.object(play_music_live, "_stream_music_loop", new=fake_stream), patch.object(
            play_music_live,
            "_ensure_music_cache",
            return_value={"music_dir": "/music", "music_files": ["Song.mp3"]},
        ):
            result = play_music_live.play_music(conn, "random", duration_minutes="bad")
            await conn._music_session.stream_task

        self.assertEqual(result.action, play_music_live.Action.RESPONSE)
        self.assertIsNone(conn._music_session.deadline)

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

    def test_stop_pause_resume_default_responses_and_states(self):
        conn = _Conn()

        self.assertEqual(play_music_live.stop_music(conn).result, "not_playing")
        self.assertEqual(play_music_live.pause_music(conn).result, "not_playing")
        self.assertEqual(play_music_live.resume_music(conn).result, "no_session")

        session = play_music_live._MusicSession(conn, "/tmp/song.mp3", 24000)
        conn._music_session = session
        self.assertEqual(play_music_live.resume_music(conn).result, "already_playing")
        session.pause()
        self.assertEqual(play_music_live.pause_music(conn).result, "already_paused")
        resumed = play_music_live.resume_music(conn, "Tiếp tục {title}")

        self.assertEqual(resumed.result, "resumed")
        self.assertEqual(resumed.response, "Tiếp tục song")

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
