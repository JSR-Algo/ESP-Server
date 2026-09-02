import asyncio
import io
import json
import wave
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts.google_live_robot_soak as robot_soak
from scripts.analyze_google_live_log import correlate_websocket_bargein_evidence
from scripts.google_live_robot_soak import (
    _build_argument_parser,
    _candidate_failure_report,
    _validate_candidate_args,
    build_candidate_journeys,
    produce_candidate_evidence,
    run_candidate_soak,
    run_soak,
)


def _write_pcm_wav(path, *, sample_rate=24000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\0\0" * 960)


def _protected_candidate_input(tmp_path):
    initial = tmp_path / "initial.wav"
    newest = tmp_path / "newest.wav"
    trigger = tmp_path / "trigger.wav"
    for path in (initial, newest, trigger):
        _write_pcm_wav(path)
    document = {
        "bargein": {
            "initialAudioPath": str(initial),
            "initialExpected": "PRIVATE INITIAL INTENT",
            "newestAudioPath": str(newest),
            "newestExpected": "PRIVATE NEWEST INTENT",
        },
        "robotSpeaking": {"triggerAudioPath": str(trigger)},
    }
    return io.BytesIO(json.dumps(document).encode()), document

IDENTITY = {
    "gitSha": "candidate-sha",
    "imageDigest": f"sha256:{'a' * 64}",
    "firmwareIdentity": "firmware-v1",
    "fixtureSha256": "b" * 64,
    "configFingerprint": "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
}
PEER_HASH = f"sha256:{'d' * 64}"
SOAK_CONNECTION_1 = "candidate-soak-websocket-1"
SOAK_CONNECTION_2 = "candidate-soak-websocket-2"
EVIDENCE_SCOPE = {
    "journeyId": "transport-journey",
    "connectionId": "connection-transport",
    "liveConnectionId": "live-1",
    "initialLiveConnectionId": "live-1",
    "peerIdentityHash": PEER_HASH,
    "serverStartUtc": "2026-08-31T10:00:00+00:00",
}
LOG_WINDOW = {
    "windowId": "transport-window",
    "start": EVIDENCE_SCOPE["serverStartUtc"],
    "end": "2026-08-31T10:01:00+00:00",
}


def _execution_window(sequence):
    start = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc) + timedelta(
        seconds=(sequence - 1) * 40
    )
    return {
        "windowId": f"window-{sequence}",
        "start": start.isoformat(),
        "end": (start + timedelta(seconds=40)).isoformat(),
    }


def _candidate_scope(journey_id, stage):
    return {
        "journeyId": journey_id,
        "connectionId": "connection-1",
        "liveConnectionId": "live-1",
        "initialLiveConnectionId": "live-1",
        "peerIdentityHash": PEER_HASH,
        "serverStartUtc": "2026-08-31T10:00:00+00:00",
        "journeyType": stage,
        "proofProfile": "candidate-lifecycle",
    }


def _candidate_finalize(scope):
    return {
        "type": "evidence_finalized",
        "status": "PASS",
        "evidenceScope": scope,
        "serverEndUtc": "2026-08-31T10:01:00+00:00",
        "finalLiveConnectionId": "live-1",
        "liveConnectionTransitions": [],
    }


def _refresh_execution_contract(result):
    result["evidenceScope"] = {
        "journeyId": result["journeyId"],
        "connectionId": result["connectionId"],
        "liveConnectionId": result["liveConnectionId"],
        "initialLiveConnectionId": result["initialLiveConnectionId"],
        "peerIdentityHash": result["peerIdentityHash"],
        "serverStartUtc": result["logWindow"]["start"],
        "journeyType": result["name"],
        "proofProfile": "candidate-lifecycle",
    }
    cancelled_id = result["evidenceSequence"] * 2 - 1
    replacement_id = result["evidenceSequence"] * 2
    result["task5LogEvidence"] = {
        "schemaVersion": "google-live-reliability.v1",
        "name": "google_live_log_reliability",
        "status": "PASS",
        "candidateIdentity": IDENTITY,
        "receiveLoopBalance": 0,
        "maxReceiveLoopsActive": 1,
        "staleAudioAfterReplacement": 0,
        "unrecoveredTimeouts": [],
        "unreleasedLessonHandoffs": [],
        "fatalHits": [],
        "failures": [],
        "journeyType": result["name"],
        "journeyLatencyEvidence": {},
        "duplicateResponseIds": [],
        "replayCountsByReopen": {},
        "correlation": {"status": "NOT_OBSERVED"},
        "correlations": [],
        "evidenceScope": deepcopy(result["evidenceScope"]),
        "initialLiveConnectionId": result["initialLiveConnectionId"],
        "finalLiveConnectionId": result["finalLiveConnectionId"],
        "liveConnectionTransitions": deepcopy(result["liveConnectionTransitions"]),
        "serverConnectionTransitions": [],
        "logWindow": deepcopy(result["logWindow"]),
    }
    if result["name"] in {"conversation", "conversation_after_lesson"}:
        result["task5LogEvidence"]["journeyLatencyEvidence"] = {
            "firstAudioMs": 1000.0
        }
    elif result["name"] in {"reopen", "reconnect"}:
        result["task5LogEvidence"]["journeyLatencyEvidence"] = {
            "reconnectRecoveryMs": 1000.0
        }
    elif result["name"] == "quiet":
        mode = result.get("quietMode")
        duration_ms = int(result.get("observationDurationSec", 40.0) * 1000)
        response_count = 0 if mode == "silence" else 1
        result["task5LogEvidence"]["candidateSemanticEvidence"] = {
            "status": "PASS",
            "kind": "quiet",
            "mode": mode,
            "durationMs": duration_ms,
            "responseGeneration": None if mode == "silence" else 1,
            "responseDurationMs": 0 if mode == "silence" else duration_ms,
            "outputChunks": response_count,
            "falseInterrupts": 0,
            "responseStarts": response_count,
            "responseEnds": response_count,
            "replacements": 0,
            "fallbacks": 0,
        }
    if result["name"] == "bargein":
        result["task5LogEvidence"]["candidateSemanticEvidence"] = {
            "status": "PASS",
            "kind": "bargein-intent",
            "initialSlotMatched": True,
            "newestSlotMatched": True,
            "orderingValid": True,
            "latestIntentMatched": True,
            "replacementOwnedByNewestGeneration": True,
        }
        result["task5LogEvidence"]["correlation"] = {
            "status": "PASS",
            "cancelledResponseId": cancelled_id,
            "replacementResponseId": replacement_id,
        }
        result["task5LogEvidence"]["correlations"] = [
            {
                "status": "PASS",
                "journeyId": result["journeyId"],
                "connectionId": result["connectionId"],
                "liveConnectionId": result["finalLiveConnectionId"],
                "cancelledResponseId": cancelled_id,
                "replacementResponseId": replacement_id,
            }
        ]
        result["task4TransportEvidence"] = {
            "schemaVersion": "google-live-reliability.v1",
            "name": "websocket_audio_bargein_transport",
            "status": "SKIPPED",
            "candidateIdentity": IDENTITY,
            "pendingCode": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "correlationSource": "server_log",
            "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "aggregateReleaseEligible": False,
            "interruptStopMarkerObserved": True,
            "replacementResponseStarted": True,
            "replacementResponseStopped": True,
            "replacementBinaryChunks": 2,
            "bargeinStopMs": 400.0,
            "maxServerOutputGapMs": 80.0,
            "journeyId": result["journeyId"],
            "evidenceScope": deepcopy(result["evidenceScope"]),
            "serverConnectionId": result["connectionId"],
            "liveConnectionId": result["liveConnectionId"],
            "peerIdentityHash": result["peerIdentityHash"],
            "initialLiveConnectionId": result["initialLiveConnectionId"],
            "finalLiveConnectionId": result["finalLiveConnectionId"],
            "liveConnectionTransitions": deepcopy(result["liveConnectionTransitions"]),
            "logWindow": deepcopy(result["logWindow"]),
        }
        result["task5CorrelatedEvidence"] = correlate_websocket_bargein_evidence(
            result["task4TransportEvidence"],
            result["task5LogEvidence"],
            expected_candidate_identity=IDENTITY,
        )
    return result


def _refresh_execution_sequence(executions):
    previous_scope = None
    for result in executions:
        _refresh_execution_contract(result)
        if result["name"] == "reconnect":
            result["task5LogEvidence"]["serverConnectionTransitions"] = [{
                "status": "PASS",
                "source": "server_log",
                "serverIssued": True,
                "sequence": 1,
                "reason": "same_device_reconnect",
                "fromJourneyId": previous_scope["journeyId"],
                "fromConnectionId": previous_scope["connectionId"],
                "toJourneyId": result["journeyId"],
                "toConnectionId": result["connectionId"],
                "peerIdentityHash": result["peerIdentityHash"],
            }]
        previous_scope = deepcopy(result["evidenceScope"])
    return executions


def _layer(name):
    return {
        "schemaVersion": "google-live-reliability.v1",
        "name": name,
        "status": "PASS",
        "candidateIdentity": IDENTITY,
    }


