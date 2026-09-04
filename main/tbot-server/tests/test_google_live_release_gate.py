import copy
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_physical_smoke_audit as physical_fixture

from scripts.analyze_google_live_log import correlate_websocket_bargein_evidence
from scripts import google_live_release_gate as release_gate
from scripts import google_live_robot_soak as robot_soak
from scripts.google_live_release_gate import (
    RELEASE_SCHEMA_VERSION,
    REQUIRED_LAYERS,
    aggregate_release_evidence,
    load_checksum_manifest,
)
from scripts.google_live_command_runner import (
    COMMAND_PROVENANCE_SCHEMA,
    CommandSpec,
    PairPointer,
    _canonical_spec,
    _digest,
    _render_pair_pointer,
    _spec_digest_summary,
    parse_provenance,
    render_commands_projection,
    render_provenance,
)
from scripts.google_live_deterministic_evidence import (
    MANIFEST_SCHEMA,
    PYTEST_RUNTIME_SCHEMA,
    parse_manifest,
    parse_pytest_runtime_manifest,
)

_PHYSICAL_CASE = physical_fixture.PhysicalSmokeAuditTest()
_OPTIONS = _PHYSICAL_CASE._candidate_audit_options()
IDENTITY = _OPTIONS["candidate_identity"]
CANONICAL_MANIFEST_PATH = (
    Path(__file__).parent / "fixtures" / "google_live_deterministic_nodes.txt"
)
CANONICAL_MANIFEST = CANONICAL_MANIFEST_PATH.read_bytes()
CANONICAL_NODES = parse_manifest(CANONICAL_MANIFEST)
PYTEST_RUNTIME_MANIFEST = (
    Path(__file__).parent / "fixtures" / "google_live_pytest_runtime_manifest.json"
).read_bytes()
PYTEST_RUNTIME = parse_pytest_runtime_manifest(PYTEST_RUNTIME_MANIFEST)
PYTHON_EXECUTABLE_MANIFEST = (
    json.dumps(
        {
            "profiles": [
                {
                    "machine": platform.machine().lower(),
                    "pythonImplementation": PYTEST_RUNTIME["pythonImplementation"],
                    "pythonMajorMinor": PYTEST_RUNTIME["pythonMajorMinor"],
                    "sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
                    "size": Path(sys.executable).resolve().stat().st_size,
                    "system": platform.system().lower(),
                }
            ],
            "schemaVersion": "google-live-python-executables.v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n"
).encode()
PYTHON_EXECUTABLE_TRUST = release_gate.parse_trusted_python_executable_manifest(
    PYTHON_EXECUTABLE_MANIFEST
)
RUNTIME_CLOSURE = {
    "distributions": [],
    "platform": "darwin-arm64-cp314",
    "resourceInventory": {
        "fileCount": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
        "totalBytes": 0,
    },
    "resources": [],
    "runtime": {
        "interpreterSha256": hashlib.sha256(
            Path(sys.executable).resolve().read_bytes()
        ).hexdigest(),
        "pythonImplementation": PYTEST_RUNTIME["pythonImplementation"],
        "pythonMajorMinor": PYTEST_RUNTIME["pythonMajorMinor"],
    },
    "schemaVersion": "google-live-runtime-closure.v1",
}
RUNTIME_CLOSURE_MANIFEST = (
    json.dumps(RUNTIME_CLOSURE, sort_keys=True, separators=(",", ":")) + "\n"
).encode()
RUNTIME_CLOSURE_SHA256 = hashlib.sha256(RUNTIME_CLOSURE_MANIFEST).hexdigest()
REAL_LOAD_TRUSTED_MANIFEST = release_gate._load_trusted_deterministic_manifest
REAL_LOAD_TRUSTED_PYTHON_EXECUTABLE_MANIFEST = (
    release_gate._load_trusted_python_executable_manifest
)


def _planned_command_argv(
    command_id: str, identity: dict = IDENTITY
) -> tuple[str | None, ...]:
    candidate = (
        "--candidate-git-sha", identity["gitSha"],
        "--candidate-image-digest", identity["imageDigest"],
        "--firmware-identity", identity["firmwareIdentity"],
        "--fixture-sha256", identity["fixtureSha256"],
    )
    soak_support = (
        "--baseline-report", "<evidence:baseline/report.json>",
        "--real-api-report", "<evidence:real-api/report.json>",
        "--transport-report", "<evidence:websocket-e2e/transport.json>",
        "--correlated-transport-report", "<evidence:websocket-e2e/report.json>",
        "--log-reliability-report", "<evidence:server-regression/report.json>",
        "--lesson-manifest", "<evidence:lesson-manifest.json>",
        "--config-json", None,
    )
    common = {
        "deterministic.produce": (sys.executable, "scripts/google_live_deterministic_evidence.py", "--manifest", "<evidence:deterministic/node-manifest.txt>", "--junit-out", "<evidence:deterministic/pytest.xml>", "--report", "<evidence:deterministic/report.json>", *candidate[:6], "--config-fingerprint", identity["configFingerprint"], *candidate[6:]),
        "real_api.round_trip": (sys.executable, "scripts/google_live_smoke.py", "--round-trip", "--audio-file", "<evidence:fixture.wav>", "--report", "<evidence:real-api/report.json>", *candidate[:6], "--config-fingerprint", identity["configFingerprint"], *candidate[6:]),
        "websocket.transport": (sys.executable, "scripts/voice_mode_websocket_audio_bargein.py", "--websocket-url", None, "--device-id", None, "--client-id", None, "--journey-id", None, *candidate[:6], "--config-json", None, *candidate[6:], "--report", "<evidence:websocket-e2e/transport.json>"),
        "websocket.log_analysis": (sys.executable, "scripts/analyze_google_live_log.py", "--log", None, "--reliability-window", "--journey-id", None, "--out-json", "<evidence:server-regression/report.json>"),
        "websocket.correlation": (sys.executable, "scripts/analyze_google_live_log.py", "--log", None, "--correlate-transport", "<evidence:websocket-e2e/transport.json>", "--expected-candidate-json", None, "--out-json", "<evidence:websocket-e2e/report.json>"),
        "candidate_soak.produce": (sys.executable, "scripts/google_live_robot_soak.py", "--mode", "candidate", "--produce-candidate-evidence", "<evidence:candidate-soak/journey-evidence.json>", "--evidence-control-url", None, "--server-log", None, "--run-id", None, *soak_support, *candidate),
        "candidate_soak.replay": (sys.executable, "scripts/google_live_robot_soak.py", "--mode", "candidate", "--journey-evidence", "<evidence:candidate-soak/journey-evidence.json>", "--report", "<evidence:candidate-soak/report.json>", *soak_support, *candidate),
        "physical.capture_and_audit": (sys.executable, "scripts/google_live_physical_evidence.py", "--candidate-soak-report", "<evidence:candidate-soak/report.json>", "--server-report", "<evidence:server-regression/report.json>", "--report", "<evidence:physical/report.json>", *candidate),
    }
    return common[command_id]


@pytest.fixture(autouse=True)
def _pin_release_manifest_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release_gate,
        "_load_trusted_deterministic_manifest",
        lambda _expected_git_sha: CANONICAL_MANIFEST,
        raising=False,
    )
    monkeypatch.setattr(
        release_gate,
        "_load_trusted_pytest_runtime_manifest",
        lambda _expected_git_sha: PYTEST_RUNTIME_MANIFEST,
        raising=False,
    )
    monkeypatch.setattr(
        release_gate,
        "_load_trusted_python_executable_manifest",
        lambda _expected_git_sha: PYTHON_EXECUTABLE_MANIFEST,
        raising=False,
    )
    monkeypatch.setattr(
        release_gate,
        "_load_runtime_closure_manifest",
        lambda _expected_git_sha: copy.deepcopy(RUNTIME_CLOSURE),
        raising=False,
    )


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


def test_release_rejects_invalid_dependency_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, checksum_manifest = _write_evidence(tmp_path)
    monkeypatch.setattr(
        release_gate,
        "_load_runtime_closure_manifest",
        lambda _sha: (_ for _ in ()).throw(ValueError("dependency drift")),
    )
    verdict = release_gate.produce_release_verdict(
        IDENTITY, paths, checksum_manifest, tmp_path / "release.json"
    )
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "DETERMINISTIC_TRUSTED_MANIFEST_INVALID" for item in verdict["failures"])


