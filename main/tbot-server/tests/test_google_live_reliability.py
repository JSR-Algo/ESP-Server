from __future__ import annotations

import hashlib
import json

import pytest

from scripts.google_live_reliability import (
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    build_candidate_identity,
    compare_latency_baseline,
    forbidden_report_fields,
    percentile,
    redact_mapping,
    reliability_verdict,
    resource_verdict,
    sample_process_resources,
)


def test_forbidden_report_fields_is_recursive_normalized_and_value_safe() -> None:
    report = {
        "safe": [{"Raw-Transcript": "private words"}],
        "nested": {"set_cookie": "private cookie"},
        "exceptionCount": 0,
        "transcriptPersisted": False,
    }

    assert forbidden_report_fields(report) == [
        "safe[0].Raw-Transcript",
        "nested.set_cookie",
    ]


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


def test_redact_mapping_normalizes_common_secret_key_variants_without_substring_matches() -> None:
    source = {
        "apiKey": "api-secret",
        "accessToken": "access-secret",
        "refreshToken": "refresh-secret",
        "client_secret": "client-secret",
        "clientSecret": "camel-secret",
        "x-goog-api-key": "google-secret",
        "headers": {
            "X-Api-Key": "header-key-secret",
            "Authorization": "Bearer header-secret",
            "tokenizer": "safe-tokenizer",
            "handlebars": "safe-handlebars",
        },
    }

    assert redact_mapping(source) == {
        "apiKey": "<redacted>",
        "accessToken": "<redacted>",
        "refreshToken": "<redacted>",
        "client_secret": "<redacted>",
        "clientSecret": "<redacted>",
        "x-goog-api-key": "<redacted>",
        "headers": {
            "X-Api-Key": "<redacted>",
            "Authorization": "<redacted>",
            "tokenizer": "safe-tokenizer",
            "handlebars": "safe-handlebars",
        },
    }


