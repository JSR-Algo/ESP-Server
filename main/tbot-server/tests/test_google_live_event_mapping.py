import importlib
import json
import sys
import time
import types
import unittest


class _DummyLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        self.messages.append(("debug", args, kwargs))
        return None

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))
        return None

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))
        return None

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))
        return None


class _DummyWebSocket:
    def __init__(self):
        self.sent_messages = []

    async def send(self, payload):
        self.sent_messages.append(payload)


class _DummyConn:
    def __init__(self, google_live_config=None):
        self.logger = _DummyLogger()
        self.websocket = _DummyWebSocket()
        self.session_id = "session-1"
        self.config = {"enable_stop_tts_notify": False, "tts_audio_send_delay": -1}
        if google_live_config is not None:
            self.config["google_live"] = google_live_config
        self.conn_from_mqtt_gateway = False
        self.client_abort = False
        self.client_is_speaking = True
        self.last_activity_time = 0
        self.sentence_id = "sentence-1"
        self.google_live_session_started_at = None
        self.google_live_audio_out_started_at = None
        self.clear_queue_calls = 0

    def clearSpeakStatus(self):
        self.client_is_speaking = False

    def clear_queues(self):
        self.clear_queue_calls += 1


class _DummyClient:
    async def send_audio(self, audio_bytes):
        return None


class _FakeStreamingEncoder:
    def __init__(self, sample_rate=24000):
        self.sample_rate = sample_rate
        self.buffer = bytearray()
        self.calls = []

    def encode_pcm_to_opus_stream(self, pcm_data, end_of_stream, callback):
        self.calls.append((len(pcm_data), end_of_stream))
        self.buffer.extend(pcm_data)
        frame_bytes = int(self.sample_rate * 60 / 1000) * 2
        while len(self.buffer) >= frame_bytes:
            del self.buffer[:frame_bytes]
            callback(b"opus-frame")
        if end_of_stream and self.buffer:
            self.buffer.clear()
            callback(b"opus-tail")


class _FailingDecoder:
    def decode(self, audio_bytes, frame_size):
        raise RuntimeError("bad opus frame")


