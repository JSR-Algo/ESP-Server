import asyncio
import json
import unittest

from core.lesson.sample import (
    DEFAULT_SAMPLE_STEP_DWELL_SEC,
    DEFAULT_SAMPLE_STEP_TIMEOUT_SEC,
    INTERACTIVE_SAMPLE_LESSON_ID,
    SAMPLE_ASSIGNMENT_ID,
    SAMPLE_LESSON_ID,
    NoOpLessonForwarder,
    SampleAssetCache,
    _step,
    build_interactive_sample_manifest,
    build_sample_manifest,
    start_sample_lesson,
)
from core.lesson.runtime import LessonRuntime
from core.voice.session_provider.google_live import (
    GoogleLiveProvider,
    LESSON_LIVE_TEXT_INSTRUCTION,
)
from core.voice.child_safety import screen_model_output


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
        scene = manifest["steps"][0]["scene"]
        self.assertEqual(
            scene["backgroundScene"]["poster"]["src"],
            "http://assets.test/sample/assets/background/barn-round-field-poster.jpg",
        )
        self.assertEqual(
            scene["teachingObject"]["asset"]["src"],
            "http://assets.test/sample/assets/objects/barn.png",
        )
        self.assertEqual(
            scene["robotOverlay"]["asset"]["src"],
            "http://assets.test/sample/assets/robot/poses/bright-teach.png",
        )

    def test_asset_base_trims_direct_input_before_layer_sources(self):
        manifest = build_sample_manifest("  http://assets.test/sample/  ")
        scene = manifest["steps"][0]["scene"]

        self.assertEqual(
            scene["backgroundScene"]["poster"]["src"],
            "http://assets.test/sample/assets/background/barn-round-field-poster.jpg",
        )

    def test_sample_manifest_rejects_non_finite_direct_dwell(self):
        manifest = build_sample_manifest(dwell_sec=float("inf"))

        json.dumps(manifest, allow_nan=False)
        self.assertTrue(all("dwellSec" not in step for step in manifest["steps"]))

    def test_sample_manifest_rejects_malformed_direct_dwell(self):
        manifest = build_sample_manifest(dwell_sec="not-a-number")

        json.dumps(manifest, allow_nan=False)
        self.assertTrue(all("dwellSec" not in step for step in manifest["steps"]))

    def test_sample_step_rejects_non_finite_direct_timing_fields(self):
        scene = build_sample_manifest()["steps"][0]["scene"]

        step = _step(
            "s3",
            "repeat",
            "Say barn.",
            scene,
            timeout_sec=float("inf"),
            completion_class="interactive",
            response_timeout_sec=float("inf"),
        )

        json.dumps(step, allow_nan=False)
        self.assertEqual(step["timeoutSec"], DEFAULT_SAMPLE_STEP_TIMEOUT_SEC)
        self.assertNotIn("responseTimeoutSec", step)

    def test_sample_step_rejects_non_finite_direct_attempt_count(self):
        scene = build_sample_manifest()["steps"][0]["scene"]

        try:
            step = _step(
                "s3",
                "repeat",
                "Say barn.",
                scene,
                completion_class="interactive",
                max_no_answer_attempts=float("inf"),
            )
        except OverflowError as exc:
            self.fail(f"non-finite maxNoAnswerAttempts should be ignored, got {type(exc).__name__}")

        json.dumps(step, allow_nan=False)
        self.assertNotIn("maxNoAnswerAttempts", step)

    def test_passthrough_asset_cache_surface(self):
        cache = SampleAssetCache()
        self.assertEqual(cache.public_url_for_source("barn.png"), "barn.png")
        self.assertEqual(cache.synthesize_preload_status(1)["ready"], True)
        self.assertIsNone(cache.assert_profile_renderable())
        self.assertTrue(isinstance(cache.preload_timeout_sec, float))

    def test_interactive_sample_finishes_with_lesson_completion_announcement(self):
        manifest = build_interactive_sample_manifest()

        self.assertEqual(manifest["steps"][-1]["completionClass"], "interactive")
        self.assertNotIn("barn", manifest["steps"][-1]["prompt"].lower())
        self.assertIn("hoàn thành bài học mẫu", manifest["steps"][-1]["successPrompt"])

    def test_interactive_sample_avoids_final_celebrate_render_step(self):
        manifest = build_interactive_sample_manifest()

        self.assertNotIn("celebrate", [step["type"] for step in manifest["steps"]])
        self.assertEqual(manifest["steps"][-1]["id"], "s4")

    def test_interactive_sample_teaches_vocabulary_through_multiple_child_turns(self):
        manifest = build_interactive_sample_manifest()
        interactive_steps = [
            step for step in manifest["steps"]
            if step.get("completionClass") == "interactive"
        ]

        self.assertGreaterEqual(len(interactive_steps), 2)
        prompts = " ".join(step["prompt"] for step in interactive_steps).lower()
        self.assertIn("nói theo", prompts)
        self.assertIn("barn", prompts)
        self.assertIn("con thấy", prompts)

        for step in interactive_steps:
            self.assertEqual(step.get("expectedResponses"), ["barn"], step["id"])
            self.assertGreaterEqual(step.get("responseTimeoutSec", 0), 30.0, step["id"])
            self.assertGreaterEqual(step.get("maxNoAnswerAttempts", 0), 3, step["id"])

    def test_interactive_sample_marks_child_questions_with_storybeat(self):
        manifest = build_interactive_sample_manifest()

        for step in manifest["steps"]:
            if step.get("completionClass") != "interactive":
                continue
            story_beat = step.get("storyBeat") or {}
            self.assertIs(story_beat.get("waitForChild"), True, step["id"])
            self.assertEqual(story_beat.get("ask"), step["prompt"], step["id"])

    def test_interactive_sample_has_child_centered_production_ux_copy_and_scene_metadata(self):
        manifest = build_interactive_sample_manifest()

        self.assertEqual([step["type"] for step in manifest["steps"]], [
            "greeting",
            "focus",
            "repeat",
            "recall",
        ])

        first_prompt = manifest["steps"][0]["prompt"].lower()
        self.assertIn("nhìn hình", first_prompt)
        self.assertIn("nghe", first_prompt)
        self.assertIn("mời", first_prompt)

        focus_prompt = manifest["steps"][1]["prompt"]
        self.assertIn("barn", focus_prompt)
        self.assertIn("cái kho", focus_prompt.lower())
        self.assertLessEqual(max(len(step["prompt"]) for step in manifest["steps"]), 135)

        for step in manifest["steps"]:
            scene = step["scene"]
            self.assertIn("cái kho", scene["backgroundScene"]["altCaption"].lower(), step["id"])
            self.assertIn("cái kho", scene["teachingObject"]["supportWords"], step["id"])
            self.assertEqual(scene["teachingObject"]["primitiveFallbackCard"]["label"], "barn")
            self.assertEqual(scene["teachingObject"]["placement"]["anchor"], "center")
            self.assertEqual(scene["robotOverlay"]["anchor"], "bottomLeft")

        retry_prompts = " ".join(
            step.get("retryPrompt", "") for step in manifest["steps"]
            if step.get("completionClass") == "interactive"
        ).lower()
        self.assertIn("chưa đúng", retry_prompts)
        self.assertIn("nhìn hình", retry_prompts)
        self.assertIn("barn", retry_prompts)

        for step in manifest["steps"]:
            if step.get("completionClass") != "interactive":
                continue
            prompts = step.get("interactionPrompts") or {}
            self.assertIn("helpOrRepeat", prompts, step["id"])
            self.assertIn("unknownOrFrustrated", prompts, step["id"])
            self.assertIn("vietnameseObject", prompts, step["id"])
            self.assertIn("alreadyInLesson", prompts, step["id"])
            self.assertIn("barn", prompts["helpOrRepeat"], step["id"])
            self.assertIn("không sao", prompts["unknownOrFrustrated"].lower(), step["id"])
            self.assertIn("cái kho", prompts["vietnameseObject"].lower(), step["id"])
            self.assertIn("đang học", prompts["alreadyInLesson"].lower(), step["id"])

    def test_interactive_sample_copy_is_short_for_fast_child_turns(self):
        manifest = build_interactive_sample_manifest()

        for step in manifest["steps"]:
            self.assertLessEqual(len(step["prompt"]), 75, step["id"])
            if step.get("retryPrompt"):
                self.assertLessEqual(len(step["retryPrompt"]), 55, step["id"])
            for key, prompt in (step.get("interactionPrompts") or {}).items():
                self.assertLessEqual(len(prompt), 68, f"{step['id']}:{key}")
            if step.get("successPrompt"):
                self.assertLessEqual(len(step["successPrompt"]), 70, step["id"])

    def test_sample_robot_overlay_pose_assets_match_interaction_state(self):
        expected_pose_file = {
            "teaching": "bright-teach.png",
            "thinking": "bright-thinking.png",
            "listening": "bright-listening.png",
            "celebrating": "bright-celebrate.png",
        }

        for manifest in (build_sample_manifest(), build_interactive_sample_manifest()):
            for step in manifest["steps"]:
                overlay = step["scene"]["robotOverlay"]
                expression = overlay["expression"]
                self.assertIn(expected_pose_file[expression], overlay["asset"]["src"], step["id"])
                self.assertIn(expected_pose_file[expression], overlay["atlas"]["image"], step["id"])
                if step.get("completionClass") == "interactive":
                    self.assertEqual(overlay["robotState"], "listening", step["id"])

    def test_sample_child_facing_copy_passes_output_safety_screen(self):
        for manifest in (build_sample_manifest(), build_interactive_sample_manifest()):
            for step in manifest["steps"]:
                texts = [
                    step.get("prompt"),
                    step.get("retryPrompt"),
                    step.get("successPrompt"),
                    *list((step.get("interactionPrompts") or {}).values()),
                ]
                for text in texts:
                    if not text:
                        continue
                    result = screen_model_output(text)
                    self.assertFalse(result["blocked"], (manifest["lessonId"], step["id"], result, text))


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

    async def test_sample_step_timeout_covers_real_https_asset_fetch_latency(self):
        manifest = build_sample_manifest(dwell_sec=0)
        timeouts = [step["timeoutSec"] for step in manifest["steps"]]
        self.assertTrue(timeouts)
        self.assertGreaterEqual(min(timeouts), 75.0)

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

    async def test_start_sample_lesson_under_sd_pack_sends_sd_asset_pack_and_local_step_paths(self):
        conn = _FakeConn(config={"lesson": {"asset_delivery_mode": "sd_pack"}})

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertEqual(conn.entered, ["sample_lesson_start"])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")

        prepare = json.loads(conn.websocket.sent[-1])
        self.assertEqual(prepare["type"], "lesson_prepare")
        pack = prepare["body"]["assetPack"]
        self.assertTrue(pack["ready"])
        self.assertEqual(pack["cacheKey"], "sample")
        self.assertEqual(pack["localRoot"], "sd://tbot/lesson-assets/sample-barn")
        pack_assets = {asset["path"]: asset for asset in pack["assets"]}
        self.assertEqual(
            set(pack_assets),
            {
                "barn-round-field-poster.jpg",
                "barn.png",
                "bright-teach.png",
                "bright-listening.png",
                "bright-thinking.png",
                "bright-celebrate.png",
            },
        )
        self.assertEqual(
            pack_assets["barn-round-field-poster.jpg"]["localPath"],
            "sd://tbot/lesson-assets/sample-barn/barn-round-field-poster.jpg",
        )

        await runtime.on_lesson_ack(
            {
                "type": "lesson_ack",
                "protocolVersion": "teebot-lesson-renderer.v1",
                "assignmentId": SAMPLE_ASSIGNMENT_ID,
                "sessionId": conn.session_id,
                "lessonId": INTERACTIVE_SAMPLE_LESSON_ID,
                "lessonVersion": 1,
                "stepId": None,
                "sequence": 1,
                "timestamp": 1,
                "body": {
                    "acks": 1,
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": "sample"},
                },
            }
        )
        await runtime.on_lesson_ack(_iack(conn, 2, 2))
        step = json.loads(conn.websocket.sent[-1])

        self.assertEqual(step["type"], "lesson_step")
        scene = step["body"]["scene"]
        self.assertEqual(
            scene["backgroundScene"]["poster"]["src"],
            "sd://tbot/lesson-assets/sample-barn/barn-round-field-poster.jpg",
        )
        self.assertEqual(
            scene["teachingObject"]["asset"]["src"],
            "sd://tbot/lesson-assets/sample-barn/barn.png",
        )
        self.assertEqual(
            scene["robotOverlay"]["asset"]["src"],
            "sd://tbot/lesson-assets/sample-barn/bright-teach.png",
        )

    async def test_interactive_sample_sd_pack_keeps_all_image_layers_on_every_step(self):
        conn = _FakeConn(
            config={
                "lesson": {
                    "asset_delivery_mode": "sd_pack",
                    "sample_mode": "interactive",
                    "sample_step_dwell_sec": 0,
                }
            }
        )

        runtime = await start_sample_lesson(conn)
        self.assertIsNotNone(runtime)

        await runtime.on_lesson_ack(
            {
                "type": "lesson_ack",
                "protocolVersion": "teebot-lesson-renderer.v1",
                "assignmentId": SAMPLE_ASSIGNMENT_ID,
                "sessionId": conn.session_id,
                "lessonId": INTERACTIVE_SAMPLE_LESSON_ID,
                "lessonVersion": 1,
                "stepId": None,
                "sequence": 1,
                "timestamp": 1,
                "body": {
                    "acks": 1,
                    "rendered": True,
                    "degraded": False,
                    "assetPack": {"ready": True, "cacheKey": "sample"},
                },
            }
        )
        await runtime.on_lesson_ack(_iack(conn, 2, 2))

        manifest = build_interactive_sample_manifest()
        root = "sd://tbot/lesson-assets/sample-barn"
        expected_overlay = {
            "s1": "bright-teach.png",
            "s2": "bright-teach.png",
            "s3": "bright-listening.png",
            "s4": "bright-listening.png",
        }

        for index, step in enumerate(manifest["steps"]):
            frame = json.loads(conn.websocket.sent[-1])
            self.assertEqual(frame["type"], "lesson_step")
            self.assertEqual(frame["stepId"], step["id"])

            scene = frame["body"]["scene"]
            self.assertEqual(
                scene["backgroundScene"]["poster"]["src"],
                f"{root}/barn-round-field-poster.jpg",
            )
            self.assertEqual(scene["teachingObject"]["asset"]["src"], f"{root}/barn.png")
            self.assertEqual(
                scene["robotOverlay"]["asset"]["src"],
                f"{root}/{expected_overlay[step['id']]}",
            )

            await runtime.on_lesson_ack(
                _iack(conn, 3 + index, 3 + index, step_id=step["id"])
            )
            if step.get("completionClass") == "interactive" and index < len(manifest["steps"]) - 1:
                self.assertTrue(await runtime.on_child_response("barn"))

    async def test_start_sample_lesson_enters_lesson_mode_and_emits_prepare(self):
        conn = _FakeConn()

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertIs(conn.lesson_runtime, runtime)
        self.assertEqual(conn.entered, ["sample_lesson_start"])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types[0], "lesson_prepare")

    async def test_start_sample_lesson_uses_lesson_asset_origin_when_sample_base_is_absent(self):
        conn = _FakeConn(config={"lesson": {"asset_origin_base": "https://assets.example/lesson"}})

        runtime = await start_sample_lesson(conn)
        self.assertIsNotNone(runtime)
        await runtime.on_lesson_ack(_iack(conn, 1, 1))
        await runtime._preload_task
        await runtime.on_lesson_ack(_iack(conn, 2, 2))

        frames = [json.loads(payload) for payload in conn.websocket.sent]
        step = next(frame for frame in frames if frame.get("type") == "lesson_step")
        scene = step["body"]["scene"]
        self.assertEqual(
            scene["backgroundScene"]["poster"]["src"],
            "https://assets.example/lesson/assets/background/barn-round-field-poster.jpg",
        )
        self.assertEqual(
            scene["teachingObject"]["asset"]["src"],
            "https://assets.example/lesson/assets/objects/barn.png",
        )
        self.assertEqual(
            scene["robotOverlay"]["asset"]["src"],
            "https://assets.example/lesson/assets/robot/poses/bright-teach.png",
        )

    async def test_start_sample_lesson_defaults_to_interactive_google_live_sample(self):
        conn = _FakeConn()

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.lesson_id, INTERACTIVE_SAMPLE_LESSON_ID)
        interactive_steps = [
            step for step in runtime.manifest["steps"]
            if step.get("completionClass") == "interactive"
        ]
        self.assertTrue(interactive_steps)

    async def test_start_sample_lesson_falls_back_from_infinite_sample_dwell_config(self):
        conn = _FakeConn(
            config={"lesson": {"sample_mode": "passive", "sample_step_dwell_sec": "inf"}}
        )

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertTrue(
            all(
                step.get("dwellSec") == DEFAULT_SAMPLE_STEP_DWELL_SEC
                for step in runtime.manifest["steps"]
            )
        )

    async def test_start_sample_lesson_restarts_active_sample_runtime_cleanly(self):
        conn = _FakeConn()

        first = await start_sample_lesson(conn)
        await first.on_lesson_ack(_iack(conn, 1, 1))
        await first._preload_task
        await first.on_lesson_ack(_iack(conn, 2, 2))
        self.assertEqual(first._step_id, "s1")
        self.assertIsNotNone(first._step_timeout_task)

        second = await start_sample_lesson(conn)

        self.assertIsNot(second, first)
        self.assertIs(conn.lesson_runtime, second)
        self.assertTrue(first._closed)
        self.assertIsNone(first._step_timeout_task)
        self.assertEqual(conn.entered, ["sample_lesson_start", "sample_lesson_start"])
        self.assertEqual(conn.lesson_start_status["code"], "STARTED")

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

    def __init__(self, provider=None):
        self.provider = provider
        self.sent_texts = []

    async def send_text(self, text):
        self.sent_texts.append(text)
        if self.provider is not None:
            self.provider.conn.google_live_lesson_prompt_output_allowed = False

    async def close(self):
        return None

    async def interrupt(self):
        return None

    async def end_audio_stream(self):
        return None


