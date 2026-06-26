import json
import unittest

from core.lesson.sample import (
    INTERACTIVE_SAMPLE_LESSON_ID,
    SAMPLE_ASSIGNMENT_ID,
    SAMPLE_LESSON_ID,
    NoOpLessonForwarder,
    SampleAssetCache,
    build_interactive_sample_manifest,
    build_sample_manifest,
    start_sample_lesson,
)
from core.lesson.runtime import LessonRuntime
from core.voice.session_provider.google_live import GoogleLiveProvider


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def error(self, *a, **k):
        return None


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _FakeConn:
    """Minimal connection that records the finish/release hooks the runtime calls."""

    def __init__(self, config=None):
        self.logger = _DummyLogger()
        self.websocket = _FakeWS()
        self.session_id = "sess-sample-1"
        self.device_id = "device-sample-1"
        self.features = {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
        self.config = config if config is not None else {}
        self.lesson_runtime = None
        self.lesson_start_status = None
        self.finished = []
        self.released = []
        self.entered = []
        self.voice_provider = None

    async def enter_lesson_mode(self, *, reason="lesson_start"):
        self.entered.append(reason)

    async def finish_lesson_mode(self, *, reason="lesson_completed"):
        self.finished.append(reason)

    async def release_lesson_mode(self, *, reason="lesson_terminal"):
        self.released.append(reason)

    def is_realtime_busy(self):
        return False


def _ack(conn, acks, env_seq, *, step_id=None):
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": SAMPLE_ASSIGNMENT_ID,
        "sessionId": conn.session_id,
        "lessonId": SAMPLE_LESSON_ID,
        "lessonVersion": 1,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": {"acks": acks, "rendered": True, "degraded": False},
    }


class SampleManifestTest(unittest.TestCase):
    def test_all_steps_passive_with_nonblank_three_layer_srcs(self):
        manifest = build_sample_manifest()
        self.assertEqual(manifest["manifestVersion"], "teebot-lesson-renderer.v1")
        self.assertEqual(manifest["profile"], "espTft")
        steps = manifest["steps"]
        self.assertGreaterEqual(len(steps), 2)
        for step in steps:
            # Every step MUST be passive so it auto-advances without child interaction.
            self.assertEqual(step["completionClass"], "passive", step["id"])
            scene = step["scene"]
            for src in (
                scene["backgroundScene"]["poster"]["src"],
                scene["teachingObject"]["asset"]["src"],
                scene["robotOverlay"]["asset"]["src"],
            ):
                self.assertTrue(isinstance(src, str) and src.strip(), step["id"])

    def test_asset_base_prefixes_layer_sources(self):
        manifest = build_sample_manifest("http://assets.test/sample")
        src = manifest["steps"][0]["scene"]["backgroundScene"]["poster"]["src"]
        self.assertTrue(src.startswith("http://assets.test/sample/"))

    def test_passthrough_asset_cache_surface(self):
        cache = SampleAssetCache()
        self.assertEqual(cache.public_url_for_source("barn.png"), "barn.png")
        self.assertEqual(cache.synthesize_preload_status(1)["ready"], True)
        self.assertIsNone(cache.assert_profile_renderable())
        self.assertTrue(isinstance(cache.preload_timeout_sec, float))


class SampleManifestTestAsync(unittest.IsolatedAsyncioTestCase):
    async def test_passthrough_cache_preload_ready(self):
        cache = SampleAssetCache()
        self.assertTrue(await cache.preload())
        await cache.aclose()
        forwarder = NoOpLessonForwarder()
        forwarder.enqueue({"events": []})
        await forwarder.aclose()
        self.assertFalse(await forwarder.replay_pending_terminal_event())


