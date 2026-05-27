import importlib
from types import SimpleNamespace
import unittest


class VoiceModePreflightTest(unittest.TestCase):
    def test_parse_ping_output_macos_format(self):
        preflight = importlib.import_module("scripts.voice_mode_preflight")
        output = """
--- 192.168.100.247 ping statistics ---
10 packets transmitted, 10 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 68.490/490.320/902.463/290.023 ms
"""

        stats = preflight.parse_ping_output(output)

        self.assertEqual(stats["loss_pct"], 0.0)
        self.assertEqual(stats["avg_ms"], 490.320)
        self.assertEqual(stats["max_ms"], 902.463)
        self.assertEqual(stats["jitter_ms"], 290.023)
        self.assertEqual(stats["duplicates"], 0)

    def test_parse_ping_output_linux_format(self):
        preflight = importlib.import_module("scripts.voice_mode_preflight")
        output = """
--- 192.168.100.247 ping statistics ---
10 packets transmitted, 9 received, 10% packet loss, time 9016ms
rtt min/avg/max/mdev = 12.100/33.200/95.400/8.900 ms
"""

        stats = preflight.parse_ping_output(output)

        self.assertEqual(stats["loss_pct"], 10.0)
        self.assertEqual(stats["avg_ms"], 33.200)
        self.assertEqual(stats["max_ms"], 95.400)
        self.assertEqual(stats["jitter_ms"], 8.900)
        self.assertEqual(stats["duplicates"], 0)

    def test_parse_ping_output_detects_duplicate_replies(self):
        preflight = importlib.import_module("scripts.voice_mode_preflight")
        output = """
64 bytes from 192.168.100.247: icmp_seq=0 ttl=64 time=2442.374 ms
64 bytes from 192.168.100.247: icmp_seq=0 ttl=64 time=2442.435 ms (DUP!)
--- 192.168.100.247 ping statistics ---
20 packets transmitted, 19 packets received, +1 duplicates, 5.0% packet loss
round-trip min/avg/max/stddev = 367.474/799.095/2442.435/582.227 ms
"""

        stats = preflight.parse_ping_output(output)

        self.assertEqual(stats["loss_pct"], 5.0)
        self.assertEqual(stats["avg_ms"], 799.095)
        self.assertEqual(stats["max_ms"], 2442.435)
        self.assertEqual(stats["jitter_ms"], 582.227)
        self.assertEqual(stats["duplicates"], 1)

    def test_parse_ping_output_handles_total_packet_loss(self):
        preflight = importlib.import_module("scripts.voice_mode_preflight")
        output = """
PING 192.168.100.247 (192.168.100.247): 56 data bytes
Request timeout for icmp_seq 0

--- 192.168.100.247 ping statistics ---
2 packets transmitted, 0 packets received, 100.0% packet loss
"""

        stats = preflight.parse_ping_output(output)

        self.assertEqual(stats["loss_pct"], 100.0)
        self.assertEqual(stats["avg_ms"], 0.0)
        self.assertEqual(stats["max_ms"], 0.0)
        self.assertEqual(stats["jitter_ms"], 0.0)
        self.assertEqual(stats["duplicates"], 0)

    def test_preflight_failure_detects_max_latency_spike(self):
        preflight = importlib.import_module("scripts.voice_mode_preflight")
        stats = {
            "loss_pct": 0.0,
            "avg_ms": 200.0,
            "max_ms": 1200.0,
            "jitter_ms": 20.0,
            "duplicates": 0,
        }
        args = SimpleNamespace(
            max_loss_pct=0.0,
            max_avg_ms=1000.0,
            max_max_ms=1000.0,
            max_jitter_ms=None,
            max_duplicates=None,
        )

        self.assertEqual(
            preflight.preflight_failure(stats, args),
            "max_latency 1200.0ms > 1000.0ms",
        )


if __name__ == "__main__":
    unittest.main()
