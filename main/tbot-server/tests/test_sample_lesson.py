import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from core.activity_lease import ActivityLeaseCoordinator, ActivityOperation, ExclusiveDisposition
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
        if config is None:
            config = {}
        self.config = config
        if isinstance(self.config, dict):
            lesson_cfg = self.config.setdefault("lesson", {})
            if isinstance(lesson_cfg, dict):
                lesson_cfg.setdefault("sample_lesson", True)
                lesson_cfg.setdefault("rollout_device_allowlist", [self.device_id])
        self.lesson_runtime = None
        self.lesson_start_status = None
        self.finished = []
        self.released = []
        self.entered = []
        self.events = []
        self.voice_provider = None

    async def enter_lesson_mode(self, *, reason="lesson_start"):
        self.entered.append(reason)
        self.events.append(f"enter:{reason}")

    async def finish_lesson_mode(self, *, reason="lesson_completed"):
        self.finished.append(reason)

    async def release_lesson_mode(self, *, reason="lesson_terminal"):
        self.released.append(reason)
        self.events.append(f"release:{reason}")

    def is_realtime_busy(self):
        return False


def _ack(conn, acks, env_seq, *, step_id=None):
    runtime = getattr(conn, "lesson_runtime", None)
    session_id = getattr(runtime, "session_id", None) or conn.session_id
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": SAMPLE_ASSIGNMENT_ID,
        "sessionId": session_id,
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

    def test_passthrough_asset_cache_defaults_invalid_preload_timeout(self):
        self.assertEqual(SampleAssetCache(preload_timeout_sec="bad").preload_timeout_sec, 30.0)
        self.assertEqual(SampleAssetCache(preload_timeout_sec=0).preload_timeout_sec, 30.0)
        self.assertEqual(SampleAssetCache(preload_timeout_sec=float("inf")).preload_timeout_sec, 30.0)

    def test_sd_sample_cache_exposes_fixed_firmware_sync_capability(self):
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample")

        self.assertEqual(
            cache.firmware_sample_sync_request(),
            {"base_url": "https://cdn.example/sample"},
        )
        files = cache.firmware_sample_sync_files()
        records = {
            asset["path"]: asset["sha256"]
            for asset in cache.asset_pack_manifest(
                assignment_version=1,
                lesson_id="sample-barn-say-it",
                lesson_version=1,
                manifest_checksum="a" * 64,
            )["assets"]
        }
        self.assertEqual(len(files), 6)
        self.assertTrue(cache.validate_firmware_sample_sync_result({
            "directory": "/sdcard/tbot/lesson-assets/sample-barn",
            "downloadedCount": 6,
            "files": [
                {"file": name, "bytes": 1, "sha256": records[name]}
                for name in files
            ],
        }))

    def test_sd_sample_cache_rejects_non_exact_firmware_attestations(self):
        cache = SampleAssetCache(sd_pack=True, asset_base="https://cdn.example/sample")
        manifest_assets = cache.asset_pack_manifest(
            assignment_version=1,
            lesson_id="sample-barn-say-it",
            lesson_version=1,
            manifest_checksum="a" * 64,
        )["assets"]
        files = [
            {
                "file": asset["path"],
                "bytes": asset["size"],
                "sha256": asset["sha256"],
            }
            for asset in manifest_assets
        ]

        invalid_files = {
            "missing digest": [{key: value for key, value in files[0].items() if key != "sha256"}, *files[1:]],
            "wrong digest": [{**files[0], "sha256": "0" * 64}, *files[1:]],
            "uppercase digest": [{**files[0], "sha256": files[0]["sha256"].upper()}, *files[1:]],
            "duplicate digest": [{**files[0], "sha256": files[1]["sha256"]}, *files[1:]],
            "duplicate filename": [{**files[0]}, {**files[0]}, *files[2:]],
            "missing asset": files[:-1],
            "extra asset": [*files, {"file": "extra.png", "bytes": 1, "sha256": "0" * 64}],
            "zero bytes": [{**files[0], "bytes": 0}, *files[1:]],
            "boolean bytes": [{**files[0], "bytes": True}, *files[1:]],
        }
        for label, candidate_files in invalid_files.items():
            with self.subTest(label=label):
                self.assertFalse(cache.validate_firmware_sample_sync_result({
                    "directory": "/sdcard/tbot/lesson-assets/sample-barn",
                    "downloadedCount": len(candidate_files),
                    "files": candidate_files,
                }))

    def test_sample_firmware_sync_request_requires_safe_http_base(self):
        valid_bases = (
            "http://cdn.example/sample",
            "https://cdn.example:8443/sample/",
        )
        for base in valid_bases:
            with self.subTest(base=base):
                self.assertEqual(
                    SampleAssetCache(sd_pack=True, asset_base=base).firmware_sample_sync_request(),
                    {"base_url": base.rstrip("/")},
                )

        invalid_bases = (
            "file:///tmp/sample",
            "ftp://cdn.example/sample",
            "//cdn.example/sample",
            "https:///sample",
            "https://user:secret@cdn.example/sample",
            "https://cdn.example\\attacker.example/sample",
            "https://cdn.example/sample\x00suffix",
            "https://cdn.example/sample\nheader: value",
            " https://cdn.example/sample",
            "https://cdn.example/sample ",
            "https://cdn.example/sample path",
            "https://cdn.example/sample?version=1",
            "https://cdn.example/sample#section",
        )
        for base in invalid_bases:
            with self.subTest(base=base):
                self.assertIsNone(
                    SampleAssetCache(sd_pack=True, asset_base=base).firmware_sample_sync_request()
                )

    def test_sample_cache_without_sd_or_base_has_no_firmware_sync_request(self):
        self.assertIsNone(
            SampleAssetCache(sd_pack=False, asset_base="https://cdn.example").firmware_sample_sync_request()
        )
        self.assertIsNone(SampleAssetCache(sd_pack=True, asset_base="").firmware_sample_sync_request())

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
            # Patient quiet windows for age ~3–6; final recall is the longest.
            self.assertGreaterEqual(step.get("responseTimeoutSec", 0), 10.0, step["id"])
            self.assertLessEqual(step.get("responseTimeoutSec", 0), 18.0, step["id"])
            self.assertEqual(step.get("maxNoAnswerAttempts"), 2, step["id"])

        final = interactive_steps[-1]
        self.assertGreaterEqual(final.get("responseTimeoutSec", 0), 14.0, final["id"])
        self.assertIn("hoàn thành bài học mẫu", final.get("successPrompt", ""))

        passive_dwells = [
            step.get("dwellSec", 0)
            for step in manifest["steps"]
            if step.get("completionClass") == "passive"
        ]
        self.assertTrue(passive_dwells)
        self.assertTrue(all(value <= 0.5 for value in passive_dwells))

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
        # Listen-first framing: TeeBot models slowly before inviting the child.
        self.assertIn("chậm", first_prompt)

        focus_prompt = manifest["steps"][1]["prompt"].lower()
        self.assertIn("barn", focus_prompt)
        self.assertIn("cái kho", focus_prompt)
        # Double model so the child hears the word shape twice before speaking.
        self.assertGreaterEqual(focus_prompt.count("barn"), 2)
        self.assertIn("nghe chậm", focus_prompt)
        self.assertLessEqual(max(len(step["prompt"]) for step in manifest["steps"]), 135)

        repeat_prompt = manifest["steps"][2]["prompt"].lower()
        self.assertIn("nói chậm", repeat_prompt)
        self.assertIn("nói theo", repeat_prompt)
        self.assertIn("barn", repeat_prompt)
        # Positive mid-turn feedback before the harder recall turn.
        self.assertIn("barn", (manifest["steps"][2].get("successPrompt") or "").lower())

        for step in manifest["steps"]:
            scene = step["scene"]
            self.assertIn("cái kho", scene["backgroundScene"]["altCaption"].lower(), step["id"])
            self.assertIn("cái kho", scene["teachingObject"]["supportWords"], step["id"])
            self.assertEqual(scene["teachingObject"]["primitiveFallbackCard"]["label"], "barn")
            self.assertEqual(scene["teachingObject"]["placement"]["anchor"], "center")
            self.assertEqual(scene["robotOverlay"]["anchor"], "bottomLeft")

        # Coaching is runtime-adaptive (not canned sample scripts). Manifest only
        # needs expectedResponses so the live path can remodel the target word.
        for step in manifest["steps"]:
            if step.get("completionClass") != "interactive":
                continue
            self.assertEqual(step.get("expectedResponses"), ["barn"], step["id"])
            self.assertNotIn("interactionPrompts", step, step["id"])
            self.assertNotIn("retryPrompt", step, step["id"])

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
    async def test_start_sample_lesson_prepares_voice_provider_before_lesson_mode(self):
        class _PrepareVoiceProvider:
            async def prepare_for_sample_lesson(self):
                conn.events.append("prepare_voice")

        conn = _FakeConn()
        conn.voice_provider = _PrepareVoiceProvider()

        runtime = await start_sample_lesson(conn)

        self.assertIsNotNone(runtime)
        self.assertEqual(conn.events[:2], ["prepare_voice", "enter:sample_lesson_start"])

    @patch("core.lesson.runtime.uuid.uuid4", return_value="sess-sample-1")
    async def test_sample_lesson_plays_all_steps_to_completed_then_finishes(self, _uuid4):
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

    @patch("core.lesson.runtime.uuid.uuid4", return_value="sess-sample-1")
    async def test_passive_dwell_delays_auto_advance_then_completes(self, _uuid4):
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

    @patch("core.lesson.runtime.uuid.uuid4", return_value="sess-sample-1")
    async def test_passive_step_waits_for_prompt_audio_idle_before_auto_advance(self, _uuid4):
        class _BlockingPromptIdleProvider:
            def __init__(self):
                self.prompts = []
                self.wait_started = asyncio.Event()
                self.release_wait = asyncio.Event()

            async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
                self.prompts.append((text, continue_listening))
                return True

            async def wait_lesson_step_prompt_idle(self):
                self.wait_started.set()
                await self.release_wait.wait()
                return True

        conn = _FakeConn()
        provider = _BlockingPromptIdleProvider()
        conn.voice_provider = provider
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

        await rt.start()
        await rt.on_lesson_ack(_ack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(conn, 2, 2))

        ack_task = asyncio.create_task(rt.on_lesson_ack(_ack(conn, 3, 3, step_id="s1")))
        try:
            await asyncio.wait_for(provider.wait_started.wait(), timeout=0.2)
            await asyncio.sleep(0)

            self.assertFalse(ack_task.done())
            self.assertEqual(rt._step_id, "s1")
            step_ids = [
                json.loads(payload).get("stepId")
                for payload in conn.websocket.sent
                if json.loads(payload).get("type") == "lesson_step"
            ]
            self.assertEqual(step_ids, ["s1"])

            provider.release_wait.set()
            await asyncio.wait_for(ack_task, timeout=1.0)

            self.assertEqual(rt._step_id, "s2")
        finally:
            provider.release_wait.set()
            if not ack_task.done():
                await asyncio.wait_for(ack_task, timeout=1.0)

    @patch("core.lesson.runtime.uuid.uuid4", return_value="sess-sample-1")
    async def test_passive_dwell_starts_after_prompt_audio_idle(self, _uuid4):
        class _BlockingPromptIdleProvider:
            def __init__(self):
                self.wait_started = asyncio.Event()
                self.release_wait = asyncio.Event()

            async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
                return True

            async def wait_lesson_step_prompt_idle(self):
                self.wait_started.set()
                await self.release_wait.wait()
                return True

        conn = _FakeConn()
        provider = _BlockingPromptIdleProvider()
        conn.voice_provider = provider
        manifest = build_sample_manifest(dwell_sec=0.01)
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

        await rt.start()
        await rt.on_lesson_ack(_ack(conn, 1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(conn, 2, 2))

        ack_task = asyncio.create_task(rt.on_lesson_ack(_ack(conn, 3, 3, step_id="s1")))
        try:
            await asyncio.wait_for(provider.wait_started.wait(), timeout=0.2)
            await asyncio.sleep(0.02)

            self.assertFalse(ack_task.done())
            self.assertEqual(rt._step_id, "s1")

            provider.release_wait.set()
            await asyncio.wait_for(ack_task, timeout=1.0)
            await asyncio.sleep(0.03)

            self.assertEqual(rt._step_id, "s2")
        finally:
            provider.release_wait.set()
            if not ack_task.done():
                await asyncio.wait_for(ack_task, timeout=1.0)

    async def test_start_sample_lesson_under_sd_pack_sends_sd_asset_pack_and_local_step_paths(self):
        conn = _FakeConn(config={"lesson": {
            "asset_delivery_mode": "sd_pack",
            "sample_asset_base_url": "https://esp.example/sample",
        }})

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
        pack_assets = {asset["key"]: asset for asset in pack["assets"]}
        self.assertEqual(
            set(pack_assets),
            {
                "backgroundScene.poster",
                "teachingObject.sample",
                "robotOverlay.teaching",
                "robotOverlay.listening",
                "robotOverlay.thinking",
                "robotOverlay.celebrating",
            },
        )
        self.assertEqual(
            pack_assets["backgroundScene.poster"]["localPath"],
            "sd://tbot/lesson-assets/sample-barn/barn-round-field-poster.jpg",
        )
        self.assertTrue(all(asset.get("localPath") for asset in pack_assets.values()))
        self.assertTrue(all("url" not in asset for asset in pack_assets.values()))

        await runtime.on_lesson_ack(
            {
                "type": "lesson_ack",
                "protocolVersion": "teebot-lesson-renderer.v1",
                "assignmentId": SAMPLE_ASSIGNMENT_ID,
                "sessionId": runtime.session_id,
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
                "sessionId": runtime.session_id,
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
        self.assertEqual(uuid.UUID(runtime.session_id).version, 4)
        self.assertNotEqual(runtime.session_id, conn.session_id)
        self.assertEqual(json.loads(conn.websocket.sent[0])["sessionId"], runtime.session_id)
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

    async def test_start_sample_lesson_blocks_malformed_lesson_config(self):
        conn = _FakeConn(config={"lesson": "bad"})

        runtime = await start_sample_lesson(conn)

        self.assertIsNone(runtime)
        self.assertEqual(conn.lesson_start_status["code"], "ROLLOUT_BLOCKED")

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
        self.assertNotEqual(second.session_id, first.session_id)
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

    def __init__(self, provider=None, sent_texts=None):
        self.provider = provider
        self.sent_texts = [] if sent_texts is None else sent_texts

    async def connect(self):
        self.connected = True

    async def receive_events(self):
        if False:
            yield None

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


def _make_real_google_live_provider(conn):
    provider_ref = {}
    sent_texts = []

    def client_factory(*_args, **_kwargs):
        return _FakeLiveClient(provider_ref["provider"], sent_texts=sent_texts)

    provider = GoogleLiveProvider(conn, client_factory=client_factory)
    provider_ref["provider"] = provider
    provider._client = client_factory()
    conn.voice_provider = provider
    return provider


def _iack(conn, acks, env_seq, *, step_id=None):
    """lesson_ack carrying the INTERACTIVE sample's lessonId."""
    runtime = getattr(conn, "lesson_runtime", None)
    session_id = getattr(runtime, "session_id", None) or conn.session_id
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": SAMPLE_ASSIGNMENT_ID,
        "sessionId": session_id,
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
        provider = _make_real_google_live_provider(conn)

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
        provider = _make_real_google_live_provider(conn)

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
            LESSON_LIVE_TEXT_INSTRUCTION + "Mình nói chậm: barn. Con nói theo mình: barn!",
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
        # Adaptive coaching: acknowledge attempt + remodel target word (no raw echo).
        self.assertTrue(
            any(
                "mình nghe rồi" in text.lower()
                and "từ mình học là" in text.lower()
                and "barn" in text.lower()
                and "nói chậm" in text.lower()
                for text in provider._client.sent_texts
            ),
            provider._client.sent_texts,
        )
        self.assertFalse(
            any("cat" in text.lower() for text in provider._client.sent_texts),
            provider._client.sent_texts,
        )
        self.assertFalse(
            any("chưa đúng" in text.lower() for text in provider._client.sent_texts),
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
        self.assertIn("từ mới là", last_prompt)
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
        self.assertIn("tiếng anh là", last_prompt)
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
        self.assertIn("tiếng anh là", last_prompt)
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
        provider = _make_real_google_live_provider(conn)

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
        provider = _make_real_google_live_provider(conn)

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


class ActivityLeaseSampleMutationTest(unittest.IsolatedAsyncioTestCase):
    async def test_sample_start_refuses_exclusive_lease_inside_shared_lock(self):
        from core.lesson import sample as sample_module

        lock = asyncio.Lock()
        await lock.acquire()
        conn = SimpleNamespace(
            _lesson_pull_lock=lock,
            activity_leases=ActivityLeaseCoordinator(asyncio.get_running_loop()),
        )
        async def forbidden(_conn):
            self.fail("sample mutation must not run during exclusive eviction")

        with patch(
            "core.providers.tools.product_toolset.sample_lesson_config_enabled",
            return_value=True,
        ), patch.object(sample_module, "_start_sample_lesson_impl", forbidden):
            task = asyncio.create_task(sample_module.start_sample_lesson(conn))
            try:
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                lease = conn.activity_leases.try_acquire_eviction(
                    ActivityOperation.LESSON_CACHE_EVICT,
                    busy_probe=lambda: False,
                )
                self.assertIsNotNone(lease)
                lease.complete_exclusive(ExclusiveDisposition.AMBIGUOUS)
                lock.release()
                result = await task
            finally:
                if lock.locked():
                    lock.release()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "CACHE_EVICTION_RESERVED")


if __name__ == "__main__":
    unittest.main()