class SampleLessonDriveTest(unittest.IsolatedAsyncioTestCase):
    async def test_sample_lesson_plays_all_steps_to_completed_then_finishes(self):
        conn = _FakeConn()
        # dwell_sec=0 -> passive steps advance immediately on ack (no per-step pacing);
        # this exercises the completion path. Pacing is covered separately below.
        manifest = build_sample_manifest(dwell_sec=0)
        rt = LessonRuntime(
            conn,
            assignment={
                "assignmentId": SAMPLE_ASSIGNMENT_ID,
                "assignmentVersion": 1,
                "lessonId": SAMPLE_LESSON_ID,
                "lessonVersion": 1,
                "profile": "espTft",
                "sessionId": conn.session_id,
                "manifestChecksum": "sample",
            },
            manifest=manifest,
            asset_cache=SampleAssetCache(),
            forwarder=NoOpLessonForwarder(),
            manifest_checksum="sample",
            alarm=None,
        )

        await rt.start()  # lesson_prepare (S->F seq 1)
        await rt.on_lesson_ack(_ack(conn, 1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(conn, 2, 2))  # start-ack -> emit s1 (seq 3)

        # Each passive step auto-advances on its render ack. S->F seqs: s1=3..s4=6.
        step_ids = [s["id"] for s in manifest["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_ack(conn, 3 + i, 3 + i, step_id=sid))

        # After the last step's ack the runtime emits lesson_stop (S->F seq 3+N);
        # its ack drives COMPLETED.
        stop_seq = 3 + len(step_ids)
        await rt.on_lesson_ack(_ack(conn, stop_seq, stop_seq))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, len(step_ids))
        # Successful completion routes to the happy-face + conversation handler.
        self.assertEqual(conn.finished, ["lesson_completed"])
        self.assertEqual(conn.released, [])

        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types[0], "lesson_prepare")
        self.assertIn("lesson_start", sent_types)
        self.assertEqual(sent_types.count("lesson_step"), len(step_ids))
        self.assertEqual(sent_types[-1], "lesson_stop")

    async def test_passive_dwell_delays_auto_advance_then_completes(self):
        import asyncio

        conn = _FakeConn()
        # Tiny non-zero dwell: the step must NOT advance immediately on its ack; it
        # advances only after the dwell elapses. Verifies pacing without flakiness.
        manifest = build_sample_manifest(dwell_sec=0.02)
        rt = LessonRuntime(
            conn,
            assignment={
                "assignmentId": SAMPLE_ASSIGNMENT_ID, "assignmentVersion": 1,
                "lessonId": SAMPLE_LESSON_ID, "lessonVersion": 1, "profile": "espTft",
                "sessionId": conn.session_id, "manifestChecksum": "sample",
            },
            manifest=manifest, asset_cache=SampleAssetCache(),
            forwarder=NoOpLessonForwarder(), manifest_checksum="sample", alarm=None,
        )

        await rt.start()
        await rt.on_lesson_ack(_ack(conn, 1, 1))      # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(conn, 2, 2))      # start-ack -> emit s1 (seq 3)

        step_ids = [s["id"] for s in manifest["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_ack(conn, 3 + i, 3 + i, step_id=sid))
            # Immediately after the ack the step is acked but NOT yet completed — the
            # dwell is pending, so the next step has not been emitted.
            self.assertTrue(rt._step_acked)
            self.assertFalse(rt._step_completed)
            self.assertIsNotNone(rt._passive_dwell_task)
            # Let the dwell elapse so the runtime advances to the next step / stop.
            await asyncio.sleep(0.05)

        stop_seq = 3 + len(step_ids)
        await rt.on_lesson_ack(_ack(conn, stop_seq, stop_seq))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, len(step_ids))
        self.assertEqual(conn.finished, ["lesson_completed"])
        # Every sample step carried a dwellSec so the scene paces on the device.
        self.assertTrue(all(s.get("dwellSec") == 0.02 for s in manifest["steps"]))

    async def test_start_sample_lesson_refuses_under_sd_pack_mode(self):
        conn = _FakeConn(config={"lesson": {"asset_delivery_mode": "sd_pack"}})

        runtime = await start_sample_lesson(conn)

        self.assertIsNone(runtime)
        self.assertEqual(conn.lesson_start_status["code"], "SAMPLE_SD_PACK_UNSUPPORTED")
        self.assertEqual(conn.entered, [])  # never entered lesson mode

    async def test_start_sample_lesson_enters_lesson_mode_and_emits_prepare(self):
        conn = _FakeConn()

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertIs(conn.lesson_runtime, runtime)
        self.assertEqual(conn.entered, ["sample_lesson_start"])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types[0], "lesson_prepare")

    async def test_start_sample_lesson_serializes_on_shared_lesson_pull_lock(self):
        # The sample must acquire the SAME per-connection _lesson_pull_lock the
        # connect-time assignment pull uses, so the two can never double-drive the
        # device. Hold the lock (as if a pull is mid-flight) and assert the sample
        # blocks — no lesson_prepare, no enter_lesson_mode — until it is released.
        import asyncio

        conn = _FakeConn()
        lock = asyncio.Lock()
        conn._lesson_pull_lock = lock
        await lock.acquire()

        task = asyncio.ensure_future(start_sample_lesson(conn))
        await asyncio.sleep(0)  # let the task reach the lock and block

        self.assertFalse(task.done())
        self.assertEqual(conn.entered, [])
        self.assertEqual(conn.websocket.sent, [])

        lock.release()
        runtime = await task

        self.assertIsNotNone(runtime)
        self.assertEqual(conn.entered, ["sample_lesson_start"])
        # The shared lock object was reused, not replaced.
        self.assertIs(conn._lesson_pull_lock, lock)


# ── END-TO-END: interactive speaking lesson over the REAL GoogleLiveProvider ─────


class _RecordingQueue:
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


class _FakeLiveClient:
    """Stand-in for the connected Google Live client (live-text fallback surface)."""

    connected = True

    def __init__(self):
        self.sent_texts = []

    async def send_text(self, text):
        self.sent_texts.append(text)

    async def close(self):
        return None

    async def interrupt(self):
        return None

    async def end_audio_stream(self):
        return None


class _RealProviderConn(_FakeConn):
    """A sample-lesson conn that ALSO carries a real GoogleLiveProvider as its
    voice_provider, with the provider-side surface its lesson hooks read."""

    def __init__(self):
        super().__init__(
            config={
                "google_live": {"lesson_child_response_window_sec": 25.0},
                "child_profile": {"child_name": "Bong"},
                # dwell 0 -> passive steps auto-advance immediately on their ack, so the
                # interactive SAY-IT step is the ONLY one that waits (for the child voice).
                "lesson": {
                    "sample_lesson": True,
                    "sample_mode": "interactive",
                    "sample_step_dwell_sec": 0,
                },
            }
        )
        self.tts = _RecordingTts()
        self.sentence_id = None
        self.func_handler = None
        self.client_abort = False

    def _lesson_runtime_enabled(self):
        return True


def _iack(conn, acks, env_seq, *, step_id=None):
    """lesson_ack carrying the INTERACTIVE sample's lessonId."""
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": SAMPLE_ASSIGNMENT_ID,
        "sessionId": conn.session_id,
        "lessonId": INTERACTIVE_SAMPLE_LESSON_ID,
        "lessonVersion": 1,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": {"acks": acks, "rendered": True, "degraded": False},
    }