class GoogleLiveEventMappingTest(unittest.IsolatedAsyncioTestCase):
    def _build_bridge(self, conn, **kwargs):
        stub_module = types.ModuleType("core.handle.sendAudioHandle")

        async def send_display_message(stub_conn, text):
            await stub_conn.websocket.send(
                json.dumps(
                    {
                        "type": "stt",
                        "text": text,
                        "session_id": stub_conn.session_id,
                    }
                )
            )

        async def send_tts_message(stub_conn, state, text=None):
            message = {
                "type": "tts",
                "state": state,
                "session_id": stub_conn.session_id,
            }
            if text is not None:
                message["text"] = text
            if state == "stop":
                stub_conn.clearSpeakStatus()
            await stub_conn.websocket.send(json.dumps(message))

        async def sendAudio(stub_conn, audios, frame_duration=60):
            audio_packets = [audios] if isinstance(audios, bytes) else list(audios)
            for index, packet in enumerate(audio_packets):
                if stub_conn.conn_from_mqtt_gateway:
                    header = bytearray(16)
                    header[0] = 1
                    header[2:4] = len(packet).to_bytes(2, "big")
                    header[4:8] = index.to_bytes(4, "big")
                    header[8:12] = (1000 + index).to_bytes(4, "big")
                    header[12:16] = len(packet).to_bytes(4, "big")
                    await stub_conn.websocket.send(bytes(header) + packet)
                else:
                    await stub_conn.websocket.send(packet)

        stub_module.send_display_message = send_display_message
        stub_module.send_tts_message = send_tts_message
        stub_module.sendAudio = sendAudio
        sys.modules["core.handle.sendAudioHandle"] = stub_module

        bridge_module = importlib.import_module("core.voice.google_live.audio_bridge")
        bridge_module = importlib.reload(bridge_module)
        return bridge_module.GoogleLiveAudioBridge(
            conn,
            _DummyClient(),
            conn.logger,
            **kwargs,
        )

    async def test_transcript_audio_states_and_audio_chunks_map_to_existing_surfaces(self):
        conn = _DummyConn()
        bridge = self._build_bridge(conn)
        conn.client_is_speaking = False

        await bridge.handle_event({"type": "transcript", "text": "hello world"})
        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event({"type": "audio_chunk", "audio": b"chunk-1"})
        await bridge.handle_event({"type": "audio", "audio": b"chunk-2"})
        await bridge.handle_event({"type": "audio_end"})

        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "stt", "text": "hello world", "session_id": "session-1"},
        )
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[1]),
            {"type": "tts", "state": "start", "session_id": "session-1"},
        )
        self.assertEqual(conn.websocket.sent_messages[2], b"chunk-1")
        self.assertEqual(conn.websocket.sent_messages[3], b"chunk-2")
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[4]),
            {"type": "tts", "state": "stop", "session_id": "session-1"},
        )
        self.assertFalse(conn.client_is_speaking)

    async def test_audio_start_marks_client_as_speaking(self):
        conn = _DummyConn()
        conn.client_is_speaking = False
        bridge = self._build_bridge(conn)

        await bridge.handle_event({"type": "audio_start"})

        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "tts", "state": "start", "session_id": "session-1"},
        )
        self.assertTrue(conn.client_is_speaking)
        self.assertIsNotNone(conn.google_live_audio_out_started_at)

    async def test_model_transcript_can_map_to_llm_surface_when_enabled(self):
        conn = _DummyConn({"send_llm_state_events": True})
        bridge = self._build_bridge(conn)

        await bridge.handle_event(
            {"type": "transcript", "text": "thinking", "source": "model"}
        )

        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "llm", "text": "thinking", "session_id": "session-1"},
        )

    async def test_model_transcript_llm_surface_preserves_json_escaping(self):
        conn = _DummyConn({"send_llm_state_events": True})
        bridge = self._build_bridge(conn)

        await bridge.handle_event(
            {"type": "transcript", "text": 'say "hi"\nnow', "source": "model"}
        )

        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "llm", "text": 'say "hi"\nnow', "session_id": "session-1"},
        )

    async def test_interruption_sets_client_abort(self):
        conn = _DummyConn()
        bridge = self._build_bridge(conn)
        conn.client_is_speaking = True

        handled = await bridge.handle_event({"type": "interruption"})

        self.assertTrue(handled)
        self.assertTrue(conn.client_abort)
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "tts", "state": "stop", "session_id": "session-1"},
        )
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(conn.clear_queue_calls, 1)

    async def test_server_interruption_is_ignored_when_disabled_by_config(self):
        conn = _DummyConn({"disable_server_side_interruptions": True})
        bridge = self._build_bridge(conn)
        conn.client_is_speaking = True

        handled = await bridge.handle_event({"type": "interruption"})

        self.assertTrue(handled)
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.client_is_speaking)
        self.assertEqual(conn.websocket.sent_messages, [])
        self.assertEqual(conn.clear_queue_calls, 0)

    async def test_cancelled_response_audio_chunks_are_dropped(self):
        conn = _DummyConn()
        current_response_id = 1
        cancelled_response_ids = set()
        bridge = self._build_bridge(
            conn,
            response_id_getter=lambda: current_response_id,
            response_cancelled_checker=lambda response_id: response_id in cancelled_response_ids,
        )

        await bridge.handle_event({"type": "audio_start"})
        cancelled_response_ids.add(1)
        current_response_id = 2
        handled = await bridge.handle_event({"type": "audio_chunk", "audio": b"old"})
        await bridge.handle_event({"type": "audio_end"})

        self.assertTrue(handled)
        self.assertEqual(
            [json.loads(message) for message in conn.websocket.sent_messages],
            [{"type": "tts", "state": "start", "session_id": "session-1"}],
        )

    async def test_stop_output_drops_late_chunks_from_cancelled_response(self):
        conn = _DummyConn()
        bridge = self._build_bridge(conn, response_id_getter=lambda: 1)

        await bridge.handle_event({"type": "audio_start"})
        await bridge.stop_output()
        await bridge.handle_event({"type": "audio_chunk", "audio": b"old"})
        await bridge.handle_event({"type": "audio_end"})

        self.assertEqual(
            [json.loads(message) for message in conn.websocket.sent_messages],
            [
                {"type": "tts", "state": "start", "session_id": "session-1"},
                {"type": "tts", "state": "stop", "session_id": "session-1"},
            ],
        )

    async def test_stop_output_suppresses_late_audio_start_inside_window(self):
        conn = _DummyConn({"interrupt_suppress_audio_sec": 1})
        bridge = self._build_bridge(conn, response_id_getter=lambda: 1)

        await bridge.stop_output()
        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event({"type": "audio_chunk", "audio": b"old"})

        self.assertEqual(
            [json.loads(message) for message in conn.websocket.sent_messages],
            [{"type": "tts", "state": "stop", "session_id": "session-1"}],
        )

    async def test_stop_output_blocks_delayed_audio_start_until_clean_user_turn(self):
        conn = _DummyConn({"interrupt_suppress_audio_sec": 0})
        bridge = self._build_bridge(conn, response_id_getter=lambda: 1)

        await bridge.stop_output()
        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event({"type": "audio_chunk", "audio": b"old"})
        await bridge.handle_event({"type": "transcript", "text": "new input", "source": "user"})
        bridge.allow_model_output()
        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event({"type": "audio_chunk", "audio": b"new"})

        self.assertEqual(json.loads(conn.websocket.sent_messages[0])["state"], "stop")
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[1]),
            {"type": "stt", "text": "new input", "session_id": "session-1"},
        )
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[2]),
            {"type": "tts", "state": "start", "session_id": "session-1"},
        )
        self.assertEqual(conn.websocket.sent_messages[3], b"new")

    async def test_user_transcript_triggers_handler_while_music_is_playing(self):
        conn = _DummyConn({"barge_in_via_transcript": True})
        conn.client_is_speaking = False
        conn._music_session = object()
        calls = []

        async def user_transcript_handler(text):
            calls.append(text)

        bridge = self._build_bridge(
            conn,
            user_transcript_barge_in_handler=user_transcript_handler,
        )

        await bridge.handle_event(
            {"type": "transcript", "text": "Hi ESP tắt nhạc", "source": "user"}
        )

        self.assertEqual(calls, ["Hi ESP tắt nhạc"])

    async def test_pcm_audio_chunk_uses_classic_send_path_for_mqtt_clients(self):
        conn = _DummyConn()
        conn.conn_from_mqtt_gateway = True
        bridge = self._build_bridge(conn)
        bridge._encode_output_audio = lambda audio_bytes, mime_type=None: [b"opus-1", b"opus-2"]

        await bridge.handle_event(
            {
                "type": "audio_chunk",
                "audio": b"pcm-audio",
                "mime_type": "audio/pcm;rate=24000",
            }
        )

        self.assertEqual(len(conn.websocket.sent_messages), 2)
        self.assertEqual(conn.websocket.sent_messages[0][:1], b"\x01")
        self.assertEqual(conn.websocket.sent_messages[0][16:], b"opus-1")
        self.assertEqual(conn.websocket.sent_messages[1][16:], b"opus-2")

    async def test_empty_encoded_output_packet_is_not_sent(self):
        conn = _DummyConn()
        bridge = self._build_bridge(conn)
        bridge._encode_output_audio = lambda audio_bytes, mime_type=None: []

        await bridge.handle_event(
            {
                "type": "audio_chunk",
                "audio": b"pcm-audio",
                "mime_type": "audio/pcm;rate=24000",
            }
        )

        self.assertEqual(conn.websocket.sent_messages, [])

    async def test_invalid_input_pcm_is_not_forwarded_to_live(self):
        conn = _DummyConn()
        forwarded = []
        client = _DummyClient()

        async def send_audio(audio_bytes):
            forwarded.append(audio_bytes)

        client.send_audio = send_audio
        bridge = self._build_bridge(conn)
        bridge.client = client

        await bridge.forward_decoded_input_audio(b"\x01")

        self.assertEqual(forwarded, [])
        self.assertTrue(
            any(
                level == "warning" and args and "invalid input audio" in args[0]
                for level, args, _kwargs in conn.logger.messages
            )
        )

    async def test_corrupt_input_opus_is_dropped_without_forwarding(self):
        conn = _DummyConn()
        forwarded = []
        client = _DummyClient()

        async def send_audio(audio_bytes):
            forwarded.append(audio_bytes)

        client.send_audio = send_audio
        bridge = self._build_bridge(conn)
        bridge.client = client
        bridge._input_decoder = _FailingDecoder()

        decoded = bridge.decode_input_audio(b"not-opus")
        await bridge.forward_decoded_input_audio(decoded)

        self.assertEqual(decoded, b"")
        self.assertEqual(forwarded, [])
        self.assertTrue(
            any(
                level == "warning" and args and "corrupt input opus" in args[0]
                for level, args, _kwargs in conn.logger.messages
            )
        )

    async def test_pcm_audio_chunks_stream_without_padding_until_audio_end(self):
        conn = _DummyConn()
        bridge = self._build_bridge(conn)
        fake_encoder = _FakeStreamingEncoder(sample_rate=24000)
        bridge._output_encoder = fake_encoder
        bridge._get_output_encoder = lambda sample_rate: fake_encoder

        await bridge.handle_event(
            {
                "type": "audio_chunk",
                "audio": b"\x01\x00" * 100,
                "mime_type": "audio/pcm;rate=24000",
            }
        )
        await bridge.handle_event(
            {
                "type": "audio_chunk",
                "audio": b"\x02\x00" * 100,
                "mime_type": "audio/pcm;rate=24000",
            }
        )

        self.assertEqual(conn.websocket.sent_messages, [])

        await bridge.handle_event({"type": "audio_end"})

        self.assertEqual(conn.websocket.sent_messages[0], b"opus-tail")
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[1]),
            {"type": "tts", "state": "stop", "session_id": "session-1"},
        )
        self.assertEqual(
            fake_encoder.calls,
            [(200, False), (200, False), (0, True)],
        )

    async def test_first_audio_chunk_logs_first_audio_latency_once(self):
        conn = _DummyConn()
        conn.google_live_session_started_at = time.monotonic() - 0.01
        bridge = self._build_bridge(conn)

        await bridge.handle_event({"type": "audio_chunk", "audio": b"chunk-1"})
        await bridge.handle_event({"type": "audio_chunk", "audio": b"chunk-2"})

        info_messages = [
            args[0]
            for level, args, _kwargs in conn.logger.messages
            if level == "info" and args
        ]
        self.assertEqual(
            sum("first_audio_out_latency_ms" in message for message in info_messages),
            1,
        )