def _args(**overrides):
    values = {
        "candidate_git_sha": IDENTITY["gitSha"],
        "candidate_image_digest": IDENTITY["imageDigest"],
        "firmware_identity": IDENTITY["firmwareIdentity"],
        "fixture_sha256": IDENTITY["fixtureSha256"],
        "config_json": "{}",
        "minimum_turns": 30,
        "minimum_duration_sec": 1800.0,
        "evidence_gap_budget_sec": 10.0,
        "maximum_padding_windows": 60,
        "cleanup_timeout_sec": 0.05,
        "bargein_cycles": 10,
        "lesson_manifest": {"manifestId": "bounded-lesson-v1"},
        "report": Path("report.json"),
        "baseline_report": {
            "latencyMetrics": {
                "firstAudioP50Ms": 1000,
                "firstAudioP95Ms": 1500,
                "bargeinP95Ms": 400,
                "reconnectRecoveryP95Ms": 1000,
            }
        },
        "real_api_report": _layer("real_api"),
        "transport_report": {
            **_layer("websocket_audio_bargein_transport"),
            "status": "SKIPPED",
            "pendingCode": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "aggregateReleaseEligible": False,
            "correlationSource": "server_log",
            "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "interruptStopMarkerObserved": True,
            "replacementResponseStarted": True,
            "replacementResponseStopped": True,
            "replacementBinaryChunks": 2,
            "bargeinStopMs": 200.0,
            "maxServerOutputGapMs": 80.0,
            "journeyId": "transport-journey",
            "evidenceScope": EVIDENCE_SCOPE,
            "serverConnectionId": EVIDENCE_SCOPE["connectionId"],
            "liveConnectionId": EVIDENCE_SCOPE["liveConnectionId"],
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "peerIdentityHash": PEER_HASH,
            "logWindow": LOG_WINDOW,
        },
        "correlated_transport_report": {
            **_layer("websocket_audio_bargein_correlated"),
            "aggregateReleaseEligible": True,
            "correlationSource": "server_log",
            "correlationStatus": "PASS",
            "journeyId": "transport-journey",
            "evidenceScope": EVIDENCE_SCOPE,
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "logWindow": LOG_WINDOW,
        },
        "log_reliability_report": {
            **_layer("google_live_log_reliability"),
            "receiveLoopBalance": 0,
            "maxReceiveLoopsActive": 1,
            "staleAudioAfterReplacement": 0,
            "unrecoveredTimeouts": [],
            "unreleasedLessonHandoffs": [],
            "fatalHits": [],
            "failures": [],
            "duplicateResponseIds": [],
            "replayCountsByReopen": {},
            "correlation": {
                "status": "PASS",
                "cancelledResponseId": 7,
                "replacementResponseId": 8,
            },
            "correlations": [
                {
                    "status": "PASS",
                    "journeyId": "transport-journey",
                    "connectionId": EVIDENCE_SCOPE["connectionId"],
                    "liveConnectionId": "live-1",
                    "cancelledResponseId": 7,
                    "replacementResponseId": 8,
                }
            ],
            "evidenceScope": EVIDENCE_SCOPE,
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "logWindow": LOG_WINDOW,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _journeys(*, mutation=None):
    sequence = 0
    last_window = None
    previous_scope = None
    current_connection = SOAK_CONNECTION_1

    async def journey(_args, *, name, index, label=None, **_kwargs):
        nonlocal sequence, last_window, previous_scope, current_connection
        sequence += 1
        previous_connection = current_connection
        if name == "reconnect":
            current_connection = SOAK_CONNECTION_2
        result = {
            "schemaVersion": "google-live-reliability.v1",
            "name": name,
            "status": "PASS",
            "candidateIdentity": IDENTITY,
            "evidenceSequence": sequence,
            "journeyId": f"candidate-{sequence}",
            "connectionId": current_connection,
            "liveConnectionId": "live-1",
            "initialLiveConnectionId": "live-1",
            "finalLiveConnectionId": "live-1",
            "liveConnectionTransitions": [],
            "peerIdentityHash": PEER_HASH,
            "serverIssued": True,
            "windowId": f"window-{sequence}",
            "logWindow": _execution_window(sequence),
            "successfulTurns": 0 if name in {"quiet", "lesson"} else 1,
            "bargeins": 1 if name == "bargein" else 0,
            "latestIntentSuccesses": 1 if name == "bargein" else 0,
            "falseInterrupts": 0,
            "unexpectedFallbacks": 0,
            "latencies": {},
        }
        if name == "quiet":
            result["quietMode"] = "silence" if index == 1 else "robot_speaking"
            result["observationDurationSec"] = 40.0
        _refresh_execution_contract(result)
        if name in {"conversation", "conversation_after_lesson"}:
            result["latencies"] = {"firstAudioMs": [1000]}
        elif name == "bargein":
            result["latencies"] = {
                "bargeinStopMs": [400],
                "serverOutputGapMs": [80],
            }
            _refresh_execution_contract(result)
        elif name in {"reopen", "reconnect"}:
            result["latencies"] = {"reconnectRecoveryMs": [1000]}
            if name == "reconnect":
                result["task5LogEvidence"]["serverConnectionTransitions"] = [{
                    "status": "PASS",
                    "source": "server_log",
                    "serverIssued": True,
                    "sequence": 1,
                    "reason": "same_device_reconnect",
                    "fromJourneyId": previous_scope["journeyId"],
                    "fromConnectionId": previous_connection,
                    "toJourneyId": result["journeyId"],
                    "toConnectionId": current_connection,
                    "peerIdentityHash": PEER_HASH,
                }]
        elif name == "lesson":
            result["lessonManifestSha256"] = "sha256:006c27e334a18ca85cdaf3a6e8ff2718219aab2004caf8233417ea6b80fd5652"
        if mutation is not None:
            mutation(result, sequence, name, index, label)
        last_window = deepcopy(result["logWindow"])
        previous_scope = deepcopy(result.get("evidenceScope"))
        return result

    async def monitor(_args, *, duration_sec):
        start = datetime.fromisoformat(last_window["end"]) + timedelta(seconds=10)
        end = start + timedelta(seconds=duration_sec)
        return [
            {
                "schemaVersion": "google-live-reliability.v1",
                "name": "quiet_padding",
                "status": "PASS",
                "candidateIdentity": IDENTITY,
                "journeyId": "quiet-padding-1",
                "connectionId": SOAK_CONNECTION_2,
                "serverIssued": True,
                "windowId": "quiet-padding-window-1",
                "logWindow": {
                    "windowId": "quiet-padding-window-1",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "evidenceScope": {
                    "journeyId": "quiet-padding-1",
                    "connectionId": SOAK_CONNECTION_2,
                    "liveConnectionId": "quiet-padding-live-1",
                    "initialLiveConnectionId": "quiet-padding-live-1",
                    "peerIdentityHash": PEER_HASH,
                    "serverStartUtc": start.isoformat(),
                    "journeyType": "quiet_padding",
                    "proofProfile": "candidate-lifecycle",
                },
                "liveConnectionId": "quiet-padding-live-1",
                "initialLiveConnectionId": "quiet-padding-live-1",
                "finalLiveConnectionId": "quiet-padding-live-1",
                "liveConnectionTransitions": [],
                "peerIdentityHash": PEER_HASH,
                "durationSec": (end - start).total_seconds(),
                "falseInterrupts": 0,
                "unexpectedFallbacks": 0,
                "resourceVerdict": {"status": "PASS"},
                "task5LogEvidence": {
                    "schemaVersion": "google-live-reliability.v1",
                    "name": "google_live_log_reliability",
                    "status": "PASS",
                    "candidateIdentity": IDENTITY,
                    "journeyType": "quiet_padding",
                    "evidenceScope": {
                        "journeyId": "quiet-padding-1",
                        "connectionId": SOAK_CONNECTION_2,
                        "liveConnectionId": "quiet-padding-live-1",
                        "initialLiveConnectionId": "quiet-padding-live-1",
                        "peerIdentityHash": PEER_HASH,
                        "serverStartUtc": start.isoformat(),
                        "journeyType": "quiet_padding",
                        "proofProfile": "candidate-lifecycle",
                    },
                    "initialLiveConnectionId": "quiet-padding-live-1",
                    "finalLiveConnectionId": "quiet-padding-live-1",
                    "liveConnectionTransitions": [],
                    "serverConnectionTransitions": [],
                    "logWindow": {
                        "windowId": "quiet-padding-window-1",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                    "receiveLoopBalance": 0,
                    "maxReceiveLoopsActive": 1,
                    "replayCountsByReopen": {},
                    "duplicateResponseIds": [],
                    "staleAudioAfterReplacement": 0,
                    "unrecoveredTimeouts": [],
                    "unreleasedLessonHandoffs": [],
                    "fatalHits": [],
                    "correlation": {"status": "NOT_OBSERVED"},
                    "correlations": [],
                    "failures": [],
                },
            }
        ]

    async def cleanup(_args, *, final_scope):
        return {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_cleanup",
            "status": "PASS",
            "candidateIdentity": IDENTITY,
            "finalScope": final_scope,
            "serverAnchor": {
                "connectionId": SOAK_CONNECTION_2,
                "peerIdentityHash": PEER_HASH,
            },
            "websocketClosed": True,
            "providerFinalizeStatus": "PASS",
            "providerCloseStatus": "PASS",
            "pendingOwnedTasks": 0,
            "activeSessions": 0,
            "activeReceiveLoops": 0,
            "logStatus": "PASS",
            "resourceEndSampleRequired": True,
        }

    journeys = dict.fromkeys(
        ("conversation", "bargein", "quiet", "reopen", "reconnect", "lesson"),
        journey,
    )
    journeys["monitor"] = monitor
    journeys["cleanup"] = cleanup
    return journeys


def _samples():
    return {
        "rssBytes": 100,
        "fdCount": 3,
        "asyncioTaskCount": 2,
        "threadCount": 1,
    }


def _cleanup_evidence(final_scope):
    return {
        "schemaVersion": "google-live-reliability.v1",
        "name": "candidate_cleanup",
        "status": "PASS",
        "candidateIdentity": IDENTITY,
        "finalScope": final_scope,
        "serverAnchor": {
            "connectionId": SOAK_CONNECTION_2,
            "peerIdentityHash": PEER_HASH,
        },
        "websocketClosed": True,
        "providerFinalizeStatus": "PASS",
        "providerCloseStatus": "PASS",
        "pendingOwnedTasks": 0,
        "activeSessions": 0,
        "activeReceiveLoops": 0,
        "logStatus": "PASS",
        "resourceEndSampleRequired": True,
    }


def _cleanup_scope_from_execution(result):
    return {
        "journeyId": result["journeyId"],
        "connectionId": result["connectionId"],
        "windowId": result["windowId"],
        "serverEndUtc": result["logWindow"]["end"],
        "evidenceScope": deepcopy(result["evidenceScope"]),
        "logWindow": deepcopy(result["logWindow"]),
        "journeyType": result["name"],
        "proofProfile": "candidate-lifecycle",
        "initialLiveConnectionId": result["initialLiveConnectionId"],
        "finalLiveConnectionId": result["finalLiveConnectionId"],
        "liveConnectionTransitions": deepcopy(result["liveConnectionTransitions"]),
        "peerIdentityHash": result["peerIdentityHash"],
        "serverIssued": True,
    }


class _Clock:
    def __init__(self, values=(0.0, 1800.0, 1800.0)):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def _run(args=None, journeys=None, clock=None, samples=_samples):
    return asyncio.run(
        run_candidate_soak(
            args or _args(),
            journeys=journeys or _journeys(),
            sample_resources=samples,
            clock=clock or _Clock(),
        )
    )


def _args_with_transition():
    args = _args()
    transition = {
        "attempt": 1,
        "fromLiveConnectionId": "live-1",
        "toLiveConnectionId": "live-2",
        "reason": "receive_timeout",
    }
    for report in (
        args.transport_report,
        args.correlated_transport_report,
        args.log_reliability_report,
    ):
        report["finalLiveConnectionId"] = "live-2"
        report["liveConnectionTransitions"] = [deepcopy(transition)]
    args.log_reliability_report["correlations"][0]["liveConnectionId"] = "live-2"
    return args


def test_candidate_soak_runs_fixed_sequence_and_meets_production_budgets():
    report = _run()

    assert [stage["name"] for stage in report["stages"]] == [
        "conversation",
        "bargein",
        "quiet",
        "reopen",
        "reconnect",
        "lesson",
        "conversation_after_lesson",
    ]
    assert [stage["executions"] for stage in report["stages"]] == [17, 10, 2, 1, 1, 1, 1]
    assert report["totals"]["successfulTurns"] == 30
    assert report["totals"]["bargeins"] == 10
    assert report["totals"]["latestIntentSuccessRate"] == 1.0
    assert report["resourceVerdict"]["status"] == "PASS"
    assert report["latencyComparison"]["pass"] is True
    assert report["status"] == "PASS"
    assert report["durationSec"] == 1800.0
    assert report["replayCandidateEvidence"] is False
    assert report["quietPadding"][0]["durationSec"] == 480.0
    assert report["quietPadding"][0]["resourceVerdict"]["status"] == "PASS"
    assert report["candidateIdentity"] == IDENTITY
    assert len(report["evidenceExecutions"]) == 33
    assert len({item["journeyId"] for item in report["evidenceExecutions"]}) == 33
    assert all(item["evidenceScope"]["peerIdentityHash"] == PEER_HASH for item in report["evidenceExecutions"])
    assert all(item["connectionId"] == item["evidenceScope"]["connectionId"] for item in report["evidenceExecutions"])
    assert report["status"] == "PASS"  # Live IDs are transition-scoped, not globally unique.
    assert "raw child" not in json.dumps(report).lower()


def test_candidate_soak_rejects_client_mutation_below_exact_semantic_evidence():
    report = _run(
        journeys=_journeys(
            mutation=lambda result, _sequence, name, index, _label: (
                result.update(latestIntentSuccesses=0)
                if name == "bargein" and index in {9, 10}
                else None
            )
        )
    )

    assert report["status"] == "FAIL"
    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        failure["code"] for failure in report["failures"]
    }


@pytest.mark.parametrize(
    ("case", "mutate", "args", "sample", "expected"),
    [
        (
            "29 turns",
            lambda r, s, n, i, label: r.update(successfulTurns=0) if s == 1 else None,
            {},
            _samples,
            "MINIMUM_TURNS_NOT_MET",
        ),
        (
            "nine bargeins",
            lambda r, s, n, i, label: r.update(bargeins=0) if n == "bargein" and i == 9 else None,
            {},
            _samples,
            "MINIMUM_BARGEINS_NOT_MET",
        ),
        (
            "79 percent newest intent",
            lambda r, s, n, i, label: r.update(latestIntentSuccesses=0) if n == "bargein" and i in {8, 9, 10} else None,
            {},
            _samples,
            "LATEST_INTENT_RATE_BELOW_BUDGET",
        ),
        (
            "false interrupt",
            lambda r, s, n, i, label: r.update(falseInterrupts=1) if n == "quiet" else None,
            {},
            _samples,
            "FALSE_INTERRUPT_OBSERVED",
        ),
        (
            "unexpected fallback",
            lambda r, s, n, i, label: r.update(unexpectedFallbacks=1) if s == 1 else None,
            {},
            _samples,
            "UNEXPECTED_FALLBACK_OBSERVED",
        ),
        (
            "candidate mismatch",
            lambda r, s, n, i, label: r.update(candidateIdentity={**IDENTITY, "gitSha": "other"}) if s == 1 else None,
            {},
            _samples,
            "CANDIDATE_IDENTITY_MISMATCH",
        ),
    ],
)
def test_candidate_soak_fails_closed_on_budget_or_evidence_violation(case, mutate, args, sample, expected):
    report = _run(args=_args(**args), journeys=_journeys(mutation=mutate), samples=sample)
    assert report["status"] == "FAIL", case
    assert expected in {failure["code"] for failure in report["failures"]}


def test_candidate_soak_fails_resource_leak():
    counter = 0

    def leaking_sample():
        nonlocal counter
        counter += 1
        return {
            "rssBytes": counter * 2_000_000,
            "fdCount": counter,
            "asyncioTaskCount": counter,
            "threadCount": counter,
        }

    report = _run(samples=leaking_sample)
    assert "RESOURCE_BUDGET_FAILED" in {item["code"] for item in report["failures"]}


def test_candidate_soak_fails_duration_and_latency_regression_budgets():
    short = _run(clock=_Clock((0.0, 1799.0, 1799.0)))
    assert short["status"] == "FAIL"
    assert short["durationSec"] == 1800.0
    assert "ACTUAL_DURATION_NOT_MET" in {item["code"] for item in short["failures"]}

    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"]["firstAudioP50Ms"] = 800
    regressed = _run(args=_args(baseline_report=baseline))
    assert "LATENCY_REGRESSION" in {item["code"] for item in regressed["failures"]}


@pytest.mark.parametrize(
    "metric",
    [
        "firstAudioP50Ms",
        "firstAudioP95Ms",
        "bargeinP95Ms",
        "reconnectRecoveryP95Ms",
    ],
)
def test_candidate_soak_rejects_baseline_missing_any_required_latency_metric(metric):
    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"].pop(metric)

    report = _run(args=_args(baseline_report=baseline))

    assert "BASELINE_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    "invalid",
    [None, True, 0, -1, float("nan"), float("inf"), float("-inf"), "1000"],
    ids=["null", "bool", "zero", "negative", "nan", "inf", "negative-inf", "string"],
)
def test_candidate_soak_rejects_malformed_baseline_latency_metric(invalid):
    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"]["reconnectRecoveryP95Ms"] = invalid

    report = _run(args=_args(baseline_report=baseline))

    assert "BASELINE_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_candidate_soak_rejects_extra_baseline_latency_metric():
    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"]["uncontractedMetricMs"] = 100

    report = _run(args=_args(baseline_report=baseline))

    assert "BASELINE_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("metrics", [None, [], "metrics", {}])
def test_candidate_soak_rejects_missing_or_nonmapping_baseline_metrics(metrics):
    baseline = deepcopy(_args().baseline_report)
    if metrics == {}:
        baseline.pop("latencyMetrics")
    else:
        baseline["latencyMetrics"] = metrics

    report = _run(args=_args(baseline_report=baseline))

    assert "BASELINE_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_candidate_soak_slow_reconnect_cannot_pass_with_omitted_baseline_metric():
    def slow_reconnect(result, _sequence, name, _index, _label):
        if name == "reconnect":
            result["latencies"]["reconnectRecoveryMs"] = [5000]

    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"].pop("reconnectRecoveryP95Ms")

    report = _run(
        args=_args(baseline_report=baseline),
        journeys=_journeys(mutation=slow_reconnect),
    )

    assert "BASELINE_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }
    assert report["latencyComparison"]["pass"] is False


@pytest.mark.parametrize("latency", ["firstAudioMs", "bargeinStopMs", "reconnectRecoveryMs"])
def test_candidate_soak_rejects_missing_candidate_latency_samples(latency):
    def remove_samples(result, _sequence, _name, _index, _label):
        if latency in result["latencies"]:
            result["latencies"][latency] = []

    report = _run(journeys=_journeys(mutation=remove_samples))

    assert "LATENCY_EVIDENCE_MALFORMED" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("latency", ["firstAudioMs", "bargeinStopMs", "reconnectRecoveryMs"])
def test_candidate_soak_rejects_nonpositive_candidate_latency_samples(latency):
    def zero_samples(result, _sequence, _name, _index, _label):
        if latency in result["latencies"]:
            result["latencies"][latency] = [0]

    report = _run(journeys=_journeys(mutation=zero_samples))

    assert "LATENCY_EVIDENCE_MALFORMED" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("metric", "baseline_value"),
    [
        ("firstAudioP50Ms", 800),
        ("firstAudioP95Ms", 800),
        ("bargeinP95Ms", 300),
        ("reconnectRecoveryP95Ms", 800),
    ],
)
def test_candidate_soak_applies_fifteen_percent_regression_to_every_metric(
    metric, baseline_value
):
    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"][metric] = baseline_value

    report = _run(args=_args(baseline_report=baseline))

    assert "LATENCY_REGRESSION" in {item["code"] for item in report["failures"]}
    assert report["latencyComparison"]["checks"][
        f"{metric.removesuffix('Ms')}Regression"
    ] is False


@pytest.mark.parametrize("bad_status", ["SKIPPED", "FAIL", None])
def test_candidate_soak_rejects_missing_or_nonpassing_upstream_layers(bad_status):
    real_api = _layer("real_api")
    real_api["status"] = bad_status
    report = _run(args=_args(real_api_report=real_api))
    assert report["status"] == "FAIL"
    assert "UPSTREAM_LAYER_NOT_PASSING" in {item["code"] for item in report["failures"]}


def test_candidate_soak_rejects_duplicate_out_of_order_or_sensitive_evidence():
    def mutate(result, sequence, _name, _index, _label):
        if sequence == 2:
            result["evidenceSequence"] = 1
            result["transcript"] = "raw child words"
            result["authorization"] = "Bearer secret"

    report = _run(journeys=_journeys(mutation=mutate))
    codes = {item["code"] for item in report["failures"]}
    assert {"EVIDENCE_SEQUENCE_INVALID", "FORBIDDEN_EVIDENCE_FIELD"} <= codes
    encoded = json.dumps(report)
    assert "raw child words" not in encoded
    assert "Bearer secret" not in encoded


def test_candidate_soak_rejects_reused_or_mismatched_upstream_window():
    correlated = deepcopy(_args().correlated_transport_report)
    correlated["logWindow"] = {"windowId": "other-window"}
    report = _run(args=_args(correlated_transport_report=correlated))
    assert "UPSTREAM_EVIDENCE_SCOPE_MISMATCH" in {item["code"] for item in report["failures"]}

    raw = deepcopy(_args().transport_report)
    correlated = deepcopy(_args().correlated_transport_report)
    log_report = deepcopy(_args().log_reliability_report)
    reused_window = {
        "windowId": "window-18",
        "start": "2026-08-31T11:18:00+00:00",
        "end": "2026-08-31T11:18:30+00:00",
    }
    reused_scope = {
        **EVIDENCE_SCOPE,
        "journeyId": "candidate-18",
        "connectionId": "candidate-connection-18",
        "serverStartUtc": reused_window["start"],
    }
    raw.update(
        journeyId="candidate-18",
        evidenceScope=reused_scope,
        serverConnectionId="candidate-connection-18",
        logWindow=reused_window,
    )
    correlated.update(
        journeyId="candidate-18",
        evidenceScope=reused_scope,
        logWindow=reused_window,
    )
    log_report.update(
        evidenceScope=reused_scope,
        logWindow=reused_window,
    )
    log_report["correlations"][0].update(
        journeyId="candidate-18",
        connectionId="candidate-connection-18",
    )
    reused = _run(
        args=_args(
            transport_report=raw,
            correlated_transport_report=correlated,
            log_reliability_report=log_report,
        )
    )
    assert "UPSTREAM_EVIDENCE_REUSED" in {item["code"] for item in reused["failures"]}


def test_candidate_soak_binds_lesson_execution_to_manifest():
    def wrong_manifest(result, _sequence, name, _index, _label):
        if name == "lesson":
            result["lessonManifestSha256"] = f"sha256:{'f' * 64}"

    report = _run(journeys=_journeys(mutation=wrong_manifest))
    assert "LESSON_MANIFEST_MISMATCH" in {item["code"] for item in report["failures"]}


def test_candidate_soak_rejects_log_layer_with_unrecovered_timeout():
    log_report = deepcopy(_args().log_reliability_report)
    log_report["unrecoveredTimeouts"] = [{"line": 7}]
    report = _run(args=_args(log_reliability_report=log_report))
    assert "LOG_RELIABILITY_CONTRACT_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("layer", "field", "value"),
    [
        ("transport", "serverConnectionId", "other-connection"),
        ("transport", "peerIdentityHash", f"sha256:{'e' * 64}"),
        ("transport", "finalLiveConnectionId", "fabricated-final"),
        ("correlated", "finalLiveConnectionId", "other-final"),
        ("log", "initialLiveConnectionId", "other-initial"),
        ("log", "logWindow", {**LOG_WINDOW, "end": "2026-08-31T10:02:00+00:00"}),
        (
            "transport",
            "liveConnectionTransitions",
            [
                {
                    "attempt": 2,
                    "fromLiveConnectionId": "live-1",
                    "toLiveConnectionId": "live-2",
                    "reason": "timeout",
                },
                {
                    "attempt": 1,
                    "fromLiveConnectionId": "live-2",
                    "toLiveConnectionId": "live-3",
                    "reason": "timeout",
                },
            ],
        ),
    ],
)
def test_candidate_soak_requires_full_task5_normalized_scope(layer, field, value):
    args = _args()
    target = {
        "transport": args.transport_report,
        "correlated": args.correlated_transport_report,
        "log": args.log_reliability_report,
    }[layer]
    target = deepcopy(target)
    target[field] = value
    overrides = {
        "transport": {"transport_report": target},
        "correlated": {"correlated_transport_report": target},
        "log": {"log_reliability_report": target},
    }[layer]

    report = _run(args=_args(**overrides))

    assert "TASK5_CORRELATED_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("mutation", ["reason", "extra", "reordered"])
