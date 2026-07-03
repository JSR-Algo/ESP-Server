import importlib
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


class PhysicalSmokeAuditTest(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
