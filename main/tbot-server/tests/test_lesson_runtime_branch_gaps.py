"""Close the *object-level* branch gaps in ``core.lesson.runtime.LessonRuntime``.

``--cov-branch`` flags several partial branches on the runtime state machine whose
false-edge no existing test takes:

  * 344->346 -- ``close``: forwarder is None (already torn down) -> skip aclose, fall
                to the asset_cache teardown.
  * 346->exit -- ``close``: asset_cache is None -> nothing left to tear down.
  * 408->exit -- ``on_lesson_progress``: a forwarded progress event whose ``event`` is
                NOT ``step_completed`` (e.g. ``step_started``) -> the completion-latch
                block is skipped and the handler returns.
  * 460->462 -- ``on_lesson_error``: ``sequence`` is not an int (or not greater than the
                last) -> the inbound-sequence bump is skipped but the handler still
                processes the error.
  * 471->exit -- ``on_lesson_error``: the run is in a non-running, non-terminal state
                (READY) -> the error is recorded but does NOT fail the run.
  * 518->exit -- ``_on_frame_acked``: an unrecognized frame type -> the elif chain
                falls through with no state transition.
  * 912->914 -- ``_emit``: a frame_type that is neither ``lesson_step`` nor one of
                prepare/start/stop (``lesson_error``) -> neither log branch runs.
  * 1018->1025 -- ``_prepare_body``: SD asset pack enabled but the cache exposes no
                callable ``asset_pack_manifest`` -> the assetPack block is skipped.
  * 1123->1120 -- ``_rewrite_required_sd_pack_layer_sources``: a required asset node
                that is None (layer absent) -> the per-node rewrite is skipped and the
                loop continues.

Reuses the proven harness from ``test_lesson_runtime.py`` (FIX-derived assignment /
manifest / fakes) by importing its helpers, so every assertion runs against the REAL
``LessonRuntime`` with no network or disk.
"""

import os
import sys
import unittest
import asyncio
import copy
import importlib.util
import json
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as T  # noqa: E402  (sibling test harness)
from core.lesson.errors import LessonError  # noqa: E402
from core.lesson.runtime import (  # noqa: E402
    LessonRuntime,
    S_COMPLETED,
    S_FAILED,
    S_PAUSED,
    S_READY,
    S_RUNNING,
    VISUAL_DEGRADED_REASONS,
    VISUAL_REJECTED_REASONS,
)


RENDERER_V3 = "teebot-lesson-renderer.v3"


def _cinematic_metadata(duration_ms, *, background=False):
    return {
        "codec": "mjpeg",
        "fps": 10,
        "durationMs": duration_ms,
        "frameCount": duration_ms // 100,
        "hasAudio": False,
        "rect": (
            {"x": 0, "y": 0, "width": 480, "height": 320}
            if background
            else {"x": 120, "y": 80, "width": 200, "height": 200}
        ),
        "chromaKey": (
            None
            if background
            else {
                "color": {"r": 0, "g": 255, "b": 0},
                "tolerance": 20,
                "feather": 4,
            }
        ),
    }


def _cinematic_phase(phase_id="opening", duration_ms=9000, suffix=""):
    identities = (
        ("background", "backgroundScene", f"scene.opening{suffix}", 3, "a", True),
        ("teachingObject", "teachingObject", f"object.barn{suffix}", 2, "b", False),
        ("robotOverlay", "robotOverlay", f"robot.walk{suffix}", 4, "c", False),
    )
    return {
        "templateId": "directMp4Cinematic",
        "templateVersion": 1,
        "phaseId": phase_id,
        "timing": (
            {"flyInMs": 3200, "farBeatMs": 800, "walkingMs": 5000, "durationMs": 9000}
            if phase_id == "opening"
            else {"durationMs": duration_ms}
        ),
        "layers": [
            {
                "layer": layer,
                "slot": slot,
                "assetVersionId": f"{key}@v{version}",
                "assetKey": key,
                "version": version,
                "path": f"visuals/{key}/v{version}.mp4",
                "url": f"https://cdn.example.test/visuals/{key}/v{version}.mp4",
                "sha256": sha * 64,
                "bytes": 900000 - version,
                "mediaType": "video/mp4",
                "width": 480 if background else 200,
                "height": 320 if background else 200,
                "metadata": _cinematic_metadata(duration_ms, background=background),
            }
            for layer, slot, key, version, sha, background in identities
        ],
    }


def _cinematic_manifest():
    manifest = T._build_manifest()
    manifest["manifestVersion"] = RENDERER_V3
    manifest["features"] = {
        "lessonRendererV3": {
            "directMp4Cinematic": True,
            "assetSource": "publishedVersionedVisualRefs",
        }
    }
    manifest["cinematicPhases"] = [
        _cinematic_phase(),
        _cinematic_phase("greet", 1200, ".greet"),
    ]
    return manifest


class _CinematicAssetCache(T._FakeAssetCache):
    def __init__(self, *, ready=True):
        super().__init__(ready=ready)
        self.asset_pack_local_root = "sd://tbot/lesson-assets"

    def asset_pack_manifest(self, **kwargs):
        pack = super().asset_pack_manifest(**kwargs)
        pack["assets"] = []
        for phase in _cinematic_manifest()["cinematicPhases"]:
            for layer in phase["layers"]:
                metadata = copy.deepcopy(layer["metadata"])
                basename = layer["assetVersionId"].replace("@", "%40")
                pack["assets"].append(
                    {
                        "key": layer["assetVersionId"],
                        "sharedAssetKey": layer["assetKey"],
                        "sharedAssetVersion": layer["version"],
                        "path": layer["path"],
                        "onlineUrl": layer["url"],
                        "url": layer["url"],
                        "sha256": layer["sha256"],
                        "size": layer["bytes"],
                        "mediaType": "video/mp4",
                        "critical": True,
                        "layer": layer["slot"],
                        "role": "video",
                        "state": "READY" if self._ready else "PENDING",
                        "checksumOk": self._ready,
                        "localPath": f"{pack['localRoot']}/{basename}",
                        "sdPath": f"{pack['localRoot']}/{basename}",
                        "compatibilityMetadata": metadata,
                        "visualRefs": [
                            {
                                "stepKey": "s4",
                                "phase": phase["phaseId"],
                                "slot": f"{layer['slot']}.{phase['phaseId']}",
                            }
                        ],
                    }
                )
        return pack

    def local_pack_url_for_source(self, source):
        if isinstance(source, str) and source:
            return f"{self.asset_pack_local_root}/{self.cache_key}/static/{os.path.basename(source)}"
        return None


def _cinematic_runtime(*, conn=None, manifest=None, cache=None, sleep=None):
    conn = conn or T._FakeConn(
        features={
            "lesson": True,
            "renderer": [RENDERER_V3],
            "lessonRendererV3": {
                "directMp4Cinematic": True,
                "sdAssetPack": True,
            },
        }
    )
    conn.device_id = "robot-v3"
    conn.config = {
        "lesson": {
            "renderer_v3_enabled": True,
            "rollout_device_allowlist": ["robot-v3"],
            "asset_delivery_mode": "sd_pack",
            "frame_ack_timeout_sec": 60,
        }
    }
    with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=conn.session_id):
        return LessonRuntime(
            conn,
            assignment=T._build_assignment(),
            manifest=manifest or _cinematic_manifest(),
            asset_cache=cache or _CinematicAssetCache(),
            forwarder=T._FakeForwarder(),
            manifest_checksum=T._manifest_checksum(),
            sleep=sleep,
        )