def test_candidate_soak_rejects_fabricated_transition_ledger(mutation):
    args = _args_with_transition()
    if mutation == "reason":
        args.correlated_transport_report["liveConnectionTransitions"][0]["reason"] = "goaway"
    elif mutation == "extra":
        args.log_reliability_report["liveConnectionTransitions"].append(
            {
                "attempt": 2,
                "fromLiveConnectionId": "live-2",
                "toLiveConnectionId": "live-3",
                "reason": "goaway",
            }
        )
        args.log_reliability_report["finalLiveConnectionId"] = "live-3"
    else:
        extra = {
            "attempt": 2,
            "fromLiveConnectionId": "live-2",
            "toLiveConnectionId": "live-3",
            "reason": "goaway",
        }
        args.transport_report["liveConnectionTransitions"].append(extra)
        args.transport_report["liveConnectionTransitions"].reverse()
        args.transport_report["finalLiveConnectionId"] = "live-3"

    report = _run(args=args)

    assert "TASK5_CORRELATED_EVIDENCE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("reused_component", "expected_code"),
    [
        ("journeyId", "EVIDENCE_JOURNEY_REUSED"),
        ("windowId", "EVIDENCE_WINDOW_REUSED"),
        ("utcWindow", "EVIDENCE_UTC_WINDOW_REUSED"),
    ],
)
def test_candidate_soak_rejects_each_reused_execution_identity_component(
    reused_component,
    expected_code,
):
    first = {}

    def reuse(result, sequence, _name, _index, _label):
        if sequence == 1:
            first.update(deepcopy(result))
            return
        if sequence != 2:
            return
        if reused_component == "journeyId":
            result["journeyId"] = first["journeyId"]
        elif reused_component == "windowId":
            result["windowId"] = first["windowId"]
            result["logWindow"]["windowId"] = first["logWindow"]["windowId"]
        else:
            result["logWindow"]["start"] = first["logWindow"]["start"]
            result["logWindow"]["end"] = first["logWindow"]["end"]

    report = _run(journeys=_journeys(mutation=reuse))

    assert expected_code in {item["code"] for item in report["failures"]}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_scope",
        "client_authored",
        "cross_peer",
        "flat_connection_mismatch",
        "fabricated_transition",
        "bargein_task5_mismatch",
    ],
)
def test_candidate_soak_requires_immutable_server_execution_scope(mutation):
    def corrupt(result, sequence, name, _index, _label):
        if mutation == "bargein_task5_mismatch":
            if name != "bargein":
                return
        elif sequence != 1:
            return
        if mutation == "missing_scope":
            result.pop("evidenceScope")
        elif mutation == "client_authored":
            result["serverIssued"] = False
        elif mutation == "cross_peer":
            result["evidenceScope"]["peerIdentityHash"] = f"sha256:{'e' * 64}"
        elif mutation == "flat_connection_mismatch":
            result["connectionId"] = "fabricated-connection"
        elif mutation == "fabricated_transition":
            result["finalLiveConnectionId"] = "fabricated-live"
        else:
            result["task5CorrelatedEvidence"]["logWindow"]["end"] = (
                "2026-08-31T11:59:59+00:00"
            )

    report = _run(journeys=_journeys(mutation=corrupt))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("mutation", ["cross_peer_both", "fabricated_live_both"])
def test_candidate_soak_binds_execution_scope_to_independent_server_identity(mutation):
    def corrupt(result, sequence, _name, _index, _label):
        if sequence != 1:
            return
        if mutation == "cross_peer_both":
            value = f"sha256:{'e' * 64}"
            result["peerIdentityHash"] = value
            result["evidenceScope"]["peerIdentityHash"] = value
            result["task5LogEvidence"]["evidenceScope"]["peerIdentityHash"] = value
        else:
            result["liveConnectionId"] = "fabricated-live"
            result["initialLiveConnectionId"] = "fabricated-live"
            result["finalLiveConnectionId"] = "fabricated-live"
            result["evidenceScope"]["liveConnectionId"] = "fabricated-live"
            result["evidenceScope"]["initialLiveConnectionId"] = "fabricated-live"

    report = _run(journeys=_journeys(mutation=corrupt))

    assert {
        "EXECUTION_SERVER_SCOPE_INVALID",
        "EXECUTION_SERVER_ANCHOR_MISMATCH",
    } & {item["code"] for item in report["failures"]}


def test_candidate_soak_rejects_three_sided_inconsistent_live_owner_chain():
    def corrupt(result, sequence, _name, _index, _label):
        if sequence != 1:
            return
        for target in (result, result["evidenceScope"], result["task5LogEvidence"]):
            if "liveConnectionId" in target:
                target["liveConnectionId"] = "fabricated-live"
            target["initialLiveConnectionId"] = "fabricated-live"
        result["finalLiveConnectionId"] = "fabricated-live-final"
        result["task5LogEvidence"]["finalLiveConnectionId"] = (
            "fabricated-live-final"
        )
        result["task5LogEvidence"]["evidenceScope"]["liveConnectionId"] = (
            "fabricated-live"
        )
        result["task5LogEvidence"]["evidenceScope"]["initialLiveConnectionId"] = (
            "fabricated-live"
        )

    report = _run(journeys=_journeys(mutation=corrupt))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_candidate_soak_rejects_reduced_non_bargein_log_proof():
    def reduce(result, sequence, name, _index, _label):
        if sequence == 1 and name != "bargein":
            result["task5LogEvidence"] = {
                key: result["task5LogEvidence"][key]
                for key in (
                    "schemaVersion",
                    "name",
                    "status",
                    "candidateIdentity",
                    "evidenceScope",
                    "initialLiveConnectionId",
                    "finalLiveConnectionId",
                    "liveConnectionTransitions",
                    "logWindow",
                )
            }

    report = _run(journeys=_journeys(mutation=reduce))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_candidate_soak_accepts_full_task5_correlated_artifact_fields():
    report = _run()

    assert report["status"] == "PASS"


def test_candidate_soak_accepts_per_execution_live_owners_independent_of_upstream_task5():
    def use_execution_live_owner(result, sequence, name, _index, _label):
        initial_id = f"execution-live-{sequence}-initial"
        final_id = initial_id
        transitions = []
        if name == "reopen":
            final_id = f"execution-live-{sequence}-reopened"
            transitions = [
                {
                    "attempt": 1,
                    "fromLiveConnectionId": initial_id,
                    "toLiveConnectionId": final_id,
                }
            ]
        result["liveConnectionId"] = initial_id
        result["initialLiveConnectionId"] = initial_id
        result["finalLiveConnectionId"] = final_id
        result["liveConnectionTransitions"] = transitions
        _refresh_execution_contract(result)
        if name == "reconnect":
            result["task5LogEvidence"]["serverConnectionTransitions"] = [
                {
                    "status": "PASS",
                    "source": "server_log",
                    "serverIssued": True,
                    "sequence": 1,
                    "reason": "same_device_reconnect",
                    "fromJourneyId": "candidate-30",
                    "fromConnectionId": SOAK_CONNECTION_1,
                    "toJourneyId": result["journeyId"],
                    "toConnectionId": SOAK_CONNECTION_2,
                    "peerIdentityHash": PEER_HASH,
                }
            ]

    report = _run(journeys=_journeys(mutation=use_execution_live_owner))

    assert report["status"] == "PASS", report
    executions = report["evidenceExecutions"]
    assert all(item["initialLiveConnectionId"] != "live-1" for item in executions)
    reopen = next(item for item in executions if item["stage"] == "reopen")
    assert reopen["initialLiveConnectionId"] != reopen["finalLiveConnectionId"]
    assert len(reopen["liveConnectionTransitions"]) == 1


def test_candidate_soak_rejects_live_owner_inconsistent_with_its_own_log_proof():
    def fabricate_flat_owner(result, sequence, _name, _index, _label):
        if sequence != 1:
            return
        fabricated = "fabricated-execution-live"
        result["liveConnectionId"] = fabricated
        result["initialLiveConnectionId"] = fabricated
        result["finalLiveConnectionId"] = fabricated
        result["liveConnectionTransitions"] = []
        result["evidenceScope"]["liveConnectionId"] = fabricated
        result["evidenceScope"]["initialLiveConnectionId"] = fabricated

    report = _run(journeys=_journeys(mutation=fabricate_flat_owner))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_candidate_soak_accepts_single_server_connection_reconnect():
    report = _run()
    executions = report["evidenceExecutions"]

    assert {item["connectionId"] for item in executions[:30]} == {
        SOAK_CONNECTION_1
    }
    assert {item["connectionId"] for item in executions[30:]} == {
        SOAK_CONNECTION_2
    }
    assert report["quietPadding"][0]["connectionId"] == SOAK_CONNECTION_2
    assert report["cleanupVerdict"]["status"] == "PASS"


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "same_connection",
        "reordered",
        "proof_mismatch",
        "extra",
        "drift_before",
        "drift_after",
        "peer_change",
    ],
)
def test_candidate_soak_rejects_invalid_server_connection_transition(failure):
    def corrupt(result, sequence, name, _index, _label):
        if name == "reconnect":
            transitions = result["task5LogEvidence"]["serverConnectionTransitions"]
            transition = transitions[0]
            if failure == "missing":
                transitions.clear()
            elif failure == "same_connection":
                transition["toConnectionId"] = transition["fromConnectionId"]
            elif failure == "reordered":
                transition["fromConnectionId"], transition["toConnectionId"] = (
                    transition["toConnectionId"],
                    transition["fromConnectionId"],
                )
            elif failure == "proof_mismatch":
                transition["fromJourneyId"] = result["journeyId"]
        if failure == "extra" and name == "lesson":
            result["task5LogEvidence"]["serverConnectionTransitions"] = [{
                "status": "PASS",
                "source": "server_log",
                "serverIssued": True,
                "sequence": 2,
                "reason": "same_device_reconnect",
                "fromJourneyId": result["journeyId"],
                "fromConnectionId": SOAK_CONNECTION_2,
                "toJourneyId": result["journeyId"],
                "toConnectionId": "candidate-soak-websocket-3",
                "peerIdentityHash": PEER_HASH,
            }]
        elif failure == "drift_before" and sequence == 1:
            result["connectionId"] = SOAK_CONNECTION_2
            _refresh_execution_contract(result)
        elif failure == "drift_after" and name == "lesson":
            result["connectionId"] = SOAK_CONNECTION_1
            _refresh_execution_contract(result)
        elif failure == "peer_change" and name == "lesson":
            result["peerIdentityHash"] = f"sha256:{'e' * 64}"
            _refresh_execution_contract(result)

    report = _run(journeys=_journeys(mutation=corrupt))
    codes = {item["code"] for item in report["failures"]}

    assert {
        "SERVER_CONNECTION_TRANSITION_INVALID",
        "SERVER_CONNECTION_DRIFT",
        "EXECUTION_SERVER_ANCHOR_MISMATCH",
    } & codes


def test_candidate_soak_rejects_cleanup_on_pre_reconnect_connection():
    journeys = _journeys()

    async def stale_cleanup(args, **kwargs):
        evidence = await _journeys()["cleanup"](args, **kwargs)
        evidence["serverAnchor"]["connectionId"] = SOAK_CONNECTION_1
        return evidence

    journeys["cleanup"] = stale_cleanup
    report = _run(journeys=journeys)

    assert report["cleanupVerdict"]["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}


def test_candidate_soak_rejects_minimal_fabricated_task5_projection():
    def minimize(result, _sequence, name, _index, _label):
        if name == "bargein":
            result["task5CorrelatedEvidence"] = {
                key: result["task5CorrelatedEvidence"][key]
                for key in (
                    "schemaVersion",
                    "name",
                    "status",
                    "candidateIdentity",
                    "journeyId",
                    "evidenceScope",
                )
            }

    report = _run(journeys=_journeys(mutation=minimize))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("case", "start", "end"),
    [
        ("malformed", "not-a-time", "2026-08-31T11:01:10+00:00"),
        ("naive", "2026-08-31T11:00:40", "2026-08-31T11:01:10"),
        ("non utc", "2026-08-31T18:00:40+07:00", "2026-08-31T18:01:10+07:00"),
        ("reversed", "2026-08-31T11:01:10+00:00", "2026-08-31T11:00:40+00:00"),
        ("equal", "2026-08-31T11:00:40+00:00", "2026-08-31T11:00:40+00:00"),
        ("overlap", "2026-08-31T11:00:20+00:00", "2026-08-31T11:00:50+00:00"),
        ("touching", "2026-08-31T11:00:30+00:00", "2026-08-31T11:01:00+00:00"),
        ("out of order", "2026-08-31T10:59:00+00:00", "2026-08-31T10:59:30+00:00"),
    ],
)
def test_candidate_soak_rejects_invalid_or_non_chronological_utc_windows(
    case, start, end
):
    def mutate(result, sequence, _name, _index, _label):
        if sequence == 2:
            result["logWindow"].update(start=start, end=end)

    report = _run(journeys=_journeys(mutation=mutate))

    assert "EVIDENCE_UTC_WINDOW_INVALID" in {
        item["code"] for item in report["failures"]
    }, case


def test_candidate_soak_rejects_utc_window_span_beyond_soak_duration():
    def mutate(result, sequence, _name, _index, _label):
        if sequence == 33:
            result["logWindow"]["end"] = "2026-08-31T12:00:00+00:00"

    report = _run(
        args=_args(candidate_evidence_duration_sec=1800),
        journeys=_journeys(mutation=mutate),
    )

    assert "CLAIMED_DURATION_MISMATCH" in {
        item["code"] for item in report["failures"]
    }


def test_claimed_duration_cannot_replace_zero_actual_or_proven_duration():
    def short_windows(result, sequence, _name, _index, _label):
        start = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc) + timedelta(
            seconds=sequence
        )
        result["logWindow"].update(
            start=start.isoformat(),
            end=(start + timedelta(milliseconds=100)).isoformat(),
        )

    journeys = _journeys(mutation=short_windows)
    journeys.pop("monitor")
    report = _run(
        args=_args(candidate_evidence_duration_sec=1800),
        journeys=journeys,
        clock=_Clock((0.0, 0.0)),
    )

    assert report["status"] == "FAIL"
    assert "PROVEN_DURATION_NOT_MET" in {item["code"] for item in report["failures"]}
    assert "ACTUAL_DURATION_NOT_MET" in {item["code"] for item in report["failures"]}


@pytest.mark.parametrize("failure", ["missing", "malformed", "gapped", "overlap"])
def test_quiet_padding_must_prove_bounded_healthy_coverage(failure):
    journeys = _journeys()
    if failure == "missing":
        journeys.pop("monitor")
    elif failure == "malformed":
        async def malformed(_args, *, duration_sec):
            return [{"name": "quiet_padding", "status": "PASS"}]

        journeys["monitor"] = malformed
    else:
        original = journeys["monitor"]

        async def gapped(args, *, duration_sec):
            evidence = await original(args, duration_sec=duration_sec)
            start = datetime.fromisoformat(evidence[0]["logWindow"]["start"])
            evidence[0]["logWindow"]["start"] = (
                start
                + timedelta(seconds=1 if failure == "gapped" else -20)
            ).isoformat()
            return evidence

        journeys["monitor"] = gapped

    report = _run(journeys=journeys)

    assert report["status"] == "FAIL"
    assert "QUIET_PADDING_INVALID" in {item["code"] for item in report["failures"]}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_log_proof",
        "wrong_peer",
        "wrong_connection",
        "live_proof_mismatch",
        "misplaced_latency",
    ],
)
def test_quiet_padding_requires_trusted_full_server_log_proof(mutation):
    journeys = _journeys()
    original = journeys["monitor"]

    async def corrupt(args, *, duration_sec):
        evidence = await original(args, duration_sec=duration_sec)
        padding = evidence[0]
        if mutation == "missing_log_proof":
            padding.pop("task5LogEvidence")
        elif mutation == "wrong_peer":
            padding["peerIdentityHash"] = f"sha256:{'e' * 64}"
            padding["evidenceScope"]["peerIdentityHash"] = padding[
                "peerIdentityHash"
            ]
        elif mutation == "wrong_connection":
            padding["connectionId"] = SOAK_CONNECTION_1
            padding["evidenceScope"]["connectionId"] = SOAK_CONNECTION_1
        elif mutation == "live_proof_mismatch":
            padding["finalLiveConnectionId"] = "fabricated-padding-live"
        else:
            padding["latencies"] = {"firstAudioMs": [1]}
        return evidence

    journeys["monitor"] = corrupt
    report = _run(journeys=journeys)

    assert "QUIET_PADDING_INVALID" in {item["code"] for item in report["failures"]}


def test_conversation_latency_cannot_spoof_bargein_or_reconnect_metrics():
    def spoof(result, _sequence, name, _index, _label):
        if name == "conversation":
            result["latencies"] = {
                "firstAudioMs": [1000],
                "bargeinStopMs": [1],
                "serverOutputGapMs": [1],
                "reconnectRecoveryMs": [1],
            }
        elif name in {"bargein", "reconnect"}:
            result["latencies"] = {}

    report = _run(journeys=_journeys(mutation=spoof))

    assert "LATENCY_EVIDENCE_MALFORMED" in {
        item["code"] for item in report["failures"]
    }


def test_missing_one_of_ten_bargein_latency_samples_fails():
    def omit(result, _sequence, name, index, _label):
        if name == "bargein" and index == 10:
            result["latencies"].pop("serverOutputGapMs")

    report = _run(journeys=_journeys(mutation=omit))

    assert "LATENCY_EVIDENCE_MALFORMED" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("stage", ["quiet", "lesson"])
def test_non_latency_stage_rejects_misplaced_latency_fields(stage):
    def misplaced(result, _sequence, name, _index, _label):
        if name == stage:
            result["latencies"] = {"firstAudioMs": [1]}

    report = _run(journeys=_journeys(mutation=misplaced))

    assert "LATENCY_EVIDENCE_MALFORMED" in {
        item["code"] for item in report["failures"]
    }


def test_slow_server_output_gap_from_bargein_stage_fails_hard_budget():
    def slow(result, _sequence, name, _index, _label):
        if name == "bargein":
            result["latencies"]["serverOutputGapMs"] = [500]

    report = _run(journeys=_journeys(mutation=slow))

    assert "HARD_LATENCY_BUDGET_FAILED" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("stage", "flat_field", "proof_field"),
    [
        ("conversation", "firstAudioMs", "firstAudioMs"),
        ("bargein", "bargeinStopMs", "bargeinStopMs"),
        ("bargein", "serverOutputGapMs", "maxServerOutputGapMs"),
        ("reopen", "reconnectRecoveryMs", "reconnectRecoveryMs"),
        ("reconnect", "reconnectRecoveryMs", "reconnectRecoveryMs"),
    ],
)
def test_candidate_latency_must_exact_match_trusted_stage_proof(
    stage, flat_field, proof_field
):
    def mismatch(result, _sequence, name, _index, _label):
        if name != stage:
            return
        result["latencies"][flat_field] = [1]
        if name == "bargein":
            assert result["task5CorrelatedEvidence"][proof_field] != 1
        else:
            assert result["task5LogEvidence"]["journeyLatencyEvidence"][proof_field] != 1

    report = _run(journeys=_journeys(mutation=mismatch))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("stage", ["conversation", "reopen", "reconnect"])
