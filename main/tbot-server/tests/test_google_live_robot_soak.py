import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.google_live_robot_soak import (
    _build_argument_parser,
    _candidate_failure_report,
    _validate_candidate_args,
    run_candidate_soak,
    run_soak,
)

IDENTITY = {
    "gitSha": "candidate-sha",
    "imageDigest": f"sha256:{'a' * 64}",
    "firmwareIdentity": "firmware-v1",
    "fixtureSha256": "b" * 64,
    "configFingerprint": "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
}
PEER_HASH = f"sha256:{'d' * 64}"
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
        "end": (start + timedelta(seconds=30)).isoformat(),
    }


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

    async def journey(_args, *, name, index, label=None, **_kwargs):
        nonlocal sequence, last_window
        sequence += 1
        result = {
            "schemaVersion": "google-live-reliability.v1",
            "name": name,
            "status": "PASS",
            "candidateIdentity": IDENTITY,
            "evidenceSequence": sequence,
            "journeyId": f"candidate-{sequence}",
            "connectionId": "candidate-soak-websocket",
            "initialLiveConnectionId": "live-session-reusable-after-close",
            "finalLiveConnectionId": "live-session-reusable-after-close",
            "liveConnectionTransitions": [],
            "windowId": f"window-{sequence}",
            "logWindow": _execution_window(sequence),
            "successfulTurns": 0 if name in {"quiet", "lesson"} else 1,
            "bargeins": 1 if name == "bargein" else 0,
            "latestIntentSuccesses": 1 if name == "bargein" else 0,
            "falseInterrupts": 0,
            "unexpectedFallbacks": 0,
            "latencies": {},
        }
        if name in {"conversation", "conversation_after_lesson"}:
            result["latencies"] = {"firstAudioMs": [1000]}
        elif name == "bargein":
            result["latencies"] = {"firstAudioMs": [1000], "bargeinMs": [400]}
        elif name in {"reopen", "reconnect"}:
            result["latencies"] = {"reconnectRecoveryMs": [1000]}
        elif name == "lesson":
            result["lessonManifestSha256"] = "sha256:006c27e334a18ca85cdaf3a6e8ff2718219aab2004caf8233417ea6b80fd5652"
        if mutation is not None:
            mutation(result, sequence, name, index, label)
        last_window = deepcopy(result["logWindow"])
        return result

    async def monitor(_args, *, duration_sec):
        start = datetime.fromisoformat(last_window["end"]) + timedelta(seconds=10)
        end = datetime(2026, 8, 31, 11, 30, tzinfo=timezone.utc)
        return [
            {
                "schemaVersion": "google-live-reliability.v1",
                "name": "quiet_padding",
                "status": "PASS",
                "candidateIdentity": IDENTITY,
                "journeyId": "quiet-padding-1",
                "connectionId": "candidate-soak-websocket",
                "serverIssued": True,
                "windowId": "quiet-padding-window-1",
                "logWindow": {
                    "windowId": "quiet-padding-window-1",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "evidenceScope": {
                    "journeyId": "quiet-padding-1",
                    "connectionId": "candidate-soak-websocket",
                    "serverStartUtc": start.isoformat(),
                },
                "durationSec": (end - start).total_seconds(),
                "falseInterrupts": 0,
                "unexpectedFallbacks": 0,
                "resourceVerdict": {"status": "PASS"},
                "logStatus": "PASS",
            }
        ]

    journeys = dict.fromkeys(
        ("conversation", "bargein", "quiet", "reopen", "reconnect", "lesson"),
        journey,
    )
    journeys["monitor"] = monitor
    return journeys


def _samples():
    return {
        "rssBytes": 100,
        "fdCount": 3,
        "asyncioTaskCount": 2,
        "threadCount": 1,
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
    assert report["quietPadding"][0]["durationSec"] == 480.0
    assert report["quietPadding"][0]["resourceVerdict"]["status"] == "PASS"
    assert report["candidateIdentity"] == IDENTITY
    assert len(report["evidenceExecutions"]) == 33
    assert len({item["journeyId"] for item in report["evidenceExecutions"]}) == 33
    assert report["status"] == "PASS"  # Live IDs are transition-scoped, not globally unique.
    assert "raw child" not in json.dumps(report).lower()


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
        return {
            "durationSec": 1800,
            "executions": executions,
            "quietPadding": padding,
            "resourceSamples": [_samples() for _ in range(36)],
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

    async def cleanup(_args):
        cleaned.append(True)

    journeys["cleanup"] = cleanup
    report = _run(journeys=journeys)
    assert report["status"] == "FAIL"
    assert cleaned == [True]


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
