import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.handle import reportHandle


class _Logger:
    def __init__(self):
        self.errors = []
        self.debugs = []

    def bind(self, **_kwargs):
        return self

    def error(self, message, *args, **kwargs):
        self.errors.append((message, args, kwargs))

    def debug(self, message, *args, **kwargs):
        self.debugs.append((message, args, kwargs))


class _Queue:
    def __init__(self, fail=False):
        self.fail = fail
        self.items = []

    def put(self, item):
        if self.fail:
            raise RuntimeError("queue full")
        self.items.append(item)


def _conn(**overrides):
    data = {
        "device_id": "device-1",
        "session_id": "session-1",
        "logger": _Logger(),
        "read_config_from_api": True,
        "need_bind": False,
        "report_tts_enable": True,
        "report_asr_enable": True,
        "chat_history_conf": 2,
        "report_queue": _Queue(),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class ReportHandleTest(unittest.IsolatedAsyncioTestCase):
    async def test_report_submits_chat_record_with_optional_converted_audio(self):
        conn = _conn()

        with patch.object(reportHandle, "opus_to_wav", return_value=b"wav") as convert, patch.object(
            reportHandle, "manage_report", new=AsyncMock()
        ) as manage_report:
            await reportHandle.report(conn, 2, "hello", [b"opus"], 1234)
            await reportHandle.report(conn, 1, "text", None, 5678)

        convert.assert_called_once_with(conn, [b"opus"])
        self.assertEqual(manage_report.await_count, 2)
        self.assertEqual(manage_report.await_args_list[0].kwargs["audio"], b"wav")
        self.assertIsNone(manage_report.await_args_list[1].kwargs["audio"])
        self.assertEqual(manage_report.await_args_list[0].kwargs["mac_address"], "device-1")
        self.assertEqual(manage_report.await_args_list[0].kwargs["session_id"], "session-1")
        self.assertEqual(manage_report.await_args_list[0].kwargs["chat_type"], 2)

    async def test_report_logs_manage_api_failures_without_raising(self):
        conn = _conn()

        with patch.object(reportHandle, "manage_report", new=AsyncMock(side_effect=RuntimeError("api down"))):
            await reportHandle.report(conn, 1, "hello", None, 99)

        self.assertIn("Chat record report failed: api down", conn.logger.errors[0][0])


class OpusToWavTest(unittest.TestCase):
    def test_opus_to_wav_decodes_valid_packets_and_writes_pcm_header(self):
        conn = _conn()

        class FakeDecoder:
            def __init__(self, rate, channels):
                self.rate = rate
                self.channels = channels

            def decode(self, packet, frame_size):
                self.last_frame_size = frame_size
                return packet.upper()

        with patch.object(reportHandle.opuslib_next, "Decoder", new=FakeDecoder):
            wav = reportHandle.opus_to_wav(conn, [b"aa", b"bb"])

        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(wav[12:16], b"fmt ")
        self.assertEqual(wav[36:40], b"data")
        self.assertEqual(wav[40:44], (4).to_bytes(4, "little"))
        self.assertEqual(wav[44:], b"AABB")

    def test_opus_to_wav_skips_decode_errors_and_releases_decoder(self):
        conn = _conn()

        class FakeOpusError(Exception):
            pass

        class FakeDecoder:
            def decode(self, packet, _frame_size):
                if packet == b"bad":
                    raise FakeOpusError("bad packet")
                return b"ok"

        with patch.object(reportHandle.opuslib_next, "Decoder", return_value=FakeDecoder()), patch.object(
            reportHandle.opuslib_next, "OpusError", new=FakeOpusError
        ):
            wav = reportHandle.opus_to_wav(conn, [b"bad", b"good"])

        self.assertEqual(wav[44:], b"ok")
        self.assertIn("Opus decode error: bad packet", conn.logger.errors[0][0])

    def test_opus_to_wav_raises_when_all_packets_fail(self):
        conn = _conn()

        class FakeOpusError(Exception):
            pass

        class FakeDecoder:
            def decode(self, *_args):
                raise FakeOpusError("no audio")

        with patch.object(reportHandle.opuslib_next, "Decoder", return_value=FakeDecoder()), patch.object(
            reportHandle.opuslib_next, "OpusError", new=FakeOpusError
        ):
            with self.assertRaisesRegex(ValueError, "No valid PCM data"):
                reportHandle.opus_to_wav(conn, [b"bad"])


class EnqueueReportTest(unittest.TestCase):
    def test_tts_report_respects_gates_and_audio_policy(self):
        blocked = [
            _conn(read_config_from_api=False),
            _conn(need_bind=True),
            _conn(report_tts_enable=False),
            _conn(chat_history_conf=0),
        ]
        for conn in blocked:
            reportHandle.enqueue_tts_report(conn, "hello", [b"opus"])
            self.assertEqual(conn.report_queue.items, [])

        with_audio = _conn(chat_history_conf=2)
        text_only = _conn(chat_history_conf=1)
        with patch.object(reportHandle.time, "time", return_value=1.5):
            reportHandle.enqueue_tts_report(with_audio, "tts", [b"opus"])
            reportHandle.enqueue_tts_report(text_only, "tts", [b"opus"])

        self.assertEqual(with_audio.report_queue.items, [(2, "tts", [b"opus"], 1500)])
        self.assertEqual(text_only.report_queue.items, [(2, "tts", None, 1500)])

    def test_tts_report_logs_queue_failures(self):
        conn = _conn(report_queue=_Queue(fail=True))

        reportHandle.enqueue_tts_report(conn, "hello", [b"opus"])

        self.assertIn("Failed to add to TTS report queue: hello, queue full", conn.logger.errors[0][0])

    def test_asr_report_respects_gates_audio_policy_and_logs_failures(self):
        blocked = [
            _conn(read_config_from_api=False),
            _conn(need_bind=True),
            _conn(report_asr_enable=False),
            _conn(chat_history_conf=0),
        ]
        for conn in blocked:
            reportHandle.enqueue_asr_report(conn, "heard", [b"opus"])
            self.assertEqual(conn.report_queue.items, [])

        with_audio = _conn(chat_history_conf=2)
        text_only = _conn(chat_history_conf=1)
        with patch.object(reportHandle.time, "time", return_value=2.25):
            reportHandle.enqueue_asr_report(with_audio, "asr", [b"opus"])
            reportHandle.enqueue_asr_report(text_only, "asr", [b"opus"])

        self.assertEqual(with_audio.report_queue.items, [(1, "asr", [b"opus"], 2250)])
        self.assertEqual(text_only.report_queue.items, [(1, "asr", None, 2250)])

        failing = _conn(report_queue=_Queue(fail=True))
        reportHandle.enqueue_asr_report(failing, "heard", [b"opus"])
        self.assertIn("Failed to add to ASR report queue: heard, queue full", failing.logger.debugs[0][0])

    def test_tool_report_formats_call_and_result_records(self):
        conn = _conn()

        with patch.object(reportHandle.time, "time", return_value=3.0):
            reportHandle.enqueue_tool_report(conn, "move", {"arm": "left"}, "ok")

        call_record, result_record = conn.report_queue.items
        self.assertEqual(call_record[0], 3)
        self.assertEqual(result_record[0], 3)
        self.assertEqual(call_record[3], 3000)
        self.assertEqual(result_record[3], 3001)
        self.assertEqual(json.loads(call_record[1]), [{"type": "tool", "text": 'move({"arm": "left"})'}])
        self.assertEqual(json.loads(result_record[1]), [{"type": "tool_result", "text": '{"result":"ok"}'}])

    def test_tool_report_can_emit_only_result_and_respects_gates(self):
        blocked = [_conn(read_config_from_api=False), _conn(need_bind=True), _conn(chat_history_conf=0)]
        for conn in blocked:
            reportHandle.enqueue_tool_report(conn, "move", {}, "ok")
            self.assertEqual(conn.report_queue.items, [])

        conn = _conn()
        with patch.object(reportHandle.time, "time", return_value=4.0):
            reportHandle.enqueue_tool_report(conn, "move", {}, "ok", report_tool_call=False)

        self.assertEqual(len(conn.report_queue.items), 1)
        self.assertEqual(json.loads(conn.report_queue.items[0][1])[0]["type"], "tool_result")

    def test_tool_report_logs_queue_failures(self):
        conn = _conn(report_queue=_Queue(fail=True))

        reportHandle.enqueue_tool_report(conn, "move", {}, "ok")

        self.assertIn("Failed to add tool to report queue: queue full", conn.logger.errors[0][0])


if __name__ == "__main__":
    unittest.main()
