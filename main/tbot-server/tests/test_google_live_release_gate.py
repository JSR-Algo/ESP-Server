import copy
import hashlib
import json
import os
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
from scripts.google_live_deterministic_evidence import MANIFEST_SCHEMA

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
    correlated = correlate_websocket_bargein_evidence(
        transport, log_report, expected_candidate_identity=IDENTITY
    )
    return {
        "schemaVersion": "google-live-reliability.v1",
        "name": "websocket_e2e",
        "status": "PASS",
        "candidateIdentity": copy.deepcopy(IDENTITY),
        "transportEvidence": transport,
        "logEvidence": log_report,
        "correlatedEvidence": correlated,
        "failures": [],
    }


def _reports() -> dict[str, dict]:
    physical_audit = _PHYSICAL_CASE._candidate_audit(
        _PHYSICAL_CASE._candidate_physical_log()
    )
    physical_audit.update(
        {
            "live_identity": True,
            "live_identity_first_audio_chains": 1,
            "audio_interrupts": 10,
            "aec_live_vad_forward": 10,
            "aec_interruption_chains": 10,
            "live_server_interruption": 10,
            "interrupt_tts_stops": 10,
            "interrupt_stop_chains": 10,
            "interrupt_user_chains": 10,
            "interrupt_relisten_chains": 10,
            "post_interrupt_user_transcripts": 10,
            "realtime_tts_stops": 10,
            "output_relisten_chains": 1,
            "expected_user_transcripts": 10,
            "user_transcripts": 10,
            "user_transcript_expected_matches": 10,
            "post_interrupt_user_transcript_expected_matches": 10,
            "expected_post_lesson_transcripts": 1,
            "post_lesson_response_chains": 1,
            "lesson_prepare": 1,
            "lesson_start": 1,
            "lesson_steps": 1,
            "lesson_step_layers_complete": 1,
            "lesson_prompt_tts": 1,
            "lesson_prompt_after_render": 1,
            "lesson_firmware_rendered": 1,
            "lesson_step_layers_drawn_by_step": 1,
            "lesson_stop": 1,
            "lesson_completed": 1,
        }
    )
    return {
        "deterministic": {
            "schemaVersion": "google-live-reliability.v1",
            "name": "deterministic",
            "status": "PASS",
            "candidateIdentity": copy.deepcopy(IDENTITY),
            "coverageProof": {},
            "testVerdict": {
                "status": "PASS",
                "total": 2,
                "failed": 0,
                "skipped": 0,
                "errors": 0,
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
        "physical": {
            "schemaVersion": "google-live-reliability.v1",
            "name": "physical",
            "status": "PASS",
            "candidateIdentity": copy.deepcopy(IDENTITY),
            "auditReport": physical_audit,
            "productionProfile": {
                "strictMarkersValidated": True,
                "lessonValidated": True,
                "postLessonValidated": True,
                "receiveLoopBalanceRequired": True,
                "sampleCounts": {
                    "firstAudio": 10,
                    "interruptStop": 10,
                    "physicalBargein": 10,
                    "serverOutputGap": 10,
                },
                "budgetsMs": {
                    "firstAudioP50": 1200.0,
                    "firstAudioP95": 1800.0,
                    "interruptStopMax": 250.0,
                    "physicalBargeinP95": 500.0,
                    "serverOutputGapMax": 250.0,
                },
            },
            "logEvidence": copy.deepcopy(_OPTIONS["reliability_report"]),
            "candidateSoakEvidence": copy.deepcopy(_OPTIONS["candidate_soak_report"]),
            "failures": [],
        },
        "candidate_soak": copy.deepcopy(_OPTIONS["candidate_soak_report"]),
    }


def _write_evidence(root: Path) -> tuple[dict[str, Path], dict[str, str], Path]:
    deterministic_dir = root / "deterministic"
    deterministic_dir.mkdir(parents=True)
    nodes = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    node_manifest = deterministic_dir / "node-manifest.txt"
    node_manifest.write_text("\n".join(nodes) + "\n", encoding="utf-8")
    junit = deterministic_dir / "pytest.xml"
    cases = "".join(
        f'<testcase classname="suite" name="{node.rsplit("::", 1)[-1]}"><properties>'
        f'<property name="google_live_nodeid" value="{node}" /></properties></testcase>'
        for node in nodes
    )
    junit.write_text(
        f'<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )
    reports = _reports()
    reports["deterministic"]["coverageProof"] = {
        "manifestSchema": MANIFEST_SCHEMA,
        "manifestSha256": hashlib.sha256(node_manifest.read_bytes()).hexdigest(),
        "manifestNodeCount": 2,
        "executedNodeCount": 2,
        "junitSha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
    }
    paths = {}
    checksums = {}
    rows = []
    for layer, report in reports.items():
        path = root / layer.replace("_", "-") / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        paths[layer] = path
        checksums[layer] = digest
        rows.append(f"{digest}  {path.relative_to(root)}")
    for support in (node_manifest, junit):
        name = "deterministic_manifest" if support == node_manifest else "deterministic_junit"
        digest = hashlib.sha256(support.read_bytes()).hexdigest()
        paths[name] = support
        checksums[name] = digest
        rows.append(f"{digest}  {support.relative_to(root)}")
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


@pytest.mark.parametrize("support_name", ["node-manifest.txt", "pytest.xml"])
def test_release_rejects_tampered_deterministic_support(tmp_path: Path, support_name: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    support = paths["deterministic"].parent / support_name
    support.write_bytes(support.read_bytes() + b"tampered")
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"].startswith("DETERMINISTIC_") for item in verdict["failures"])


def test_release_independently_rejects_suite_level_junit_error_even_with_rebound_hashes(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    junit.write_text(
        junit.read_text(encoding="utf-8").replace(
            '<testsuite tests="2" failures="0" errors="0" skipped="0">',
            '<testsuite tests="2" failures="0" errors="1" skipped="0"><error message="session crashed" />',
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "DETERMINISTIC_SUPPORT_CONTRACT_INVALID" for item in verdict["failures"])


def test_release_independently_rejects_secret_junit_without_leaking(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    junit.write_bytes(
        junit.read_bytes().replace(b'<testsuites>', b'<testsuites>GOOGLE_API_KEY=secret', 1)
    )
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert "secret" not in json.dumps(verdict).lower()


def test_release_rejects_rebound_junit_sensitive_property_pair_without_leaking(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    secret = "my-real-credential"
    extra = f'<property name="GOOGLE_API_KEY" value="{secret}" />'.encode()
    junit.write_bytes(junit.read_bytes().replace(b"</properties>", extra + b"</properties>", 1))
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert secret not in json.dumps(verdict)


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


@pytest.mark.parametrize(
    "unsupported",
    [
        {"failures": [{"code": "FAILED_ATTEMPT"}]},
        {"errorClass": "network_or_transport"},
        {"error": {"class": "network_or_transport"}},
        {"errorMessage": "must-never-leak"},
        {"exception": "must-never-leak"},
        {"unexpected": True},
    ],
)
def test_real_api_pass_rejects_failure_only_or_unsupported_fields(
    tmp_path: Path, unsupported: dict
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["real_api"], lambda report: report.update(unsupported))
    checksums["real_api"] = hashlib.sha256(paths["real_api"].read_bytes()).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert "must-never-leak" not in json.dumps(verdict)
    assert any(
        item["code"] == "LAYER_CONTRACT_INVALID" and item["layer"] == "real_api"
        for item in verdict["failures"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "FAIL", "attempts": 1},
        {"status": "PASS", "attempts": 0},
        {"attempts": True},
        {"audioChunks": 0},
        {"audioChunks": 1.5},
        {"connectionMs": None},
        {"firstServerEventMs": -1.0},
        {"firstAudioMs": float("nan")},
        {"connectionMs": 400.0, "firstServerEventMs": 300.0},
        {"firstServerEventMs": 800.0, "firstAudioMs": 700.0},
    ],
)
def test_real_api_pass_rejects_malformed_or_contradictory_evidence(
    tmp_path: Path, changes: dict
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["real_api"], lambda report: report.update(changes))
    checksums["real_api"] = hashlib.sha256(paths["real_api"].read_bytes()).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"


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


@pytest.mark.parametrize(
    ("layer", "forbidden_key", "container"),
    [
        ("deterministic", "rawTranscript", "nested"),
        ("server_regression", "Cookie", "list"),
        ("real_api", "set-cookie", "nested"),
        ("websocket_e2e", "credential", "list"),
        ("physical", "SECRET", "nested"),
        ("candidate_soak", "exception", "list"),
        ("deterministic", "audioChunk", "nested"),
        ("real_api", "session_resumption_handle", "list"),
    ],
)
def test_release_rejects_canonical_forbidden_fields_anywhere(
    tmp_path: Path, layer: str, forbidden_key: str, container: str
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)

    def tamper(report: dict) -> None:
        value = {forbidden_key: "must-never-leak"}
        report["releaseMetadata"] = value if container == "nested" else [{"safe": value}]

    _rewrite(paths[layer], tamper)
    checksums[layer] = hashlib.sha256(paths[layer].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    encoded = json.dumps(verdict)

    assert verdict["status"] == "FAIL"
    assert "must-never-leak" not in encoded
    assert any(item["code"] == "LAYER_CONTRACT_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize(
    "safe_key",
    ["exceptionCount", "transcriptPersisted", "rawAudioPersisted", "tokenCount"],
)
def test_release_allows_safe_near_miss_metadata_keys(tmp_path: Path, safe_key: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["deterministic"], lambda report: report.update({safe_key: 0}))
    checksums["deterministic"] = hashlib.sha256(
        paths["deterministic"].read_bytes()
    ).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "PASS"


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
        (
            "websocket_e2e",
            lambda report: report["correlatedEvidence"].update(
                aggregateReleaseEligible=False
            ),
        ),
        (
            "websocket_e2e",
            lambda report: report["transportEvidence"].update(
                maxServerOutputGapMs=251.0
            ),
        ),
        (
            "physical",
            lambda report: report["auditReport"]["firstAudioLatencyMs"].update(
                p95=1801.0
            ),
        ),
        (
            "physical",
            lambda report: report["auditReport"].update(receiveLoopBalance=1),
        ),
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


@pytest.mark.parametrize(
    "tamper",
    [
        lambda report: report["transportEvidence"].pop("logWindow"),
        lambda report: report["transportEvidence"]["evidenceScope"].update(
            connectionId="fabricated"
        ),
        lambda report: report["transportEvidence"]["evidenceScope"].update(
            liveConnectionId="fabricated"
        ),
        lambda report: report["transportEvidence"]["evidenceScope"].update(
            serverStartUtc="not-a-utc-time"
        ),
        lambda report: report["logEvidence"].pop("evidenceScope"),
        lambda report: report["logEvidence"].update(logWindow={}),
        lambda report: report["transportEvidence"].update(
            liveConnectionTransitions=[
                {
                    "sequence": 2,
                    "fromLiveConnectionId": "live-1",
                    "toLiveConnectionId": "live-2",
                }
            ]
        ),
        lambda report: report["correlatedEvidence"].update(logWindow={}),
    ],
)
def test_websocket_release_recomputes_exact_task4_task5_correlation(
    tmp_path: Path, tamper
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["websocket_e2e"], tamper)
    checksums["websocket_e2e"] = hashlib.sha256(
        paths["websocket_e2e"].read_bytes()
    ).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(
        item["code"] == "LAYER_CONTRACT_INVALID"
        and item["layer"] == "websocket_e2e"
        for item in verdict["failures"]
    )


@pytest.mark.parametrize(
    "tamper",
    [
        lambda report: report["auditReport"].update(malformedLatencyMarkers=1),
        lambda report: report["auditReport"].update(live_identity_mismatches=1),
        lambda report: report["auditReport"].update(live_identity=False),
        lambda report: report["auditReport"].update(audio_interrupts=11),
        lambda report: report["auditReport"].pop("lesson_completed"),
        lambda report: report["auditReport"].update(lesson_stop=0),
        lambda report: report["auditReport"]["serverOutputGapMs"].update(observed=0),
        lambda report: report["auditReport"]["serverOutputGapMs"].update(invalid=1),
        lambda report: report["auditReport"]["serverOutputGapMs"][
            "unexplainedResidualMs"
        ].update(max=251.0),
        lambda report: report["auditReport"]["serverOutputGapMs"].update(
            excludedIntentional=1
        ),
        lambda report: report["auditReport"]["serverOutputGapMs"].update(
            excludedDurationMs=-1
        ),
        lambda report: report["auditReport"]["serverOutputGapMs"][
            "rawGapDurationMs"
        ].update(min=200.0, max=100.0),
        lambda report: report["auditReport"].update(
            first_audio_out_ms={"count": 10, "min": 1.0, "max": 1.0, "p50": 1.0, "p95": 1.0}
        ),
        lambda report: report["auditReport"].update(input_audio_diag=0),
        lambda report: report["auditReport"].update(user_transcripts=0),
        lambda report: report["productionProfile"].update(
            strictMarkersValidated=False
        ),
        lambda report: report["logEvidence"].update(receiveLoopBalance=1),
        lambda report: report["candidateSoakEvidence"]["cleanupVerdict"].update(
            websocketClosed=False
        ),
        lambda report: report["candidateSoakEvidence"].update(
            candidateIdentity={**IDENTITY, "gitSha": "other"}
        ),
        lambda report: report["logEvidence"].update(alternateValidEvidence=True),
        lambda report: report["candidateSoakEvidence"].update(
            alternateValidEvidence=True
        ),
    ],
)
def test_physical_release_revalidates_full_task7_and_upstream_bindings(
    tmp_path: Path, tamper
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    _rewrite(paths["physical"], tamper)
    checksums["physical"] = hashlib.sha256(paths["physical"].read_bytes()).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(
        item["code"]
        in {"LAYER_CONTRACT_INVALID", "PHYSICAL_UPSTREAM_BINDING_MISMATCH"}
        and item["layer"] == "physical"
        for item in verdict["failures"]
    )


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


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_cli_never_overwrites_evidence_through_output_alias(
    tmp_path: Path, alias_kind: str
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    target = paths["real_api"]
    original = target.read_bytes()
    if alias_kind == "direct":
        out = target
    else:
        out = tmp_path / f"{alias_kind}-release-verdict.json"
        if alias_kind == "symlink":
            out.symlink_to(target)
        else:
            out.hardlink_to(target)
    completed = _run_cli(paths, manifest, out)

    assert completed.returncode == 1
    assert target.read_bytes() == original
    assert out.read_bytes() == original


def test_cli_never_overwrites_checksum_manifest(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    original = manifest.read_bytes()

    completed = _run_cli(paths, manifest, manifest)

    assert completed.returncode == 1
    assert manifest.read_bytes() == original


def test_cli_writes_failure_atomically_for_malformed_checksum_manifest(
    tmp_path: Path,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text("malformed checksum row\n", encoding="utf-8")
    out = tmp_path / "release-verdict.json"

    completed = _run_cli(paths, manifest, out)

    assert completed.returncode == 1
    verdict = json.loads(out.read_text(encoding="utf-8"))
    assert verdict["status"] == "FAIL"
    assert json.loads(completed.stdout) == verdict
    assert not list(tmp_path.glob(".release-verdict.json.*.tmp"))


def test_cli_writes_failure_atomically_for_malformed_layer_argument(
    tmp_path: Path,
) -> None:
    _, _, manifest = _write_evidence(tmp_path)
    out = tmp_path / "release-verdict.json"
    script = Path(__file__).parents[1] / "scripts" / "google_live_release_gate.py"
    command = [
        sys.executable,
        str(script),
        "--expected-git-sha",
        IDENTITY["gitSha"],
        "--expected-image-digest",
        IDENTITY["imageDigest"],
        "--expected-firmware-identity",
        IDENTITY["firmwareIdentity"],
        "--expected-config-fingerprint",
        IDENTITY["configFingerprint"],
        "--expected-fixture-sha256",
        IDENTITY["fixtureSha256"],
        "--checksums-file",
        str(manifest),
        "--layer",
        "malformed-layer-argument",
        "--out",
        str(out),
    ]

    completed = subprocess.run(command, text=True, capture_output=True, check=False)

    assert completed.returncode == 1
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "FAIL"
    assert not list(tmp_path.glob(".release-verdict.json.*.tmp"))


def test_malformed_duplicate_layers_cannot_hide_output_alias(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    target = paths["real_api"]
    original = target.read_bytes()
    script = Path(__file__).parents[1] / "scripts" / "google_live_release_gate.py"
    command = [
        sys.executable,
        str(script),
        "--expected-git-sha",
        IDENTITY["gitSha"],
        "--expected-image-digest",
        IDENTITY["imageDigest"],
        "--expected-firmware-identity",
        IDENTITY["firmwareIdentity"],
        "--expected-config-fingerprint",
        IDENTITY["configFingerprint"],
        "--expected-fixture-sha256",
        IDENTITY["fixtureSha256"],
        "--checksums-file",
        str(manifest),
        "--layer",
        f"real_api={target}",
        "--layer",
        f"real_api={target}",
        "--out",
        str(target),
    ]

    completed = subprocess.run(command, text=True, capture_output=True, check=False)

    assert completed.returncode == 1
    assert target.read_bytes() == original


def _run_cli(paths: dict[str, Path], manifest: Path, out: Path) -> subprocess.CompletedProcess:
    script = Path(__file__).parents[1] / "scripts" / "google_live_release_gate.py"
    command = [sys.executable, str(script), "--expected-git-sha", IDENTITY["gitSha"], "--expected-image-digest", IDENTITY["imageDigest"], "--expected-firmware-identity", IDENTITY["firmwareIdentity"], "--expected-config-fingerprint", IDENTITY["configFingerprint"], "--expected-fixture-sha256", IDENTITY["fixtureSha256"], "--checksums-file", str(manifest), "--out", str(out)]
    for layer in REQUIRED_LAYERS:
        command.extend(["--layer", f"{layer}={paths[layer]}"])
    command.extend(["--support", f"deterministic_manifest={paths['deterministic'].parent / 'node-manifest.txt'}"])
    command.extend(["--support", f"deterministic_junit={paths['deterministic'].parent / 'pytest.xml'}"])
    return subprocess.run(command, text=True, capture_output=True, check=False)


@pytest.mark.parametrize("support_name", ["node-manifest.txt", "pytest.xml"])
@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_cli_never_overwrites_deterministic_support_through_output_alias(
    tmp_path: Path, support_name: str, alias_kind: str
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    support = paths["deterministic"].parent / support_name
    original = support.read_bytes()
    if alias_kind == "direct":
        out = support
    else:
        out = tmp_path / f"{alias_kind}-out.json"
        out.symlink_to(support) if alias_kind == "symlink" else os.link(support, out)
    completed = _run_cli(paths, manifest, out)
    assert completed.returncode == 1
    assert support.read_bytes() == original


def test_release_atomic_writer_rejects_parent_inode_swap(tmp_path: Path, monkeypatch) -> None:
    from scripts import google_live_deterministic_evidence as deterministic
    from scripts import google_live_release_gate as release_gate

    parent = tmp_path / "out"
    parent.mkdir()
    evidence_parent = tmp_path / "evidence"
    evidence_parent.mkdir()
    evidence = evidence_parent / "release.json"
    evidence.write_text("evidence", encoding="utf-8")
    real_match = deterministic._pinned_parent_path_matches

    def swap_then_match(path, ancestry):
        parent.rename(tmp_path / "moved-out")
        parent.symlink_to(evidence_parent, target_is_directory=True)
        return real_match(path, ancestry)

    monkeypatch.setattr(deterministic, "_pinned_parent_path_matches", swap_then_match)
    with pytest.raises(RuntimeError, match="parent changed"):
        release_gate._atomic_write(parent / "release.json", "candidate")
    assert evidence.read_text(encoding="utf-8") == "evidence"


def test_release_atomic_writer_rejects_directory_replacement_after_snapshot(
    tmp_path: Path,
) -> None:
    from scripts import google_live_deterministic_evidence as deterministic
    from scripts import google_live_release_gate as release_gate

    parent = tmp_path / "out"
    parent.mkdir()
    output = parent / "release.json"
    identity = deterministic.snapshot_output_parent(output)
    parent.rename(tmp_path / "original-out")
    parent.mkdir()
    with pytest.raises(RuntimeError, match="parent changed"):
        release_gate._atomic_write(output, "candidate", identity)
    assert not output.exists()
    assert not (tmp_path / "original-out" / "release.json").exists()


def test_release_writer_cannot_return_pass_after_late_hardlink_alias(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts import google_live_deterministic_evidence as deterministic
    from scripts import google_live_release_gate as release_gate

    output = tmp_path / "release.json"
    external_alias = tmp_path / "external-alias.json"
    real_match = deterministic._pinned_parent_path_matches

    def match(path, ancestry):
        result = real_match(path, ancestry)
        if output.exists() and not external_alias.exists():
            os.link(output, external_alias)
        return result

    monkeypatch.setattr(deterministic, "_pinned_parent_path_matches", match)
    with pytest.raises(RuntimeError, match="alias"):
        release_gate._atomic_write(output, '{"status":"PASS"}\n')
    assert not output.exists()
    assert json.loads(external_alias.read_text(encoding="utf-8"))["status"] == "PASS"
    assert external_alias.stat().st_nlink == 1
