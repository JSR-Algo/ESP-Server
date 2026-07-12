import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.handle import sendAudioHandle
from core.voice.session_orchestrator import SessionMode


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Logger:
    def __init__(self):
        self.debugs = []
        self.infos = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message, *_args, **_kwargs):
        self.debugs.append(message)

    def info(self, message, *_args, **_kwargs):
        self.infos.append(message)


class _Conn:
    def __init__(self):
        self.config = {"enable_stop_tts_notify": False}
        self.tts = type("Tts", (), {"tts_audio_first_sentence": False})()
        self.session_id = "session-1"
        self.sentence_id = "old-sentence"
        self.client_abort = False
        self.client_is_speaking = True
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0
        self.close_after_chat = False
        self.websocket = _WebSocket()
        self.logger = _Logger()
        self.clear_speak_calls = 0
        self.closed = False

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False

    async def close(self):
        self.closed = True


class _DoneTask:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


class _Event:
    def __init__(self):
        self.waited = False

    async def wait(self):
        self.waited = True


class _RateController:
    def __init__(self, done=False):
        self.pending_send_task = _DoneTask(done)
        self.queue = []
        self.frame_duration = 60
        self.queue_empty_event = _Event()
        self.messages = []
        self.audio = []
        self.reset_calls = 0
        self.stop_calls = 0
        self.send_callback = None

    def add_message(self, message):
        self.messages.append(message)

    def add_audio(self, packet):
        self.audio.append(packet)

    async def wait_until_empty(self):
        await self.queue_empty_event.wait()

    def reset(self):
        self.reset_calls += 1
        self.pending_send_task = _DoneTask(False)

    def start_sending(self, callback):
        self.send_callback = callback

    def stop_sending(self):
        self.stop_calls += 1


class SendTtsStopTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_stop_still_releases_client_when_no_new_audio_started(self):
        conn = _Conn()

        async def mutate_sentence_id(_conn):
            _conn.sentence_id = "new-sentence"

        original_wait = sendAudioHandle._wait_for_audio_completion
        sendAudioHandle._wait_for_audio_completion = mutate_sentence_id
        try:
            await sendAudioHandle.send_tts_message(conn, "stop")
        finally:
            sendAudioHandle._wait_for_audio_completion = original_wait

        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [{"type": "tts", "state": "stop", "session_id": "session-1"}],
        )

    async def test_google_live_stop_keeps_device_in_realtime_listening(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}

        await sendAudioHandle.send_tts_message(conn, "stop")

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "stop",
                    "session_id": "session-1",
                    "continue_listening": True,
                    "listen_mode": "realtime",
                }
            ],
        )

    async def test_google_live_stop_does_not_play_local_stop_notify_audio(self):
        conn = _Conn()
        conn.config.update(
            {
                "voice_mode": {"type": "google_live"},
                "enable_stop_tts_notify": True,
                "stop_tts_notify_voice": "notify.mp3",
            }
        )

        with patch.object(sendAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"notify"])) as audio_to_data, patch.object(
            sendAudioHandle, "sendAudio", new=AsyncMock()
        ) as send_audio:
            await sendAudioHandle.send_tts_message(conn, "stop")

        audio_to_data.assert_not_awaited()
        send_audio.assert_not_awaited()
        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "stop",
                    "session_id": "session-1",
                    "continue_listening": True,
                    "listen_mode": "realtime",
                }
            ],
        )

    async def test_google_live_stop_does_not_open_generic_mic_during_lesson(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.session_mode = SessionMode.LESSON

        await sendAudioHandle.send_tts_message(conn, "stop")

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [{"type": "tts", "state": "stop", "session_id": "session-1"}],
        )

    async def test_lesson_prompt_stop_can_continue_realtime_listening_once(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.session_mode = SessionMode.LESSON
        conn.lesson_continue_listening_after_tts_stop = True

        await sendAudioHandle.sendAudioMessage(conn, sendAudioHandle.SentenceType.LAST, [], None)
        await sendAudioHandle.sendAudioMessage(conn, sendAudioHandle.SentenceType.LAST, [], None)

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "stop",
                    "session_id": "session-1",
                    "continue_listening": True,
                    "listen_mode": "realtime",
                },
                {"type": "tts", "state": "stop", "session_id": "session-1"},
            ],
        )

    async def test_google_live_stop_preserves_explicit_tts_stop_fields(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}

        await sendAudioHandle.send_tts_message(
            conn,
            "stop",
            extra_fields={"continue_listening": False, "listen_mode": "manual"},
        )

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "stop",
                    "session_id": "session-1",
                    "continue_listening": False,
                    "listen_mode": "manual",
                }
            ],
        )

    async def test_stt_message_does_not_put_device_into_speaking_before_audio(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.send_stt_message(conn, "Hi ESP")

        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [{"type": "stt", "text": "Hi ESP", "session_id": "session-1"}],
        )

    async def test_first_audio_message_sends_tts_start_before_audio(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.FIRST,
            [b"opus-frame"],
            "Hello",
        )

        self.assertTrue(conn.client_is_speaking)
        self.assertEqual(json.loads(conn.websocket.sent[0])["state"], "start")
        self.assertEqual(json.loads(conn.websocket.sent[1])["state"], "sentence_start")
        self.assertEqual(conn.websocket.sent[2], b"opus-frame")

    async def test_google_live_audio_message_drops_classic_audio_frames(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.client_is_speaking = False

        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.FIRST,
            [b"classic-opus"],
            "Local fallback",
        )

        self.assertEqual(conn.websocket.sent, [])
        self.assertFalse(conn.client_is_speaking)
        self.assertFalse(
            any("Send audio message:" in message for message in conn.logger.infos)
        )

    async def test_streaming_middle_audio_starts_tts_after_text_only_first(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.FIRST,
            None,
            "Lesson prompt",
        )
        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.MIDDLE,
            [b"opus-frame"],
            None,
        )

        sent_json = [json.loads(p) for p in conn.websocket.sent if isinstance(p, str)]
        self.assertEqual(sent_json[0]["state"], "sentence_start")
        self.assertEqual(sent_json[1]["state"], "start")
        self.assertEqual(conn.websocket.sent[-1], b"opus-frame")
        self.assertTrue(conn.client_is_speaking)

    async def test_audio_message_skips_stale_sentence_and_queues_sentence_start_for_active_flow(self):
        stale = _Conn()
        await sendAudioHandle.sendAudioMessage(
            stale,
            sendAudioHandle.SentenceType.FIRST,
            [b"old"],
            "Old",
            sentence_id="stale-sentence",
        )
        self.assertEqual(stale.websocket.sent, [])

        conn = _Conn()
        conn.client_is_speaking = True
        conn.tts.tts_audio_first_sentence = True
        conn.audio_rate_controller = _RateController(done=False)
        conn.audio_flow_control = {"packet_count": 0, "sequence": 0, "sentence_id": conn.sentence_id}

        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.FIRST,
            [b"opus-frame"],
            "Hello",
        )

        self.assertFalse(conn.tts.tts_audio_first_sentence)
        self.assertEqual(len(conn.audio_rate_controller.messages), 1)
        self.assertEqual(conn.websocket.sent, [b"opus-frame"])
        self.assertIn("Send first voice segment: Hello", conn.logger.infos)

    async def test_last_audio_message_sends_stop_and_closes_when_requested(self):
        conn = _Conn()
        conn.close_after_chat = True

        await sendAudioHandle.sendAudioMessage(conn, sendAudioHandle.SentenceType.LAST, [], None)

        self.assertTrue(conn.closed)
        self.assertEqual(json.loads(conn.websocket.sent[0])["state"], "stop")

    async def test_wait_for_audio_completion_waits_for_queue_and_prebuffer_delay(self):
        conn = _Conn()
        conn.audio_rate_controller = _RateController(done=False)
        conn.audio_rate_controller.queue = [b"queued"]
        conn.audio_rate_controller.frame_duration = 50

        with patch.object(sendAudioHandle.asyncio, "sleep", new=AsyncMock()) as sleep:
            await sendAudioHandle._wait_for_audio_completion(conn)

        self.assertTrue(conn.audio_rate_controller.queue_empty_event.waited)
        sleep.assert_awaited_once_with((sendAudioHandle.PRE_BUFFER_COUNT + 2) * 50 / 1000.0)

    async def test_mqtt_gateway_audio_frame_has_16_byte_header(self):
        conn = _Conn()

        await sendAudioHandle._send_to_mqtt_gateway(conn, b"opus", 0x01020304, 7)

        packet = conn.websocket.sent[0]
        self.assertEqual(packet[:16], bytes([1, 0, 0, 4, 0, 0, 0, 7, 1, 2, 3, 4, 0, 0, 0, 4]))
        self.assertEqual(packet[16:], b"opus")

    async def test_send_audio_handles_empty_single_packet_fixed_delay_dynamic_queue_and_abort(self):
        conn = _Conn()
        await sendAudioHandle.sendAudio(conn, None)
        await sendAudioHandle.sendAudio(conn, [])
        self.assertEqual(conn.websocket.sent, [])

        single = _Conn()
        with patch.object(sendAudioHandle, "AudioRateController", side_effect=lambda frame_duration: _RateController()):
            await sendAudioHandle.sendAudio(single, b"single")
        self.assertEqual(single.websocket.sent, [b"single"])

        fixed = _Conn()
        fixed.config["tts_audio_send_delay"] = 25
        fixed.audio_rate_controller = _RateController(done=False)
        fixed.audio_flow_control = {"packet_count": sendAudioHandle.PRE_BUFFER_COUNT, "sequence": 0, "sentence_id": fixed.sentence_id}
        with patch.object(sendAudioHandle.asyncio, "sleep", new=AsyncMock()) as sleep:
            await sendAudioHandle.sendAudio(fixed, [b"delayed"])
        sleep.assert_awaited_once_with(0.025)
        self.assertEqual(fixed.websocket.sent, [b"delayed"])

        dynamic = _Conn()
        dynamic.audio_rate_controller = _RateController(done=False)
        dynamic.audio_flow_control = {"packet_count": sendAudioHandle.PRE_BUFFER_COUNT, "sequence": 0, "sentence_id": dynamic.sentence_id}
        await sendAudioHandle.sendAudio(dynamic, [b"queued"])
        self.assertEqual(dynamic.audio_rate_controller.audio, [b"queued"])

        aborted = _Conn()
        aborted.client_abort = True
        aborted.audio_rate_controller = _RateController(done=False)
        aborted.audio_flow_control = {"packet_count": 0, "sequence": 0, "sentence_id": aborted.sentence_id}
        await sendAudioHandle.sendAudio(aborted, [b"skip"])
        self.assertEqual(aborted.websocket.sent, [])

    def test_rate_controller_reuses_resets_or_recreates_by_task_and_sentence_state(self):
        reuse = _Conn()
        reuse.audio_rate_controller = _RateController(done=False)
        reuse.audio_flow_control = {"packet_count": 1, "sequence": 2, "sentence_id": reuse.sentence_id}
        rate, flow = sendAudioHandle._get_or_create_rate_controller(reuse, 60, False)
        self.assertIs(rate, reuse.audio_rate_controller)
        self.assertEqual(flow["packet_count"], 1)

        stopped = _Conn()
        stopped.audio_rate_controller = _RateController(done=True)
        stopped.audio_flow_control = {"packet_count": 4, "sequence": 5, "sentence_id": stopped.sentence_id}
        sendAudioHandle._get_or_create_rate_controller(stopped, 60, False)
        self.assertEqual(stopped.audio_rate_controller.reset_calls, 1)
        self.assertEqual(stopped.audio_flow_control, {"packet_count": 0, "sequence": 0, "sentence_id": stopped.sentence_id})

        changed = _Conn()
        changed.audio_rate_controller = _RateController(done=False)
        changed.audio_flow_control = {"packet_count": 4, "sequence": 5, "sentence_id": "other"}
        sendAudioHandle._get_or_create_rate_controller(changed, 60, False)
        self.assertEqual(changed.audio_rate_controller.reset_calls, 1)

    async def test_background_sender_callback_updates_activity_sends_and_honors_abort(self):
        conn = _Conn()
        rate = _RateController(done=False)
        flow = {"packet_count": 0, "sequence": 0, "sentence_id": conn.sentence_id}
        sendAudioHandle._start_background_sender(conn, rate, flow)

        with patch.object(sendAudioHandle.time, "time", return_value=12.5):
            await rate.send_callback(b"packet")
        self.assertEqual(conn.last_activity_time, 12500)
        self.assertEqual(conn.websocket.sent, [b"packet"])

        conn.client_abort = True
        with self.assertRaises(sendAudioHandle.asyncio.CancelledError):
            await rate.send_callback(b"packet")

    async def test_do_send_audio_uses_mqtt_path_and_updates_flow(self):
        conn = _Conn()
        conn.conn_from_mqtt_gateway = True
        flow = {"packet_count": 2, "sequence": 9}

        with patch.object(sendAudioHandle.time, "time", return_value=1.25):
            await sendAudioHandle._do_send_audio(conn, b"opus", flow)

        self.assertEqual(flow, {"packet_count": 3, "sequence": 10})
        self.assertEqual(conn.websocket.sent[0][16:], b"opus")

    async def test_sentence_start_includes_child_name_from_private_profile(self):
        conn = _Conn()
        conn.config["child_profile"] = {"child_name": "Bong"}

        await sendAudioHandle.send_tts_message(conn, "sentence_start", "Welcome to the barn story.")

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "sentence_start",
                    "session_id": "session-1",
                    "text": "Welcome to the barn story.",
                    "child_name": "Bong",
                    "childName": "Bong",
                }
            ],
        )

    async def test_tts_message_ignores_sentence_start_without_text_and_sets_start_state(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.send_tts_message(conn, "sentence_start", None)
        self.assertEqual(conn.websocket.sent, [])

        await sendAudioHandle.send_tts_message(conn, "start")
        self.assertTrue(conn.client_is_speaking)
        self.assertEqual(json.loads(conn.websocket.sent[0]), {"type": "tts", "state": "start", "session_id": "session-1"})

    async def test_stop_message_can_play_notify_skip_stale_stop_and_stop_rate_controller(self):
        notify = _Conn()
        notify.config.update({"enable_stop_tts_notify": True, "stop_tts_notify_voice": "notify.mp3"})
        notify.audio_rate_controller = _RateController(done=False)
        with patch.object(sendAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"notify"])), patch.object(
            sendAudioHandle, "sendAudio", new=AsyncMock()
        ) as send_audio:
            await sendAudioHandle.send_tts_message(notify, "stop")
        send_audio.assert_awaited_once_with(notify, [b"notify"])
        self.assertEqual(notify.audio_rate_controller.stop_calls, 1)
        self.assertEqual(notify.clear_speak_calls, 1)

        stale = _Conn()

        async def mutate_to_new_flow(_conn):
            _conn.sentence_id = "new-sentence"
            _conn.audio_flow_control = {"sentence_id": "new-sentence"}

        with patch.object(sendAudioHandle, "_wait_for_audio_completion", new=mutate_to_new_flow):
            await sendAudioHandle.send_tts_message(stale, "stop")
        self.assertEqual(stale.websocket.sent, [])
        self.assertEqual(stale.clear_speak_calls, 0)

    def test_child_name_helper_rejects_bad_config_shapes(self):
        self.assertIsNone(sendAudioHandle._child_name_for_tts_state(SimpleNamespace(config="bad")))
        self.assertIsNone(sendAudioHandle._child_name_for_tts_state(SimpleNamespace(config={"child_profile": "bad"})))
        self.assertIsNone(sendAudioHandle._child_name_for_tts_state(SimpleNamespace(config={"child_profile": {"childName": 5}})))

    async def test_sentence_start_omits_child_name_when_profile_has_no_name(self):
        conn = _Conn()
        conn.config["child_profile"] = {"child_name": "  "}

        await sendAudioHandle.send_tts_message(conn, "sentence_start", "Welcome to the barn story.")

        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [
                {
                    "type": "tts",
                    "state": "sentence_start",
                    "session_id": "session-1",
                    "text": "Welcome to the barn story.",
                }
            ],
        )

    async def test_stt_message_skips_end_prompt_parses_speaker_json_and_handles_bad_json(self):
        end_prompt = _Conn()
        end_prompt.config["end_prompt"] = {"prompt": "bye prompt"}
        await sendAudioHandle.send_stt_message(end_prompt, "bye prompt")
        self.assertEqual(end_prompt.websocket.sent, [])

        malformed_end_prompt = _Conn()
        malformed_end_prompt.config["end_prompt"] = "bad"
        await sendAudioHandle.send_stt_message(malformed_end_prompt, "hello")
        self.assertEqual(
            json.loads(malformed_end_prompt.websocket.sent[0]),
            {"type": "stt", "text": "hello", "session_id": "session-1"},
        )

        conn = _Conn()
        await sendAudioHandle.send_stt_message(conn, '{"speaker":"Ada","content":"Hi!!!"}')
        self.assertEqual(conn.current_speaker, "Ada")
        self.assertEqual(json.loads(conn.websocket.sent[0]), {"type": "stt", "text": "Hi", "session_id": "session-1"})

        bad = _Conn()
        await sendAudioHandle.send_stt_message(bad, "{bad json}")
        self.assertEqual(json.loads(bad.websocket.sent[0]), {"type": "stt", "text": "{bad json}", "session_id": "session-1"})

    async def test_display_message_sends_stt_payload_without_text_cleanup(self):
        conn = _Conn()

        await sendAudioHandle.send_display_message(conn, "Raw display text!")

        self.assertEqual(json.loads(conn.websocket.sent[0]), {"type": "stt", "text": "Raw display text!", "session_id": "session-1"})


if __name__ == "__main__":
    unittest.main()
