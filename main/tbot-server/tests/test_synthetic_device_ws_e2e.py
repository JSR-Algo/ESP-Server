"""REAL synthetic-device-over-WebSocket end-to-end test for the esp32-server.

Unlike ``tests/test_lesson_e2e_flow.py`` (which drives ``LessonRuntime`` /
``GoogleLiveProvider`` objects directly, in-process, with hand-fed frames), this
file impersonates the firmware over a **genuine websockets client connected to a
genuine ``websockets.serve`` acceptor** running the REAL
``WebSocketServer._handle_connection`` -> ``ConnectionHandler.handle_connection``
-> ``_route_message`` dispatch. Every server->device frame travels over a real
TCP/WS socket and every device->server frame (hello, listen/detect, lesson_ack)
is parsed by the real connection handler. No existing test boots a live socket;
this is the first.

THE JOURNEY DRIVEN OVER THE WIRE (fully offline — no Gemini, no backend, no HW):
  1. Real WS client connects with the mandatory ``device-id`` header, sends the
     firmware ``hello`` advertising ``features.lesson`` + the lesson renderer,
     and receives the real hello ACK (server's ``tbot`` block + a session_id).
  2. Client injects the child utterance ``"bắt đầu bài học"`` as a
     ``{type:listen,state:detect,text:...}`` frame. The REAL
     ``GoogleLiveProvider.handle_text_message`` -> ``_dispatch_lesson_start_intent``
     -> pure string classifier admits ``start_lesson`` (NO Gemini), the REAL
     ``start_lesson`` plugin schedules the REAL pull-on-connect, which fetches a
     canned assignment+manifest (backend HTTP stubbed) and emits ``lesson_prepare``
     over the socket.
  3. The synthetic device acks ``lesson_prepare``; the server emits ``lesson_start``;
     the device acks that; the server emits ``lesson_step`` frames. The device acks
     each step, and for INTERACTIVE steps sends the child's voice response as a
     ``listen/detect`` frame (which the runtime's open child-response window
     consumes to advance the step).
  4. After the last step the server emits ``lesson_stop {reason:COMPLETED}``; the
     device acks it; the lesson reaches COMPLETED and finishes back to CONVERSATION.
  5. A ``lesson_completed`` progress event is forwarded through the REAL
     ``config.manage_api_client.post_lesson_event`` COPPA boundary into an in-memory
     sink (the HTTP-execute leg ``_lesson_request_with_retry`` is replaced), proving
     the child-speech strip + ``result``->``outcome`` rename happened on the genuine
     forwarder path bound to the minted device UUID + JWT.

ONLY three unavoidable externals are stubbed, each justified:
  * Gemini Live client (``GoogleLiveProvider._client``) — a real socket dial-out;
    the lesson-admission text path never opens it, so a ``_LiveClientStub`` suffices.
  * Backend HTTP (``resolve_device_identity`` / ``get_current_assignment`` /
    ``get_lesson_manifest`` / ``get_device_child_name`` and the shared
    ``_lesson_request_with_retry`` execute helper) — no Nest backend in CI.
  * Asset-cache disk/network preload (``core.lesson.asset_cache.AssetCache``) — no
    /sdcard or asset origin in CI.
Everything else is real: the socket, the WS upgrade/handshake, the connection
handler, the message router, the voice-provider seam, the start_lesson plugin, the
LessonRuntime state machine, and the LessonEventForwarder COPPA boundary.

Run with ``.venv311/bin/python -m pytest tests/test_synthetic_device_ws_e2e.py``.
Async tests use ``unittest.IsolatedAsyncioTestCase`` (no pytest-asyncio markers),
matching the lesson e2e harness.
"""

import asyncio
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import websockets  # noqa: E402

import core.connection as conn_mod  # noqa: E402
import core.websocket_server as ws_mod  # noqa: E402
import test_lesson_runtime as L  # noqa: E402  (sibling test harness: builders + fakes)
from core.voice.session_orchestrator import (  # noqa: E402
    SessionMode,
    normalize_session_mode,
)

_ORIGINAL_ASYNCIO_SLEEP = asyncio.sleep
_ORIGINAL_ASYNCIO_CREATE_TASK = asyncio.create_task
_ORIGINAL_ASYNCIO_RUN = asyncio.run
_ORIGINAL_ASYNCIO_RUN_COROUTINE_THREADSAFE = asyncio.run_coroutine_threadsafe