class _BlockingLessonVoiceProvider:
    def __init__(self):
        self.sent_texts = []
        self.open_attempts = 0
        self.retry_open_started = asyncio.Event()
        self.release_retry_open = asyncio.Event()

    async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
        self.sent_texts.append((text, continue_listening))
        return True

    async def open_lesson_child_response_window(self):
        self.open_attempts += 1
        if self.open_attempts == 1:
            return True
        self.retry_open_started.set()
        await self.release_retry_open.wait()
        return True

class _BlockingSuccessLessonVoiceProvider:
    def __init__(self):
        self.sent_texts = []
        self.success_started = asyncio.Event()
        self.release_success = asyncio.Event()

    async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
        self.sent_texts.append((text, continue_listening))
        if "hoàn thành bài học mẫu" in str(text).lower():
            self.success_started.set()
            await self.release_success.wait()
        return True

    async def open_lesson_child_response_window(self):
        return True

class _GateLessonVoiceProvider:
    def __init__(self):
        self.sent_texts = []
        self.open_started = asyncio.Event()
        self.open_release = asyncio.Event()

    async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
        self.sent_texts.append((text, continue_listening))
        return True

    async def open_lesson_child_response_window(self):
        self.open_started.set()
        await self.open_release.wait()
        return True

class _RealProviderConn(_FakeConn):
    """A sample-lesson conn that ALSO carries a real GoogleLiveProvider as its
    voice_provider, with the provider-side surface its lesson hooks read."""

    def __init__(self):
        super().__init__(
            config={
                "google_live": {
                    "lesson_child_response_window_sec": 25.0,
                    "lesson_prompt_output_guard_timeout_sec": 0.01,
                    "lesson_prompt_playback_guard_timeout_sec": 0.01,
                    "lesson_prompt_playback_tail_sec": 0.0,
                },
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

    async def _start_until_repeat_step(self):
        conn = _RealProviderConn()
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient(provider)
        conn.voice_provider = provider

        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)
        await rt.on_lesson_ack(_iack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_iack(conn, 2, 2))

        step_ids = [s["id"] for s in build_interactive_sample_manifest()["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_iack(conn, 3 + i, 3 + i, step_id=sid))
            if sid == "s3":
                self.assertEqual(rt._step_id, "s3")
                self.assertTrue(rt._child_response_window_open)
                return conn, provider, rt
        self.fail("interactive repeat step s3 was not reached")

    async def test_full_speaking_flow_child_says_word_then_completes_happy(self):
        conn = _RealProviderConn()
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient(provider)
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
                # The runtime narrated the prompt through Google Live and opened the child window;
                # the interactive step must NOT auto-advance.
                self.assertFalse(rt._step_completed, f"{sid} auto-advanced (should wait)")
                self.assertGreater(
                    provider._user_audio_allowed_until,
                    0.0,
                    "child-response window did not open",
                )
                self.assertGreaterEqual(
                    len(provider._client.sent_texts),
                    3,
                    "interactive prompt was not narrated to the child",
                )
                prompt_messages_before_answer = list(provider._client.sent_texts)
                # The child SPEAKS -> Live transcript -> real provider routes it to the
                # runtime, which completes the step. No chat/model forwarding.
                handled = await provider._on_user_transcript("barn")
                self.assertTrue(handled, f"child answer for {sid} was not routed")
                success_prompt = next(
                    s.get("successPrompt") for s in manifest["steps"] if s["id"] == sid
                )
                if success_prompt:
                    self.assertEqual(
                        provider._client.sent_texts[-1],
                        LESSON_LIVE_TEXT_INSTRUCTION + success_prompt,
                    )
                    self.assertEqual(
                        provider._client.sent_texts[:-1],
                        prompt_messages_before_answer,
                    )
                else:
                    self.assertEqual(provider._client.sent_texts, prompt_messages_before_answer)
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
        self.assertIn(
            LESSON_LIVE_TEXT_INSTRUCTION + "Đến lượt con. Con nói theo mình: barn!",
            provider._client.sent_texts,
        )

        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types[0], "lesson_prepare")
        self.assertEqual(sent_types.count("lesson_step"), len(step_ids))
        self.assertEqual(sent_types[-1], "lesson_stop")

    async def test_wrong_child_answer_does_not_advance_and_prompts_retry(self):
        conn, provider, rt = await self._start_until_repeat_step()

        lesson_steps_before = [
            json.loads(payload)["stepId"]
            for payload in conn.websocket.sent
            if json.loads(payload)["type"] == "lesson_step"
        ]

        handled_wrong = await provider._on_user_transcript("cat")

        self.assertTrue(handled_wrong)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._step_completed)
        self.assertEqual(rt._steps_completed, 2)
        lesson_steps_after_wrong = [
            json.loads(payload)["stepId"]
            for payload in conn.websocket.sent
            if json.loads(payload)["type"] == "lesson_step"
        ]
        self.assertEqual(lesson_steps_after_wrong, lesson_steps_before)
        self.assertTrue(
            any("nói lại" in text.lower() and "barn" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )
        self.assertTrue(
            any("nhìn hình" in text.lower() and "cái kho" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )
        self.assertFalse(
            any("cat" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )
        self.assertTrue(
            any("chưa đúng" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )
        self.assertFalse(
            any("chưa đúng rồi. chưa đúng" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )

        handled_correct = await provider._on_user_transcript("barn")

        self.assertTrue(handled_correct)
        self.assertEqual(rt._step_id, "s4")
        self.assertFalse(rt._step_completed)
        lesson_steps_after_correct = [
            json.loads(payload)["stepId"]
            for payload in conn.websocket.sent
            if json.loads(payload)["type"] == "lesson_step"
        ]
        self.assertEqual(lesson_steps_after_correct[-1], "s4")

    async def test_child_can_ask_for_repeat_without_being_marked_wrong(self):
        _conn, provider, rt = await self._start_until_repeat_step()

        handled = await provider._on_user_transcript("nói lại đi con chưa nghe")

        self.assertTrue(handled)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._step_completed)
        self.assertTrue(rt._child_response_window_open)
        last_prompt = provider._client.sent_texts[-1].lower()
        self.assertIn("mình nhắc lại", last_prompt)
        self.assertIn("barn", last_prompt)
        self.assertNotIn("chưa đúng", last_prompt)

    async def test_child_unknown_or_frustrated_gets_supportive_hint(self):
        _conn, provider, rt = await self._start_until_repeat_step()

        handled = await provider._on_user_transcript("con không biết khó quá")

        self.assertTrue(handled)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._step_completed)
        self.assertTrue(rt._child_response_window_open)
        last_prompt = provider._client.sent_texts[-1].lower()
        self.assertIn("không sao", last_prompt)
        self.assertIn("nhìn hình", last_prompt)
        self.assertIn("barn", last_prompt)
        self.assertNotIn("chưa đúng", last_prompt)

    async def test_child_vietnamese_object_answer_is_coached_to_english_word(self):
        _conn, provider, rt = await self._start_until_repeat_step()

        handled = await provider._on_user_transcript("con thấy cái kho")

        self.assertTrue(handled)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._step_completed)
        self.assertTrue(rt._child_response_window_open)
        last_prompt = provider._client.sent_texts[-1].lower()
        self.assertIn("đúng", last_prompt)
        self.assertIn("cái kho", last_prompt)
        self.assertIn("tiếng anh", last_prompt)
        self.assertIn("barn", last_prompt)
        self.assertNotIn("chưa đúng", last_prompt)

    async def test_child_start_lesson_phrase_during_response_window_stays_in_current_lesson(self):
        _conn, provider, rt = await self._start_until_repeat_step()

        handled = await provider._on_user_transcript("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._step_completed)
        self.assertTrue(rt._child_response_window_open)
        last_prompt = provider._client.sent_texts[-1].lower()
        self.assertIn("đang học", last_prompt)
        self.assertIn("barn", last_prompt)
        self.assertNotIn("chưa đúng", last_prompt)

    async def test_internal_child_response_probe_can_drive_ready_interactive_step_when_voice_window_closed(self):
        _conn, _provider, rt = await self._start_until_repeat_step()
        rt._child_response_window_open = False

        handled = await rt.on_child_response("barn", source="internal_dev_endpoint")

        self.assertTrue(handled)
        self.assertEqual(rt._step_id, "s4")

    async def test_retry_prompt_closes_child_window_until_reopened(self):
        conn = _RealProviderConn()
        provider = _BlockingLessonVoiceProvider()
        conn.voice_provider = provider

        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)

        await rt.on_lesson_ack(_iack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_iack(conn, 2, 2))

        step_ids = [s["id"] for s in build_interactive_sample_manifest()["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_iack(conn, 3 + i, 3 + i, step_id=sid))
            if sid == "s3":
                break
        self.assertEqual(rt._step_id, "s3")
        self.assertTrue(rt._child_response_window_open)

        wrong_task = asyncio.create_task(rt.on_child_response("cat"))
        await asyncio.wait_for(provider.retry_open_started.wait(), timeout=1.0)

        handled_correct = await rt.on_child_response("barn")

        self.assertFalse(handled_correct)
        self.assertEqual(rt._step_id, "s3")
        self.assertFalse(rt._child_response_window_open)

        provider.release_retry_open.set()
        self.assertTrue(await wrong_task)

        self.assertEqual(rt._step_id, "s3")
        self.assertTrue(rt._child_response_window_open)

        handled_correct_after_reopen = await rt.on_child_response("barn")

        self.assertTrue(handled_correct_after_reopen)
        self.assertEqual(rt._step_id, "s4")
        self.assertFalse(rt._child_response_window_open)

    async def test_final_success_prompt_closes_window_before_tts_handoff_returns(self):
        conn = _RealProviderConn()
        provider = _BlockingSuccessLessonVoiceProvider()
        conn.voice_provider = provider

        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)

        await rt.on_lesson_ack(_iack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_iack(conn, 2, 2))

        step_ids = [s["id"] for s in build_interactive_sample_manifest()["steps"]]
        for i, sid in enumerate(step_ids):
            await rt.on_lesson_ack(_iack(conn, 3 + i, 3 + i, step_id=sid))
            if sid == "s3":
                self.assertTrue(await rt.on_child_response("barn"))
                self.assertEqual(rt._step_id, "s4")
            if sid == "s4":
                break

        self.assertEqual(rt._step_id, "s4")
        self.assertTrue(rt._child_response_window_open)

        first_task = asyncio.create_task(rt.on_child_response("barn"))
        duplicate_task = None
        try:
            await asyncio.wait_for(provider.success_started.wait(), timeout=1.0)

            self.assertTrue(rt._step_completed)
            self.assertFalse(rt._child_response_window_open)

            duplicate_task = asyncio.create_task(rt.on_child_response("barn"))
            await asyncio.sleep(0)

            self.assertTrue(duplicate_task.done())
            self.assertFalse(await duplicate_task)
        finally:
            provider.release_success.set()
            await asyncio.wait_for(first_task, timeout=1.0)
            if duplicate_task is not None and not duplicate_task.done():
                await asyncio.wait_for(duplicate_task, timeout=1.0)

    async def test_superseded_runtime_cannot_reopen_child_window_or_consume_answers(self):
        conn = _RealProviderConn()
        provider = _GateLessonVoiceProvider()
        conn.voice_provider = provider

        first = await start_sample_lesson(conn)
        self.assertIsNotNone(first)

        await first.on_lesson_ack(_iack(conn, 1, 1))
        await first._preload_task
        await first.on_lesson_ack(_iack(conn, 2, 2))
        await first.on_lesson_ack(_iack(conn, 3, 3, step_id="s1"))
        await first.on_lesson_ack(_iack(conn, 4, 4, step_id="s2"))

        stale_ack_task = asyncio.create_task(
            first.on_lesson_ack(_iack(conn, 5, 5, step_id="s3"))
        )
        await asyncio.wait_for(provider.open_started.wait(), timeout=1.0)

        second = await start_sample_lesson(conn)
        self.assertIsNotNone(second)
        self.assertIs(conn.lesson_runtime, second)
        self.assertTrue(first._closed)

        provider.open_release.set()
        await asyncio.wait_for(stale_ack_task, timeout=1.0)

        self.assertFalse(first._child_response_window_open)
        self.assertIsNone(first._child_response_timeout_task)
        self.assertFalse(await first.on_child_response("barn"))
        self.assertEqual(conn.finished, [])
        self.assertIs(conn.lesson_runtime, second)

    async def test_passive_sample_does_not_wait_for_child(self):
        """Control: the DEFAULT (passive) sample completes with NO child-response window
        and NO transcript routing — proving the interactive wait above is load-bearing."""
        conn = _RealProviderConn()
        conn.config["lesson"]["sample_mode"] = "passive"
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient(provider)
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

    async def test_interactive_sample_silent_child_finishes_happy_not_abandoned(self):
        """Demo-critical: in a showcase the child may never speak. The interactive
        sample must MODEL the answer and advance through every step to the HAPPY
        lesson_completed ending, never a sad child_inactive abandon. Real assigned
        lessons keep abandon semantics (graceful_inactivity_finish stays False)."""
        conn = _RealProviderConn()
        provider = GoogleLiveProvider(conn)
        provider._client = _FakeLiveClient(provider)
        conn.voice_provider = provider

        rt = await start_sample_lesson(conn)
        self.assertIsNotNone(rt)
        self.assertTrue(rt._graceful_inactivity_finish)
        await rt.on_lesson_ack(_iack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_iack(conn, 2, 2))

        manifest = build_interactive_sample_manifest()
        step_ids = [s["id"] for s in manifest["steps"]]
        seq = 3
        for sid in step_ids:
            await rt.on_lesson_ack(_iack(conn, seq, seq, step_id=sid))
            seq += 1
            if rt.state == "COMPLETED":
                break
            # Interactive steps wait for the child. Simulate a SILENT child by
            # exhausting the no-answer attempts; the demo path must model + advance.
            step = next(s for s in manifest["steps"] if s["id"] == sid)
            if step.get("completionClass") == "interactive":
                for _ in range(rt._max_child_response_timeouts()):
                    await rt._handle_child_response_timeout(sid)
                # Never abandoned, never paused.
                self.assertNotEqual(rt.state, "PAUSED")

        # Drive the terminal lesson_stop ack to COMPLETED.
        if rt.state != "COMPLETED":
            await rt.on_lesson_ack(_iack(conn, seq, seq))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(conn.finished, ["lesson_completed"])
        self.assertEqual(conn.released, [])
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertNotIn("lesson_abandoned", sent_types)
        self.assertEqual(sent_types[-1], "lesson_stop")


if __name__ == "__main__":
    unittest.main()