def _runtime(conn=None, *, asset_cache=None, forwarder=None, manifest=None):
    conn = conn or T._FakeConn(session_id="sess")
    with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=conn.session_id):
        return LessonRuntime(
            conn,
            assignment=T._build_assignment(),
            manifest=manifest or T._build_manifest(),
            asset_cache=asset_cache if asset_cache is not None else T._FakeAssetCache(ready=True),
            forwarder=forwarder if forwarder is not None else T._FakeForwarder(),
            manifest_checksum=T._manifest_checksum(),
        )


class RuntimeCloseBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 344->346: close with no forwarder still tears down the asset cache ────────
    async def test_close_without_forwarder_still_closes_asset_cache(self):
        asset_cache = T._FakeAssetCache(ready=True)
        rt = _runtime(asset_cache=asset_cache)
        rt.forwarder = None  # already torn down / never attached

        await rt.close()

        # forwarder branch skipped (no crash), asset cache still closed.
        self.assertTrue(asset_cache.closed)
        self.assertTrue(rt._closed)

    async def test_close_rejects_all_visual_waiters_and_increments_generation(self):
        rt = _runtime()
        rt.negotiated_version = "teebot-lesson-renderer.v2"
        rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        rt.conn.device_id = "robot-01"
        rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        task = asyncio.create_task(rt.send_visual_state("thinking", overlay_key="thinking"))
        await asyncio.sleep(0)
        generation = rt._visual_generation
        self.assertEqual(len(rt._visual_ack_waiters), 1)

        await rt.close()
        result = await task

        self.assertFalse(result.accepted)
        self.assertEqual(rt._visual_generation, generation + 1)
        self.assertEqual(rt._visual_ack_waiters, {})
        self.assertEqual(rt._visual_ack_timeout_tasks, {})

    # ── 346->exit: close with no asset cache is a clean no-op tail ────────────────
    async def test_close_without_asset_cache_is_clean(self):
        forwarder = T._FakeForwarder()
        rt = _runtime(forwarder=forwarder)
        rt.asset_cache = None

        await rt.close()

        self.assertTrue(forwarder.closed)
        self.assertTrue(rt._closed)


class VisualAckWaiterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rt = _runtime()
        self.rt.negotiated_version = "teebot-lesson-renderer.v2"
        self.rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        self.rt.conn.device_id = "robot-01"
        self.rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        self.rt.state = S_RUNNING
        self.rt._step_id = "s4"

    def _ack(self, server_sequence, inbound_sequence, *, generation, **body):
        return {
            "type": "lesson_ack",
            "protocolVersion": "teebot-lesson-renderer.v2",
            "assignmentId": self.rt.assignment_id,
            "sessionId": self.rt.session_id,
            "stepId": "s4",
            "sequence": inbound_sequence,
            "body": {
                "acks": server_sequence,
                "accepted": True,
                "degraded": False,
                "degradedReason": None,
                "visualGeneration": generation,
                **body,
            },
        }

    def test_superseded_is_rejection_only_not_a_degraded_fallback(self):
        self.assertEqual(len(VISUAL_DEGRADED_REASONS), 7)
        self.assertNotIn("superseded", VISUAL_DEGRADED_REASONS)
        self.assertEqual(
            VISUAL_REJECTED_REASONS - VISUAL_DEGRADED_REASONS,
            {"superseded"},
        )

    async def test_accepts_firmware_visual_ack_identity_metadata(self):
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        ack = self._ack(
            frame["sequence"],
            1,
            generation=frame["body"]["visualGeneration"],
        )
        ack.update(
            {
                "lessonId": self.rt.lesson_id,
                "lessonVersion": self.rt.lesson_version,
                "timestamp": 1_700_000_000_000,
            }
        )

        await self.rt.on_lesson_ack(ack)
        await asyncio.sleep(0)

        self.assertTrue(task.done())
        self.assertTrue((await task).accepted)

    async def test_independent_sequences_resolve_only_the_correlated_waiter(self):
        first = asyncio.create_task(self.rt.send_visual_state("thinking"))
        second = asyncio.create_task(self.rt.send_visual_state("listen"))
        await asyncio.sleep(0)
        frames = [json.loads(item) for item in self.rt.conn.websocket.sent]
        visual = [frame for frame in frames if frame["type"] == "lesson_visual_state"]
        first_seq, second_seq = visual[-2]["sequence"], visual[-1]["sequence"]
        generation = visual[-1]["body"]["visualGeneration"]

        await self.rt.on_lesson_ack(self._ack(second_seq, 1, generation=generation))
        await asyncio.sleep(0)
        self.assertTrue(second.done())
        self.assertFalse(first.done())
        await self.rt.on_lesson_ack(self._ack(first_seq, 2, generation=generation))

        self.assertTrue((await first).accepted)
        self.assertTrue((await second).accepted)

    async def test_wrong_generation_and_negative_ack_are_no_motion_noops(self):
        task = asyncio.create_task(
            self.rt.send_visual_state("incorrect", motion_preset="tryAgain")
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        seq = frame["sequence"]
        generation = frame["body"]["visualGeneration"]

        await self.rt.on_lesson_ack(self._ack(seq, 1, generation=generation + 1))
        self.assertFalse(task.done())
        await self.rt.on_lesson_ack(
            self._ack(
                seq,
                1,
                generation=generation,
                accepted=False,
                degraded=False,
                degradedReason="unsupportedContract",
            )
        )
        result = await task
        self.assertFalse(result.accepted)
        self.assertIsNone(self.rt._motion_task)

    async def test_current_superseded_ack_resolves_rejected_without_timeout_or_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            task = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "incorrect", "robotOverlay.thinking", "tryAgain"
                )
            )
            await asyncio.sleep(0)
            frame = json.loads(self.rt.conn.websocket.sent[-1])
            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    accepted=True,
                    degraded=True,
                    degradedReason="superseded",
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(task.done(), "superseded is not an accepted fallback")
            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    accepted=False,
                    degraded=False,
                    degradedReason="superseded",
                )
            )
            await asyncio.sleep(0)
            try:
                self.assertTrue(task.done())
            finally:
                if not task.done():
                    await self.rt.close()
            self.assertFalse(await task)

        self.assertEqual(dispatched, [])
        self.assertEqual(
            sum(
                json.loads(payload)["type"] == "lesson_visual_state"
                for payload in self.rt.conn.websocket.sent
            ),
            1,
        )
        self.assertEqual(self.rt._visual_ack_waiters, {})
        self.assertEqual(self.rt._visual_ack_timeout_tasks, {})

    async def test_retired_superseded_ack_is_consumed_without_timeout_or_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            task = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "thinking", "robotOverlay.thinking", "thinking"
                )
            )
            await asyncio.sleep(0)
            frame = json.loads(self.rt.conn.websocket.sent[-1])
            await self.rt.pause()
            self.assertFalse(await task)
            self.assertIn(frame["sequence"], self.rt._retired_visual_ack_sequences)

            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    accepted=False,
                    degraded=False,
                    degradedReason="superseded",
                )
            )

        self.assertNotIn(frame["sequence"], self.rt._retired_visual_ack_sequences)
        self.assertEqual(self.rt._last_inbound_sequence, 1)
        self.assertEqual(
            sum(
                json.loads(payload)["type"] == "lesson_visual_state"
                for payload in self.rt.conn.websocket.sent
            ),
            1,
        )
        self.assertEqual(self.rt._visual_ack_timeout_tasks, {})
        self.assertEqual(dispatched, [])

    async def test_visual_ack_rejects_extra_or_wrong_typed_frozen_fields(self):
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        seq = frame["sequence"]
        generation = frame["body"]["visualGeneration"]

        wrong_body = self._ack(seq, 1, generation=generation)
        wrong_body["body"] = []
        await self.rt.on_lesson_ack(wrong_body)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        extra = self._ack(seq, 1, generation=generation)
        extra["body"]["unexpected"] = True
        await self.rt.on_lesson_ack(extra)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        wrong_sequence = self._ack(seq, True, generation=generation)
        await self.rt.on_lesson_ack(wrong_sequence)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        await self.rt.on_lesson_ack(self._ack(seq, 1, generation=generation))
        self.assertTrue((await task).accepted)

    async def test_timeout_retries_once_with_new_sequence_same_generation(self):
        sleeps = []

        async def immediate_sleep(delay):
            sleeps.append(delay)

        self.rt._sleep = immediate_sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        result = await self.rt.send_visual_state("thinking")
        frames = [
            json.loads(item)
            for item in self.rt.conn.websocket.sent
            if json.loads(item)["type"] == "lesson_visual_state"
        ]
        self.assertEqual(len(frames), 2)
        self.assertNotEqual(frames[0]["sequence"], frames[1]["sequence"])
        self.assertEqual(
            frames[0]["body"]["visualGeneration"],
            frames[1]["body"]["visualGeneration"],
        )
        self.assertFalse(result.accepted)
        self.assertEqual(len(sleeps), 2)

    async def test_late_ack_for_timed_out_attempt_advances_inbound_before_retry_ack(self):
        second_timeout = asyncio.Event()
        sleep_calls = 0

        async def controlled_sleep(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                return
            await second_timeout.wait()

        self.rt._sleep = controlled_sleep
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        try:
            for _ in range(10):
                await asyncio.sleep(0)
                frames = [
                    json.loads(item)
                    for item in self.rt.conn.websocket.sent
                    if json.loads(item)["type"] == "lesson_visual_state"
                ]
                if len(frames) == 2:
                    break
            self.assertEqual(len(frames), 2)
            first, retry = frames
            generation = retry["body"]["visualGeneration"]

            await self.rt.on_lesson_ack(
                self._ack(first["sequence"], 1, generation=generation)
            )
            await self.rt.on_lesson_ack(
                self._ack(retry["sequence"], 2, generation=generation)
            )
            await asyncio.sleep(0)

            self.assertTrue(task.done())
            self.assertTrue((await task).accepted)
            sent_types = [json.loads(item)["type"] for item in self.rt.conn.websocket.sent]
            self.assertNotIn("lesson_error", sent_types)
        finally:
            second_timeout.set()
            if not task.done():
                await self.rt.close()
                await task

    def test_retired_visual_sequences_are_bounded_and_pruned_on_generation_change(self):
        for sequence in range(140):
            self.rt._retire_visual_ack_sequence(sequence, self.rt._visual_generation, "s4")

        self.assertEqual(len(self.rt._retired_visual_ack_sequences), 128)
        self.assertNotIn(0, self.rt._retired_visual_ack_sequences)

        self.rt._cancel_visual_waiters(increment_generation=True, reason="replaced")
        self.assertEqual(self.rt._retired_visual_ack_sequences, {})

    async def test_pause_cancels_waiter_without_completing_step_and_resume_resends(self):
        self.rt._step_completed = False
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        old_generation = self.rt._visual_generation

        await self.rt.pause()
        paused = await task
        self.assertFalse(paused.accepted)
        self.assertFalse(self.rt._step_completed)
        self.assertEqual(self.rt._visual_generation, old_generation)

        resume = asyncio.create_task(self.rt.resume())
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        self.assertEqual(frame["type"], "lesson_visual_state")
        self.assertEqual(frame["body"]["visualGeneration"], old_generation + 1)
        await self.rt.on_lesson_ack(
            self._ack(frame["sequence"], 1, generation=old_generation + 1)
        )
        self.assertTrue((await resume).accepted)

    async def test_late_pause_ack_is_retired_before_resume_ack_sequence(self):
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        paused_frame = json.loads(self.rt.conn.websocket.sent[-1])

        await self.rt.pause()
        self.assertFalse((await task).accepted)
        self.assertEqual(self.rt._visual_ack_waiters, {})
        self.assertIn(paused_frame["sequence"], self.rt._retired_visual_ack_sequences)

        await self.rt.on_lesson_ack(
            self._ack(
                paused_frame["sequence"],
                1,
                generation=paused_frame["body"]["visualGeneration"],
            )
        )
        self.assertEqual(self.rt._last_inbound_sequence, 1)

        resume = asyncio.create_task(self.rt.resume())
        await asyncio.sleep(0)
        resumed_frame = json.loads(self.rt.conn.websocket.sent[-1])
        await self.rt.on_lesson_ack(
            self._ack(
                resumed_frame["sequence"],
                2,
                generation=resumed_frame["body"]["visualGeneration"],
            )
        )

        self.assertTrue((await resume).accepted)
        self.assertNotIn(
            "lesson_error",
            [json.loads(payload)["type"] for payload in self.rt.conn.websocket.sent],
        )

    async def test_pause_after_ack_resolution_prevents_stale_motion_dispatch(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        task = asyncio.create_task(
            self.rt.send_visual_state("correct", motion_preset="celebrate")
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])

        await self.rt.on_lesson_ack(
            self._ack(
                frame["sequence"],
                1,
                generation=frame["body"]["visualGeneration"],
            )
        )
        await self.rt.pause()
        result = await task

        self.assertTrue(result.accepted)
        self.assertIsNone(self.rt._motion_task)

    async def test_orchestrator_dispatches_authored_motion_once_after_matching_ack(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        self.rt._step = {
            "motion": {"correct": "celebrate"},
            "scene": {
                "robotOverlay": {"asset": {"key": "robotOverlay.celebrate"}}
            },
        }
        events = []

        async def motion(_conn, preset):
            events.append(("motion", preset))
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            task = asyncio.create_task(
                self.rt._apply_authored_visual_then_motion("correct", "correct")
            )
            await asyncio.sleep(0)
            frame = json.loads(self.rt.conn.websocket.sent[-1])
            events.append(("visual", frame["body"]["state"]))
            self.assertEqual(events, [("visual", "correct")])

            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    degraded=True,
                    degradedReason="reducedMotion",
                )
            )
            self.assertTrue(await task)
            self.assertEqual(events, [("visual", "correct"), ("motion", "celebrate")])
            self.assertFalse(
                await self.rt._dispatch_motion_once(
                    "celebrate", frame["body"]["visualGeneration"], self.rt._step_id
                )
            )

            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    degraded=True,
                    degradedReason="reducedMotion",
                )
            )
            await asyncio.sleep(0)
            self.assertEqual(events, [("visual", "correct"), ("motion", "celebrate")])

    async def test_orchestrator_assigns_new_generation_and_replacement_blocks_stale_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            first = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "incorrect", "robotOverlay.thinking", "tryAgain"
                )
            )
            await asyncio.sleep(0)
            first_frame = json.loads(self.rt.conn.websocket.sent[-1])
            second = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "retry", "robotOverlay.thinking", "tryAgain"
                )
            )
            await asyncio.sleep(0)
            second_frame = json.loads(self.rt.conn.websocket.sent[-1])

            self.assertGreater(
                second_frame["body"]["visualGeneration"],
                first_frame["body"]["visualGeneration"],
            )
            self.assertFalse(await first)
            await self.rt.on_lesson_ack(
                self._ack(
                    second_frame["sequence"],
                    1,
                    generation=second_frame["body"]["visualGeneration"],
                )
            )
            self.assertTrue(await second)
            self.assertEqual(dispatched, ["tryAgain"])

    async def test_rejected_timeout_pause_stop_disconnect_and_replacement_dispatch_no_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True

        async def assert_cancelled(action):
            task = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "thinking", "robotOverlay.thinking", "thinking"
                )
            )
            await asyncio.sleep(0)
            await action()
            self.assertFalse(await task)
            self.assertIsNone(self.rt._motion_task)

        await assert_cancelled(self.rt.pause)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.stop)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.on_disconnect)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.on_replaced)

        self.rt.state = S_RUNNING
        rejected = asyncio.create_task(
            self.rt._apply_visual_then_motion(
                "incorrect", "robotOverlay.thinking", "tryAgain"
            )
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        await self.rt.on_lesson_ack(
            self._ack(
                frame["sequence"],
                1,
                generation=frame["body"]["visualGeneration"],
                accepted=False,
                degraded=False,
                degradedReason="unsupportedContract",
            )
        )
        self.assertFalse(await rejected)
        self.assertIsNone(self.rt._motion_task)

        async def immediate_sleep(_delay):
            return None

        self.rt._sleep = immediate_sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        timed_out = await self.rt._apply_visual_then_motion(
            "thinking", "robotOverlay.thinking", "thinking"
        )
        self.assertFalse(timed_out)
        self.assertIsNone(self.rt._motion_task)

        self.rt._sleep = asyncio.sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 60
        cancelled = asyncio.create_task(
            self.rt._apply_visual_then_motion(
                "thinking", "robotOverlay.thinking", "thinking"
            )
        )
        await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertIsNone(self.rt._motion_task)


class CompletionVisualFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rt = _runtime()
        self.rt.negotiated_version = "teebot-lesson-renderer.v2"
        self.rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        self.rt.conn.device_id = "robot-01"
        self.rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "motion_presets_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        self.rt.conn.lesson_runtime = self.rt
        self.rt.state = S_RUNNING
        self.rt._step_index = len(self.rt._steps) - 1
        self.rt._step = self.rt._steps[-1]
        self.rt._step["motion"] = {"completion": "goodbye"}
        self.rt._step["scene"] = {
            "robotOverlay": {"asset": {"key": "robotOverlay.celebrate"}}
        }
        self.rt._step_id = self.rt._step["id"]
        self.rt._step_seq = 3
        self.rt._step_acked = True
        self.rt._step_completed = True

    def _frames(self, frame_type):
        return [
            json.loads(payload)
            for payload in self.rt.conn.websocket.sent
            if json.loads(payload)["type"] == frame_type
        ]

    def _ack(self, frame, inbound_sequence, **body):
        return {
            "type": "lesson_ack",
            "protocolVersion": "teebot-lesson-renderer.v2",
            "assignmentId": self.rt.assignment_id,
            "sessionId": self.rt.session_id,
            "stepId": self.rt._step_id if frame["type"] == "lesson_visual_state" else None,
            "sequence": inbound_sequence,
            "body": {
                "acks": frame["sequence"],
                "accepted": True,
                "degraded": False,
                "degradedReason": None,
                "visualGeneration": frame["body"].get(
                    "visualGeneration", self.rt._visual_generation
                ),
                **body,
            },
        }

    async def _begin_completion(self):
        await self.rt._maybe_finish_step()
        await asyncio.sleep(0)
        return self._frames("lesson_visual_state")[-1]

    async def _ack_stop(self, inbound_sequence):
        stop = self._frames("lesson_stop")[-1]
        await self.rt.on_lesson_ack(
            {
                "type": "lesson_ack",
                "protocolVersion": "teebot-lesson-renderer.v2",
                "assignmentId": self.rt.assignment_id,
                "sessionId": self.rt.session_id,
                "stepId": None,
                "sequence": inbound_sequence,
                "body": {
                    "acks": stop["sequence"],
                    "rendered": True,
                    "degraded": False,
                },
            }
        )
        self.assertEqual(self.rt.state, "COMPLETED")

    async def test_accepted_and_degraded_completion_dispatch_motion_then_stop(self):
        for degraded, reason in ((False, None), (True, "reducedMotion")):
            with self.subTest(degraded=degraded):
                if degraded:
                    self.setUp()
                dispatched = []

                async def motion(_conn, preset, calls=dispatched):
                    calls.append(preset)
                    return True

                with mock.patch(
                    "core.lesson.runtime.dispatch_motion_preset", side_effect=motion
                ):
                    visual = await self._begin_completion()
                    await self.rt.on_lesson_ack(
                        self._ack(
                            visual,
                            1,
                            degraded=degraded,
                            degradedReason=reason,
                        )
                    )
                    await self.rt._visual_transition_task

                self.assertEqual(dispatched, ["goodbye"])
                self.assertEqual(len(self._frames("lesson_stop")), 1)
                await self._ack_stop(2)

    async def test_rejected_completion_skips_motion_but_still_stops(self):
        visual = await self._begin_completion()
        with mock.patch("core.lesson.runtime.dispatch_motion_preset") as motion:
            await self.rt.on_lesson_ack(
                self._ack(
                    visual,
                    1,
                    accepted=False,
                    degraded=False,
                    degradedReason="unsupportedContract",
                )
            )
            await self.rt._visual_transition_task

        motion.assert_not_called()
        self.assertEqual(len(self._frames("lesson_stop")), 1)
        await self._ack_stop(2)

    async def test_timed_out_completion_skips_motion_but_still_stops(self):
        stop_timeout = asyncio.Event()
        sleep_calls = 0

        async def immediate_sleep(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls <= 2:
                return None
            await stop_timeout.wait()

        self.rt._sleep = immediate_sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        try:
            with mock.patch("core.lesson.runtime.dispatch_motion_preset") as motion:
                await self.rt._maybe_finish_step()
                await self.rt._visual_transition_task

            motion.assert_not_called()
            self.assertEqual(len(self._frames("lesson_visual_state")), 2)
            self.assertEqual(len(self._frames("lesson_stop")), 1)
            await self._ack_stop(1)
        finally:
            stop_timeout.set()

    async def test_pause_stop_replacement_and_cancellation_suppress_completed_stop(self):
        async def cancel_task(rt):
            rt._visual_transition_task.cancel()
            await asyncio.gather(rt._visual_transition_task, return_exceptions=True)

        for action_name in ("pause", "stop", "on_replaced", "cancel"):
            with self.subTest(action=action_name):
                self.setUp()
                await self._begin_completion()
                if action_name == "cancel":
                    await cancel_task(self.rt)
                else:
                    await getattr(self.rt, action_name)()
                    await asyncio.gather(
                        self.rt._visual_transition_task, return_exceptions=True
                    )

                completed_stops = [
                    frame
                    for frame in self._frames("lesson_stop")
                    if frame["body"].get("reason") == "COMPLETED"
                ]
                self.assertEqual(completed_stops, [])
                self.assertFalse(self.rt._completion_stop_sent)

    async def test_pause_then_resume_completion_ack_finishes_lesson(self):
        visual = await self._begin_completion()
        await self.rt.pause()
        await self.rt._visual_transition_task
        self.assertFalse(self.rt._completion_stop_sent)

        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            return True

        with mock.patch(
            "core.lesson.runtime.dispatch_motion_preset", side_effect=motion
        ):
            resume = asyncio.create_task(self.rt.resume())
            await asyncio.sleep(0)
            resumed_visual = self._frames("lesson_visual_state")[-1]
            self.assertNotEqual(resumed_visual["sequence"], visual["sequence"])
            await self.rt.on_lesson_ack(self._ack(resumed_visual, 1))
            self.assertTrue((await resume).accepted)

        self.assertEqual(dispatched, ["goodbye"])
        self.assertEqual(len(self._frames("lesson_stop")), 1)
        await self._ack_stop(2)


class StepVisualContinuationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rt = _runtime()
        self.rt.negotiated_version = "teebot-lesson-renderer.v2"
        self.rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        self.rt.conn.device_id = "robot-01"
        self.rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "motion_presets_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        self.rt.conn.voice_provider = T._RecordingLessonVoiceProvider()
        self.rt.conn.lesson_runtime = self.rt
        self.rt.state = S_RUNNING
        self.rt._step_index = 0
        self.rt._step = self.rt._steps[0]
        self.rt._step.update(
            {
                "completionClass": "interactive",
                "robotState": "modeling",
                "motion": {"present": "teach", "listen": "listen"},
                "scene": {
                    "robotOverlay": {"asset": {"key": "robotOverlay.teach"}}
                },
            }
        )
        self.rt._step_id = self.rt._step["id"]
        self.rt._step_seq = 3
        self.rt._step_passive = False

    def _visual_frames(self):
        return [
            json.loads(payload)
            for payload in self.rt.conn.websocket.sent
            if json.loads(payload)["type"] == "lesson_visual_state"
        ]

    def _ack(self, frame, inbound_sequence):
        return {
            "type": "lesson_ack",
            "protocolVersion": "teebot-lesson-renderer.v2",
            "assignmentId": self.rt.assignment_id,
            "sessionId": self.rt.session_id,
            "stepId": self.rt._step_id,
            "sequence": inbound_sequence,
            "body": {
                "acks": frame["sequence"],
                "accepted": True,
                "degraded": False,
                "degradedReason": None,
                "visualGeneration": frame["body"]["visualGeneration"],
            },
        }

    async def test_prompt_and_input_wait_for_teach_then_listen_visuals(self):
        await self.rt._on_frame_acked({"type": "lesson_step"}, {})
        await asyncio.sleep(0)
        teach = self._visual_frames()[-1]
        self.assertEqual(teach["body"]["state"], "teach")
        self.assertEqual(self.rt.conn.voice_provider.prompts, [])
        self.assertEqual(self.rt.conn.voice_provider.child_response_windows, [])

        early = asyncio.create_task(
            self.rt.on_child_response("barn", source="internal_dev_endpoint")
        )
        try:
            await asyncio.sleep(0)
            self.assertTrue(early.done())
            self.assertFalse(await early)
        finally:
            if not early.done():
                early.cancel()
                await asyncio.gather(early, return_exceptions=True)

        await self.rt.on_lesson_ack(self._ack(teach, 1))
        await asyncio.sleep(0)
        listen = self._visual_frames()[-1]
        self.assertEqual(listen["body"]["state"], "listen")
        self.assertEqual(self.rt.conn.voice_provider.prompts, [])
        self.assertEqual(self.rt.conn.voice_provider.child_response_windows, [])

        await self.rt.on_lesson_ack(self._ack(listen, 2))
        await self.rt._visual_transition_task
        self.assertEqual(len(self.rt.conn.voice_provider.prompts), 1)
        self.assertEqual(self.rt.conn.voice_provider.child_response_windows, [True])

    async def test_pause_during_teach_motion_resumes_listen_then_continuation(self):
        motion_started = asyncio.Event()
        release_motion = asyncio.Event()
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            if len(dispatched) == 1:
                motion_started.set()
                await release_motion.wait()
            return True

        with mock.patch(
            "core.lesson.runtime.dispatch_motion_preset", side_effect=motion
        ):
            await self.rt._on_frame_acked({"type": "lesson_step"}, {})
            await asyncio.sleep(0)
            teach = self._visual_frames()[-1]
            await self.rt.on_lesson_ack(self._ack(teach, 1))
            await motion_started.wait()

            await self.rt.pause()
            release_motion.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertTrue(self.rt._visual_transition_task.done())
            self.assertFalse(self.rt._step_visuals_ready)
            self.assertEqual(self.rt.conn.voice_provider.prompts, [])

            resume = asyncio.create_task(self.rt.resume())
            await asyncio.sleep(0)
            resumed_teach = self._visual_frames()[-1]
            await self.rt.on_lesson_ack(self._ack(resumed_teach, 2))
            for _ in range(10):
                await asyncio.sleep(0)
                if self._visual_frames()[-1]["body"]["state"] == "listen":
                    break
            listen = self._visual_frames()[-1]
            self.assertEqual(listen["body"]["state"], "listen")
            await self.rt.on_lesson_ack(self._ack(listen, 3))
            self.assertTrue((await resume).accepted)

        self.assertEqual(dispatched, ["teach", "teach", "listen"])
        self.assertTrue(self.rt._step_visuals_ready)
        self.assertEqual(len(self.rt.conn.voice_provider.prompts), 1)
        self.assertEqual(self.rt.conn.voice_provider.child_response_windows, [True])

    async def test_pause_during_listen_motion_resumes_current_continuation(self):
        listen_motion_started = asyncio.Event()
        release_listen_motion = asyncio.Event()
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            if preset == "listen" and dispatched.count("listen") == 1:
                listen_motion_started.set()
                await release_listen_motion.wait()
            return True

        with mock.patch(
            "core.lesson.runtime.dispatch_motion_preset", side_effect=motion
        ):
            await self.rt._on_frame_acked({"type": "lesson_step"}, {})
            await asyncio.sleep(0)
            teach = self._visual_frames()[-1]
            await self.rt.on_lesson_ack(self._ack(teach, 1))
            for _ in range(10):
                await asyncio.sleep(0)
                if self._visual_frames()[-1]["body"]["state"] == "listen":
                    break
            listen = self._visual_frames()[-1]
            await self.rt.on_lesson_ack(self._ack(listen, 2))
            await listen_motion_started.wait()

            await self.rt.pause()
            release_listen_motion.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertTrue(self.rt._visual_transition_task.done())
            self.assertFalse(self.rt._step_visuals_ready)

            resume = asyncio.create_task(self.rt.resume())
            await asyncio.sleep(0)
            resumed_listen = self._visual_frames()[-1]
            self.assertEqual(resumed_listen["body"]["state"], "listen")
            await self.rt.on_lesson_ack(self._ack(resumed_listen, 3))
            self.assertTrue((await resume).accepted)

        self.assertEqual(dispatched, ["teach", "listen", "listen"])
        self.assertTrue(self.rt._step_visuals_ready)
        self.assertEqual(len(self.rt.conn.voice_provider.prompts), 1)
        self.assertEqual(self.rt.conn.voice_provider.child_response_windows, [True])

class ProgressNonCompletionBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 408->exit: a non-step_completed progress event is forwarded but not latched ─
    async def test_non_completion_progress_event_is_forwarded_without_latch(self):
        conn = T._FakeConn()
        forwarder = T._FakeForwarder()
        rt = _runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(T._ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(T._ack(2, 2))
        await rt.on_lesson_ack(T._ack(3, 3, step_id="s4"))

        completed_before = rt._steps_completed

        # event != step_completed -> forwarded for observability, NOT latched.
        await rt.on_lesson_progress(
            T._progress(4, {"event": "step_started", "stepType": "model"}, step_id="s4")
        )

        forwarded = [
            b["events"][0]["type"]
            for b in forwarder.batches
            if b.get("events")
        ]
        self.assertIn("step_started", forwarded)
        # No completion latched by a non-completion event.
        self.assertEqual(rt._steps_completed, completed_before)
        self.assertFalse(rt._step_completed)


class LessonErrorBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 460->462: non-int sequence skips the inbound bump but still processes ──────
    async def test_lesson_error_with_non_int_sequence_still_records(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_RUNNING
        prior_seq = rt._last_inbound_sequence

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": None,
                "body": {"code": "DISPLAY_FAULT", "message": "x"},
            }
        )

        # sequence bump skipped (None is not an int) ...
        self.assertEqual(rt._last_inbound_sequence, prior_seq)
        # ... but the error still recorded + the running run failed.
        self.assertEqual(rt.last_error.code, "DISPLAY_FAULT")
        self.assertEqual(rt.state, S_FAILED)

    # ── 471->exit: error in a non-running, non-terminal state does NOT fail run ────
    async def test_lesson_error_in_ready_state_records_without_failing(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_READY  # not RUNNING/PRELOADING, not terminal

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": 9,
                "body": {"code": "TRANSIENT", "message": "blip"},
            }
        )

        # Recorded for accounting, but the run is NOT failed (471 false edge).
        self.assertEqual(rt.last_error.code, "TRANSIENT")
        self.assertEqual(rt.state, S_READY)
        self.assertEqual(rt._last_inbound_sequence, 9)


class RuntimeLogContextTest(unittest.TestCase):
    def test_runtime_warning_log_includes_assignment_and_session_id(self):
        messages = []

        class _CapturingLogger(T._DummyLogger):
            def warning(self, message, *args, **kwargs):
                messages.append(str(message))

        conn = T._FakeConn(session_id="sess-ctx")
        conn.logger = _CapturingLogger()
        rt = _runtime(conn=conn)

        rt._log("warning", "runtime degraded")

        self.assertEqual(len(messages), 1)
        self.assertIn("runtime degraded", messages[0])
        self.assertIn(f"assignment_id={rt.assignment_id}", messages[0])
        self.assertIn(f"session_id={rt.session_id}", messages[0])


class FrameAckUnknownTypeBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 518->exit: an unrecognized acked frame type is a no-op state-wise ─────────
    async def test_on_frame_acked_ignores_unknown_frame_type(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_RUNNING

        # No matching elif arm -> falls through, state untouched, no crash.
        await rt._on_frame_acked({"type": "totally_unknown_frame"}, {})

        self.assertEqual(rt.state, S_RUNNING)


class EmitNonStandardFrameTypeBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 912->914: emit a frame type outside step + prepare/start/stop ─────────────
    async def test_emit_non_standard_frame_type_skips_both_log_branches(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)

        seq = await rt._emit("lesson_error", body={"code": "X"})

        # The frame was still sent on the wire (neither log branch is required for it).
        sent = [__import__("json").loads(p) for p in conn.websocket.sent]
        emitted = [f for f in sent if f["type"] == "lesson_error" and f["sequence"] == seq]
        self.assertEqual(len(emitted), 1)


class PrepareBodySdPackBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1018->1025: SD pack enabled but cache has no callable asset_pack_manifest ──
    def test_prepare_body_sd_pack_without_manifest_helper_omits_asset_pack(self):
        # Enable SD pack mode via conn.config so _sd_asset_pack_enabled() is True.
        conn = T._FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}

        class _NoPackManifestCache(T._FakeAssetCache):
            # asset_pack_manifest deliberately NOT callable (None) -> getattr -> None.
            asset_pack_manifest = None

        rt = _runtime(conn=conn, asset_cache=_NoPackManifestCache(ready=True))

        body = rt._prepare_body()

        # SD pack path taken, but no callable manifest helper -> no assetPack key.
        self.assertTrue(rt._sd_asset_pack_enabled())
        self.assertNotIn("assetPack", body)


class RewriteRequiredLayerSourcesBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1123->1120: a None required-asset node is skipped, loop continues ─────────
    def test_rewrite_skips_none_required_nodes(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)

        # backgroundScene present (a dict node), teachingObject/robotOverlay absent ->
        # _required_lesson_step_asset_nodes yields None for the two missing layers, so
        # the ``if isinstance(node, dict)`` rewrite is skipped for them (branch 1123->1120).
        scene = {
            "backgroundScene": {"poster": {"src": "barn.png"}},
            # teachingObject + robotOverlay intentionally missing -> None nodes.
        }

        # local_pack_url_for_source on _FakeAssetCache is callable, so the loop runs.
        rt._rewrite_required_sd_pack_layer_sources(scene)

        # The present node was rewritten; the absent layers stayed absent (no crash on
        # the None nodes -> the branch was exercised).
        self.assertIsInstance(scene["backgroundScene"]["poster"]["src"], str)
        self.assertNotIn("teachingObject", scene)
        self.assertNotIn("robotOverlay", scene)


class CinematicContractTest(unittest.TestCase):
    def test_projects_one_known_phase_from_ready_pack_without_online_data(self):
        spec = importlib.util.find_spec("core.lesson.cinematic_contract")
        self.assertIsNotNone(spec, "cinematic contract module must exist")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        phase = _cinematic_manifest()["cinematicPhases"][0]
        pack = _CinematicAssetCache().asset_pack_manifest(
            assignment_version=1,
            lesson_id="lesson",
            lesson_version=3,
            manifest_checksum=T._manifest_checksum(),
        )

        projected = module.project_cinematic_phase(phase, pack)

        self.assertEqual(projected["phaseId"], "opening")
        self.assertEqual(projected["templateId"], "directMp4Cinematic")
        self.assertEqual(projected["templateVersion"], 1)
        self.assertEqual(projected["durationMs"], 9000)
        self.assertEqual(projected["fps"], 10)
        self.assertEqual(projected["frameCount"], 90)
        self.assertEqual([layer["layer"] for layer in projected["layers"]], [
            "background", "teachingObject", "robotOverlay",
        ])
        for source, layer in zip(phase["layers"], projected["layers"]):
            self.assertTrue(layer["sdPath"].startswith("sd://tbot/lesson-assets/"))
            self.assertEqual(layer["assetVersionId"], source["assetVersionId"])
            self.assertEqual(layer["version"], source["version"])
            self.assertEqual(layer["sha256"], source["sha256"])
            self.assertEqual(layer["bytes"], source["bytes"])
            self.assertEqual(layer["rect"], source["metadata"]["rect"])
            self.assertEqual(layer["chromaKey"], source["metadata"]["chromaKey"])
        wire = json.dumps(projected, sort_keys=True)
        self.assertNotIn("http://", wire)
        self.assertNotIn("https://", wire)
        self.assertNotRegex(wire.lower(), r"token|credential|authorization|cookie|signed")

    def test_rejects_not_ready_missing_paths_and_mismatched_phase_metadata(self):
        spec = importlib.util.find_spec("core.lesson.cinematic_contract")
        self.assertIsNotNone(spec, "cinematic contract module must exist")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        phase = _cinematic_manifest()["cinematicPhases"][0]
        base_pack = _CinematicAssetCache().asset_pack_manifest(
            assignment_version=1,
            lesson_id="lesson",
            lesson_version=3,
            manifest_checksum=T._manifest_checksum(),
        )
        mutations = (
            ("pack not ready", lambda p, _phase: p.update(ready=False), "CINEMATIC_PACK_NOT_READY"),
            ("missing path", lambda p, _phase: (p["assets"][0].pop("localPath"), p["assets"][0].pop("sdPath")), "CINEMATIC_SD_PATH_MISSING"),
            ("online path", lambda p, _phase: p["assets"][0].update(localPath="https://cdn.test/a.mp4"), "CINEMATIC_SD_PATH_MISSING"),
            ("query path", lambda p, _phase: p["assets"][0].update(localPath=p["assets"][0]["localPath"] + "?token=x", sdPath=p["assets"][0]["sdPath"] + "?token=x"), "CINEMATIC_SD_PATH_MISSING"),
            ("fragment path", lambda p, _phase: p["assets"][0].update(localPath=p["assets"][0]["localPath"] + "#secret", sdPath=p["assets"][0]["sdPath"] + "#secret"), "CINEMATIC_SD_PATH_MISSING"),
            ("userinfo path", lambda p, _phase: p["assets"][0].update(localPath=p["assets"][0]["localPath"] + "@user:pass", sdPath=p["assets"][0]["sdPath"] + "@user:pass"), "CINEMATIC_SD_PATH_MISSING"),
            ("metadata mismatch", lambda _p, ph: ph["layers"][0]["metadata"].update(frameCount=89), "CINEMATIC_METADATA_MISMATCH"),
        )
        for label, mutate, code in mutations:
            with self.subTest(label=label):
                pack = copy.deepcopy(base_pack)
                candidate = copy.deepcopy(phase)
                mutate(pack, candidate)
                with self.assertRaises(module.CinematicContractError) as ctx:
                    module.project_cinematic_phase(candidate, pack)
                self.assertEqual(ctx.exception.code, code)


class CinematicRuntimeTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _frames(rt):
        return [json.loads(payload) for payload in rt.conn.websocket.sent]

    @staticmethod
    def _ack(
        rt,
        frame,
        inbound_sequence,
        *,
        event=None,
        phase_id=None,
        command_sequence_id=None,
        accepted=True,
    ):
        body = frame["body"]
        command = body.get("cinematicPhase") if isinstance(body.get("cinematicPhase"), dict) else body
        command_kind = command.get("command")
        ack = {
            "event": event or (
                "frameZeroReady" if command_kind == "prepare"
                else "phaseReady" if command_kind == "start"
                else "commandApplied"
            ),
            "command": command_kind,
            "phaseId": phase_id or command.get("phaseId"),
            "commandSequenceId": (
                command_sequence_id
                if command_sequence_id is not None
                else command.get("commandSequenceId", body.get("commandSequenceId"))
            ),
            "accepted": accepted,
        }
        if command_kind == "prepare":
            ack["frameZeroReady"] = accepted
        elif command_kind == "start":
            ack["phaseReady"] = accepted
        return {
            "type": "lesson_ack",
            "protocolVersion": RENDERER_V3,
            "assignmentId": rt.assignment_id,
            "sessionId": rt.session_id,
            "lessonId": rt.lesson_id,
            "lessonVersion": rt.lesson_version,
            "stepId": frame.get("stepId"),
            "sequence": inbound_sequence,
            "timestamp": 1,
            "body": {
                "acks": frame["sequence"],
                "assetPack": {
                    "ready": True,
                    "cacheKey": rt.asset_cache.cache_key,
                },
                "cinematicPhase": ack,
            },
        }

    async def _advance_to_running(self, rt):
        await rt.start()
        prepare = self._frames(rt)[-1]
        await rt.on_lesson_ack(self._ack(rt, prepare, 1))
        self.assertEqual(rt.state, S_READY)
        start = self._frames(rt)[-1]
        await rt.on_lesson_ack(self._ack(rt, start, 2))
        self.assertEqual(rt.state, S_RUNNING)
        self.assertEqual(self._frames(rt)[-1]["type"], "lesson_step")
        return 3

    async def test_exact_v3_direct_mp4_capability_gates_prepare(self):
        for renderer in ("teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"):
            with self.subTest(renderer=renderer):
                conn = T._FakeConn(features={"lesson": True, "renderer": [renderer]})
                rt = _cinematic_runtime(conn=conn)
                with self.assertRaises(LessonError) as ctx:
                    await rt.start()
                self.assertEqual(ctx.exception.code, "CINEMATIC_CAPABILITY_UNSUPPORTED")
                self.assertEqual(conn.websocket.sent, [])

        conn = T._FakeConn(features={"lesson": True, "renderer": [RENDERER_V3]})
        rt = _cinematic_runtime(conn=conn)
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "CINEMATIC_CAPABILITY_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])

    async def test_prepare_and_start_each_require_their_own_typed_ready_ack(self):
        rt = _cinematic_runtime()
        await rt.start()
        frames = self._frames(rt)
        self.assertEqual([frame["type"] for frame in frames], ["lesson_prepare"])
        prepare = frames[0]
        command = prepare["body"]["cinematicPhase"]
        self.assertEqual(command["command"], "prepare")
        self.assertEqual(command["phaseId"], "opening")
        self.assertEqual(command["commandSequenceId"], prepare["sequence"])
        self.assertEqual(command["durationMs"], 9000)
        self.assertEqual(command["fps"], 10)
        self.assertEqual(command["frameCount"], 90)
        self.assertEqual(len(command["layers"]), 3)
        self.assertNotIn("nextPhase", command)
        self.assertNotIn("branches", command)
        self.assertNotIn("cinematicPhases", prepare["body"])

        generic = self._ack(rt, prepare, 1)
        generic["body"].pop("cinematicPhase")
        await rt.on_lesson_ack(generic)
        self.assertEqual([f["type"] for f in self._frames(rt)], ["lesson_prepare"])

        wrong = self._ack(rt, prepare, 1, event="phaseReady")
        wrong["body"]["cinematicPhase"]["phaseReady"] = True
        await rt.on_lesson_ack(wrong)
        self.assertEqual([f["type"] for f in self._frames(rt)], ["lesson_prepare"])
        wrong = self._ack(rt, prepare, 1, phase_id="greet")
        await rt.on_lesson_ack(wrong)
        self.assertEqual([f["type"] for f in self._frames(rt)], ["lesson_prepare"])

        ready = self._ack(rt, prepare, 1)
        await rt.on_lesson_ack(ready)
        frames = self._frames(rt)
        self.assertEqual([frame["type"] for frame in frames], ["lesson_prepare", "lesson_start"])
        start = frames[-1]
        self.assertEqual(rt.state, S_READY)
        self.assertEqual(start["body"]["cinematicPhase"], {
            "command": "start",
            "phaseId": "opening",
            "commandSequenceId": start["sequence"],
        })

        await rt.on_lesson_ack(ready)
        self.assertEqual(len(self._frames(rt)), 2)

        generic_start = self._ack(rt, start, 2)
        generic_start["body"].pop("cinematicPhase")
        await rt.on_lesson_ack(generic_start)
        self.assertEqual(rt.state, S_READY)
        self.assertEqual(len(self._frames(rt)), 2)

        wrong_start = self._ack(rt, start, 2, event="frameZeroReady")
        wrong_start["body"]["cinematicPhase"]["frameZeroReady"] = True
        await rt.on_lesson_ack(wrong_start)
        self.assertEqual(rt.state, S_READY)
        self.assertEqual(len(self._frames(rt)), 2)

        phase_ready = self._ack(rt, start, 2)
        await rt.on_lesson_ack(phase_ready)
        self.assertEqual(rt.state, S_RUNNING)
        self.assertEqual([f["type"] for f in self._frames(rt)], [
            "lesson_prepare", "lesson_start", "lesson_step",
        ])
        await rt.on_lesson_ack(phase_ready)
        self.assertEqual(len(self._frames(rt)), 3)

    async def test_prepare_retry_reuses_command_identity_and_lifecycle_commands_are_idempotent(self):
        sleep = T._GatedSleep()
        rt = _cinematic_runtime(sleep=sleep)
        rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        await rt.start()
        await asyncio.sleep(0)
        first = self._frames(rt)[0]
        sleep.release_next()
        await asyncio.sleep(0)
        retry = self._frames(rt)[-1]
        self.assertNotEqual(retry["sequence"], first["sequence"])
        self.assertEqual(
            retry["body"]["cinematicPhase"]["commandSequenceId"],
            first["body"]["cinematicPhase"]["commandSequenceId"],
        )

        await rt.on_lesson_ack(self._ack(rt, retry, 1))
        start = self._frames(rt)[-1]
        await rt.on_lesson_ack(self._ack(rt, start, 2))
        self.assertEqual(self._frames(rt)[-1]["type"], "lesson_step")

        await rt.pause()
        await rt.pause()
        pause_frames = [f for f in self._frames(rt) if f["type"] == "lesson_cinematic_control" and f["body"]["command"] == "pause"]
        self.assertEqual(len(pause_frames), 1)
        self.assertEqual(rt.state, S_RUNNING)
        generic_pause = self._ack(rt, pause_frames[0], 3)
        generic_pause["body"].pop("cinematicPhase")
        await rt.on_lesson_ack(generic_pause)
        self.assertEqual(rt.state, S_RUNNING)
        await rt.on_lesson_ack(self._ack(rt, pause_frames[0], 3))
        self.assertEqual(rt.state, S_PAUSED)
        step = [f for f in self._frames(rt) if f["type"] == "lesson_step"][-1]
        stale_step_ack = T._ack(step["sequence"], 4, step_id=step["stepId"])
        stale_step_ack["protocolVersion"] = RENDERER_V3
        await rt.on_lesson_ack(stale_step_ack)
        self.assertFalse(rt._step_acked)

        await rt.resume()
        await rt.resume()
        resume_frames = [f for f in self._frames(rt) if f["type"] == "lesson_cinematic_control" and f["body"]["command"] == "resume"]
        self.assertEqual(len(resume_frames), 1)
        self.assertEqual(rt.state, S_PAUSED)
        self.assertGreater(resume_frames[0]["body"]["clockRebaseSequenceId"], pause_frames[0]["body"]["commandSequenceId"])
        await rt.on_lesson_ack(self._ack(rt, resume_frames[0], 4))
        self.assertEqual(rt.state, S_RUNNING)

        await rt.stop()
        await rt.stop()
        stop_frames = [f for f in self._frames(rt) if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop_frames), 1)
        self.assertEqual(rt.state, S_RUNNING)
        self.assertEqual(stop_frames[0]["body"]["cinematicPhase"]["command"], "stop")
        await rt.on_lesson_ack(self._ack(rt, stop_frames[0], 5))
        self.assertEqual(rt.state, S_COMPLETED)
        self.assertIsNone(rt._cinematic_phase)

    async def test_cancel_is_typed_idempotent_and_does_not_offer_firmware_branch_selection(self):
        rt = _cinematic_runtime()
        rt.state = S_RUNNING
        rt._cinematic_phase = {
            "phaseId": "opening",
        }
        await rt.cancel("assignmentReplaced")
        await rt.cancel("assignmentReplaced")
        frames = self._frames(rt)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["type"], "lesson_cinematic_control")
        self.assertEqual(frames[0]["body"]["command"], "cancel")
        self.assertEqual(frames[0]["body"]["reason"], "assignmentReplaced")
        self.assertEqual(rt.state, S_RUNNING)
        generic = self._ack(rt, frames[0], 1)
        generic["body"].pop("cinematicPhase")
        await rt.on_lesson_ack(generic)
        self.assertEqual(rt.state, S_RUNNING)
        await rt.on_lesson_ack(self._ack(rt, frames[0], 1))
        self.assertEqual(rt.state, S_COMPLETED)
        self.assertIsNone(rt._cinematic_phase)
        wire = json.dumps(frames[0])
        self.assertNotRegex(wire, r"nextPhase|nextStep|branch|choice")

    async def test_step_ack_between_pause_command_and_ack_is_consumed_then_resumed_once(self):
        rt = _cinematic_runtime()
        next_inbound = await self._advance_to_running(rt)
        step = [f for f in self._frames(rt) if f["type"] == "lesson_step"][-1]

        await rt.pause()
        pause = self._frames(rt)[-1]
        self.assertEqual(pause["body"]["command"], "pause")
        step_ack = T._ack(step["sequence"], next_inbound, step_id=step["stepId"])
        step_ack["protocolVersion"] = RENDERER_V3
        await rt.on_lesson_ack(step_ack)

        self.assertEqual(rt._last_inbound_sequence, next_inbound)
        self.assertNotIn(step["sequence"], rt._outstanding)
        self.assertFalse(rt._step_acked)
        self.assertIsNotNone(rt._cinematic_deferred_step_ack)
        sent_before_duplicate = len(self._frames(rt))
        await rt.on_lesson_ack(step_ack)
        self.assertEqual(len(self._frames(rt)), sent_before_duplicate)
        self.assertEqual(rt._last_inbound_sequence, next_inbound)

        await rt.on_lesson_ack(self._ack(rt, pause, next_inbound + 1))
        self.assertEqual(rt.state, S_PAUSED)
        self.assertIsNone(rt.last_error)
        await rt.resume()
        resume = self._frames(rt)[-1]
        await rt.on_lesson_ack(self._ack(rt, resume, next_inbound + 2))

        self.assertEqual(rt.state, S_RUNNING)
        self.assertTrue(rt._step_acked)
        self.assertTrue(rt._step_visuals_ready)
        self.assertIsNone(rt._cinematic_deferred_step_ack)
        sent_after_resume = len(self._frames(rt))
        await rt.on_lesson_ack(self._ack(rt, resume, next_inbound + 2))
        self.assertEqual(len(self._frames(rt)), sent_after_resume)

    async def test_stop_and_cancel_discard_deferred_step_ack_without_leaks(self):
        for command in ("stop", "cancel"):
            with self.subTest(command=command):
                rt = _cinematic_runtime()
                next_inbound = await self._advance_to_running(rt)
                step = [f for f in self._frames(rt) if f["type"] == "lesson_step"][-1]
                if command == "stop":
                    await rt.stop()
                else:
                    await rt.cancel("assignmentReplaced")
                terminal = self._frames(rt)[-1]
                step_ack = T._ack(step["sequence"], next_inbound, step_id=step["stepId"])
                step_ack["protocolVersion"] = RENDERER_V3

                await rt.on_lesson_ack(step_ack)
                self.assertEqual(rt._last_inbound_sequence, next_inbound)
                self.assertIsNotNone(rt._cinematic_deferred_step_ack)
                self.assertNotIn(step["sequence"], rt._outstanding)
                await rt.on_lesson_ack(self._ack(rt, terminal, next_inbound + 1))

                self.assertEqual(rt.state, S_COMPLETED)
                self.assertIsNone(rt._cinematic_deferred_step_ack)
                self.assertIsNone(rt._cinematic_pending_command)
                self.assertFalse(any(
                    rt._cinematic_frame_command(frame) is not None
                    for frame in rt._outstanding.values()
                ))

    async def test_step_ack_during_pending_resume_is_consumed_and_applied_once(self):
        rt = _cinematic_runtime()
        next_inbound = await self._advance_to_running(rt)
        step = [f for f in self._frames(rt) if f["type"] == "lesson_step"][-1]
        await rt.pause()
        pause = self._frames(rt)[-1]
        await rt.on_lesson_ack(self._ack(rt, pause, next_inbound))
        self.assertEqual(rt.state, S_PAUSED)

        await rt.resume()
        resume = self._frames(rt)[-1]
        step_ack = T._ack(step["sequence"], next_inbound + 1, step_id=step["stepId"])
        step_ack["protocolVersion"] = RENDERER_V3
        await rt.on_lesson_ack(step_ack)

        self.assertEqual(rt._last_inbound_sequence, next_inbound + 1)
        self.assertEqual(rt.state, S_PAUSED)
        self.assertFalse(rt._step_acked)
        self.assertNotIn(step["sequence"], rt._outstanding)
        self.assertIsNotNone(rt._cinematic_deferred_step_ack)
        await rt.on_lesson_ack(step_ack)
        self.assertEqual(rt._last_inbound_sequence, next_inbound + 1)

        await rt.on_lesson_ack(self._ack(rt, resume, next_inbound + 2))
        self.assertEqual(rt.state, S_RUNNING)
        self.assertTrue(rt._step_acked)
        self.assertTrue(rt._step_visuals_ready)
        self.assertIsNone(rt._cinematic_deferred_step_ack)
        self.assertIsNone(rt._cinematic_pending_command)
        self.assertNotIn(resume["sequence"], rt._outstanding)
        sent = len(self._frames(rt))
        await rt.on_lesson_ack(self._ack(rt, resume, next_inbound + 2))
        self.assertEqual(len(self._frames(rt)), sent)


if __name__ == "__main__":
    unittest.main()
