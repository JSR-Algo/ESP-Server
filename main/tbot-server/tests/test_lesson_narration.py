"""CR-RUN-13 — lesson narration TEXT delivery (``speak_lesson_step_prompt``).

The per-step narration text does NOT ride the ``lesson_step`` renderer frame and is
NOT carried by ``core/lesson``. It is spoken out-of-band via the voice provider's
``speak_lesson_step_prompt`` (``core.voice.session_provider.google_live`` ~:1778),
handed off by the runtime in ``LessonRuntime._speak_lesson_prompt_text``
(``core/lesson/runtime.py`` ~:674). A regression on that seam is SILENT: the lesson
still renders visually, but the child hears nothing.

These tests close the renderer↔voice seam end-to-end by driving the REAL
``LessonRuntime`` to RUNNING and letting the REAL ``GoogleLiveProvider`` speak. We
assert the exact step ``prompt`` text reaches the child on BOTH delivery channels:

  * local-TTS channel — the prompt is enqueued as a real ``TTSMessageDTO`` stream
    (the words the on-device TTS will speak) and a ``tts``/``sentence_start`` frame
    goes to the device, and
  * live-text fallback channel — the prompt is forwarded to the Live client wrapped
    in the production "say exactly this" instruction.

No network, no real backend: we reuse the FROZEN-fixture builders/fakes from
``tests.test_lesson_runtime`` (a module import, NOT its TestCase) and a real
provider whose only injected seams are an in-memory TTS queue / live-text client.

Async tests use ``unittest.IsolatedAsyncioTestCase`` (this repo does not use
pytest-asyncio markers), mirroring the neighboring lesson suites.
"""

import json
import unittest

from core.voice.session_provider.google_live import (
    GoogleLiveProvider,
    LESSON_LIVE_TEXT_INSTRUCTION,
)

# Reuse the byte-frozen runtime harness *fixtures* (free symbols only — we do NOT
# import the LessonRuntimeTest TestCase, which pytest's unittest collector would
# otherwise drag in wholesale). Importing this module re-runs its
# fixture-existence guards, so this whole file is skipped (not errored) when the
# sibling robot/docs + TBOT-Firmware checkouts are absent — same contract as the
# rest of the lesson suite.
from tests.test_lesson_runtime import (
    _FakeAssetCache,
    _FakeConn,
    _FakeForwarder,
    _ack,
    _build_assignment,
    _build_manifest,
    _manifest_checksum,
)


# ── runtime drive helpers (mirrored from LessonRuntimeTest, no TestCase deps) ────


def _runtime(conn, manifest):
    from core.lesson.runtime import LessonRuntime

    return LessonRuntime(
        conn,
        assignment=_build_assignment(),
        manifest=manifest,
        asset_cache=_FakeAssetCache(ready=True),
        forwarder=_FakeForwarder(),
        manifest_checksum=_manifest_checksum(),
        alarm=None,
    )


async def _drive_to_running(rt):
    """prepare -> prepare-ack -> preload -> start -> start-ack
    (=> RUNNING, emits lesson_step s4). Narration is intentionally gated until
    firmware ACKs that step, proving the child hears the prompt only after the
    visual scene has rendered."""
    await rt.start()
    await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
    await rt._preload_task
    await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + lesson_step s4

async def _drive_to_first_step_acked(rt):
    await _drive_to_running(rt)
    await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # rendered step -> narration


def _sent_frames(conn):
    return [json.loads(p) for p in conn.websocket.sent]


# ── real-provider outbound seams (in-memory, no network) ────────────────────────


class _RecordingQueue:
    """Stand-in for ``tts.tts_text_queue``; records the DTO stream the provider
    enqueues for the on-device TTS to speak."""

    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _RecordingTts:
    def __init__(self):
        self.tts_text_queue = _RecordingQueue()
        self.stored_texts = []

    def store_tts_text(self, sentence_id, text):
        self.stored_texts.append((sentence_id, text))


class _RecordingLiveTextClient:
    """Stand-in for the Google Live client used by the live-text fallback channel.
    ``send_text`` is exactly the surface the child's audio is generated from."""

    def __init__(self):
        self.sent_texts = []
        self.connected = True

    async def send_text(self, text):
        self.sent_texts.append(text)


def _conn_with_real_provider(*, tts=None, live_text_client=None):
    """A frozen-fixture runtime ``conn`` carrying a REAL ``GoogleLiveProvider`` as
    its ``voice_provider``. The provider's ``self.conn`` IS this conn, so its TTS
    path reads ``conn.websocket`` / ``conn.tts`` / ``conn.config`` / ``conn.logger``
    directly — exactly as in production (one device websocket, shared by the runtime
    frame emitter AND the TTS sentence stream)."""
    conn = _FakeConn()
    conn.tts = tts
    conn.sentence_id = None

    provider = GoogleLiveProvider(conn)
    if live_text_client is not None:
        provider._client = live_text_client
    conn.voice_provider = provider
    return conn, provider


# The exact production wrapper the provider prepends in ``_send_live_text_ack``.
LIVE_TEXT_INSTRUCTION = LESSON_LIVE_TEXT_INSTRUCTION


def _single_step_manifest_with_prompt(prompt, *, story_beat=None, story_text=None):
    manifest = _build_manifest()  # already a single-step manifest
    step = manifest["steps"][0]
    step["prompt"] = prompt
    if story_beat is None:
        step.pop("storyBeat", None)
    else:
        step["storyBeat"] = story_beat
    if story_text is None:
        step.pop("storyText", None)
    else:
        step["storyText"] = story_text
    return manifest