def test_command_spec_digest_binds_runtime_closure_identity() -> None:
    entry = {"argv": [sys.executable, "scripts/google_live_smoke.py"], "commandId": "real_api.round_trip", "cwd": ".", "environmentSources": [], "terminalPolicy": {"expectedExitCodes": [0], "timeoutSec": 1.0, "cleanupGraceSec": 0.1}, "inputs": [], "outputs": [], "secretSources": [], "stdinSource": None}
    first = release_gate._recorded_command_spec_digest(entry, "a" * 64)
    second = release_gate._recorded_command_spec_digest(entry, "b" * 64)
    assert first != second


def test_release_requires_complete_runtime_closure_support(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "PASS"
    assert verdict["failures"] == []


@pytest.mark.parametrize("artifact", ["resource", "dependency", "manifest", "source"])
def test_release_revalidates_runtime_closure_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    calls = 0

    def drifted_loader(_sha: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return copy.deepcopy(RUNTIME_CLOSURE)
        raise ValueError(f"{artifact} drift")

    monkeypatch.setattr(release_gate, "_load_runtime_closure_manifest", drifted_loader)
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    encoded = json.dumps(verdict)

    assert verdict["status"] == "FAIL"
    assert calls >= 2
    assert artifact not in encoded
    assert "drift" not in encoded


def test_release_rejects_tampered_runtime_closure_support_without_leaking_content(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    secret = "closure-private-content"
    paths["runtime_closure_manifest"].write_bytes(secret.encode())
    checksums["runtime_closure_manifest"] = hashlib.sha256(
        secret.encode()
    ).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert "closure-private-content" not in json.dumps(verdict)
    assert any(item["code"] == "RUNTIME_CLOSURE_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_release_rejects_runtime_closure_support_alias(
    tmp_path: Path, alias_kind: str
) -> None:
    paths, checksums, checksum_manifest = _write_evidence(tmp_path)
    target = paths["runtime_closure_manifest"]
    alias = tmp_path / f"closure-{alias_kind}.json"
    if alias_kind == "symlink":
        alias.symlink_to(target)
    else:
        os.link(target, alias)
    paths["runtime_closure_manifest"] = alias
    with pytest.raises(ValueError, match="alias"):
        release_gate.produce_release_verdict(
            IDENTITY, paths, checksum_manifest, tmp_path / "release.json"
        )


def test_release_rejects_unsupported_runtime_closure_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    unsupported = copy.deepcopy(RUNTIME_CLOSURE)
    unsupported["platform"] = "linux-x86_64-cp314"
    monkeypatch.setattr(
        release_gate, "_load_runtime_closure_manifest", lambda _sha: unsupported
    )

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "DETERMINISTIC_TRUSTED_MANIFEST_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize("mutation", ["size", "content", "identity", "symlink", "hardlink"])
def test_runtime_closure_distribution_file_is_revalidated(
    tmp_path: Path, mutation: str
) -> None:
    package_root = tmp_path / "site-packages"
    package_root.mkdir()
    package_file = package_root / "example.py"
    package_file.write_bytes(b"trusted")
    manifest = {
        "distributions": [{
            "fileCount": 1, "files": [{"path": "example.py", "sha256": hashlib.sha256(b"trusted").hexdigest(), "size": 7}],
            "importRoots": ["example"], "name": "example", "root": str(package_root), "totalBytes": 7, "version": "1",
        }],
    }
    snapshot = release_gate._validate_runtime_closure_distributions(manifest)
    if mutation == "size":
        package_file.write_bytes(b"too-long")
    elif mutation == "content":
        package_file.write_bytes(b"changed")
    elif mutation == "identity":
        replacement = package_root / "replacement.py"
        replacement.write_bytes(b"trusted")
        os.replace(replacement, package_file)
    elif mutation == "symlink":
        moved = package_root / "moved.py"
        package_file.rename(moved)
        package_file.symlink_to(moved)
    else:
        os.link(package_file, package_root / "alias.py")
    with pytest.raises(ValueError, match="distribution"):
        release_gate._validate_runtime_closure_distributions(manifest, snapshot)
def _reports(test_count: int = len(CANONICAL_NODES)) -> dict[str, dict]:
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
                "total": test_count,
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


def _write_evidence(
    root: Path,
    *,
    nodes: list[str] | None = None,
) -> tuple[dict[str, Path], dict[str, str], Path]:
    deterministic_dir = root / "deterministic"
    deterministic_dir.mkdir(parents=True)
    nodes = list(CANONICAL_NODES if nodes is None else nodes)
    node_manifest = deterministic_dir / "node-manifest.txt"
    node_manifest.write_text("\n".join(nodes) + "\n", encoding="utf-8")
    junit = deterministic_dir / "pytest.xml"
    cases = []
    for node in nodes:
        parts = node.split("::")
        classname = parts[0][:-3].replace("/", ".")
        if len(parts) > 2:
            classname += "." + ".".join(parts[1:-1])
        cases.append(
            f'<testcase classname="{classname}" name="{parts[-1]}" time="0.000">'
            f'<properties><property name="google_live_nodeid" value="{node}" />'
            f'</properties></testcase>'
        )
    junit.write_text(
        f'<testsuites name="pytest tests"><testsuite name="pytest" tests="{len(nodes)}" failures="0" errors="0" skipped="0">{"".join(cases)}</testsuite></testsuites>',
        encoding="utf-8",
    )
    reports = _reports(len(nodes))
    reports["deterministic"]["coverageProof"] = {
        "manifestSchema": MANIFEST_SCHEMA,
        "manifestSha256": hashlib.sha256(node_manifest.read_bytes()).hexdigest(),
        "manifestNodeCount": len(nodes),
        "executedNodeCount": len(nodes),
        "junitSha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
        "nodeidPluginSha256": PYTEST_RUNTIME["plugin"]["sha256"],
        "pytestRuntimeManifestSha256": hashlib.sha256(PYTEST_RUNTIME_MANIFEST).hexdigest(),
        "pytestRuntimeSchema": PYTEST_RUNTIME_SCHEMA,
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
    runtime_closure = root / "runtime-closure.json"
    runtime_closure.write_bytes(RUNTIME_CLOSURE_MANIFEST)
    paths["runtime_closure_manifest"] = runtime_closure
    checksums["runtime_closure_manifest"] = RUNTIME_CLOSURE_SHA256
    rows.append(f"{RUNTIME_CLOSURE_SHA256}  {runtime_closure.relative_to(root)}")
    journey_evidence = root / "candidate-soak" / "journey-evidence.json"
    journey_evidence.write_text('{"closed":true}\n', encoding="utf-8")
    journey_evidence_artifact = {
        "label": str(journey_evidence.relative_to(root)),
        "sha256": hashlib.sha256(journey_evidence.read_bytes()).hexdigest(),
    }
    fixture = root / "fixture.wav"
    fixture.write_bytes(b"fixture")
    fixture_artifact = {
        "label": "fixture.wav",
        "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
    }
    transport = root / "websocket-e2e" / "transport.json"
    transport.write_text('{"status":"PENDING"}\n', encoding="utf-8")
    transport_artifact = {
        "label": "websocket-e2e/transport.json",
        "sha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
    }
    baseline = root / "baseline" / "report.json"
    baseline.parent.mkdir()
    baseline.write_text('{}\n', encoding="utf-8")
    baseline_artifact = {"label": "baseline/report.json", "sha256": hashlib.sha256(baseline.read_bytes()).hexdigest()}
    lesson = root / "lesson-manifest.json"
    lesson.write_text('{}\n', encoding="utf-8")
    lesson_artifact = {"label": "lesson-manifest.json", "sha256": hashlib.sha256(lesson.read_bytes()).hexdigest()}
    soak_inputs = [
        copy.deepcopy(baseline_artifact),
        {"label": "real-api/report.json", "sha256": checksums["real_api"]},
        copy.deepcopy(transport_artifact),
        {"label": "websocket-e2e/report.json", "sha256": checksums["websocket_e2e"]},
        {"label": "server-regression/report.json", "sha256": checksums["server_regression"]},
        copy.deepcopy(lesson_artifact),
    ]
    command_outputs = {
        "deterministic.produce": ("deterministic", "deterministic_manifest", "deterministic_junit"),
        "real_api.round_trip": ("real_api",),
        "websocket.transport": (),
        "websocket.log_analysis": ("server_regression",),
        "websocket.correlation": ("websocket_e2e",),
        "candidate_soak.produce": (),
        "candidate_soak.replay": ("candidate_soak",),
        "physical.capture_and_audit": ("server_regression", "physical"),
    }
    commands = []
    argv_patterns = {
        command_id: _planned_command_argv(command_id)
        for command_id in command_outputs
    }
    for index, (command_id, output_names) in enumerate(command_outputs.items()):
        secret_sources = ["<env:GOOGLE_API_KEY>"] if command_id == "real_api.round_trip" else (
            ["<env:TBOT_DEVICE_MINT_SECRET>"]
            if command_id in {"websocket.transport", "candidate_soak.produce", "physical.capture_and_audit"}
            else []
        )
        inputs = {
            "real_api.round_trip": [copy.deepcopy(fixture_artifact)],
            "websocket.correlation": [
                copy.deepcopy(transport_artifact),
                {"label": "server-regression/report.json", "sha256": checksums["server_regression"]},
            ],
            "candidate_soak.produce": copy.deepcopy(soak_inputs),
            "candidate_soak.replay": [copy.deepcopy(journey_evidence_artifact), *copy.deepcopy(soak_inputs)],
            "physical.capture_and_audit": [
                {"label": "candidate-soak/report.json", "sha256": checksums["candidate_soak"]}
            ],
        }.get(command_id, [])
        outputs = [
            {"label": str(paths[name].relative_to(root)), "sha256": checksums[name]}
            for name in output_names
        ]
        if command_id == "websocket.transport":
            outputs.append(copy.deepcopy(transport_artifact))
        if command_id == "candidate_soak.produce":
            outputs.append(copy.deepcopy(journey_evidence_artifact))
        command = {
                "argv": ["runtime-value" if value is None else value for value in argv_patterns[command_id]],
                "candidateIdentity": copy.deepcopy(IDENTITY),
                "commandId": command_id,
                "cwd": ".",
                "endedAtUtc": f"2026-09-03T00:00:{index:02d}.500000Z",
                "environmentSources": [],
                "exitCode": 0,
                "inputs": inputs,
                "outputs": outputs,
                "schemaVersion": COMMAND_PROVENANCE_SCHEMA,
                "secretSources": secret_sources,
                "specSha256": "0" * 64,
                "startedAtUtc": f"2026-09-03T00:00:{index:02d}.000000Z",
                "stdinSource": (
                    "<stdin:protected_transcript_plan>"
                    if command_id == "physical.capture_and_audit"
                    else (
                        "<stdin:protected_candidate_plan>"
                        if command_id == "candidate_soak.produce"
                        else None
                    )
                ),
                "terminalPolicy": {
                    "classification": "expected_exit",
                    "cleanupGraceSec": 2.0,
                    "expectedExitCodes": [0],
                    "satisfied": True,
                    "timeoutSec": 300.0,
                },
            }
        command["specSha256"] = release_gate._recorded_command_spec_digest(
            command, RUNTIME_CLOSURE_SHA256
        )
        commands.append(command)
    provenance = root / "commands.jsonl"
    provenance.write_bytes(render_provenance(commands))
    digest = hashlib.sha256(provenance.read_bytes()).hexdigest()
    paths["command_provenance"] = provenance
    checksums["command_provenance"] = digest
    rows.append(f"{digest}  {provenance.relative_to(root)}")
    manifest = root / "checksums.sha256"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    _install_committed_provenance_pair(provenance)
    return paths, checksums, manifest


def _rewrite(path: Path, mutate) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    mutate(report)
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


def _rebind_provenance_output(
    paths: dict[str, Path], checksums: dict[str, str], artifact: str
) -> None:
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    label = str(paths[artifact].relative_to(paths["command_provenance"].parent))
    for entry in entries:
        for output in entry["outputs"]:
            if output["label"] == label:
                output["sha256"] = checksums[artifact]
    paths["command_provenance"].write_bytes(render_provenance(entries))
    checksums["command_provenance"] = hashlib.sha256(
        paths["command_provenance"].read_bytes()
    ).hexdigest()


def test_release_passes_only_real_exact_candidate_contracts(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["schemaVersion"] == RELEASE_SCHEMA_VERSION
    assert verdict["status"] == "PASS"
    assert verdict["candidateIdentity"] == IDENTITY
    assert [item["name"] for item in verdict["layers"]] == list(REQUIRED_LAYERS)
    assert verdict["failures"] == []


@pytest.mark.parametrize("command_id", release_gate.REQUIRED_COMMAND_IDS)
def test_release_accepts_planned_immutable_command_specs(
    tmp_path: Path, command_id: str
) -> None:
    identity = {
        "gitSha": "a" * 40,
        "imageDigest": "sha256:" + "b" * 64,
        "firmwareIdentity": "firmware-v1",
        "configFingerprint": "sha256:" + "c" * 64,
        "fixtureSha256": "d" * 64,
    }
    trusted = release_gate._trusted_command_specs(identity)[command_id]
    inputs = tuple(tmp_path / label for label in trusted.input_labels)
    outputs = tuple(tmp_path / label for label in trusted.output_labels)
    planned = _planned_command_argv(command_id, identity)
    argv = tuple(
        str(tmp_path / value.removeprefix("<evidence:").removesuffix(">"))
        if isinstance(value, str) and value.startswith("<evidence:")
        else ("runtime-value" if value is None else value)
        for value in planned
    )
    secret_env = (
        ("GOOGLE_API_KEY",)
        if command_id == "real_api.round_trip"
        else (
            ("TBOT_DEVICE_MINT_SECRET",)
            if command_id in {"websocket.transport", "candidate_soak.produce", "physical.capture_and_audit"}
            else ()
        )
    )
    spec = CommandSpec(
        command_id=command_id,
        argv=argv,
        cwd=tmp_path,
        candidate_identity=identity,
        secret_env=secret_env,
        inputs=inputs,
        outputs=outputs,
        expected_exit_codes=(0,),
        stdin_source=(
            "protected_transcript_plan"
            if command_id == "physical.capture_and_audit"
            else (
                "protected_candidate_plan"
                if command_id == "candidate_soak.produce"
                else None
            )
        ),
        timeout_sec=300.0,
        cleanup_grace_sec=2.0,
    )
    canonical = _canonical_spec(spec, tmp_path)
    emitted = {
        "argv": canonical["argv"],
        "commandId": command_id,
        "cwd": canonical["cwd"],
        "environmentSources": canonical["environmentSources"],
        "inputs": [{"label": label, "sha256": "e" * 64} for label in trusted.input_labels],
        "outputs": [{"label": label, "sha256": "f" * 64} for label in trusted.output_labels],
        "secretSources": canonical["secretSources"],
        "specSha256": _digest(canonical),
        "stdinSource": canonical["stdinSource"],
        "terminalPolicy": {
            "cleanupGraceSec": 2.0,
            "expectedExitCodes": [0],
            "timeoutSec": 300.0,
        },
    }

    assert release_gate._command_matches_trusted_spec(
        emitted,
        trusted,
        PYTEST_RUNTIME,
        executable_trust=PYTHON_EXECUTABLE_TRUST,
    )


@pytest.mark.parametrize("command_id", ["candidate_soak.produce", "candidate_soak.replay"])
def test_candidate_soak_contract_satisfies_actual_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command_id: str,
) -> None:
    values = []
    for value in _planned_command_argv(command_id)[2:]:
        if value is None:
            values.append("{}" if values[-1] == "--config-json" else "runtime-value")
        elif value.startswith("<evidence:"):
            values.append(str(tmp_path / value.removeprefix("<evidence:").removesuffix(">")))
        else:
            values.append(value)
    if command_id == "candidate_soak.produce":
        monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "test-secret")
    parser = robot_soak._build_argument_parser()
    args = parser.parse_args(values)

    robot_soak._validate_candidate_args(parser, args)


@pytest.mark.parametrize("mutation", ["missing", "reordered", "diagnostic_substitution"])
def test_release_requires_exact_ordered_command_provenance(tmp_path: Path, mutation: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    if mutation == "missing":
        entries.pop(2)
    elif mutation == "reordered":
        entries[1], entries[2] = entries[2], entries[1]
    else:
        entries[2]["commandId"] = "diagnostic.websocket_transport"
        entries[2]["specSha256"] = "f" * 64
    paths["command_provenance"].write_bytes(render_provenance(entries))
    checksums["command_provenance"] = hashlib.sha256(paths["command_provenance"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


def test_release_rejects_untrusted_command_spec_with_fresh_digest(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    entry = next(item for item in entries if item["commandId"] == "real_api.round_trip")
    entry["argv"] = ["/usr/bin/false", "--arbitrary-command"]
    entry["specSha256"] = hashlib.sha256(b"attacker-controlled-command-spec").hexdigest()
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


def test_release_rejects_legacy_pre_source_binding_spec_digest(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    entry = next(item for item in entries if item["commandId"] == "real_api.round_trip")
    legacy = {
        "argv": entry["argv"],
        "commandId": entry["commandId"],
        "cwd": entry["cwd"],
        "environmentSources": entry["environmentSources"],
        "expectedExitCodes": entry["terminalPolicy"]["expectedExitCodes"],
        "inputs": [item["label"] for item in entry["inputs"]],
        "outputs": [item["label"] for item in entry["outputs"]],
        "secretSources": entry["secretSources"],
        "stdinSource": entry["stdinSource"],
        "timeoutSec": entry["terminalPolicy"]["timeoutSec"],
        "cleanupGraceSec": entry["terminalPolicy"]["cleanupGraceSec"],
    }
    entry["specSha256"] = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize(
    ("command_id", "old", "new"),
    [
        ("deterministic.produce", "scripts/google_live_deterministic_evidence.py", "scripts/google_live_smoke.py"),
        ("candidate_soak.produce", "--produce-candidate-evidence", "--journey-evidence"),
        ("candidate_soak.replay", "--journey-evidence", "--produce-candidate-evidence"),
        ("physical.capture_and_audit", "scripts/google_live_physical_evidence.py", "scripts/physical_smoke_audit.py"),
    ],
)
def test_release_rejects_deviation_from_planned_command_chain(
    tmp_path: Path, command_id: str, old: str, new: str
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    entry = next(item for item in entries if item["commandId"] == command_id)
    entry["argv"][entry["argv"].index(old)] = new
    entry["specSha256"] = release_gate._recorded_command_spec_digest(entry)
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


def test_release_accepts_approved_interpreter_symlink(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    interpreter = tmp_path / f"python{PYTEST_RUNTIME['pythonMajorMinor']}"
    interpreter.symlink_to(sys.executable)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    for entry in entries:
        entry["argv"][0] = str(interpreter)
        entry["specSha256"] = release_gate._recorded_command_spec_digest(
            entry, RUNTIME_CLOSURE_SHA256
        )
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    assert aggregate_release_evidence(IDENTITY, paths, checksums)["status"] == "PASS"


def test_release_accepts_different_approved_runtime_from_verifier_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_venv = tmp_path / "foreign-venv"
    foreign_venv.mkdir()
    (foreign_venv / "pyvenv.cfg").write_text("home = /untrusted\n", encoding="utf-8")
    bin_dir = foreign_venv / "bin"
    bin_dir.mkdir()
    interpreter = bin_dir / f"python{PYTEST_RUNTIME['pythonMajorMinor']}"
    interpreter.symlink_to(sys.executable)

    recorded_interpreter = str(interpreter)
    monkeypatch.setattr(release_gate.sys, "executable", "/different/verifier/python")

    assert release_gate._approved_python_executable(
        recorded_interpreter, PYTEST_RUNTIME, PYTHON_EXECUTABLE_TRUST
    )


def test_release_rejects_interpreter_when_runtime_version_does_not_match() -> None:
    mismatched_runtime = copy.deepcopy(PYTEST_RUNTIME)
    mismatched_runtime["pythonMajorMinor"] = "0.0"

    assert not release_gate._approved_python_executable(
        sys.executable, mismatched_runtime, PYTHON_EXECUTABLE_TRUST
    )


def test_release_rejects_unapproved_interpreter_substitution(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    entries[0]["argv"][0] = "/bin/sh"
    entries[0]["specSha256"] = release_gate._recorded_command_spec_digest(entries[0])
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    assert aggregate_release_evidence(IDENTITY, paths, checksums)["status"] == "FAIL"


def test_release_rejects_executable_that_spoofs_runtime_probe(tmp_path: Path) -> None:
    interpreter = tmp_path / f"python{PYTEST_RUNTIME['pythonMajorMinor']}"
    expected = json.dumps(
        {
            "implementation": PYTEST_RUNTIME["pythonImplementation"],
            "majorMinor": PYTEST_RUNTIME["pythonMajorMinor"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    interpreter.write_text(f"#!/bin/sh\nprintf '%s\\n' '{expected}'\n", encoding="utf-8")
    interpreter.chmod(0o700)

    assert not release_gate._approved_python_executable(
        str(interpreter), PYTEST_RUNTIME, PYTHON_EXECUTABLE_TRUST
    )


def test_release_binds_digest_check_to_opened_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = tmp_path / f"python{PYTEST_RUNTIME['pythonMajorMinor']}"
    interpreter.symlink_to(sys.executable)
    spoof = tmp_path / "spoof"
    spoof.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    spoof.chmod(0o700)
    real_read = release_gate.os.read
    swapped = False

    def swap_then_read(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            interpreter.unlink()
            interpreter.symlink_to(spoof)
            swapped = True
        return real_read(*args, **kwargs)

    monkeypatch.setattr(release_gate.os, "read", swap_then_read)

    assert release_gate._approved_python_executable(
        str(interpreter), PYTEST_RUNTIME, PYTHON_EXECUTABLE_TRUST
    )


def test_release_checks_executable_mode_on_opened_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = tmp_path / f"python{PYTEST_RUNTIME['pythonMajorMinor']}"
    interpreter.write_bytes(Path(sys.executable).resolve().read_bytes())
    interpreter.chmod(0o600)
    monkeypatch.setattr(release_gate.os, "access", lambda *_args: True)

    assert not release_gate._approved_python_executable(
        str(interpreter), PYTEST_RUNTIME, PYTHON_EXECUTABLE_TRUST
    )


def test_release_rejects_wrong_platform_interpreter_profile() -> None:
    trust = copy.deepcopy(PYTHON_EXECUTABLE_TRUST)
    trust["profiles"][0]["system"] = "unsupported-system"

    assert not release_gate._approved_python_executable(
        sys.executable, PYTEST_RUNTIME, trust
    )


@pytest.mark.parametrize("mutation", ["missing", "tampered", "candidate_mismatch"])
def test_trusted_python_executable_manifest_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    expected_sha = IDENTITY["gitSha"]
    content = PYTHON_EXECUTABLE_MANIFEST
    fixture_path = (
        tmp_path
        / "main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
    )
    fixture_path.parent.mkdir(parents=True)

    def git_output(_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return f"{tmp_path}\n".encode()
        if arguments[:2] == ("rev-parse", "HEAD"):
            return ("f" * 40 if mutation == "candidate_mismatch" else expected_sha).encode() + b"\n"
        if arguments[0] == "status":
            return b""
        if arguments[0] == "show":
            if mutation == "missing":
                raise RuntimeError("missing")
            if mutation == "tampered":
                return content.replace(b'"schemaVersion"', b'"badSchema"')
            return content
        raise AssertionError(arguments)

    monkeypatch.setattr(release_gate, "_git_output", git_output)
    monkeypatch.setattr(
        release_gate,
        "_trusted_manifest_path",
        lambda: fixture_path,
    )

    with pytest.raises((RuntimeError, ValueError)):
        REAL_LOAD_TRUSTED_PYTHON_EXECUTABLE_MANIFEST(expected_sha)


def test_release_rejects_fresh_digest_for_trusted_command_spec(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    entry = next(item for item in entries if item["commandId"] == "real_api.round_trip")
    entry["specSha256"] = hashlib.sha256(b"different-command-spec").hexdigest()
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize("mutation", ["identity", "output", "exit", "digest", "privacy"])
def test_release_rejects_command_provenance_mismatch_or_tamper(tmp_path: Path, mutation: str) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    if mutation == "identity":
        entries[0]["candidateIdentity"]["gitSha"] = "f" * 40
    elif mutation == "output":
        entries[1]["outputs"][0]["sha256"] = "f" * 64
    elif mutation == "exit":
        entries[1]["terminalPolicy"]["satisfied"] = False
    elif mutation == "digest":
        entries[1]["specSha256"] = entries[0]["specSha256"]
    else:
        entries[1]["metadata"] = {"apiKey": "must-never-leak"}
    if mutation == "privacy":
        content = ("\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in entries) + "\n").encode()
    else:
        content = ("\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in entries) + "\n").encode()
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    encoded = json.dumps(verdict)
    assert verdict["status"] == "FAIL"
    assert "must-never-leak" not in encoded
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize(
    "mutation",
    [
        "producer_final",
        "replay_missing_output",
        "replay_self_input",
        "intermediate_digest",
        "intermediate_file",
    ],
)
def test_release_binds_candidate_soak_producer_intermediate_and_replay(
    tmp_path: Path, mutation: str
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    by_id = {entry["commandId"]: entry for entry in entries}
    producer = by_id["candidate_soak.produce"]
    replay = by_id["candidate_soak.replay"]
    final = next(item for item in replay["outputs"] if item["label"].endswith("report.json"))
    if mutation == "producer_final":
        producer["outputs"] = [copy.deepcopy(final)]
    elif mutation == "replay_missing_output":
        replay["outputs"] = []
    elif mutation == "replay_self_input":
        replay["inputs"] = [copy.deepcopy(final)]
    elif mutation == "intermediate_digest":
        replay["inputs"][0]["sha256"] = "f" * 64
    else:
        (paths["command_provenance"].parent / producer["outputs"][0]["label"]).write_text(
            '{"closed":false}\n', encoding="utf-8"
        )
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


@pytest.mark.parametrize(
    ("command_id", "field", "value"),
    [
        ("websocket.transport", "secretSources", []),
        ("websocket.transport", "secretSources", ["<env:TBOT_DEVICE_MINT_SECRET>", "<env:EXTRA_SECRET>"]),
        ("candidate_soak.replay", "secretSources", ["<env:TBOT_DEVICE_MINT_SECRET>"]),
        ("real_api.round_trip", "stdinSource", "<stdin:protected_transcript_plan>"),
        ("physical.capture_and_audit", "stdinSource", None),
    ],
)
def test_release_requires_exact_secret_and_stdin_assignment(
    tmp_path: Path, command_id: str, field: str, value
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    entries = [json.loads(line) for line in paths["command_provenance"].read_text().splitlines()]
    next(entry for entry in entries if entry["commandId"] == command_id)[field] = value
    content = render_provenance(entries)
    paths["command_provenance"].write_bytes(content)
    checksums["command_provenance"] = hashlib.sha256(content).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert any(item["code"] == "COMMAND_PROVENANCE_INVALID" for item in verdict["failures"])


def test_release_rejects_self_consistent_tiny_deterministic_manifest(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(
        tmp_path,
        nodes=["tests/test_a.py::test_one", "tests/test_b.py::test_two"],
    )

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(
        item["code"] == "DETERMINISTIC_SUPPORT_CONTRACT_INVALID"
        for item in verdict["failures"]
    )


@pytest.mark.parametrize("mutation", ["reordered", "missing", "duplicate", "unapproved"])
def test_release_rejects_self_consistent_noncanonical_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    nodes = list(CANONICAL_NODES)
    if mutation == "reordered":
        nodes[0], nodes[1] = nodes[1], nodes[0]
    elif mutation == "missing":
        nodes.pop()
    elif mutation == "duplicate":
        nodes.append(nodes[-1])
    else:
        nodes[0] = "tests/unapproved_google_live.py::test_hidden"
    paths, checksums, _ = _write_evidence(tmp_path, nodes=nodes)

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "FAIL"
    assert any(
        item["code"] == "DETERMINISTIC_SUPPORT_CONTRACT_INVALID"
        for item in verdict["failures"]
    )


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_release_rejects_canonical_fixture_alias_as_supplied_manifest(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    supplied = paths["deterministic_manifest"]
    supplied.unlink()
    if alias_kind == "direct":
        paths["deterministic_manifest"] = CANONICAL_MANIFEST_PATH
    elif alias_kind == "symlink":
        supplied.symlink_to(CANONICAL_MANIFEST_PATH)
    else:
        os.link(CANONICAL_MANIFEST_PATH, supplied)

    try:
        verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    finally:
        if alias_kind == "hardlink" and supplied.exists():
            supplied.unlink()

    assert verdict["status"] == "FAIL"
    assert any(item["code"].startswith("DETERMINISTIC_SUPPORT") for item in verdict["failures"])


@pytest.mark.parametrize("drift", ["head", "status", "untracked", "content", "missing_blob"])
def test_trusted_manifest_loader_rejects_repository_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    fixture = tmp_path / "main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(CANONICAL_MANIFEST)
    expected_sha = "a" * 40

    def git_output(_repo_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path).encode() + b"\n"
        if arguments == ("rev-parse", "HEAD"):
            return (("b" * 40) if drift == "head" else expected_sha).encode() + b"\n"
        if arguments[0] == "status":
            return b" M main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt\0" if drift == "status" else b""
        if arguments[0] == "ls-files":
            if drift == "untracked":
                raise RuntimeError("not tracked")
            return str(fixture.relative_to(tmp_path)).encode() + b"\n"
        if arguments[0] == "show":
            assert arguments[1].startswith(f"{expected_sha}:")
            if drift == "missing_blob":
                raise RuntimeError("missing candidate object")
            return b"tests/test_a.py::test_one\n" if drift == "content" else CANONICAL_MANIFEST
        raise AssertionError(arguments)

    monkeypatch.setattr(release_gate, "_trusted_manifest_path", lambda: fixture)
    monkeypatch.setattr(release_gate, "_git_output", git_output)

    with pytest.raises((RuntimeError, ValueError)):
        REAL_LOAD_TRUSTED_MANIFEST(expected_sha)


def test_trusted_manifest_loader_uses_immutable_sha_across_head_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(CANONICAL_MANIFEST)
    candidate_sha = "a" * 40
    other_sha = "b" * 40
    show_objects = []

    def git_output(_repo_root: Path, *arguments: str) -> bytes:
        if arguments[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path).encode() + b"\n"
        if arguments == ("rev-parse", "HEAD"):
            return candidate_sha.encode() + b"\n"
        if arguments[0] == "status":
            return b""
        if arguments[0] == "ls-files":
            return str(fixture.relative_to(tmp_path)).encode() + b"\n"
        if arguments[0] == "show":
            show_objects.append(arguments[1])
            if arguments[1].startswith(f"{other_sha}:") or arguments[1].startswith("HEAD:"):
                return b"tests/test_a.py::test_one\n"
            return CANONICAL_MANIFEST
        raise AssertionError(arguments)

    monkeypatch.setattr(release_gate, "_trusted_manifest_path", lambda: fixture)
    monkeypatch.setattr(release_gate, "_git_output", git_output)

    assert REAL_LOAD_TRUSTED_MANIFEST(candidate_sha) == CANONICAL_MANIFEST
    assert show_objects == [
        f"{candidate_sha}:main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
    ]


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
            f'<testsuite name="pytest" tests="{len(CANONICAL_NODES)}" failures="0" errors="0" skipped="0">',
            f'<testsuite name="pytest" tests="{len(CANONICAL_NODES)}" failures="0" errors="1" skipped="0"><error message="session crashed" />',
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
        junit.read_bytes().replace(
            b'<testsuites name="pytest tests">',
            b'<testsuites name="pytest tests">GOOGLE_API_KEY=secret',
            1,
        )
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


def test_release_rejects_rebound_fullwidth_sensitive_allowed_attribute(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    secret = "private"
    sensitive = f"ＧＯＯＧＬＥ＿ＡＰＩ＿ＫＥＹ＝{secret}"
    junit.write_bytes(
        junit.read_bytes().replace(
            f'classname="{CANONICAL_NODES[0].split("::", 1)[0][:-3].replace("/", ".")}"'.encode(),
            f'classname="{sensitive}"'.encode(),
            1,
        )
    )
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert secret not in json.dumps(verdict)


def test_release_rejects_rebound_junit_missing_testcase_time(tmp_path: Path) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    junit.write_bytes(junit.read_bytes().replace(b' time="0.000"', b"", 1))
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    assert aggregate_release_evidence(IDENTITY, paths, checksums)["status"] == "FAIL"


def test_release_rejects_rebound_embedded_credential_metadata_without_leaking(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    secret = "private"
    junit.write_bytes(
        junit.read_bytes().replace(
            b'name="pytest tests"',
            f'name="safe client_secret={secret}"'.encode(),
            1,
        )
    )
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert secret not in json.dumps(verdict)


def test_release_rejects_rebound_double_encoded_credential_without_leaking(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    encoded_secret = "access%255Ftoken%253Dprivate"
    junit.write_bytes(
        junit.read_bytes().replace(
            b'name="pytest tests"',
            f'name="{encoded_secret}"'.encode(),
            1,
        )
    )
    digest = hashlib.sha256(junit.read_bytes()).hexdigest()
    checksums["deterministic_junit"] = digest
    _rewrite(paths["deterministic"], lambda report: report["coverageProof"].update(junitSha256=digest))
    checksums["deterministic"] = hashlib.sha256(paths["deterministic"].read_bytes()).hexdigest()
    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)
    assert verdict["status"] == "FAIL"
    assert "private" not in json.dumps(verdict)


def test_release_rejects_rebound_compact_bearer_credential_without_leaking(
    tmp_path: Path,
) -> None:
    paths, checksums, _ = _write_evidence(tmp_path)
    junit = paths["deterministic_junit"]
    secret = "abcdefghijklmnopqrst"
    junit.write_bytes(
        junit.read_bytes().replace(
            b'name="pytest tests"',
            f'name="Bearer {secret}"'.encode(),
            1,
        )
    )
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
    _rebind_provenance_output(paths, checksums, "deterministic")

    verdict = aggregate_release_evidence(IDENTITY, paths, checksums)

    assert verdict["status"] == "PASS"


def test_checksum_manifest_maps_every_exact_declared_artifact(tmp_path: Path) -> None:
    paths, checksums, manifest = _write_evidence(tmp_path)
    assert load_checksum_manifest(manifest, paths) == checksums


def test_checksum_manifest_rejects_unknown_artifacts(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + f"{'0' * 64}  timeline.log\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_checksum_manifest(manifest, paths)


@pytest.mark.parametrize(
    "hidden_path",
    [
        "GOOGLE_API_KEY=super-secret",
        "ＧＯＯＧＬＥ＿ＡＰＩ＿ＫＥＹ＝super-secret",
        "access%255Ftoken%253Dsuper-secret",
        "Bearer abcdefghijklmnopqrst",
        "cookie=session-private",
    ],
)
def test_checksum_manifest_rejects_private_unknown_rows_without_leaking(
    tmp_path: Path,
    hidden_path: str,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + f"{'0' * 64}  {hidden_path}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as error:
        load_checksum_manifest(manifest, paths)
    assert "super-secret" not in str(error.value)
    assert "abcdefghijklmnopqrst" not in str(error.value)


@pytest.mark.parametrize("suffix", ["\n", "# hidden\n"])
def test_checksum_manifest_rejects_blank_or_comment_rows(
    tmp_path: Path,
    suffix: str,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text(manifest.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    with pytest.raises(ValueError):
        load_checksum_manifest(manifest, paths)


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
    command.extend(
        [
            "--support",
            f"deterministic_manifest={paths['deterministic_manifest']}",
            "--support",
            f"deterministic_junit={paths['deterministic_junit']}",
            "--support",
            f"runtime_closure_manifest={paths['runtime_closure_manifest']}",
            "--support",
            f"command_provenance={paths['command_provenance']}",
        ]
    )
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    assert completed.returncode == 1
    assert completed.stdout, completed.stderr
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


def test_cli_does_not_publish_for_malformed_checksum_manifest(
    tmp_path: Path,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    manifest.write_text("malformed checksum row\n", encoding="utf-8")
    out = tmp_path / "release-verdict.json"

    completed = _run_cli(paths, manifest, out)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "release evidence validation failed\n"
    assert not out.exists()
    assert not list(tmp_path.glob(".release-verdict.json.*.tmp"))


def test_cli_does_not_publish_for_malformed_layer_argument(
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
    assert completed.stdout == ""
    assert completed.stderr == "release evidence validation failed\n"
    assert not out.exists()
    assert not list(tmp_path.glob(".release-verdict.json.*.tmp"))


@pytest.mark.parametrize("unsafe", ["delete", "symlink"])
def test_cli_does_not_publish_when_an_input_is_unsafe(
    tmp_path: Path, unsafe: str
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    target = paths["real_api"]
    if unsafe == "delete":
        target.unlink()
    else:
        moved = tmp_path / "moved-real-api.json"
        target.rename(moved)
        target.symlink_to(moved)
    out = tmp_path / "release-verdict.json"

    completed = _run_cli(paths, manifest, out)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "release evidence validation failed\n"
    assert not out.exists()


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
    command.extend(["--support", f"runtime_closure_manifest={paths['runtime_closure_manifest']}"])
    command.extend(["--support", f"command_provenance={paths['command_provenance']}"])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def test_bound_release_verdict_publishes_valid_exact_inputs(tmp_path: Path) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    out = tmp_path / "release-verdict.json"

    verdict = release_gate.produce_release_verdict(
        IDENTITY, paths, manifest, out
    )

    assert verdict["status"] == "PASS"
    assert json.loads(out.read_text(encoding="utf-8")) == verdict


def _install_committed_provenance_pair(provenance: Path) -> Path:
    entries = parse_provenance(provenance.read_bytes())
    projection = render_commands_projection(entries)
    provenance.with_suffix(".txt").write_bytes(projection)
    generation = "f" * 32
    generation_dir = provenance.parent / f".{provenance.name}.generations"
    generation_dir.mkdir(mode=0o700)
    generation_jsonl = generation_dir / f"{generation}.jsonl"
    generation_projection = generation_dir / f"{generation}.txt"
    generation_jsonl.write_bytes(provenance.read_bytes())
    generation_projection.write_bytes(projection)
    generation_jsonl.chmod(0o400)
    generation_projection.chmod(0o400)
    pointer = PairPointer(
        generation,
        hashlib.sha256(provenance.read_bytes()).hexdigest(),
        hashlib.sha256(projection).hexdigest(),
        len(entries),
        _spec_digest_summary(entries),
    )
    pointer_path = provenance.parent / f".{provenance.name}.pair"
    pointer_path.write_bytes(_render_pair_pointer(pointer))
    pointer_path.chmod(0o600)
    return pointer_path


def test_bound_release_verdict_accepts_valid_committed_provenance_pair(
    tmp_path: Path,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    out = tmp_path / "release-verdict.json"

    verdict = release_gate.produce_release_verdict(
        IDENTITY, paths, manifest, out
    )

    assert verdict["status"] == "PASS"
    assert json.loads(out.read_text(encoding="utf-8")) == verdict


def test_bound_release_verdict_rejects_invalid_committed_provenance_pair(
    tmp_path: Path,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    pointer = tmp_path / ".commands.jsonl.pair"
    generation = json.loads(pointer.read_text())["generation"]
    generation_jsonl = pointer.parent / ".commands.jsonl.generations" / f"{generation}.jsonl"
    generation_jsonl.chmod(0o600)
    generation_jsonl.write_bytes(b"tampered\n")
    generation_jsonl.chmod(0o400)

    with pytest.raises((RuntimeError, ValueError), match="provenance|release evidence"):
        release_gate.produce_release_verdict(
            IDENTITY,
            paths,
            manifest,
            tmp_path / "release-verdict.json",
        )


def test_bound_release_verdict_rejects_missing_provenance_pointer(
    tmp_path: Path,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    pointer = tmp_path / ".commands.jsonl.pair"
    pointer.unlink()
    out = tmp_path / "release-verdict.json"

    with pytest.raises((OSError, RuntimeError, ValueError), match="pointer|provenance"):
        release_gate.produce_release_verdict(IDENTITY, paths, manifest, out)

    assert not out.exists()


@pytest.mark.parametrize(
    ("hook_name", "mutation"),
    [
        ("after_checksum_read", "replace"),
        ("after_inputs_parsed", "delete"),
        ("pre_publish", "symlink"),
        ("post_publish", "hardlink"),
        ("pre_publish", "restore_bytes"),
        ("post_publish", "aba"),
    ],
)
def test_bound_release_verdict_rolls_back_when_inputs_drift(
    tmp_path: Path,
    hook_name: str,
    mutation: str,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    target = paths["real_api"]
    original = target.read_bytes()
    out = tmp_path / "release-verdict.json"

    def mutate() -> None:
        if mutation == "replace":
            replacement = tmp_path / "replacement.json"
            replacement.write_bytes(original)
            os.replace(replacement, target)
        elif mutation == "delete":
            target.unlink()
        elif mutation == "symlink":
            moved = tmp_path / "moved-real-api.json"
            target.rename(moved)
            target.symlink_to(moved)
        elif mutation == "hardlink":
            os.link(target, tmp_path / "real-api-alias.json")
        elif mutation == "restore_bytes":
            target.write_bytes(original + b" ")
            target.write_bytes(original)
        elif mutation == "aba":
            moved = tmp_path / "aba-original.json"
            replacement = tmp_path / "aba-replacement.json"
            target.rename(moved)
            replacement.write_bytes(original)
            replacement.rename(target)
            target.unlink()
            moved.rename(target)
        else:  # pragma: no cover - the parametrization is exhaustive.
            raise AssertionError(mutation)

    hooks = {hook_name: mutate}
    with pytest.raises(RuntimeError, match="release evidence changed"):
        release_gate.produce_release_verdict(
            IDENTITY,
            paths,
            manifest,
            out,
            **hooks,
        )

    assert not out.exists()


def test_bound_release_input_detects_parent_directory_aba(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    evidence = evidence_dir / "report.json"
    evidence.write_bytes(b"evidence")
    bound = release_gate._read_bound_release_input(evidence)
    moved = tmp_path / "moved-evidence"

    evidence_dir.rename(moved)
    moved.rename(evidence_dir)

    with pytest.raises(RuntimeError, match="release evidence changed"):
        release_gate._require_release_input_unchanged(bound)


def test_bound_release_input_detects_allowed_evidence_root_aba(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "report.json"
    evidence.write_bytes(b"evidence")
    bound = release_gate._read_bound_release_input(evidence)
    moved = tmp_path / "moved-evidence"

    evidence_root.rename(moved)
    moved.rename(evidence_root)

    with pytest.raises(RuntimeError, match="release evidence changed"):
        release_gate._require_release_input_unchanged(
            bound,
            protected_root=evidence_root,
            allowed_changed_directory=evidence_root,
        )


def test_bound_release_input_allows_unrelated_parent_sibling_churn(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = evidence_root / "report.json"
    evidence.write_bytes(b"evidence")
    bound = release_gate._read_bound_release_input(evidence)

    (tmp_path / "unrelated-sibling").write_bytes(b"unrelated")

    release_gate._require_release_input_unchanged(
        bound,
        protected_root=evidence_root,
        allowed_changed_directory=evidence_root,
    )


def test_bound_release_verdict_leaves_no_output_when_publisher_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _, manifest = _write_evidence(tmp_path)
    out = tmp_path / "release-verdict.json"
    monkeypatch.setattr(
        release_gate,
        "_atomic_write",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("publisher failed")),
    )

    with pytest.raises(RuntimeError, match="publisher failed"):
        release_gate.produce_release_verdict(IDENTITY, paths, manifest, out)

    assert not out.exists()


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
