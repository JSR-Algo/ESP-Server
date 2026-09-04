"""Shared report primitives for Google Live production reliability evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import resource
import sys
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "google-live-reliability.v1"
NORMALIZED_SECRET_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "token",
        "accesstoken",
        "refreshtoken",
        "sessionresumptionhandle",
        "handle",
        "clientsecret",
        "xgoogapikey",
        "xapikey",
    }
)
FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "audio",
        "audiochunk",
        "audiobytes",
        "rawaudio",
        "rawaudiobase64",
        "base64audio",
        "transcript",
        "rawtranscript",
        "prompt",
        "modeltext",
        "rawlog",
        "loglines",
        "authorization",
        "apikey",
        "xgoogapikey",
        "xgoogleapikey",
        "xapikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "bearertoken",
        "cookie",
        "setcookie",
        "credential",
        "credentials",
        "secret",
        "clientsecret",
        "exception",
        "handle",
        "sessionresumptionhandle",
    }
)
GOOGLE_LIVE_LIMITS = {
    "firstAudioP50Ms": 1200.0,
    "firstAudioP95Ms": 1800.0,
    "serverStopMaxMs": 250.0,
    "physicalBargeinP95Ms": 500.0,
    "serverOutputGapMaxMs": 250.0,
    "relativeLatencyRegressionPct": 15.0,
    "minimumSoakTurns": 30,
    "minimumSoakDurationSec": 1800.0,
    "minimumBargeins": 10,
    "minimumLatestIntentSuccessRate": 0.80,
    "rssDeltaBytes": 32 * 1024 * 1024,
    "fdDelta": 8,
    "asyncioTaskDelta": 4,
    "threadDelta": 4,
    "rssSlopeBytesPerSample": 1024 * 1024,
    "fdSlopePerSample": 0.25,
    "asyncioTaskSlopePerSample": 0.25,
    "threadSlopePerSample": 0.25,
}


def monotonic_growth_slope(values: Sequence[int | float]) -> float:
    if len(values) < 2:
        return 0.0
    midpoint = (len(values) - 1) / 2
    denominator = sum((index - midpoint) ** 2 for index in range(len(values)))
    if denominator == 0:
        return 0.0
    mean = sum(float(value) for value in values) / len(values)
    slope = sum(
        (index - midpoint) * (float(value) - mean)
        for index, value in enumerate(values)
    ) / denominator
    return max(0.0, slope)


def _process_rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, AttributeError, OSError):
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(usage if sys.platform == "darwin" else usage * 1024)


def _fd_count() -> int:
    try:
        import psutil

        counter = getattr(psutil.Process(), "num_fds", None)
        if counter is not None:
            return int(counter())
    except (ImportError, AttributeError, OSError):
        pass
    for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return len(list(directory.iterdir()))
        except OSError:
            continue
    return 0


def redact_mapping(value: Any) -> Any:
    """Return a recursively redacted copy of JSON-like report data."""
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>"
            if re.sub(r"[^a-zA-Z0-9]", "", str(key)).lower() in NORMALIZED_SECRET_KEYS
            else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def forbidden_report_fields(value: Any, path: str = "") -> list[str]:
    """Return privacy-forbidden key paths without reading or returning their values."""
    hits = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}" if path else str(key)
            normalized = re.sub(r"[^a-zA-Z0-9]", "", str(key)).lower()
            if normalized in FORBIDDEN_REPORT_KEYS:
                hits.append(item_path)
            else:
                hits.extend(forbidden_report_fields(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(forbidden_report_fields(item, f"{path}[{index}]"))
    return hits


def validate_real_api_pass_report(
    report: Any, *, expected_candidate_identity: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Validate the exact privacy-safe PASS schema emitted by google_live_smoke."""
    failures = []

    def mismatch(field: str) -> None:
        failures.append({"code": "REAL_API_CONTRACT_MISMATCH", "field": field})

    expected_fields = {
        "schemaVersion",
        "name",
        "candidateIdentity",
        "status",
        "connectionMs",
        "firstServerEventMs",
        "firstAudioMs",
        "audioChunks",
        "attempts",
    }
    if not isinstance(report, Mapping):
        mismatch("report")
        return failures
    if set(report) != expected_fields:
        mismatch("fields")
    for field, expected in (
        ("schemaVersion", SCHEMA_VERSION),
        ("name", "real_api"),
        ("candidateIdentity", dict(expected_candidate_identity)),
        ("status", "PASS"),
    ):
        if report.get(field) != expected or type(report.get(field)) is not type(expected):
            mismatch(field)
    attempts = report.get("attempts")
    if type(attempts) is not int or attempts not in {1, 2}:
        mismatch("attempts")
    audio_chunks = report.get("audioChunks")
    if type(audio_chunks) is not int or audio_chunks < 1:
        mismatch("audioChunks")
    timing_fields = ("connectionMs", "firstServerEventMs", "firstAudioMs")
    timings = [report.get(field) for field in timing_fields]
    for field, value in zip(timing_fields, timings, strict=True):
        if type(value) is not float or not math.isfinite(value) or value < 0:
            mismatch(field)
    if all(type(value) is float and math.isfinite(value) for value in timings):
        if not timings[0] <= timings[1] <= timings[2]:
            mismatch("timingOrder")
        if timings[2] > GOOGLE_LIVE_LIMITS["firstAudioP95Ms"]:
            mismatch("firstAudioBudget")
    if forbidden_report_fields(report):
        mismatch("privacy")
    return failures


