import importlib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


class PhysicalSmokeAuditTest(unittest.TestCase):
    def test_cli_production_output_safe_strict_accepts_suppressed_echo_and_post_lesson_response(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:02[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:04[GoogleLive]-INFO-Google Live turn_latency_ms=620.0 phase=first_audio_out
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:06[GoogleLive]-INFO-Google Live echo_suppressed reason=robot_speaking bytes=1920 rms=2600
260518 20:10:07[GoogleLive]-INFO-Google Live waiting_model_timeout released_without_audio timeout_sec=4.0
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=4
260518 20:10:09[GoogleLive]-INFO-Google Live transcript source=user chars=24 text='con nói sau bài học'
260518 20:10:10[GoogleLive]-INFO-Google Live turn_latency_ms=900.0 phase=first_audio_out
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/physical_smoke_audit.py",
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-output-safe-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result = json.loads(proc.stdout)
        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["fatal_hits"], [])
        self.assertEqual(result["post_lesson_response_chains"], 1)

    def test_cli_production_output_safe_strict_rejects_aec_forward_and_listen_start_interrupt(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:02[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:04[GoogleLive]-INFO-Google Live turn_latency_ms=620.0 phase=first_audio_out
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:06[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:07[GoogleLive]-INFO-Google Live user_interrupted reason=listen_start cancelled_response_id=1 next_response_id=2
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=4
260518 20:10:09[GoogleLive]-INFO-Google Live transcript source=user chars=24 text='con nói sau bài học'
260518 20:10:10[GoogleLive]-INFO-Google Live turn_latency_ms=900.0 phase=first_audio_out
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/physical_smoke_audit.py",
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-output-safe-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr + proc.stdout)
        result = json.loads(proc.stdout)
        self.assertIn("aec_live_vad_forward<=0", result["missing"])
        self.assertIn("listen_start_interrupts<=0", result["missing"])

    def test_cli_production_voice_strict_requires_aec_forward_and_first_audio_budget(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("aec_live_vad_forward", result["missing"])
        self.assertIn("first_audio_out_ms", result["missing"])

    def test_cli_production_voice_strict_requires_expected_user_transcript(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 2)
        self.assertIn(
            "--production-voice-strict requires --expected-user-transcript",
            proc.stderr,
        )

    def test_cli_production_voice_strict_requires_live_server_interruption(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:05[GoogleLive]-INFO-Google Live interruption output_age_ms=n/a
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("live_server_interruption", result["missing"])

    def test_cli_production_voice_strict_requires_live_interruption_per_cycle(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:05[GoogleLive]-INFO-Google Live interruption output_age_ms=420.5
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("live_server_interruptions>=10", result["missing"])

    def test_cli_production_voice_strict_accepts_turn_latency_first_audio_marker(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='xin chào'
260518 20:10:03[GoogleLive]-INFO-Google Live turn_latency_ms=900.0 phase=first_audio_out
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:05[GoogleLive]-INFO-Google Live interruption output_age_ms=420.5
260518 20:10:05[GoogleLive]-INFO-Google Live tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime
260518 20:10:05[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=2.0
260518 20:10:06[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
260518 20:10:07[GoogleLive]-INFO-Google Live tts_stop_sent continue_listening=true listen_mode=realtime
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                    "--min-interrupts",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["first_audio_out_ms"]["max"], 900.0)

    def test_cli_production_voice_strict_prefers_turn_latency_over_session_start_latency(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='xin chào'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=9710.4
260518 20:10:03[GoogleLive]-INFO-Google Live turn_latency_ms=578.3 phase=first_audio_out
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:05[GoogleLive]-INFO-Google Live interruption output_age_ms=420.5
260518 20:10:05[GoogleLive]-INFO-Google Live tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime
260518 20:10:05[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=2.0
260518 20:10:06[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
260518 20:10:07[GoogleLive]-INFO-Google Live tts_stop_sent continue_listening=true listen_mode=realtime
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                    "--min-interrupts",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["first_audio_out_ms"]["max"], 578.3)

    def test_cli_production_voice_strict_requires_aec_forward_per_cycle(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("aec_live_vad_forward>=10", result["missing"])

    def test_cli_production_voice_strict_requires_tts_stop_per_interrupt_cycle(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_tts_stops>=10", result["missing"])

    def test_cli_production_voice_strict_does_not_count_normal_stops_as_interrupt_stops(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1),
                )
            )
            for i in range(10)
        ) + """
260518 20:15:00[GoogleLive]-INFO-Google Live transcript source=user chars=18 text='con muốn hỏi tiếp'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["interrupt_tts_stops"], 0)
        self.assertIn("interrupt_tts_stops>=10", result["missing"])

    def test_cli_production_voice_strict_requires_realtime_relisten_after_output(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("realtime_tts_stops>=1", result["missing"])

    def test_cli_production_voice_strict_requires_output_to_relisten_chain(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:04[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("output_relisten_chains>=1", result["missing"])

    def test_cli_production_voice_strict_requires_ordered_interrupt_stop_chain(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_stop_chains>=10", result["missing"])

    def test_cli_production_voice_strict_local_audio_interrupt_does_not_replace_live_relisten(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_relisten_chains>=10", result["missing"])
        self.assertIn("post_interrupt_user_transcripts>=1", result["missing"])

    def test_cli_production_voice_strict_requires_interrupt_stop_relisten_fields(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_relisten_chains>=10", result["missing"])

    def test_cli_production_voice_strict_requires_ordered_aec_interruption_chain(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("aec_interruption_chains>=10", result["missing"])

    def test_cli_production_voice_strict_accepts_live_interrupt_without_local_audio_interrupt(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["passed"])
        self.assertEqual(result["audio_interrupts"], 0)
        self.assertEqual(result["live_server_interruption"], 10)

    def test_cli_production_child_safety_strict_requires_output_moderation_proof(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                    "--production-child-safety-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("output_moderation_blocks>=1", result["missing"])
        self.assertIn("safe_deflection_live_text>=1", result["missing"])

    def test_cli_production_child_safety_strict_accepts_moderation_block_and_deflection(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:05[GoogleLive]-WARNING-Google Live output_moderation_blocked source=model_output
260518 20:10:06[GoogleLive]-INFO-Google Live safe_deflection sent via live text chars=72
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                    "--production-child-safety-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["passed"])
        self.assertEqual(result["output_moderation_blocks"], 1)
        self.assertEqual(result["safe_deflection_live_text"], 1)

    def test_cli_refuses_child_live_moderation_proof_when_strict_evidence_missing(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            proof_path = Path(tmp) / "child_live_moderation_proof.md"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-child-safety-strict",
                    "--child-live-moderation-proof",
                    str(proof_path),
                    "--proof-backend-device-uuid",
                    "redacted-device-uuid",
                    "--proof-board-mac",
                    "redacted-board-mac",
                    "--proof-captured-at",
                    "2026-05-18T20:10:00Z",
                    "--proof-deployed-robot-server-image",
                    "redacted-image-digest",
                    "--proof-hardware-sample-cp7-safety-run",
                    "true",
                    "--proof-image-reference-type",
                    "redacted-digest",
                    "--proof-lesson-flow-completed",
                    "true",
                    "--proof-llm-judge-checked",
                    "true",
                    "--proof-output-classifier-checked",
                    "true",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertFalse(proof_path.exists())
            result = json.loads(proc.stdout)
            self.assertIn("output_moderation_blocks>=1", result["missing"])
            self.assertIn("safe_deflection_live_text>=1", result["missing"])

    def test_cli_refuses_child_live_moderation_proof_when_metadata_missing(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:05[GoogleLive]-WARNING-Google Live output_moderation_blocked source=model_output
260518 20:10:06[GoogleLive]-INFO-Google Live safe_deflection sent via live text chars=72
""" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            proof_path = Path(tmp) / "child_live_moderation_proof.md"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-child-safety-strict",
                    "--child-live-moderation-proof",
                    str(proof_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 2)
            self.assertFalse(proof_path.exists())
            self.assertIn("--proof-backend-device-uuid", proc.stderr)

    def test_cli_writes_child_live_moderation_proof_with_exact_required_keys(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:05[GoogleLive]-WARNING-Google Live output_moderation_blocked source=model_output
260518 20:10:06[GoogleLive]-INFO-Google Live safe_deflection sent via live text chars=72
""" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            proof_path = Path(tmp) / "child_live_moderation_proof.md"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-child-safety-strict",
                    "--child-live-moderation-proof",
                    str(proof_path),
                    "--proof-backend-device-uuid",
                    "redacted-device-uuid",
                    "--proof-board-mac",
                    "redacted-board-mac",
                    "--proof-captured-at",
                    "2026-05-18T20:10:00Z",
                    "--proof-deployed-robot-server-image",
                    "redacted-image-digest",
                    "--proof-hardware-sample-cp7-safety-run",
                    "true",
                    "--proof-image-reference-type",
                    "redacted-digest",
                    "--proof-lesson-flow-completed",
                    "true",
                    "--proof-llm-judge-checked",
                    "true",
                    "--proof-output-classifier-checked",
                    "true",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            proof_lines = proof_path.read_text(encoding="utf-8").splitlines()
            keys = [line.split(":", 1)[0] for line in proof_lines if ":" in line]
            self.assertEqual(
                keys,
                [
                    "backend_device_uuid",
                    "board_mac",
                    "captured_at",
                    "deployed_robot_server_image",
                    "hardware_sample_cp7_safety_run",
                    "image_reference_type",
                    "lesson_flow_completed",
                    "llm_judge_checked",
                    "output_classifier_checked",
                    "redacted",
                    "unsafe_output_blocked",
                ],
            )
            proof_text = "\n".join(proof_lines)
            self.assertIn("redacted: true", proof_text)
            self.assertIn("unsafe_output_blocked: true", proof_text)
            self.assertNotIn("bắt đầu bài học", proof_text)
            self.assertNotIn("safe_deflection sent", proof_text)

    def test_cli_production_voice_strict_requires_fast_interrupt_stop_latency(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=900.0".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_stop_latency_ms<=250", result["missing"])

    def test_cli_production_voice_strict_rejects_aec_bypass_logs(self):
        for bad_line, fatal_hit in (
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live AEC import failed, running without AEC: missing",
                "Google Live AEC import failed",
            ),
            (
                "260518 20:10:00[GoogleLive]-INFO-Google Live AEC initialised sample_rate=16000 filter_ms=200 frame_ms=10 bypassed=True reason=no_speex",
                "aec_bypassed",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live AEC process_mic failed while output active, dropping input chunk: aec failed",
                "Google Live AEC process_mic failed while output active",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live AEC reference resample failed: bad rate",
                "Google Live AEC reference resample failed",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live AEC push_reference failed: buffer error",
                "Google Live AEC push_reference failed",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live AEC process_mic failed, dropping AEC for this chunk: aec failed",
                "Google Live AEC process_mic failed, dropping AEC for this chunk",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live dropped invalid input audio bytes=1",
                "Google Live dropped invalid input audio",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live dropped corrupt input opus encoded_bytes=80 source_rate=16000 target_rate=16000 error_type=OpusError",
                "Google Live dropped corrupt input opus",
            ),
        ):
            with self.subTest(fatal_hit=fatal_hit):
                log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
{bad_line}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
                    "\n".join(
                        (
                            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                        )
                    )
                    for i in range(10)
                ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
                with tempfile.TemporaryDirectory() as tmp:
                    log_path = Path(tmp) / "server.log"
                    log_path.write_text(log_text, encoding="utf-8")

                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(Path("scripts/physical_smoke_audit.py")),
                            str(log_path),
                            "--device-id",
                            "3c:0f:02:de:c2:e0",
                            "--client-id",
                            "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                            "--expected-user-transcript",
                            "dừng lại",
                            "--production-voice-strict",
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        text=True,
                        capture_output=True,
                    )

                self.assertEqual(proc.returncode, 1, proc.stderr)
                result = json.loads(proc.stdout)
                self.assertIn(fatal_hit, result["fatal_hits"])
                self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_live_hang_markers(self):
        for bad_line, fatal_hit in (
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live receive timed out",
                "Google Live receive timed out",
            ),
            (
                "260518 20:10:00[GoogleLive]-INFO-Google Live waiting_model_timeout released_without_audio timeout_sec=2.0",
                "Google Live waiting_model_timeout",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live lesson_prompt_output_guard_timeout timeout_sec=15.0",
                "Google Live lesson_prompt_output_guard_timeout",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live lesson_prompt_playback_guard_timeout timeout_sec=12.0 queue_len=1",
                "Google Live lesson_prompt_playback_guard_timeout",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live reconnect attempt 1 after runtime failure: Google Live receive timed out",
                "Google Live reconnect attempt",
            ),
            (
                "260518 20:10:00[GoogleLive]-INFO-reconnect_started reason=timeout attempt=1 state=RECONNECTING",
                "reconnect_started",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live tool timeout name=start_lesson id=call-1 timeout_ms=10000",
                "Google Live tool timeout",
            ),
            (
                "260518 20:10:00[GoogleLive]-WARNING-Google Live runtime failure type=timeout: receive loop stopped",
                "Google Live runtime failure type=",
            ),
            (
                "260518 20:10:00[GoogleLive]-ERROR-Google Live unavailable type=auth: bad api key",
                "Google Live unavailable type=",
            ),
            (
                "260518 20:10:00[lesson]-ERROR-STEP_TIMEOUT step=s1 seq=1",
                "STEP_TIMEOUT",
            ),
        ):
            with self.subTest(fatal_hit=fatal_hit):
                log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
{bad_line}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
                    "\n".join(
                        (
                            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                        )
                    )
                    for i in range(10)
                ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
                with tempfile.TemporaryDirectory() as tmp:
                    log_path = Path(tmp) / "server.log"
                    log_path.write_text(log_text, encoding="utf-8")

                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(Path("scripts/physical_smoke_audit.py")),
                            str(log_path),
                            "--device-id",
                            "3c:0f:02:de:c2:e0",
                            "--client-id",
                            "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                            "--expected-user-transcript",
                            "dừng lại",
                            "--production-voice-strict",
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        text=True,
                        capture_output=True,
                    )

                self.assertEqual(proc.returncode, 1, proc.stderr)
                result = json.loads(proc.stdout)
                self.assertIn(fatal_hit, result["fatal_hits"])
                self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_requires_live_identity_marker(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("live_identity", result["missing"])

    def test_cli_production_voice_strict_requires_live_identity_before_first_audio(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "dừng lại",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("live_identity_before_first_audio", result["missing"])

    def test_cli_production_voice_strict_rejects_any_bad_live_identity(self):
        for bad_identity in (
            "Google Live session identity model=gemini-3.1-flash-live-preview voice=Puck language=vi-VN",
            "Google Live session identity model=gemini-2.5-flash-live-preview voice=Kore language=vi-VN",
            "Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=en-US",
        ):
            with self.subTest(bad_identity=bad_identity):
                log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:00[GoogleLive]-INFO-Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:05[GoogleLive]-INFO-{bad_identity}
""" + "\n".join(
                    "\n".join(
                        (
                            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                            "260518 20:13:{:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=25.0".format(i),
                        )
                    )
                    for i in range(10)
                ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
                with tempfile.TemporaryDirectory() as tmp:
                    log_path = Path(tmp) / "server.log"
                    log_path.write_text(log_text, encoding="utf-8")

                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(Path("scripts/physical_smoke_audit.py")),
                            str(log_path),
                            "--device-id",
                            "3c:0f:02:de:c2:e0",
                            "--client-id",
                            "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                            "--expected-user-transcript",
                            "dừng lại",
                            "--production-voice-strict",
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        text=True,
                        capture_output=True,
                    )

                self.assertEqual(proc.returncode, 1, proc.stderr)
                result = json.loads(proc.stdout)
                self.assertIn("live_identity_mismatch", result["fatal_hits"])
                self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_requires_expected_transcript_after_interrupt(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("post_interrupt_user_transcript_expected_match>=1", result["missing"])

    def test_cli_production_voice_strict_requires_user_transcript_after_interrupt(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1),
                )
            )
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("post_interrupt_user_transcripts>=1", result["missing"])

    def test_audit_ignores_evidence_after_non_target_connection(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[core.connection]-INFO-127.0.0.1 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'synthetic-client', 'user-agent': 'Python/3.11 websockets/14.2'}
260518 20:10:02[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:04[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:05[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:06[GoogleLive]-INFO-Google Live interruption output_age_ms=420
260518 20:10:07[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id=1 next_response_id=2
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=1,
            expected_user_transcripts=["bắt đầu bài học"],
            require_aec_live_vad_forward=True,
            require_live_server_interruption=True,
            min_live_server_interruptions=1,
            max_first_audio_ms=1800,
        )

        self.assertFalse(result["passed"])
        self.assertTrue(result["physical_ws_connected"])
        self.assertEqual(result["input_audio_diag"], 0)
        self.assertIn("input_audio_diag", result["missing"])
        self.assertIn("user_transcript", result["missing"])

    def test_audit_stops_target_segment_at_target_disconnect(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[core.connection]-INFO-Client disconnected device_id=3c:0f:02:de:c2:e0 client_ip=192.168.0.50
260518 20:10:02[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:04[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:05[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
260518 20:10:06[GoogleLive]-INFO-Google Live interruption output_age_ms=420
260518 20:10:07[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id=1 next_response_id=2
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=1,
            expected_user_transcripts=["bắt đầu bài học"],
            require_aec_live_vad_forward=True,
            require_live_server_interruption=True,
            min_live_server_interruptions=1,
            max_first_audio_ms=1800,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["input_audio_diag"], 0)
        self.assertIn("Client disconnected", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])
        self.assertIn("input_audio_diag", result["missing"])

    def test_cli_production_strict_enables_voice_and_course_gates(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
"""
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "type": "listen",
                    "completionClass": "interactive",
                    "prompt": "Con hãy nói rõ từ barn với TeeBot.",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "server.log"
            manifest_path = tmp_path / "lesson.json"
            log_path.write_text(log_text, encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-strict",
                    "--lesson-manifest",
                    str(manifest_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("first_audio_out_ms", result["missing"])
        self.assertIn("aec_live_vad_forward", result["missing"])
        self.assertIn("lesson_prompt_live_text>=1", result["missing"])
        self.assertIn("lesson_prompt_live_text_hashes>=1", result["missing"])

    def test_cli_production_strict_requires_lesson_manifest_with_alias_message(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 2)
        self.assertIn(
            "--production-strict requires --lesson-manifest",
            proc.stderr,
        )

    def test_cli_production_course_strict_requires_manifest_live_text_hashes(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
"""
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "type": "listen",
                    "completionClass": "interactive",
                    "prompt": "Con hãy nói rõ từ barn với TeeBot.",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "server.log"
            manifest_path = tmp_path / "lesson.json"
            log_path.write_text(log_text, encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-course-strict",
                    "--lesson-manifest",
                    str(manifest_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("lesson_start", result["missing"])
        self.assertIn("lesson_prompt_live_text>=1", result["missing"])
        self.assertIn("lesson_prompt_live_text_hashes>=1", result["missing"])

    def test_audit_passes_for_physical_audio_interrupt_smoke(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.6'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["audio_interrupts"], 10)
        self.assertEqual(result["input_audio_diag"], 1)
        self.assertEqual(result["user_transcripts"], 1)

    def test_audit_rejects_local_python_soak_as_physical_evidence(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:03:08[core.connection]-INFO-127.0.0.1 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'Python/3.11 websockets/14.2'}
260518 20:03:09[GoogleLive]-INFO-Google Live user_interrupted reason=text_input cancelled_response_id=0 next_response_id=1
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("physical_ws_connected", result["missing"])
        self.assertIn("user_transcript", result["missing"])
        self.assertIn("audio_interrupts>=10", result["missing"])

    def test_audit_rejects_server_ip_even_with_firmware_like_user_agent(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.114 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            server_ip="192.168.0.114",
        )

        self.assertFalse(result["passed"])
        self.assertIn("physical_ws_connected", result["missing"])

    def test_audit_does_not_accept_audio_without_user_transcript(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("user_transcript", result["missing"])

    def test_audit_matches_expected_user_transcript_text(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=28 text='Con muốn BẮT ĐẦU BÀI HỌC, nhé.'
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            expected_user_transcripts=["bắt đầu bài học"],
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["expected_user_transcripts"], 1)
        self.assertEqual(result["user_transcript_expected_matches"], 1)

    def test_audit_requires_post_lesson_transcript_to_get_first_audio(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[LessonRuntime]-INFO-lesson_completed stepsCompleted=4
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=24 text='bạn nghe thấy con không'
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=0,
            min_audio_interrupts=0,
            expected_post_lesson_transcripts=["bạn nghe thấy con không"],
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["post_lesson_response_chains"], 0)
        self.assertIn("post_lesson_response", result["missing"])

    def test_audit_accepts_post_lesson_transcript_followed_by_first_audio(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[LessonRuntime]-INFO-lesson_completed stepsCompleted=4
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=user chars=24 text='bạn nghe thấy con không'
260518 20:10:04[GoogleLive]-INFO-Google Live turn_latency_ms=720.0 phase=first_audio_out
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=0,
            min_audio_interrupts=0,
            expected_post_lesson_transcripts=["bạn nghe thấy con không"],
        )

        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["post_lesson_response_chains"], 1)

    def test_audit_rejects_expected_user_transcript_when_log_has_only_chars(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            expected_user_transcripts=["bắt đầu bài học"],
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["user_transcripts"], 1)
        self.assertEqual(result["user_transcript_expected_matches"], 0)
        self.assertIn("user_transcript_expected_match>=1", result["missing"])

    def test_audit_rejects_wrong_expected_user_transcript_text(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=15 text='mở nhạc cho con'
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            expected_user_transcripts=["bắt đầu bài học"],
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["user_transcript_expected_matches"], 0)
        self.assertIn("user_transcript_expected_match>=1", result["missing"])


    def test_audit_requires_aec_live_vad_forward_when_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
260518 20:10:02[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_aec_live_vad_forward=True,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["aec_live_vad_forward"], 1)

    def test_audit_rejects_missing_aec_live_vad_forward_when_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_aec_live_vad_forward=True,
        )

        self.assertFalse(result["passed"])
        self.assertIn("aec_live_vad_forward", result["missing"])

    def test_audit_rejects_slow_first_audio_when_budget_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
260518 20:10:02[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=2200.0
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            max_first_audio_ms=1800,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["first_audio_out_ms"]["max"], 2200.0)
        self.assertIn("first_audio_out_ms<=1800", result["missing"])

    def test_audit_requires_lesson_prompts_via_live_text_when_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='Xin chào.'
260518 20:10:07[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            require_lesson_live_text=True,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text"], 0)
        self.assertIn("lesson_prompt_live_text>=1", result["missing"])

    def test_audit_rejects_queued_only_lesson_live_text_when_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        prompt = "Can you say barn with TeeBot?"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via live text chars={len(prompt)} sha256={prompt_hash}
260518 20:10:07[GoogleLive]-INFO-Google Live user_audio_window_open reason=lesson_child_response window_ms=25000
260518 20:10:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:10:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:10:10[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:11[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
            require_lesson_live_text=True,
            lesson_manifest={
                "steps": [
                    {
                        "id": "s1",
                        "type": "listen",
                        "completionClass": "interactive",
                        "prompt": prompt,
                    }
                ]
            },
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text"], 0)
        self.assertIn("lesson_prompt_live_text>=1", result["missing"])
        self.assertIn("lesson_prompt_live_text_hashes>=1", result["missing"])

    def test_audit_rejects_local_tts_even_when_live_text_was_sent(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='Xin chào.'
260518 20:10:07[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars=8 sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
260518 20:10:08[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:09[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            require_lesson_live_text=True,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_local_tts"], 1)
        self.assertIn("no_lesson_local_tts", result["missing"])

    def test_audit_rejects_lesson_runtime_local_tts_even_when_live_text_was_sent(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[LessonRuntime]-INFO-lesson_step_prompt queued via tts stepId=s1 text='Xin chào.'
260518 20:10:07[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars=8 sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
260518 20:10:08[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:09[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            require_lesson_live_text=True,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_local_tts"], 1)
        self.assertIn("no_lesson_local_tts", result["missing"])

    def test_audit_rejects_local_tts_lesson_ack_even_when_step_live_text_was_sent(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:04[GoogleLive]-INFO-Google Live lesson_start_ack queued via tts text='Bắt đầu bài học nhé.'
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:07[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars=8 sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
260518 20:10:08[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:09[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            require_lesson_live_text=True,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_local_tts"], 1)
        self.assertIn("no_lesson_local_tts", result["missing"])

    def test_audit_rejects_short_lesson_live_text_when_min_chars_requested(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars=8
260518 20:10:07[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            require_lesson_live_text=True,
            min_lesson_live_text_chars=20,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text_chars"], 8)
        self.assertIn("lesson_prompt_live_text_chars>=20", result["missing"])

    def test_audit_derives_lesson_expected_text_from_manifest(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "completionClass": "passive",
                    "prompt": "Xin chào con.",
                },
                {
                    "id": "s2",
                    "completionClass": "interactive",
                    "prompt": "Con nói theo mình: barn.",
                },
            ]
        }
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars=12
260518 20:10:07[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_manifest_steps"], 2)
        self.assertIn("lesson_steps>=2", result["missing"])
        self.assertIn("lesson_prompt_live_text>=2", result["missing"])
        self.assertIn(
            "lesson_prompt_live_text_chars>=37",
            result["missing"],
        )
        self.assertIn("interactive_child_response_windows>=1", result["missing"])

    def test_audit_rejects_lesson_live_text_hash_mismatch_from_manifest(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        prompt = "Xin chào con."
        wrong_hash = hashlib.sha256("Xin chào cô.".encode("utf-8")).hexdigest()
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "completionClass": "passive",
                    "prompt": prompt,
                },
            ]
        }
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text chars={len(prompt)} sha256={wrong_hash}
260518 20:10:07[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:08[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text_hash_matches"], 0)
        self.assertIn("lesson_prompt_live_text_hashes>=1", result["missing"])

    def test_audit_rejects_extra_lesson_live_text_hash_outside_manifest(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        prompt = "Xin chào con."
        extra_prompt = "AI tự thêm câu ngoài giáo án."
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        extra_hash = hashlib.sha256(extra_prompt.encode("utf-8")).hexdigest()
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "completionClass": "passive",
                    "prompt": prompt,
                },
            ]
        }
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s1 chars={len(prompt)} sha256={prompt_hash}
260518 20:10:07[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s1 chars={len(extra_prompt)} sha256={extra_hash}
260518 20:10:08[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:09[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text_hash_matches"], 1)
        self.assertEqual(result["lesson_prompt_live_text_unexpected_hashes"], 1)
        self.assertIn("no_unexpected_lesson_live_text_hashes", result["missing"])

    def test_audit_rejects_lesson_live_text_hashes_out_of_manifest_order(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        first_prompt = "Xin chào con."
        second_prompt = "Mình cùng nhìn chuồng ngựa nhé."
        first_hash = hashlib.sha256(first_prompt.encode("utf-8")).hexdigest()
        second_hash = hashlib.sha256(second_prompt.encode("utf-8")).hexdigest()
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "completionClass": "passive",
                    "prompt": first_prompt,
                },
                {
                    "id": "s2",
                    "completionClass": "passive",
                    "prompt": second_prompt,
                },
            ]
        }
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s1 chars={len(second_prompt)} sha256={second_hash}
260518 20:10:07[LessonRuntime]-INFO-emit lesson_step stepId=s2 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:07[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s2
260518 20:10:07[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s2
260518 20:10:07[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s2
260518 20:10:07[lesson_handler]-INFO-lesson_step rendered stepId=s2 passive=1 degraded=0
260518 20:10:08[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s2 chars={len(first_prompt)} sha256={first_hash}
260518 20:10:09[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:10[LessonRuntime]-INFO-lesson_completed stepsCompleted=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_live_text_hash_matches"], 2)
        self.assertEqual(result["lesson_prompt_live_text_hash_order_matches"], 1)
        self.assertIn("lesson_prompt_live_text_hash_order", result["missing"])

    def test_audit_derives_retry_and_success_text_from_manifest(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        prompt = "Con có thể nói barn không?"
        retry_prompt = "Con thử nói lại nhé: barn."
        success_prompt = "Giỏi lắm, con đã nói barn."
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "type": "listen",
                    "completionClass": "interactive",
                    "prompt": prompt,
                    "retryPrompt": retry_prompt,
                    "successPrompt": success_prompt,
                },
            ]
        }
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s1 chars={len(prompt)} sha256={prompt_hash} text='{prompt}'
260518 20:10:07[LessonRuntime]-INFO-child response window opened stepId=s1
260518 20:10:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:10:09[LessonRuntime]-INFO-lesson_progress step_completed stepId=s1
260518 20:10:10[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:11[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(12, 22)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_manifest_prompt_hashes"], 3)
        self.assertIn("lesson_prompt_live_text_hashes>=3", result["missing"])
        self.assertIn(
            f"lesson_prompt_live_text_chars>={len(prompt) + len(retry_prompt) + len(success_prompt)}",
            result["missing"],
        )

    def test_audit_uses_storybeat_ask_as_spoken_manifest_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        visual_prompt = "Look at the picture on the screen."
        spoken_prompt = "What animal do you see?"
        spoken_hash = hashlib.sha256(spoken_prompt.encode("utf-8")).hexdigest()
        manifest = {
            "steps": [
                {
                    "id": "s1",
                    "type": "listen",
                    "completionClass": "interactive",
                    "prompt": visual_prompt,
                    "storyBeat": {
                        "ask": spoken_prompt,
                        "waitForChild": True,
                    },
                    "vocab": {"promptKind": "guided-speaking"},
                },
            ]
        }
        log_text = f"""
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {{'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=lesson-1
260518 20:10:04[LessonRuntime]-INFO-emit lesson_start
260518 20:10:05[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:10:05[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:10:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_step_prompt sent via live text stepId=s1 chars={len(spoken_prompt)} sha256={spoken_hash} text='{spoken_prompt}'
260518 20:10:07[LessonRuntime]-INFO-child response window opened stepId=s1
260518 20:10:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:10:09[LessonRuntime]-INFO-lesson_progress step_completed stepId=s1
260518 20:10:10[LessonRuntime]-INFO-emit lesson_stop
260518 20:10:11[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(12, 22)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            require_lesson_live_text=True,
            lesson_manifest=manifest,
        )

        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["lesson_manifest_prompt_hashes"], 1)
        self.assertEqual(result["lesson_prompt_live_text_hash_matches"], 1)

    def test_audit_passes_for_full_physical_lesson_flow(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        step_lines = []
        for index, step_type in enumerate(
            [
                "greeting",
                "review",
                "focus",
                "model",
                "listen",
                "repeat",
                "fillBlank",
                "feedback",
                "celebrate",
            ],
            start=1,
        ):
            step_lines.extend(
                [
                    f"260518 20:11:{index:02d}[LessonRuntime]-INFO-emit lesson_step stepId=s{index} stepType={step_type} backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step rendered stepId=s{index} passive=0 degraded=0",
                    f"260518 20:11:{index:02d}[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='Can you say step {index} with TeeBot?'"
                    if step_type in {"model", "listen", "repeat", "fillBlank"}
                    else f"260518 20:11:{index:02d}[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='step {index}'",
                ]
            )
            if step_type in {"model", "listen", "repeat", "fillBlank"}:
                step_lines.extend(
                    [
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-child response window opened stepId=s{index} listening=true",
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-interactive child response accepted stepId=s{index} recognizedText=step{index}",
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s{index}",
                    ]
                )
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
""" + "\n".join(step_lines) + """
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=9
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=9,
            expected_interactive_steps=4,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["lesson_steps"], 9)
        self.assertEqual(result["lesson_prompt_tts"], 9)
        self.assertEqual(result["lesson_prompt_after_render"], 9)
        self.assertEqual(result["lesson_firmware_rendered"], 9)
        self.assertEqual(result["lesson_robot_overlays_drawn"], 9)
        self.assertEqual(result["lesson_step_layers_drawn_by_step"], 9)
        self.assertEqual(result["interactive_child_response_windows"], 4)
        self.assertEqual(result["interactive_child_responses"], 4)
        self.assertEqual(result["interactive_child_responses_observed"], 4)
        self.assertEqual(result["interactive_child_response_ordered"], 4)
        self.assertEqual(result["interactive_child_response_after_prompt"], 4)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 4)
        self.assertEqual(result["interactive_guided_prompts"], 4)
        self.assertEqual(result["interactive_child_response_before_progress"], 4)

    def test_audit_rejects_lesson_flow_missing_expected_interactive_child_turns(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("interactive_child_response_windows>=1", result["missing"])
        self.assertIn("interactive_child_responses>=1", result["missing"])

    def test_audit_rejects_child_response_before_child_response_window(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:07[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:08[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_ordered>=1", result["missing"])

    def test_audit_rejects_step_completed_before_child_response(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:09[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_before_progress>=1", result["missing"])

    def test_audit_rejects_child_response_before_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:07[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:08[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_after_prompt>=1", result["missing"])

    def test_audit_rejects_child_response_window_before_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:07[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 0)
        self.assertIn("interactive_child_response_window_after_prompt>=1", result["missing"])

    def test_audit_accepts_google_live_child_response_audio_window_log(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[GoogleLive]-INFO-Google Live user_audio_window_open reason=lesson_child_response window_ms=25000
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_response_ordered"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 1)

    def test_audit_accepts_google_live_step_prompt_queued_via_live_text(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via live text='Can you say barn with TeeBot?'
260518 20:11:07[GoogleLive]-INFO-Google Live user_audio_window_open reason=lesson_child_response window_ms=25000
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["lesson_prompt_tts"], 1)
        self.assertEqual(result["lesson_prompt_after_render"], 1)
        self.assertEqual(result["interactive_guided_prompts"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 1)

    def test_audit_rejects_command_only_interactive_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Say barn with TeeBot.'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_guided_prompts"], 0)
        self.assertIn("interactive_guided_prompts>=1", result["missing"])

    def test_audit_rejects_immediate_pronunciation_correction(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_step_prompt queued via tts stepId=s1 text='Con phát âm chưa chuẩn, mình nói lại nhé.'
260518 20:11:10[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["immediate_pronunciation_scoring"], 1)
        self.assertIn("no_immediate_pronunciation_scoring", result["missing"])

    def test_audit_rejects_lesson_image_decode_or_fetch_failures_even_with_draw_lines(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[Lesson]-WARN-backgroundScene.poster: JPEG decode failed: 257
260518 20:11:03[Lesson]-WARN-lesson_step poster fetch failed; caption-only fallback
260518 20:11:04[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:06[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:07[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:08[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:09[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:10[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:11[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_google_live_fallback_disabled(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-WARN-Google Live fallback_disabled reason=quota exceeded 429
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("fallback_disabled", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_robot_echo_bypass_or_disabled_live_interrupts(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live echo_bypass reason=robot_speaking bytes=1920 rms=2600
260518 20:10:04[GoogleLive]-WARN-Google Live server interruption ignored by config
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("Google Live echo_bypass", result["fatal_hits"])
        self.assertIn(
            "Google Live server interruption ignored by config",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_local_loud_input_interrupt(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
260518 20:10:05[GoogleLive]-INFO-interrupt_started reason=loud_input state=INTERRUPTING turn_id=1 response_id=1
260518 20:10:06[GoogleLive]-INFO-Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1
""" + "\n".join(
            "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i)
            for i in range(10)
        ) + "\n" + "\n".join(
            "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("interrupt_started reason=loud_input", result["fatal_hits"])
        self.assertIn(
            "Google Live user_interrupted reason=loud_input",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_local_tts_voice_segment(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[sendAudio]-INFO-Send first voice segment: Xin chào.
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1),
                )
            )
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("Send first voice segment:", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_local_tts_audio_message(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[sendAudio]-INFO-Send audio message: SentenceType.FIRST, Xin chào.
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("Send audio message:", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_local_tts_sentence_start_frame(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[core.websocket]-INFO-send {"type":"tts","state":"sentence_start","text":"Xin chào.","session_id":"session-1"}
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn('"state":"sentence_start"', result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_spaced_local_tts_sentence_start_frame(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[core.websocket]-INFO-send {"type" : "tts", "state" : "sentence_start", "text" : "Xin chào."}
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='dừng lại'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("tts sentence_start", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_robot_speaking_mic_suppression_or_delayed_live_interrupt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live echo_suppressed reason=robot_speaking bytes=1920 rms=2600
260518 20:10:04[GoogleLive]-INFO-Google Live interruption suppressed_for_age output_age_ms=40 threshold_ms=200
260518 20:10:05[GoogleLive]-INFO-Google Live transcript_barge_in suppressed_for_age output_age_ms=40 threshold_ms=200
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "Google Live echo_suppressed reason=robot_speaking",
            result["fatal_hits"],
        )
        self.assertIn(
            "Google Live interruption suppressed_for_age",
            result["fatal_hits"],
        )
        self.assertIn(
            "Google Live transcript_barge_in suppressed_for_age",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_model_echo_transcript(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:04[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                    "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1),
                )
            )
            for i in range(10)
        ) + """
260518 20:15:00[GoogleLive]-INFO-Google Live transcript source=user chars=18 text='robot đang nói tiếp'
260518 20:15:01[GoogleLive]-INFO-Google Live transcript_barge_in suppressed_as_model_echo chars=18 text_preview='robot đang nói tiếp'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "bắt đầu bài học",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn(
            "Google Live transcript_barge_in suppressed_as_model_echo",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_user_transcript_matching_model_transcript(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=model chars=18 text='robot đang nói tiếp'
260518 20:10:04[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=18 text='robot đang nói tiếp'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "robot đang nói tiếp",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("model_echo_user_transcript", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_cli_production_voice_strict_rejects_long_user_transcript_inside_model_transcript(self):
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live transcript source=model chars=43 text='robot đang nói tiếp nội dung dài cho con nghe'
260518 20:10:04[GoogleLive]-INFO-Google Live first_audio_out_latency_ms=900
260518 20:10:05[GoogleLive]-INFO-tts_stop_sent continue_listening=true listen_mode=realtime
""" + "\n".join(
            "\n".join(
                (
                    "260518 20:11:{:02d}[GoogleLive]-INFO-Google Live aec_live_vad_forward reason=robot_speaking bytes=640 rms=300".format(i),
                    "260518 20:12:{:02d}[GoogleLive]-INFO-Google Live interruption output_age_ms=420".format(i),
                    "260518 20:13:{:02d}[GoogleLive]-INFO-tts_stop_sent reason=interrupt continue_listening=true listen_mode=realtime".format(i),
                )
            )
            for i in range(10)
        ) + """
260518 20:14:00[GoogleLive]-INFO-Google Live transcript source=user chars=25 text='nói tiếp nội dung dài'
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(log_text, encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts/physical_smoke_audit.py")),
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--expected-user-transcript",
                    "nói tiếp nội dung dài",
                    "--production-voice-strict",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 1, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("model_echo_user_transcript", result["fatal_hits"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_model_echo_detector_ignores_short_user_prompt_fragment(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        lines = [
            "260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=model chars=5 text=' nghe'",
            "260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=4 text='nghe'",
        ]

        self.assertEqual(audit._model_echo_user_transcript_count(lines), 0)

    def test_audit_rejects_robot_speaking_audio_decision_suppression(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-audio_decision decision=suppress_echo reason=robot_speaking state=MODEL_SPEAKING turn_id=0 response_id=0 audio_seq=275 bytes=1920 rms=2600
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "audio_decision decision=suppress_echo reason=robot_speaking",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_robot_speaking_audio_decision_drop_or_hold(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-audio_decision decision=drop_input reason=output_active state=MODEL_SPEAKING turn_id=0 response_id=0 audio_seq=275 bytes=1920 rms=2600
260518 20:10:04[GoogleLive]-INFO-audio_decision decision=hold_interrupt_audio reason=blocked_output state=MODEL_SPEAKING turn_id=0 response_id=0 audio_seq=276 bytes=1920 rms=2600
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "audio_decision decision=drop_input reason=output_active",
            result["fatal_hits"],
        )
        self.assertIn(
            "audio_decision decision=hold_interrupt_audio reason=blocked_output",
            result["fatal_hits"],
        )
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_child_response_without_observable_input(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_responses_observed"], 0)
        self.assertIn("interactive_child_responses_observed>=1", result["missing"])

    def test_audit_rejects_contradictory_child_response_evidence(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn accepted=false confidence=0
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_responses_observed"], 0)
        self.assertIn("interactive_child_responses_observed>=1", result["missing"])

    def test_audit_rejects_child_response_on_passive_step(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='hello'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=hello
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:12:01[LessonRuntime]-INFO-emit lesson_step stepId=s2 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:12:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s2
260518 20:12:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s2
260518 20:12:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s2
260518 20:12:05[lesson_handler]-INFO-lesson_step rendered stepId=s2 passive=0 degraded=0
260518 20:12:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s2 text='Can you say barn with TeeBot?'
260518 20:12:07[LessonRuntime]-INFO-child response window opened stepId=s2 listening=true
260518 20:12:08[LessonRuntime]-INFO-interactive child response accepted stepId=s2 recognizedText=barn
260518 20:12:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s2
260518 20:12:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:13:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=2,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["passive_child_response_activity"], 2)
        self.assertIn("no_passive_child_response_activity", result["missing"])

    def test_audit_rejects_layer_draws_not_tied_to_each_step_id(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='hello'
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:12:01[LessonRuntime]-INFO-emit lesson_step stepId=s2 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:12:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:12:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:12:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:12:05[lesson_handler]-INFO-lesson_step rendered stepId=s2 passive=0 degraded=0
260518 20:12:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s2 text='Can you say barn with TeeBot?'
260518 20:12:07[LessonRuntime]-INFO-child response window opened stepId=s2 listening=true
260518 20:12:08[LessonRuntime]-INFO-interactive child response accepted stepId=s2 recognizedText=barn
260518 20:12:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s2
260518 20:12:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:13:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=2,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_step_layers_drawn_by_step"], 1)
        self.assertIn("lesson_step_layers_drawn_by_step", result["missing"])

    def test_audit_rejects_lesson_prompt_before_rendered_ack(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:03[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_prompt_after_render", result["missing"])

    def test_audit_rejects_lesson_flow_missing_robot_overlay_draw(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:05[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_robot_overlays_drawn>=1", result["missing"])

    def test_audit_rejects_lesson_flow_with_only_start_ack_tts_no_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_start_ack queued via tts text='Bắt đầu bài học nhé.'
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_tts"], 0)
        self.assertIn("lesson_prompt_tts>=1", result["missing"])

    def test_audit_rejects_lesson_flow_missing_layers_or_steps(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=0 robotOverlay=1 prompt=1
260518 20:11:01[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='step 1'
260518 20:11:01[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=9,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_steps>=9", result["missing"])
        self.assertIn("lesson_step_layers_complete", result["missing"])
        self.assertIn("lesson_firmware_rendered>=9", result["missing"])

    def _candidate_identity(self):
        return {
            "gitSha": "candidate-sha",
            "imageDigest": f"sha256:{'a' * 64}",
            "firmwareIdentity": "esp32-production-2.2.7",
            "fixtureSha256": "b" * 64,
            "configFingerprint": f"sha256:{'c' * 64}",
        }

    def _candidate_physical_log(
        self,
        *,
        first_audio=None,
        interrupt_stop=None,
        physical_bargein=None,
        output_gaps=None,
        extra_lines=None,
    ):
        identity = json.dumps(self._candidate_identity(), separators=(",", ":"))
        first_audio = [600.0] * 10 if first_audio is None else first_audio
        interrupt_stop = [25.0] * 10 if interrupt_stop is None else interrupt_stop
        physical_bargein = (
            [300.0] * 10 if physical_bargein is None else physical_bargein
        )
        output_gaps = [100.0] * 10 if output_gaps is None else output_gaps
        lines = [
            "260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}",
            "2026-08-31 13:10:00+00:00[GoogleLive]-INFO-Google Live reliability_window_start window_id=physical-1 journey_id=physical-1 connection_id=connection-1 live_connection_id=live-1 initial_live_connection_id=live-1 peer_identity_hash=sha256:"
            + "d" * 64
            + " server_start_utc=2026-08-31T13:10:00+00:00 candidate_identity="
            + identity,
            "260518 20:10:00[GoogleLive]-INFO-Google Live evidence_receive_loop_started journey_id=physical-1 connection_id=connection-1 live_connection_id=live-1 generation=1",
            "260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2",
            "260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=8 text='xin chào'",
        ]
        lines.extend(
            f"260518 20:11:{index:02d}[GoogleLive]-INFO-Google Live turn_latency_ms={value} phase=first_audio_out"
            for index, value in enumerate(first_audio)
        )
        lines.extend(
            f"260518 20:12:{index:02d}[GoogleLive]-INFO-Google Live interruption_stop_latency_ms={value}"
            for index, value in enumerate(interrupt_stop)
        )
        lines.extend(
            f"260518 20:13:{index:02d}[GoogleLive]-INFO-Google Live physical_bargein_latency_ms={value}"
            for index, value in enumerate(physical_bargein)
        )
        gap_epoch = datetime(2026, 8, 31, 13, 14, tzinfo=timezone.utc)
        for index, item in enumerate(output_gaps):
            if isinstance(item, tuple):
                value, boundary_type, boundary_duration = item
            else:
                value, boundary_type, boundary_duration = item, None, None
            start = gap_epoch + timedelta(seconds=index * 2)
            end = start + timedelta(milliseconds=value)
            gap_id = f"gap-{index}"
            lines.append(
                "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live server_output_gap "
                "gap_id={} start_utc={} end_utc={} duration_ms={}".format(
                    index,
                    gap_id,
                    start.isoformat(),
                    end.isoformat(),
                    value,
                )
            )
            if boundary_type is not None:
                boundary_end = start + timedelta(milliseconds=boundary_duration)
                lines.append(
                    "260518 20:14:{:02d}[GoogleLive]-INFO-Google Live "
                    "server_output_gap_boundary gap_id={} type={} start_utc={} "
                    "end_utc={} duration_ms={}".format(
                        index,
                        gap_id,
                        boundary_type,
                        start.isoformat(),
                        boundary_end.isoformat(),
                        boundary_duration,
                    )
                )
        lines.extend(extra_lines or [])
        lines.extend(
            [
                "260518 20:15:00[GoogleLive]-INFO-Google Live evidence_receive_loop_stopped journey_id=physical-1 connection_id=connection-1 live_connection_id=live-1 generation=1",
                "2026-08-31 13:15:01+00:00[GoogleLive]-INFO-Google Live reliability_window_end window_id=physical-1 server_end_utc=2026-08-31T13:15:01+00:00",
            ]
        )
        return "\n".join(lines)

    def _candidate_audit_options(self):
        identity = self._candidate_identity()
        stage_names = [
            name
            for name, count in (
                ("conversation", 17),
                ("bargein", 10),
                ("quiet", 2),
                ("reopen", 1),
                ("reconnect", 1),
                ("lesson", 1),
                ("conversation_after_lesson", 1),
            )
            for _ in range(count)
        ]
        cursor = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
        evidence_executions = []
        for sequence, stage in enumerate(stage_names, start=1):
            end = cursor + timedelta(seconds=40)
            connection_id = "connection-2" if sequence >= 31 else "connection-1"
            journey_id = f"journey-{sequence}"
            window_id = f"window-{sequence}"
            evidence_scope = {
                "journeyId": journey_id,
                "connectionId": connection_id,
                "liveConnectionId": "live-1",
                "initialLiveConnectionId": "live-1",
                "peerIdentityHash": f"sha256:{'d' * 64}",
                "serverStartUtc": cursor.isoformat(),
            }
            server_transitions = []
            if stage == "reconnect":
                server_transitions = [
                    {
                        "status": "PASS",
                        "source": "server_log",
                        "serverIssued": True,
                        "sequence": 1,
                        "reason": "same_device_reconnect",
                        "fromJourneyId": f"journey-{sequence - 1}",
                        "fromConnectionId": "connection-1",
                        "toJourneyId": journey_id,
                        "toConnectionId": connection_id,
                        "peerIdentityHash": evidence_scope["peerIdentityHash"],
                    }
                ]
            evidence_executions.append(
                {
                    "sequence": sequence,
                    "stage": stage,
                    "journeyId": journey_id,
                    "connectionId": connection_id,
                    "windowId": window_id,
                    "evidenceScope": evidence_scope,
                    "initialLiveConnectionId": "live-1",
                    "finalLiveConnectionId": "live-1",
                    "liveConnectionTransitions": [],
                    "serverConnectionTransitions": server_transitions,
                    "status": "PASS",
                    "logWindow": {
                        "windowId": window_id,
                        "start": cursor.isoformat(),
                        "end": end.isoformat(),
                    },
                }
            )
            cursor = end
        padding_start = cursor + timedelta(seconds=10)
        padding_end = padding_start + timedelta(seconds=480)
        quiet_padding = [
            {
                "journeyId": "quiet-padding-1",
                "candidateIdentity": identity,
                "connectionId": "connection-2",
                "windowId": "quiet-padding-window-1",
                "logWindow": {
                    "windowId": "quiet-padding-window-1",
                    "start": padding_start.isoformat(),
                    "end": padding_end.isoformat(),
                },
                "durationSec": 480.0,
                "status": "PASS",
                "serverIssued": True,
                "peerIdentityHash": f"sha256:{'d' * 64}",
                "liveConnectionId": "padding-live-1",
                "initialLiveConnectionId": "padding-live-1",
                "finalLiveConnectionId": "padding-live-1",
                "liveConnectionTransitions": [],
                "evidenceScope": {
                    "journeyId": "quiet-padding-1",
                    "connectionId": "connection-2",
                    "liveConnectionId": "padding-live-1",
                    "initialLiveConnectionId": "padding-live-1",
                    "peerIdentityHash": f"sha256:{'d' * 64}",
                    "serverStartUtc": padding_start.isoformat(),
                },
                "falseInterrupts": 0,
                "unexpectedFallbacks": 0,
                "resourceVerdict": {"status": "PASS"},
                "logStatus": "PASS",
            }
        ]
        reliability_report = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "google_live_log_reliability",
            "status": "PASS",
            "candidateIdentity": identity,
            "logWindow": {
                "windowId": "physical-1",
                "start": "2026-08-31T13:10:00+00:00",
                "end": "2026-08-31T13:15:01+00:00",
            },
            "evidenceScope": {
                "journeyId": "physical-1",
                "connectionId": "connection-1",
                "liveConnectionId": "live-1",
                "initialLiveConnectionId": "live-1",
                "peerIdentityHash": f"sha256:{'d' * 64}",
                "serverStartUtc": "2026-08-31T13:10:00+00:00",
            },
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "serverConnectionTransitions": [],
            "receiveLoopBalance": 0,
            "maxReceiveLoopsActive": 1,
            "staleAudioAfterReplacement": 0,
            "duplicateResponseIds": [],
            "unrecoveredTimeouts": [],
            "unreleasedLessonHandoffs": [],
            "replayCountsByReopen": {},
            "correlations": [
                {
                    "status": "PASS",
                    "journeyId": "physical-1",
                    "connectionId": "connection-1",
                    "liveConnectionId": "live-1",
                    "cancelledResponseId": 1,
                    "replacementResponseId": 2,
                }
            ],
            "correlation": {
                "status": "PASS",
                "cancelledResponseId": 1,
                "replacementResponseId": 2,
            },
            "failures": [],
            "fatalHits": [],
        }
        candidate_soak_report = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_soak",
            "status": "PASS",
            "candidateIdentity": identity,
            "stages": [
                {"name": name, "executions": count, "status": "PASS"}
                for name, count in (
                    ("conversation", 17),
                    ("bargein", 10),
                    ("quiet", 2),
                    ("reopen", 1),
                    ("reconnect", 1),
                    ("lesson", 1),
                    ("conversation_after_lesson", 1),
                )
            ],
            "cleanupVerdict": {
                "status": "PASS",
                "websocketClosed": True,
                "pendingOwnedTasks": 0,
                "actualPendingCleanupTasks": 0,
                "activeSessions": 0,
                "activeReceiveLoops": 0,
                "providerFinalizeStatus": "PASS",
                "providerCloseStatus": "PASS",
                "logStatus": "PASS",
                "resourceEndSampleAccounted": True,
            },
            "durationSec": 1800.0,
            "runtimeElapsedSec": 1800.0,
            "recordedRuntimeElapsedSec": None,
            "replayCandidateEvidence": False,
            "evidenceGapBudgetSec": 10.0,
            "evidenceAnchors": {
                "serverStartUtc": "2026-08-31T11:00:00+00:00",
                "serverEndUtc": "2026-08-31T11:30:10+00:00",
            },
            "evidenceExecutions": evidence_executions,
            "quietPadding": quiet_padding,
            "totals": {
                "successfulTurns": 30,
                "bargeins": 10,
                "latestIntentSuccesses": 10,
                "falseInterrupts": 0,
                "unexpectedFallbacks": 0,
                "latestIntentSuccessRate": 1.0,
            },
            "latencyMetrics": {
                "firstAudioP50Ms": 600.0,
                "firstAudioP95Ms": 900.0,
                "bargeinP95Ms": 300.0,
                "reconnectRecoveryP95Ms": 900.0,
            },
            "serverOutputGapP95Ms": 100.0,
            "latencyComparison": {
                "checks": {
                    "firstAudioP50Regression": True,
                    "firstAudioP95Regression": True,
                    "bargeinP95Regression": True,
                    "reconnectRecoveryP95Regression": True,
                },
                "regressionPct": {
                    "firstAudioP50Ms": 0.0,
                    "firstAudioP95Ms": 0.0,
                    "bargeinP95Ms": 0.0,
                    "reconnectRecoveryP95Ms": 0.0,
                },
                "pass": True,
            },
            "resourceVerdict": {
                "status": "PASS",
                "checks": {
                    "rssDeltaBounded": True,
                    "fdDeltaBounded": True,
                    "taskDeltaBounded": True,
                    "threadDeltaBounded": True,
                    "rssSlopeBounded": True,
                    "fdSlopeBounded": True,
                    "taskSlopeBounded": True,
                    "threadSlopeBounded": True,
                },
                "failures": [],
                "deltas": {
                    "rssBytes": 0,
                    "fdCount": 0,
                    "asyncioTaskCount": 0,
                    "threadCount": 0,
                },
                "slopes": {
                    "rssBytesPerSample": 0.0,
                    "fdCountPerSample": 0.0,
                    "asyncioTaskCountPerSample": 0.0,
                    "threadCountPerSample": 0.0,
                },
                "limits": {
                    "rssDeltaBytes": 33554432,
                    "fdDelta": 8,
                    "asyncioTaskDelta": 4,
                    "threadDelta": 4,
                    "rssSlopeBytesPerSample": 1048576,
                    "fdSlopePerSample": 0.25,
                    "asyncioTaskSlopePerSample": 0.25,
                    "threadSlopePerSample": 0.25,
                },
            },
            "upstreamLayers": [
                {"name": name, "status": status, "candidateIdentity": identity}
                for name, status in (
                    ("real_api", "PASS"),
                    ("websocket_audio_bargein_transport", "SKIPPED"),
                    ("websocket_audio_bargein_correlated", "PASS"),
                    ("google_live_log_reliability", "PASS"),
                )
            ],
            "failures": [],
            "rawAudioPersisted": False,
            "transcriptPersisted": False,
            "exit_code": 0,
        }
        return {
            "device_id": "3c:0f:02:de:c2:e0",
            "client_id": "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            "min_interrupts": 0,
            "candidate_identity": self._candidate_identity(),
            "reliability_report": reliability_report,
            "candidate_soak_report": candidate_soak_report,
            "max_first_audio_p50_ms": 1200.0,
            "max_first_audio_p95_ms": 1800.0,
            "max_interrupt_stop_latency_ms": 250.0,
            "max_physical_bargein_p95_ms": 500.0,
            "max_server_output_gap_ms": 250.0,
            "min_first_audio_samples": 10,
            "min_interrupt_stop_samples": 10,
            "min_physical_bargein_samples": 10,
            "min_server_output_gap_samples": 10,
            "require_receive_loop_balance": True,
        }

    def _candidate_audit(self, log_text, **overrides):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        options = self._candidate_audit_options()
        options.update(overrides)
        return audit.audit_log(log_text, **options)

    def test_candidate_physical_audit_reports_all_production_budgets(self):
        result = self._candidate_audit(self._candidate_physical_log())

        self.assertTrue(result["passed"], result["missing"])
        self.assertLessEqual(result["firstAudioLatencyMs"]["p50"], 1200.0)
        self.assertLessEqual(result["firstAudioLatencyMs"]["p95"], 1800.0)
        self.assertLessEqual(result["interruptStopLatencyMs"]["max"], 250.0)
        self.assertLessEqual(result["physicalBargeinLatencyMs"]["p95"], 500.0)
        self.assertLessEqual(result["serverOutputGapMs"]["max"], 250.0)
        self.assertEqual(result["receiveLoopBalance"], 0)
        self.assertEqual(result["maxReceiveLoopsActive"], 1)
        self.assertEqual(result["candidateIdentity"], self._candidate_identity())

    def test_candidate_physical_audit_fails_each_latency_bound(self):
        cases = (
            ({"first_audio": [1300.0] * 10}, "first_audio_p50_ms<=1200"),
            ({"first_audio": [600.0] * 9 + [1900.0]}, "first_audio_p95_ms<=1800"),
            ({"interrupt_stop": [25.0] * 9 + [251.0]}, "interrupt_stop_latency_ms<=250"),
            ({"physical_bargein": [300.0] * 9 + [501.0]}, "physical_bargein_p95_ms<=500"),
            ({"output_gaps": [100.0] * 9 + [251.0]}, "server_output_gap_ms<=250"),
        )
        for changes, expected_missing in cases:
            with self.subTest(expected_missing=expected_missing):
                result = self._candidate_audit(self._candidate_physical_log(**changes))
                self.assertFalse(result["passed"])
                self.assertIn(expected_missing, result["missing"])

    def test_candidate_physical_audit_excludes_intentional_interrupt_output_gap(self):
        result = self._candidate_audit(
            self._candidate_physical_log(
                output_gaps=[100.0] * 9 + [(900.0, "interrupt", 700.0)]
            )
        )

        self.assertTrue(result["passed"], result["missing"])
        self.assertEqual(result["serverOutputGapMs"]["excludedIntentional"], 1)
        self.assertEqual(result["serverOutputGapMs"]["excludedDurationMs"], 700.0)
        self.assertEqual(
            result["serverOutputGapMs"]["unexplainedResidualMs"]["max"], 200.0
        )
        self.assertEqual(result["serverOutputGapMs"]["max"], 200.0)

    def test_candidate_physical_audit_fails_closed_on_identity_scope_and_multiplicity(self):
        mismatched = self._candidate_identity()
        mismatched["gitSha"] = "other-sha"
        result = self._candidate_audit(
            self._candidate_physical_log(first_audio=[600.0] * 9),
            candidate_identity=mismatched,
        )

        self.assertFalse(result["passed"])
        self.assertIn("candidate_identity_match", result["missing"])
        self.assertIn("first_audio_samples=10", result["missing"])

    def test_candidate_physical_audit_rejects_missing_latency_evidence(self):
        cases = (
            ({"first_audio": []}, "first_audio_samples=10"),
            ({"interrupt_stop": []}, "interrupt_stop_latency_ms=10"),
            ({"physical_bargein": []}, "physical_bargein_samples=10"),
            ({"output_gaps": []}, "server_output_gap_samples=10"),
        )
        for changes, expected_missing in cases:
            with self.subTest(expected_missing=expected_missing):
                result = self._candidate_audit(self._candidate_physical_log(**changes))
                self.assertFalse(result["passed"])
                self.assertIn(expected_missing, result["missing"])

    def test_candidate_physical_audit_requires_exact_metric_multiplicities(self):
        cases = (
            ("first_audio", 600.0, "first_audio_samples=10"),
            ("interrupt_stop", 25.0, "interrupt_stop_latency_ms=10"),
            ("physical_bargein", 300.0, "physical_bargein_samples=10"),
            ("output_gaps", 100.0, "server_output_gap_samples=10"),
        )
        for field, sample, expected_missing in cases:
            for count in (9, 11):
                with self.subTest(field=field, count=count):
                    result = self._candidate_audit(
                        self._candidate_physical_log(**{field: [sample] * count})
                    )
                    self.assertFalse(result["passed"])
                    self.assertIn(expected_missing, result["missing"])

    def test_candidate_physical_audit_uses_full_normalized_log_contract(self):
        mutations = (
            (
                "peer",
                lambda report: report["evidenceScope"].update(
                    peerIdentityHash=f"sha256:{'e' * 64}"
                ),
            ),
            (
                "connection",
                lambda report: report["evidenceScope"].update(
                    connectionId="fabricated"
                ),
            ),
            (
                "window_time",
                lambda report: report["logWindow"].update(
                    end="2026-08-31T13:16:01+00:00"
                ),
            ),
            (
                "initial_live",
                lambda report: report.update(
                    initialLiveConnectionId="fabricated-live"
                ),
            ),
            (
                "final_live",
                lambda report: report.update(finalLiveConnectionId="fabricated-live"),
            ),
            (
                "transition_ledger",
                lambda report: report.update(
                    liveConnectionTransitions=[
                        {
                            "attempt": 2,
                            "fromLiveConnectionId": "live-1",
                            "toLiveConnectionId": "fabricated-live",
                            "status": "committed",
                        }
                    ]
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                report = deepcopy(
                    self._candidate_audit_options()["reliability_report"]
                )
                mutate(report)
                result = self._candidate_audit(
                    self._candidate_physical_log(), reliability_report=report
                )
                self.assertFalse(result["passed"])
                self.assertIn("log_reliability_report_pass", result["missing"])

    def test_candidate_physical_audit_rejects_malformed_latency_evidence(self):
        log_text = self._candidate_physical_log() + "\n" + "\n".join(
            (
                "260518 20:16:00[GoogleLive]-INFO-Google Live turn_latency_ms=nan phase=first_audio_out",
                "260518 20:16:01[GoogleLive]-INFO-Google Live interruption_stop_latency_ms=-1",
                "260518 20:16:02[GoogleLive]-INFO-Google Live physical_bargein_latency_ms=inf",
                "260518 20:16:03[GoogleLive]-INFO-Google Live server_output_gap_ms=oops boundary=continuous",
                "260518 20:16:04[GoogleLive]-INFO-Google Live turn_latency_ms=1.2.3 phase=first_audio_out",
            )
        )

        result = self._candidate_audit(log_text)

        self.assertFalse(result["passed"])
        self.assertIn("latency_evidence_valid", result["missing"])

    def test_candidate_physical_audit_rejects_untyped_gap_exclusion(self):
        result = self._candidate_audit(
            self._candidate_physical_log(
                output_gaps=[100.0] * 9
                + [(900.0, "interrupt_like", 700.0)]
            )
        )

        self.assertFalse(result["passed"])
        self.assertIn("server_output_gap_boundaries_valid", result["missing"])

    def test_candidate_physical_audit_boundary_is_subtractive_not_whole_gap_mask(self):
        result = self._candidate_audit(
            self._candidate_physical_log(
                output_gaps=[100.0] * 9 + [(900.0, "interrupt", 1.0)]
            )
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["serverOutputGapMs"]["max"], 899.0)
        self.assertIn("server_output_gap_ms<=250", result["missing"])

    def test_candidate_physical_audit_rejects_duplicate_or_oversized_boundary(self):
        log_text = self._candidate_physical_log(
            output_gaps=[100.0] * 9 + [(900.0, "interrupt", 700.0)]
        )
        boundary_line = next(
            line
            for line in log_text.splitlines()
            if "server_output_gap_boundary gap_id=gap-9" in line
        )
        duplicate = self._candidate_audit(log_text + "\n" + boundary_line)
        oversized = self._candidate_audit(
            self._candidate_physical_log(
                output_gaps=[100.0] * 9 + [(900.0, "interrupt", 901.0)]
            )
        )
        negative = self._candidate_audit(
            self._candidate_physical_log(
                output_gaps=[100.0] * 9 + [(900.0, "interrupt", -1.0)]
            )
        )

        self.assertIn("server_output_gap_boundaries_valid", duplicate["missing"])
        self.assertIn("server_output_gap_boundaries_valid", oversized["missing"])
        self.assertIn("server_output_gap_boundaries_valid", negative["missing"])

    def test_candidate_physical_audit_rejects_partially_overlapping_boundaries(self):
        log_text = self._candidate_physical_log(
            output_gaps=[100.0] * 9 + [(900.0, "interrupt", 700.0)]
        )
        overlap = (
            "260518 20:14:09[GoogleLive]-INFO-Google Live "
            "server_output_gap_boundary gap_id=gap-9 type=backpressure "
            "start_utc=2026-08-31T13:14:18.600000+00:00 "
            "end_utc=2026-08-31T13:14:18.800000+00:00 duration_ms=200.0"
        )

        result = self._candidate_audit(log_text + "\n" + overlap)

        self.assertFalse(result["passed"])
        self.assertIn("server_output_gap_boundaries_valid", result["missing"])

    def test_candidate_physical_audit_rejects_unbalanced_or_overlapping_receive_loops(self):
        identity = self._candidate_identity()
        bad_report = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "google_live_log_reliability",
            "status": "PASS",
            "candidateIdentity": identity,
            "logWindow": {
                "windowId": "physical-1",
                "start": "2026-08-31T13:10:00+00:00",
                "end": "2026-08-31T13:15:01+00:00",
            },
            "evidenceScope": {
                "journeyId": "physical-1",
                "connectionId": "connection-1",
                "liveConnectionId": "live-1",
                "initialLiveConnectionId": "live-1",
                "peerIdentityHash": f"sha256:{'d' * 64}",
                "serverStartUtc": "2026-08-31T13:10:00+00:00",
            },
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "serverConnectionTransitions": [],
            "receiveLoopBalance": 1,
            "maxReceiveLoopsActive": 2,
            "staleAudioAfterReplacement": 0,
            "duplicateResponseIds": [],
            "unrecoveredTimeouts": [],
            "unreleasedLessonHandoffs": [],
            "replayCountsByReopen": {},
            "correlations": [
                {
                    "status": "PASS",
                    "journeyId": "physical-1",
                    "connectionId": "connection-1",
                    "liveConnectionId": "live-1",
                    "cancelledResponseId": 1,
                    "replacementResponseId": 2,
                }
            ],
            "correlation": {
                "status": "PASS",
                "cancelledResponseId": 1,
                "replacementResponseId": 2,
            },
            "failures": [],
            "fatalHits": [],
        }
        result = self._candidate_audit(
            self._candidate_physical_log(), reliability_report=bad_report
        )

        self.assertFalse(result["passed"])
        self.assertIn("receive_loop_balance=0", result["missing"])
        self.assertIn("max_receive_loops_active=1", result["missing"])

    def test_candidate_physical_audit_rejects_skipped_or_sensitive_upstream_report(self):
        identity = self._candidate_identity()
        skipped = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_soak",
            "status": "SKIPPED",
            "candidateIdentity": identity,
            "rawAudio": "forbidden",
        }

        result = self._candidate_audit(
            self._candidate_physical_log(), candidate_soak_report=skipped
        )

        self.assertFalse(result["passed"])
        self.assertIn("candidate_soak_report_pass", result["missing"])
        self.assertIn("candidate_soak_report_privacy_safe", result["missing"])

    def test_candidate_physical_audit_uses_full_candidate_soak_contract(self):
        mutations = (
            ("websocket_open", lambda report: report["cleanupVerdict"].update(websocketClosed=False)),
            ("pending_bool", lambda report: report["cleanupVerdict"].update(pendingOwnedTasks=False)),
            ("pending_float", lambda report: report["cleanupVerdict"].update(pendingOwnedTasks=0.0)),
            ("pending_nonzero", lambda report: report["cleanupVerdict"].update(pendingOwnedTasks=1)),
            ("active_session", lambda report: report["cleanupVerdict"].update(activeSessions=1)),
            ("active_receive", lambda report: report["cleanupVerdict"].update(activeReceiveLoops=1)),
            ("provider_finalize", lambda report: report["cleanupVerdict"].update(providerFinalizeStatus="FAIL")),
            ("provider_close", lambda report: report["cleanupVerdict"].update(providerCloseStatus="FAIL")),
            ("stage_bool", lambda report: report["stages"][3].update(executions=True)),
            ("totals_float", lambda report: report["totals"].update(successfulTurns=30.0)),
            ("false_interrupt_bool", lambda report: report["totals"].update(falseInterrupts=False)),
            ("false_interrupt", lambda report: report["totals"].update(falseInterrupts=1)),
            ("unexpected_fallback", lambda report: report["totals"].update(unexpectedFallbacks=1)),
            ("resource_status", lambda report: report["resourceVerdict"].update(status="FAIL")),
            (
                "resource_checks_list",
                lambda report: report["resourceVerdict"].update(
                    checks=list(report["resourceVerdict"]["checks"])
                ),
            ),
            ("resource_check_int", lambda report: report["resourceVerdict"]["checks"].update(rssDeltaBounded=1)),
            ("resource_leak", lambda report: report["resourceVerdict"]["deltas"].update(rssBytes=33554433)),
            ("latency_comparison", lambda report: report["latencyComparison"].update(pass_=False)),
            ("latency_check_int", lambda report: report["latencyComparison"]["checks"].update(firstAudioP50Regression=1)),
            ("latency_failures", lambda report: report["latencyComparison"].update(failures=[{"code": "FAIL"}])),
            (
                "latency_checks_list",
                lambda report: report["latencyComparison"].update(
                    checks=list(report["latencyComparison"]["checks"])
                ),
            ),
            (
                "latency_metrics_list",
                lambda report: report.update(
                    latencyMetrics=list(report["latencyMetrics"])
                ),
            ),
            ("latency_zero", lambda report: report["latencyMetrics"].update(reconnectRecoveryP95Ms=0.0)),
            ("latency_metric", lambda report: report["latencyMetrics"].update(firstAudioP95Ms=1801.0)),
            ("duration", lambda report: report.update(durationSec=1799.0)),
            ("live_runtime", lambda report: report.update(runtimeElapsedSec=0.0)),
            ("recorded_runtime", lambda report: report.update(recordedRuntimeElapsedSec=1799.0)),
            ("gap_budget", lambda report: report.update(evidenceGapBudgetSec=1000.0)),
            ("anchor", lambda report: report["evidenceAnchors"].update(serverEndUtc="2026-08-31T10:59:00+00:00")),
            ("anchors_list", lambda report: report.update(evidenceAnchors=[])),
            ("execution_fail", lambda report: report["evidenceExecutions"][0].update(status="FAIL")),
            (
                "execution_journey_duplicate",
                lambda report: report["evidenceExecutions"][1].update(
                    journeyId=report["evidenceExecutions"][0]["journeyId"],
                    evidenceScope={
                        **report["evidenceExecutions"][1]["evidenceScope"],
                        "journeyId": report["evidenceExecutions"][0]["journeyId"],
                    },
                ),
            ),
            ("execution_scope_missing", lambda report: report["evidenceExecutions"][0].pop("evidenceScope")),
            ("execution_connection_fabricated", lambda report: report["evidenceExecutions"][0].update(connectionId="fabricated")),
            ("execution_peer_fabricated", lambda report: report["evidenceExecutions"][0]["evidenceScope"].update(peerIdentityHash=f"sha256:{'e' * 64}")),
            ("execution_live_owner_stripped", lambda report: report["evidenceExecutions"][0].pop("liveConnectionTransitions")),
            (
                "execution_live_final_fabricated",
                lambda report: report["evidenceExecutions"][0].update(
                    finalLiveConnectionId="fabricated-live"
                ),
            ),
            ("execution_server_transition_stripped", lambda report: report["evidenceExecutions"][30].update(serverConnectionTransitions=[])),
            ("padding_candidate_identity", lambda report: report["quietPadding"][0].update(candidateIdentity={**self._candidate_identity(), "gitSha": "other"})),
            ("padding_journey_duplicate", lambda report: report["quietPadding"][0].update(journeyId=report["evidenceExecutions"][0]["journeyId"], evidenceScope={**report["quietPadding"][0]["evidenceScope"], "journeyId": report["evidenceExecutions"][0]["journeyId"]})),
            ("padding_connection", lambda report: report["quietPadding"][0].update(connectionId="unrelated", evidenceScope={**report["quietPadding"][0]["evidenceScope"], "connectionId": "unrelated"})),
            ("padding_scope_missing", lambda report: report["quietPadding"][0].pop("evidenceScope")),
            ("padding_peer", lambda report: report["quietPadding"][0].update(peerIdentityHash=f"sha256:{'e' * 64}", evidenceScope={**report["quietPadding"][0]["evidenceScope"], "peerIdentityHash": f"sha256:{'e' * 64}"})),
            ("padding_live_final", lambda report: report["quietPadding"][0].update(finalLiveConnectionId="fabricated-live")),
            ("padding_live_transitions", lambda report: report["quietPadding"][0].update(liveConnectionTransitions=[{"attempt": 1, "fromLiveConnectionId": "wrong", "toLiveConnectionId": "fabricated-live"}])),
            ("padding_log_status", lambda report: report["quietPadding"][0].update(logStatus="FAIL")),
            ("padding_log_status_missing", lambda report: report["quietPadding"][0].pop("logStatus")),
            ("padding_extra_field", lambda report: report["quietPadding"][0].update(extra=True)),
            ("padding_window_duplicate", lambda report: report["quietPadding"][0].update(windowId=report["evidenceExecutions"][0]["windowId"], logWindow={**report["quietPadding"][0]["logWindow"], "windowId": report["evidenceExecutions"][0]["windowId"]})),
            ("fake_padding", lambda report: report["quietPadding"].append({"status": "PASS"})),
            ("raw_audio_flag_int", lambda report: report.update(rawAudioPersisted=0)),
            (
                "upstream_identity",
                lambda report: report["upstreamLayers"][0].update(
                    candidateIdentity={**self._candidate_identity(), "gitSha": "other"}
                ),
            ),
            ("upstream_missing", lambda report: report.pop("upstreamLayers")),
            ("upstream_malformed", lambda report: report.update(upstreamLayers={})),
            ("upstream_reordered", lambda report: report["upstreamLayers"].reverse()),
            (
                "upstream_extra",
                lambda report: report["upstreamLayers"].append(
                    deepcopy(report["upstreamLayers"][0])
                ),
            ),
            (
                "upstream_duplicate",
                lambda report: report["upstreamLayers"].__setitem__(
                    1, deepcopy(report["upstreamLayers"][0])
                ),
            ),
            (
                "upstream_wrong_status",
                lambda report: report["upstreamLayers"][0].update(status="SKIPPED"),
            ),
            (
                "upstream_extra_field",
                lambda report: report["upstreamLayers"][0].update(extra=True),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                report = deepcopy(
                    self._candidate_audit_options()["candidate_soak_report"]
                )
                mutate(report)
                if "pass_" in report.get("latencyComparison", {}):
                    report["latencyComparison"]["pass"] = report[
                        "latencyComparison"
                    ].pop("pass_")
                result = self._candidate_audit(
                    self._candidate_physical_log(), candidate_soak_report=report
                )
                self.assertFalse(result["passed"])
                self.assertIn("candidate_soak_report_pass", result["missing"])

    def test_candidate_physical_audit_accepts_minimum_latest_intent_rate(self):
        report = deepcopy(self._candidate_audit_options()["candidate_soak_report"])
        report["totals"].update(
            latestIntentSuccesses=8,
            latestIntentSuccessRate=0.8,
        )

        result = self._candidate_audit(
            self._candidate_physical_log(), candidate_soak_report=report
        )

        self.assertTrue(result["passed"])

    def test_candidate_cli_requires_complete_valid_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "server.log"
            log_path.write_text(self._candidate_physical_log(), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/physical_smoke_audit.py",
                    str(log_path),
                    "--device-id",
                    "3c:0f:02:de:c2:e0",
                    "--client-id",
                    "d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
                    "--production-google-live-candidate",
                    "--candidate-git-sha",
                    "candidate-sha",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

        self.assertEqual(proc.returncode, 2)
        self.assertIn("requires --candidate-image-digest", proc.stderr)

if __name__ == "__main__":
    unittest.main()
