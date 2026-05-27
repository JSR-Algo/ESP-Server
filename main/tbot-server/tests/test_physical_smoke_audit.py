import importlib
import unittest


class PhysicalSmokeAuditTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