def test_candidate_latency_fails_when_trusted_stage_proof_metric_is_missing(stage):
    def remove_proof(result, _sequence, name, _index, _label):
        if name == stage:
            result["task5LogEvidence"]["journeyLatencyEvidence"] = {}

    report = _run(journeys=_journeys(mutation=remove_proof))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("stage", "wrong_type"),
    [
        ("conversation", "conversation_after_lesson"),
        ("conversation_after_lesson", "conversation"),
        ("reopen", "reconnect"),
        ("reconnect", "reopen"),
        ("reconnect", None),
    ],
)
def test_candidate_latency_proof_journey_type_must_match_execution_stage(
    stage, wrong_type
):
    def mutate(result, _sequence, name, _index, _label):
        if name == stage:
            result["task5LogEvidence"]["journeyType"] = wrong_type

    report = _run(journeys=_journeys(mutation=mutate))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize("server_issued", [None, False, "true", 1])
def test_quiet_padding_requires_exact_true_server_issued(server_issued):
    journeys = _journeys()
    original = journeys["monitor"]

    async def mutate(args, *, duration_sec):
        evidence = await original(args, duration_sec=duration_sec)
        evidence[0]["serverIssued"] = server_issued
        return evidence

    journeys["monitor"] = mutate
    report = _run(journeys=journeys)

    assert "QUIET_PADDING_INVALID" in {item["code"] for item in report["failures"]}


@pytest.mark.parametrize("server_issued", [None, False, "true", 1])
def test_primary_execution_requires_exact_true_server_issued(server_issued):
    def mutate(result, sequence, _name, _index, _label):
        if sequence == 1:
            result["serverIssued"] = server_issued

    report = _run(journeys=_journeys(mutation=mutate))

    assert "EXECUTION_SERVER_SCOPE_INVALID" in {
        item["code"] for item in report["failures"]
    }


def test_replay_derives_duration_from_execution_and_padding_windows():
    async def build_manifest():
        journeys = _journeys()
        executions = []
        for name, count in (
            ("conversation", 17),
            ("bargein", 10),
            ("quiet", 2),
            ("reopen", 1),
            ("reconnect", 1),
            ("lesson", 1),
            ("conversation_after_lesson", 1),
        ):
            callable_name = "conversation" if name == "conversation_after_lesson" else name
            for index in range(1, count + 1):
                executions.append(
                    await journeys[callable_name](
                        _args(), name=name, index=index, label=None
                    )
                )
        padding = await journeys["monitor"](_args(), duration_sec=480)
        final_scope = _cleanup_scope_from_execution(padding[-1])
        return {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_soak_evidence_manifest",
            "candidateIdentity": IDENTITY,
            "durationSec": 1800,
            "runtimeElapsedSec": 1800,
            "executions": executions,
            "quietPadding": padding,
            "cleanup": _cleanup_evidence(final_scope),
            "resourceSamples": [
                {**_samples(), "sampleId": f"candidate-resource-{index}"}
                for index in range(1, 37)
            ],
        }

    args = _args(
        mode="candidate",
        candidate_journeys=None,
        journey_evidence=asyncio.run(build_manifest()),
    )
    report = asyncio.run(run_soak(args))

    assert report["status"] == "PASS"
    assert report["durationSec"] == 1800.0
    assert report["runtimeElapsedSec"] < report["durationSec"]
    assert report["replayCandidateEvidence"] is True


def test_replay_does_not_credit_inter_window_gaps_toward_duration():
    manifest = _full_span_manifest()
    start = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
    for index, execution in enumerate(manifest["executions"]):
        window_start = start + timedelta(seconds=index * 55)
        execution["logWindow"].update(
            start=window_start.isoformat(),
            end=(window_start + timedelta(seconds=45)).isoformat(),
        )
        if execution["name"] == "quiet":
            execution["observationDurationSec"] = 45.0
            execution["task5LogEvidence"]["candidateSemanticEvidence"][
                "durationMs"
            ] = 45000
    _refresh_execution_sequence(manifest["executions"])
    manifest["durationSec"] = 1485
    manifest["cleanup"] = _cleanup_evidence(
        _cleanup_scope_from_execution(manifest["executions"][-1])
    )

    report = _run_manifest(manifest)

    assert report["durationSec"] == 1485.0
    assert "PROVEN_DURATION_NOT_MET" in {item["code"] for item in report["failures"]}


def test_replay_sums_monitored_windows_with_gaps_to_exact_duration():
    report = _run_manifest(_full_span_manifest())

    assert report["status"] == "PASS"
    assert report["durationSec"] == 1800.0


def test_replay_runtime_scalar_is_informational_but_cannot_undercut_coverage():
    manifest = _full_span_manifest()
    manifest["runtimeElapsedSec"] = 1799.5

    report = _run_manifest(manifest)

    assert "CLAIMED_RUNTIME_MISMATCH" in {
        item["code"] for item in report["failures"]
    }


@pytest.mark.parametrize(
    ("field", "secret"),
    [
        ("transcript", "raw child words"),
        ("Authorization", "Bearer private-token"),
        ("apiKey", "google-secret"),
        ("raw_audio_base64", "UklGRlNFQ1JFVA=="),
        ("exception", "token=exception-secret"),
        ("sessionResumptionHandle", "session-private"),
        ("Cookie", "session-cookie-secret"),
        ("xGoogleApiKey", "variant-secret"),
    ],
)
def test_quiet_padding_rejects_nested_sensitive_evidence_without_leaking(
    field, secret
):
    journeys = _journeys()
    original = journeys["monitor"]

    async def sensitive(args, *, duration_sec):
        evidence = await original(args, duration_sec=duration_sec)
        evidence[0]["metadata"] = {"nested": {field: secret}}
        return evidence

    journeys["monitor"] = sensitive
    report = _run(journeys=journeys)
    encoded = json.dumps(report)

    assert "QUIET_PADDING_INVALID" in {item["code"] for item in report["failures"]}
    assert secret not in encoded
    assert json.dumps(field) + ":" not in encoded


def test_candidate_manifest_is_scanned_centrally_before_artifact_sections():
    args = _args(
        mode="candidate",
        candidate_journeys=None,
        journey_evidence={
            "durationSec": 1800,
            "executions": [],
            "quietPadding": [],
            "resourceSamples": [],
            "futureSection": {"xGoogleApiKey": "manifest-secret"},
        },
    )

    with pytest.raises(ValueError) as captured:
        asyncio.run(run_soak(args))

    assert str(captured.value) == "candidate evidence contains forbidden fields"
    assert "manifest-secret" not in str(captured.value)
    assert "xGoogleApiKey" not in str(captured.value)


def _full_span_manifest(*, padding=None, samples=35):
    async def build():
        journeys = _journeys()
        executions = []
        sequence = 0
        for name, count in (
            ("conversation", 17),
            ("bargein", 10),
            ("quiet", 2),
            ("reopen", 1),
            ("reconnect", 1),
            ("lesson", 1),
            ("conversation_after_lesson", 1),
        ):
            callable_name = "conversation" if name == "conversation_after_lesson" else name
            for index in range(1, count + 1):
                sequence += 1
                item = await journeys[callable_name](_args(), name=name, index=index)
                start = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc) + timedelta(
                    seconds=(sequence - 1) * 56
                )
                item["logWindow"].update(
                    start=start.isoformat(),
                    end=(
                        start + timedelta(seconds=40 if sequence == 33 else 55)
                    ).isoformat(),
                )
                if name == "quiet":
                    item["observationDurationSec"] = 55.0
                    item["task5LogEvidence"]["candidateSemanticEvidence"][
                        "durationMs"
                    ] = 55000
                _refresh_execution_contract(item)
                executions.append(item)
        _refresh_execution_sequence(executions)
        return {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_soak_evidence_manifest",
            "candidateIdentity": IDENTITY,
            "durationSec": 1800,
            "runtimeElapsedSec": 1800,
            "executions": executions,
            "quietPadding": [] if padding is None else padding,
            "cleanup": _cleanup_evidence(
                _cleanup_scope_from_execution(executions[-1])
            ),
            "resourceSamples": [
                {**_samples(), "sampleId": f"candidate-resource-{index}"}
                for index in range(1, samples + 1)
            ],
        }

    return asyncio.run(build())


def _run_manifest(manifest):
    return asyncio.run(
        run_soak(
            _args(
                mode="candidate",
                candidate_journeys=None,
                journey_evidence=manifest,
            )
        )
    )


def test_full_span_rejects_any_supplied_padding_even_when_safe():
    safe_padding = [
        {
            "schemaVersion": "google-live-reliability.v1",
            "name": "quiet_padding",
            "status": "PASS",
            "candidateIdentity": IDENTITY,
        }
    ]
    with pytest.raises(ValueError, match="manifest schema"):
        _run_manifest(_full_span_manifest(padding=safe_padding, samples=36))


def test_full_span_rejects_malformed_padding_instead_of_ignoring_it():
    with pytest.raises(ValueError, match="manifest schema"):
        _run_manifest(
            _full_span_manifest(padding=[{"name": "quiet_padding"}], samples=36)
        )


def test_full_span_rejects_unused_or_leaking_resource_sample():
    manifest = _full_span_manifest(samples=36)
    manifest["resourceSamples"][-1]["exception"] = "token=unused-secret"
    with pytest.raises(ValueError, match="forbidden fields") as captured:
        _run_manifest(manifest)
    assert "unused-secret" not in str(captured.value)


def test_full_span_rejects_safe_extra_resource_sample():
    with pytest.raises(ValueError, match="manifest is incomplete"):
        _run_manifest(_full_span_manifest(samples=36))


@pytest.mark.parametrize(
    "failure", ["missing", "malformed", "skipped", "mismatch", "extra"]
)
def test_replay_requires_exactly_one_matching_cleanup_artifact(failure):
    manifest = _full_span_manifest()
    if failure == "missing":
        manifest.pop("cleanup")
    elif failure == "malformed":
        manifest["cleanup"] = {"name": "candidate_cleanup"}
    elif failure == "skipped":
        manifest["cleanup"]["status"] = "SKIPPED"
    elif failure == "mismatch":
        manifest["cleanup"]["candidateIdentity"] = {**IDENTITY, "gitSha": "other"}
    else:
        manifest["cleanup"] = [manifest["cleanup"], deepcopy(manifest["cleanup"])]

    with pytest.raises(ValueError, match="manifest"):
        _run_manifest(manifest)


def test_needed_padding_rejects_duplicate_resource_sample_id():
    # Reuse the proven 22-minute + padding manifest from the replay test shape.
    async def build():
        journeys = _journeys()
        executions = []
        for name, count in (
            ("conversation", 17),
            ("bargein", 10),
            ("quiet", 2),
            ("reopen", 1),
            ("reconnect", 1),
            ("lesson", 1),
            ("conversation_after_lesson", 1),
        ):
            callable_name = "conversation" if name == "conversation_after_lesson" else name
            for index in range(1, count + 1):
                executions.append(await journeys[callable_name](_args(), name=name, index=index))
        padding = await journeys["monitor"](_args(), duration_sec=480)
        samples = [
            {**_samples(), "sampleId": f"candidate-resource-{index}"}
            for index in range(1, 37)
        ]
        samples[-1]["sampleId"] = samples[-2]["sampleId"]
        return {
            "schemaVersion": "google-live-reliability.v1",
            "name": "candidate_soak_evidence_manifest",
            "candidateIdentity": IDENTITY,
            "durationSec": 1800,
            "runtimeElapsedSec": 1800,
            "executions": executions,
            "quietPadding": padding,
            "cleanup": _cleanup_evidence(
                _cleanup_scope_from_execution(padding[-1])
            ),
            "resourceSamples": samples,
        }

    with pytest.raises(ValueError, match="resource accounting"):
        _run_manifest(asyncio.run(build()))


def test_candidate_soak_rejects_hard_latency_budget_even_with_matching_baseline():
    def slow(result, _sequence, name, _index, _label):
        if name in {"conversation", "conversation_after_lesson", "bargein"}:
            result["latencies"]["firstAudioMs"] = [1900]

    baseline = deepcopy(_args().baseline_report)
    baseline["latencyMetrics"]["firstAudioP50Ms"] = 1900
    baseline["latencyMetrics"]["firstAudioP95Ms"] = 1900
    report = _run(args=_args(baseline_report=baseline), journeys=_journeys(mutation=slow))
    assert "HARD_LATENCY_BUDGET_FAILED" in {item["code"] for item in report["failures"]}


def test_candidate_soak_runs_cleanup_after_first_journey_failure():
    cleaned = []
    journeys = _journeys(mutation=lambda result, sequence, *_: result.update(status="FAIL") if sequence == 1 else None)

    async def cleanup(_args, **_kwargs):
        cleaned.append(True)
        return await _journeys()["cleanup"](_args, **_kwargs)

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)
    assert report["status"] == "FAIL"
    assert cleaned == [True]


def test_candidate_soak_requires_cleanup_callable_before_workload():
    journeys = _journeys()
    journeys.pop("cleanup")
    report = _run(journeys=journeys)
    assert report["status"] == "FAIL"
    assert report["failures"] == [{"code": "CLEANUP_CALLABLE_MISSING"}]


@pytest.mark.parametrize("failure", ["throws", "hangs", "pending_tasks"])
def test_candidate_soak_cleanup_failure_overrides_pass_safely(failure):
    journeys = _journeys()
    if failure == "throws":
        async def cleanup(_args, **_kwargs):
            raise RuntimeError("token=cleanup-secret")
    elif failure == "hangs":
        async def cleanup(_args, **_kwargs):
            await asyncio.Event().wait()
    else:
        async def cleanup(args, **kwargs):
            evidence = await _journeys()["cleanup"](args, **kwargs)
            evidence["pendingOwnedTasks"] = 1
            return evidence
    journeys["cleanup"] = cleanup

    report = _run(journeys=journeys)
    encoded = json.dumps(report)

    assert report["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}
    assert "cleanup-secret" not in encoded


def test_candidate_soak_sync_cleanup_failure_is_reported_safely():
    journeys = _journeys()

    def cleanup(_args, **_kwargs):
        raise RuntimeError("token=sync-cleanup-secret")

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)

    assert report["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}
    assert "sync-cleanup-secret" not in json.dumps(report)


def test_candidate_soak_non_awaitable_cleanup_result_fails_safely():
    journeys = _journeys()
    journeys["cleanup"] = lambda _args, **_kwargs: "raw log token=invalid-secret"

    report = _run(journeys=journeys)

    assert report["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}
    assert "invalid-secret" not in json.dumps(report)


def test_candidate_soak_rejects_sensitive_cleanup_evidence_without_leaking():
    journeys = _journeys()

    async def cleanup(args, **kwargs):
        evidence = await _journeys()["cleanup"](args, **kwargs)
        evidence["metadata"] = {
            "nested": {"Authorization": "Bearer cleanup-private-token"}
        }
        return evidence

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)
    encoded = json.dumps(report)

    assert report["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}
    assert "cleanup-private-token" not in encoded
    assert "Authorization" not in encoded


def test_candidate_soak_normalizes_untrusted_cleanup_status_values():
    journeys = _journeys()

    async def cleanup(args, **kwargs):
        evidence = await _journeys()["cleanup"](args, **kwargs)
        evidence["providerFinalizeStatus"] = "Bearer provider-secret"
        evidence["providerCloseStatus"] = "raw close log token=close-secret"
        evidence["logStatus"] = "raw server log token=log-secret"
        return evidence

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)
    encoded = json.dumps(report)

    assert report["cleanupVerdict"]["providerFinalizeStatus"] == "FAIL"
    assert report["cleanupVerdict"]["providerCloseStatus"] == "FAIL"
    assert report["cleanupVerdict"]["logStatus"] == "FAIL"
    assert "provider-secret" not in encoded
    assert "close-secret" not in encoded
    assert "log-secret" not in encoded


@pytest.mark.parametrize("field", ["pendingOwnedTasks", "activeSessions", "activeReceiveLoops"])
@pytest.mark.parametrize("invalid_zero", [False, 0.0])
def test_candidate_soak_requires_integer_zero_cleanup_counters(field, invalid_zero):
    journeys = _journeys()

    async def cleanup(args, **kwargs):
        evidence = await _journeys()["cleanup"](args, **kwargs)
        evidence[field] = invalid_zero
        return evidence

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)

    assert report["status"] == "FAIL"
    assert "CLEANUP_FAILED" in {item["code"] for item in report["failures"]}


@pytest.mark.parametrize("bad_sample", [{}, {"rssBytes": 100}])
def test_candidate_soak_requires_valid_final_resource_sample(bad_sample):
    journeys = _journeys()
    cleaned = False

    async def cleanup(args, **kwargs):
        nonlocal cleaned
        cleaned = True
        return await _journeys()["cleanup"](args, **kwargs)

    def sample():
        return bad_sample if cleaned else _samples()

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys, samples=sample)

    assert report["status"] == "FAIL"
    assert report["cleanupVerdict"]["status"] == "FAIL"
    assert report["cleanupVerdict"]["resourceEndSampleAccounted"] is False


