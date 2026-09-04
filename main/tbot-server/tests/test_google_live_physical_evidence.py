import json
from pathlib import Path

import pytest

from scripts.google_live_physical_evidence import (
    PhysicalEvidenceClient,
    PhysicalEvidenceError,
    build_enrollment,
    validate_ready_snapshot,
)


IDENTITY = {
    "gitSha": "a" * 40,
    "imageDigest": "sha256:" + "b" * 64,
    "firmwareIdentity": "firmware-1",
    "configFingerprint": "sha256:" + "c" * 64,
    "fixtureSha256": "d" * 64,
}


def test_enrollment_hashes_transcripts_and_retains_no_plaintext():
    key = bytearray(b"k" * 32)
    enrollment = build_enrollment(
        device_id="device-1",
        client_id="client-1",
        journey_id="physical.20260904T010203Z",
        transcript_plan=[
            {"slot": index, "phase": "interrupt", "text": f"private {index}"}
            for index in range(1, 11)
        ]
        + [{"slot": 11, "phase": "post_lesson", "text": "private final"}],
        hmac_key=key,
        ttl_sec=120,
    )
    encoded = json.dumps(enrollment)
    assert "private" not in encoded
    assert enrollment["normalizationVersion"] == "google-live-transcript-nfkc-casefold.v1"
    assert len(enrollment["transcriptPlan"]) == 11


def test_ready_snapshot_requires_exact_ordered_physical_proof():
    snapshot = {
        "journeyId": "physical.run",
        "status": "ACTIVE",
        "transcriptExpectedCount": 11,
        "transcriptObservedCount": 11,
        "transcriptMatchedCount": 11,
        "transcriptMismatchCount": 0,
        "transcriptMissingCount": 0,
        "transcriptMatchedSlots": list(range(1, 12)),
        "transcriptMatchedPhases": ["interrupt"] * 10 + ["post_lesson"],
        "transcriptOrderingProof": True,
        "postInterruptVerdict": True,
        "postLessonVerdict": True,
        "readyToFinalize": True,
    }
    validate_ready_snapshot(snapshot, journey_id="physical.run")
    snapshot["transcriptMatchedSlots"] = list(reversed(range(1, 12)))
    with pytest.raises(PhysicalEvidenceError):
        validate_ready_snapshot(snapshot, journey_id="physical.run")


def test_client_always_deletes_enrollment_after_timeout():
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"data": {"registered": True, "journeyId": "physical.run"}}
        return {
            "journeyId": "physical.run",
            "status": "ACTIVE",
            "readyToFinalize": False,
        }

    client = PhysicalEvidenceClient(
        "http://127.0.0.1:8003", "device-1", "secret", request=request
    )
    with pytest.raises(PhysicalEvidenceError):
        client.capture(
            journey_id="physical.run",
            enrollment={"clientId": "client-1"},
            timeout_sec=0.001,
            poll_interval_sec=0,
        )
    assert calls[-1][0] == "DELETE"


def test_client_finalizes_without_any_hardware_control_request():
    calls = []
    ready = {
        "journeyId": "physical.run",
        "status": "ACTIVE",
        "transcriptExpectedCount": 11,
        "transcriptObservedCount": 11,
        "transcriptMatchedCount": 11,
        "transcriptMismatchCount": 0,
        "transcriptMissingCount": 0,
        "transcriptMatchedSlots": list(range(1, 12)),
        "transcriptMatchedPhases": ["interrupt"] * 10 + ["post_lesson"],
        "transcriptOrderingProof": True,
        "postInterruptVerdict": True,
        "postLessonVerdict": True,
        "readyToFinalize": True,
    }

    def request(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/finalize"):
            return {**ready, "status": "PASS"}
        if method == "POST":
            return {"data": {"registered": True, "journeyId": "physical.run"}}
        return ready

    client = PhysicalEvidenceClient(
        "http://127.0.0.1:8003", "device-1", "secret", request=request
    )
    result = client.capture(
        journey_id="physical.run",
        enrollment={"clientId": "client-1"},
        candidate_identity=IDENTITY,
        timeout_sec=1,
        poll_interval_sec=0,
    )
    assert result["status"] == "PASS"
    assert [method for method, path, _ in calls if path.endswith("candidate-identity")] == ["PUT"]
    assert calls[-1][1].endswith("/finalize")
    assert all(not any(word in path for word in ("deploy", "flash", "reset")) for _, path, _ in calls)