def percentile(values: Sequence[int | float], percentile_value: float) -> float | None:
    """Return the rounded nearest-rank percentile, clamped to the sample bounds."""
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile_value must be between 0 and 100")
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1))
    return round(ordered[index], 3)


def build_candidate_identity(
    git_sha: Any,
    image_digest: Any,
    firmware_identity: Any,
    config: Mapping[str, Any] | None,
    fixture_sha256: Any,
) -> dict[str, str]:
    """Bind all reliability layers to one privacy-safe candidate identity."""
    required_values = {
        "git_sha": git_sha,
        "image_digest": image_digest,
        "firmware_identity": firmware_identity,
        "fixture_sha256": fixture_sha256,
    }
    for field, value in required_values.items():
        if value is None or not str(value).strip():
            raise ValueError(f"{field} must be non-empty")
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", str(image_digest)) is None:
        raise ValueError("image_digest must be sha256: followed by 64 hexadecimal characters")
    if re.fullmatch(r"[0-9a-fA-F]{64}", str(fixture_sha256)) is None:
        raise ValueError("fixture_sha256 must contain exactly 64 hexadecimal characters")
    safe_config = redact_mapping(config or {})
    encoded = json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode()
    return {
        "gitSha": str(git_sha),
        "imageDigest": str(image_digest),
        "firmwareIdentity": str(firmware_identity),
        "fixtureSha256": str(fixture_sha256),
        "configFingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    }


def compare_latency_baseline(
    candidate: Mapping[str, int | float], baseline: Mapping[str, int | float]
) -> dict[str, Any]:
    """Compare shared latency metrics against the regression budget."""
    required_metrics = (
        "firstAudioP50Ms",
        "firstAudioP95Ms",
        "bargeinP95Ms",
        "reconnectRecoveryP95Ms",
    )
    checks: dict[str, bool] = {}
    regressions: dict[str, float] = {}
    failures: list[dict[str, str]] = []
    required_set = set(required_metrics)
    for side, metrics in (("candidate", candidate), ("baseline", baseline)):
        for key in sorted(set(metrics) - required_set):
            failures.append(
                {"code": "LATENCY_METRIC_INVALID", "metric": key, "side": side}
            )
    for key in required_metrics:
        candidate_valid = key in candidate and _valid_latency_metric(
            candidate[key], positive=True
        )
        baseline_valid = key in baseline and _valid_latency_metric(
            baseline[key], positive=True
        )
        if not candidate_valid:
            failures.append(
                {"code": "LATENCY_METRIC_INVALID", "metric": key, "side": "candidate"}
            )
        if not baseline_valid:
            failures.append(
                {"code": "LATENCY_METRIC_INVALID", "metric": key, "side": "baseline"}
            )
        if (
            not candidate_valid
            or not baseline_valid
        ):
            continue
        regression = (
            (float(candidate[key]) - float(baseline[key])) / float(baseline[key])
        ) * 100
        checks[f"{key.removesuffix('Ms')}Regression"] = (
            regression <= GOOGLE_LIVE_LIMITS["relativeLatencyRegressionPct"]
        )
        regressions[key] = round(regression, 2)
    result: dict[str, Any] = {
        "checks": checks,
        "regressionPct": regressions,
        "pass": len(checks) == len(required_metrics) and all(checks.values()) and not failures,
    }
    if failures:
        result["failures"] = failures
    return result


def _valid_latency_metric(value: Any, *, positive: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return value > 0 if positive else value >= 0


def reliability_verdict(
    expected_identity: Mapping[str, Any], layers: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Fail closed when a layer is not passing or identifies another candidate."""
    failures: list[dict[str, Any]] = []
    if not layers:
        failures.append({"code": "LAYERS_MISSING"})
    seen_names: set[str] = set()
    for layer in layers:
        name = layer.get("name")
        normalized_name = name.strip() if isinstance(name, str) else ""
        if not normalized_name:
            failures.append({"code": "LAYER_NAME_MISSING", "layer": name})
        elif normalized_name in seen_names:
            failures.append({"code": "DUPLICATE_LAYER_NAME", "layer": name})
        else:
            seen_names.add(normalized_name)
        if layer.get("status") != "PASS":
            failures.append(
                {
                    "code": f"LAYER_{layer.get('status', 'MISSING')}",
                    "layer": name,
                }
            )
        if layer.get("candidateIdentity") != expected_identity:
            failures.append(
                {"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": name}
            )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": expected_identity,
        "layers": list(layers),
        "failures": failures,
    }


def validate_log_reliability_contract(
    report: Any,
    *,
    expected_candidate_identity: Mapping[str, Any],
    expected_log_window: Any,
    expected_evidence_scope: Any,
) -> list[dict[str, Any]]:
    """Validate the normalized Task 5 identity, lifecycle, and correlation contract."""
    failures = []

    def mismatch(field):
        failures.append({"code": "SERVER_LOG_CONTRACT_MISMATCH", "field": field})

    if not isinstance(report, Mapping):
        mismatch("report")
        return failures
    required = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "google_live_log_reliability",
        "status": "PASS",
        "candidateIdentity": dict(expected_candidate_identity),
        "logWindow": expected_log_window,
        "evidenceScope": expected_evidence_scope,
        "failures": [],
    }
    for field, expected in required.items():
        if report.get(field) != expected:
            mismatch(field)

    scope = expected_evidence_scope if isinstance(expected_evidence_scope, Mapping) else {}
    initial_id = report.get("initialLiveConnectionId")
    final_id = report.get("finalLiveConnectionId")
    transitions = report.get("liveConnectionTransitions")
    if initial_id != scope.get("initialLiveConnectionId"):
        mismatch("initialLiveConnectionId")
    if _live_transition_final(initial_id, transitions) != final_id:
        mismatch("liveConnectionTransitions")

    server_transitions = report.get("serverConnectionTransitions", [])
    if not isinstance(server_transitions, list) or len(server_transitions) > 1:
        mismatch("serverConnectionTransitions")
    elif server_transitions:
        transition = server_transitions[0]
        if not (
            isinstance(transition, Mapping)
            and transition.get("status") == "PASS"
            and transition.get("source") == "server_log"
            and transition.get("serverIssued") is True
            and transition.get("sequence") == 1
            and transition.get("reason") == "same_device_reconnect"
            and transition.get("toJourneyId") == scope.get("journeyId")
            and transition.get("toConnectionId") == scope.get("connectionId")
            and transition.get("peerIdentityHash") == scope.get("peerIdentityHash")
            and isinstance(transition.get("fromJourneyId"), str)
            and bool(transition.get("fromJourneyId"))
            and isinstance(transition.get("fromConnectionId"), str)
            and bool(transition.get("fromConnectionId"))
            and transition.get("fromConnectionId") != transition.get("toConnectionId")
        ):
            mismatch("serverConnectionTransitions")

    exact_zero_fields = ("receiveLoopBalance", "staleAudioAfterReplacement")
    for field in exact_zero_fields:
        if type(report.get(field)) is not int or report.get(field) != 0:
            mismatch(field)
    if type(report.get("maxReceiveLoopsActive")) is not int or report.get(
        "maxReceiveLoopsActive"
    ) not in {0, 1}:
        mismatch("maxReceiveLoopsActive")
    replay_counts = report.get("replayCountsByReopen")
    if not isinstance(replay_counts, Mapping) or any(
        not isinstance(key, str) or type(value) is not int or value not in {0, 1}
        for key, value in replay_counts.items()
    ):
        mismatch("replayCountsByReopen")
    for field in (
        "duplicateResponseIds",
        "unrecoveredTimeouts",
        "unreleasedLessonHandoffs",
        "fatalHits",
    ):
        if report.get(field) != []:
            mismatch(field)

    correlations = report.get("correlations")
    if not isinstance(correlations, list):
        mismatch("correlations")
        correlations = []
    for item in correlations:
        if (
            not isinstance(item, Mapping)
            or item.get("status") != "PASS"
            or any(
                not isinstance(item.get(field), str) or not item.get(field)
                for field in ("journeyId", "connectionId", "liveConnectionId")
            )
            or item.get("liveConnectionId") != final_id
            or not _nonnegative_int(item.get("cancelledResponseId"))
            or not _nonnegative_int(item.get("replacementResponseId"))
        ):
            mismatch("correlations")
            break
    correlation = report.get("correlation")
    if not isinstance(correlation, Mapping):
        mismatch("correlation")
    elif correlation.get("status") == "PASS":
        if (
            len(correlations) != 1
            or not _nonnegative_int(correlation.get("cancelledResponseId"))
            or not _nonnegative_int(correlation.get("replacementResponseId"))
            or any(
                correlation.get(field) != correlations[0].get(field)
                for field in ("cancelledResponseId", "replacementResponseId")
            )
        ):
            mismatch("correlation")
    elif correlation.get("status") == "MULTIPLE":
        observed = correlation.get("observedInterrupts")
        if type(observed) is not int or len(correlations) < 2 or observed not in {
            0,
            len(correlations),
        }:
            mismatch("correlation")
    elif correlation.get("status") == "NOT_OBSERVED":
        if correlations:
            mismatch("correlation")
    else:
        mismatch("correlation")
    return failures


def validate_candidate_soak_report(
    report: Any, *, expected_candidate_identity: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Validate the complete released Task 6 candidate-soak report."""
    failures = []

    def mismatch(field):
        failures.append({"code": "CANDIDATE_SOAK_CONTRACT_MISMATCH", "field": field})

    if not isinstance(report, Mapping):
        mismatch("report")
        return failures
    required = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "candidate_soak",
        "status": "PASS",
        "candidateIdentity": dict(expected_candidate_identity),
        "failures": [],
        "rawAudioPersisted": False,
        "transcriptPersisted": False,
        "exit_code": 0,
    }
    for field, expected in required.items():
        if report.get(field) != expected or type(report.get(field)) is not type(expected):
            mismatch(field)

    stage_specs = (
        ("conversation", 17),
        ("bargein", 10),
        ("quiet", 2),
        ("reopen", 1),
        ("reconnect", 1),
        ("lesson", 1),
        ("conversation_after_lesson", 1),
    )
    expected_stages = [
        {"name": name, "executions": count, "status": "PASS"}
        for name, count in stage_specs
    ]
    stages = report.get("stages")
    if (
        stages != expected_stages
        or not isinstance(stages, list)
        or any(
            not isinstance(stage, Mapping)
            or type(stage.get("executions")) is not int
            for stage in stages
        )
    ):
        mismatch("stages")
    totals = report.get("totals")
    exact_totals = {
        "successfulTurns": 30,
        "bargeins": 10,
        "falseInterrupts": 0,
        "unexpectedFallbacks": 0,
    }
    latest_intent_successes = (
        totals.get("latestIntentSuccesses") if isinstance(totals, Mapping) else None
    )
    latest_intent_rate = (
        totals.get("latestIntentSuccessRate") if isinstance(totals, Mapping) else None
    )
    if (
        not isinstance(totals, Mapping)
        or set(totals)
        != {*exact_totals, "latestIntentSuccesses", "latestIntentSuccessRate"}
        or any(
            type(totals.get(field)) is not int or totals.get(field) != expected
            for field, expected in exact_totals.items()
        )
        or type(latest_intent_successes) is not int
        or not 8 <= latest_intent_successes <= 10
        or type(latest_intent_rate) is not float
        or latest_intent_rate != latest_intent_successes / 10
        or latest_intent_rate < GOOGLE_LIVE_LIMITS["minimumLatestIntentSuccessRate"]
    ):
        mismatch("totals")

    cleanup = report.get("cleanupVerdict")
    cleanup_required = {
        "status": "PASS",
        "websocketClosed": True,
        "pendingOwnedTasks": 0,
        "actualPendingCleanupTasks": 0,
        "activeSessions": 0,
        "activeReceiveLoops": 0,
        "providerFinalizeStatus": "PASS",
        "providerCloseStatus": "PASS",
        "logStatus": "PASS",
        "resourceEndSampleAccounted": True,
    }
    if not isinstance(cleanup, Mapping) or any(
        cleanup.get(field) != expected or type(cleanup.get(field)) is not type(expected)
        for field, expected in cleanup_required.items()
    ):
        mismatch("cleanupVerdict")

    resource = report.get("resourceVerdict")
    resource_limits = {
        "rssBytes": GOOGLE_LIVE_LIMITS["rssDeltaBytes"],
        "fdCount": GOOGLE_LIVE_LIMITS["fdDelta"],
        "asyncioTaskCount": GOOGLE_LIVE_LIMITS["asyncioTaskDelta"],
        "threadCount": GOOGLE_LIVE_LIMITS["threadDelta"],
    }
    slope_limits = {
        "rssBytesPerSample": GOOGLE_LIVE_LIMITS["rssSlopeBytesPerSample"],
        "fdCountPerSample": GOOGLE_LIVE_LIMITS["fdSlopePerSample"],
        "asyncioTaskCountPerSample": GOOGLE_LIVE_LIMITS["asyncioTaskSlopePerSample"],
        "threadCountPerSample": GOOGLE_LIVE_LIMITS["threadSlopePerSample"],
    }
    expected_limit_report = {
        key: GOOGLE_LIVE_LIMITS[key]
        for key in (
            "rssDeltaBytes",
            "fdDelta",
            "asyncioTaskDelta",
            "threadDelta",
            "rssSlopeBytesPerSample",
            "fdSlopePerSample",
            "asyncioTaskSlopePerSample",
            "threadSlopePerSample",
        )
    }
    if (
        not isinstance(resource, Mapping)
        or resource.get("status") != "PASS"
        or resource.get("failures") != []
        or not isinstance(resource.get("checks"), Mapping)
        or any(value is not True for value in resource["checks"].values())
        or set(resource["checks"])
        != {
            "rssDeltaBounded",
            "fdDeltaBounded",
            "taskDeltaBounded",
            "threadDeltaBounded",
            "rssSlopeBounded",
            "fdSlopeBounded",
            "taskSlopeBounded",
            "threadSlopeBounded",
        }
        or resource.get("limits") != expected_limit_report
        or not _bounded_metric_mapping(resource.get("deltas"), resource_limits)
        or not _bounded_metric_mapping(resource.get("slopes"), slope_limits)
    ):
        mismatch("resourceVerdict")

    metrics = report.get("latencyMetrics")
    metric_limits = {
        "firstAudioP50Ms": GOOGLE_LIVE_LIMITS["firstAudioP50Ms"],
        "firstAudioP95Ms": GOOGLE_LIVE_LIMITS["firstAudioP95Ms"],
        "bargeinP95Ms": GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"],
    }
    comparison = report.get("latencyComparison")
    comparison_checks = comparison.get("checks") if isinstance(comparison, Mapping) else None
    comparison_regressions = (
        comparison.get("regressionPct") if isinstance(comparison, Mapping) else None
    )
    expected_checks = {
        "firstAudioP50Regression",
        "firstAudioP95Regression",
        "bargeinP95Regression",
        "reconnectRecoveryP95Regression",
    }
    expected_regressions = {
        "firstAudioP50Ms",
        "firstAudioP95Ms",
        "bargeinP95Ms",
        "reconnectRecoveryP95Ms",
    }
    if (
        not isinstance(metrics, Mapping)
        or set(metrics) != expected_regressions
        or any(not _finite_positive(metrics.get(key)) for key in metrics)
        or any(metrics[key] > limit for key, limit in metric_limits.items())
        or not _finite_positive(report.get("serverOutputGapP95Ms"))
        or report.get("serverOutputGapP95Ms") > GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"]
        or not isinstance(comparison, Mapping)
        or comparison.get("pass") is not True
        or not isinstance(comparison_checks, Mapping)
        or set(comparison_checks) != expected_checks
        or any(value is not True for value in comparison_checks.values())
        or not isinstance(comparison_regressions, Mapping)
        or set(comparison_regressions) != expected_regressions
        or comparison.get("failures", []) != []
        or any(
            not _finite_number(value)
            or value > GOOGLE_LIVE_LIMITS["relativeLatencyRegressionPct"]
            for value in comparison_regressions.values()
        )
    ):
        mismatch("latencyVerdict")

    duration = report.get("durationSec")
    runtime = report.get("runtimeElapsedSec")
    recorded_runtime = report.get("recordedRuntimeElapsedSec")
    replay_candidate_evidence = report.get("replayCandidateEvidence")
    gap_budget = report.get("evidenceGapBudgetSec")
    anchors = report.get("evidenceAnchors")
    start = _parse_utc_timestamp(
        anchors.get("serverStartUtc") if isinstance(anchors, Mapping) else None
    )
    end = _parse_utc_timestamp(
        anchors.get("serverEndUtc") if isinstance(anchors, Mapping) else None
    )
    monitored_duration = _candidate_monitored_duration(
        report,
        expected_candidate_identity=expected_candidate_identity,
        expected_stage_names=[name for name, count in stage_specs for _ in range(count)],
        expected_start=start,
        expected_end=end,
        gap_budget=gap_budget,
    )
    if (
        not _finite_nonnegative(duration)
        or duration < GOOGLE_LIVE_LIMITS["minimumSoakDurationSec"]
        or not _finite_nonnegative(runtime)
        or type(replay_candidate_evidence) is not bool
        or (not replay_candidate_evidence and runtime < duration)
        or (not replay_candidate_evidence and recorded_runtime is not None)
        or (
            recorded_runtime is not None
            and (not _finite_nonnegative(recorded_runtime) or recorded_runtime < duration)
        )
        or not _finite_nonnegative(gap_budget)
        or not 0 < gap_budget <= 10.0
        or monitored_duration is None
        or abs(monitored_duration - duration) > 0.001
    ):
        mismatch("monitoredDurationProof")

    upstream = report.get("upstreamLayers")
    expected_upstream = [
        {
            "name": name,
            "status": status,
            "candidateIdentity": dict(expected_candidate_identity),
        }
        for name, status in (
            ("real_api", "PASS"),
            ("websocket_audio_bargein_transport", "SKIPPED"),
            ("websocket_audio_bargein_correlated", "PASS"),
            ("google_live_log_reliability", "PASS"),
        )
    ]
    if upstream != expected_upstream:
        mismatch("upstreamLayers")
    return failures


def _bounded_metric_mapping(value, limits):
    return (
        isinstance(value, Mapping)
        and set(value) == set(limits)
        and all(_finite_nonnegative(value[key]) and value[key] <= limit for key, limit in limits.items())
    )


def _finite_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _finite_nonnegative(value):
    return _finite_number(value) and value >= 0


def _finite_positive(value):
    return _finite_number(value) and value > 0


def _candidate_monitored_duration(
    report,
    *,
    expected_candidate_identity,
    expected_stage_names,
    expected_start,
    expected_end,
    gap_budget,
):
    executions = report.get("evidenceExecutions")
    padding = report.get("quietPadding")
    if (
        not isinstance(executions, list)
        or len(executions) != len(expected_stage_names)
        or not isinstance(padding, list)
        or len(padding) > 60
        or expected_start is None
        or expected_end is None
        or not _finite_nonnegative(gap_budget)
    ):
        return None

    windows = []
    previous_execution = None
    peer_identity_hash = None
    seen_journey_ids = set()
    execution_fields = {
        "sequence",
        "stage",
        "journeyId",
        "connectionId",
        "windowId",
        "evidenceScope",
        "initialLiveConnectionId",
        "finalLiveConnectionId",
        "liveConnectionTransitions",
        "serverConnectionTransitions",
        "logWindow",
        "status",
    }
    for sequence, (item, stage) in enumerate(zip(executions, expected_stage_names), start=1):
        scope = item.get("evidenceScope") if isinstance(item, Mapping) else None
        window = item.get("logWindow") if isinstance(item, Mapping) else None
        initial_live_id = item.get("initialLiveConnectionId") if isinstance(item, Mapping) else None
        final_live_id = item.get("finalLiveConnectionId") if isinstance(item, Mapping) else None
        transitions = item.get("liveConnectionTransitions") if isinstance(item, Mapping) else None
        expected_scope = {
            "journeyId": item.get("journeyId") if isinstance(item, Mapping) else None,
            "connectionId": item.get("connectionId") if isinstance(item, Mapping) else None,
            "liveConnectionId": initial_live_id,
            "initialLiveConnectionId": initial_live_id,
            "peerIdentityHash": scope.get("peerIdentityHash") if isinstance(scope, Mapping) else None,
            "serverStartUtc": window.get("start") if isinstance(window, Mapping) else None,
            "journeyType": stage,
            "proofProfile": "candidate-lifecycle",
        }
        server_transitions = item.get("serverConnectionTransitions") if isinstance(item, Mapping) else None
        expected_server_transitions = []
        if stage == "reconnect" and isinstance(previous_execution, Mapping):
            expected_server_transitions = [
                {
                    "status": "PASS",
                    "source": "server_log",
                    "serverIssued": True,
                    "sequence": 1,
                    "reason": "same_device_reconnect",
                    "fromJourneyId": previous_execution.get("journeyId"),
                    "fromConnectionId": previous_execution.get("connectionId"),
                    "toJourneyId": item.get("journeyId"),
                    "toConnectionId": item.get("connectionId"),
                    "peerIdentityHash": expected_scope["peerIdentityHash"],
                }
            ]
        if (
            not isinstance(item, Mapping)
            or set(item) != execution_fields
            or type(item.get("sequence")) is not int
            or item.get("sequence") != sequence
            or item.get("stage") != stage
            or item.get("status") != "PASS"
            or not isinstance(item.get("journeyId"), str)
            or not item.get("journeyId")
            or item.get("journeyId") in seen_journey_ids
            or not isinstance(item.get("connectionId"), str)
            or not item.get("connectionId")
            or not isinstance(item.get("windowId"), str)
            or not item.get("windowId")
            or not isinstance(scope, Mapping)
            or dict(scope) != expected_scope
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_scope["peerIdentityHash"] or "") is None
            or (peer_identity_hash is not None and expected_scope["peerIdentityHash"] != peer_identity_hash)
            or _live_transition_final(initial_live_id, transitions) != final_live_id
            or server_transitions != expected_server_transitions
            or (
                previous_execution is not None
                and stage != "reconnect"
                and item.get("connectionId") != previous_execution.get("connectionId")
            )
            or (
                previous_execution is not None
                and stage == "reconnect"
                and item.get("connectionId") == previous_execution.get("connectionId")
            )
        ):
            return None
        peer_identity_hash = expected_scope["peerIdentityHash"]
        seen_journey_ids.add(item["journeyId"])
        previous_execution = item
        windows.append(window)
    padding_fields = {
        "journeyId",
        "candidateIdentity",
        "connectionId",
        "windowId",
        "logWindow",
        "durationSec",
        "status",
        "serverIssued",
        "peerIdentityHash",
        "liveConnectionId",
        "initialLiveConnectionId",
        "finalLiveConnectionId",
        "liveConnectionTransitions",
        "evidenceScope",
        "falseInterrupts",
        "unexpectedFallbacks",
        "resourceVerdict",
        "logStatus",
    }
    previous_connection_id = previous_execution.get("connectionId")
    for item in padding:
        scope = item.get("evidenceScope") if isinstance(item, Mapping) else None
        window = item.get("logWindow") if isinstance(item, Mapping) else None
        expected_scope = {
            "journeyId": item.get("journeyId") if isinstance(item, Mapping) else None,
            "connectionId": item.get("connectionId") if isinstance(item, Mapping) else None,
            "liveConnectionId": item.get("liveConnectionId") if isinstance(item, Mapping) else None,
            "initialLiveConnectionId": item.get("initialLiveConnectionId") if isinstance(item, Mapping) else None,
            "peerIdentityHash": item.get("peerIdentityHash") if isinstance(item, Mapping) else None,
            "serverStartUtc": window.get("start") if isinstance(window, Mapping) else None,
            "journeyType": "quiet_padding",
            "proofProfile": "candidate-lifecycle",
        }
        if (
            not isinstance(item, Mapping)
            or set(item) != padding_fields
            or item.get("candidateIdentity") != dict(expected_candidate_identity)
            or item.get("status") != "PASS"
            or item.get("serverIssued") is not True
            or not isinstance(item.get("journeyId"), str)
            or not item.get("journeyId")
            or item.get("journeyId") in seen_journey_ids
            or item.get("connectionId") != previous_connection_id
            or not isinstance(scope, Mapping)
            or dict(scope) != expected_scope
            or item.get("peerIdentityHash") != peer_identity_hash
            or item.get("liveConnectionId") != item.get("initialLiveConnectionId")
            or _live_transition_final(
                item.get("initialLiveConnectionId"),
                item.get("liveConnectionTransitions"),
            )
            != item.get("finalLiveConnectionId")
            or type(item.get("falseInterrupts")) is not int
            or item.get("falseInterrupts") != 0
            or type(item.get("unexpectedFallbacks")) is not int
            or item.get("unexpectedFallbacks") != 0
            or not isinstance(item.get("resourceVerdict"), Mapping)
            or item["resourceVerdict"].get("status") != "PASS"
            or item.get("logStatus") != "PASS"
        ):
            return None
        seen_journey_ids.add(item["journeyId"])
        previous_connection_id = item["connectionId"]
        windows.append(window)

    parsed_windows = []
    seen_window_ids = set()
    for index, window in enumerate(windows):
        start = _parse_utc_timestamp(
            window.get("start") if isinstance(window, Mapping) else None
        )
        end = _parse_utc_timestamp(
            window.get("end") if isinstance(window, Mapping) else None
        )
        window_id = window.get("windowId") if isinstance(window, Mapping) else None
        if (
            not isinstance(window, Mapping)
            or not isinstance(window_id, str)
            or not window_id
            or window_id in seen_window_ids
            or start is None
            or end is None
            or end <= start
            or (
                index < len(executions)
                and window_id != executions[index].get("windowId")
            )
        ):
            return None
        if index >= len(executions) and (
            not _finite_positive(padding[index - len(executions)].get("durationSec"))
            or abs(
                float(padding[index - len(executions)]["durationSec"])
                - (end - start).total_seconds()
            )
            > 1.0
        ):
            return None
        if index and not 0 <= (start - parsed_windows[-1][1]).total_seconds() <= gap_budget:
            return None
        seen_window_ids.add(window_id)
        parsed_windows.append((start, end))

    if (
        not parsed_windows
        or parsed_windows[0][0] != expected_start
        or parsed_windows[-1][1] != expected_end
    ):
        return None
    return sum((end - start).total_seconds() for start, end in parsed_windows)


def _parse_utc_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0) else None