def test_candidate_soak_final_resource_sampler_exception_fails_cleanup_safely():
    journeys = _journeys()
    cleaned = False

    async def cleanup(args, **kwargs):
        nonlocal cleaned
        cleaned = True
        return await _journeys()["cleanup"](args, **kwargs)

    def sample():
        if cleaned:
            raise RuntimeError("token=resource-secret")
        return _samples()

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys, samples=sample)

    assert report["cleanupVerdict"]["status"] == "FAIL"
    assert "resource-secret" not in json.dumps(report)


@pytest.mark.parametrize("timeout", ["invalid", float("nan"), 0, -1])
def test_candidate_soak_invalid_cleanup_timeout_still_cleans_and_fails(timeout):
    calls = []
    journeys = _journeys()

    async def cleanup(args, **kwargs):
        calls.append(True)
        return await _journeys()["cleanup"](args, **kwargs)

    journeys["cleanup"] = cleanup
    report = _run(args=_args(cleanup_timeout_sec=timeout), journeys=journeys)

    assert calls == [True]
    assert report["status"] == "FAIL"
    assert "CLEANUP_TIMEOUT_INVALID" in {item["code"] for item in report["failures"]}


def test_candidate_soak_cleanup_timeout_is_hard_when_cancellation_is_suppressed():
    journeys = _journeys()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def cleanup(_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            finished.set()

    journeys["cleanup"] = cleanup

    async def run_timeout():
        started = asyncio.get_running_loop().time()
        report = await run_candidate_soak(
            _args(cleanup_timeout_sec=0.01),
            journeys=journeys,
            sample_resources=_samples,
            clock=_Clock(),
        )
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 0.1
        assert report["cleanupVerdict"]["status"] == "FAIL"
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)

    asyncio.run(run_timeout())


def test_candidate_soak_cancellation_still_runs_cleanup_exactly_once():
    calls = []
    journeys = _journeys()

    async def blocked(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def cleanup(args, **kwargs):
        calls.append(True)
        return await _journeys()["cleanup"](args, **kwargs)

    journeys["conversation"] = blocked
    journeys["cleanup"] = cleanup

    async def run_and_cancel():
        task = asyncio.create_task(
            run_candidate_soak(
                _args(),
                journeys=journeys,
                sample_resources=_samples,
                clock=_Clock(),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    assert calls == [True]


def test_candidate_soak_cancellation_during_cleanup_keeps_owned_cleanup_alive():
    journeys = _journeys()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def cleanup(args, **kwargs):
        cleanup_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_finished.set()
        return await _journeys()["cleanup"](args, **kwargs)

    journeys["cleanup"] = cleanup

    async def run_and_cancel():
        task = asyncio.create_task(
            run_candidate_soak(
                _args(cleanup_timeout_sec=0.01),
                journeys=journeys,
                sample_resources=_samples,
                clock=_Clock(),
            )
        )
        await cleanup_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cleanup_finished.wait(), timeout=0.1)

    asyncio.run(run_and_cancel())


def test_candidate_soak_tracks_cancel_suppressing_cleanup_until_drained():
    journeys = _journeys()
    cleanup_started = asyncio.Event()
    release = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def cleanup(_args, **_kwargs):
        cleanup_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            cleanup_finished.set()

    journeys["cleanup"] = cleanup

    async def run_and_cancel():
        task = asyncio.create_task(
            run_candidate_soak(
                _args(cleanup_timeout_sec=0.01),
                journeys=journeys,
                sample_resources=_samples,
                clock=_Clock(),
            )
        )
        await cleanup_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owned = [
            item
            for item in asyncio.all_tasks()
            if item.get_name() == "google-live-candidate-cleanup" and not item.done()
        ]
        assert len(owned) == 1
        release.set()
        await asyncio.wait_for(cleanup_finished.wait(), timeout=0.1)
        await asyncio.sleep(0)
        assert owned[0].done()

    asyncio.run(run_and_cancel())


def test_candidate_soak_blocks_pass_while_prior_owned_cleanup_is_pending():
    release = asyncio.Event()
    first_finished = asyncio.Event()
    first_journeys = _journeys()

    async def stuck_cleanup(_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            first_finished.set()

    first_journeys["cleanup"] = stuck_cleanup

    async def run_twice():
        first = await run_candidate_soak(
            _args(cleanup_timeout_sec=0.01),
            journeys=first_journeys,
            sample_resources=_samples,
            clock=_Clock(),
        )
        second = await run_candidate_soak(
            _args(),
            journeys=_journeys(),
            sample_resources=_samples,
            clock=_Clock(),
        )
        assert first["cleanupVerdict"]["actualPendingCleanupTasks"] == 1
        assert second["status"] == "FAIL"
        assert second["cleanupVerdict"]["actualPendingCleanupTasks"] == 1
        release.set()
        await asyncio.wait_for(first_finished.wait(), timeout=0.1)
        await asyncio.sleep(0)
        third = await run_candidate_soak(
            _args(),
            journeys=_journeys(),
            sample_resources=_samples,
            clock=_Clock(),
        )
        assert third["status"] == "PASS"
        assert third["cleanupVerdict"]["actualPendingCleanupTasks"] == 0

    asyncio.run(run_twice())


def test_candidate_mode_cli_requires_identity_and_evidence_inputs():
    parser = _build_argument_parser()
    incomplete = parser.parse_args(["--mode", "candidate"])
    with pytest.raises(SystemExit):
        _validate_candidate_args(parser, incomplete)

    parsed = parser.parse_args(
        [
            "--mode",
            "candidate",
            "--candidate-git-sha",
            "candidate-sha",
            "--candidate-image-digest",
            f"sha256:{'a' * 64}",
            "--firmware-identity",
            "firmware-v1",
            "--fixture-sha256",
            "b" * 64,
            "--config-json",
            "{}",
            "--baseline-report",
            "baseline.json",
            "--real-api-report",
            "real-api.json",
            "--transport-report",
            "transport.json",
            "--correlated-transport-report",
            "correlated.json",
            "--log-reliability-report",
            "log.json",
            "--journey-evidence",
            "journeys.json",
            "--lesson-manifest",
            "lesson.json",
            "--report",
            "report.json",
        ]
    )
    _validate_candidate_args(parser, parsed)
    assert parsed.minimum_turns == 30
    assert parsed.minimum_duration_sec == 1800
    assert parsed.bargein_cycles == 10


def test_candidate_failure_report_never_persists_exception_or_config_secrets():
    args = _args(config_json='{"apiKey":"secret"}')
    report = _candidate_failure_report(args, RuntimeError("token=do-not-persist"))
    encoded = json.dumps(report)
    assert report["status"] == "FAIL"
    assert report["failures"] == [
        {"code": "CANDIDATE_SOAK_EXECUTION_FAILED", "errorClass": "RuntimeError"}
    ]
    assert "secret" not in encoded
    assert "do-not-persist" not in encoded


def test_candidate_producer_writes_closed_manifest_with_exact_accounting(tmp_path):
    output = tmp_path / "journey-evidence.json"
    args = _args(
        produce_candidate_evidence=output,
        run_id="20260831T100000Z",
    )

    result = asyncio.run(
        produce_candidate_evidence(
            args,
            journeys=_journeys(),
            sample_resources=_samples,
            clock=_Clock(),
        )
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert result["status"] == "PASS"
    assert manifest["name"] == "candidate_soak_evidence_manifest"
    assert [item["name"] for item in manifest["executions"]] == [
        *("conversation" for _ in range(17)),
        *("bargein" for _ in range(10)),
        *("quiet" for _ in range(2)),
        "reopen",
        "reconnect",
        "lesson",
        "conversation_after_lesson",
    ]
    assert len(manifest["executions"]) == 33
    assert manifest["cleanup"]["status"] == "PASS"
    assert len(manifest["resourceSamples"]) == (
        1 + 33 + len(manifest["quietPadding"]) + 1
    )

    replay_args = _args(
        mode="candidate",
        candidate_journeys=None,
        journey_evidence=output,
    )
    replay = asyncio.run(run_soak(replay_args))
    assert replay["status"] == "PASS"
    assert replay["replayCandidateEvidence"] is True
    assert replay["durationSec"] == 1800.0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing", "extra", "wrong_id", "duplicate_id", "bool_count",
        "negative_count", "float_rss", "negative_rss",
    ],
)
def test_candidate_replay_rejects_invalid_resource_sample_contract(
    tmp_path, mutation
):
    manifest = _full_span_manifest()
    sample = manifest["resourceSamples"][0]
    if mutation == "missing":
        sample.pop("fdCount")
    elif mutation == "extra":
        sample["extra"] = 0
    elif mutation == "wrong_id":
        sample["sampleId"] = "resource-1"
    elif mutation == "duplicate_id":
        manifest["resourceSamples"][1]["sampleId"] = sample["sampleId"]
    elif mutation == "bool_count":
        sample["threadCount"] = True
    elif mutation == "negative_count":
        sample["asyncioTaskCount"] = -1
    elif mutation == "float_rss":
        sample["rssBytes"] = 1.5
    else:
        sample["rssBytes"] = -1
    report_path = tmp_path / "candidate-soak" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"status":"PASS","closed":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="resource accounting"):
        asyncio.run(
            run_soak(
                _args(
                    mode="candidate",
                    candidate_journeys=None,
                    journey_evidence=manifest,
                )
            )
        )

    assert report_path.read_text(encoding="utf-8") == (
        '{"status":"PASS","closed":true}\n'
    )


def test_candidate_producer_rejects_invalid_resource_sample_before_publish(tmp_path):
    output = tmp_path / "journey-evidence.json"

    result = asyncio.run(
        produce_candidate_evidence(
            _args(produce_candidate_evidence=output),
            journeys=_journeys(),
            sample_resources=lambda: {**_samples(), "fdCount": False},
            clock=_Clock(),
        )
    )

    assert result["status"] == "FAIL"
    assert not output.exists()


def test_candidate_producer_failure_leaves_no_partial_manifest(tmp_path):
    output = tmp_path / "journey-evidence.json"
    journeys = _journeys()

    async def fail_interrupt(*_args, **_kwargs):
        raise RuntimeError("Authorization: Bearer private")

    journeys["bargein"] = fail_interrupt
    result = asyncio.run(
        produce_candidate_evidence(
            _args(produce_candidate_evidence=output, run_id="20260831T100000Z"),
            journeys=journeys,
            sample_resources=_samples,
            clock=_Clock(),
        )
    )

    assert result["status"] == "FAIL"
    assert not output.exists()
    assert "private" not in json.dumps(result)


def test_candidate_producer_refuses_to_replace_existing_closed_manifest(tmp_path):
    output = tmp_path / "journey-evidence.json"
    output.write_text('{"closed":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="must not already exist"):
        asyncio.run(
            produce_candidate_evidence(
                _args(
                    produce_candidate_evidence=output,
                    run_id="20260831T100000Z",
                ),
                journeys=_journeys(),
                sample_resources=_samples,
                clock=_Clock(),
            )
        )

    assert output.read_text(encoding="utf-8") == '{"closed":true}\n'


@pytest.mark.parametrize("mutation", ["extra_field", "secret_value"])
def test_candidate_manifest_rejects_untrusted_execution_shape_and_values(
    tmp_path, mutation
):
    output = tmp_path / "journey-evidence.json"
    asyncio.run(
        produce_candidate_evidence(
            _args(produce_candidate_evidence=output, run_id="20260831T100000Z"),
            journeys=_journeys(),
            sample_resources=_samples,
            clock=_Clock(),
        )
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    if mutation == "extra_field":
        manifest["executions"][0]["futureField"] = "safe"
    else:
        manifest["executions"][0]["windowId"] = "Authorization: Bearer secret"

    with pytest.raises(ValueError, match="manifest"):
        robot_soak._validate_candidate_manifest_structure(
            manifest,
            identity=IDENTITY,
        )


@pytest.mark.parametrize(
    "secret",
    [
        "AIzaSyA12345678901234567890123456789012",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "AQEAbcdEfghIjklMNopQRstUvwxYZ0123456789",
    ],
)
def test_candidate_manifest_rejects_credential_shaped_allowed_values(secret):
    assert robot_soak._forbidden_evidence_fields({"windowId": secret}) == [
        "windowId"
    ]


@pytest.mark.parametrize(
    "mutation",
    ["candidate_identity", "schema", "name", "extra", "missing"],
)
def test_candidate_replay_rejects_mutated_top_level_manifest(tmp_path, mutation):
    output = tmp_path / "journey-evidence.json"
    args = _args(
        produce_candidate_evidence=output,
        run_id="20260831T100000Z",
    )
    result = asyncio.run(
        produce_candidate_evidence(
            args,
            journeys=_journeys(),
            sample_resources=_samples,
            clock=_Clock(),
        )
    )
    assert result["status"] == "PASS"
    manifest = json.loads(output.read_text(encoding="utf-8"))
    if mutation == "candidate_identity":
        manifest["candidateIdentity"] = {**IDENTITY, "gitSha": "mutated"}
    elif mutation == "schema":
        manifest["schemaVersion"] = "mutated.v1"
    elif mutation == "name":
        manifest["name"] = "mutated"
    elif mutation == "extra":
        manifest["unexpected"] = True
    else:
        manifest.pop("name")

    with pytest.raises(ValueError, match="manifest"):
        asyncio.run(
            run_soak(
                _args(
                    mode="candidate",
                    candidate_journeys=None,
                    journey_evidence=manifest,
                )
            )
        )

    report_path = tmp_path / "candidate-soak" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"status":"PASS","closed":true}\n', encoding="utf-8")
    assert robot_soak._publish_candidate_report(
        report_path, {"status": "FAIL"}
    ) is False
    assert report_path.read_text(encoding="utf-8") == (
        '{"status":"PASS","closed":true}\n'
    )


def test_candidate_replay_report_is_atomic_and_never_overwritten_on_failure(tmp_path):
    report_path = tmp_path / "candidate-soak" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text('{"status":"PASS","closed":true}\n', encoding="utf-8")

    published = robot_soak._publish_candidate_report(
        report_path,
        {"status": "FAIL", "failures": [{"code": "MUTATED_EVIDENCE"}]},
    )

    assert published is False
    assert report_path.read_text(encoding="utf-8") == (
        '{"status":"PASS","closed":true}\n'
    )

    fresh = tmp_path / "fresh" / "report.json"
    assert robot_soak._publish_candidate_report(fresh, {"status": "PASS"}) is True
    assert json.loads(fresh.read_text(encoding="utf-8")) == {"status": "PASS"}


def test_candidate_report_publish_loses_atomic_create_race_without_overwrite(
    tmp_path, monkeypatch
):
    report_path = tmp_path / "report.json"

    def raced_publish(_path, _value):
        report_path.write_text('{"winner":true}\n', encoding="utf-8")
        raise FileExistsError

    monkeypatch.setattr(robot_soak, "_atomic_write_json_exclusive", raced_publish)

    assert robot_soak._publish_candidate_report(
        report_path, {"status": "PASS"}
    ) is False
    assert report_path.read_text(encoding="utf-8") == '{"winner":true}\n'


def test_candidate_exclusive_writer_rejects_parent_symlink(tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(OSError):
        robot_soak._atomic_write_json_exclusive(
            linked_parent / "report.json", {"status": "PASS"}
        )
    assert not (real_parent / "report.json").exists()


def test_candidate_exclusive_writer_rejects_ancestor_symlink(tmp_path):
    real_ancestor = tmp_path / "real"
    (real_ancestor / "nested").mkdir(parents=True)
    linked_ancestor = tmp_path / "linked"
    linked_ancestor.symlink_to(real_ancestor, target_is_directory=True)

    with pytest.raises(OSError):
        robot_soak._atomic_write_json_exclusive(
            linked_ancestor / "nested" / "report.json", {"status": "PASS"}
        )
    assert not (real_ancestor / "nested" / "report.json").exists()


def test_candidate_exclusive_writer_rejects_ancestor_component_swap(
    tmp_path, monkeypatch
):
    ancestor = tmp_path / "evidence"
    (ancestor / "nested").mkdir(parents=True)
    moved = tmp_path / "evidence-original"
    original_open = robot_soak.os.open
    swapped = False

    def swap_then_open(path, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            ancestor.rename(moved)
            (ancestor / "nested").mkdir(parents=True)
            swapped = True
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(robot_soak.os, "open", swap_then_open)

    with pytest.raises(RuntimeError, match="parent changed"):
        robot_soak._atomic_write_json_exclusive(
            ancestor / "nested" / "report.json", {"status": "PASS"}
        )
    assert not (ancestor / "nested" / "report.json").exists()


def test_candidate_exclusive_writer_rejects_ancestor_swap_to_alias_symlink(
    tmp_path, monkeypatch
):
    ancestor = tmp_path / "evidence"
    (ancestor / "nested").mkdir(parents=True)
    moved = tmp_path / "evidence-original"
    original_link = robot_soak.os.link
    swapped = False

    def swap_to_alias_then_link(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            ancestor.rename(moved)
            ancestor.symlink_to(moved, target_is_directory=True)
            swapped = True
        return original_link(*args, **kwargs)

    monkeypatch.setattr(robot_soak.os, "link", swap_to_alias_then_link)

    with pytest.raises(RuntimeError, match="parent changed"):
        robot_soak._atomic_write_json_exclusive(
            ancestor / "nested" / "report.json", {"status": "PASS"}
        )
    assert not (moved / "nested" / "report.json").exists()


def test_candidate_exclusive_writer_rejects_parent_swap(tmp_path, monkeypatch):
    parent = tmp_path / "evidence"
    parent.mkdir()
    moved = tmp_path / "evidence-original"
    original_link = robot_soak.os.link
    swapped = False

    def swap_then_link(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            parent.rename(moved)
            parent.mkdir()
            swapped = True
        return original_link(*args, **kwargs)

    monkeypatch.setattr(robot_soak.os, "link", swap_then_link)

    with pytest.raises(RuntimeError, match="parent changed"):
        robot_soak._atomic_write_json_exclusive(
            parent / "report.json", {"status": "PASS"}
        )
    assert not (parent / "report.json").exists()


def test_candidate_exclusive_writer_does_not_remove_post_link_replacement(
    tmp_path, monkeypatch
):
    parent = tmp_path / "evidence"
    parent.mkdir()
    target = parent / "report.json"
    original_unlink = robot_soak.os.unlink
    replacement_created = False

    def replace_target_after_temp_unlink(path, *args, **kwargs):
        nonlocal replacement_created
        result = original_unlink(path, *args, **kwargs)
        if not replacement_created and str(path).endswith(".tmp"):
            original_unlink(target)
            target.write_text('{"winner":true}\n', encoding="utf-8")
            replacement_created = True
        return result

    monkeypatch.setattr(robot_soak.os, "unlink", replace_target_after_temp_unlink)

    with pytest.raises(RuntimeError, match="identity changed|alias detected"):
        robot_soak._atomic_write_json_exclusive(target, {"status": "PASS"})
    assert target.read_text(encoding="utf-8") == '{"winner":true}\n'


def test_candidate_producer_cancellation_leaves_no_partial_manifest(tmp_path):
    output = tmp_path / "journey-evidence.json"
    journeys = _journeys()
    started = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    journeys["conversation"] = blocked

    async def cancel():
        task = asyncio.create_task(
            produce_candidate_evidence(
                _args(produce_candidate_evidence=output, run_id="20260831T100000Z"),
                journeys=journeys,
                sample_resources=_samples,
                clock=_Clock(),
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert not output.exists()


def test_candidate_producer_factory_has_only_consumed_journey_keys():
    args = _args(run_id="20260831T100000Z")
    args.candidate_journey_driver = lambda *_args, **_kwargs: None

    assert set(build_candidate_journeys(args)) == {
        "conversation",
        "bargein",
        "quiet",
        "reopen",
        "reconnect",
        "lesson",
        "monitor",
        "cleanup",
    }


def test_candidate_protected_input_is_exact_private_json_with_distinct_safe_fixtures(tmp_path):
    stream, private = _protected_candidate_input(tmp_path)
    output = tmp_path / "evidence.json"

    protected = robot_soak._read_candidate_protected_input(
        stream,
        output_paths=(output,),
        sample_rate=24000,
    )

    assert protected.bargein_initial.label == "bargein/initial"
    assert protected.bargein_newest.label == "bargein/newest"
    assert protected.robot_speaking.label == "robot_speaking/trigger"
    rendered = repr(protected)
    assert private["bargein"]["initialExpected"] not in rendered
    assert private["bargein"]["newestExpected"] not in rendered


@pytest.mark.parametrize("mutation", ["missing", "unknown", "symlink", "duplicate", "equal_text", "copied_text", "bad_wav", "output_alias"])
def test_candidate_protected_input_fails_closed_for_unsafe_documents(tmp_path, mutation):
    stream, document = _protected_candidate_input(tmp_path)
    output = tmp_path / "evidence.json"
    if mutation == "missing":
        stream = io.BytesIO(b"")
    elif mutation == "unknown":
        document["unexpected"] = True
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "symlink":
        alias = tmp_path / "alias.wav"
        alias.symlink_to(tmp_path / "initial.wav")
        document["bargein"]["initialAudioPath"] = str(alias)
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "duplicate":
        document["bargein"]["newestAudioPath"] = document["bargein"]["initialAudioPath"]
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "equal_text":
        document["bargein"]["newestExpected"] = document["bargein"]["initialExpected"]
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "copied_text":
        document["bargein"]["newestExpected"] = " private--initial   intent "
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "bad_wav":
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"not-wave")
        document["robotSpeaking"]["triggerAudioPath"] = str(bad)
        stream = io.BytesIO(json.dumps(document).encode())
    elif mutation == "output_alias":
        output = tmp_path / "initial.wav"

    with pytest.raises(ValueError, match="protected candidate input"):
        robot_soak._read_candidate_protected_input(
            stream,
            output_paths=(output,),
            sample_rate=24000,
        )


def test_candidate_protected_input_seals_plans_and_drops_expected_plaintext(tmp_path):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )

    protected.seal_bargein_plans(10)

    assert protected.initial_expected is None
    assert protected.newest_expected is None
    assert len(protected.bargein_plans) == 10
    assert len({bytes(plan.key) for plan in protected.bargein_plans}) == 10


def test_candidate_plan_seal_failure_zeroizes_prior_plans_and_all_private_input(tmp_path, monkeypatch):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )
    private_buffers = [
        protected.bargein_initial.pcm,
        protected.bargein_newest.pcm,
        protected.robot_speaking.pcm,
        protected.initial_expected,
        protected.newest_expected,
    ]
    created = []
    original_plan = robot_soak._SealedBargeinPlan

    def capture_plan(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        created.append(plan)
        return plan

    calls = 0
    original_token_bytes = robot_soak.secrets.token_bytes

    def fail_after_two(size):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected seal failure")
        return original_token_bytes(size)

    monkeypatch.setattr(robot_soak, "_SealedBargeinPlan", capture_plan)
    monkeypatch.setattr(robot_soak.secrets, "token_bytes", fail_after_two)

    with pytest.raises(RuntimeError, match="injected"):
        protected.seal_bargein_plans(10)

    assert protected.bargein_plans == []
    assert protected.initial_expected is None
    assert protected.newest_expected is None
    assert all(not any(private) for private in private_buffers if private is not None)
    assert all(
        not any(private)
        for plan in created
        for private in (plan.key, plan.initial_mac, plan.newest_mac)
    )


@pytest.mark.parametrize("failure", ["initial_hmac", "newest_hmac", "constructor"])
def test_candidate_plan_seal_zeroizes_untransferred_current_key(tmp_path, monkeypatch, failure):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )
    keys = []
    macs = []

    def new_key():
        key = bytearray(bytes([len(keys) + 1]) * 32)
        keys.append(key)
        return key

    hmac_calls = 0
    original_hmac = robot_soak._candidate_hmac_hex

    def failing_hmac(key, value):
        nonlocal hmac_calls
        hmac_calls += 1
        if (failure == "initial_hmac" and hmac_calls == 3) or (
            failure == "newest_hmac" and hmac_calls == 4
        ):
            raise RuntimeError("injected hmac failure")
        mac = original_hmac(key, value)
        macs.append(mac)
        return mac

    original_plan = robot_soak._SealedBargeinPlan
    constructor_calls = 0

    def failing_plan(*args, **kwargs):
        nonlocal constructor_calls
        constructor_calls += 1
        if failure == "constructor" and constructor_calls == 2:
            raise RuntimeError("injected constructor failure")
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(robot_soak, "_new_candidate_semantic_key", new_key)
    monkeypatch.setattr(robot_soak, "_candidate_hmac_hex", failing_hmac)
    monkeypatch.setattr(robot_soak, "_SealedBargeinPlan", failing_plan)

    with pytest.raises(RuntimeError, match="injected"):
        protected.seal_bargein_plans(10)

    assert keys
    assert all(not any(key) for key in keys)
    assert all(not any(mac) for mac in macs)
    assert protected.bargein_plans == []
    assert protected.initial_expected is None
    assert protected.newest_expected is None


def test_candidate_hmac_renderer_zeroizes_partial_output_on_interrupt(monkeypatch):
    captured = []

    def interrupt(index, rendered):
        captured.append(rendered)
        if index == 7:
            raise KeyboardInterrupt

    monkeypatch.setattr(robot_soak, "_candidate_hmac_render_checkpoint", interrupt)

    with pytest.raises(KeyboardInterrupt):
        robot_soak._candidate_hmac_hex(bytearray(b"k" * 32), bytearray(b"intent"))

    assert captured
    assert not any(captured[-1])


def test_candidate_producer_cancellation_zeroizes_all_remaining_sealed_plans(tmp_path, monkeypatch):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )
    protected.seal_bargein_plans(10)
    private_buffers = [
        private
        for plan in protected.bargein_plans
        for private in (plan.key, plan.initial_mac, plan.newest_mac)
    ]
    monkeypatch.setattr(robot_soak, "_read_candidate_protected_input", lambda *_args, **_kwargs: protected)
    monkeypatch.setattr(robot_soak, "build_candidate_journeys", lambda *_args, **_kwargs: {})

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(robot_soak, "run_candidate_soak", cancelled)
    args = _args(
        produce_candidate_evidence=tmp_path / "evidence.json",
        candidate_protected_stdin=io.BytesIO(b"{}"),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(produce_candidate_evidence(args))
    assert protected.bargein_plans == []
    assert all(not any(private) for private in private_buffers)


def test_candidate_wav_oversize_is_rejected_before_readframes(tmp_path):
    fixture = tmp_path / "oversize.wav"
    fixture.write_bytes(b"placeholder")

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        getnchannels = lambda self: 1
        getsampwidth = lambda self: 2
        getframerate = lambda self: 24000
        getcomptype = lambda self: "NONE"
        getnframes = lambda self: 20_000_000

        def readframes(self, _count):
            raise AssertionError("oversize WAV must be rejected before readframes")

    with patch.object(robot_soak.wave, "open", return_value=Source()):
        with pytest.raises(ValueError, match="unsupported"):
            robot_soak._read_protected_wav(
                fixture, sample_rate=24000, label="bargein/initial"
            )


@pytest.mark.parametrize(
    "relative_target",
    [
        "executions/01-conversation.json",
        "executions/34-quiet_padding.json",
        "cleanup/candidate-soak.20260831T100000Z.34.json",
    ],
)
def test_candidate_protected_fixture_cannot_alias_any_future_generated_output(
    tmp_path, relative_target
):
    output = tmp_path / "manifest.json"
    target = tmp_path / relative_target
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_pcm_wav(target)
    stream, document = _protected_candidate_input(tmp_path)
    document["bargein"]["initialAudioPath"] = str(target)
    stream = io.BytesIO(json.dumps(document).encode())
    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=output,
        maximum_padding_windows=60,
    )

    with pytest.raises(ValueError, match="output alias"):
        robot_soak._read_candidate_protected_input(
            stream,
            output_paths=robot_soak._candidate_generated_output_paths(args, output),
            sample_rate=24000,
        )


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), 601])
def test_candidate_quiet_duration_rejected_before_enrollment(tmp_path, duration):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )
    calls = []
    args = _args(
        run_id="20260831T100000Z",
        idle_duration_sec=duration,
        produce_candidate_evidence=tmp_path / "evidence.json",
        candidate_control_json=lambda *items, **_kwargs: calls.append(items),
    )

    with pytest.raises(ValueError, match="quiet duration"):
        asyncio.run(
            build_candidate_journeys(args, protected_input=protected)["quiet"](
                args, name="quiet", index=1
            )
        )
    assert calls == []


