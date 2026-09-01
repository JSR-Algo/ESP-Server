import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.handle import helloHandle
from core.handle.helloHandle import handleHelloMessage
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.google_live.evidence_enrollment import (
    CANDIDATE_LIFECYCLE_PROFILE,
    EvidenceEnrollmentRegistry,
    TranscriptExpectation,
)


class _Logger:
    def __init__(self):
        self.debugs = []
        self.infos = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message, *_args, **_kwargs):
        self.debugs.append(message)

    def info(self, message, *_args, **_kwargs):
        self.infos.append(message.format(*_args))


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Conn:
    def __init__(self):
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.audio_format = "opus"
        self.input_sample_rate = None
        self.sample_rate = 24000
        self.features = None
        self.mcp_client = None
        self.mcp_scheduled = []
        self.mcp_sent_counts_at_schedule = []
        self.google_live_evidence_journey_id = None
        self.google_live_evidence_scope = None
        self.google_live_live_connection_id = None
        self.session_id = "server-connection-1"
        self.device_id = "device-secret-1"
        self.client_id = "client-secret-1"
        self.headers = {"client-id": self.client_id}
        self.evidence_registry = None
        self.voice_provider = None
        self.config = {
            "voice_mode": {"type": "classic_pipeline"},
            "google_live": {"output_sample_rate": 24000},
        }
        self.welcome_msg = {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "session_id": "session-1",
            "audio_params": {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        }

    def schedule_mcp_background_task(self, coro):
        self.mcp_scheduled.append(coro)
        self.mcp_sent_counts_at_schedule.append(len(self.websocket.sent))
        coro.close()
        return SimpleNamespace(done=lambda: True)


class HelloAudioParamsTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _enroll(conn, journey_id="physical.run-1"):
        registry = EvidenceEnrollmentRegistry()
        registry.register(
            device_id=conn.device_id,
            client_id=conn.client_id,
            journey_id=journey_id,
            transcript_plan=(TranscriptExpectation(1, "post_lesson", "a" * 64),),
            hmac_key=b"k" * 32,
            ttl_sec=120,
        )
        registry.bind_candidate_identity(
            device_id=conn.device_id,
            journey_id=journey_id,
            candidate_identity={
                "gitSha": "a" * 40,
                "imageDigest": "sha256:" + "b" * 64,
                "firmwareIdentity": "firmware-v1",
                "fixtureSha256": "c" * 64,
                "configFingerprint": "sha256:" + "d" * 64,
            },
        )
        conn.evidence_registry = registry
        return registry

    @staticmethod
    def _enroll_candidate(conn, journey_id="candidate.run-1"):
        registry = EvidenceEnrollmentRegistry()
        registry.register(
            device_id=conn.device_id,
            client_id=conn.client_id,
            journey_id=journey_id,
            journey_type="conversation",
            proof_profile=CANDIDATE_LIFECYCLE_PROFILE,
            transcript_plan=(),
            hmac_key=bytearray(),
            ttl_sec=120,
        )
        registry.bind_candidate_identity(
            device_id=conn.device_id,
            journey_id=journey_id,
            candidate_identity={
                "gitSha": "a" * 40,
                "imageDigest": "sha256:" + "b" * 64,
                "firmwareIdentity": "firmware-v1",
                "fixtureSha256": "c" * 64,
                "configFingerprint": "sha256:" + "d" * 64,
            },
        )
        conn.evidence_registry = registry
        return registry

    async def test_google_live_candidate_scope_and_marker_use_registry_claims(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        self._enroll_candidate(conn)

        await handleHelloMessage(
            conn,
            {
                "evidence_journey_id": "candidate.run-1",
                "journeyType": "bargein",
                "proofProfile": "physical-transcript",
            },
        )

        scope = json.loads(conn.websocket.sent[0])["evidenceScope"]
        self.assertEqual(scope["journeyType"], "conversation")
        self.assertEqual(scope["proofProfile"], CANDIDATE_LIFECYCLE_PROFILE)
        marker = next(
            message
            for message in conn.logger.infos
            if "reliability_window_start" in message
        )
        self.assertIn("journeys=conversation ", marker)
        self.assertIn("proof_profile=candidate-lifecycle ", marker)
        self.assertEqual(conn.google_live_evidence_journey_type, "conversation")
        self.assertEqual(
            conn.google_live_evidence_proof_profile,
            CANDIDATE_LIFECYCLE_PROFILE,
        )

    async def test_google_live_invalid_registry_claims_emit_no_production_marker(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        conn.evidence_registry = SimpleNamespace(
            claim_for_scope=lambda **_claims: {"gitSha": "a" * 40},
            safe_snapshot=lambda _journey: {
                "journeyType": "conversation",
                "proofProfile": "physical-transcript",
            },
            abort_claim=lambda **_claims: None,
        )

        await handleHelloMessage(conn, {"evidence_journey_id": "candidate.run-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(
            ack["evidenceScope"]["failureCode"], "EVIDENCE_ENROLLMENT_INVALID"
        )
        conn.voice_provider.prepare_evidence_scope.assert_not_awaited()
        self.assertFalse(
            any("reliability_window_start" in message for message in conn.logger.infos)
        )

    async def test_google_live_hello_claims_injected_enrollment_before_scope(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        registry = self._enroll(conn)

        await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(ack["evidenceScope"]["journeyId"], "physical.run-1")
        self.assertTrue(registry.safe_snapshot("physical.run-1")["connected"])
        self.assertTrue(
            any("reliability_window_start" in message for message in conn.logger.infos)
        )

    async def test_google_live_hello_fails_closed_for_invalid_or_reused_enrollment(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        registry = self._enroll(conn)
        registry.claim_once(
            device_id=conn.device_id,
            client_id=conn.client_id,
            journey_id="physical.run-1",
        )

        await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(ack["evidenceScope"], {
            "status": "FAIL",
            "failureCode": "EVIDENCE_ENROLLMENT_INVALID",
        })
        self.assertIsNone(conn.google_live_evidence_journey_id)
        self.assertIsNone(conn.google_live_evidence_scope)
        conn.voice_provider.prepare_evidence_scope.assert_not_awaited()
        self.assertFalse(
            any("reliability_window_start" in message for message in conn.logger.infos)
        )

    async def test_google_live_hello_ignores_fabricated_client_candidate_identity(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        self._enroll(conn)
        fabricated = {
            "gitSha": "f" * 40,
            "imageDigest": "sha256:" + "f" * 64,
            "firmwareIdentity": "fabricated",
            "fixtureSha256": "f" * 64,
            "configFingerprint": "sha256:" + "f" * 64,
        }

        await handleHelloMessage(
            conn,
            {
                "evidence_journey_id": "physical.run-1",
                "candidate_identity": fabricated,
            },
        )

        logs = " ".join(conn.logger.infos)
        self.assertNotIn(json.dumps(fabricated, sort_keys=True), logs)

    async def test_google_live_hello_without_bound_identity_fails_before_scope(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        registry = EvidenceEnrollmentRegistry()
        registry.register(
            device_id=conn.device_id,
            client_id=conn.client_id,
            journey_id="physical.run-1",
            transcript_plan=(TranscriptExpectation(1, "post_lesson", "a" * 64),),
            hmac_key=b"k" * 32,
            ttl_sec=120,
        )
        conn.evidence_registry = registry

        await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(
            ack["evidenceScope"]["failureCode"], "EVIDENCE_ENROLLMENT_INVALID"
        )
        conn.voice_provider.prepare_evidence_scope.assert_not_awaited()

    async def test_google_live_start_marker_is_after_ack_and_send_failure_aborts_claim(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        registry = self._enroll(conn)

        async def fail_send(_payload):
            raise RuntimeError("send failed")

        conn.websocket.send = fail_send
        with self.assertRaises(RuntimeError):
            await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        self.assertFalse(
            any("reliability_window_start" in message for message in conn.logger.infos)
        )
        self.assertEqual(registry.safe_snapshot("physical.run-1")["status"], "FAIL")

    async def test_google_live_start_marker_is_logged_after_successful_ack(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        self._enroll(conn)
        events = []
        original_info = conn.logger.info

        async def record_send(payload):
            events.append("ack")
            conn.websocket.sent.append(payload)

        def record_info(message, *args, **kwargs):
            original_info(message, *args, **kwargs)
            if "reliability_window_start" in message:
                events.append("start")

        conn.websocket.send = record_send
        conn.logger.info = record_info

        await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        self.assertEqual(events, ["ack", "start"])

    async def test_google_live_prepare_failure_aborts_claim_and_allows_new_enrollment(self):
        for failure in (RuntimeError("prepare failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                conn = _Conn()
                conn.config["voice_mode"] = {"type": "google_live"}
                conn.voice_provider = SimpleNamespace(
                    prepare_evidence_scope=AsyncMock(side_effect=failure)
                )
                registry = self._enroll(conn)

                with self.assertRaises(type(failure)):
                    await handleHelloMessage(
                        conn, {"evidence_journey_id": "physical.run-1"}
                    )

                self.assertEqual(
                    registry.safe_snapshot("physical.run-1")["status"], "FAIL"
                )
                self.assertIsNone(conn.google_live_evidence_journey_id)
                self.assertIsNone(conn.google_live_evidence_scope)
                self.assertFalse(
                    any(
                        "reliability_window_start" in message
                        for message in conn.logger.infos
                    )
                )
                replacement = registry.register(
                    device_id=conn.device_id,
                    client_id=conn.client_id,
                    journey_id="physical.run-2",
                    transcript_plan=(
                        TranscriptExpectation(1, "post_lesson", "a" * 64),
                    ),
                    hmac_key=b"r" * 32,
                    ttl_sec=120,
                )
                self.assertEqual(replacement.journey_id, "physical.run-2")

    async def test_google_live_scope_construction_failure_aborts_claim(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )
        registry = self._enroll(conn)

        with patch.object(
            helloHandle,
            "_evidence_peer_identity_hash",
            side_effect=RuntimeError("scope construction failed"),
        ), self.assertRaises(RuntimeError):
            await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        self.assertEqual(registry.safe_snapshot("physical.run-1")["status"], "FAIL")
        self.assertIsNone(conn.google_live_evidence_journey_id)
        self.assertIsNone(conn.google_live_evidence_scope)

    async def test_google_live_pre_scope_timestamp_failure_aborts_claim(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        registry = self._enroll(conn)

        with patch.object(
            helloHandle,
            "_utc_now_iso",
            side_effect=KeyboardInterrupt(),
        ), self.assertRaises(KeyboardInterrupt):
            await handleHelloMessage(conn, {"evidence_journey_id": "physical.run-1"})

        self.assertEqual(registry.safe_snapshot("physical.run-1")["status"], "FAIL")
        self.assertIsNone(conn.google_live_evidence_journey_id)
    async def test_client_audio_params_update_connection_sample_rate(self):
        conn = _Conn()

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        )

        self.assertEqual(conn.sample_rate, 16000)
        self.assertEqual(conn.input_sample_rate, 16000)
        server_hello = json.loads(conn.websocket.sent[0])
        self.assertEqual(server_hello["audio_params"]["sample_rate"], 16000)

    async def test_google_live_uses_client_sample_rate_for_input_and_configured_output_rate(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.config["google_live"] = {"output_sample_rate": 24000}

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        )

        self.assertEqual(conn.input_sample_rate, 16000)
        self.assertEqual(conn.sample_rate, 24000)
        server_hello = json.loads(conn.websocket.sent[0])
        self.assertEqual(server_hello["audio_params"]["sample_rate"], 24000)

    async def test_features_with_mcp_initializes_client_and_schedules_initialize_message(self):
        conn = _Conn()

        with patch.object(helloHandle, "MCPClient", return_value="mcp-client"), patch.object(
            helloHandle, "send_mcp_initialize_message", new=AsyncMock()
        ):
            await handleHelloMessage(conn, {"features": {"mcp": True, "vision": True}})

        self.assertEqual(conn.features, {"mcp": True, "vision": True})
        self.assertEqual(conn.mcp_client, "mcp-client")
        self.assertEqual(len(conn.mcp_scheduled), 1)
        self.assertEqual(conn.mcp_sent_counts_at_schedule, [1])
        self.assertEqual(json.loads(conn.websocket.sent[0])["type"], "hello")

    async def test_empty_hello_still_sends_server_welcome_without_overwriting_state(self):
        conn = _Conn()

        await handleHelloMessage(conn, {})

        self.assertEqual(conn.audio_format, "opus")
        self.assertEqual(conn.sample_rate, 24000)
        self.assertIsNone(conn.features)
        self.assertEqual(json.loads(conn.websocket.sent[0])["session_id"], "session-1")

    async def test_google_live_hello_activates_only_safe_evidence_journey_label(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )

        with patch.object(
            helloHandle,
            "_utc_now_iso",
            return_value="2026-08-31T03:00:00+00:00",
        ):
            await handleHelloMessage(
                conn, {"evidence_journey_id": "bargein.run-1:test"}
            )

        self.assertEqual(conn.google_live_evidence_journey_id, "bargein.run-1:test")
        ack = json.loads(conn.websocket.sent[0])
        scope = ack["evidenceScope"]
        self.assertEqual(scope["journeyId"], "bargein.run-1:test")
        self.assertEqual(scope["connectionId"], "server-connection-1")
        self.assertEqual(scope["liveConnectionId"], "live-7")
        self.assertEqual(scope["serverStartUtc"], "2026-08-31T03:00:00+00:00")
        self.assertRegex(scope["peerIdentityHash"], r"^sha256:[0-9a-f]{64}$")
        encoded = json.dumps(ack)
        self.assertNotIn("device-secret-1", encoded)
        self.assertNotIn("client-secret-1", encoded)
        self.assertEqual(conn.google_live_evidence_scope, scope)

    async def test_evidence_boundary_is_captured_before_prewarm_scope_adoption(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        adopted_at = []

        async def prepare_scope():
            adopted_at.append(helloHandle._utc_now_iso())
            return "live-7"

        conn.voice_provider = SimpleNamespace(prepare_evidence_scope=prepare_scope)
        with patch.object(
            helloHandle,
            "_utc_now_iso",
            side_effect=[
                "2026-08-31T03:00:00+00:00",
                "2026-08-31T03:00:01+00:00",
            ],
        ):
            await handleHelloMessage(conn, {"evidence_journey_id": "bargein-1"})

        scope = json.loads(conn.websocket.sent[0])["evidenceScope"]
        self.assertEqual(scope["serverStartUtc"], "2026-08-31T03:00:00+00:00")
        self.assertEqual(adopted_at, ["2026-08-31T03:00:01+00:00"])

    async def test_google_live_evidence_scope_fails_closed_without_live_identity(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value=None)
        )

        await handleHelloMessage(conn, {"evidence_journey_id": "bargein-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(ack["evidenceScope"]["status"], "FAIL")
        self.assertEqual(ack["evidenceScope"]["failureCode"], "LIVE_SCOPE_UNAVAILABLE")
        self.assertIsNone(conn.google_live_evidence_scope)

    async def test_google_live_hello_emits_trusted_same_peer_connection_transition(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.session_id = "server-connection-2"
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-31")
        )
        peer_hash = helloHandle._evidence_peer_identity_hash(conn)
        conn.google_live_previous_server_connection = {
            "connectionId": "server-connection-1",
            "peerIdentityHash": peer_hash,
            "evidenceScope": {
                "journeyId": "lesson-journey-30",
                "connectionId": "server-connection-1",
                "peerIdentityHash": peer_hash,
            },
        }

        await handleHelloMessage(conn, {"evidence_journey_id": "reconnect-journey-31"})

        ack = json.loads(conn.websocket.sent[0])
        transition = ack["connectionTransition"]
        self.assertEqual(transition["fromJourneyId"], "lesson-journey-30")
        self.assertEqual(transition["fromConnectionId"], "server-connection-1")
        self.assertEqual(transition["toJourneyId"], "reconnect-journey-31")
        self.assertEqual(transition["toConnectionId"], "server-connection-2")
        self.assertEqual(transition["peerIdentityHash"], peer_hash)
        self.assertTrue(transition["serverIssued"])
        logs = " ".join(conn.logger.infos)
        self.assertIn("evidence_server_connection_transition", logs)
        self.assertNotIn(conn.device_id, logs)
        self.assertNotIn(conn.client_id, logs)

    async def test_google_live_hello_fails_closed_for_untrusted_previous_connection(self):
        cases = {
            "missing_scope": {
                "connectionId": "server-connection-1",
                "peerIdentityHash": "unused",
                "evidenceScope": None,
            },
            "wrong_peer": {
                "connectionId": "server-connection-1",
                "peerIdentityHash": f"sha256:{'f' * 64}",
                "evidenceScope": {
                    "journeyId": "lesson-journey-30",
                    "connectionId": "server-connection-1",
                },
            },
            "same_connection": {
                "connectionId": "server-connection-2",
                "peerIdentityHash": None,
                "evidenceScope": {
                    "journeyId": "lesson-journey-30",
                    "connectionId": "server-connection-2",
                },
            },
            "missing_previous_journey": {
                "connectionId": "server-connection-1",
                "peerIdentityHash": None,
                "evidenceScope": {
                    "connectionId": "server-connection-1",
                },
            },
            "scope_peer_mismatch": {
                "connectionId": "server-connection-1",
                "peerIdentityHash": None,
                "evidenceScope": {
                    "journeyId": "lesson-journey-30",
                    "connectionId": "server-connection-1",
                    "peerIdentityHash": f"sha256:{'e' * 64}",
                },
            },
        }
        for name, previous in cases.items():
            with self.subTest(name=name):
                conn = _Conn()
                conn.config["voice_mode"] = {"type": "google_live"}
                conn.session_id = "server-connection-2"
                conn.voice_provider = SimpleNamespace(
                    prepare_evidence_scope=AsyncMock(return_value="live-31")
                )
                if previous["peerIdentityHash"] is None:
                    previous["peerIdentityHash"] = (
                        helloHandle._evidence_peer_identity_hash(conn)
                    )
                conn.google_live_previous_server_connection = previous

                await handleHelloMessage(
                    conn, {"evidence_journey_id": "reconnect-journey-31"}
                )

                ack = json.loads(conn.websocket.sent[0])
                self.assertNotIn("connectionTransition", ack)
                self.assertFalse(
                    any(
                        "evidence_server_connection_transition" in message
                        for message in conn.logger.infos
                    )
                )

    async def test_google_live_hello_emits_a_server_transition_only_once(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.session_id = "server-connection-2"
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(side_effect=["live-31", "live-31"])
        )
        peer_hash = helloHandle._evidence_peer_identity_hash(conn)
        conn.google_live_previous_server_connection = {
            "connectionId": "server-connection-1",
            "peerIdentityHash": peer_hash,
            "evidenceScope": {
                "journeyId": "lesson-journey-30",
                "connectionId": "server-connection-1",
                "peerIdentityHash": peer_hash,
            },
        }

        await handleHelloMessage(conn, {"evidence_journey_id": "reconnect-journey-31"})
        await handleHelloMessage(conn, {"evidence_journey_id": "reconnect-journey-31"})

        acks = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertIn("connectionTransition", acks[0])
        self.assertNotIn("connectionTransition", acks[1])
        self.assertEqual(
            sum(
                "evidence_server_connection_transition" in message
                for message in conn.logger.infos
            ),
            1,
        )

    async def test_google_live_hello_rejects_unsafe_evidence_journey_without_logging_value(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        unsafe = "secret journey value"

        await handleHelloMessage(conn, {"evidence_journey_id": unsafe})

        self.assertIsNone(conn.google_live_evidence_journey_id)
        self.assertNotIn(unsafe, " ".join(conn.logger.debugs + conn.logger.infos))

class GoogleLiveAudioBridgeSampleRateTest(unittest.TestCase):
    def test_input_frame_size_uses_client_input_sample_rate_not_output_rate(self):
        conn = _Conn()
        conn.input_sample_rate = 16000
        conn.sample_rate = 24000
        client = SimpleNamespace(config={"input_sample_rate": 16000})

        bridge = GoogleLiveAudioBridge(conn, client, conn.logger)

        self.assertEqual(bridge._get_input_frame_size(), 960)


if __name__ == "__main__":
    unittest.main()