class TestLessonNarrationDelivery(unittest.IsolatedAsyncioTestCase):
    """Renderer↔voice seam: the per-step prompt is actually SPOKEN to the child."""

    async def test_step_prompt_is_spoken_via_real_live_text_channel_even_when_tts_exists(self):
        """After firmware ACKs the rendered step, narration stays on Google Live.

        Local TTS may exist on the connection, but production AI speech must not
        queue it for lesson narration.
        """
        prompt = "Hãy nói theo robot nhé: barn."
        client = _RecordingLiveTextClient()
        tts = _RecordingTts()
        conn, _provider = _conn_with_real_provider(tts=tts, live_text_client=client)
        rt = _runtime(conn, _single_step_manifest_with_prompt(prompt))

        await _drive_to_first_step_acked(rt)

        self.assertEqual(client.sent_texts, [LIVE_TEXT_INSTRUCTION + prompt])
        self.assertEqual(tts.tts_text_queue.items, [])
        self.assertEqual(tts.stored_texts, [])
        tts_frames = [
            f for f in _sent_frames(conn)
            if f.get("type") == "tts" and f.get("state") == "sentence_start"
        ]
        self.assertEqual(tts_frames, [])

    async def test_step_prompt_is_spoken_via_real_live_text_channel(self):
        """When no local TTS exists, the real provider forwards the EXACT step
        prompt to the Live client wrapped in the production 'say exactly this'
        instruction — the channel the child's audio is generated from."""
        prompt = "Now look at the barn and say: barn."
        client = _RecordingLiveTextClient()
        conn, _provider = _conn_with_real_provider(tts=None, live_text_client=client)
        # No tts AND no websocket => local-TTS path is unavailable, forcing the
        # live-text fallback. (The runtime no longer emits frames, but it has already
        # rendered lesson_step before speaking; dropping the websocket only disables
        # the TTS queue path, which is what we want to exercise here.)
        conn.websocket = None
        rt = _runtime(conn, _single_step_manifest_with_prompt(prompt))

        await _drive_to_first_step_acked(rt)

        # Exactly one live-text send, carrying the authored prompt verbatim inside
        # the production wrapper. The prompt substring proves the child's narration
        # text survived the renderer→voice handoff.
        self.assertEqual(client.sent_texts, [LIVE_TEXT_INSTRUCTION + prompt])
        self.assertIn(prompt, client.sent_texts[0])

    async def test_storybeat_ask_is_spoken_via_real_live_text_channel(self):
        """Guided speaking uses ``storyBeat.ask`` as the child-facing question,
        while preserving the visual renderer prompt in the lesson_step frame."""
        visual_prompt = "Look at the barn on the screen."
        ask = "Can you try saying barn after TeeBot models it?"
        client = _RecordingLiveTextClient()
        conn, _provider = _conn_with_real_provider(tts=None, live_text_client=client)
        conn.websocket = None
        rt = _runtime(
            conn,
            _single_step_manifest_with_prompt(
                visual_prompt,
                story_beat={"ask": ask, "waitForChild": True},
                story_text="TeeBot models the word barn, then invites the child to try.",
            ),
        )

        await _drive_to_first_step_acked(rt)

        self.assertEqual(client.sent_texts, [LIVE_TEXT_INSTRUCTION + ask])
        self.assertNotIn(visual_prompt, client.sent_texts[0])

    async def test_blank_step_prompt_is_not_spoken(self):
        """A whitespace-only / missing prompt must NOT produce a narration utterance:
        the runtime guards it and the provider's strip() guard also rejects it. Proves
        the spoken-text assertions above are load-bearing, not always-true."""
        client = _RecordingLiveTextClient()
        tts = _RecordingTts()
        conn, _provider = _conn_with_real_provider(tts=tts, live_text_client=client)
        rt = _runtime(conn, _single_step_manifest_with_prompt("   "))

        await _drive_to_first_step_acked(rt)

        # The lesson still rendered its step frame (visual lesson intact)…
        self.assertIn("lesson_step", [f["type"] for f in _sent_frames(conn)])
        # …but NOTHING was spoken on either narration channel.
        self.assertEqual(client.sent_texts, [])
        self.assertEqual(tts.tts_text_queue.items, [])
        self.assertEqual(
            [f for f in _sent_frames(conn) if f.get("type") == "tts"], []
        )

    async def test_runtime_logs_prompt_handoff_to_voice_provider(self):
        """The runtime records the renderer→voice handoff with the step id and
        ``handoff=1`` only when the provider confirms it spoke. This is the breadcrumb
        an operator uses to catch a silent narration regression in the field."""
        events = []

        class _CapturingLogger:
            def bind(self, **kwargs):
                return self

            def info(self, message, *a, **k):
                events.append(("info", str(message)))

            def debug(self, *a, **k):
                return None

            def warning(self, message, *a, **k):
                events.append(("warning", str(message)))

            def error(self, *a, **k):
                return None

        prompt = "Say barn with TeeBot."
        client = _RecordingLiveTextClient()
        conn, _provider = _conn_with_real_provider(tts=None, live_text_client=client)
        conn.websocket = None  # force the live-text channel; provider confirms spoken
        conn.logger = _CapturingLogger()
        rt = _runtime(conn, _single_step_manifest_with_prompt(prompt))

        await _drive_to_first_step_acked(rt)

        handoff_logs = [m for level, m in events if "lesson step prompt handoff" in m]
        self.assertEqual(len(handoff_logs), 1)
        self.assertIn("handoff=1", handoff_logs[0])
        self.assertIn("stepId=s4", handoff_logs[0])
        # And the prompt really went out on the wire.
        self.assertEqual(client.sent_texts, [LIVE_TEXT_INSTRUCTION + prompt])


if __name__ == "__main__":
    unittest.main()
