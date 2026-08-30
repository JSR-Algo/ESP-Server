"""Shared report primitives for Google Live production reliability evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.course_mode_resource_soak import _fd_count, _process_rss_bytes, monotonic_growth_slope

SCHEMA_VERSION = "google-live-reliability.v1"
SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "session_resumption_handle",
        "handle",
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


def redact_mapping(value: Any) -> Any:
    """Return a recursively redacted copy of JSON-like report data."""
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>"
            if str(key).lower() in SECRET_KEYS
            else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def percentile(values: Sequence[int | float], percentile_value: float) -> float | None:
    """Return a rounded nearest-rank percentile for a numeric sample."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(
        len(ordered) - 1,
        max(0, int(round((percentile_value / 100) * (len(ordered) - 1)))),
    )
    return round(ordered[index], 3)


def build_candidate_identity(
    git_sha: Any,
    image_digest: Any,
    firmware_identity: Any,
    config: Mapping[str, Any] | None,
    fixture_sha256: Any,
) -> dict[str, str]:
    """Bind all reliability layers to one privacy-safe candidate identity."""
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
    """Compare shared nonzero latency metrics against the regression budget."""
    checks: dict[str, bool] = {}
    regressions: dict[str, float] = {}
    for key in (
        "firstAudioP50Ms",
        "firstAudioP95Ms",
        "bargeinP95Ms",
        "reconnectRecoveryP95Ms",
    ):
        if key not in candidate or key not in baseline or not baseline[key]:
            continue
        regression = (
            (float(candidate[key]) - float(baseline[key])) / float(baseline[key])
        ) * 100
        checks[f"{key.removesuffix('Ms')}Regression"] = (
            regression <= GOOGLE_LIVE_LIMITS["relativeLatencyRegressionPct"]
        )
        regressions[key] = round(regression, 2)
    return {"checks": checks, "regressionPct": regressions, "pass": all(checks.values())}


def reliability_verdict(
    expected_identity: Mapping[str, Any], layers: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Fail closed when a layer is not passing or identifies another candidate."""
    failures = []
    for layer in layers:
        if layer.get("status") != "PASS":
            failures.append(
                {
                    "code": f"LAYER_{layer.get('status', 'MISSING')}",
                    "layer": layer.get("name"),
                }
            )
        if layer.get("candidateIdentity") != expected_identity:
            failures.append(
                {"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": layer.get("name")}
            )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": expected_identity,
        "layers": list(layers),
        "failures": failures,
    }


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