def test_candidate_identity_is_deterministic_and_fingerprints_only_redacted_config() -> None:
    config = {"model": "gemini-live", "voice_name": "Kore", "api_key": "secret"}
    redacted = {"api_key": "<redacted>", "model": "gemini-live", "voice_name": "Kore"}
    expected_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    image_digest = f"sha256:{'b' * 64}"
    fixture_sha256 = "a" * 64
    identity = build_candidate_identity("abc123", image_digest, "fw-7", config, fixture_sha256)
    reordered_identity = build_candidate_identity(
        "abc123",
        image_digest,
        "fw-7",
        {"api_key": "different-secret", "voice_name": "Kore", "model": "gemini-live"},
        fixture_sha256,
    )

    assert identity == {
        "gitSha": "abc123",
        "imageDigest": image_digest,
        "firmwareIdentity": "fw-7",
        "fixtureSha256": fixture_sha256,
        "configFingerprint": expected_fingerprint,
    }
    assert reordered_identity == identity
    assert "secret" not in json.dumps(identity)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("git_sha", None),
        ("git_sha", "  "),
        ("image_digest", ""),
        ("firmware_identity", None),
        ("firmware_identity", "\t"),
        ("fixture_sha256", ""),
    ],
)
def test_candidate_identity_rejects_missing_required_values(field: str, value: object) -> None:
    values = {
        "git_sha": "abc123",
        "image_digest": f"sha256:{'b' * 64}",
        "firmware_identity": "fw-7",
        "config": {},
        "fixture_sha256": "a" * 64,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        build_candidate_identity(**values)


@pytest.mark.parametrize(
    "image_digest",
    ["sha256:image", "b" * 64, f"sha256:{'b' * 63}", f"sha256:{'g' * 64}"],
)
def test_candidate_identity_rejects_malformed_image_digest(image_digest: str) -> None:
    with pytest.raises(ValueError, match="image_digest"):
        build_candidate_identity("abc123", image_digest, "fw-7", {}, "a" * 64)


@pytest.mark.parametrize("fixture_sha256", ["fixture", "a" * 63, "g" * 64])
def test_candidate_identity_rejects_malformed_fixture_sha256(fixture_sha256: str) -> None:
    with pytest.raises(ValueError, match="fixture_sha256"):
        build_candidate_identity(
            "abc123", f"sha256:{'b' * 64}", "fw-7", {}, fixture_sha256
        )


def _complete_latency_metrics(**overrides):
    metrics = {
        "firstAudioP50Ms": 1000,
        "firstAudioP95Ms": 1500,
        "bargeinP95Ms": 400,
        "reconnectRecoveryP95Ms": 800,
    }
    metrics.update(overrides)
    return metrics


def test_latency_baseline_checks_all_required_metrics_at_fifteen_percent() -> None:
    result = compare_latency_baseline(
        _complete_latency_metrics(
            firstAudioP50Ms=1160,
            firstAudioP95Ms=1700,
            bargeinP95Ms=500,
            reconnectRecoveryP95Ms=900,
        ),
        _complete_latency_metrics(),
    )

    assert result == {
        "checks": {
            "firstAudioP50Regression": False,
            "firstAudioP95Regression": True,
            "bargeinP95Regression": False,
            "reconnectRecoveryP95Regression": True,
        },
        "regressionPct": {
            "firstAudioP50Ms": 16.0,
            "firstAudioP95Ms": 13.33,
            "bargeinP95Ms": 25.0,
            "reconnectRecoveryP95Ms": 12.5,
        },
        "pass": False,
    }


@pytest.mark.parametrize(
    ("candidate", "baseline"),
    [
        ({}, {}),
        ({"firstAudioP50Ms": 1000}, {"firstAudioP95Ms": 1000}),
    ],
)
def test_latency_baseline_fails_closed_without_comparable_metrics(
    candidate: dict, baseline: dict
) -> None:
    result = compare_latency_baseline(candidate, baseline)

    assert result["checks"] == {}
    assert result["regressionPct"] == {}
    assert result["pass"] is False
    assert result["failures"]
    assert all(item["code"] == "LATENCY_METRIC_INVALID" for item in result["failures"])


@pytest.mark.parametrize("side", ["candidate", "baseline"])
@pytest.mark.parametrize(
    "invalid_value",
    [True, False, -1, float("nan"), float("inf"), float("-inf")],
    ids=[
        "true",
        "false",
        "negative",
        "nan",
        "positive-infinity",
        "negative-infinity",
    ],
)
def test_latency_baseline_rejects_malformed_supplied_metrics(
    side: str, invalid_value: object
) -> None:
    candidate = _complete_latency_metrics()
    baseline = _complete_latency_metrics()
    target = candidate if side == "candidate" else baseline
    target["firstAudioP50Ms"] = invalid_value

    result = compare_latency_baseline(candidate, baseline)

    assert result["pass"] is False
    assert result["failures"] == [
        {
            "code": "LATENCY_METRIC_INVALID",
            "metric": "firstAudioP50Ms",
            "side": side,
        }
    ]
    assert len(result["checks"]) == 3
    assert len(result["regressionPct"]) == 3


def test_latency_baseline_rejects_zero_baseline_metric() -> None:
    result = compare_latency_baseline(
        _complete_latency_metrics(firstAudioP50Ms=0),
        _complete_latency_metrics(firstAudioP50Ms=0),
    )

    assert result["pass"] is False
    assert result["failures"] == [
        {
            "code": "LATENCY_METRIC_INVALID",
            "metric": "firstAudioP50Ms",
            "side": "candidate",
        },
        {
            "code": "LATENCY_METRIC_INVALID",
            "metric": "firstAudioP50Ms",
            "side": "baseline",
        }
    ]


def test_latency_baseline_rejects_extra_metric_instead_of_skipping_it() -> None:
    baseline = _complete_latency_metrics(uncontractedMetricMs=100)

    result = compare_latency_baseline(_complete_latency_metrics(), baseline)

    assert result["pass"] is False
    assert result["failures"] == [
        {
            "code": "LATENCY_METRIC_INVALID",
            "metric": "uncontractedMetricMs",
            "side": "baseline",
        }
    ]


def test_latency_baseline_preserves_valid_checks_while_failing_malformed_metric() -> None:
    result = compare_latency_baseline(
        _complete_latency_metrics(firstAudioP50Ms=True, firstAudioP95Ms=1600),
        _complete_latency_metrics(),
    )

    assert result == {
        "checks": {
            "firstAudioP95Regression": True,
            "bargeinP95Regression": True,
            "reconnectRecoveryP95Regression": True,
        },
        "regressionPct": {
            "firstAudioP95Ms": 6.67,
            "bargeinP95Ms": 0.0,
            "reconnectRecoveryP95Ms": 0.0,
        },
        "pass": False,
        "failures": [
            {
                "code": "LATENCY_METRIC_INVALID",
                "metric": "firstAudioP50Ms",
                "side": "candidate",
            }
        ],
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


def test_reliability_verdict_fails_closed_without_layers() -> None:
    report = reliability_verdict({"gitSha": "same"}, [])

    assert report["status"] == "FAIL"
    assert report["failures"] == [{"code": "LAYERS_MISSING"}]


@pytest.mark.parametrize("name", ["", "   ", None])
def test_reliability_verdict_rejects_empty_layer_names(name: object) -> None:
    identity = {"gitSha": "same"}

    report = reliability_verdict(
        identity, [{"name": name, "status": "PASS", "candidateIdentity": identity}]
    )

    assert report["status"] == "FAIL"
    assert report["failures"] == [{"code": "LAYER_NAME_MISSING", "layer": name}]


def test_reliability_verdict_rejects_duplicate_layer_names() -> None:
    identity = {"gitSha": "same"}
    layer = {"name": "physical", "status": "PASS", "candidateIdentity": identity}

    report = reliability_verdict(identity, [layer, dict(layer)])

    assert report["status"] == "FAIL"
    assert report["failures"] == [{"code": "DUPLICATE_LAYER_NAME", "layer": "physical"}]


def test_percentile_uses_clamped_nearest_rank_boundaries() -> None:
    assert percentile([], 95) is None
    values = [4.4444, 1.1111, 3.3333, 2.2222]
    assert percentile(values, 0) == 1.111
    assert percentile(values, 50) == 2.222
    assert percentile(values, 95) == 4.444
    assert percentile(values, 100) == 4.444
    assert percentile([9.87654], 95) == 9.877


@pytest.mark.parametrize("percentile_value", [-0.001, 100.001])
def test_percentile_rejects_values_outside_zero_to_one_hundred(
    percentile_value: float,
) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        percentile([1], percentile_value)


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
