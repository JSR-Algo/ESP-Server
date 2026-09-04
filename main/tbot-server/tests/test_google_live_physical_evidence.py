import json
from pathlib import Path

import pytest

from scripts.google_live_physical_evidence import (
    PhysicalEvidenceClient,
    PhysicalEvidenceError,
    build_enrollment,
    compose_physical_report,
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
    finalized = False
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
        nonlocal finalized
        calls.append((method, path, body))
        if path.endswith("/finalize"):
            finalized = True
            return {**ready, "status": "PASS"}
        if method == "POST":
            return {"data": {"registered": True, "journeyId": "physical.run"}}
        return {**ready, "status": "PASS"} if finalized else ready

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
    assert calls[-2][1].endswith("/finalize")
    assert calls[-1][:2] == ("GET", "/internal/devices/device-1/google-live-evidence/physical.run")
    assert all(not any(word in path for word in ("deploy", "flash", "reset")) for _, path, _ in calls)


def test_client_rejects_terminal_snapshot_for_another_journey():
    ready = {
        "journeyId": "physical.run", "status": "ACTIVE", "transcriptExpectedCount": 11,
        "transcriptObservedCount": 11, "transcriptMatchedCount": 11, "transcriptMismatchCount": 0,
        "transcriptMissingCount": 0, "transcriptMatchedSlots": list(range(1, 12)),
        "transcriptMatchedPhases": ["interrupt"] * 10 + ["post_lesson"],
        "transcriptOrderingProof": True, "postInterruptVerdict": True,
        "postLessonVerdict": True, "readyToFinalize": True,
    }
    finalized = False

    def request(method, path, body=None):
        nonlocal finalized
        if method == "POST" and path.endswith("/finalize"):
            finalized = True
            return {**ready, "status": "PASS"}
        if method == "POST":
            return {"data": {"registered": True, "journeyId": "physical.run"}}
        if method == "DELETE":
            return {}
        return {**ready, "journeyId": "other.run", "status": "PASS"} if finalized else ready

    client = PhysicalEvidenceClient("http://127.0.0.1:8003", "device-1", "secret", request=request)
    with pytest.raises(PhysicalEvidenceError, match="journey mismatch"):
        client.capture(journey_id="physical.run", enrollment={"clientId": "client-1"}, timeout_sec=1, poll_interval_sec=0)


def test_terminal_snapshot_is_retained_for_report_binding():
    ready = {"journeyId": "physical.run", "status": "ACTIVE", "transcriptExpectedCount": 11, "transcriptObservedCount": 11, "transcriptMatchedCount": 11, "transcriptMismatchCount": 0, "transcriptMissingCount": 0, "transcriptMatchedSlots": list(range(1, 12)), "transcriptMatchedPhases": ["interrupt"] * 10 + ["post_lesson"], "transcriptOrderingProof": True, "postInterruptVerdict": True, "postLessonVerdict": True, "readyToFinalize": True}
    terminal = {**ready, "status": "PASS", "finalizedAt": "2026-09-04T01:02:03Z"}
    finalized = False
    def request(method, path, body=None):
        nonlocal finalized
        if method == "POST" and path.endswith("/finalize"):
            finalized = True
            return terminal
        if method == "POST":
            return {"data": {"registered": True, "journeyId": "physical.run"}}
        if method == "DELETE":
            return {}
        return terminal if finalized else ready
    result = PhysicalEvidenceClient("http://127.0.0.1:8003", "device-1", "secret", request=request).capture(journey_id="physical.run", enrollment={"clientId": "client-1"}, timeout_sec=1, poll_interval_sec=0)
    assert result["transcriptMatchedCount"] == 11
    assert result["finalizedAt"] == "2026-09-04T01:02:03Z"


def test_composition_invokes_existing_audit_and_writes_bound_report(tmp_path: Path):
    log = tmp_path / "server.log"
    log.write_text("websocket evidence\nphysical evidence\n")
    soak = {"status": "PASS", "candidateIdentity": IDENTITY}
    seen = {}

    def audit(text, **kwargs):
        seen.update(kwargs)
        assert "private" not in text
        return {"passed": True, "missing": []}

    output = tmp_path / "physical" / "report.json"
    bounded = tmp_path / "physical" / "server-window.log"
    server_output = tmp_path / "physical" / "server-report.json"
    report = compose_physical_report(
        raw_server_log=log,
        journey_id="physical.run",
        candidate_soak_report=soak,
        candidate_identity=IDENTITY,
        device_id="device-1",
        client_id="client-1",
        output=output,
        bounded_log_output=bounded,
        server_report_output=server_output,
        audit_fn=audit,
        selector_fn=lambda lines, journey: [line for line in lines if journey.split(".")[0] in line],
        analyzer_fn=lambda path: {
            "status": "PASS",
            "evidenceScope": {"journeyId": "physical.run"},
            "logWindow": {"windowId": "physical-window", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"},
        },
    )
    assert seen["require_receive_loop_balance"] is True
    assert seen["min_interrupts"] == 10
    assert report["logEvidence"]["evidenceScope"]["journeyId"] == "physical.run"
    assert "websocket" not in bounded.read_text()
    assert json.loads(server_output.read_text()) == report["logEvidence"]
    assert json.loads(output.read_text())["candidateSoakEvidence"] == soak


def test_default_bounded_window_is_ephemeral(tmp_path: Path):
    log = tmp_path / "server.log"
    log.write_text("private transcript must not persist\n")
    output = tmp_path / "physical" / "report.json"
    compose_physical_report(
        raw_server_log=log, journey_id="physical.run",
        candidate_soak_report={"status": "PASS"}, candidate_identity=IDENTITY,
        device_id="device-1", client_id="client-1", output=output,
        selector_fn=lambda lines, journey: lines,
        analyzer_fn=lambda path: {"status": "PASS", "evidenceScope": {"journeyId": "physical.run"}, "logWindow": {"windowId": "physical.run", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"}},
        audit_fn=lambda text, **kwargs: {"passed": True},
    )
    assert not (output.parent / "server-window.log").exists()
    assert "private transcript" not in "".join(path.read_text(errors="ignore") for path in output.parent.iterdir())