def test_candidate_quiet_rejects_short_semantic_observation(tmp_path):
    with pytest.raises(RuntimeError, match="semantic evidence"):
        robot_soak._candidate_semantic_counters(
            "quiet",
            {
                "status": "PASS",
                "candidateSemanticEvidence": {
                    "status": "PASS",
                    "kind": "quiet",
                    "mode": "silence",
                    "durationMs": 119000,
                    "responseGeneration": None,
                    "responseDurationMs": 0,
                    "outputChunks": 0,
                    "falseInterrupts": 0,
                    "responseStarts": 0,
                    "responseEnds": 0,
                    "replacements": 0,
                    "fallbacks": 0,
                },
            },
            quiet_mode="silence",
            requested_duration_sec=120,
            window_duration_sec=120,
        )


def test_candidate_robot_speaking_accepts_short_bounded_terminal_response():
    counters = robot_soak._candidate_semantic_counters(
        "quiet",
        {
            "status": "PASS",
            "candidateSemanticEvidence": {
                "status": "PASS",
                "kind": "quiet",
                "mode": "robot_speaking",
                "durationMs": 2400,
                "responseGeneration": 1,
                "responseDurationMs": 2400,
                "outputChunks": 1,
                "falseInterrupts": 0,
                "responseStarts": 1,
                "responseEnds": 1,
                "replacements": 0,
                "fallbacks": 0,
            },
        },
        quiet_mode="robot_speaking",
        requested_duration_sec=120,
        window_duration_sec=3.0,
    )

    assert counters == {"latestIntentSuccesses": 0, "falseInterrupts": 0}


def test_candidate_replay_rejects_mutated_robot_speaking_persisted_duration():
    journeys = _journeys()
    robot_speaking = asyncio.run(journeys["quiet"](_args(), name="quiet", index=2))
    robot_speaking["observationDurationSec"] = 39.999

    assert robot_soak._validated_execution_server_scope(
        robot_speaking, identity=IDENTITY
    ) is None


def test_candidate_semantic_verdicts_are_derived_only_from_exact_bound_analyzer():
    bargein = robot_soak._candidate_semantic_counters(
        "bargein",
        {
            "status": "PASS",
            "candidateSemanticEvidence": {
                "status": "PASS",
                "kind": "bargein-intent",
                "initialSlotMatched": True,
                "newestSlotMatched": True,
                "orderingValid": True,
                "latestIntentMatched": True,
                "replacementOwnedByNewestGeneration": True,
            },
        },
    )
    quiet = robot_soak._candidate_semantic_counters(
        "quiet",
        {
            "status": "PASS",
            "candidateSemanticEvidence": {
                "status": "PASS",
                "kind": "quiet",
                "mode": "silence",
                "durationMs": 120000,
                "responseGeneration": None,
                "responseDurationMs": 0,
                "outputChunks": 0,
                "falseInterrupts": 0,
                "responseStarts": 0,
                "responseEnds": 0,
                "replacements": 0,
                "fallbacks": 0,
            },
        },
        quiet_mode="silence",
        requested_duration_sec=120,
        window_duration_sec=120,
    )

    assert bargein == {"latestIntentSuccesses": 1, "falseInterrupts": 0}
    assert quiet == {"latestIntentSuccesses": 0, "falseInterrupts": 0}
    with pytest.raises(RuntimeError, match="semantic evidence"):
        robot_soak._candidate_semantic_counters(
            "bargein",
            {
                "status": "PASS",
                "candidateSemanticEvidence": {
                    "status": "PASS",
                    "kind": "bargein-intent",
                    "initialSlotMatched": True,
                    "newestSlotMatched": True,
                    "orderingValid": True,
                    "latestIntentMatched": False,
                    "replacementOwnedByNewestGeneration": True,
                },
            },
        )


def test_candidate_factory_enrolls_binds_runs_finalizes_then_analyzes(tmp_path):
    events = []

    scope = _candidate_scope("candidate-soak.20260831T100000Z.1", "bargein")

    async def control(method, url, payload=None):
        events.append((method, url.rsplit("/", 1)[-1], payload))
        if url.endswith("/finalize"):
            return _candidate_finalize(scope)
        return {"status": "PASS"}

    async def driver(_args, **context):
        events.append(("driver", context["journey_id"], None))
        return {
            "name": context["name"],
            "status": "PASS",
            "evidenceScope": scope,
        }

    async def analyzer(*, journey_id, output_path):
        events.append(("analyzer", journey_id, output_path.name))
        return {
            "name": "google_live_log_reliability",
            "status": "PASS",
            "journeyType": "bargein",
            "evidenceScope": {
                "journeyType": "bargein",
                "proofProfile": "candidate-lifecycle",
            },
        }

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        candidate_log_analyzer=analyzer,
    )
    journey = build_candidate_journeys(args)["bargein"]

    result = asyncio.run(journey(args, name="bargein", index=1))

    journey_id = "candidate-soak.20260831T100000Z.1"
    assert [event[0] for event in events] == [
        "POST", "PUT", "driver", "POST", "analyzer"
    ]
    assert events[2][1] == journey_id
    assert events[0][2] == {
        "clientId": "robot-client",
        "journeyId": journey_id,
        "ttlSec": 3600,
        "journeyType": "bargein",
        "proofProfile": "candidate-lifecycle",
    }
    assert result["task5LogEvidence"]["status"] == "PASS"
    assert "secret" not in json.dumps(events)


def test_candidate_enrollment_cancel_after_ambiguous_commit_deletes_once_and_propagates(tmp_path):
    stream, _private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream, output_paths=(), sample_rate=24000
    )
    committed = asyncio.Event()
    release_response = asyncio.Event()
    deletes = 0
    registry = set()

    async def control(method, url, payload=None):
        nonlocal deletes
        if method == "POST":
            registry.add(payload["journeyId"])
            committed.set()
            await release_response.wait()
            return {"status": "PASS"}
        if method == "DELETE":
            deletes += 1
            registry.discard(url.rsplit("/", 1)[-1])
            return {"status": "PASS"}
        return {"status": "PASS"}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        cleanup_timeout_sec=0.05,
    )
    journeys = build_candidate_journeys(args, protected_input=protected)
    attempted_plan = protected.bargein_plans[0]

    async def cancel():
        task = asyncio.create_task(journeys["bargein"](args, name="bargein", index=1))
        await committed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert deletes == 0
        assert registry
        assert not any(attempted_plan.key)
        assert not args.produce_candidate_evidence.exists()
        release_response.set()
        for _attempt in range(20):
            if not registry:
                break
            await asyncio.sleep(0)
        assert deletes == 1
        assert registry == set()

    asyncio.run(cancel())