FIX = L.FIX

DEVICE_ID = "AA:BB:CC:DD:EE:01"
CLIENT_ID = "synthetic-firmware-client"
API_BASE = "http://backend.test/v1"

# The minted backend identity the (stubbed) device-token mint returns; the real
# pull-on-connect must thread THESE into the assignment pull AND the forwarder.
MINTED_UUID = "dev-uuid-42"
MINTED_JWT = "minted-jwt-xyz"

# The server's hello-ack template (mirrors config.yaml:123 `tbot:` block).
TBOT_TEMPLATE = {
    "type": "hello",
    "version": 1,
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 24000,
        "channels": 1,
        "frame_duration": 60,
    },
}


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        return None

    def debug(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def error(self, *a, **k):
        return None


class _RealPluginStartLessonHandler(L._RegistryStartLessonHandler):
    """The lightweight real-plugin handler from the sibling harness, plus the
    ``cleanup`` coroutine the real ConnectionHandler.close() calls on teardown (the
    base stub lacks it, which only logs a benign error)."""

    async def cleanup(self):
        return None


def _build_assignment():
    """Canned ASSIGNED assignment whose checksum matches the seed manifest. We pin
    ``sessionId`` so the runtime's inbound-identity guard uses a value we know up
    front; the synthetic device echoes it (and assignmentId) read from each frame."""
    prep = FIX["frames"]["lesson_prepare"]
    return {
        "assignmentId": prep["assignmentId"],
        "assignmentVersion": prep["body"]["assignmentVersion"],
        "lessonId": prep["lessonId"],
        "lessonVersion": prep["lessonVersion"],
        "manifestChecksum": L._manifest_checksum(),
        "profile": "espTft",
        "state": "ASSIGNED",
    }


class SyntheticDeviceWebSocketEndToEndTest(unittest.IsolatedAsyncioTestCase):
    """Drives the full lesson journey over a real localhost websocket."""

    def setUp(self):
        # Several lower-level connection tests patch asyncio module attributes
        # directly. Because modules are shared process-wide, reset the event-loop
        # primitives this real websocket E2E depends on before opening sockets.
        asyncio.sleep = _ORIGINAL_ASYNCIO_SLEEP
        asyncio.create_task = _ORIGINAL_ASYNCIO_CREATE_TASK
        asyncio.run = _ORIGINAL_ASYNCIO_RUN
        asyncio.run_coroutine_threadsafe = _ORIGINAL_ASYNCIO_RUN_COROUTINE_THREADSAFE

        # ── module-level bootstrap stubs (must precede WebSocketServer construction).
        self._saved_initialize_modules = ws_mod.initialize_modules
        self._saved_setup_logging = ws_mod.setup_logging
        ws_mod.initialize_modules = lambda *a, **k: {}
        ws_mod.setup_logging = lambda: _DummyLogger()

        # ── Gemini seam: real GoogleLiveProvider, stubbed Live client (no dial-out),
        # and the real-plugin func_handler attached at provider-construction time so
        # the spoken start_lesson admits through the genuine dispatch without the
        # heavy UnifiedToolHandler bootstrap.
        from core.voice.session_provider.google_live import GoogleLiveProvider

        def _fake_voice_factory(conn):
            # Capture the REAL server-side ConnectionHandler so a test can read its
            # session_mode + lesson_runtime (white-box corroboration of wire behavior:
            # the finish->CONVERSATION flip and the interactive match/retry state).
            self._server_conn = conn
            conn.func_handler = _RealPluginStartLessonHandler()
            provider = GoogleLiveProvider(conn)
            provider._client = L._LiveClientStub(provider)
            return provider

        self._server_conn = None

        self._saved_create_provider = conn_mod.create_voice_session_provider
        conn_mod.create_voice_session_provider = _fake_voice_factory
        self._saved_generate_chat_title = conn_mod.generate_and_save_chat_title

        async def _skip_chat_title(_session_id):
            return None

        conn_mod.generate_and_save_chat_title = _skip_chat_title

        # ── backend HTTP seam: mint + assignment + manifest + child-name + the shared
        # HTTP-execute helper. Patched as MODULE ATTRIBUTES (the runtime + forwarder
        # resolve them lazily, so from-imports would not see the patch).
        import config.device_token_client as dtc
        import config.manage_api_client as mac
        import core.lesson.asset_cache as asset_cache_mod

        self._dtc = dtc
        self._mac = mac
        self._asset_cache_mod = asset_cache_mod

        assignment = _build_assignment()
        manifest = L._build_full_seed_story_manifest()
        self.manifest = manifest
        etag = f'"lesson-3-espTft-{L._manifest_checksum()}"'

        self.mint_calls = []
        self.posted = []  # COPPA-clean bodies that "left the ESP" via the real boundary

        async def _mint(client, base_url, mac_addr, *, logger=None):
            self.mint_calls.append(mac_addr)
            return MINTED_UUID, MINTED_JWT

        async def _get_assignment(client, base_url, device_id, *, token=None):
            self._assignment_pull = (base_url, device_id, token)
            return assignment

        async def _get_manifest(client, base_url, lesson_id, profile, *, token=None,
                                renderer_capabilities=None, lesson_version=None):
            self._manifest_pull = (lesson_id, profile, token, lesson_version)
            return manifest, etag

        async def _get_child_name(client, base_url, device_id, *, token=None):
            return None

        async def _sink_http(client, method, url, **kwargs):
            # The REAL post_lesson_event has already COPPA-normalized kwargs['json']
            # by the time it reaches this execute helper. Capture the cleaned body.
            self.posted.append(
                {"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")}
            )
            return {"data": {"stored": True}}

        self._saved = {
            "mint": dtc.resolve_device_identity,
            "assignment": mac.get_current_assignment,
            "manifest": mac.get_lesson_manifest,
            "child_name": getattr(mac, "get_device_child_name", None),
            "http": mac._lesson_request_with_retry,
            "asset_cache": asset_cache_mod.AssetCache,
        }
        dtc.resolve_device_identity = _mint
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        mac.get_device_child_name = _get_child_name
        mac._lesson_request_with_retry = _sink_http
        asset_cache_mod.AssetCache = lambda **_kw: L._FakeAssetCache(ready=True)

    def tearDown(self):
        ws_mod.initialize_modules = self._saved_initialize_modules
        ws_mod.setup_logging = self._saved_setup_logging
        conn_mod.create_voice_session_provider = self._saved_create_provider
        conn_mod.generate_and_save_chat_title = self._saved_generate_chat_title
        self._dtc.resolve_device_identity = self._saved["mint"]
        self._mac.get_current_assignment = self._saved["assignment"]
        self._mac.get_lesson_manifest = self._saved["manifest"]
        if self._saved["child_name"] is not None:
            self._mac.get_device_child_name = self._saved["child_name"]
        self._mac._lesson_request_with_retry = self._saved["http"]
        self._asset_cache_mod.AssetCache = self._saved["asset_cache"]

    def _build_config(self):
        return {
            "read_config_from_api": False,
            "selected_module": {},
            "exit_commands": [],
            "close_connection_no_voice_time": 120,
            "server": {
                "ip": "127.0.0.1",
                "port": 0,
                "auth_key": "test-secret",
                "auth": {"enabled": False},
            },
            "voice_mode": {"type": "google_live"},
            "lesson": {"runtime_enabled": True, "api_base": API_BASE},
            # Collapse the lesson voice-prompt guard/window timing so the real
            # provider opens the child-response window PROMPTLY instead of waiting
            # the production 20s output-idle guard per interactive step (the Live
            # client stub never signals output-done). These are genuine config
            # knobs the real provider reads — not a behavioral stub.
            "google_live": {
                "lesson_prompt_output_guard_timeout_sec": 0.1,
                "lesson_prompt_playback_guard_timeout_sec": 0.1,
                "lesson_prompt_playback_tail_sec": 0.0,
                "lesson_child_response_open_delay_sec": 0.0,
                "lesson_child_response_max_open_delay_sec": 0.0,
                "lesson_prompt_tts_chars_per_sec": 0.0,
            },
            "tbot": copy.deepcopy(TBOT_TEMPLATE),
        }

    @staticmethod
    def _device_ack(frame, env_seq):
        """Build a firmware lesson_ack that correlates ``body.acks`` to the frame's
        S->F ``sequence`` and echoes the frame's OWN assignmentId/sessionId (the
        runtime drops frames whose identity doesn't match), with the device's
        independent strictly-monotonic F->S ``sequence`` counter."""
        return {
            "type": "lesson_ack",
            "protocolVersion": "teebot-lesson-renderer.v1",
            "assignmentId": frame["assignmentId"],
            "sessionId": frame["sessionId"],
            "lessonId": frame.get("lessonId"),
            "lessonVersion": frame.get("lessonVersion"),
            "stepId": frame.get("stepId"),
            "sequence": env_seq,
            "timestamp": 1,
            "body": {"acks": frame["sequence"], "rendered": True, "degraded": False},
        }

    async def _recv_json_until(self, client, predicate, *, timeout=5.0, collect=None):
        """Recv frames from the real socket (skipping non-JSON control strings and
        unrelated voice frames) until ``predicate(frame)`` is true; return that frame.
        Optionally append every lesson_* frame seen to ``collect``."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise AssertionError("timed out waiting for an expected server frame")
            raw = await asyncio.wait_for(client.recv(), timeout=remaining)
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue  # bare control strings ("Port normal...", etc.)
            if collect is not None and isinstance(frame, dict) and str(
                frame.get("type", "")
            ).startswith("lesson_"):
                collect.append(frame)
            if predicate(frame):
                return frame

    async def _assert_no_frame(self, client, predicate, *, window=0.8):
        """Assert that NO frame matching ``predicate`` arrives within ``window`` sec,
        draining any unrelated frames. Used as the negative oracle for a rejected
        child answer: a wrong answer must NOT advance the lesson, so no further
        lesson_step / lesson_stop may appear before the correct answer is sent."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + window
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                raw = await asyncio.wait_for(client.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if predicate(frame):
                raise AssertionError(
                    f"a forbidden frame arrived within {window}s: {frame.get('type')}"
                )

    async def test_full_lesson_journey_over_real_websocket_with_coppa_forwarded_progress(self):
        config = self._build_config()
        server = ws_mod.WebSocketServer(config)

        manifest = self.manifest
        expected_steps = [s["id"] for s in manifest["steps"]]
        interactive = {
            s["id"] for s in manifest["steps"] if s["completionClass"] == "interactive"
        }

        lesson_frames = []  # ordered server->device lesson_* frames seen on the wire

        async with websockets.serve(
            server._handle_connection,
            "127.0.0.1",
            0,
            process_request=server._http_response,
        ) as srv:
            port = srv.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}/tbot/v1/"

            async with websockets.connect(
                uri,
                additional_headers={"device-id": DEVICE_ID, "client-id": CLIENT_ID},
            ) as client:
                # ── 1) hello handshake over the real socket ──────────────────────
                hello = {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 24000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                    "features": {
                        "lesson": True,
                        "renderer": "teebot-lesson-renderer.v1",
                        "mcp": False,
                    },
                }
                await client.send(json.dumps(hello))
                ack = await self._recv_json_until(
                    client, lambda f: f.get("type") == "hello"
                )
                self.assertEqual(ack["type"], "hello")
                self.assertTrue(ack.get("session_id"), "hello ack must carry a session_id")

                # ── 2) child utterance admits start_lesson (no Gemini) -> prepare ─
                await client.send(
                    json.dumps(
                        {"type": "listen", "state": "detect", "text": "bắt đầu bài học"}
                    )
                )
                prepare = await self._recv_json_until(
                    client,
                    lambda f: f.get("type") == "lesson_prepare",
                    collect=lesson_frames,
                    timeout=8.0,
                )
                self.assertEqual(prepare["sequence"], 1)
                self.assertEqual(prepare["lessonId"], FIX["frames"]["lesson_prepare"]["lessonId"])
                self.assertEqual(
                    prepare["body"]["manifestRef"]["manifestChecksum"],
                    L._manifest_checksum(),
                )

                # the robot MAC was minted; assignment + manifest pulls bound to the
                # MINTED uuid/jwt, not the raw device_id.
                self.assertEqual(self.mint_calls, [DEVICE_ID])
                self.assertEqual(self._assignment_pull, (API_BASE, MINTED_UUID, MINTED_JWT))
                self.assertEqual(self._manifest_pull[2], MINTED_JWT)

                # ── 3) prepare-ack -> lesson_start ───────────────────────────────
                env_seq = 1
                await client.send(json.dumps(self._device_ack(prepare, env_seq)))
                start = await self._recv_json_until(
                    client,
                    lambda f: f.get("type") == "lesson_start",
                    collect=lesson_frames,
                )
                self.assertEqual(start["sequence"], 2)

                # start-ack -> first lesson_step
                env_seq += 1
                await client.send(json.dumps(self._device_ack(start, env_seq)))
                step = await self._recv_json_until(
                    client,
                    lambda f: f.get("type") == "lesson_step",
                    collect=lesson_frames,
                )

                # ── 4) walk every step in manifest order ─────────────────────────
                acked_step_ids = []
                for expected in manifest["steps"]:
                    sid = expected["id"]
                    self.assertEqual(
                        step["stepId"], sid, f"steps must arrive in manifest order; got {step['stepId']}"
                    )
                    # the three-layer scene survives onto the wire.
                    scene = step["body"]["scene"]
                    self.assertEqual(
                        set(scene), {"backgroundScene", "teachingObject", "robotOverlay"}
                    )
                    self.assertEqual(
                        scene["robotOverlay"]["asset"]["key"],
                        expected["scene"]["robotOverlay"]["asset"]["key"],
                    )
                    self.assertEqual(step["body"]["completionClass"], expected["completionClass"])

                    # ack the step as rendered.
                    env_seq += 1
                    await client.send(json.dumps(self._device_ack(step, env_seq)))
                    acked_step_ids.append(sid)

                    if sid in interactive:
                        # interactive step is NOT completed by its ack: it needs the
                        # child's voice response, injected as a real listen/detect frame
                        # that the runtime's open child-response window consumes.
                        await client.send(
                            json.dumps(
                                {
                                    "type": "listen",
                                    "state": "detect",
                                    "text": f"con noi {sid} barn",
                                }
                            )
                        )

                    # the next frame is either the next step or the terminal stop.
                    nxt = await self._recv_json_until(
                        client,
                        lambda f: f.get("type") in ("lesson_step", "lesson_stop"),
                        collect=lesson_frames,
                        timeout=8.0,
                    )
                    if nxt["type"] == "lesson_step":
                        step = nxt
                    else:
                        stop = nxt
                        break

                # ── lesson_stop -> COMPLETED -> CONVERSATION return ──────────────
                self.assertEqual(stop["type"], "lesson_stop")
                self.assertEqual(stop["body"]["reason"], "COMPLETED")
                env_seq += 1
                await client.send(json.dumps(self._device_ack(stop, env_seq)))

                # ── finish -> CONVERSATION: a SUCCESSFUL completion returns the robot
                # to a HAPPY face + normal conversation (conn.finish_lesson_mode), so a
                # child can keep talking instead of the robot going dark. Prove BOTH the
                # wire signal (the happy llm-emotion frame) AND the server-side mode flip.
                happy = await self._recv_json_until(
                    client,
                    lambda f: f.get("type") == "llm" and f.get("emotion") == "happy",
                    timeout=5.0,
                )
                self.assertEqual(happy.get("emotion"), "happy")
                self.assertEqual(
                    normalize_session_mode(self._server_conn.session_mode),
                    SessionMode.CONVERSATION,
                    "a completed lesson must return the session to CONVERSATION, not stay "
                    "in LESSON or fall to DORMANT (robot would go dark on the child)",
                )

                # ── 5) the lesson_completed progress event reached the REAL post
                # boundary, COPPA-stripped + result->outcome, bound to minted id/jwt.
                # Poll the in-memory sink (the forwarder POSTs via create_task).
                for _ in range(50):
                    if any(
                        ev.get("type") == "lesson_completed"
                        for cap in self.posted
                        for ev in cap["json"].get("events", [])
                    ):
                        break
                    await asyncio.sleep(0.05)

        # ── post-socket assertions ──────────────────────────────────────────────
        # every manifest step arrived exactly once, in order, over the wire.
        sent_step_ids = [f["stepId"] for f in lesson_frames if f["type"] == "lesson_step"]
        self.assertEqual(sent_step_ids, expected_steps)
        self.assertEqual(acked_step_ids, expected_steps)

        # the ordered frame thread on the wire: prepare, start, then 9 steps, then stop.
        frame_types = [f["type"] for f in lesson_frames]
        self.assertEqual(frame_types[0], "lesson_prepare")
        self.assertEqual(frame_types[1], "lesson_start")
        self.assertEqual(frame_types[-1], "lesson_stop")
        self.assertEqual(frame_types.count("lesson_step"), len(expected_steps))
        # prepare precedes start precedes the first step precedes stop (ordering oracle).
        self.assertLess(frame_types.index("lesson_prepare"), frame_types.index("lesson_start"))
        self.assertLess(frame_types.index("lesson_start"), frame_types.index("lesson_step"))
        self.assertLess(
            len(frame_types) - 1 - frame_types[::-1].index("lesson_step"),
            frame_types.index("lesson_stop"),
        )

        # COPPA boundary proof: a lesson_completed event left the ESP, with NO child
        # speech anywhere, result renamed to outcome, on the device-scoped route with
        # the minted JWT.
        posted_events = [
            ev for cap in self.posted for ev in cap["json"].get("events", [])
        ]
        completed = [ev for ev in posted_events if ev.get("type") == "lesson_completed"]
        self.assertTrue(completed, "lesson_completed must reach the real post boundary")
        step_completed = [ev for ev in posted_events if ev.get("type") == "step_completed"]
        self.assertTrue(step_completed, "interactive step_completed must reach the boundary")
        for ev in step_completed:
            # result was renamed to outcome; no child transcript survived the strip.
            self.assertNotIn("result", ev)
            self.assertEqual(ev.get("outcome"), "success")
            self.assertNotIn("recognizedText", ev.get("detail", {}))
        # no child speech token anywhere in any posted body.
        for cap in self.posted:
            self.assertNotIn("con noi", json.dumps(cap["json"]))
        # the post hit the minted-device route with the minted JWT.
        self.assertTrue(
            self.posted[0]["url"].endswith(f"/devices/{MINTED_UUID}/lesson-events"),
            self.posted[0]["url"],
        )
        self.assertEqual(
            self.posted[0]["headers"]["Authorization"], f"Bearer {MINTED_JWT}"
        )

        # cleanup invariant: the server released the connection bookkeeping.
        self.assertEqual(server._active_device_connections, 0)
        self.assertEqual(server.lesson_connections, {})

    async def test_wrong_interactive_answer_is_rejected_then_correct_answer_advances(self):
        """The interactive MATCH logic must DISCRIMINATE, over the real socket: a wrong
        spoken answer is REJECTED (the lesson does not advance and nothing is forwarded),
        and only a CORRECT answer completes the step. The seed steps ship with no
        expectedResponse (so any non-blank text passes) — here we give the first
        interactive step a real expected word, then prove wrong-then-right. Without this,
        a child mumbling gibberish would 'pass' every speaking step and learn nothing."""
        # Arm the first interactive step with a real expected answer.
        target_idx = next(
            i for i, s in enumerate(self.manifest["steps"])
            if s["completionClass"] == "interactive"
        )
        target_id = self.manifest["steps"][target_idx]["id"]
        self.manifest["steps"][target_idx]["expectedResponses"] = ["barn"]
        wrong_text = "con noi con meo"   # no 'barn' token -> must be rejected
        right_text = "con noi tu barn"   # contains 'barn' -> must be accepted

        config = self._build_config()
        server = ws_mod.WebSocketServer(config)
        manifest = self.manifest
        interactive = {
            s["id"] for s in manifest["steps"] if s["completionClass"] == "interactive"
        }
        lesson_frames = []

        async with websockets.serve(
            server._handle_connection, "127.0.0.1", 0,
            process_request=server._http_response,
        ) as srv:
            port = srv.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}/tbot/v1/"
            async with websockets.connect(
                uri,
                additional_headers={"device-id": DEVICE_ID, "client-id": CLIENT_ID},
            ) as client:
                # hello -> ack
                await client.send(json.dumps({
                    "type": "hello", "version": 1, "transport": "websocket",
                    "audio_params": {
                        "format": "opus", "sample_rate": 24000,
                        "channels": 1, "frame_duration": 60,
                    },
                    "features": {
                        "lesson": True, "renderer": "teebot-lesson-renderer.v1",
                        "mcp": False,
                    },
                }))
                await self._recv_json_until(client, lambda f: f.get("type") == "hello")

                # admit start_lesson -> prepare/start
                await client.send(json.dumps(
                    {"type": "listen", "state": "detect", "text": "bắt đầu bài học"}
                ))
                prepare = await self._recv_json_until(
                    client, lambda f: f.get("type") == "lesson_prepare",
                    collect=lesson_frames, timeout=8.0,
                )
                env_seq = 1
                await client.send(json.dumps(self._device_ack(prepare, env_seq)))
                start = await self._recv_json_until(
                    client, lambda f: f.get("type") == "lesson_start", collect=lesson_frames,
                )
                env_seq += 1
                await client.send(json.dumps(self._device_ack(start, env_seq)))
                step = await self._recv_json_until(
                    client, lambda f: f.get("type") == "lesson_step", collect=lesson_frames,
                )

                stop = None
                for expected in manifest["steps"]:
                    sid = expected["id"]
                    self.assertEqual(step["stepId"], sid)
                    env_seq += 1
                    await client.send(json.dumps(self._device_ack(step, env_seq)))

                    if sid in interactive:
                        if sid == target_id:
                            # ── WRONG answer: must be rejected, must NOT advance ─────
                            await client.send(json.dumps(
                                {"type": "listen", "state": "detect", "text": wrong_text}
                            ))
                            # negative wire oracle: no next step / no stop appears.
                            await self._assert_no_frame(
                                client,
                                lambda f: f.get("type") in ("lesson_step", "lesson_stop"),
                                window=0.8,
                            )
                            # white-box corroboration: still on this step, not completed.
                            rt = self._server_conn.lesson_runtime
                            self.assertEqual(
                                rt._step_id, target_id,
                                "a wrong answer must leave the runtime on the same step",
                            )
                            self.assertFalse(
                                rt._step_completed,
                                "a wrong answer must not complete the interactive step",
                            )
                            self.assertFalse(
                                any(
                                    ev.get("type") == "step_completed"
                                    and ev.get("stepId") == target_id
                                    for cap in self.posted
                                    for ev in cap["json"].get("events", [])
                                ),
                                "a rejected answer must not forward a step_completed",
                            )
                            # ── CORRECT answer: now it advances ─────────────────────
                            await client.send(json.dumps(
                                {"type": "listen", "state": "detect", "text": right_text}
                            ))
                        else:
                            await client.send(json.dumps(
                                {"type": "listen", "state": "detect",
                                 "text": f"con noi {sid} barn"}
                            ))

                    nxt = await self._recv_json_until(
                        client, lambda f: f.get("type") in ("lesson_step", "lesson_stop"),
                        collect=lesson_frames, timeout=8.0,
                    )
                    if nxt["type"] == "lesson_step":
                        step = nxt
                    else:
                        stop = nxt
                        break

                self.assertIsNotNone(stop, "the lesson must terminate")
                self.assertEqual(stop["body"]["reason"], "COMPLETED")
                env_seq += 1
                await client.send(json.dumps(self._device_ack(stop, env_seq)))
                for _ in range(50):
                    if any(
                        ev.get("type") == "lesson_completed"
                        for cap in self.posted
                        for ev in cap["json"].get("events", [])
                    ):
                        break
                    await asyncio.sleep(0.05)

        # the interactive step completed EXACTLY ONCE — the wrong attempt forwarded
        # nothing, the correct attempt forwarded one step_completed.
        target_completions = [
            ev for cap in self.posted for ev in cap["json"].get("events", [])
            if ev.get("type") == "step_completed" and ev.get("stepId") == target_id
        ]
        self.assertEqual(
            len(target_completions), 1,
            "the interactive step must complete once (only the correct answer counts)",
        )
        # neither child utterance (wrong or right) survived to the backend boundary.
        for cap in self.posted:
            body = json.dumps(cap["json"])
            self.assertNotIn(wrong_text, body)
            self.assertNotIn(right_text, body)


if __name__ == "__main__":
    unittest.main()
