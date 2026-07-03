import tempfile
import unittest
from pathlib import Path

from scripts.analyze_google_live_log import analyze, summarize_pains


class AnalyzeGoogleLiveLogTest(unittest.TestCase):
    def test_interruptibility_events_are_counted(self):
        log = "\n".join(
            [
                "2026-05-20 14:00:00 Google Live receive loop started",
                "2026-05-20 14:00:01 Google Live audio_start",
                "2026-05-20 14:00:01 Google Live echo_suppressed reason=robot_speaking bytes=1920 rms=300",
                "2026-05-20 14:00:01 Google Live aec_live_vad_forward reason=robot_speaking bytes=1920 rms=280",
                "2026-05-20 14:00:02 Google Live echo_bypass reason=robot_speaking bytes=1920 rms=2600",
                "2026-05-20 14:00:02 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
                "2026-05-20 14:00:02 Google Live stale_model_event_dropped type=transcript reason=blocked_until_user_turn response_id=0 current_response_id=1",
                "2026-05-20 14:00:02 Google Live model_output_still_blocked_waiting_user_turn after 1500 ms",
                "2026-05-20 14:00:03 Google Live clean_user_turn_opened reason=audio_input response_id=1",
                "2026-05-20 14:00:03 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=2 bytes=3840 response_id=1",
                "2026-05-20 14:00:04 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=420 response_id=1 frames=4 bytes=7680 peak_rms=2600",
                "2026-05-20 14:00:03 Google Live music_control_intent tool=stop_music text_preview='tắt nhạc'",
                "2026-05-20 14:00:04 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["echo_suppressed"], 1)
        self.assertEqual(totals["aec_live_vad_forward"], 1)
        self.assertEqual(totals["echo_bypass"], 1)
        self.assertEqual(totals["stale_model_event_dropped"], 1)
        self.assertEqual(totals["model_output_still_blocked_waiting_user_turn"], 1)
        self.assertEqual(totals["clean_user_turn_opened"], 1)
        self.assertEqual(totals["replayed_interrupt_audio"], 1)
        self.assertEqual(totals["interrupt_input_finalized"], 1)
        self.assertEqual(totals["music_control_intents"], 1)
        self.assertEqual(report["aec_live_vad_forward_rms"], {"count": 1, "min": 280, "max": 280, "mean": 280, "median": 280, "p95": 280, "p99": 280})
        self.assertEqual(report["echo_bypass_rms"], {"count": 1, "min": 2600, "max": 2600, "mean": 2600, "median": 2600, "p95": 2600, "p99": 2600})
        self.assertEqual(report["interrupt_reason_distribution"], {"loud_input": 1})

    def test_production_audio_gateway_markers_are_counted(self):
        log = "\n".join(
            [
                "2026-05-28 10:00:00 Google Live receive loop started",
                "2026-05-28 10:00:01 audio_decision decision=suppress_echo reason=robot_speaking state=MODEL_SPEAKING turn_id=2 response_id=3 audio_seq=9",
                "2026-05-28 10:00:01 interrupt_started reason=wake state=INTERRUPTING turn_id=3 response_id=4",
                "2026-05-28 10:00:01 output_queue_cleared reason=interrupt response_id=3",
                "2026-05-28 10:00:02 reconnect_started reason=session_expiring attempt=1 state=RECONNECTING",
                "2026-05-28 10:00:03 reconnect_succeeded attempt=1 live_connection_id=live-2",
                "2026-05-28 10:00:04 reconnect_failed attempt=2 error_class=network",
                "2026-05-28 10:00:05 fallback_triggered reason=auth",
                "2026-05-28 10:00:05 Google Live fallback_disabled reason=quota exceeded 429",
                "2026-05-28 10:00:06 music_state_changed state=paused trigger=user_interrupt",
                "2026-05-28 10:00:06 audio_output_transport_closed reason=normal_close detail=received 1000 OK",
                "2026-05-28 10:00:07 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["audio_decision"], 1)
        self.assertEqual(totals["interrupt_started"], 1)
        self.assertEqual(totals["output_queue_cleared"], 1)
        self.assertEqual(totals["reconnect_started"], 1)
        self.assertEqual(totals["reconnect_succeeded"], 1)
        self.assertEqual(totals["reconnect_failed"], 1)
        self.assertEqual(totals["fallback_disabled_sessions"], 1)
        self.assertEqual(totals["music_state_changed"], 1)
        self.assertEqual(totals["audio_output_transport_closed"], 1)

    def test_lesson_local_tts_marker_is_counted(self):
        log = "\n".join(
            [
                "2026-05-28 10:00:00 Google Live receive loop started",
                "2026-05-28 10:00:01 Google Live lesson_step_prompt queued via tts text='Xin chào.'",
                "2026-05-28 10:00:01 Google Live lesson_start_ack queued via tts text='Bắt đầu bài học nhé.'",
                "2026-05-28 10:00:02 Google Live lesson_step_prompt sent via live text chars=8 sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "2026-05-28 10:00:03 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["lesson_prompt_local_tts"], 2)
        self.assertEqual(totals["lesson_prompt_live_text"], 1)


class SummarizePainsTest(unittest.TestCase):
    def _make_log(self, lines: list[str]) -> Path:
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / "server.log"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_returns_five_pain_keys(self):
        path = self._make_log(["2026-05-20 10:00:00 nothing here"])
        result = summarize_pains(path)
        self.assertIn("P1_user_speech_lost", result)
        self.assertIn("P2_stop_latency", result)
        self.assertIn("P3_response_overlap", result)
        self.assertIn("P4_function_calls", result)
        self.assertIn("P5_music_ducking", result)

    def test_p1_counts_interrupts_and_transcripts(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
            "2026-05-20 10:00:01 Google Live user_interrupted reason=vad cancelled_response_id=1 next_response_id=2",
            "2026-05-20 10:00:02 Google Live live_transcript_recv chars=12 source=user",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["interrupts_initiated"], 2)
        self.assertEqual(p1["transcripts_received"], 1)
        self.assertAlmostEqual(p1["transcript_loss_rate"], 0.5)

    def test_p1_zero_loss_rate_when_no_interrupts(self):
        path = self._make_log(["2026-05-20 10:00:00 nothing"])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertIsNone(p1["transcript_loss_rate"])

    def test_p1_buffer_appends_and_replay_skipped(self):
        path = self._make_log([
            "2026-05-20 10:00:00 user_speech_pending_replay frames=10 bytes=3200",
            "2026-05-20 10:00:01 user_speech_pending_replay frames=5 bytes=1600",
            "2026-05-20 10:00:02 replay_skipped reason=response_id_mismatch",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["buffer_appends"], 2)
        self.assertEqual(p1["replay_skipped_by_reason"], {"response_id_mismatch": 1})

    def test_p1_capture_finalized_tracks_zero_frames(self):
        path = self._make_log([
            "2026-05-20 10:00:00 interrupt_capture_finalized frames=0 duration_ms=300",
            "2026-05-20 10:00:01 interrupt_capture_finalized frames=8 duration_ms=400",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["capture_finalized_count"], 2)
        self.assertEqual(p1["capture_finalized_with_zero_frames"], 1)

    def test_p1_ignores_transcript_with_zero_chars(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live live_transcript_recv chars=0 source=user",
            "2026-05-20 10:00:01 Google Live live_transcript_recv chars=5 source=user",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["transcripts_received"], 1)

    def test_p2_stop_latency_computed_between_interrupt_and_tts_stop(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
            "2026-05-20 10:00:01 Google Live tts_state_stop_sent",
        ])
        p2 = summarize_pains(path)["P2_stop_latency"]
        stats = p2["interrupt_to_tts_stop_sent_ms"]
        self.assertEqual(stats["count"], 1)
        self.assertAlmostEqual(stats["mean"], 1000.0)

    def test_p2_no_latency_when_no_matching_pair(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live tts_state_stop_sent",
        ])
        p2 = summarize_pains(path)["P2_stop_latency"]
        self.assertEqual(p2["interrupt_to_tts_stop_sent_ms"]["count"], 0)

    def test_p3_stale_chunks_counted(self):
        path = self._make_log([
            "2026-05-20 10:00:00 model_output_chunk_dropped reason=stale_response_id old=1 current=2",
            "2026-05-20 10:00:01 model_output_chunk_dropped reason=stale_response_id old=1 current=3",
            "2026-05-20 10:00:02 model_output_unblock_trigger source=audio_end",
        ])
        p3 = summarize_pains(path)["P3_response_overlap"]
        self.assertEqual(p3["stale_chunks_dropped"], 2)
        self.assertEqual(p3["model_output_unblock_triggers"], {"audio_end": 1})

    def test_p4_tool_dispatches_grouped_by_name(self):
        path = self._make_log([
            "2026-05-20 10:00:00 tool_call_dispatched name=get_weather response_id=1",
            "2026-05-20 10:00:01 tool_call_dispatched name=get_weather response_id=2",
            "2026-05-20 10:00:02 tool_call_dispatched name=play_music response_id=3",
        ])
        p4 = summarize_pains(path)["P4_function_calls"]
        self.assertEqual(p4["tool_call_dispatched_count"], 3)
        self.assertEqual(p4["by_name"], {"get_weather": 2, "play_music": 1})

    def test_p5_music_pause_grouped_by_trigger(self):
        path = self._make_log([
            "2026-05-20 10:00:00 music_auto_paused trigger=user_interrupt",
            "2026-05-20 10:00:01 music_auto_paused trigger=user_interrupt",
            "2026-05-20 10:00:02 music_auto_paused trigger=barge_in",
        ])
        p5 = summarize_pains(path)["P5_music_ducking"]
        self.assertEqual(p5["music_auto_pause_count"], 3)
        self.assertEqual(p5["by_trigger"], {"user_interrupt": 2, "barge_in": 1})

    def test_empty_log_returns_zeros(self):
        path = self._make_log([])
        result = summarize_pains(path)
        self.assertEqual(result["P1_user_speech_lost"]["interrupts_initiated"], 0)
        self.assertEqual(result["P3_response_overlap"]["stale_chunks_dropped"], 0)
        self.assertEqual(result["P4_function_calls"]["tool_call_dispatched_count"], 0)
        self.assertEqual(result["P5_music_ducking"]["music_auto_pause_count"], 0)


if __name__ == "__main__":
    unittest.main()