def _live_transition_final(initial_id, transitions):
    if not isinstance(initial_id, str) or not initial_id or not isinstance(transitions, list):
        return None
    current = initial_id
    previous_attempt = 0
    for transition in transitions:
        if not isinstance(transition, Mapping):
            return None
        attempt = transition.get("attempt")
        next_id = transition.get("toLiveConnectionId")
        if (
            type(attempt) is not int
            or attempt <= previous_attempt
            or transition.get("fromLiveConnectionId") != current
            or not isinstance(next_id, str)
            or not next_id
            or next_id == current
        ):
            return None
        previous_attempt = attempt
        current = next_id
    return current


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def sample_process_resources() -> dict[str, int]:
    """Sample the process metrics used by the established resource-soak contract."""
    try:
        task_count = len(asyncio.all_tasks())
    except RuntimeError:
        task_count = 0
    return {
        "rssBytes": _process_rss_bytes(),
        "fdCount": _fd_count(),
        "asyncioTaskCount": task_count,
        "threadCount": threading.active_count(),
    }


def resource_verdict(samples: Sequence[Mapping[str, int | float]]) -> dict[str, Any]:
    """Apply the established resource-soak delta and fitted-slope semantics."""
    if not samples:
        raise ValueError("at least one resource sample is required")
    fields = {
        "rss": "rssBytes",
        "fd": "fdCount",
        "task": "asyncioTaskCount",
        "thread": "threadCount",
    }
    slopes = {
        "rssBytesPerSample": monotonic_growth_slope([sample[fields["rss"]] for sample in samples]),
        "fdCountPerSample": monotonic_growth_slope([sample[fields["fd"]] for sample in samples]),
        "asyncioTaskCountPerSample": monotonic_growth_slope(
            [sample[fields["task"]] for sample in samples]
        ),
        "threadCountPerSample": monotonic_growth_slope(
            [sample[fields["thread"]] for sample in samples]
        ),
    }
    deltas = {
        field: max(0, samples[-1][field] - samples[0][field])
        for field in fields.values()
    }
    checks = {
        "rssDeltaBounded": deltas["rssBytes"] <= GOOGLE_LIVE_LIMITS["rssDeltaBytes"],
        "fdDeltaBounded": deltas["fdCount"] <= GOOGLE_LIVE_LIMITS["fdDelta"],
        "taskDeltaBounded": deltas["asyncioTaskCount"]
        <= GOOGLE_LIVE_LIMITS["asyncioTaskDelta"],
        "threadDeltaBounded": deltas["threadCount"] <= GOOGLE_LIVE_LIMITS["threadDelta"],
        "rssSlopeBounded": slopes["rssBytesPerSample"]
        <= GOOGLE_LIVE_LIMITS["rssSlopeBytesPerSample"],
        "fdSlopeBounded": slopes["fdCountPerSample"]
        <= GOOGLE_LIVE_LIMITS["fdSlopePerSample"],
        "taskSlopeBounded": slopes["asyncioTaskCountPerSample"]
        <= GOOGLE_LIVE_LIMITS["asyncioTaskSlopePerSample"],
        "threadSlopeBounded": slopes["threadCountPerSample"]
        <= GOOGLE_LIVE_LIMITS["threadSlopePerSample"],
    }
    failure_codes = (
        ("rssDeltaBounded", "RSS_DELTA_EXCEEDED"),
        ("fdDeltaBounded", "FD_DELTA_EXCEEDED"),
        ("taskDeltaBounded", "ASYNCIO_TASK_DELTA_EXCEEDED"),
        ("threadDeltaBounded", "THREAD_DELTA_EXCEEDED"),
        ("rssSlopeBounded", "RSS_SLOPE_EXCEEDED"),
        ("fdSlopeBounded", "FD_SLOPE_EXCEEDED"),
        ("taskSlopeBounded", "ASYNCIO_TASK_SLOPE_EXCEEDED"),
        ("threadSlopeBounded", "THREAD_SLOPE_EXCEEDED"),
    )
    failures = [
        {"code": code, "phase": "resource_verdict"}
        for check, code in failure_codes
        if not checks[check]
    ]
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failures": failures,
        "deltas": deltas,
        "slopes": slopes,
        "limits": {
            key: GOOGLE_LIVE_LIMITS[key]
            for key in (
                "rssDeltaBytes",
                "fdDelta",
                "asyncioTaskDelta",
                "threadDelta",
                "rssSlopeBytesPerSample",
                "fdSlopePerSample",
                "asyncioTaskSlopePerSample",
                "threadSlopePerSample",
            )
        },
    }
