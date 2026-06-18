import json
import subprocess
import sys
import unittest
from pathlib import Path


class BenchmarkGoogleLiveAudioRuntimeScriptTest(unittest.TestCase):
    def test_script_runs_from_repo_root_without_pythonpath(self):
        repo_root = Path(__file__).resolve().parents[1]

        result = subprocess.run(
            [
                sys.executable,
                "scripts/benchmark_google_live_audio_runtime.py",
                "--speakers",
                "1",
                "--frames",
                "2",
            ],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["results"][0]["speakers"], 1)
        self.assertEqual(payload["results"][0]["audio_execution"], "worker")
        self.assertEqual(payload["results"][0]["frames_per_speaker"], 2)
        self.assertEqual(payload["results"][0]["stream_audio_ms"], 120)
        self.assertGreaterEqual(payload["results"][0]["wall_ms"], 50)
        self.assertGreaterEqual(
            payload["results"][0]["recommended_accept_cap_1_core_70_headroom"],
            1,
        )
        self.assertGreaterEqual(
            payload["results"][0]["connection_loop_latency_p95_ms"],
            0,
        )
        self.assertGreater(payload["results"][0]["sent_device_packets"], 0)


if __name__ == "__main__":
    unittest.main()
