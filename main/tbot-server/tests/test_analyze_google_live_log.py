import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import analyze_google_live_log
from scripts.analyze_google_live_log import (
    analyze,
    analyze_reliability_window,
    correlate_websocket_bargein_evidence,
    summarize_pains,
)

CANDIDATE_IDENTITY = {
    "gitSha": "candidate-sha",
    "imageDigest": f"sha256:{'a' * 64}",
    "firmwareIdentity": "firmware-v1",
    "fixtureSha256": "b" * 64,
    "configFingerprint": f"sha256:{'c' * 64}",
}

EVIDENCE_SCOPE = {
    "journeyId": "bargein-journey-1",
    "connectionId": "conn-1",
    "liveConnectionId": "live-1",
    "peerIdentityHash": f"sha256:{'d' * 64}",
    "serverStartUtc": "2026-08-31T10:00:00+00:00",
}


def _window_lines(
    *body,
    candidate_identity=CANDIDATE_IDENTITY,
    window_id="window-1",
    journey_id=None,
    journeys=None,
    evidence_scope=None,
):
    identity = json.dumps(candidate_identity, sort_keys=True, separators=(",", ":"))
    evidence = ""
    if journey_id is None and evidence_scope is not None:
        journey_id = evidence_scope["journeyId"]
    if journey_id is not None:
        evidence += f"journey_id={journey_id} "
    if journeys is not None:
        evidence += f"journeys={journeys} "
    if evidence_scope is not None:
        evidence += (
            f"connection_id={evidence_scope['connectionId']} "
            f"live_connection_id={evidence_scope['liveConnectionId']} "
            f"peer_identity_hash={evidence_scope['peerIdentityHash']} "
            f"server_start_utc={evidence_scope['serverStartUtc']} "
        )
    end_scope = (
        " server_end_utc=2026-08-31T10:00:59+00:00"
        if evidence_scope is not None
        else ""
    )
    return [
        "2026-08-31 10:00:00 Google Live reliability_window_start "
        f"window_id={window_id} {evidence}candidate_identity={identity}",
        *body,
        "2026-08-31 10:00:59 Google Live reliability_window_end "
        f"window_id={window_id}{end_scope}",
    ]


def _write_log(lines):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "server.log"
    path.write_text("\n".join(lines), encoding="utf-8")
    return tmp, path


def _transport_observation(**overrides):
    observation = {
        "schemaVersion": "google-live-reliability.v1",
        "name": "websocket_audio_bargein_transport",
        "status": "SKIPPED",
        "pendingCode": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
        "correlationSource": "server_log",
        "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
        "aggregateReleaseEligible": False,
        "interruptStopMarkerObserved": True,
        "replacementResponseStarted": True,
        "replacementResponseStopped": True,
        "replacementBinaryChunks": 2,
        "maxServerOutputGapMs": 60.0,
        "bargeinStopMs": 210.0,
        "candidateIdentity": CANDIDATE_IDENTITY,
        "evidenceScope": EVIDENCE_SCOPE,
        "serverConnectionId": EVIDENCE_SCOPE["connectionId"],
        "liveConnectionId": EVIDENCE_SCOPE["liveConnectionId"],
        "peerIdentityHash": EVIDENCE_SCOPE["peerIdentityHash"],
        "logWindow": {
            "windowId": "window-1",
            "start": "2026-08-31T10:00:00+00:00",
            "end": "2026-08-31T10:00:59+00:00",
        },
        "journeyId": "bargein-journey-1",
    }
    observation.update(overrides)
    return observation


def _valid_log_verdict(**overrides):
    verdict = {
        "schemaVersion": "google-live-reliability.v1",
        "name": "google_live_log_reliability",
        "status": "PASS",
        "candidateIdentity": CANDIDATE_IDENTITY,
        "evidenceScope": EVIDENCE_SCOPE,
        "logWindow": _transport_observation()["logWindow"],
        "receiveLoopBalance": 0,
        "maxReceiveLoopsActive": 1,
        "replayCountsByReopen": {},
        "duplicateResponseIds": [],
        "staleAudioAfterReplacement": 0,
        "unrecoveredTimeouts": [],
        "unreleasedLessonHandoffs": [],
        "fatalHits": [],
        "correlation": {
            "status": "PASS",
            "cancelledResponseId": 7,
            "replacementResponseId": 8,
        },
        "correlations": [
            {
                "status": "PASS",
                "journeyId": "bargein-journey-1",
                "connectionId": "conn-1",
                "liveConnectionId": "live-1",
                "cancelledResponseId": 7,
                "replacementResponseId": 8,
            }
        ],
        "failures": [],
    }
    verdict.update(overrides)
    return verdict


def _scoped_bargein_chain(
    *, journey_id, connection_id, live_connection_id, old, new, include_stale=True
):
    scope = (
        f"journey_id={journey_id} connection_id={connection_id} "
        f"live_connection_id={live_connection_id}"
    )
    markers = [
        f"Google Live evidence_receive_loop_started {scope} generation=1",
        f"Google Live evidence_response_started {scope} response_id={old}",
        f"Google Live user_interrupt_started {scope} reason=vad "
        f"cancelled_response_id={old} next_response_id={new}",
        f"Google Live interrupt_output_stopped {scope} "
        f"cancelled_response_id={old} next_response_id={new}",
        f"Google Live evidence_user_interrupted {scope} reason=vad "
        f"cancelled_response_id={old} next_response_id={new}",
        f"Google Live evidence_interrupt_audio_replayed {scope} response_id={new}",
        f"Google Live evidence_interrupt_input_finalized {scope} response_id={new}",
        f"Google Live evidence_response_started {scope} response_id={new}",
        f"Google Live model_output_chunk_forwarded {scope} response_id={new}",
        f"Google Live evidence_response_ended {scope} response_id={new}",
        f"Google Live evidence_receive_loop_stopped {scope} generation=1",
        f"Google Live evidence_connection_close {scope} pending_tasks=0 "
        "close_code=1000 reason=evidence_finalize",
    ]
    if include_stale:
        markers.insert(
            4,
            f"Google Live evidence_stale_model_drop {scope} response_id={old} "
            f"current_response_id={new}",
        )
    return markers


