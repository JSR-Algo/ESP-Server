import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_physical_smoke_audit as physical_fixture

from scripts.analyze_google_live_log import correlate_websocket_bargein_evidence
from scripts.google_live_release_gate import (
    RELEASE_SCHEMA_VERSION,
    REQUIRED_LAYERS,
    aggregate_release_evidence,
    load_checksum_manifest,
)

_PHYSICAL_CASE = physical_fixture.PhysicalSmokeAuditTest()
_OPTIONS = _PHYSICAL_CASE._candidate_audit_options()
IDENTITY = _OPTIONS["candidate_identity"]


def _websocket_report() -> dict:
    log_report = copy.deepcopy(_OPTIONS["reliability_report"])
    scope = log_report["evidenceScope"]
    transport = {
        "schemaVersion": "google-live-reliability.v1",
        "name": "websocket_audio_bargein_transport",
        "status": "SKIPPED",
        "candidateIdentity": copy.deepcopy(IDENTITY),
        "pendingCode": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
        "correlationSource": "server_log",
        "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
        "aggregateReleaseEligible": False,
        "interruptStopMarkerObserved": True,
        "replacementResponseStarted": True,
        "replacementResponseStopped": True,
        "replacementBinaryChunks": 2,
        "bargeinStopMs": 300.0,
        "maxServerOutputGapMs": 80.0,
        "journeyId": scope["journeyId"],
        "serverConnectionId": scope["connectionId"],
        "liveConnectionId": scope["liveConnectionId"],
        "peerIdentityHash": scope["peerIdentityHash"],
        "evidenceScope": copy.deepcopy(scope),
        "initialLiveConnectionId": log_report["initialLiveConnectionId"],
        "finalLiveConnectionId": log_report["finalLiveConnectionId"],
        "liveConnectionTransitions": copy.deepcopy(log_report["liveConnectionTransitions"]),
        "logWindow": copy.deepcopy(log_report["logWindow"]),
    }
    return correlate_websocket_bargein_evidence(
        transport, log_report, expected_candidate_identity=IDENTITY
    )


def _reports() -> dict[str, dict]:
    return {
        "deterministic": {
            "schemaVersion": "google-live-reliability.v1",
            "name": "deterministic",
            "status": "PASS",
            "candidateIdentity": copy.deepcopy(IDENTITY),
            "testVerdict": {
                "status": "PASS",
                "total": 646,
                "failed": 0,
                "skipped": 0,
                "failures": [],
            },
            "failures": [],
        },
        "server_regression": copy.deepcopy(_OPTIONS["reliability_report"]),
        "real_api": {
            "schemaVersion": "google-live-reliability.v1",
            "name": "real_api",
            "status": "PASS",
            "candidateIdentity": copy.deepcopy(IDENTITY),
            "attempts": 1,
            "audioChunks": 3,
            "connectionMs": 100.0,
            "firstServerEventMs": 300.0,
            "firstAudioMs": 700.0,
        },
        "websocket_e2e": _websocket_report(),
        "physical": _PHYSICAL_CASE._candidate_audit(
            _PHYSICAL_CASE._candidate_physical_log()
        ),
        "candidate_soak": copy.deepcopy(_OPTIONS["candidate_soak_report"]),
    }


def _write_evidence(root: Path) -> tuple[dict[str, Path], dict[str, str], Path]:
    paths = {}
    checksums = {}
    rows = []
    for layer, report in _reports().items():
        path = root / layer.replace("_", "-") / "report.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        paths[layer] = path
        checksums[layer] = digest
        rows.append(f"{digest}  {path.relative_to(root)}")
    manifest = root / "checksums.sha256"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return paths, checksums, manifest


def _rewrite(path: Path, mutate) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    mutate(report)
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


