import asyncio
import unittest
from unittest import mock

from core.lesson.interaction_templates import (
    FUN_PATTERN_PROMPTS,
    SafeSpeakingSession,
)
from core.lesson.motion_presets import ALLOWED_MOTION_PRESETS, dispatch_motion_preset
from core.lesson.runtime import LessonRuntime, S_RUNNING, _spoken_step_prompt


class _Forwarder:
    def __init__(self):
        self.batches = []

    def enqueue(self, batch):
        self.batches.append(batch)


class _Voice:
    def __init__(self):
        self.prompts = []

    async def speak_lesson_step_prompt(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        return True

    async def wait_lesson_step_prompt_idle(self):
        return None

    async def open_lesson_child_response_window(self):
        return True

    def close_lesson_child_response_window(self):
        return None


class _Conn:
    def __init__(self):
        self.session_id = "session-1"
        self.features = {"lesson": True, "renderer": ["teebot-lesson-renderer.v1"]}
        self.config = {}
        self.voice_provider = _Voice()
        self.mcp_client = None
        self.lesson_runtime = None


def _safe_step():
    return {
        "id": "s1",
        "type": "teachWord",
        "completionClass": "interactive",
        "expectedResponses": ["barn"],
        "teachingWord": {"text": "BARN"},
        "interaction": {
            "template": "safeSpeaking",
            "maxAttempts": 3,
            "funPattern": "copyMyMove",
        },
        "motion": {
            "present": "teach",
            "listen": "listen",
            "correct": "celebrate",
            "nearMiss": "encourage",
            "incorrect": "tryAgain",
        },
        "storyBeat": {
            "successReaction": "pet.entersBarn",
            "unitGrowth": "farm.friendship.1",
            "nextTease": "What will the pet eat tomorrow?",
        },
        "scene": {},
    }


class SafeSpeakingTemplateTests(unittest.TestCase):
    def test_all_ten_authored_fun_patterns_have_safe_prompts(self):
        self.assertEqual(len(FUN_PATTERN_PROMPTS), 10)
        self.assertEqual(
            set(FUN_PATTERN_PROMPTS),
            {
                "mysteryReveal", "copyMyMove", "sillyChoice", "whisperThenLoud",
                "soundGuess", "missingObject", "robotForgot", "miniStoryRescue",
                "fastSlow", "celebrationFinale",
            },
        )
        step = _safe_step()
        self.assertIn("Copy TeeBot's move", _spoken_step_prompt(step))

    def test_correct_and_brave_try_advance_without_unsafe_language(self):
        session = SafeSpeakingSession(max_attempts=3, target_word="barn")
        for branch in ("correct", "brave_try"):
            decision = session.decide(branch)
            self.assertTrue(decision.advance)
            self.assertNotRegex(decision.prompt.lower(), r"\b(wrong|sai|không đúng)\b")

    def test_incorrect_silence_help_vietnamese_and_stt_failure_reach_fallback_by_three(self):
        for branch in ("incorrect", "silence", "help_or_repeat", "vietnamese_object", "stt_failure"):
            session = SafeSpeakingSession(max_attempts=99, target_word="barn")
            decisions = [session.decide(branch) for _ in range(3)]
            self.assertFalse(decisions[0].advance, branch)
            self.assertFalse(decisions[1].advance, branch)
            self.assertTrue(decisions[2].advance, branch)
            self.assertEqual(session.attempts, 3)
            self.assertEqual(decisions[2].outcome, "modeled_fallback")
            for decision in decisions:
                self.assertNotRegex(decision.prompt.lower(), r"\b(wrong|sai|không đúng)\b")


class SafeSpeakingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def _runtime(self):
        conn = _Conn()
        forwarder = _Forwarder()
        step = _safe_step()
        rt = LessonRuntime(
            conn,
            assignment={
                "assignmentId": "a1", "assignmentVersion": 1, "lessonId": "l1",
                "lessonVersion": 1, "profile": "espTft", "sessionId": "session-1",
            },
            manifest={"manifestVersion": "teebot-lesson-renderer.v1", "steps": [step]},
            asset_cache=object(),
            forwarder=forwarder,
        )
        conn.lesson_runtime = rt
        rt.state = S_RUNNING
        rt._step = step
        rt._step_id = "s1"
        rt._step_seq = 3
        rt._step_acked = True
        rt._step_passive = False
        rt._child_response_window_open = True
        return rt, conn, forwarder

    async def test_step_body_forwards_new_contract_fields(self):
        rt, _conn, _forwarder = self._runtime()
        body = rt._step_body(rt._step)
        self.assertEqual(body["teachingWord"], {"text": "BARN"})
        self.assertEqual(body["interaction"]["template"], "safeSpeaking")
        self.assertEqual(body["motion"]["listen"], "listen")

    async def test_correct_emits_only_categorical_coppa_safe_outcome_and_story(self):
        rt, _conn, forwarder = self._runtime()
        rt._maybe_finish_step = lambda: asyncio.sleep(0)
        self.assertTrue(await rt.on_child_response("barn", source="voice_transcript"))
        events = [event for batch in forwarder.batches for event in batch["events"]]
        completed = next(event for event in events if event["type"] == "step_completed")
        serialized = repr(completed).lower()
        self.assertEqual(completed["result"], "correct")
        for forbidden in ("barn", "transcript", "recognizedtext", "confidence", "score"):
            self.assertNotIn(forbidden, serialized)
        story = next(event for event in events if event["type"] == "story_progress")
        self.assertEqual(story["petReaction"], "pet.entersBarn")
        self.assertEqual(story["unitGrowth"], "farm.friendship.1")
        self.assertNotIn("attendance", repr(story).lower())

    async def test_three_incorrect_answers_model_then_advance(self):
        rt, _conn, forwarder = self._runtime()
        finished = []

        async def finish():
            finished.append(True)

        rt._maybe_finish_step = finish
        for _ in range(3):
            rt._child_response_window_open = True
            self.assertTrue(await rt.on_child_response("cat"))
        self.assertTrue(rt._step_completed)
        self.assertEqual(finished, [True])
        completed = [
            event for batch in forwarder.batches for event in batch["events"]
            if event["type"] == "step_completed"
        ]
        self.assertEqual(completed[0]["result"], "modeled_fallback")

    async def test_typed_stt_failure_uses_same_bounded_state_machine(self):
        rt, _conn, _forwarder = self._runtime()
        rt._maybe_finish_step = lambda: asyncio.sleep(0)
        for _ in range(3):
            rt._child_response_window_open = True
            self.assertTrue(await rt.on_child_response_failure("stt_failure"))
        self.assertTrue(rt._step_completed)

    async def test_motion_dispatch_is_nonblocking_and_failure_does_not_fail_lesson(self):
        rt, _conn, _forwarder = self._runtime()
        blocked = asyncio.Event()

        async def slow_failure(_conn, _preset):
            await blocked.wait()
            raise RuntimeError("uart unavailable")

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=slow_failure):
            rt._dispatch_step_motion("listen")
            await asyncio.sleep(0)
            self.assertEqual(rt.state, S_RUNNING)
            self.assertEqual(len(rt._motion_tasks), 1)
            blocked.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(rt.state, S_RUNNING)

    async def test_legacy_step_keeps_existing_unbounded_retry_behavior(self):
        rt, _conn, _forwarder = self._runtime()
        rt._step.pop("interaction")
        for _ in range(3):
            rt._child_response_window_open = True
            self.assertTrue(await rt.on_child_response("cat"))
        self.assertFalse(rt._step_completed)


class MotionPresetTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_named_presets_are_accepted_and_failure_is_nonfatal(self):
        self.assertEqual(
            ALLOWED_MOTION_PRESETS,
            {"rest", "teach", "presentLeft", "presentRight", "listen", "thinking",
             "encourage", "tryAgain", "celebrate", "goodbye"},
        )
        conn = _Conn()
        for preset in ALLOWED_MOTION_PRESETS:
            self.assertFalse(await dispatch_motion_preset(conn, preset))


if __name__ == "__main__":
    unittest.main()