def test_candidate_ambiguous_enrollment_delete_timeout_is_bounded(tmp_path):
    post_started = asyncio.Event()
    release_post = asyncio.Event()
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()

    async def control(method, _url, _payload=None):
        if method == "POST":
            post_started.set()
            await release_post.wait()
        elif method == "DELETE":
            delete_started.set()
            try:
                await release_delete.wait()
            except asyncio.CancelledError:
                await release_delete.wait()
        return {"status": "PASS"}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        cleanup_timeout_sec=0.01,
    )
    journey = build_candidate_journeys(args)["conversation"]

    async def cancel():
        task = asyncio.create_task(journey(args, name="conversation", index=1))
        await post_started.wait()
        started = asyncio.get_running_loop().time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.get_running_loop().time() - started < 0.2
        assert not delete_started.is_set()
        release_post.set()
        await asyncio.wait_for(delete_started.wait(), timeout=0.2)
        release_delete.set()
        await asyncio.sleep(0)

    asyncio.run(cancel())


def test_candidate_reconciliation_deletes_after_late_post_commit(tmp_path):
    post_started = asyncio.Event()
    release_commit = asyncio.Event()
    registry = set()
    deletes = 0

    async def control(method, url, payload=None):
        nonlocal deletes
        if method == "POST":
            post_started.set()
            await release_commit.wait()
            registry.add(payload["journeyId"])
            return {"status": "PASS"}
        if method == "DELETE":
            deletes += 1
            registry.discard(url.rsplit("/", 1)[-1])
            return {"status": "PASS"}
        return {"status": "PASS"}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        cleanup_timeout_sec=0.01,
    )
    journey = build_candidate_journeys(args)["conversation"]

    async def cancel():
        task = asyncio.create_task(journey(args, name="conversation", index=1))
        await post_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert deletes == 0
        assert any(
            owned.get_name() == "google-live-candidate-enrollment-cleanup"
            for owned in robot_soak._OWNED_CLEANUP_TASKS
        )
        release_commit.set()
        for _attempt in range(20):
            if deletes:
                break
            await asyncio.sleep(0)
        assert deletes == 1
        assert registry == set()
        await asyncio.sleep(0)
        assert not any(
            owned.get_name() == "google-live-candidate-enrollment-cleanup"
            for owned in robot_soak._OWNED_CLEANUP_TASKS
        )

    asyncio.run(cancel())


def test_candidate_failed_post_treats_delete_not_found_as_safe_cleanup(tmp_path):
    deletes = 0

    async def control(method, _url, _payload=None):
        nonlocal deletes
        if method == "POST":
            raise RuntimeError("post did not commit")
        if method in {"DELETE", "GET"}:
            deletes += 1
            raise robot_soak._EvidenceControlNotFound("not found")
        return {"status": "PASS"}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
    )

    with pytest.raises(RuntimeError, match="post did not commit"):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )
    assert deletes == 2
    assert not args.produce_candidate_evidence.exists()


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (
        ("delete_failure", "journey failed"),
        ("malformed_delete", "candidate enrollment cleanup failed"),
        ("get_identity_mismatch", "candidate enrollment cleanup failed"),
        ("get_active", "candidate enrollment cleanup failed"),
    ),
)
def test_candidate_ambiguous_enrollment_cleanup_requires_verified_terminal_state(
    tmp_path, case, expected_error
):
    journey_id = "candidate-soak.20260831T100000Z.1"
    terminal = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }
    calls = []

    async def control(method, _url, payload=None):
        calls.append(method)
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            if case == "delete_failure":
                raise RuntimeError("private delete detail")
            if case == "malformed_delete":
                return {"status": "PASS"}
            return dict(terminal)
        if method == "GET":
            if case == "get_identity_mismatch":
                return {**terminal, "journeyId": "other"}
            if case == "get_active":
                return {**terminal, "status": "ACTIVE"}
            return dict(terminal)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )

    assert calls.count("DELETE") == (3 if case == "get_active" else 1)
    assert calls.count("GET") == (
        0 if case == "malformed_delete" else 3 if case == "get_active" else 1
    )
    assert not args.produce_candidate_evidence.exists()


@pytest.mark.parametrize(
    ("get_case", "expected_error"),
    (
        ("active", "candidate enrollment cleanup failed"),
        ("malformed", "candidate enrollment cleanup failed"),
        ("mismatch", "candidate enrollment cleanup failed"),
        ("terminal", "post response lost"),
    ),
)
def test_candidate_post_commit_with_local_failure_always_reconciles_get(
    tmp_path, get_case, expected_error
):
    journey_id = "candidate-soak.20260831T100000Z.1"
    terminal = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }
    registry = {journey_id: "ACTIVE"}
    calls = []

    async def control(method, _url, payload=None):
        calls.append(method)
        if method == "POST":
            registry[payload["journeyId"]] = "ACTIVE"
            raise RuntimeError("post response lost")
        if method == "DELETE":
            raise robot_soak._EvidenceControlNotFound("delete response ambiguous")
        if method == "GET":
            if get_case == "active":
                return {**terminal, "status": "ACTIVE"}
            if get_case == "malformed":
                return {"status": "FAIL"}
            if get_case == "mismatch":
                return {**terminal, "journeyId": "other"}
            registry[journey_id] = "FAIL"
            return dict(terminal)
        return {"status": "PASS"}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )

    expected_attempts = 3 if get_case == "active" else 1
    assert calls.count("DELETE") == expected_attempts
    assert calls.count("GET") == expected_attempts
    if get_case == "terminal":
        assert registry[journey_id] == "FAIL"


def test_candidate_cleanup_timeout_is_explicit_and_reconciliation_drains(
    tmp_path
):
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()
    terminal = {
        "journeyId": "candidate-soak.20260831T100000Z.1",
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }

    async def control(method, _url, payload=None):
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            delete_started.set()
            await release_delete.wait()
            return dict(terminal)
        if method == "GET":
            return dict(terminal)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        cleanup_timeout_sec=0.01,
    )

    async def run():
        with pytest.raises(RuntimeError, match="candidate enrollment cleanup failed"):
            await build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        assert delete_started.is_set()
        for _attempt in range(20):
            if not any(
                task.get_name() == "google-live-candidate-enrollment-cleanup"
                for task in robot_soak._OWNED_CLEANUP_TASKS
            ):
                break
            await asyncio.sleep(0)
        release_delete.set()
        assert not any(
            task.get_name() == "google-live-candidate-enrollment-cleanup"
            for task in robot_soak._OWNED_CLEANUP_TASKS
        )

    asyncio.run(run())


def test_candidate_active_cleanup_retries_delete_until_terminal(tmp_path):
    journey_id = "candidate-soak.20260831T100000Z.1"
    active = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "ACTIVE",
    }
    terminal = {
        **active,
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }
    deletes = 0
    gets = 0

    async def control(method, _url, payload=None):
        nonlocal deletes, gets
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            deletes += 1
            if deletes == 1:
                raise RuntimeError("transport failed before server")
            return dict(terminal)
        if method == "GET":
            gets += 1
            return dict(active if deletes == 1 else terminal)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        cleanup_timeout_sec=0.2,
    )

    with pytest.raises(RuntimeError, match="journey failed"):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )

    assert deletes == 2
    assert gets == 2


def test_candidate_active_cleanup_retry_exhaustion_is_explicit(tmp_path):
    journey_id = "candidate-soak.20260831T100000Z.1"
    active = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "ACTIVE",
    }
    deletes = 0

    async def control(method, _url, payload=None):
        nonlocal deletes
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            deletes += 1
            raise RuntimeError("delete unavailable")
        if method == "GET":
            return dict(active)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        cleanup_timeout_sec=0.05,
    )

    with pytest.raises(RuntimeError, match="candidate enrollment cleanup failed"):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )

    assert 1 < deletes <= 4
    assert not robot_soak._OWNED_CLEANUP_TASKS


def test_candidate_cancellation_during_active_retry_keeps_cleanup_owned(tmp_path):
    journey_id = "candidate-soak.20260831T100000Z.1"
    active = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "ACTIVE",
    }
    terminal = {
        **active,
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }
    retry_started = asyncio.Event()
    release_retry = asyncio.Event()
    deletes = 0

    async def control(method, _url, payload=None):
        nonlocal deletes
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            deletes += 1
            if deletes == 1:
                raise RuntimeError("first delete failed")
            retry_started.set()
            await release_retry.wait()
            return dict(terminal)
        if method == "GET":
            return dict(active if deletes == 1 else terminal)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        cleanup_timeout_sec=0.2,
    )

    async def run():
        task = asyncio.create_task(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )
        await retry_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(
            owned.get_name() == "google-live-candidate-enrollment-cleanup"
            for owned in robot_soak._OWNED_CLEANUP_TASKS
        )
        release_retry.set()
        for _attempt in range(20):
            if not robot_soak._OWNED_CLEANUP_TASKS:
                break
            await asyncio.sleep(0)
        assert not robot_soak._OWNED_CLEANUP_TASKS
        assert deletes == 2

    asyncio.run(run())


def test_candidate_cleanup_sleep_exhausts_deadline_before_next_delete(tmp_path):
    journey_id = "candidate-soak.20260831T100000Z.1"
    active = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "ACTIVE",
    }
    clock_values = deque((0.0, 0.0, 0.0, 0.0, 0.04, 0.06))
    deletes = 0
    gets = 0

    def cleanup_clock():
        if len(clock_values) > 1:
            return clock_values.popleft()
        return clock_values[0]

    async def control(method, _url, payload=None):
        nonlocal deletes, gets
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == "DELETE":
            deletes += 1
            raise RuntimeError("delete unavailable")
        if method == "GET":
            gets += 1
            return dict(active)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        candidate_cleanup_clock=cleanup_clock,
        cleanup_timeout_sec=0.05,
    )

    with pytest.raises(RuntimeError, match="candidate enrollment cleanup failed"):
        asyncio.run(
            build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        )

    assert deletes == 1
    assert gets == 1


@pytest.mark.parametrize("slow_method", ("DELETE", "GET"))
def test_candidate_cleanup_bounds_each_request_by_remaining_deadline(
    tmp_path, slow_method
):
    journey_id = "candidate-soak.20260831T100000Z.1"
    active = {
        "journeyId": journey_id,
        "journeyType": "conversation",
        "proofProfile": "candidate-lifecycle",
        "status": "ACTIVE",
    }
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []

    async def block():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def control(method, _url, payload=None):
        calls.append(method)
        if method == "POST":
            return {"data": {"registered": True, "journeyId": payload["journeyId"]}}
        if method == slow_method:
            await block()
        if method == "DELETE":
            raise RuntimeError("delete ambiguous")
        if method == "GET":
            return dict(active)
        return {"status": "PASS"}

    async def driver(_args, **_context):
        raise RuntimeError("journey failed")

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        cleanup_timeout_sec=0.02,
    )

    async def run():
        with pytest.raises(RuntimeError, match="candidate enrollment cleanup failed"):
            await build_candidate_journeys(args)["conversation"](
                args, name="conversation", index=1
            )
        assert started.is_set()
        for _attempt in range(20):
            if cancelled.is_set() and not robot_soak._OWNED_CLEANUP_TASKS:
                break
            await asyncio.sleep(0)
        assert cancelled.is_set()
        assert not robot_soak._OWNED_CLEANUP_TASKS
        if slow_method == "DELETE":
            assert "GET" not in calls
        else:
            assert calls.count("DELETE") == 1
            assert calls.count("GET") == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "fail",
        "missing_status",
        "wrong_type",
        "wrong_scope",
        "wrong_profile",
        "wrong_journey_type",
        "retryable",
        "malformed",
        "scope_missing_field",
        "scope_extra_field",
        "empty_connection_id",
        "empty_live_id",
        "none_initial_live_id",
        "none_final_live_id",
        "wrong_peer_hash",
        "zero_window",
        "reversed_window",
        "invalid_transition_chain",
    ],
)
def test_candidate_factory_rejects_incomplete_finalize_before_analyzer(
    tmp_path, case
):
    analyzer_calls = 0
    journey_id = "candidate-soak.20260831T100000Z.1"
    scope = _candidate_scope(journey_id, "bargein")
    finalized = _candidate_finalize(scope)
    if case == "fail":
        finalized.update(status="FAIL", failureCode="EVIDENCE_CLEANUP_INCOMPLETE")
    elif case == "missing_status":
        finalized.pop("status")
    elif case == "wrong_type":
        finalized["type"] = "evidence_pending"
    elif case == "wrong_scope":
        finalized["evidenceScope"] = {**scope, "journeyId": "other"}
    elif case == "wrong_profile":
        finalized["evidenceScope"] = {**scope, "proofProfile": "physical-transcript"}
    elif case == "wrong_journey_type":
        finalized["evidenceScope"] = {**scope, "journeyType": "quiet"}
    elif case == "retryable":
        finalized["retryable"] = True
    elif case == "malformed":
        finalized = "malformed"
    elif case == "scope_missing_field":
        finalized["evidenceScope"] = dict(scope)
        finalized["evidenceScope"].pop("connectionId")
    elif case == "scope_extra_field":
        finalized["evidenceScope"] = {**scope, "unexpected": "value"}
    elif case == "empty_connection_id":
        finalized["evidenceScope"] = {**scope, "connectionId": ""}
    elif case == "empty_live_id":
        finalized["evidenceScope"] = {**scope, "liveConnectionId": ""}
    elif case == "none_initial_live_id":
        finalized["evidenceScope"] = {**scope, "initialLiveConnectionId": None}
    elif case == "none_final_live_id":
        finalized["finalLiveConnectionId"] = None
    elif case == "wrong_peer_hash":
        finalized["evidenceScope"] = {**scope, "peerIdentityHash": "sha256:ABC"}
    elif case == "zero_window":
        finalized["serverEndUtc"] = scope["serverStartUtc"]
    elif case == "reversed_window":
        finalized["serverEndUtc"] = "2026-08-31T09:59:59+00:00"
    elif case == "invalid_transition_chain":
        finalized["finalLiveConnectionId"] = "live-2"
        finalized["liveConnectionTransitions"] = [
            {
                "attempt": 1,
                "fromLiveConnectionId": "foreign-live",
                "toLiveConnectionId": "live-2",
            }
        ]
    driver_scope = (
        finalized.get("evidenceScope")
        if isinstance(finalized, dict)
        and case
        in {
            "scope_missing_field",
            "scope_extra_field",
            "empty_connection_id",
            "empty_live_id",
            "none_initial_live_id",
            "wrong_peer_hash",
        }
        else scope
    )

    cleanup_terminal = {
        "journeyId": journey_id,
        "journeyType": "bargein",
        "proofProfile": "candidate-lifecycle",
        "status": "FAIL",
        "failureCode": "OPERATOR_CANCELLED",
    }

    async def control(method, url, _payload=None):
        if url.endswith("/finalize"):
            return finalized
        if method in {"DELETE", "GET"}:
            return dict(cleanup_terminal)
        return {"status": "PASS"}

    async def analyzer(**_kwargs):
        nonlocal analyzer_calls
        analyzer_calls += 1
        return {}

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=lambda *_args, **_kwargs: {
            "name": "bargein",
            "status": "PASS",
            "evidenceScope": driver_scope,
        },
        candidate_log_analyzer=analyzer,
    )

    with pytest.raises(RuntimeError, match="finalization"):
        asyncio.run(
            build_candidate_journeys(args)["bargein"](
                args, name="bargein", index=1
            )
        )
    assert analyzer_calls == 0
    assert not args.produce_candidate_evidence.exists()


def test_candidate_factory_posts_exact_lifecycle_claims_for_all_33_executions(
    tmp_path,
):
    posts = []

    async def control(method, url, payload=None):
        if method == "POST" and isinstance(payload, dict) and "clientId" in payload:
            posts.append(deepcopy(payload))
        if url.endswith("/finalize"):
            body = posts[-1]
            return _candidate_finalize(
                _candidate_scope(body["journeyId"], body["journeyType"])
            )
        return {"status": "PASS"}

    async def driver(_args, **context):
        return {
            "name": context["name"],
            "status": "PASS",
            "evidenceScope": _candidate_scope(
                context["journey_id"], context["name"]
            ),
        }

    async def analyzer(*, journey_id, **_kwargs):
        stage = posts[-1]["journeyType"]
        return {
            "name": "google_live_log_reliability",
            "status": "PASS",
            "journeyType": stage,
            "evidenceScope": {
                "journeyId": journey_id,
                "journeyType": stage,
                "proofProfile": "candidate-lifecycle",
            },
            "journeyLatencyEvidence": {},
        }

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        candidate_log_analyzer=analyzer,
    )
    journeys = build_candidate_journeys(args)

    async def run_all():
        for stage, count in (
            ("conversation", 17),
            ("bargein", 10),
            ("quiet", 2),
            ("reopen", 1),
            ("reconnect", 1),
            ("lesson", 1),
            ("conversation_after_lesson", 1),
        ):
            callable_name = (
                "conversation" if stage == "conversation_after_lesson" else stage
            )
            for index in range(1, count + 1):
                await journeys[callable_name](args, name=stage, index=index)

    asyncio.run(run_all())

    expected_stages = [
        *(["conversation"] * 17),
        *(["bargein"] * 10),
        *(["quiet"] * 2),
        "reopen",
        "reconnect",
        "lesson",
        "conversation_after_lesson",
    ]
    assert posts == [
        {
            "clientId": "robot-client",
            "journeyId": f"candidate-soak.20260831T100000Z.{sequence}",
            "ttlSec": 3600,
            "journeyType": stage,
            "proofProfile": "candidate-lifecycle",
        }
        for sequence, stage in enumerate(expected_stages, start=1)
    ]
    encoded = json.dumps(posts).lower()
    assert "transcript" not in encoded
    assert "hmac" not in encoded
    assert "key" not in encoded
    assert "mac" not in encoded


