"""T2.3 — ESP voice-pipeline integration during lessons.

Two invariants, verified **independently per pipeline** (campaign ground rule 1:
Google Live and ``classic_pipeline`` are separate; a fix in one must not leak
into the other):

1. **Output-queue discipline / audio-overlap invariant.** While a lesson owns the
   speaker (``session_mode == LESSON`` or a lesson runtime in ``PRELOADING`` /
   ``RUNNING``), voice-pipeline audio must never stream to the device — it would
   mix with the firmware's lesson audio coming off the SD pack.

   * Google Live already enforces this upstream in
     ``GoogleLiveAudioBridge._should_drop_lesson_model_output`` — locked here as a
     regression test.
   * The classic pipeline enforces it in ``sendAudioHandle.sendAudioMessage``,
     which is the single funnel every classic TTS sentence passes through
     (``core/providers/tts/base.py`` worker, ``helloHandle``, intent ``speak_txt``).

2. **Vietnamese diacritic / tone folding on child answers.** The Vietnamese letter
   ``đ`` (U+0111) has no combining-mark decomposition, so NFKD does not fold it to
   ``d``. ``GoogleLiveProvider._normalize_intent_text`` already maps it explicitly
   for the *trigger* phrase; the in-lesson *answer* classifier
   (``runtime._matching_tokens``) must fold it the same way, otherwise an
   accent-stripped STT transcript is routed to the wrong coaching branch.
"""

import unittest
from unittest.mock import AsyncMock, patch

from core.handle import sendAudioHandle
from core.lesson.runtime import (
    CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON,
    CHILD_RESPONSE_INTENT_HELP_OR_REPEAT,
    CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED,
    _classify_child_response_intent,
)
from core.providers.tts.dto.dto import SentenceType
from core.voice.session_orchestrator import SessionMode


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Logger:
    def __init__(self):
        self.lines = []

    def bind(self, **_kwargs):
        return self

    def info(self, message, *args, **_kwargs):
        self.lines.append(str(message))

    debug = info
    warning = info
    error = info


class _Runtime:
    """Minimal lesson-runtime stand-in: only the fields the gate reads."""

    def __init__(self, state="RUNNING"):
        self.state = state


class _Conn:
    def __init__(self):
        self.config = {"enable_stop_tts_notify": False}
        self.tts = type("Tts", (), {"tts_audio_first_sentence": False})()
        self.session_id = "session-1"
        self.sentence_id = "sentence-1"
        self.client_abort = False
        self.client_is_speaking = False
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0
        self.close_after_chat = False
        self.session_mode = SessionMode.DORMANT
        self.lesson_runtime = None
        self.websocket = _WebSocket()
        self.logger = _Logger()

    def clearSpeakStatus(self):
        self.client_is_speaking = False

    async def close(self):
        return None