class InteractiveSampleSpeakingE2ETest(unittest.IsolatedAsyncioTestCase):
    """Proves the WHOLE speaking-lesson arc end-to-end on the REAL provider:
    start_lesson (sample, interactive) -> Gemini narration (local-TTS) -> the SAY-IT
    step opens a child-response window and WAITS -> the child SPEAKS the word, whose
    Live transcript the real provider routes to runtime.on_child_response -> the step
    advances -> completion -> happy face + return to conversation (finish_lesson_mode).
    """

    async def test_full_speaking_flow_child_says_word_then_completes_happy(self):
        conn = _RealProviderConn()
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient()
        conn.voice_provider = provider

        # start_sample_lesson(interactive) builds the runtime, enters lesson mode (face
        # off on the device), and emits lesson_prepare.
        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)
        self.assertEqual(conn.entered, ["sample_lesson_start"])

        manifest = build_interactive_sample_manifest()
        step_ids = [s["id"] for s in manifest["steps"]]
        interactive_ids = {
            s["id"] for s in manifest["steps"] if s["completionClass"] == "interactive"
        }
        self.assertTrue(interactive_ids, "the interactive sample must have a SAY-IT step")

        # prepare-ack -> preload -> start-ack -> emit s1 (seq 3)
        await rt.on_lesson_ack(_iack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_iack(conn, 2, 2))

        child_routed = []
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_iack(conn, 3 + i, 3 + i, step_id=sid))
            if sid in interactive_ids:
                # The runtime narrated the prompt (local-TTS) and opened the child window;
                # the interactive step must NOT auto-advance.
                self.assertFalse(rt._step_completed, f"{sid} auto-advanced (should wait)")
                self.assertGreater(
                    provider._user_audio_allowed_until,
                    0.0,
                    "child-response window did not open",
                )
                self.assertGreaterEqual(
                    len(conn.tts.tts_text_queue.items), 3,
                    "interactive prompt was not narrated to the child",
                )
                # The child SPEAKS -> Live transcript -> real provider routes it to the
                # runtime, which completes the step. No chat/model forwarding.
                handled = await provider._on_user_transcript("barn")
                self.assertTrue(handled, f"child answer for {sid} was not routed")
                self.assertEqual(provider._client.sent_texts, [])
                child_routed.append(sid)

        # last step's ack -> lesson_stop; its ack -> COMPLETED
        stop_seq = 3 + len(step_ids)
        await rt.on_lesson_ack(_iack(conn, stop_seq, stop_seq))

        # End state: completed, happy-face + conversation (finish), never released/dormant.
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, len(step_ids))
        self.assertEqual(conn.finished, ["lesson_completed"])
        self.assertEqual(conn.released, [])
        self.assertEqual(child_routed, sorted(interactive_ids))

        # The child actually heard the SAY-IT prompt verbatim, and only the interactive
        # step waited on a spoken answer.
        spoken = [text for (_sid, text) in conn.tts.stored_texts]
        self.assertIn("Bây giờ con hãy nói theo mình nào: barn!", spoken)

        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types[0], "lesson_prepare")
        self.assertEqual(sent_types.count("lesson_step"), len(step_ids))
        self.assertEqual(sent_types[-1], "lesson_stop")

    async def test_passive_sample_does_not_wait_for_child(self):
        """Control: the DEFAULT (passive) sample completes with NO child-response window
        and NO transcript routing — proving the interactive wait above is load-bearing."""
        conn = _RealProviderConn()
        conn.config["lesson"]["sample_mode"] = "passive"
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient()
        conn.voice_provider = provider

        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)
        # passive sample uses the all-passive lessonId
        await rt.on_lesson_ack(_ack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(conn, 2, 2))
        step_ids = [s["id"] for s in build_sample_manifest(dwell_sec=0)["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_ack(conn, 3 + i, 3 + i, step_id=sid))
        stop_seq = 3 + len(step_ids)
        await rt.on_lesson_ack(_ack(conn, stop_seq, stop_seq))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(conn.finished, ["lesson_completed"])
        # No interactive window ever opened.
        self.assertEqual(provider._user_audio_allowed_until, 0.0)


if __name__ == "__main__":
    unittest.main()