def test_candidate_factory_posts_fresh_semantic_plans_and_derives_safe_verdicts(tmp_path):
    stream, private = _protected_candidate_input(tmp_path)
    protected = robot_soak._read_candidate_protected_input(
        stream,
        output_paths=(tmp_path / "evidence.json",),
        sample_rate=24000,
    )
    posts = []

    async def control(method, url, payload=None):
        if method == "POST" and isinstance(payload, dict) and "clientId" in payload:
            posts.append(deepcopy(payload))
        if url.endswith("/finalize"):
            body = posts[-1]
            return _candidate_finalize(
                _candidate_scope(body["journeyId"], body["journeyType"])
            )
        return {"status": "PASS"}

    async def driver(_args, **context):
        scope = _candidate_scope(context["journey_id"], context["name"])
        return {
            "name": context["name"],
            "status": "PASS",
            "evidenceScope": scope,
            "logWindow": {
                "windowId": context["journey_id"],
                "start": scope["serverStartUtc"],
                "end": "2026-08-31T10:01:00+00:00",
            },
            "latestIntentSuccesses": 0,
            "falseInterrupts": 99,
        }

    async def analyzer(*, journey_id, **_kwargs):
        stage = posts[-1]["journeyType"]
        semantic = (
            {
                "status": "PASS",
                "kind": "bargein-intent",
                "initialSlotMatched": True,
                "newestSlotMatched": True,
                "orderingValid": True,
                "latestIntentMatched": True,
                "replacementOwnedByNewestGeneration": True,
            }
            if stage == "bargein"
            else {
                "status": "PASS",
                "kind": "quiet",
                "mode": "silence",
                "durationMs": 60000,
                "responseGeneration": None,
                "responseDurationMs": 0,
                "outputChunks": 0,
                "falseInterrupts": 0,
                "responseStarts": 0,
                "responseEnds": 0,
                "replacements": 0,
                "fallbacks": 0,
            }
        )
        return {
            "name": "google_live_log_reliability",
            "status": "PASS",
            "journeyType": stage,
            "candidateIdentity": IDENTITY,
            "serverIssued": True,
            "evidenceScope": _candidate_scope(journey_id, stage),
            "logWindow": {
                "windowId": journey_id,
                "start": "2026-08-31T10:00:00+00:00",
                "end": "2026-08-31T10:01:00+00:00",
            },
            "candidateSemanticEvidence": semantic,
            "journeyLatencyEvidence": {},
        }

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        candidate_log_analyzer=analyzer,
        idle_duration_sec=60.0,
    )
    journeys = build_candidate_journeys(args, protected_input=protected)

    bargein = asyncio.run(journeys["bargein"](args, name="bargein", index=1))
    asyncio.run(journeys["bargein"](args, name="bargein", index=2))
    quiet = asyncio.run(journeys["quiet"](args, name="quiet", index=1))

    intent = posts[0]["semanticProof"]
    assert intent["version"] == "google-live-candidate-intent-nfkc-casefold.v1"
    assert [item["role"] for item in intent["intentPlan"]] == ["initial", "newest"]
    assert private["bargein"]["initialExpected"] not in json.dumps(posts)
    assert private["bargein"]["newestExpected"] not in json.dumps(posts)
    assert posts[0]["semanticProof"]["hmacKeyBase64"] != posts[1]["semanticProof"]["hmacKeyBase64"]
    assert posts[2]["semanticProof"] == {
        "version": "google-live-candidate-quiet.v1",
        "mode": "silence",
    }
    assert bargein["latestIntentSuccesses"] == 1
    assert quiet["falseInterrupts"] == 0
    assert quiet["quietMode"] == "silence"


def test_candidate_factory_monitor_uses_enrolled_lifecycle_window(tmp_path):
    events = []
    journey_id = "candidate-soak.20260831T100000Z.1"
    scope = _candidate_scope(journey_id, "quiet_padding")

    async def control(method, url, payload=None):
        events.append((method, url, payload))
        return {"status": "PASS"}

    async def driver(_args, **context):
        events.append(("driver", context["journey_id"], context["duration_sec"]))
        return {
            "name": "quiet_padding",
            "status": "PASS",
            "journeyId": context["journey_id"],
            "evidenceScope": scope,
            "_scopeFinalized": True,
            "_finalizeResult": _candidate_finalize(scope),
        }

    async def analyzer(*, journey_id, output_path):
        events.append(("analyzer", journey_id, output_path.name))
        return {
            "name": "google_live_log_reliability",
            "status": "PASS",
            "journeyType": "quiet_padding",
            "evidenceScope": {
                "journeyType": "quiet_padding",
                "proofProfile": "candidate-lifecycle",
            },
        }

    args = _args(
        run_id="20260831T100000Z",
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        client_id="robot-client",
        candidate_control_json=control,
        candidate_journey_driver=driver,
        candidate_log_analyzer=analyzer,
    )

    result = asyncio.run(build_candidate_journeys(args)["monitor"](args, duration_sec=37.5))

    assert events[0][2] == {
        "clientId": "robot-client",
        "journeyId": journey_id,
        "ttlSec": 3600,
        "journeyType": "quiet_padding",
        "proofProfile": "candidate-lifecycle",
    }
    assert [event[0] for event in events] == ["POST", "PUT", "driver", "analyzer"]
    assert events[2] == ("driver", journey_id, 37.5)
    assert result[0]["task5LogEvidence"]["journeyType"] == "quiet_padding"


def test_default_candidate_bargein_sends_opus_audio_not_text(monkeypatch):
    sent = []

    class Websocket:
        async def send(self, value):
            sent.append(value)

    async def no_preflight(*_args, **_kwargs):
        return None

    async def stopped(*_args, **_kwargs):
        return {"stop": {"reason": "interrupt"}, "binaryCount": 0, "observedAt": 2.0}

    async def replacement(*_args, **_kwargs):
        return {
            "replacementResponseStarted": True,
            "replacementResponseStopped": True,
            "replacementBinaryChunks": 2,
            "maxServerOutputGapMs": 80.0,
        }

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(robot_soak, "_candidate_audio_packets", lambda _args: [b"opus"])
    monkeypatch.setattr(robot_soak, "_drain_preflight_terminal", no_preflight)
    monkeypatch.setattr(robot_soak, "_observe_interrupt_stop", stopped)
    monkeypatch.setattr(robot_soak, "_collect_replacement_response", replacement)
    monkeypatch.setattr(robot_soak.asyncio, "sleep", no_sleep)

    result = asyncio.run(
        robot_soak._run_candidate_audio_bargein(
            _args(interrupt_timeout_sec=3.0, event_timeout_sec=20.0),
            Websocket(),
            clock=lambda: 1.6,
        )
    )

    assert sent == [
        json.dumps({"type": "listen", "state": "start", "mode": "realtime"}),
        b"opus",
    ]
    assert result["bargeinStopMs"] == 400.0


def test_candidate_factory_cleanup_refuses_second_call():
    calls = 0

    async def driver(_args, **context):
        nonlocal calls
        calls += 1
        return {"status": "PASS", "operation": context["operation"]}

    args = _args(
        run_id="20260831T100000Z",
        candidate_journey_driver=driver,
    )
    cleanup = build_candidate_journeys(args)["cleanup"]

    asyncio.run(cleanup(args, final_scope={}))
    with pytest.raises(RuntimeError, match="more than once"):
        asyncio.run(cleanup(args, final_scope={}))
    assert calls == 1


def test_default_candidate_cleanup_rejects_authoritative_leaked_server_work(tmp_path):
    class Websocket:
        async def close(self):
            return None

    journeys = _journeys()
    asyncio.run(journeys["quiet"](_args(), name="quiet", index=1))
    execution = asyncio.run(journeys["monitor"](_args(), duration_sec=480))[0]
    final_scope = _cleanup_scope_from_execution(execution)
    log_evidence = deepcopy(execution["task5LogEvidence"])
    log_evidence["serverIssued"] = True
    log_evidence["cleanupEvidence"] = {
        "status": "FAIL",
        "pendingOwnedTasks": 1,
        "activeSessions": 1,
        "activeReceiveLoops": 1,
    }
    args = _args(
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        candidate_control_json=lambda *_args, **_kwargs: {
            "journeyId": final_scope["journeyId"],
            "journeyType": final_scope["journeyType"],
            "proofProfile": "candidate-lifecycle",
            "status": "PASS",
        },
        candidate_log_analyzer=lambda **_kwargs: log_evidence,
    )
    args._candidate_websocket_state = {"websocket": Websocket()}

    result = asyncio.run(
        robot_soak._run_candidate_websocket_journey(
            args,
            operation="cleanup",
            final_scope=final_scope,
        )
    )

    assert result["status"] == "FAIL"
    assert result["providerCloseStatus"] == "FAIL"
    assert result["pendingOwnedTasks"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "identity", "scope", "window", "journey_type", "proof_profile",
        "lineage", "server_issued", "journey_id", "final_scope",
        "terminal_type", "terminal_profile",
    ],
)
def test_default_candidate_cleanup_rejects_unbound_analyzer_evidence(
    tmp_path, mutation
):
    class Websocket:
        async def close(self):
            return None

    journeys = _journeys()
    asyncio.run(journeys["quiet"](_args(), name="quiet", index=1))
    result = asyncio.run(journeys["monitor"](_args(), duration_sec=480))[0]
    final_scope = _cleanup_scope_from_execution(result)
    log_evidence = deepcopy(result["task5LogEvidence"])
    log_evidence["serverIssued"] = True
    log_evidence["cleanupEvidence"] = {
        "status": "PASS",
        "pendingOwnedTasks": 0,
        "activeSessions": 0,
        "activeReceiveLoops": 0,
    }
    terminal = {
        "journeyId": final_scope["journeyId"],
        "journeyType": final_scope["journeyType"],
        "proofProfile": final_scope["proofProfile"],
        "status": "PASS",
    }
    if mutation == "identity":
        log_evidence["candidateIdentity"] = {**IDENTITY, "gitSha": "f" * 40}
    elif mutation == "scope":
        log_evidence["evidenceScope"]["connectionId"] = "foreign"
    elif mutation == "window":
        log_evidence["logWindow"]["windowId"] = "foreign"
    elif mutation == "journey_type":
        log_evidence["journeyType"] = "conversation"
    elif mutation == "proof_profile":
        log_evidence["evidenceScope"]["proofProfile"] = "physical-transcript"
    elif mutation == "lineage":
        log_evidence["finalLiveConnectionId"] = "foreign-live"
    elif mutation == "server_issued":
        log_evidence["serverIssued"] = False
    elif mutation == "journey_id":
        terminal["journeyId"] = "foreign-journey"
    elif mutation == "final_scope":
        final_scope["connectionId"] = "foreign-connection"
    elif mutation == "terminal_type":
        terminal["journeyType"] = "conversation"
    else:
        terminal["proofProfile"] = "physical-transcript"
    args = _args(
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        candidate_control_json=lambda *_args, **_kwargs: terminal,
        candidate_log_analyzer=lambda **_kwargs: log_evidence,
    )
    args._candidate_websocket_state = {"websocket": Websocket()}

    cleanup = asyncio.run(
        robot_soak._run_candidate_websocket_journey(
            args,
            operation="cleanup",
            final_scope=final_scope,
        )
    )

    assert cleanup["status"] == "FAIL"
    assert cleanup["providerCloseStatus"] == "FAIL"
    assert cleanup["logStatus"] == "FAIL"


def test_default_candidate_cleanup_accepts_only_bound_analyzer_evidence(tmp_path):
    class Websocket:
        async def close(self):
            return None

    journeys = _journeys()
    asyncio.run(journeys["quiet"](_args(), name="quiet", index=1))
    execution = asyncio.run(journeys["monitor"](_args(), duration_sec=480))[0]
    final_scope = _cleanup_scope_from_execution(execution)
    log_evidence = deepcopy(execution["task5LogEvidence"])
    log_evidence["serverIssued"] = True
    log_evidence["cleanupEvidence"] = {
        "status": "PASS",
        "pendingOwnedTasks": 0,
        "activeSessions": 0,
        "activeReceiveLoops": 0,
    }
    args = _args(
        produce_candidate_evidence=tmp_path / "journey-evidence.json",
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        candidate_control_json=lambda *_args, **_kwargs: {
            "journeyId": final_scope["journeyId"],
            "journeyType": final_scope["journeyType"],
            "proofProfile": final_scope["proofProfile"],
            "status": "PASS",
        },
        candidate_log_analyzer=lambda **_kwargs: log_evidence,
    )
    args._candidate_websocket_state = {"websocket": Websocket()}

    cleanup = asyncio.run(
        robot_soak._run_candidate_websocket_journey(
            args, operation="cleanup", final_scope=final_scope
        )
    )

    assert cleanup["status"] == "PASS"
    assert cleanup["pendingOwnedTasks"] == 0
    assert cleanup["activeSessions"] == 0
    assert cleanup["activeReceiveLoops"] == 0


@pytest.mark.parametrize(
    "stage",
    ["quiet_padding", "conversation_after_lesson"],
)
def test_default_candidate_cleanup_rejects_reconnect_transition_for_non_reconnect_final_stage(
    tmp_path, monkeypatch, stage
):
    class Websocket:
        async def close(self):
            return None

    if stage == "conversation_after_lesson":
        def extended_window(sequence):
            start = datetime(
                2026, 8, 31, 11, 0, tzinfo=timezone.utc
            ) + timedelta(seconds=(sequence - 1) * 55)
            return {
                "windowId": f"window-{sequence}",
                "start": start.isoformat(),
                "end": (start + timedelta(seconds=55)).isoformat(),
            }

        monkeypatch.setitem(
            _journeys.__globals__, "_execution_window", extended_window
        )
    journeys = _journeys()
    captured = {}
    original_conversation = journeys["conversation"]
    original_monitor = journeys["monitor"]

    async def capture_conversation(*args, **kwargs):
        execution = await original_conversation(*args, **kwargs)
        if kwargs.get("name") == "conversation_after_lesson":
            captured["final"] = execution
        return execution

    async def capture_monitor(*args, **kwargs):
        padding = await original_monitor(*args, **kwargs)
        captured["final"] = padding[-1]
        return padding

    output = tmp_path / "journey-evidence.json"
    args = _args(
        produce_candidate_evidence=output,
        evidence_control_url="http://server.test",
        device_id="aa:bb",
        candidate_peer_identity_hash=PEER_HASH,
    )

    async def cleanup(_args, *, final_scope):
        execution = captured["final"]
        assert execution["name"] == stage
        log_evidence = deepcopy(execution["task5LogEvidence"])
        log_evidence["serverIssued"] = True
        log_evidence["serverConnectionTransitions"] = [
            {
                "status": "PASS",
                "source": "server_log",
                "serverIssued": True,
                "sequence": 1,
                "reason": "same_device_reconnect",
                "fromJourneyId": "foreign-journey",
                "fromConnectionId": SOAK_CONNECTION_1,
                "toJourneyId": final_scope["journeyId"],
                "toConnectionId": final_scope["connectionId"],
                "peerIdentityHash": final_scope["peerIdentityHash"],
            }
        ]
        log_evidence["cleanupEvidence"] = {
            "status": "PASS",
            "pendingOwnedTasks": 0,
            "activeSessions": 0,
            "activeReceiveLoops": 0,
        }
        args.candidate_control_json = lambda *_args, **_kwargs: {
            "journeyId": final_scope["journeyId"],
            "journeyType": final_scope["journeyType"],
            "proofProfile": final_scope["proofProfile"],
            "status": "PASS",
        }
        args.candidate_log_analyzer = lambda **_kwargs: log_evidence
        args._candidate_websocket_state = {"websocket": Websocket()}
        return await robot_soak._run_candidate_websocket_journey(
            args, operation="cleanup", final_scope=final_scope
        )

    journeys["conversation"] = capture_conversation
    journeys["monitor"] = capture_monitor
    journeys["cleanup"] = cleanup
    result = asyncio.run(
        produce_candidate_evidence(
            args,
            journeys=journeys,
            sample_resources=_samples,
            clock=_Clock(),
        )
    )

    assert result["status"] == "FAIL"
    assert {failure["code"] for failure in result["failures"]} >= {
        "CLEANUP_FAILED"
    }
    assert not output.exists()


def test_candidate_cli_producer_and_replay_are_mutually_exclusive(tmp_path):
    parser = _build_argument_parser()
    parsed = parser.parse_args(
        [
            "--mode", "candidate",
            "--journey-evidence", str(tmp_path / "replay.json"),
            "--produce-candidate-evidence", str(tmp_path / "produce.json"),
        ]
    )

    with pytest.raises(SystemExit):
        _validate_candidate_args(parser, parsed)


def test_candidate_cli_producer_reads_only_named_secret_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CUSTOM_MINT_ENV", "never-render-this-value")
    parser = _build_argument_parser()
    parsed = parser.parse_args(
        [
            "--mode", "candidate",
            "--candidate-git-sha", "a" * 40,
            "--candidate-image-digest", f"sha256:{'a' * 64}",
            "--firmware-identity", "firmware-v1",
            "--fixture-sha256", "b" * 64,
            "--baseline-report", "baseline.json",
            "--real-api-report", "real-api.json",
            "--transport-report", "transport.json",
            "--correlated-transport-report", "correlated.json",
            "--log-reliability-report", "log.json",
            "--lesson-manifest", "lesson.json",
            "--produce-candidate-evidence", str(tmp_path / "journeys.json"),
            "--evidence-control-url", "http://server.test",
            "--evidence-mint-secret-env", "CUSTOM_MINT_ENV",
            "--server-log", "server.log",
            "--run-id", "20260831T100000Z",
        ]
    )

    _validate_candidate_args(parser, parsed)
    assert "never-render-this-value" not in repr(parsed)
    assert parsed.report is None