def test_release_passes_only_real_exact_candidate_contracts(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["schemaVersion"] == RELEASE_SCHEMA_VERSION
    assert verdict["status"] == "PASS"
    assert verdict["candidateIdentity"] == IDENTITY
    assert [item["name"] for item in verdict["layers"]] == list(REQUIRED_LAYERS)
    assert verdict["failures"] == []


@pytest.mark.parametrize("missing_layer", REQUIRED_LAYERS)
def test_release_fails_closed_for_missing_layer(tmp_path: Path, missing_layer: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    paths[missing_layer].unlink()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "LAYER_FILE_MISSING" for item in verdict["failures"])


@pytest.mark.parametrize("status", ["FAIL", "SKIPPED", "PENDING", None, 1])
def test_release_rejects_every_non_pass_status(tmp_path: Path, status: object) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["real_api"], lambda report: report.update(status=status))
    checksums["real_api"] = hashlib.sha256(paths["real_api"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "LAYER_CONTRACT_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize("field", list(IDENTITY))
def test_release_rejects_identity_mismatch_in_every_dimension(tmp_path: Path, field: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["real_api"], lambda report: report["candidateIdentity"].update({field: "wrong"}))
    checksums["real_api"] = hashlib.sha256(paths["real_api"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "CANDIDATE_IDENTITY_MISMATCH" and item["field"] == field for item in verdict["failures"])


def test_release_rejects_corrupt_json_without_leaking_contents(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    paths["real_api"].write_text('{"apiKey":"super-secret"', encoding="utf-8")
    checksums["real_api"] = hashlib.sha256(paths["real_api"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert "super-secret" not in json.dumps(verdict)
    assert any(item["code"] == "LAYER_JSON_INVALID" for item in verdict["failures"])


def test_release_rejects_missing_and_mismatched_trusted_checksum(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    checksums.pop("deterministic")
    checksums["physical"] = "0" * 64
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    codes = {item["code"] for item in verdict["failures"]}
    assert {"CHECKSUM_MISSING", "CHECKSUM_MISMATCH"} <= codes


def test_self_embedded_checksum_cannot_authorize_tampering(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)

    def tamper(report: dict) -> None:
        report["firstAudioMs"] = 999999.0
        unsigned = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        report["checksum"] = f"sha256:{hashlib.sha256(unsigned).hexdigest()}"

    _rewrite(paths["real_api"], tamper)
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert any(item["code"] == "CHECKSUM_MISMATCH" for item in verdict["failures"])


def test_checksum_manifest_maps_exact_files_and_allows_other_bounded_artifacts(tmp_path: Path) -> None:
    paths, checksums, manifest = _write_evidence(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + f"{'0' * 64}  timeline.log\n",
        encoding="utf-8",
    )
    assert load_checksum_manifest(manifest, paths) == checksums


def test_checksum_manifest_rejects_duplicate_report(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text(manifest.read_text(encoding="utf-8") + f"{'0' * 64}  deterministic/report.json\n" + f"{'0' * 64}  ../outside.json\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_checksum_manifest(manifest, paths)


def test_checksum_manifest_rejects_path_escape(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + f"{'0' * 64}  ../outside.json\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_checksum_manifest(manifest, paths)


@pytest.mark.parametrize(
    ("layer", "mutate"),
    [
        ("deterministic", lambda report: report["testVerdict"].update(failed=1)),
        ("server_regression", lambda report: report.update(receiveLoopBalance=1)),
        ("real_api", lambda report: report.update(firstAudioMs=1801.0)),
        ("websocket_e2e", lambda report: report.update(aggregateReleaseEligible=False)),
        ("websocket_e2e", lambda report: report.update(maxServerOutputGapMs=251.0)),
        ("physical", lambda report: report["firstAudioLatencyMs"].update(p95=1801.0)),
        ("physical", lambda report: report.update(receiveLoopBalance=1)),
        ("candidate_soak", lambda report: report["latencyComparison"].update(pass_=False)),
        ("candidate_soak", lambda report: report["resourceVerdict"].update(status="FAIL")),
        ("candidate_soak", lambda report: report["cleanupVerdict"].update(websocketClosed=False)),
    ],
)
def test_release_rejects_layer_specific_nested_tampering(tmp_path: Path, layer: str, mutate) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths[layer], mutate)
    if layer == "candidate_soak":
        report = json.loads(paths[layer].read_text(encoding="utf-8"))
        if "pass_" in report.get("latencyComparison", {}):
            report["latencyComparison"]["pass"] = report["latencyComparison"].pop("pass_")
            paths[layer].write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    checksums[layer] = hashlib.sha256(paths[layer].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "LAYER_CONTRACT_INVALID" for item in verdict["failures"])


def test_cli_reads_checksum_manifest_and_writes_deterministic_failure(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    _rewrite(paths["real_api"], lambda report: report.update(status="SKIPPED"))
    out = tmp_path / "release-verdict.json"
    script = Path(__file__).parents[1] / "scripts" / "google_live_release_gate.py"
    command = [sys.executable, str(script), "--expected-git-sha", IDENTITY["gitSha"], "--expected-image-digest", IDENTITY["imageDigest"], "--expected-firmware-identity", IDENTITY["firmwareIdentity"], "--expected-config-fingerprint", IDENTITY["configFingerprint"], "--expected-fixture-sha256", IDENTITY["fixtureSha256"], "--checksums-file", str(manifest), "--out", str(out)]
    for layer in REQUIRED_LAYERS:
        command.extend(["--layer", f"{layer}={paths[layer]}"])
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(completed.stdout)["status"] == "FAIL"
