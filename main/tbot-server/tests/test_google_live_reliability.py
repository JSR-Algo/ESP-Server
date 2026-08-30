from __future__ import annotations

import hashlib
import json

from scripts.google_live_reliability import (
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    build_candidate_identity,
    compare_latency_baseline,
    percentile,
    redact_mapping,
    reliability_verdict,
    resource_verdict,
    sample_process_resources,
)


def _resource_sample(index: int, *, rss: int, fds: int, tasks: int, threads: int) -> dict:
    return {
        "index": index,
        "rssBytes": rss,
        "fdCount": fds,
        "asyncioTaskCount": tasks,
        "threadCount": threads,
    }


def test_contract_constants_match_google_live_release_limits() -> None:
    assert SCHEMA_VERSION == "google-live-reliability.v1"
    assert GOOGLE_LIVE_LIMITS == {
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


def test_redact_mapping_recursively_redacts_secret_keys_and_preserves_safe_values() -> None:
    source = {
        "api_key": "key-secret",
        "authorization": "Bearer secret",
        "model": "gemini-live",
        "nested": {
            "token": "token-secret",
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "session_resumption_handle": "resume-secret",
            "handle": "handle-secret",
            "voice_name": "Kore",
        },
        "items": [{"token": "list-secret", "safe": 7}],
    }

    assert redact_mapping(source) == {
        "api_key": "<redacted>",
        "authorization": "<redacted>",
        "model": "gemini-live",
        "nested": {
            "token": "<redacted>",
            "access_token": "<redacted>",
            "refresh_token": "<redacted>",
            "session_resumption_handle": "<redacted>",
            "handle": "<redacted>",
            "voice_name": "Kore",
        },
        "items": [{"token": "<redacted>", "safe": 7}],
    }


def test_candidate_identity_is_deterministic_and_fingerprints_only_redacted_config() -> None:
    config = {"model": "gemini-live", "voice_name": "Kore", "api_key": "secret"}
    redacted = {"api_key": "<redacted>", "model": "gemini-live", "voice_name": "Kore"}
    expected_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    identity = build_candidate_identity("abc123", "sha256:image", "fw-7", config, "fixture-hash")
    reordered_identity = build_candidate_identity(
        "abc123",
        "sha256:image",
        "fw-7",
        {"api_key": "different-secret", "voice_name": "Kore", "model": "gemini-live"},
        "fixture-hash",
    )

    assert identity == {
        "gitSha": "abc123",
        "imageDigest": "sha256:image",
        "firmwareIdentity": "fw-7",
        "fixtureSha256": "fixture-hash",
        "configFingerprint": expected_fingerprint,
    }
    assert reordered_identity == identity
    assert "secret" not in json.dumps(identity)


def test_latency_baseline_checks_only_shared_nonzero_metrics_at_fifteen_percent() -> None:
    result = compare_latency_baseline(
        {
            "firstAudioP50Ms": 1160,
            "firstAudioP95Ms": 1700,
            "bargeinP95Ms": 500,
            "reconnectRecoveryP95Ms": 900,
        },
        {
            "firstAudioP50Ms": 1000,
            "firstAudioP95Ms": 1500,
            "bargeinP95Ms": 0,
        },
    )

    assert result == {
        "checks": {
            "firstAudioP50Regression": False,
            "firstAudioP95Regression": True,
        },
        "regressionPct": {"firstAudioP50Ms": 16.0, "firstAudioP95Ms": 13.33},
        "pass": False,
    }


def test_reliability_verdict_keeps_skipped_and_candidate_mismatch_failures() -> None:
    expected = {"gitSha": "expected"}
    report = reliability_verdict(
        expected,
        [
            {"name": "deterministic", "status": "PASS", "candidateIdentity": expected},
            {"name": "physical", "status": "SKIPPED", "candidateIdentity": {"gitSha": "wrong"}},
        ],
    )

    assert report == {
        "schemaVersion": "google-live-reliability.v1",
        "status": "FAIL",
        "candidateIdentity": expected,
        "layers": [
            {"name": "deterministic", "status": "PASS", "candidateIdentity": expected},
            {"name": "physical", "status": "SKIPPED", "candidateIdentity": {"gitSha": "wrong"}},
        ],
        "failures": [
            {"code": "LAYER_SKIPPED", "layer": "physical"},
            {"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": "physical"},
        ],
    }


def test_reliability_verdict_passes_only_all_matching_pass_layers() -> None:
    identity = {"gitSha": "same"}

    report = reliability_verdict(
        identity,
        [
            {"name": "deterministic", "status": "PASS", "candidateIdentity": identity},
            {"name": "physical", "status": "PASS", "candidateIdentity": identity},
        ],
    )

    assert report["status"] == "PASS"
    assert report["failures"] == []


def test_percentile_handles_empty_and_rounded_nearest_rank_values() -> None:
    assert percentile([], 95) is None
    assert percentile([1.1111, 2.2222, 3.3333, 4.4444], 50) == 3.333
    assert percentile([9.87654], 95) == 9.877


def test_process_resource_sample_uses_resource_soak_field_contract() -> None:
    sample = sample_process_resources()

    assert set(sample) == {"rssBytes", "fdCount", "asyncioTaskCount", "threadCount"}
    assert all(type(value) is int and value >= 0 for value in sample.values())


def test_resource_verdict_fails_rss_and_task_leaks_with_exact_soak_semantics() -> None:
    samples = [
        _resource_sample(
            index,
            rss=100 + index * 2_000_000,
            fds=4,
            tasks=1 + index,
            threads=2,
        )
        for index in range(6)
    ]

    verdict = resource_verdict(samples)

    assert verdict["status"] == "FAIL"
    assert verdict["checks"]["rssSlopeBounded"] is False
    assert verdict["checks"]["taskDeltaBounded"] is False
    assert verdict["checks"]["taskSlopeBounded"] is False
    assert {failure["code"] for failure in verdict["failures"]} >= {
        "RSS_SLOPE_EXCEEDED",
        "ASYNCIO_TASK_DELTA_EXCEEDED",
        "ASYNCIO_TASK_SLOPE_EXCEEDED",
    }
    assert verdict["slopes"]["rssBytesPerSample"] == 2_000_000.0
    assert verdict["limits"]["rssDeltaBytes"] == 32 * 1024 * 1024