class AnalyzeGoogleLiveReliabilityWindowTest(unittest.TestCase):
    def _analyze(self, lines):
        tmp, path = _write_log(lines)
        self.addCleanup(tmp.cleanup)
        return analyze_reliability_window(path)

    def test_balanced_window_passes_and_proves_correlated_bargein_lifecycle(self):
        lines = _window_lines(
            "2026-08-31 10:00:01 Google Live evidence_receive_loop_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 generation=1",
            "2026-08-31 10:00:05 Google Live model_audio_start_hold_input response_id=7",
            "2026-08-31 10:00:05 Google Live evidence_response_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=7",
            "2026-08-31 10:00:09 Google Live user_interrupt_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:10 Google Live tts_state_stop_sent response_id=7 reason=interrupt",
            "2026-08-31 10:00:10 Google Live interrupt_output_stopped journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:11 Google Live user_interrupted reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:11 Google Live evidence_user_interrupted journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:12 Google Live stale_model_event_dropped type=audio reason=blocked_until_user_turn response_id=7 current_response_id=8",
            "2026-08-31 10:00:12 Google Live evidence_stale_model_drop journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=7 current_response_id=8",
            "2026-08-31 10:00:13 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=2 bytes=3840 response_id=8",
            "2026-08-31 10:00:13 Google Live evidence_interrupt_audio_replayed journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:14 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=420 response_id=8 frames=4 bytes=7680 peak_rms=2600",
            "2026-08-31 10:00:14 Google Live evidence_interrupt_input_finalized journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:15 Google Live model_audio_start_hold_input response_id=8",
            "2026-08-31 10:00:15 Google Live evidence_response_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:16 Google Live model_output_chunk_forwarded response_id=8 bytes=1920",
            "2026-08-31 10:00:16 Google Live model_output_chunk_forwarded journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:17 Google Live model_audio_end_ready_to_listen response_id=8",
            "2026-08-31 10:00:17 Google Live evidence_response_ended journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:18 Google Live evidence_receive_loop_stopped journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 generation=1",
            "2026-08-31 10:00:19 Google Live evidence_connection_close journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 pending_tasks=0 close_code=1000 reason=evidence_finalize",
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["schemaVersion"], "google-live-reliability.v1")
        self.assertEqual(verdict["candidateIdentity"], CANDIDATE_IDENTITY)
        self.assertEqual(verdict["receiveLoopBalance"], 0)
        self.assertEqual(verdict["maxReceiveLoopsActive"], 1)
        self.assertEqual(verdict["duplicateResponseIds"], [])
        self.assertEqual(verdict["staleAudioAfterReplacement"], 0)
        self.assertEqual(verdict["fatalHits"], [])
        self.assertEqual(verdict["correlation"]["status"], "PASS")
        self.assertEqual(verdict["correlation"]["cancelledResponseId"], 7)
        self.assertEqual(verdict["correlation"]["replacementResponseId"], 8)
        self.assertNotIn("bytes=", json.dumps(verdict))

        combined = correlate_websocket_bargein_evidence(
            _transport_observation(),
            verdict,
            expected_candidate_identity=CANDIDATE_IDENTITY,
        )
        self.assertEqual(combined["status"], "PASS", combined)
        self.assertTrue(combined["aggregateReleaseEligible"])
        self.assertEqual(combined["correlationStatus"], "PASS")
        self.assertEqual(combined["candidateIdentity"], CANDIDATE_IDENTITY)

    def test_scoped_bargein_passes_without_optional_stale_drop(self):
        body = [
            f"2026-08-31 10:00:{second:02d} {marker}"
            for second, marker in enumerate(
                _scoped_bargein_chain(
                    journey_id="bargein-journey-1",
                    connection_id="conn-1",
                    live_connection_id="live-1",
                    old=7,
                    new=8,
                    include_stale=False,
                ),
                start=1,
            )
        ]
        body.append(
            "2026-08-31 10:00:20 Google Live evidence_connection_close "
            "journey_id=bargein-journey-1 connection_id=conn-1 "
            "live_connection_id=live-1 pending_tasks=0 close_code=1000 "
            "reason=evidence_finalize"
        )

        verdict = self._analyze(
            _window_lines(
                *body,
                window_id="window-1",
                journey_id="bargein-journey-1",
                journeys="bargein",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_scoped_cleanup_must_match_anchored_connection_and_live_id(self):
        body = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        body.append(
            "Google Live evidence_connection_close journey_id=bargein-journey-1 "
            "connection_id=conn-2 live_connection_id=live-2 pending_tasks=0 "
            "close_code=1000 reason=evidence_finalize"
        )
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(body, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "EVIDENCE_CLEANUP_SCOPE_MISMATCH",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_cleanup_requires_finalize_close_semantics(self):
        for close_code, reason in (("1001", "evidence_finalize"), ("1000", "other")):
            with self.subTest(close_code=close_code, reason=reason):
                body = _scoped_bargein_chain(
                    journey_id="bargein-journey-1",
                    connection_id="conn-1",
                    live_connection_id="live-1",
                    old=7,
                    new=8,
                    include_stale=False,
                )
                body.append(
                    "Google Live evidence_connection_close "
                    "journey_id=bargein-journey-1 connection_id=conn-1 "
                    "live_connection_id=live-1 pending_tasks=0 "
                    f"close_code={close_code} reason={reason}"
                )
                verdict = self._analyze(
                    _window_lines(
                        *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(body, 1)),
                        journey_id="bargein-journey-1",
                        journeys="bargein",
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )

                self.assertEqual(verdict["status"], "FAIL", verdict)
                self.assertIn(
                    "EVIDENCE_CLEANUP_SEMANTICS_INVALID",
                    [item["code"] for item in verdict["failures"]],
                )

    def test_foreign_journey_cleanup_does_not_poison_exact_scoped_cleanup(self):
        body = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        body.extend(
            [
                "Google Live evidence_connection_close journey_id=other-journey "
                "connection_id=conn-2 live_connection_id=live-2 pending_tasks=5 "
                "close_code=1000 reason=evidence_finalize",
                "Google Live evidence_connection_close journey_id=bargein-journey-1 "
                "connection_id=conn-1 live_connection_id=live-1 pending_tasks=0 "
                "close_code=1000 reason=evidence_finalize",
            ]
        )
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(body, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["staleAudioAfterReplacement"], 0)

    def test_late_cancelled_chunk_after_replacement_first_chunk_fails(self):
        chain = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        chain.insert(
            -1,
            "Google Live model_output_chunk_forwarded journey_id=bargein-journey-1 "
            "connection_id=conn-1 live_connection_id=live-1 response_id=7",
        )
        body = [
            f"2026-08-31 10:00:{second:02d} {marker}"
            for second, marker in enumerate(chain, start=1)
        ]

        verdict = self._analyze(
            _window_lines(*body, journey_id="bargein-journey-1")
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "STALE_AUDIO_AFTER_REPLACEMENT",
            [item["code"] for item in verdict["failures"]],
        )

    def test_public_correlation_rejects_non_mapping_inputs_without_crash(self):
        for name, value in {
            "list": [],
            "null": None,
            "string": "secret-raw-input",
            "number": 42,
        }.items():
            with self.subTest(name=name, layer="transport"):
                combined = correlate_websocket_bargein_evidence(
                    value,
                    _valid_log_verdict(),
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertFalse(combined["aggregateReleaseEligible"])
                self.assertNotIn("secret-raw-input", json.dumps(combined))
            with self.subTest(name=name, layer="log"):
                combined = correlate_websocket_bargein_evidence(
                    _transport_observation(),
                    value,
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertFalse(combined["aggregateReleaseEligible"])
                self.assertNotIn("secret-raw-input", json.dumps(combined))

    def test_lifecycle_failures_are_fail_closed(self):
        cases = {
            "two_receive_starts": (
                [
                    "2026-08-31 10:00:01 Google Live receive loop started",
                    "2026-08-31 10:00:02 Google Live receive loop started",
                ],
                "RECEIVE_LOOP_OVERLAP",
            ),
            "duplicate_replay_for_reopen": (
                [
                    "2026-08-31 10:00:01 reconnect_succeeded attempt=1 live_connection_id=live-2",
                    "2026-08-31 10:00:02 Google Live replayed_buffered_audio frames=2 bytes=20",
                    "2026-08-31 10:00:03 Google Live replayed_buffered_audio frames=1 bytes=10",
                ],
                "DUPLICATE_BUFFER_REPLAY",
            ),
            "unrecovered_timeout": (
                [
                    "2026-08-31 10:00:01 Google Live waiting_model_timeout timeout_sec=5",
                    "2026-08-31 10:00:02 unrelated marker",
                ],
                "UNRECOVERED_TIMEOUT",
            ),
            "old_audio_after_replacement": (
                [
                    "2026-08-31 10:00:01 Google Live user_interrupted reason=vad cancelled_response_id=3 next_response_id=4",
                    "2026-08-31 10:00:02 Google Live model_audio_start_hold_input response_id=4",
                    "2026-08-31 10:00:03 Google Live model_output_chunk_forwarded response_id=3 bytes=1920",
                ],
                "STALE_AUDIO_AFTER_REPLACEMENT",
            ),
            "non_retriable_reconnect": (
                [
                    "2026-08-31 10:00:01 Google Live classify_error kind=auth retry=no",
                    "2026-08-31 10:00:02 reconnect_started reason=auth attempt=1 state=RECONNECTING",
                ],
                "NON_RETRIABLE_RECONNECT",
            ),
            "unreleased_handoff": (
                [
                    "2026-08-31 10:00:01 lesson_start_handoff_acquired lease=(1, 1) reason=lesson_start_intent",
                ],
                "UNRELEASED_LESSON_HANDOFF",
            ),
            "pending_task_at_close": (
                [
                    "2026-08-31 10:00:01 Google Live connection_close pending_tasks=flush,timeout,replay",
                ],
                "PENDING_TASK_AT_CLOSE",
            ),
            "lesson_ping_without_progress": (
                [
                    "2026-08-31 10:00:01 Google Live lesson_step_started step_id=step-1",
                    "2026-08-31 10:00:02 firmware_ping lesson_step=step-1",
                    "2026-08-31 10:00:03 firmware_ping lesson_step=step-1",
                    "2026-08-31 10:00:04 Google Live lesson_step_ended step_id=step-1",
                ],
                "LESSON_PING_WITHOUT_PROGRESS",
            ),
        }

        for name, (body, expected_code) in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(_window_lines(*body))
                self.assertEqual(verdict["status"], "FAIL", verdict)
                self.assertIn(expected_code, [item["code"] for item in verdict["failures"]])

    def test_timeout_is_allowed_only_with_ordered_terminal_recovery(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live waiting_model_timeout timeout_sec=5",
                "2026-08-31 10:00:02 reconnect_succeeded attempt=1 live_connection_id=live-2",
            )
        )

        self.assertEqual(verdict["unrecoveredTimeouts"], [])
        self.assertNotIn("UNRECOVERED_TIMEOUT", [item["code"] for item in verdict["failures"]])

    def test_receive_loop_stop_does_not_recover_waiting_model_timeout(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live receive loop started",
                "2026-08-31 10:00:02 Google Live waiting_model_timeout timeout_sec=5",
                "2026-08-31 10:00:03 Google Live receive loop stopped",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "UNRECOVERED_TIMEOUT",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_timeout_outcomes_pair_with_each_timeout_occurrence(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:02 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=unhandled",
                f"2026-08-31 10:00:03 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:04 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=handled",
                f"2026-08-31 10:00:05 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=handled",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        codes = [item["code"] for item in verdict["failures"]]
        self.assertIn("RECEIVE_TIMEOUT_UNHANDLED", codes)
        self.assertIn("TIMEOUT_OUTCOME_WITHOUT_TIMEOUT", codes)

    def test_buffered_replay_requires_successful_reopen_marker(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 reconnect_succeeded attempt=1 live_connection_id=live-2",
                "2026-08-31 10:00:03 Google Live replayed_buffered_audio frames=2 bytes=20",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["replayCountsByReopen"], {"attempt-1": 1})

    def test_buffered_replay_accepts_production_reopen_ready_before_success_summary(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 Google Live reopen_ready reason=network attempt=1 live_connection_id=live-2",
                "2026-08-31 10:00:03 Google Live replayed_buffered_audio frames=2 bytes=20",
                "2026-08-31 10:00:04 reconnect_succeeded attempt=1 live_connection_id=live-2",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["replayCountsByReopen"], {"attempt-1": 1})

    def test_replay_from_prior_attempt_cannot_satisfy_new_reconnect_attempt(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 Google Live reopen_ready reason=network attempt=1 live_connection_id=live-1",
                "2026-08-31 10:00:03 reconnect_succeeded attempt=1 live_connection_id=live-1",
                "2026-08-31 10:00:04 reconnect_started reason=network attempt=2 state=RECONNECTING",
                "2026-08-31 10:00:05 Google Live replayed_buffered_audio frames=2 bytes=20",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "BUFFER_REPLAY_WITHOUT_SUCCESSFUL_REOPEN",
            [item["code"] for item in verdict["failures"]],
        )

    def test_reconnect_attempts_own_their_replay_independently(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 Google Live reopen_ready reason=network attempt=1 live_connection_id=live-1",
                "2026-08-31 10:00:03 Google Live replayed_buffered_audio frames=1 bytes=10",
                "2026-08-31 10:00:04 reconnect_succeeded attempt=1 live_connection_id=live-1",
                "2026-08-31 10:00:05 reconnect_started reason=network attempt=2 state=RECONNECTING",
                "2026-08-31 10:00:06 Google Live reopen_ready reason=network attempt=2 live_connection_id=live-2",
                "2026-08-31 10:00:07 Google Live replayed_buffered_audio frames=1 bytes=10",
                "2026-08-31 10:00:08 reconnect_succeeded attempt=2 live_connection_id=live-2",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["replayCountsByReopen"], {"attempt-1": 1, "attempt-2": 1})

    def test_buffered_replay_before_reconnect_failure_is_not_certified(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 Google Live replayed_buffered_audio frames=2 bytes=20",
                "2026-08-31 10:00:03 reconnect_failed attempt=1 error_class=network",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "BUFFER_REPLAY_WITHOUT_SUCCESSFUL_REOPEN",
            [item["code"] for item in verdict["failures"]],
        )

    def test_reopen_failure_after_buffer_replay_fails(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 reconnect_started reason=network attempt=1 state=RECONNECTING",
                "2026-08-31 10:00:02 Google Live reopen_ready reason=network attempt=1 live_connection_id=live-2",
                "2026-08-31 10:00:03 Google Live replayed_buffered_audio frames=2 bytes=20",
                "2026-08-31 10:00:04 reconnect_failed attempt=1 error_class=network",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "REOPEN_FAILED_AFTER_BUFFER_REPLAY",
            [item["code"] for item in verdict["failures"]],
        )

    def test_non_retriable_classification_blocks_later_reconnect_with_other_reason(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live classify_error kind=auth retry=no",
                "2026-08-31 10:00:02 reconnect_started reason=network attempt=1 state=RECONNECTING",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "NON_RETRIABLE_RECONNECT",
            [item["code"] for item in verdict["failures"]],
        )

    def test_clean_connection_close_is_an_exact_timeout_terminal(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live waiting_model_timeout timeout_sec=5",
                "2026-08-31 10:00:02 Client disconnected device_id=robot-1 close_code=1000 close_reason_sha256=- close_reason_length=0",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["unrecoveredTimeouts"], [])

    def test_abnormal_connection_close_does_not_recover_timeout(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live waiting_model_timeout timeout_sec=5",
                "2026-08-31 10:00:02 Client disconnected device_id=robot-1 close_code=1011 close_reason_sha256=abc close_reason_length=3",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "UNRECOVERED_TIMEOUT",
            [item["code"] for item in verdict["failures"]],
        )

    def test_correlation_markers_must_follow_the_causal_order(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live user_interrupted reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live model_audio_start_hold_input response_id=8",
                "2026-08-31 10:00:03 Google Live tts_state_stop_sent response_id=7 reason=interrupt",
                "2026-08-31 10:00:04 Google Live stale_model_event_dropped type=audio reason=blocked response_id=7 current_response_id=8",
                "2026-08-31 10:00:05 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=1 bytes=10 response_id=8",
                "2026-08-31 10:00:06 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=100 response_id=8 frames=1 bytes=10 peak_rms=1000",
                "2026-08-31 10:00:07 Google Live model_audio_end_ready_to_listen response_id=8",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "BARGEIN_CORRELATION_ORDER_INVALID",
            [item["code"] for item in verdict["failures"]],
        )

    def test_reliability_marker_outside_closed_window_fails(self):
        verdict = self._analyze(
            _window_lines()
            + ["2026-08-31 10:01:00 Google Live receive loop started"]
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "OUT_OF_WINDOW_RELIABILITY_MARKER",
            [item["code"] for item in verdict["failures"]],
        )

    def test_lesson_progress_before_ping_does_not_prove_post_ping_liveness(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live lesson_step_started step_id=step-1",
                "2026-08-31 10:00:02 Google Live lesson_step_progress step_id=step-1",
                "2026-08-31 10:00:03 firmware_ping lesson_step=step-1",
                "2026-08-31 10:00:04 firmware_ping lesson_step=step-1",
                "2026-08-31 10:00:05 Google Live lesson_step_ended step_id=step-1",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "LESSON_PING_WITHOUT_PROGRESS",
            [item["code"] for item in verdict["failures"]],
        )

    def test_window_anchors_and_candidate_identity_must_be_unique_and_well_formed(self):
        cases = {
            "missing_start": _window_lines()[1:],
            "duplicate_start": [*_window_lines()[:1], *_window_lines()],
            "wrong_end_id": _window_lines()[:-1]
            + ["2026-08-31 10:00:59 Google Live reliability_window_end window_id=other"],
            "timestamp_regression": _window_lines(
                "2026-08-31 10:00:20 Google Live receive loop started",
                "2026-08-31 10:00:19 Google Live receive loop stopped",
            ),
            "malformed_relevant_line": _window_lines(
                "not-a-timestamp Google Live receive loop started"
            ),
        }

        for name, lines in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(lines)
                self.assertEqual(verdict["status"], "FAIL", verdict)
                self.assertTrue(verdict["failures"])

    def test_candidate_identity_anchor_accepts_json_string_spaces(self):
        identity = {**CANDIDATE_IDENTITY, "firmwareIdentity": "firmware build 1"}

        verdict = self._analyze(_window_lines(candidate_identity=identity))

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["candidateIdentity"], identity)

    def test_combined_evidence_rejects_missing_ambiguous_out_of_window_or_mismatched_inputs(self):
        valid = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live user_interrupted reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live tts_state_stop_sent response_id=7 reason=interrupt",
                "2026-08-31 10:00:03 Google Live stale_model_event_dropped type=audio reason=blocked response_id=7 current_response_id=8",
                "2026-08-31 10:00:04 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=1 bytes=10 response_id=8",
                "2026-08-31 10:00:05 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=100 response_id=8 frames=1 bytes=10 peak_rms=1000",
                "2026-08-31 10:00:06 Google Live model_audio_start_hold_input response_id=8",
                "2026-08-31 10:00:07 Google Live model_audio_end_ready_to_listen response_id=8",
            )
        )
        cases = {
            "missing_identity": _transport_observation(candidateIdentity=None),
            "wrong_identity": _transport_observation(
                candidateIdentity={**CANDIDATE_IDENTITY, "gitSha": "other"}
            ),
            "wrong_window": _transport_observation(
                logWindow={
                    "windowId": "other",
                    "start": "2026-08-31T10:00:00+00:00",
                    "end": "2026-08-31T10:00:59+00:00",
                }
            ),
            "already_passed_transport": _transport_observation(status="PASS"),
            "invented_response_ids": _transport_observation(responseId="made-up"),
            "malformed_latency": _transport_observation(bargeinStopMs=float("nan")),
            "malformed_chunk_count": _transport_observation(
                replacementBinaryChunks=True
            ),
            "latency_over_budget": _transport_observation(bargeinStopMs=501.0),
            "output_gap_over_budget": _transport_observation(
                maxServerOutputGapMs=251.0
            ),
        }

        for name, observation in cases.items():
            with self.subTest(name=name):
                combined = correlate_websocket_bargein_evidence(
                    observation,
                    valid,
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertFalse(combined["aggregateReleaseEligible"])

    def test_transport_correlation_requires_exactly_one_matching_journey(self):
        base_log = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "google_live_log_reliability",
            "status": "PASS",
            "candidateIdentity": CANDIDATE_IDENTITY,
            "logWindow": _transport_observation()["logWindow"],
            "correlations": [],
            "failures": [],
        }
        match = {
            "status": "PASS",
            "journeyId": "bargein-journey-1",
            "connectionId": "conn-1",
            "liveConnectionId": "live-1",
            "cancelledResponseId": 7,
            "replacementResponseId": 8,
        }

        for correlations in ([], [match, dict(match)]):
            with self.subTest(count=len(correlations)):
                combined = correlate_websocket_bargein_evidence(
                    _transport_observation(),
                    {**base_log, "correlations": correlations},
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertIn(
                    "SERVER_LOG_SCOPE_CORRELATION_COUNT",
                    [item["code"] for item in combined["failures"]],
                )

    def test_transport_correlation_requires_exact_server_scope(self):
        cases = {
            "connection": _transport_observation(serverConnectionId="conn-2"),
            "live": _transport_observation(liveConnectionId="live-2"),
            "peer": _transport_observation(peerIdentityHash=f"sha256:{'e' * 64}"),
            "scope": _transport_observation(
                evidenceScope={**EVIDENCE_SCOPE, "connectionId": "conn-2"}
            ),
        }

        for name, transport in cases.items():
            with self.subTest(name=name):
                combined = correlate_websocket_bargein_evidence(
                    transport,
                    _valid_log_verdict(),
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertFalse(combined["aggregateReleaseEligible"])

    def test_transport_correlation_does_not_accept_same_journey_from_other_connection(self):
        log_verdict = _valid_log_verdict(
            correlations=[
                {
                    **_valid_log_verdict()["correlations"][0],
                    "connectionId": "conn-2",
                }
            ]
        )

        combined = correlate_websocket_bargein_evidence(
            _transport_observation(),
            log_verdict,
            expected_candidate_identity=CANDIDATE_IDENTITY,
        )

        self.assertEqual(combined["status"], "FAIL", combined)
        self.assertIn(
            "SERVER_LOG_SCOPE_CORRELATION_COUNT",
            [item["code"] for item in combined["failures"]],
        )

    def test_transport_correlation_rejects_non_utc_or_reversed_server_windows(self):
        cases = {
            "naive": ("2026-08-31T10:00:00", "2026-08-31T10:00:59"),
            "offset": ("2026-08-31T17:00:00+07:00", "2026-08-31T17:00:59+07:00"),
            "reversed": ("2026-08-31T10:01:00+00:00", "2026-08-31T10:00:59+00:00"),
        }

        for name, (start, end) in cases.items():
            with self.subTest(name=name):
                scope = {**EVIDENCE_SCOPE, "serverStartUtc": start}
                window = {"windowId": "window-1", "start": start, "end": end}
                transport = _transport_observation(
                    evidenceScope=scope,
                    logWindow=window,
                )
                log_verdict = _valid_log_verdict(
                    evidenceScope=scope,
                    logWindow=window,
                )

                combined = correlate_websocket_bargein_evidence(
                    transport,
                    log_verdict,
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )

                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertIn(
                    "TRANSPORT_EVIDENCE_WINDOW_INVALID",
                    [item["code"] for item in combined["failures"]],
                )

    def test_transport_correlation_rejects_incompatible_log_report_contract(self):
        cases = {
            "wrong_schema": _valid_log_verdict(schemaVersion="other"),
            "wrong_name": _valid_log_verdict(name="fabricated"),
            "wrong_status": _valid_log_verdict(status="SKIPPED"),
            "missing_failures": _valid_log_verdict(),
            "malformed_correlations": _valid_log_verdict(correlations={}),
            "boolean_receive_balance": _valid_log_verdict(receiveLoopBalance=False),
            "boolean_stale_audio": _valid_log_verdict(
                staleAudioAfterReplacement=False
            ),
            "fabricated_minimal": {
                "status": "PASS",
                "candidateIdentity": CANDIDATE_IDENTITY,
                "logWindow": _transport_observation()["logWindow"],
                "correlations": _valid_log_verdict()["correlations"],
            },
        }
        cases["missing_failures"].pop("failures")

        for name, log_verdict in cases.items():
            with self.subTest(name=name):
                combined = correlate_websocket_bargein_evidence(
                    _transport_observation(),
                    log_verdict,
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertFalse(combined["aggregateReleaseEligible"])
                self.assertIn(
                    "SERVER_LOG_CONTRACT_MISMATCH",
                    [item["code"] for item in combined["failures"]],
                )

    def test_transport_correlation_preserves_valid_multi_journey_log_report(self):
        body = []
        for second, marker in enumerate(
            [
                *_scoped_bargein_chain(
                    journey_id="bargein-journey-1",
                    connection_id="conn-1",
                    live_connection_id="live-1",
                    old=7,
                    new=8,
                ),
                *_scoped_bargein_chain(
                    journey_id="other-journey",
                    connection_id="conn-2",
                    live_connection_id="live-2",
                    old=9,
                    new=10,
                ),
            ],
            start=1,
        ):
            body.append(f"2026-08-31 10:00:{second:02d} {marker}")
        log_verdict = self._analyze(
            _window_lines(
                *body,
                window_id="window-1",
                journeys="bargein",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(log_verdict["status"], "PASS", log_verdict)
        self.assertEqual(
            log_verdict["correlation"],
            {
                "status": "PASS",
                "cancelledResponseId": 7,
                "replacementResponseId": 8,
            },
        )
        self.assertEqual(len(log_verdict["correlations"]), 1)

        combined = correlate_websocket_bargein_evidence(
            _transport_observation(),
            log_verdict,
            expected_candidate_identity=CANDIDATE_IDENTITY,
        )

        self.assertEqual(combined["status"], "PASS", combined)
        self.assertTrue(combined["aggregateReleaseEligible"])

    def test_transport_correlation_rejects_fabricated_summary_for_multiple_entries(self):
        other = {
            **_valid_log_verdict()["correlations"][0],
            "journeyId": "other-journey",
            "connectionId": "conn-2",
            "liveConnectionId": "live-2",
            "cancelledResponseId": 9,
            "replacementResponseId": 10,
        }
        correlations = [*_valid_log_verdict()["correlations"], other]
        cases = {
            "pass_with_multiple": _valid_log_verdict(correlations=correlations),
            "impossible_count": _valid_log_verdict(
                correlation={"status": "MULTIPLE", "observedInterrupts": 999},
                correlations=correlations,
            ),
        }

        for name, log_verdict in cases.items():
            with self.subTest(name=name):
                combined = correlate_websocket_bargein_evidence(
                    _transport_observation(),
                    log_verdict,
                    expected_candidate_identity=CANDIDATE_IDENTITY,
                )
                self.assertEqual(combined["status"], "FAIL", combined)
                self.assertIn(
                    "SERVER_LOG_CONTRACT_MISMATCH",
                    [item["code"] for item in combined["failures"]],
                )

    def test_multiple_complete_interrupt_chains_pass_overall_analyzer(self):
        chain = [
            "Google Live user_interrupted reason=vad cancelled_response_id={old} next_response_id={new}",
            "Google Live tts_state_stop_sent response_id={old} reason=interrupt",
            "Google Live stale_model_event_dropped type=audio reason=blocked response_id={old} current_response_id={new}",
            "Google Live replayed_interrupt_audio reason=model_output_unblocked frames=1 bytes=10 response_id={new}",
            "Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=100 response_id={new} frames=1 bytes=10 peak_rms=1000",
            "Google Live model_audio_start_hold_input response_id={new}",
            "Google Live model_audio_end_ready_to_listen response_id={new}",
        ]
        body = []
        second = 1
        for old, new in ((1, 2), (2, 3)):
            for marker in chain:
                body.append(
                    f"2026-08-31 10:00:{second:02d} "
                    + marker.format(old=old, new=new)
                )
                second += 1

        verdict = self._analyze(_window_lines(*body))

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(len(verdict["correlations"]), 2)

    def test_claimed_bargein_without_scoped_forward_and_cleanup_is_coverage_missing(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live interrupt_output_stopped journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:03 Google Live evidence_user_interrupted journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
                journey_id="bargein-journey-1",
                journeys="bargein",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn("COVERAGE_MISSING", [item["code"] for item in verdict["failures"]])

    def test_claimed_scoped_bargein_requires_receive_loop_coverage(self):
        body = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        body = [line for line in body if "evidence_receive_loop_" not in line]
        body.append(
            "Google Live evidence_connection_close journey_id=bargein-journey-1 "
            "connection_id=conn-1 live_connection_id=live-1 pending_tasks=0 "
            "close_code=1000 reason=evidence_finalize"
        )

        verdict = self._analyze(
            _window_lines(
                *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(body, 1)),
                journey_id="bargein-journey-1",
                journeys="bargein",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn("COVERAGE_MISSING", [item["code"] for item in verdict["failures"]])

    def test_exact_scoped_bargein_requires_recognized_unique_journey_claim(self):
        body = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        body.append(
            "Google Live evidence_connection_close journey_id=bargein-journey-1 "
            "connection_id=conn-1 live_connection_id=live-1 pending_tasks=0 "
            "close_code=1000 reason=evidence_finalize"
        )
        cases = {
            "missing": (None, "JOURNEY_CLAIM_MISSING"),
            "unknown": ("unknown", "JOURNEY_CLAIM_INVALID"),
            "duplicate": ("bargein,bargein", "JOURNEY_CLAIM_INVALID"),
        }

        for name, (journeys, expected) in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(
                    _window_lines(
                        *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(body, 1)),
                        journey_id="bargein-journey-1",
                        journeys=journeys,
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )
                self.assertIn(
                    expected,
                    [item["code"] for item in verdict["failures"]],
                )

    def test_duplicate_scoped_handoff_acquire_cannot_be_balanced_by_one_release(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
                f"2026-08-31 10:00:02 Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=duplicate",
                f"2026-08-31 10:00:03 Google Live evidence_lesson_handoff_released {scope} generation=1 holder=1 outcome=lesson_started",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "DUPLICATE_LESSON_HANDOFF_ACQUIRE",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_handoff_release_without_acquire_fails(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_lesson_handoff_released {scope} generation=1 holder=1 outcome=lesson_started",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "HANDOFF_TERMINAL_WITHOUT_ACQUIRE",
            [item["code"] for item in verdict["failures"]],
        )

    def test_response_id_reuse_across_live_connections_passes_but_same_scope_duplicates_fail(self):
        cross_scope = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1",
                "2026-08-31 10:00:02 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1",
                "2026-08-31 10:00:03 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l2 response_id=1",
                "2026-08-31 10:00:04 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l2 response_id=1",
            )
        )
        duplicate = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1",
                "2026-08-31 10:00:02 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1",
            )
        )

        self.assertEqual(cross_scope["status"], "PASS", cross_scope)
        self.assertEqual(duplicate["status"], "FAIL", duplicate)
        self.assertIn("DUPLICATE_RESPONSE_ID", [item["code"] for item in duplicate["failures"]])

    def test_abnormal_connection_close_fails_without_timeout(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Client disconnected device_id=robot-1 close_code=1011 close_reason_sha256=abc close_reason_length=3"
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn("ABNORMAL_CONNECTION_CLOSE", [item["code"] for item in verdict["failures"]])

    def test_scoped_pending_tasks_at_close_fail(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_connection_close journey_id=j1 connection_id=c1 live_connection_id=l1 pending_tasks=5 close_code=1000 reason=evidence_finalize"
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn("PENDING_TASK_AT_CLOSE", [item["code"] for item in verdict["failures"]])

    def test_claimed_lesson_and_reconnect_require_non_vacuous_coverage(self):
        for journey in ("lesson", "reconnect"):
            with self.subTest(journey=journey):
                verdict = self._analyze(
                    _window_lines(
                        journey_id=f"{journey}-journey-1",
                        journeys=journey,
                    )
                )
                self.assertEqual(verdict["status"], "FAIL", verdict)
                self.assertIn(
                    "COVERAGE_MISSING",
                    [item["code"] for item in verdict["failures"]],
                )

    def test_scoped_chain_cannot_mix_connections_with_same_response_ids(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:00 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7",
                "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live interrupt_output_stopped journey_id=j1 connection_id=c1 live_connection_id=l1 cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:03 Google Live evidence_user_interrupted journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:04 Google Live evidence_stale_model_drop journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=7 current_response_id=8",
                "2026-08-31 10:00:05 Google Live evidence_interrupt_audio_replayed journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=8",
                "2026-08-31 10:00:06 Google Live evidence_interrupt_input_finalized journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=8",
                "2026-08-31 10:00:07 Google Live evidence_response_started journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=8",
                "2026-08-31 10:00:08 Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=8",
                "2026-08-31 10:00:09 Google Live evidence_response_ended journey_id=j1 connection_id=c2 live_connection_id=l2 response_id=8",
                journey_id="j1",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "SCOPED_BARGEIN_CORRELATION_INVALID",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_reconnect_attempts_are_owned_by_connection(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 reason=network",
                "2026-08-31 10:00:02 Google Live evidence_reconnect_started journey_id=j1 connection_id=c2 live_connection_id=l2 attempt=1 reason=network",
                "2026-08-31 10:00:03 Google Live evidence_reopen_ready journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:04 Google Live evidence_replayed_buffered_audio journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:05 Google Live evidence_reconnect_succeeded journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1",
                "2026-08-31 10:00:06 Google Live evidence_reopen_ready journey_id=j1 connection_id=c2 attempt=1 live_connection_id=l2",
                "2026-08-31 10:00:07 Google Live evidence_reconnect_succeeded journey_id=j1 connection_id=c2 live_connection_id=l2 attempt=1",
                journey_id="j1",
                journeys="reconnect",
            )
        )
        wrong_owner = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 reason=network",
                "2026-08-31 10:00:02 Google Live evidence_reopen_ready journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:03 Google Live evidence_replayed_buffered_audio journey_id=j1 connection_id=c2 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:04 Google Live evidence_reconnect_succeeded journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1",
                journey_id="j1",
                journeys="reconnect",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(wrong_owner["status"], "FAIL", wrong_owner)
        self.assertIn(
            "BUFFER_REPLAY_WITHOUT_SUCCESSFUL_REOPEN",
            [item["code"] for item in wrong_owner["failures"]],
        )

    def test_scoped_old_response_chunk_after_replacement_start_fails(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:00 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7",
                "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live interrupt_output_stopped journey_id=j1 connection_id=c1 live_connection_id=l1 cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:03 Google Live evidence_user_interrupted journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:04 Google Live evidence_stale_model_drop journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7 current_response_id=8",
                "2026-08-31 10:00:05 Google Live evidence_interrupt_audio_replayed journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:06 Google Live evidence_interrupt_input_finalized journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:07 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:08 Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7",
                "2026-08-31 10:00:09 Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:10 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                journey_id="j1",
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertEqual(verdict["staleAudioAfterReplacement"], 1)
        self.assertIn("STALE_AUDIO_AFTER_REPLACEMENT", [item["code"] for item in verdict["failures"]])

    def test_scoped_replacement_accepts_one_two_or_many_owned_chunks(self):
        for chunk_count in (1, 2, 5):
            with self.subTest(chunk_count=chunk_count):
                chain = _scoped_bargein_chain(
                    journey_id="bargein-journey-1",
                    connection_id="conn-1",
                    live_connection_id="live-1",
                    old=7,
                    new=8,
                    include_stale=False,
                )
                forwarded = chain.index(
                    "Google Live model_output_chunk_forwarded "
                    "journey_id=bargein-journey-1 connection_id=conn-1 "
                    "live_connection_id=live-1 response_id=8"
                )
                chain[forwarded:forwarded + 1] = [chain[forwarded]] * chunk_count
                lines = _window_lines(
                    *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(chain, 1)),
                    journey_id="bargein-journey-1",
                    journeys="bargein",
                    evidence_scope=EVIDENCE_SCOPE,
                )

                verdict = self._analyze(lines)

                self.assertEqual(verdict["status"], "PASS", verdict)

    def test_foreign_scoped_state_neither_satisfies_nor_poisons_target(self):
        target = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        foreign = [
            "Google Live evidence_response_started journey_id=other-journey "
            "connection_id=conn-2 live_connection_id=live-2 response_id=99",
            "Google Live evidence_reconnect_started journey_id=other-journey "
            "connection_id=conn-2 live_connection_id=live-2 attempt=1 reason=network",
            "Google Live user_interrupt_started journey_id=other-journey "
            "connection_id=conn-2 live_connection_id=live-2 reason=vad "
            "cancelled_response_id=123 next_response_id=124",
            "Google Live firmware_ping journey_id=other-journey connection_id=conn-2 "
            "live_connection_id=live-2 lesson_step=foreign-step",
        ]
        interleaved = [target[0], *foreign, *target[1:]]
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(interleaved, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_same_journey_wrong_connection_is_explicit_scope_mismatch(self):
        target = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        target.insert(
            1,
            "Google Live evidence_response_started journey_id=bargein-journey-1 "
            "connection_id=conn-2 live_connection_id=live-2 response_id=99",
        )
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(target, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "EVIDENCE_SCOPE_MISMATCH",
            [item["code"] for item in verdict["failures"]],
        )

    def test_unscoped_legacy_markers_do_not_poison_exact_scoped_evidence(self):
        target = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        target[1:1] = [
            "Google Live user_interrupted reason=vad cancelled_response_id=90 next_response_id=91",
            "Google Live model_output_chunk_forwarded response_id=90 bytes=1920",
            "Google Live evidence_reconnect_started journey_id=other-journey connection_id=conn-9 live_connection_id=live-9 attempt=1 reason=network",
            "Google Live connection_close pending_tasks=5",
        ]
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(target, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_scoped_lesson_and_ping_validate_scope_without_active_step(self):
        lines = _window_lines(
            "2026-08-31 10:00:01 Google Live firmware_ping "
            "journey_id=bargein-journey-1 connection_id=conn-2 "
            "live_connection_id=live-2 lesson_step=step-1",
            "2026-08-31 10:00:02 Google Live lesson_step_progress "
            "journey_id=bargein-journey-1 connection_id=conn-2 "
            "live_connection_id=live-2 step_id=step-1",
            journey_id="bargein-journey-1",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertEqual(
            [item["code"] for item in verdict["failures"]].count(
                "EVIDENCE_SCOPE_MISMATCH"
            ),
            2,
        )

    def test_foreign_disconnects_do_not_mutate_exact_scoped_evidence(self):
        target = _scoped_bargein_chain(
            journey_id="bargein-journey-1",
            connection_id="conn-1",
            live_connection_id="live-1",
            old=7,
            new=8,
            include_stale=False,
        )
        target[1:1] = [
            "Client disconnected close_code=1006",
            "Client disconnected close_code=1000",
            "Google Live clean_close",
        ]
        lines = _window_lines(
            *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(target, 1)),
            journey_id="bargein-journey-1",
            journeys="bargein",
            evidence_scope=EVIDENCE_SCOPE,
        )

        verdict = self._analyze(lines)

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_scoped_receive_timeout_and_handoff_ignore_foreign_state(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        foreign = "journey_id=other connection_id=conn-2 live_connection_id=live-2"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_receive_loop_started {scope} generation=1",
                f"2026-08-31 10:00:02 Google Live evidence_receive_loop_started {foreign} generation=1",
                f"2026-08-31 10:00:03 Google Live evidence_receive_timeout {foreign} generation=1",
                f"2026-08-31 10:00:04 Google Live evidence_lesson_handoff_acquired {foreign} generation=9 holder=1 reason=lesson_start",
                f"2026-08-31 10:00:05 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:06 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=handled",
                f"2026-08-31 10:00:07 Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
                f"2026-08-31 10:00:08 Google Live evidence_lesson_handoff_released {scope} generation=1 holder=1 outcome=lesson_started",
                f"2026-08-31 10:00:09 Google Live evidence_receive_loop_stopped {scope} generation=1",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_scoped_receive_and_handoff_invariants_fail_for_target(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        cases = {
            "overlap": [
                f"Google Live evidence_receive_loop_started {scope} generation=1",
                f"Google Live evidence_receive_loop_started {scope} generation=2",
            ],
            "timeout": [
                f"Google Live evidence_receive_timeout {scope} generation=1",
            ],
            "handoff": [
                f"Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
            ],
        }
        expected = {
            "overlap": "RECEIVE_LOOP_OVERLAP",
            "timeout": "UNRECOVERED_TIMEOUT",
            "handoff": "UNRELEASED_LESSON_HANDOFF",
        }

        for name, markers in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(
                    _window_lines(
                        *(f"2026-08-31 10:00:{index:02d} {marker}" for index, marker in enumerate(markers, 1)),
                        journey_id="bargein-journey-1",
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )
                self.assertIn(
                    expected[name],
                    [item["code"] for item in verdict["failures"]],
                )

    def test_legacy_receive_timeout_and_handoff_do_not_poison_scoped_anchor(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live receive loop started",
                "2026-08-31 10:00:02 Google Live receive loop started",
                "2026-08-31 10:00:03 Google Live waiting_model_timeout timeout_sec=5",
                "2026-08-31 10:00:04 lesson_start_handoff_acquired lease=(1,1) reason=x",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_scoped_receive_timeout_and_handoff_reject_same_journey_wrong_scope(self):
        wrong_scope = (
            "journey_id=bargein-journey-1 connection_id=conn-wrong "
            "live_connection_id=live-wrong"
        )
        markers = [
            f"Google Live evidence_receive_loop_started {wrong_scope} generation=1",
            f"Google Live evidence_receive_timeout {wrong_scope} generation=1",
            f"Google Live evidence_receive_timeout_outcome {wrong_scope} generation=1 outcome=handled",
            f"Google Live evidence_lesson_handoff_acquired {wrong_scope} generation=1 holder=1 reason=lesson_start",
            f"Google Live evidence_lesson_handoff_released {wrong_scope} generation=1 holder=1 outcome=lesson_started",
        ]

        for marker in markers:
            with self.subTest(marker=marker):
                verdict = self._analyze(
                    _window_lines(
                        f"2026-08-31 10:00:01 {marker}",
                        journey_id="bargein-journey-1",
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )
                self.assertIn(
                    "EVIDENCE_SCOPE_MISMATCH",
                    [item["code"] for item in verdict["failures"]],
                )

    def test_foreign_scoped_terminal_markers_cannot_satisfy_target_state(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        foreign = "journey_id=other connection_id=conn-2 live_connection_id=live-2"
        cases = {
            "timeout": (
                f"Google Live evidence_receive_timeout {scope} generation=1",
                f"Google Live evidence_receive_timeout_outcome {foreign} generation=1 outcome=handled",
                "UNRECOVERED_TIMEOUT",
            ),
            "handoff": (
                f"Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
                f"Google Live evidence_lesson_handoff_released {foreign} generation=1 holder=1 outcome=lesson_started",
                "UNRELEASED_LESSON_HANDOFF",
            ),
        }

        for name, (start, terminal, expected) in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(
                    _window_lines(
                        f"2026-08-31 10:00:01 {start}",
                        f"2026-08-31 10:00:02 {terminal}",
                        journey_id="bargein-journey-1",
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )
                self.assertIn(
                    expected,
                    [item["code"] for item in verdict["failures"]],
                )

    def test_scoped_failed_timeout_and_handoff_outcomes_fail(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        cases = {
            "timeout": (
                f"Google Live evidence_receive_timeout {scope} generation=1",
                f"Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=failed",
                "RECEIVE_TIMEOUT_RECOVERY_FAILED",
            ),
            "handoff": (
                f"Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
                f"Google Live evidence_lesson_handoff_failed {scope} generation=1 holder=1 outcome=stale_release",
                "LESSON_HANDOFF_FAILED",
            ),
        }

        for name, (start, terminal, expected) in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(
                    _window_lines(
                        f"2026-08-31 10:00:01 {start}",
                        f"2026-08-31 10:00:02 {terminal}",
                        journey_id="bargein-journey-1",
                        evidence_scope=EVIDENCE_SCOPE,
                    )
                )
                self.assertIn(
                    expected,
                    [item["code"] for item in verdict["failures"]],
                )

    def test_scoped_unhandled_timeout_is_not_recovery(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:02 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=unhandled",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "RECEIVE_TIMEOUT_UNHANDLED",
            [item["code"] for item in verdict["failures"]],
        )

    def test_each_scoped_timeout_requires_its_own_handled_outcome(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:02 Google Live evidence_receive_timeout {scope} generation=1",
                f"2026-08-31 10:00:03 Google Live evidence_receive_timeout_outcome {scope} generation=1 outcome=handled",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(
            [item["code"] for item in verdict["failures"]].count(
                "UNRECOVERED_TIMEOUT"
            ),
            1,
        )

    def test_scoped_handoff_tracks_each_coalesced_holder(self):
        scope = "journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=1 reason=lesson_start",
                f"2026-08-31 10:00:02 Google Live evidence_lesson_handoff_acquired {scope} generation=1 holder=2 reason=protected_nudge",
                f"2026-08-31 10:00:03 Google Live evidence_lesson_handoff_released {scope} generation=1 holder=1 outcome=lesson_started",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "UNRELEASED_LESSON_HANDOFF",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_lesson_progress_must_follow_ping_for_same_step(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live firmware_ping "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 lesson_step=step-a",
                "2026-08-31 10:00:02 Google Live lesson_step_progress "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 step_id=step-b",
                journey_id="bargein-journey-1",
                journeys="lesson",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(verdict["status"], "FAIL", verdict)
        self.assertIn(
            "LESSON_PING_WITHOUT_PROGRESS",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_lesson_progress_before_ping_does_not_satisfy_liveness(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live lesson_step_progress "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 step_id=step-a",
                "2026-08-31 10:00:02 Google Live firmware_ping "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 lesson_step=step-a",
                journey_id="bargein-journey-1",
                journeys="lesson",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "LESSON_PING_WITHOUT_PROGRESS",
            [item["code"] for item in verdict["failures"]],
        )

    def test_scoped_lesson_tracks_multiple_steps_independently(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:00 Google Live evidence_receive_loop_started "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 generation=1",
                "2026-08-31 10:00:01 Google Live firmware_ping "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 lesson_step=step-a",
                "2026-08-31 10:00:02 Google Live firmware_ping "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 lesson_step=step-b",
                "2026-08-31 10:00:03 Google Live lesson_step_progress "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 step_id=step-b",
                "2026-08-31 10:00:04 Google Live lesson_step_progress "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 step_id=step-a",
                "2026-08-31 10:00:05 Google Live evidence_receive_loop_stopped "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-1 generation=1",
                journey_id="bargein-journey-1",
                journeys="lesson",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)

    def test_later_response_after_replacement_completion_is_not_stale(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:00 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7",
                "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:02 Google Live interrupt_output_stopped journey_id=j1 connection_id=c1 live_connection_id=l1 cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:03 Google Live evidence_user_interrupted journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                "2026-08-31 10:00:04 Google Live evidence_stale_model_drop journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=7 current_response_id=8",
                "2026-08-31 10:00:05 Google Live evidence_interrupt_audio_replayed journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:06 Google Live evidence_interrupt_input_finalized journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:07 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:08 Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:09 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=8",
                "2026-08-31 10:00:10 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=9",
                "2026-08-31 10:00:11 Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=9",
                "2026-08-31 10:00:12 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=9",
                journey_id="j1",
            )
        )

        self.assertEqual(verdict["status"], "PASS", verdict)
        self.assertEqual(verdict["staleAudioAfterReplacement"], 0)

    def test_scoped_reconnect_requires_one_terminal_and_replay_then_failure_fails(self):
        unfinished = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 reason=network",
                "2026-08-31 10:00:02 Google Live evidence_reopen_ready journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                journey_id="j1",
            )
        )
        replay_failed = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 reason=network",
                "2026-08-31 10:00:02 Google Live evidence_reopen_ready journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:03 Google Live evidence_replayed_buffered_audio journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:04 Google Live evidence_reconnect_failed journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 error_class=network",
                journey_id="j1",
            )
        )

        self.assertIn("RECONNECT_ATTEMPT_UNFINISHED", [item["code"] for item in unfinished["failures"]])
        self.assertIn("REOPEN_FAILED_AFTER_BUFFER_REPLAY", [item["code"] for item in replay_failed["failures"]])

    def test_scoped_reconnect_requires_exact_anchor_live_id(self):
        wrong_live = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "live_connection_id=live-2 attempt=1 reason=network",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )
        missing_live = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started "
                "journey_id=bargein-journey-1 connection_id=conn-1 "
                "attempt=1 reason=network",
                journey_id="bargein-journey-1",
                evidence_scope=EVIDENCE_SCOPE,
            )
        )

        self.assertIn(
            "EVIDENCE_SCOPE_MISMATCH",
            [item["code"] for item in wrong_live["failures"]],
        )
        self.assertIn(
            "MALFORMED_RELIABILITY_LOG_LINE",
            [item["code"] for item in missing_live["failures"]],
        )

    def test_scoped_reconnect_rejects_replay_after_terminal(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1 reason=network",
                "2026-08-31 10:00:02 Google Live evidence_reopen_ready journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                "2026-08-31 10:00:03 Google Live evidence_reconnect_succeeded journey_id=j1 connection_id=c1 live_connection_id=l1 attempt=1",
                "2026-08-31 10:00:04 Google Live evidence_replayed_buffered_audio journey_id=j1 connection_id=c1 attempt=1 live_connection_id=l1",
                journey_id="j1",
            )
        )
        self.assertIn("RECONNECT_MARKER_AFTER_TERMINAL", [item["code"] for item in verdict["failures"]])

    def test_scoped_interrupt_requires_cancelled_response_to_be_active(self):
        verdict = self._analyze(
            _window_lines(
                "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=j1 connection_id=c1 live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8",
                journey_id="j1",
            )
        )
        self.assertIn("INTERRUPT_WITHOUT_ACTIVE_RESPONSE", [item["code"] for item in verdict["failures"]])

    def test_scoped_response_lifecycle_requires_single_balanced_owner(self):
        cases = {
            "end_without_start": (
                ["2026-08-31 10:00:01 Google Live evidence_response_ended journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1"],
                "RESPONSE_END_WITHOUT_START",
            ),
            "start_without_end": (
                ["2026-08-31 10:00:01 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1"],
                "RESPONSE_START_WITHOUT_END",
            ),
            "overlap": (
                [
                    "2026-08-31 10:00:01 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1",
                    "2026-08-31 10:00:02 Google Live evidence_response_started journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=2",
                ],
                "RESPONSE_OVERLAP",
            ),
        }
        for name, (body, code) in cases.items():
            with self.subTest(name=name):
                verdict = self._analyze(_window_lines(*body, journey_id="j1"))
                self.assertEqual(verdict["status"], "FAIL", verdict)
                self.assertIn(code, [item["code"] for item in verdict["failures"]])

    def test_reports_never_copy_credentials_transcripts_raw_audio_or_exception_text(self):
        secret = "super-secret-google-key"
        verdict = self._analyze(
            _window_lines(
                f"2026-08-31 10:00:01 Authorization: Bearer {secret}",
                f"2026-08-31 10:00:02 transcript source=user text={secret}",
                f"2026-08-31 10:00:03 raw_audio={secret}",
                f"2026-08-31 10:00:04 Traceback RuntimeError({secret})",
            )
        )

        encoded = json.dumps(verdict)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("Bearer", encoded)
        self.assertNotIn("raw_audio", encoded)
        self.assertIn("Traceback", verdict["fatalHits"])

    def test_cli_check_reliability_embeds_json_and_exits_nonzero_on_failure(self):
        tmp, path = _write_log(
            _window_lines("2026-08-31 10:00:01 Google Live receive loop started")
        )
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "report.json"
        stdout = io.StringIO()
        argv = [
            "analyze_google_live_log.py",
            "--log",
            str(path),
            "--out-json",
            str(out),
            "--check-reliability",
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                analyze_google_live_log.main()

        self.assertEqual(raised.exception.code, 1)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(report["reliability"]["status"], "FAIL")

    def test_cli_reliability_json_does_not_leak_legacy_exception_detail(self):
        secret = "private-token-in-exception"
        tmp, path = _write_log(
            _window_lines(
                "2026-08-31 10:00:01 Google Live receive loop started",
                f"2026-08-31 10:00:02 Audio send loop exception {secret}",
                "2026-08-31 10:00:03 Google Live receive loop stopped",
            )
        )
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "report.json"
        argv = [
            "analyze_google_live_log.py",
            "--log",
            str(path),
            "--out-json",
            str(out),
            "--check-reliability",
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            analyze_google_live_log.main()

        self.assertNotIn(secret, out.read_text(encoding="utf-8"))

    def test_cli_correlates_task4_transport_with_exact_raw_log_window(self):
        body = [
            "2026-08-31 10:00:00 Google Live evidence_receive_loop_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 generation=1",
            "2026-08-31 10:00:00 Google Live evidence_response_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=7",
            "2026-08-31 10:00:01 Google Live user_interrupt_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:02 Google Live tts_state_stop_sent response_id=7 reason=interrupt",
            "2026-08-31 10:00:02 Google Live interrupt_output_stopped journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:03 Google Live user_interrupted reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:03 Google Live evidence_user_interrupted journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 reason=vad cancelled_response_id=7 next_response_id=8",
            "2026-08-31 10:00:04 Google Live stale_model_event_dropped type=audio reason=blocked response_id=7 current_response_id=8",
            "2026-08-31 10:00:04 Google Live evidence_stale_model_drop journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=7 current_response_id=8",
            "2026-08-31 10:00:05 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=1 bytes=10 response_id=8",
            "2026-08-31 10:00:05 Google Live evidence_interrupt_audio_replayed journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:06 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=100 response_id=8 frames=1 bytes=10 peak_rms=1000",
            "2026-08-31 10:00:06 Google Live evidence_interrupt_input_finalized journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:07 Google Live evidence_response_started journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:08 Google Live model_audio_start_hold_input response_id=8",
            "2026-08-31 10:00:09 Google Live model_output_chunk_forwarded journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:10 Google Live model_audio_end_ready_to_listen response_id=8",
            "2026-08-31 10:00:10 Google Live evidence_response_ended journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 response_id=8",
            "2026-08-31 10:00:11 Google Live evidence_receive_loop_stopped journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 generation=1",
            "2026-08-31 10:00:11 Google Live evidence_connection_close journey_id=bargein-journey-1 connection_id=conn-1 live_connection_id=live-1 pending_tasks=0 close_code=1000 reason=evidence_finalize",
        ]
        body = [line.replace("2026-08-31 10:", "2026-08-31 18:") for line in body]
        tmp, log_path = _write_log(body)
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport = _transport_observation(
            logWindow={
                "windowId": "bargein-journey-1",
                "start": "2026-08-31T10:00:00+00:00",
                "end": "2026-08-31T10:00:59+00:00",
            }
        )
        transport_path.write_text(json.dumps(transport), encoding="utf-8")
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py",
            "--log",
            str(log_path),
            "--correlate-transport",
            str(transport_path),
            "--expected-candidate-json",
            str(candidate_path),
            "--log-timezone",
            "Asia/Shanghai",
            "--out-json",
            str(out_path),
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            analyze_google_live_log.main()

        report = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["journeyId"], "bargein-journey-1")

    def test_cli_correlation_rejects_missing_journey_identity_or_window(self):
        tmp, log_path = _write_log([])
        self.addCleanup(tmp.cleanup)
        candidate_path = Path(tmp.name) / "candidate.json"
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        for field in ("journeyId", "candidateIdentity", "logWindow"):
            with self.subTest(field=field):
                transport = _transport_observation()
                transport.pop(field)
                transport_path = Path(tmp.name) / f"transport-{field}.json"
                out_path = Path(tmp.name) / f"combined-{field}.json"
                transport_path.write_text(json.dumps(transport), encoding="utf-8")
                argv = [
                    "analyze_google_live_log.py",
                    "--log",
                    str(log_path),
                    "--correlate-transport",
                    str(transport_path),
                    "--expected-candidate-json",
                    str(candidate_path),
                    "--out-json",
                    str(out_path),
                ]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        analyze_google_live_log.main()

    def test_cli_correlation_rejects_non_object_transport_json_redacted(self):
        secret = "secret-non-object-transport"
        cases = {
            "array": "[]",
            "null": "null",
            "string": json.dumps(secret),
            "number": "42",
        }

        for name, payload in cases.items():
            with self.subTest(name=name):
                tmp, log_path = _write_log([])
                self.addCleanup(tmp.cleanup)
                transport_path = Path(tmp.name) / "transport.json"
                candidate_path = Path(tmp.name) / "candidate.json"
                out_path = Path(tmp.name) / "combined.json"
                transport_path.write_text(payload, encoding="utf-8")
                candidate_path.write_text(
                    json.dumps(CANDIDATE_IDENTITY), encoding="utf-8"
                )
                argv = [
                    "analyze_google_live_log.py", "--log", str(log_path),
                    "--correlate-transport", str(transport_path),
                    "--expected-candidate-json", str(candidate_path),
                    "--out-json", str(out_path),
                ]

                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        analyze_google_live_log.main()

                self.assertEqual(raised.exception.code, 1)
                encoded = out_path.read_text(encoding="utf-8")
                report = json.loads(encoded)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn(
                    "EVIDENCE_JSON_INVALID",
                    [item["code"] for item in report["failures"]],
                )
                self.assertNotIn(secret, encoded)

    def test_cli_correlation_rejects_non_object_candidate_json_redacted(self):
        secret = "secret-non-object-candidate"
        tmp, log_path = _write_log([])
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport_path.write_text(
            json.dumps(_transport_observation()), encoding="utf-8"
        )
        candidate_path.write_text(json.dumps(secret), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py", "--log", str(log_path),
            "--correlate-transport", str(transport_path),
            "--expected-candidate-json", str(candidate_path),
            "--out-json", str(out_path),
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                analyze_google_live_log.main()

        self.assertEqual(raised.exception.code, 1)
        encoded = out_path.read_text(encoding="utf-8")
        report = json.loads(encoded)
        self.assertEqual(report["status"], "FAIL")
        self.assertNotIn(secret, encoded)

    def test_cli_correlation_fails_redacted_on_malformed_reliability_line(self):
        secret = "secret-token-value"
        tmp, log_path = _write_log(
            [f"not-a-timestamp Google Live evidence_response_started {secret}"]
        )
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport_path.write_text(
            json.dumps(
                _transport_observation(
                    logWindow={
                        "windowId": "bargein-journey-1",
                        "start": "2026-08-31T10:00:00+00:00",
                        "end": "2026-08-31T10:00:59+00:00",
                    }
                )
            ),
            encoding="utf-8",
        )
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py",
            "--log",
            str(log_path),
            "--correlate-transport",
            str(transport_path),
            "--expected-candidate-json",
            str(candidate_path),
            "--out-json",
            str(out_path),
        ]

        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                analyze_google_live_log.main()

        encoded = out_path.read_text(encoding="utf-8")
        report = json.loads(encoded)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("MALFORMED_BOUNDED_LOG_MARKER", [item["code"] for item in report["failures"]])
        self.assertNotIn(secret, encoded)

    def test_cli_correlation_rejects_timestamped_malformed_evidence(self):
        secret = "secret-token-value"
        tmp, log_path = _write_log(
            [f"2026-08-31 10:00:05 Google Live evidence_response_started {secret}"]
        )
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport_path.write_text(
            json.dumps(
                _transport_observation(
                    logWindow={
                        "windowId": "bargein-journey-1",
                        "start": "2026-08-31T10:00:00+00:00",
                        "end": "2026-08-31T10:00:59+00:00",
                    }
                )
            ),
            encoding="utf-8",
        )
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py", "--log", str(log_path),
            "--correlate-transport", str(transport_path),
            "--expected-candidate-json", str(candidate_path),
            "--out-json", str(out_path),
        ]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                analyze_google_live_log.main()
        encoded = out_path.read_text(encoding="utf-8")
        self.assertIn("MALFORMED_BOUNDED_LOG_MARKER", encoded)
        self.assertNotIn(secret, encoded)

    def test_cli_correlation_ignores_timestamped_malformed_evidence_outside_window(self):
        tmp, log_path = _write_log(
            [
                "2026-08-30 10:00:05 Google Live evidence_response_started unrelated",
                "2026-08-31 10:00:00 unrelated in-window noise",
            ]
        )
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport = _transport_observation(
            logWindow={
                "windowId": "bargein-journey-1",
                "start": "2026-08-31T10:00:00+00:00",
                "end": "2026-08-31T10:00:59+00:00",
            }
        )
        transport_path.write_text(json.dumps(transport), encoding="utf-8")
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py", "--log", str(log_path),
            "--correlate-transport", str(transport_path),
            "--expected-candidate-json", str(candidate_path),
            "--out-json", str(out_path),
        ]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                analyze_google_live_log.main()
        failures = json.loads(out_path.read_text(encoding="utf-8"))["failures"]
        self.assertNotIn("MALFORMED_BOUNDED_LOG_MARKER", [item["code"] for item in failures])

    def test_cli_correlation_rejects_valid_marker_with_trailing_junk(self):
        secret = "secret-trailing-junk"
        tmp, log_path = _write_log(
            [
                "2026-08-31 10:00:05 Google Live evidence_response_started "
                "journey_id=j1 connection_id=c1 live_connection_id=l1 response_id=1 "
                + secret
            ]
        )
        self.addCleanup(tmp.cleanup)
        transport_path = Path(tmp.name) / "transport.json"
        candidate_path = Path(tmp.name) / "candidate.json"
        out_path = Path(tmp.name) / "combined.json"
        transport_path.write_text(
            json.dumps(
                _transport_observation(
                    logWindow={
                        "windowId": "bargein-journey-1",
                        "start": "2026-08-31T10:00:00+00:00",
                        "end": "2026-08-31T10:00:59+00:00",
                    }
                )
            ),
            encoding="utf-8",
        )
        candidate_path.write_text(json.dumps(CANDIDATE_IDENTITY), encoding="utf-8")
        argv = [
            "analyze_google_live_log.py", "--log", str(log_path),
            "--correlate-transport", str(transport_path),
            "--expected-candidate-json", str(candidate_path),
            "--out-json", str(out_path),
        ]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                analyze_google_live_log.main()
        encoded = out_path.read_text(encoding="utf-8")
        self.assertIn("MALFORMED_BOUNDED_LOG_MARKER", encoded)
        self.assertNotIn(secret, encoded)

    def test_cli_correlation_rejects_malformed_scoped_marker_families_redacted(self):
        secret = "secret-scoped-marker-junk"
        cases = {
            "interrupt_started": (
                "Google Live user_interrupt_started journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 reason=vad cancelled_response_id=7 next_response_id=8"
            ),
            "interrupt_stopped": (
                "Google Live interrupt_output_stopped journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 cancelled_response_id=7 next_response_id=8"
            ),
            "forwarded": (
                "Google Live model_output_chunk_forwarded journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 response_id=8"
            ),
            "response": (
                "Google Live evidence_response_started journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 response_id=8"
            ),
            "reconnect": (
                "Google Live evidence_reconnect_started journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 attempt=1 reason=network"
            ),
            "close": (
                "Google Live evidence_connection_close journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 pending_tasks=0 close_code=1000 "
                "reason=evidence_finalize"
            ),
            "lesson": (
                "Google Live lesson_step_progress journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 step_id=step-1"
            ),
            "ping": (
                "Google Live firmware_ping journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 lesson_step=step-1"
            ),
            "interrupt_started_partial": (
                "Google Live user_interrupt_started journey_id=j1"
            ),
            "forwarded_partial": (
                "Google Live model_output_chunk_forwarded journey_id=j1"
            ),
            "response_partial": "Google Live evidence_response_started journey_id=j1",
            "reconnect_partial": "Google Live evidence_reconnect_started journey_id=j1",
            "close_partial": "Google Live evidence_connection_close journey_id=j1",
            "lesson_partial": "Google Live lesson_step_progress journey_id=j1",
            "ping_partial": "Google Live firmware_ping journey_id=j1",
            "forwarded_double_space": (
                "Google Live model_output_chunk_forwarded  journey_id=j1 "
                "connection_id=c1 live_connection_id=l1 response_id=8"
            ),
            "lesson_tab": (
                "Google Live lesson_step_progress\tjourney_id=j1 connection_id=c1 "
                "live_connection_id=l1 step_id=step-1"
            ),
            "ping_double_space": (
                "Google Live firmware_ping  journey_id=j1 connection_id=c1 "
                "live_connection_id=l1 lesson_step=step-1"
            ),
            "interrupt_bare": "Google Live user_interrupt_started",
            "close_bare": "Google Live evidence_connection_close",
        }

        for name, marker in cases.items():
            with self.subTest(name=name):
                tmp, log_path = _write_log(
                    [f"2026-08-31 10:00:05 {marker} {secret}"]
                )
                self.addCleanup(tmp.cleanup)
                transport_path = Path(tmp.name) / "transport.json"
                candidate_path = Path(tmp.name) / "candidate.json"
                out_path = Path(tmp.name) / "combined.json"
                transport_path.write_text(
                    json.dumps(
                        _transport_observation(
                            logWindow={
                                "windowId": "bargein-journey-1",
                                "start": "2026-08-31T10:00:00+00:00",
                                "end": "2026-08-31T10:00:59+00:00",
                            }
                        )
                    ),
                    encoding="utf-8",
                )
                candidate_path.write_text(
                    json.dumps(CANDIDATE_IDENTITY), encoding="utf-8"
                )
                argv = [
                    "analyze_google_live_log.py", "--log", str(log_path),
                    "--correlate-transport", str(transport_path),
                    "--expected-candidate-json", str(candidate_path),
                    "--out-json", str(out_path),
                ]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        analyze_google_live_log.main()
                encoded = out_path.read_text(encoding="utf-8")
                report = json.loads(encoded)
                self.assertIn(
                    "MALFORMED_BOUNDED_LOG_MARKER",
                    [item["code"] for item in report["failures"]],
                )
                self.assertNotIn(secret, encoded)


class AnalyzeGoogleLiveLogTest(unittest.TestCase):
    def test_interruptibility_events_are_counted(self):
        log = "\n".join(
            [
                "2026-05-20 14:00:00 Google Live receive loop started",
                "2026-05-20 14:00:01 Google Live audio_start",
                "2026-05-20 14:00:01 Google Live echo_suppressed reason=robot_speaking bytes=1920 rms=300",
                "2026-05-20 14:00:01 Google Live aec_live_vad_forward reason=robot_speaking bytes=1920 rms=280",
                "2026-05-20 14:00:02 Google Live echo_bypass reason=robot_speaking bytes=1920 rms=2600",
                "2026-05-20 14:00:02 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
                "2026-05-20 14:00:02 Google Live stale_model_event_dropped type=transcript reason=blocked_until_user_turn response_id=0 current_response_id=1",
                "2026-05-20 14:00:02 Google Live model_output_still_blocked_waiting_user_turn after 1500 ms",
                "2026-05-20 14:00:03 Google Live clean_user_turn_opened reason=audio_input response_id=1",
                "2026-05-20 14:00:03 Google Live replayed_interrupt_audio reason=model_output_unblocked frames=2 bytes=3840 response_id=1",
                "2026-05-20 14:00:04 Google Live interrupt_input_finalized reason=speech_tail elapsed_ms=420 response_id=1 frames=4 bytes=7680 peak_rms=2600",
                "2026-05-20 14:00:03 Google Live music_control_intent tool=stop_music text_preview='tắt nhạc'",
                "2026-05-20 14:00:04 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["echo_suppressed"], 1)
        self.assertEqual(totals["aec_live_vad_forward"], 1)
        self.assertEqual(totals["echo_bypass"], 1)
        self.assertEqual(totals["stale_model_event_dropped"], 1)
        self.assertEqual(totals["model_output_still_blocked_waiting_user_turn"], 1)
        self.assertEqual(totals["clean_user_turn_opened"], 1)
        self.assertEqual(totals["replayed_interrupt_audio"], 1)
        self.assertEqual(totals["interrupt_input_finalized"], 1)
        self.assertEqual(totals["music_control_intents"], 1)
        self.assertEqual(report["aec_live_vad_forward_rms"], {"count": 1, "min": 280, "max": 280, "mean": 280, "median": 280, "p95": 280, "p99": 280})
        self.assertEqual(report["echo_bypass_rms"], {"count": 1, "min": 2600, "max": 2600, "mean": 2600, "median": 2600, "p95": 2600, "p99": 2600})
        self.assertEqual(report["interrupt_reason_distribution"], {"loud_input": 1})

    def test_production_audio_gateway_markers_are_counted(self):
        log = "\n".join(
            [
                "2026-05-28 10:00:00 Google Live receive loop started",
                "2026-05-28 10:00:01 audio_decision decision=suppress_echo reason=robot_speaking state=MODEL_SPEAKING turn_id=2 response_id=3 audio_seq=9",
                "2026-05-28 10:00:01 interrupt_started reason=wake state=INTERRUPTING turn_id=3 response_id=4",
                "2026-05-28 10:00:01 output_queue_cleared reason=interrupt response_id=3",
                "2026-05-28 10:00:02 reconnect_started reason=session_expiring attempt=1 state=RECONNECTING",
                "2026-05-28 10:00:03 reconnect_succeeded attempt=1 live_connection_id=live-2",
                "2026-05-28 10:00:04 reconnect_failed attempt=2 error_class=network",
                "2026-05-28 10:00:05 fallback_triggered reason=auth",
                "2026-05-28 10:00:05 Google Live fallback_disabled reason=quota exceeded 429",
                "2026-05-28 10:00:06 music_state_changed state=paused trigger=user_interrupt",
                "2026-05-28 10:00:06 audio_output_transport_closed reason=normal_close detail=received 1000 OK",
                "2026-05-28 10:00:07 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["audio_decision"], 1)
        self.assertEqual(totals["interrupt_started"], 1)
        self.assertEqual(totals["output_queue_cleared"], 1)
        self.assertEqual(totals["reconnect_started"], 1)
        self.assertEqual(totals["reconnect_succeeded"], 1)
        self.assertEqual(totals["reconnect_failed"], 1)
        self.assertEqual(totals["fallback_disabled_sessions"], 1)
        self.assertEqual(totals["music_state_changed"], 1)
        self.assertEqual(totals["audio_output_transport_closed"], 1)

    def test_lesson_local_tts_marker_is_counted(self):
        log = "\n".join(
            [
                "2026-05-28 10:00:00 Google Live receive loop started",
                "2026-05-28 10:00:01 Google Live lesson_step_prompt queued via tts text='Xin chào.'",
                "2026-05-28 10:00:01 Google Live lesson_start_ack queued via tts text='Bắt đầu bài học nhé.'",
                "2026-05-28 10:00:02 Google Live lesson_step_prompt sent via live text chars=8 sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "2026-05-28 10:00:03 Google Live receive loop stopped",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            path.write_text(log, encoding="utf-8")

            report = analyze(path)

        totals = report["totals"]
        self.assertEqual(totals["lesson_prompt_local_tts"], 2)
        self.assertEqual(totals["lesson_prompt_live_text"], 1)


class SummarizePainsTest(unittest.TestCase):
    def _make_log(self, lines: list[str]) -> Path:
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / "server.log"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def test_returns_five_pain_keys(self):
        path = self._make_log(["2026-05-20 10:00:00 nothing here"])
        result = summarize_pains(path)
        self.assertIn("P1_user_speech_lost", result)
        self.assertIn("P2_stop_latency", result)
        self.assertIn("P3_response_overlap", result)
        self.assertIn("P4_function_calls", result)
        self.assertIn("P5_music_ducking", result)

    def test_p1_counts_interrupts_and_transcripts(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
            "2026-05-20 10:00:01 Google Live user_interrupted reason=vad cancelled_response_id=1 next_response_id=2",
            "2026-05-20 10:00:02 Google Live live_transcript_recv chars=12 source=user",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["interrupts_initiated"], 2)
        self.assertEqual(p1["transcripts_received"], 1)
        self.assertAlmostEqual(p1["transcript_loss_rate"], 0.5)

    def test_p1_zero_loss_rate_when_no_interrupts(self):
        path = self._make_log(["2026-05-20 10:00:00 nothing"])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertIsNone(p1["transcript_loss_rate"])

    def test_p1_buffer_appends_and_replay_skipped(self):
        path = self._make_log([
            "2026-05-20 10:00:00 user_speech_pending_replay frames=10 bytes=3200",
            "2026-05-20 10:00:01 user_speech_pending_replay frames=5 bytes=1600",
            "2026-05-20 10:00:02 replay_skipped reason=response_id_mismatch",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["buffer_appends"], 2)
        self.assertEqual(p1["replay_skipped_by_reason"], {"response_id_mismatch": 1})

    def test_p1_capture_finalized_tracks_zero_frames(self):
        path = self._make_log([
            "2026-05-20 10:00:00 interrupt_capture_finalized frames=0 duration_ms=300",
            "2026-05-20 10:00:01 interrupt_capture_finalized frames=8 duration_ms=400",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["capture_finalized_count"], 2)
        self.assertEqual(p1["capture_finalized_with_zero_frames"], 1)

    def test_p1_ignores_transcript_with_zero_chars(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live live_transcript_recv chars=0 source=user",
            "2026-05-20 10:00:01 Google Live live_transcript_recv chars=5 source=user",
        ])
        p1 = summarize_pains(path)["P1_user_speech_lost"]
        self.assertEqual(p1["transcripts_received"], 1)

    def test_p2_stop_latency_computed_between_interrupt_and_tts_stop(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live user_interrupted reason=loud_input cancelled_response_id=0 next_response_id=1",
            "2026-05-20 10:00:01 Google Live tts_state_stop_sent",
        ])
        p2 = summarize_pains(path)["P2_stop_latency"]
        stats = p2["interrupt_to_tts_stop_sent_ms"]
        self.assertEqual(stats["count"], 1)
        self.assertAlmostEqual(stats["mean"], 1000.0)

    def test_p2_no_latency_when_no_matching_pair(self):
        path = self._make_log([
            "2026-05-20 10:00:00 Google Live tts_state_stop_sent",
        ])
        p2 = summarize_pains(path)["P2_stop_latency"]
        self.assertEqual(p2["interrupt_to_tts_stop_sent_ms"]["count"], 0)

    def test_p3_stale_chunks_counted(self):
        path = self._make_log([
            "2026-05-20 10:00:00 model_output_chunk_dropped reason=stale_response_id old=1 current=2",
            "2026-05-20 10:00:01 model_output_chunk_dropped reason=stale_response_id old=1 current=3",
            "2026-05-20 10:00:02 model_output_unblock_trigger source=audio_end",
        ])
        p3 = summarize_pains(path)["P3_response_overlap"]
        self.assertEqual(p3["stale_chunks_dropped"], 2)
        self.assertEqual(p3["model_output_unblock_triggers"], {"audio_end": 1})

    def test_p4_tool_dispatches_grouped_by_name(self):
        path = self._make_log([
            "2026-05-20 10:00:00 tool_call_dispatched name=get_weather response_id=1",
            "2026-05-20 10:00:01 tool_call_dispatched name=get_weather response_id=2",
            "2026-05-20 10:00:02 tool_call_dispatched name=play_music response_id=3",
        ])
        p4 = summarize_pains(path)["P4_function_calls"]
        self.assertEqual(p4["tool_call_dispatched_count"], 3)
        self.assertEqual(p4["by_name"], {"get_weather": 2, "play_music": 1})

    def test_p5_music_pause_grouped_by_trigger(self):
        path = self._make_log([
            "2026-05-20 10:00:00 music_auto_paused trigger=user_interrupt",
            "2026-05-20 10:00:01 music_auto_paused trigger=user_interrupt",
            "2026-05-20 10:00:02 music_auto_paused trigger=barge_in",
        ])
        p5 = summarize_pains(path)["P5_music_ducking"]
        self.assertEqual(p5["music_auto_pause_count"], 3)
        self.assertEqual(p5["by_trigger"], {"user_interrupt": 2, "barge_in": 1})

    def test_empty_log_returns_zeros(self):
        path = self._make_log([])
        result = summarize_pains(path)
        self.assertEqual(result["P1_user_speech_lost"]["interrupts_initiated"], 0)
        self.assertEqual(result["P3_response_overlap"]["stale_chunks_dropped"], 0)
        self.assertEqual(result["P4_function_calls"]["tool_call_dispatched_count"], 0)
        self.assertEqual(result["P5_music_ducking"]["music_auto_pause_count"], 0)


if __name__ == "__main__":
    unittest.main()
