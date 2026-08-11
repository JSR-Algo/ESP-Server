"""End-to-end runtime-flow test for the esp-server learning leg.

This file is the esp-journey companion to ``tests/test_lesson_runtime.py``. That
sibling already proves the byte-consistent wire thread and a 9-step voice-start
walk (``test_voice_transcript_start_lesson_loads_backend_manifest_and_plays_all_steps``).
This file EXTENDS the e2e coverage with the cross-component assertions the sibling
does not make:

  * an ORDERING oracle — every step's ``lesson_step`` frame reaches the (fake)
    firmware, then that step is ACKed as rendered, BEFORE the spoken prompt is
    handed to the voice provider, asserted from a single interleaved transcript of
    WS-send + ACK + prompt-speak events (the sibling drives prompts through a
    Live-client stub and never interleaves the three);
  * the COPPA child-speech strip driven through the REAL
    ``config.manage_api_client.post_lesson_event`` boundary — the runtime forwards
    ``recognizedText`` to the forwarder, and the boundary drops it + renames
    ``result``->``outcome`` before the body leaves the ESP. The sibling only checks
    that the forwarder BATCH still carries ``recognizedText`` (pre-boundary);
  * the voice path resolves the WebSocket device id to a backend UUID/JWT, then
    binds the assignment pull, manifest pull, and forwarder to that minted identity.

Harness is reused from the sibling module (frozen S2 wire fixture, fake backend via
monkeypatched ``config.manage_api_client``, fake voice/ws/device, fake AssetCache).
Async tests use ``unittest.IsolatedAsyncioTestCase`` (no pytest-asyncio markers).
Run with ``.venv311/bin/python -m pytest tests/test_lesson_e2e_flow.py``.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as L  # noqa: E402  (sibling test harness)

FIX = L.FIX
MINTED_DEVICE_UUID = "14140000-0000-4000-8000-000000000042"
MINTED_DEVICE_JWT = "minted-device-jwt"


# ── interleaved ordering oracle ─────────────────────────────────────────────────


class _OrderingWebSocket:
    """A WS whose every send appends a ('frame', type, stepId) row to a SHARED
    interleave log, so frame-vs-prompt ordering is observable across components."""

    def __init__(self, log):
        self.sent = []
        self._log = log

    async def send(self, payload):
        self.sent.append(payload)
        frame = json.loads(payload)
        self._log.append(("frame", frame.get("type"), frame.get("stepId")))


class _OrderingVoiceProvider:
    """Voice provider that records prompt-speak + child-response-window opens into
    the SAME interleave log as the WS, so a single ordered transcript proves the
    'frame to firmware -> ACK rendered -> prompt to child' contract per step."""

    def __init__(self, log):
        self._log = log
        self.prompts = []
        self.child_response_windows = 0

    async def speak_lesson_step_prompt(self, text):
        self.prompts.append(text)
        self._log.append(("prompt", text, None))
        return True

    async def open_lesson_child_response_window(self):
        self.child_response_windows += 1
        self._log.append(("listen_window", None, None))
        return True


class _OrderingConn:
    """Minimal conn that wires the ordering WS + voice provider together. Mirrors
    ``_FakeConn`` but threads the shared interleave log into both surfaces."""

    def __init__(self, log, *, session_id):
        self.logger = L._DummyLogger()
        self.websocket = _OrderingWebSocket(log)
        self.session_id = session_id
        self.device_id = "dev-e2e"
        self.features = {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
        self.config = {}
        self.voice_provider = _OrderingVoiceProvider(log)

    def is_realtime_busy(self):
        return False


# ── COPPA-strip forwarder driven through the REAL post boundary ──────────────────


class _RealBoundaryForwarder:
    """Forwarder stand-in whose ``enqueue`` synchronously drives each batch through
    the REAL ``config.manage_api_client.post_lesson_event`` (with the network leg
    replaced by an in-memory sink). This proves the COPPA strip + result->outcome
    rename happen on the wire the runtime actually feeds, not in a test re-impl."""

    def __init__(self):
        self.batches = []           # raw pre-boundary batches (what the runtime hands us)
        self.posted_bodies = []     # post-boundary JSON bodies that 'left the ESP'
        self.closed = False
        self._loop = asyncio.get_event_loop()

    def enqueue(self, batch):
        self.batches.append(batch)
        # Run the real boundary synchronously so assertions can read post_bodies.
        self._loop.create_task(self._drive(batch))

    async def _drive(self, batch):
        import config.manage_api_client as mac

        captured = {}

        async def _sink(client, method, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            return {"data": {"stored": True}}

        saved = mac._lesson_request_with_retry
        mac._lesson_request_with_retry = _sink
        try:
            await mac.post_lesson_event(
                object(), "http://backend.test/v1", "dev-uuid-42", batch, token="device-jwt"
            )
        finally:
            mac._lesson_request_with_retry = saved
        if "json" in captured:
            self.posted_bodies.append(captured)

    async def aclose(self):
        self.closed = True


class LessonEndToEndFlowTest(unittest.IsolatedAsyncioTestCase):
    def _ack(self, *args, **kwargs):
        return L._ack(*args, **kwargs)

    def _sent(self, conn):
        return [json.loads(p) for p in conn.websocket.sent]

    async def test_renderer_v2_supports_all_runtime_visual_states_with_authored_values(self):
        from core.lesson.runtime import LessonRuntime, RENDERER_V2_VISUAL_STATES, S_RUNNING

        prep = FIX["frames"]["lesson_prepare"]
        conn = L._FakeConn(session_id=prep["sessionId"])
        conn.device_id = "renderer-v2-e2e"
        conn.features = {
            "lesson": True,
            "renderer": ["teebot-lesson-renderer.v2"],
        }
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "motion_presets_enabled": True,
                "rollout_device_allowlist": [conn.device_id],
                "frame_ack_timeout_sec": 60,
            }
        }
        manifest = L._build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        step = manifest["steps"][0]
        step["scene"]["robotOverlay"]["asset"]["key"] = "robotOverlay.authored"
        step["motion"] = {
            "present": "teach",
            "listen": "listen",
            "correct": "celebrate",
            "nearMiss": "encourage",
            "incorrect": "tryAgain",
            "completion": "goodbye",
        }
        with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
            rt = LessonRuntime(
                conn,
                assignment=L._build_assignment(),
                manifest=manifest,
                asset_cache=L._FakeAssetCache(ready=True),
                forwarder=L._FakeForwarder(),
                manifest_checksum=L._manifest_checksum(),
            )
        conn.lesson_runtime = rt
        rt.state = S_RUNNING
        rt._step = step
        rt._step_id = step["id"]
        rt._step_seq = 3
        self.assertNotIn("present", rt._step_body(step)["motion"])

        self.assertEqual(
            RENDERER_V2_VISUAL_STATES,
            {
                "teach", "listen", "thinking", "correct", "nearMiss",
                "incorrect", "retry", "celebrate", "completion",
            },
        )
        sent = []

        async def send_and_ack(state, preset):
            task = asyncio.create_task(
                rt._apply_visual_then_motion(state, "robotOverlay.authored", preset)
            )
            await asyncio.sleep(0)
            frame = self._sent(conn)[-1]
            sent.append(frame["body"])
            await rt.on_lesson_ack(
                {
                    "type": "lesson_ack",
                    "protocolVersion": "teebot-lesson-renderer.v2",
                    "assignmentId": rt.assignment_id,
                    "sessionId": rt.session_id,
                    "stepId": rt._step_id,
                    "sequence": len(sent),
                    "body": {
                        "acks": frame["sequence"],
                        "accepted": True,
                        "degraded": False,
                        "degradedReason": None,
                        "visualGeneration": frame["body"]["visualGeneration"],
                    },
                }
            )
            self.assertTrue(await task)

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", return_value=True):
            for state in RENDERER_V2_VISUAL_STATES:
                await send_and_ack(state, step["motion"].get("completion") if state == "completion" else None)

        self.assertEqual({body["state"] for body in sent}, RENDERER_V2_VISUAL_STATES)
        self.assertTrue(all(body["overlayKey"] == "robotOverlay.authored" for body in sent))
        phase_events = [
            event
            for batch in rt.forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "runtime_phase_changed"
        ]
        expected_phase = {
            "teach": "teaching",
            "listen": "listening",
            "thinking": "thinking",
            "correct": "feedback",
            "nearMiss": "feedback",
            "incorrect": "feedback",
            "retry": "feedback",
            "celebrate": "feedback",
            "completion": "feedback",
        }
        self.assertEqual(
            [event["phase"] for event in phase_events],
            [expected_phase[body["state"]] for body in sent],
        )

    async def test_renderer_v2_does_not_forward_phase_for_rejected_or_stale_visual_ack(self):
        from core.lesson.runtime import LessonRuntime, S_RUNNING

        prep = FIX["frames"]["lesson_prepare"]
        conn = L._FakeConn(session_id=prep["sessionId"])
        conn.device_id = "renderer-v2-e2e"
        conn.features = {"lesson": True, "renderer": ["teebot-lesson-renderer.v2"]}
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": [conn.device_id],
                "frame_ack_timeout_sec": 60,
            }
        }
        manifest = L._build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        forwarder = L._FakeForwarder()
        rt = LessonRuntime(
            conn,
            assignment=L._build_assignment(),
            manifest=manifest,
            asset_cache=L._FakeAssetCache(ready=True),
            forwarder=forwarder,
            manifest_checksum=L._manifest_checksum(),
        )
        conn.lesson_runtime = rt
        rt.state = S_RUNNING
        rt._step = manifest["steps"][0]
        rt._step_id = rt._step["id"]

        task = asyncio.create_task(rt._apply_visual_then_motion("thinking", None, None))
        await asyncio.sleep(0)
        frame = self._sent(conn)[-1]
        await rt.on_lesson_ack(
            {
                "type": "lesson_ack",
                "protocolVersion": "teebot-lesson-renderer.v2",
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "stepId": rt._step_id,
                "sequence": 1,
                "body": {
                    "acks": frame["sequence"],
                    "accepted": False,
                    "degraded": False,
                    "degradedReason": "missingOverlay",
                    "visualGeneration": frame["body"]["visualGeneration"],
                },
            }
        )
        self.assertFalse(await task)
        self.assertEqual(
            [
                event
                for batch in forwarder.batches
                for event in batch.get("events", [])
                if event.get("type") == "runtime_phase_changed"
            ],
            [],
        )

    # ── 1) ordering oracle: every frame is ACKed before its prompt, full story ───

    async def test_each_step_frame_is_acked_by_firmware_before_prompt_across_full_story(self):
        from core.lesson.runtime import LessonRuntime

        prep = FIX["frames"]["lesson_prepare"]
        log = []  # shared interleave transcript: (kind, payload, stepId) in real order
        conn = _OrderingConn(log, session_id=prep["sessionId"])
        manifest = L._build_full_seed_story_manifest()
        forwarder = L._FakeForwarder()
        with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
            rt = LessonRuntime(
                conn,
                assignment=L._build_assignment(),
                manifest=manifest,
                asset_cache=L._FakeAssetCache(ready=True),
                forwarder=forwarder,
                manifest_checksum=L._manifest_checksum(),
            )

        await rt.start()
        await rt.on_lesson_ack(self._ack(1, 1))   # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(self._ack(2, 2))   # start-ack -> first lesson_step

        expected_steps = [s["id"] for s in manifest["steps"]]
        interactive = {s["id"] for s in manifest["steps"] if s["completionClass"] == "interactive"}

        inbound_seq = 3
        for step in manifest["steps"]:
            sid = step["id"]
            frame = [f for f in self._sent(conn) if f["type"] == "lesson_step"][-1]
            self.assertEqual(frame["stepId"], sid)
            # three image layers + pose-specific robotOverlay still on the wire.
            scene = frame["body"]["scene"]
            self.assertEqual(set(scene), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertEqual(
                scene["robotOverlay"]["asset"]["key"],
                step["scene"]["robotOverlay"]["asset"]["key"],
            )
            log.append(("ack", "lesson_step", sid))
            await rt.on_lesson_ack(self._ack(frame["sequence"], inbound_seq, step_id=sid))
            if sid in interactive:
                self.assertTrue(
                    await rt.on_child_response(f"con noi {sid}", source="voice_transcript")
                )
            inbound_seq += 1

        stop = [f for f in self._sent(conn) if f["type"] == "lesson_stop"][-1]
        await rt.on_lesson_ack(self._ack(stop["sequence"], inbound_seq))

        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(rt._steps_completed, 9)
        self.assertEqual(
            [f["stepId"] for f in self._sent(conn) if f["type"] == "lesson_step"],
            expected_steps,
        )
        forwarded_events = [
            event for batch in forwarder.batches for event in batch.get("events", [])
        ]
        completed = [event for event in forwarded_events if event.get("type") == "lesson_completed"]
        self.assertEqual(completed[-1]["summary"]["stepsCompleted"], 9)
        self.assertIn(
            "completed",
            [
                event.get("phase")
                for event in forwarded_events
                if event.get("type") == "runtime_phase_changed"
            ],
        )

        # ORDERING ORACLE: walk the single interleaved transcript. For every step,
        # the ('frame','lesson_step',sid) row MUST precede the rendered ACK, and the
        # ACK MUST precede that step's ('prompt',...) row. Indices are computed
        # independently per kind so a swapped emit order fails as a clean assertion,
        # not a lookup error.
        prompt_rows = [i for i, r in enumerate(log) if r[0] == "prompt"]
        # Every step has one authored prompt; current adaptive coaching also adds a
        # safe success cheer after each interactive response.
        self.assertEqual(len(prompt_rows), len(expected_steps) + len(interactive))
        success_rows = [i for i in prompt_rows if log[i][1] == "Đúng rồi! barn!"]
        self.assertEqual(len(success_rows), len(interactive))
        for sid in expected_steps:
            frame_idxs = [i for i, r in enumerate(log) if r == ("frame", "lesson_step", sid)]
            ack_idxs = [i for i, r in enumerate(log) if r == ("ack", "lesson_step", sid)]
            prompt_text = _prompt_for(manifest, sid)
            prompt_idxs = [i for i in prompt_rows if log[i][1] == prompt_text]
            self.assertEqual(len(frame_idxs), 1, f"{sid} frame not sent exactly once")
            self.assertEqual(len(ack_idxs), 1, f"{sid} render ack not observed exactly once")
            self.assertEqual(len(prompt_idxs), 1, f"{sid} prompt not spoken exactly once")
            self.assertLess(
                frame_idxs[0], ack_idxs[0], f"ack for {sid} preceded its frame"
            )
            self.assertLess(
                ack_idxs[0], prompt_idxs[0], f"prompt for {sid} preceded render ack"
            )
        # listen-window only opens for interactive steps.
        self.assertEqual(
            sum(1 for r in log if r[0] == "listen_window"), len(interactive)
        )

        # MUTATION ORACLE (non-tautology): had the runtime spoken any prompt BEFORE
        # its frame/ack, the first step-related transcript event would be a 'prompt'.
        # The very first frame/ack/prompt event is a frame.
        step_events = [
            r
            for r in log
            if r[0] == "prompt" or (r[0] in {"frame", "ack"} and r[1] == "lesson_step")
        ]
        self.assertEqual(step_events[0][0], "frame")

    # ── 2) COPPA strip proven through the REAL post_lesson_event boundary ────────

    async def test_child_transcript_progress_is_coppa_stripped_at_the_real_post_boundary(self):
        from core.lesson.runtime import LessonRuntime

        conn = L._FakeConn()
        conn.voice_provider = L._RecordingLessonVoiceProvider()
        manifest = L._build_class_steps_manifest(
            [("s4", "repeat", "interactive"), ("s8", "feedback", "passive")]
        )
        forwarder = _RealBoundaryForwarder()
        prep = FIX["frames"]["lesson_prepare"]
        with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
            rt = LessonRuntime(
                conn,
                assignment=L._build_assignment(),
                manifest=manifest,
                asset_cache=L._FakeAssetCache(ready=True),
                forwarder=forwarder,
                manifest_checksum=L._manifest_checksum(),
            )

        await rt.start()
        await rt.on_lesson_ack(self._ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(self._ack(2, 2))
        await rt.on_lesson_ack(self._ack(3, 3, step_id="s4"))

        secret_speech = "con noi barn that la to ro rang"
        self.assertTrue(await rt.on_child_response(secret_speech, source="voice_transcript"))

        await rt.on_lesson_ack(self._ack(4, 4, step_id="s8"))  # passive auto-advance
        await rt.on_lesson_ack(self._ack(5, 5))                # stop-ack
        await asyncio.sleep(0)  # let the boundary tasks drain
        await asyncio.sleep(0)

        self.assertEqual(rt.state, "COMPLETED")

        # PRE-boundary: the runtime DID forward the child's raw transcript verbatim.
        step_completed = [
            ev
            for batch in forwarder.batches
            for ev in batch.get("events", [])
            if ev.get("type") == "step_completed"
        ]
        interactive_completed = next(ev for ev in step_completed if ev.get("stepId") == "s4")
        self.assertEqual(interactive_completed["result"], "success")
        self.assertEqual(interactive_completed["detail"]["recognizedText"], secret_speech)

        # POST-boundary: the body that 'left the ESP' has NO child speech anywhere
        # and renamed result->outcome. Scan every posted event recursively.
        posted_events = [
            ev for cap in forwarder.posted_bodies for ev in cap["json"].get("events", [])
        ]
        step_done_posted = [ev for ev in posted_events if ev.get("type") == "step_completed"]
        self.assertTrue(step_done_posted, "no step_completed reached the post boundary")
        posted = next(ev for ev in step_done_posted if ev.get("stepId") == "s4")
        self.assertEqual(posted.get("outcome"), "success")
        self.assertNotIn("result", posted)
        self.assertNotIn(secret_speech, json.dumps(posted))
        self.assertNotIn("recognizedText", posted.get("detail", {}))
        # the non-speech detail (source) survives the strip.
        self.assertEqual(posted["detail"]["source"], "voice_transcript")
        # the post hit the device-scoped lesson-events route with the device JWT.
        self.assertTrue(forwarder.posted_bodies[0]["url"].endswith("/devices/dev-uuid-42/lesson-events"))
        self.assertEqual(
            forwarder.posted_bodies[0]["headers"]["Authorization"], "Bearer device-jwt"
        )

    # ── 3) device-identity mint threaded through the full pull-on-connect ────────

    async def test_voice_start_uses_minted_device_identity_for_pull_and_forwarder(self):
        from core.voice.session_provider.google_live import GoogleLiveProvider

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": L._manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = L._build_full_seed_story_manifest()

        # Capture the device id + token the runtime threads into the assignment pull.
        seen = {}

        import config.device_token_client as dtc
        import config.manage_api_client as mac

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            seen["mint_device_id"] = device_id
            return MINTED_DEVICE_UUID, MINTED_DEVICE_JWT

        async def _get_child_name(client, base_url, device_id, *, token=None):
            seen["child_name_identity"] = (device_id, token)
            return None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            seen["assignment_device_id"] = device_id
            seen["assignment_token"] = token
            return assignment

        async def _get_manifest(client, base_url, lesson_id, profile, *, token=None,
                                 renderer_capabilities=None, renderer_v2_enabled=False,
                                 lesson_version=None):
            seen["manifest_token"] = token
            return manifest, f'"lesson-3-espTft-{L._manifest_checksum()}"'

        saved = (
            dtc.resolve_device_identity,
            mac.get_device_child_name,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_device_child_name = _get_child_name
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest

        conn = L._RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.func_handler = L._RegistryStartLessonHandler()
        provider = GoogleLiveProvider(conn)
        provider._client = L._LiveClientStub(provider)
        conn.voice_provider = provider

        # keep the asset preload + event POST entirely in-memory
        import core.lesson.asset_cache as asset_cache_mod
        saved_cache = asset_cache_mod.AssetCache
        asset_cache_mod.AssetCache = lambda **_kw: L._FakeAssetCache(ready=True)
        try:
            handled = await provider._on_user_transcript("bắt đầu bài học")
            rt = await conn.lesson_pull_task
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_device_child_name,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
            ) = saved
            asset_cache_mod.AssetCache = saved_cache

        self.assertTrue(handled)
        self.assertIsNotNone(rt)
        # the voice phrase admitted start_lesson exactly once.
        self.assertEqual(conn.func_handler.calls, [{"name": "start_lesson", "arguments": {}}])
        self.assertEqual(seen["mint_device_id"], conn.device_id)
        self.assertEqual(
            seen["child_name_identity"],
            (MINTED_DEVICE_UUID, MINTED_DEVICE_JWT),
        )
        self.assertEqual(seen["assignment_device_id"], MINTED_DEVICE_UUID)
        self.assertEqual(seen["assignment_token"], MINTED_DEVICE_JWT)
        self.assertEqual(seen["manifest_token"], MINTED_DEVICE_JWT)
        self.assertEqual(rt.forwarder.device_id, MINTED_DEVICE_UUID)
        self.assertEqual(rt.forwarder.token, MINTED_DEVICE_JWT)
        # only the prepare frame is on the wire until the firmware acks.
        self.assertEqual(
            [json.loads(p)["type"] for p in conn.websocket.sent], ["lesson_prepare"]
        )

    async def test_course_phrase_voice_start_loads_backend_manifest_and_plays_all_layered_steps(self):
        from core.voice.session_provider.google_live import GoogleLiveProvider

        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": L._manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        manifest = L._build_full_seed_story_manifest()
        seen = {}

        import config.device_token_client as dtc
        import config.manage_api_client as mac
        import core.lesson.asset_cache as asset_cache_mod

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            seen["mint_device_id"] = device_id
            return MINTED_DEVICE_UUID, MINTED_DEVICE_JWT

        async def _get_child_name(client, base_url, device_id, *, token=None):
            seen["child_name_identity"] = (device_id, token)
            return None

        async def _get_assignment(client, base_url, device_id, *, token=None):
            seen["assignment"] = (base_url, device_id, token)
            return assignment

        async def _get_manifest(client, base_url, lesson_id, profile, *, token=None,
                                 renderer_capabilities=None, renderer_v2_enabled=False,
                                 lesson_version=None):
            seen["manifest"] = (lesson_id, profile, token, renderer_capabilities, lesson_version)
            return manifest, f'"lesson-3-espTft-{L._manifest_checksum()}"'

        saved = (
            dtc.resolve_device_identity,
            mac.get_device_child_name,
            mac.get_current_assignment,
            mac.get_lesson_manifest,
            asset_cache_mod.AssetCache,
        )
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_device_child_name = _get_child_name
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        asset_cache_mod.AssetCache = lambda **_kw: L._FakeAssetCache(ready=True)

        conn = L._RepublishConn()
        conn.loop = asyncio.get_running_loop()
        conn.func_handler = L._RegistryStartLessonHandler()
        provider = GoogleLiveProvider(conn)
        provider._client = L._LiveClientStub(provider)
        conn.voice_provider = provider

        try:
            with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=prep["sessionId"]):
                handled = await provider._on_user_transcript("vào khóa học của con")
                rt = await conn.lesson_pull_task
        finally:
            (
                dtc.resolve_device_identity,
                mac.get_device_child_name,
                mac.get_current_assignment,
                mac.get_lesson_manifest,
                asset_cache_mod.AssetCache,
            ) = saved

        self.assertTrue(handled)
        self.assertIs(conn.lesson_runtime, rt)
        self.assertEqual(conn.func_handler.calls, [{"name": "start_lesson", "arguments": {}}])
        self.assertEqual(seen["mint_device_id"], conn.device_id)
        self.assertEqual(
            seen["child_name_identity"],
            (MINTED_DEVICE_UUID, MINTED_DEVICE_JWT),
        )
        self.assertEqual(
            seen["assignment"],
            ("http://backend.test/v1", MINTED_DEVICE_UUID, MINTED_DEVICE_JWT),
        )
        self.assertEqual(
            seen["manifest"][0:3],
            (prep["lessonId"], "espTft", MINTED_DEVICE_JWT),
        )
        self.assertEqual(seen["manifest"][4], prep["lessonVersion"])
        self.assertEqual(rt.forwarder.device_id, MINTED_DEVICE_UUID)
        self.assertEqual(rt.forwarder.token, MINTED_DEVICE_JWT)

        sent = [json.loads(p) for p in conn.websocket.sent]
        self.assertEqual([frame["type"] for frame in sent], ["lesson_prepare"])
        prepare = sent[0]
        self.assertEqual(prepare["lessonId"], prep["lessonId"])
        self.assertEqual(prepare["body"]["manifestRef"]["manifestChecksum"], L._manifest_checksum())
        self.assertEqual(
            [asset["layer"] for asset in prepare["body"]["criticalAssets"]],
            ["backgroundScene", "teachingObject"],
        )

        rt.asset_cache = L._FakeAssetCache(ready=True)
        rt.forwarder = L._FakeForwarder()
        await rt.on_lesson_ack(self._ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(self._ack(2, 2))

        inbound_seq = 3
        for expected_step in manifest["steps"]:
            frame = [json.loads(p) for p in conn.websocket.sent if json.loads(p)["type"] == "lesson_step"][-1]
            sid = expected_step["id"]
            self.assertEqual(frame["stepId"], sid)
            self.assertEqual(frame["body"]["stepType"], expected_step["type"])
            self.assertEqual(frame["body"]["completionClass"], expected_step["completionClass"])
            scene = frame["body"]["scene"]
            self.assertEqual(set(scene), {"backgroundScene", "teachingObject", "robotOverlay"})
            self.assertEqual(scene["backgroundScene"]["poster"]["key"], "backgroundScene.poster")
            self.assertEqual(scene["teachingObject"]["asset"]["key"], "teachingObject.barn")
            self.assertEqual(
                scene["robotOverlay"]["asset"]["key"],
                expected_step["scene"]["robotOverlay"]["asset"]["key"],
            )
            await rt.on_lesson_ack(self._ack(frame["sequence"], inbound_seq, step_id=sid))
            if expected_step["completionClass"] == "interactive":
                self.assertTrue(await rt.on_child_response(f"con noi {sid}", source="voice_transcript"))
            inbound_seq += 1

        stop = [json.loads(p) for p in conn.websocket.sent if json.loads(p)["type"] == "lesson_stop"][-1]
        await rt.on_lesson_ack(self._ack(stop["sequence"], inbound_seq))
        self.assertEqual(rt.state, "COMPLETED")
        self.assertEqual(
            [json.loads(p)["stepId"] for p in conn.websocket.sent if json.loads(p)["type"] == "lesson_step"],
            [step["id"] for step in manifest["steps"]],
        )


def _prompt_for(manifest, step_id):
    return next(s["prompt"] for s in manifest["steps"] if s["id"] == step_id)


if __name__ == "__main__":
    unittest.main()
