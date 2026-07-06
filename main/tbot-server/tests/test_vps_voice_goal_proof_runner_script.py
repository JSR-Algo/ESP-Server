import re
import unittest
from pathlib import Path


class VpsVoiceGoalProofRunnerScriptTest(unittest.TestCase):
    def test_runner_uses_owner_metrics_and_direct_container_nudge(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("/internal/lesson-runtime/metrics", text)
        self.assertIn("clientId", text)
        self.assertIn("owner_ip", text)
        self.assertIn("X-Mint-Secret", text)
        self.assertIn('f"http://{owner_ip}:8003/internal/devices/{target}/lesson-nudge"', text)
        self.assertIn('f"http://{owner_ip}:8003/internal/devices/{target}/lesson-child-response"', text)
        self.assertNotIn("127.0.0.1:8003/internal/devices", text)
        self.assertNotIn("request.full_url", text)
        self.assertNotIn("if owner_header or lb_header:", text)

    def test_runner_drives_local_prompts_and_strict_audit(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn('INTERRUPT_PROMPT_TEXT="${INTERRUPT_PROMPT_TEXT:-kẹo}"', text)
        self.assertIn('say -v "${INTERRUPT_PROMPT_VOICE}" "${INTERRUPT_PROMPT_TEXT}"', text)
        self.assertIn('CHILD_PROMPT_VOICE="${CHILD_PROMPT_VOICE:-Samantha}"', text)
        self.assertIn('CHILD_PROMPT_TEXT="${CHILD_PROMPT_TEXT:-darn darn darn}"', text)
        self.assertIn('CHILD_RESPONSE_TEXT="${CHILD_RESPONSE_TEXT:-barn}"', text)
        self.assertIn('POST_LESSON_PROMPT_TEXT="${POST_LESSON_PROMPT_TEXT:-bạn nghe thấy con không}"', text)
        self.assertIn('EXPECTED_TRANSCRIPT="${EXPECTED_TRANSCRIPT:-${INTERRUPT_PROMPT_TEXT}}"', text)
        self.assertIn("child_window_seen_local_prompt=child", text)
        self.assertIn('remote_child_response "${owner_ip}" "${CHILD_RESPONSE_TEXT}"', text)
        self.assertNotIn("sleep 6\n  say -v Daniel 'barn barn barn'", text)
        self.assertIn("physical_smoke_audit.py", text)
        self.assertIn("--production-output-safe-strict", text)
        self.assertNotIn("--production-voice-strict", text)
        self.assertNotIn("--expected-user-transcript", text)
        self.assertNotIn("--expected-post-lesson-transcript", text)
        self.assertIn("--min-interrupts", text)

    def test_runner_waits_for_child_window_before_child_prompt(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn("child_window_seen()", text)
        self.assertIn("lesson_child_response_window_open", text)
        self.assertNotIn('"Google Live user_audio_window_open reason=listen_start"', text)
        child_watch = text[
            text.index("child_window_seen_local_prompt=child") :
            text.index("capture_logs", text.index("child_window_seen_local_prompt=child"))
        ]
        self.assertIn("child_window_seen", child_watch)
        self.assertIn('remote_child_response "${owner_ip}" "${CHILD_RESPONSE_TEXT}"', child_watch)

    def test_runner_prompts_each_child_window_until_lesson_completes(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")
        child_watch = text[
            text.index("child_window_seen_local_prompt=child") :
            text.index("capture_logs", text.index("child_window_seen_local_prompt=child"))
        ]

        self.assertIn("last_child_window_count", child_watch)
        self.assertIn("lesson_completed_seen", child_watch)
        self.assertIn("child_window_watch_complete", child_watch)

    def test_runner_speaks_after_lesson_completed(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")

        self.assertIn('POST_LESSON_PROMPT_TEXT="${POST_LESSON_PROMPT_TEXT:-bạn nghe thấy con không}"', text)
        self.assertIn("lesson_completed_seen_local_prompt=post_lesson", text)
        self.assertIn("sleep 6", text)
        self.assertIn('say -v "${POST_LESSON_PROMPT_VOICE}" "${POST_LESSON_PROMPT_TEXT}"', text)

    def test_runner_does_not_reset_flash_or_use_serial(self):
        script = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "run-voice-goal-proof-local.sh"
        )
        text = script.read_text(encoding="utf-8")

        forbidden_patterns = (
            r"\besptool\b",
            r"\bidf\.py\s+flash\b",
            r"/dev/(?:cu|tty)",
            r"docker\s+restart",
            r"docker\s+compose",
        )
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, text), pattern)


if __name__ == "__main__":
    unittest.main()
