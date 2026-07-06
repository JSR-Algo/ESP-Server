import re
import unittest
from pathlib import Path


class VpsVoiceGoalWatcherScriptTest(unittest.TestCase):
    def test_passive_watcher_captures_short_target_sessions(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("--target", text)
        self.assertIn("--client", text)
        self.assertIn("28:84:85:85:1a:80", text)
        self.assertIn("c29ce67a-3288-4c39-8544-bba97dab332b", text)
        self.assertIn("/internal/lesson-runtime/metrics", text)
        self.assertIn("docker logs", text)
        self.assertRegex(text, r"docker logs[^\n]+(--follow|-f)")
        self.assertIn("Headers:", text)
        self.assertIn("input_audio_diag", text)
        self.assertIn("tts_stop_sent", text)
        self.assertIn("transcript source=user", text)
        self.assertIn("user_audio_window_expired", text)

    def test_passive_watcher_captures_haproxy_target_sessions(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("follow_container tbot-wss-lb", text)
        self.assertIn("tbot-wss-lb", text)

    def test_passive_watcher_matches_haproxy_url_encoded_target(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn('TARGET_URLENC="${TARGET//:/%3A}"', text)
        self.assertIn("${TARGET_URLENC}", text)

    def test_passive_watcher_snapshots_haproxy_only_target_sessions(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("raw_target_seen=", text)
        self.assertIn('grep -E "${TARGET}|${TARGET_URLENC}|${CLIENT}"', text)
        self.assertIn('if printf \'%s\' "${metrics}" | grep -q "${TARGET}" || [[ "${raw_target_seen}" == "1" ]]; then', text)

    def test_passive_watcher_reads_metrics_from_direct_server_container_ips(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("docker inspect", text)
        self.assertIn("owner_ip", text)
        self.assertIn('http://${owner_ip}:8003/internal/lesson-runtime/metrics', text)
        self.assertNotIn(
            "http://127.0.0.1:8003/internal/lesson-runtime/metrics",
            text,
        )

    def test_remote_worker_respects_launcher_environment(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn('TARGET="${TARGET:-28:84:85:85:1a:80}"', text)
        self.assertIn(
            'CLIENT="${CLIENT:-c29ce67a-3288-4c39-8544-bba97dab332b}"',
            text,
        )
        self.assertIn('HEAD_TAG="${HEAD_TAG:-$(git -C', text)
        self.assertIn('DURATION_SEC="${DURATION_SEC:-43200}"', text)
        self.assertIn('WATCH_DIR="${WATCH_DIR:-/opt/tbot/voice_goal_watch}"', text)

    def test_passive_watcher_does_not_touch_hardware(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        forbidden_patterns = (
            r"\besptool\b",
            r"\bidf\.py\s+flash\b",
            r"/dev/(?:cu|tty)",
            r"local_sample_demo_nudge",
            r"lesson-nudge",
        )
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, text), pattern)

    def test_remote_worker_defaults_preserve_launcher_environment(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "watch-voice-goal-vps.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn('TARGET="${TARGET:-28:84:85:85:1a:80}"', text)
        self.assertIn('CLIENT="${CLIENT:-c29ce67a-3288-4c39-8544-bba97dab332b}"', text)
        self.assertIn('DURATION_SEC="${DURATION_SEC:-43200}"', text)
        self.assertIn('WATCH_DIR="${WATCH_DIR:-/opt/tbot/voice_goal_watch}"', text)
        self.assertIn('HEAD_TAG="${HEAD_TAG:-', text)


if __name__ == "__main__":
    unittest.main()