class ClassicLessonAudioOverlapInvariantTest(unittest.IsolatedAsyncioTestCase):
    """Invariant 1 — classic pipeline half."""

    async def _send_one_sentence(self, conn):
        with patch.object(sendAudioHandle, "sendAudio", new=AsyncMock()) as send_audio:
            await sendAudioHandle.sendAudioMessage(
                conn, SentenceType.FIRST, [b"opus-frame"], "xin chào"
            )
        return send_audio

    async def test_classic_audio_streams_when_no_lesson_owns_the_speaker(self):
        """Control: outside a lesson the classic path is untouched."""
        conn = _Conn()

        send_audio = await self._send_one_sentence(conn)

        send_audio.assert_awaited_once()
        self.assertTrue(
            any(b'"state": "start"' in payload.encode() for payload in conn.websocket.sent)
        )

    async def test_classic_audio_blocked_while_lesson_session_mode_active(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON

        send_audio = await self._send_one_sentence(conn)

        send_audio.assert_not_awaited()
        self.assertEqual(conn.websocket.sent, [])
        self.assertFalse(conn.client_is_speaking)

    async def test_classic_audio_blocked_while_lesson_runtime_is_running(self):
        """A runtime mid-step blocks even if session_mode has not been flipped."""
        conn = _Conn()
        conn.lesson_runtime = _Runtime("RUNNING")

        send_audio = await self._send_one_sentence(conn)

        send_audio.assert_not_awaited()
        self.assertEqual(conn.websocket.sent, [])

    async def test_classic_audio_blocked_while_lesson_runtime_is_preloading(self):
        conn = _Conn()
        conn.lesson_runtime = _Runtime("PRELOADING")

        send_audio = await self._send_one_sentence(conn)

        send_audio.assert_not_awaited()
        self.assertEqual(conn.websocket.sent, [])

    async def test_terminal_lesson_runtime_does_not_block_classic_audio(self):
        """COMPLETED / FAILED runtimes no longer own the speaker."""
        for state in ("COMPLETED", "FAILED", "PAUSED", "IDLE"):
            with self.subTest(state=state):
                conn = _Conn()
                conn.lesson_runtime = _Runtime(state)

                send_audio = await self._send_one_sentence(conn)

                send_audio.assert_awaited_once()

    async def test_lesson_layer_can_open_the_gate_for_its_own_narration(self):
        """The gate is an output-queue discipline, not a hard mute: the lesson
        layer opens it for server-authored lesson narration, exactly like
        ``google_live_lesson_prompt_output_allowed`` does on the Live side."""
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.lesson_runtime = _Runtime("RUNNING")
        conn.lesson_prompt_output_allowed = True

        send_audio = await self._send_one_sentence(conn)

        send_audio.assert_awaited_once()

    async def test_blocked_sentence_does_not_leave_device_stuck_speaking(self):
        """A blocked LAST sentence must not leave ``client_is_speaking`` latched."""
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.client_is_speaking = False

        with patch.object(sendAudioHandle, "sendAudio", new=AsyncMock()):
            await sendAudioHandle.sendAudioMessage(
                conn, SentenceType.LAST, [b"opus-frame"], "hết"
            )

        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(conn.websocket.sent, [])


class GoogleLiveLessonAudioOverlapInvariantTest(unittest.IsolatedAsyncioTestCase):
    """Invariant 1 — Google Live half (regression lock; already enforced).

    Proves the classic-side gate did not leak: the Live model-output drop rule is
    unchanged and is still the thing that keeps Live audio off the wire during a
    lesson.
    """

    def _bridge(self, conn):
        from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

        return GoogleLiveAudioBridge.__new__(GoogleLiveAudioBridge)

    def _drop(self, *, session_mode, runtime_state, prompt_allowed, event_type="audio"):
        from core.voice.google_live import audio_bridge as bridge_module

        conn = _Conn()
        conn.session_mode = session_mode
        conn.lesson_runtime = _Runtime(runtime_state) if runtime_state else None
        conn.google_live_lesson_prompt_output_allowed = prompt_allowed
        bridge = self._bridge(conn)
        bridge.conn = conn
        bridge.logger = conn.logger
        bridge._active_response_id = 1
        bridge._response_id_getter = lambda: 1
        return bridge_module.GoogleLiveAudioBridge._should_drop_lesson_model_output(
            bridge, event_type
        )

    def test_live_model_audio_dropped_during_lesson(self):
        self.assertTrue(
            self._drop(
                session_mode=SessionMode.LESSON,
                runtime_state="RUNNING",
                prompt_allowed=False,
            )
        )

    def test_live_lesson_narration_passes_when_server_authored(self):
        self.assertFalse(
            self._drop(
                session_mode=SessionMode.LESSON,
                runtime_state="RUNNING",
                prompt_allowed=True,
            )
        )

    def test_live_conversation_audio_untouched_outside_lesson(self):
        self.assertFalse(
            self._drop(
                session_mode=SessionMode.CONVERSATION,
                runtime_state=None,
                prompt_allowed=False,
            )
        )


class ChildAnswerDiacriticFoldingTest(unittest.TestCase):
    """Invariant 2 — accent-stripped STT must reach the same coaching branch.

    Pipeline-neutral: ``_classify_child_response_intent`` is the shared classifier
    both providers' transcripts land in via ``runtime.on_child_response``.
    """

    EXPECTED = ["barn"]

    def test_lesson_restart_request_matches_without_diacritics(self):
        """§1 rule 6 trigger phrase, as an in-lesson utterance."""
        self.assertEqual(
            _classify_child_response_intent("bắt đầu bài học", self.EXPECTED),
            CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON,
        )
        self.assertEqual(
            _classify_child_response_intent("bat dau bai hoc", self.EXPECTED),
            CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON,
        )

    def test_repeat_request_matches_without_diacritics(self):
        for text in ("đọc lại", "doc lai", "Doc Lai"):
            with self.subTest(text=text):
                self.assertEqual(
                    _classify_child_response_intent(text, self.EXPECTED),
                    CHILD_RESPONSE_INTENT_HELP_OR_REPEAT,
                )

    def test_frustration_matches_without_diacritics(self):
        for text in ("con không làm được", "con khong lam duoc"):
            with self.subTest(text=text):
                self.assertEqual(
                    _classify_child_response_intent(text, self.EXPECTED),
                    CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED,
                )

    def test_tone_variants_still_fold_together(self):
        """Tone marks were already folded; keep that true after the đ fix."""
        for text in ("bắt đầu bài học", "bât đâu bai hoc", "bat dâu bài học"):
            with self.subTest(text=text):
                self.assertEqual(
                    _classify_child_response_intent(text, self.EXPECTED),
                    CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON,
                )

    def test_expected_answer_with_d_stroke_accepts_stripped_transcript(self):
        from core.lesson.runtime import _child_response_matches_expected

        self.assertTrue(_child_response_matches_expected("đi", ["đi"]))
        self.assertTrue(_child_response_matches_expected("di", ["đi"]))
        self.assertTrue(_child_response_matches_expected("đi", ["di"]))

    def test_folding_does_not_collapse_unrelated_answers(self):
        """d/đ folding must not make a wrong answer look correct."""
        from core.lesson.runtime import _child_response_matches_expected

        self.assertFalse(_child_response_matches_expected("con mèo", ["barn"]))
        self.assertFalse(_child_response_matches_expected("dê", ["đá"]))


if __name__ == "__main__":
    unittest.main()
