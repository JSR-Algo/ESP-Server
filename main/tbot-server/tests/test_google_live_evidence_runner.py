import json
from pathlib import Path

import pytest
from types import SimpleNamespace

from scripts.google_live_evidence_runner import (
    COMMAND_ORDER,
    EvidenceRunner,
    EvidenceStateError,
    LAYERS,
    TerminalLayerStateError,
)


IDENTITY = {
    "gitSha": "a" * 40,
    "imageDigest": "sha256:" + "b" * 64,
    "firmwareIdentity": "firmware-1",
    "configFingerprint": "sha256:" + "c" * 64,
    "fixtureSha256": "d" * 64,
}


def _runner(tmp_path: Path, run_id: str = "20260904T010203Z") -> EvidenceRunner:
    runner = EvidenceRunner.initialize(tmp_path, run_id=run_id, identity=IDENTITY)
    for relative in ("fixture.wav", "server.log", "baseline/report.json", "lesson-manifest.json"):
        path = runner.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}\n")
    runner.operator_config = {
        "fixture": str(runner.root / "fixture.wav"),
        "websocket_url": "ws://127.0.0.1/ws",
        "device_id": "device-1",
        "client_id": "client-1",
        "journey_id": "journey-1",
        "server_log": str(runner.root / "server.log"),
        "config_json": "{}",
        "expected_candidate_json": json.dumps(IDENTITY, sort_keys=True),
        "evidence_control_url": "http://127.0.0.1/internal",
        "baseline_report": str(runner.root / "baseline/report.json"),
        "lesson_manifest": str(runner.root / "lesson-manifest.json"),
        "base_url": "http://127.0.0.1",
    }
    return runner


def test_terminal_failure_is_immutable_and_blocks_dependents(tmp_path: Path):
    runner = _runner(tmp_path)
    runner.start_layer("deterministic")
    runner.finish_layer("deterministic", "PASS")
    runner.start_layer("real_api")
    runner.finish_layer("real_api", "PASS")
    runner.start_layer("websocket_e2e")
    runner.finish_layer(
        "websocket_e2e", "FAIL", failure={"code": "EVIDENCE_INTEGRITY"}
    )

    with pytest.raises(TerminalLayerStateError):
        runner.finish_layer("websocket_e2e", "PASS")
    assert runner.state("physical") == "PENDING"
    assert not runner.can_start("physical")


def test_failure_retains_only_first_safe_code(tmp_path: Path):
    runner = _runner(tmp_path)
    runner.start_layer("deterministic")
    runner.finish_layer(
        "deterministic",
        "FAIL",
        failure={"code": "NETWORK", "message": "api-key=secret child transcript"},
    )
    state = json.loads(runner.state_path.read_text())

    assert state["layers"]["deterministic"]["firstFailure"]["code"] == "NETWORK"
    assert "secret" not in json.dumps(state)
    with pytest.raises(TerminalLayerStateError):
        runner.finish_layer("deterministic", "FAIL", failure={"code": "TIMEOUT"})


def test_new_run_id_is_required_for_retry_and_resume_is_refused(tmp_path: Path):
    runner = _runner(tmp_path)
    runner.start_layer("deterministic")
    runner.finish_layer("deterministic", "FAIL", failure={"code": "CANCELLED"})

    with pytest.raises(FileExistsError):
        EvidenceRunner.initialize(tmp_path, run_id=runner.run_id, identity=IDENTITY)
    with pytest.raises(EvidenceStateError):
        EvidenceRunner.open(runner.root, resume=True)
    assert _runner(tmp_path, "20260904T010204Z").run_id == "20260904T010204Z"


def test_invalid_run_id_and_evidence_root_alias_fail_closed(tmp_path: Path):
    with pytest.raises(ValueError):
        EvidenceRunner.initialize(tmp_path, run_id="latest", identity=IDENTITY)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        EvidenceRunner.initialize(alias, run_id="20260904T010205Z", identity=IDENTITY)


def test_state_write_is_atomic_when_replace_fails(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    before = runner.state_path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated")

    monkeypatch.setattr("scripts.google_live_evidence_runner.os.replace", fail_replace)
    with pytest.raises(OSError):
        runner.start_layer("deterministic")
    assert runner.state_path.read_bytes() == before


def test_physical_command_requires_explicit_operator_confirmation(tmp_path: Path):
    runner = _runner(tmp_path)
    for layer in ("deterministic", "real_api", "websocket_e2e", "candidate_soak"):
        runner.start_layer(layer)
        runner.finish_layer(layer, "PASS")
    with pytest.raises(PermissionError):
        runner.require_physical_confirmation(
            operator_confirmed=False, transcript_plan_stdin=True
        )
    with pytest.raises(PermissionError):
        runner.require_physical_confirmation(
            operator_confirmed=True, transcript_plan_stdin=False
        )


def test_command_specs_preserve_exact_order_and_secret_assignment(tmp_path: Path):
    runner = _runner(tmp_path)
    specs = runner.command_specs()
    assert [spec.command_id for spec in specs] == [
        "deterministic.produce", "real_api.round_trip", "websocket.transport",
        "websocket.log_analysis", "websocket.correlation",
        "candidate_soak.produce", "candidate_soak.replay",
        "physical.capture_and_audit",
    ]
    assert [spec.command_id for spec in specs if spec.secret_env] == [
        "real_api.round_trip", "websocket.transport", "candidate_soak.produce",
        "physical.capture_and_audit",
    ]
    assert specs[5].stdin_source == "protected_candidate_plan"
    assert specs[7].stdin_source == "protected_transcript_plan"


def test_multi_command_layers_stay_running_until_authoritative_output(tmp_path: Path):
    runner = _runner(tmp_path)
    calls = []

    def execute(spec, **kwargs):
        calls.append((spec.command_id, kwargs))
        return SimpleNamespace(policy_satisfied=True)

    runner.execute_layer("deterministic.produce", executor=execute)
    runner.execute_layer("real_api.round_trip", executor=execute)
    runner.execute_layer("websocket.transport", executor=execute)
    assert runner.state("websocket_e2e") == "RUNNING"
    runner.execute_layer("websocket.log_analysis", executor=execute)
    assert runner.state("server_regression") == "PASS"
    runner.execute_layer("websocket.correlation", executor=execute)
    assert runner.state("websocket_e2e") == "PASS"
    runner.execute_layer("candidate_soak.produce", stdin_bytes=b"{}", executor=execute)
    assert runner.state("candidate_soak") == "RUNNING"
    runner.execute_layer("candidate_soak.replay", executor=execute)
    assert runner.state("candidate_soak") == "PASS"
    runner.execute_layer("physical.capture_and_audit", stdin_bytes=b"{}", executor=execute)
    assert runner.state("physical") == "PASS"
    assert [item[0] for item in calls] == list(COMMAND_ORDER)


def test_finalize_refuses_nonpassing_layer_and_hashes_closed_artifacts(tmp_path: Path):
    runner = _runner(tmp_path)
    with pytest.raises(EvidenceStateError):
        runner.finalize()
    for layer in ("deterministic", "real_api", "websocket_e2e", "server_regression", "candidate_soak", "physical"):
        runner.start_layer(layer) if runner.state(layer) == "PENDING" and runner.can_start(layer) else None
        if runner.state(layer) == "RUNNING":
            runner.finish_layer(layer, "PASS")
    for layer in LAYERS:
        path = runner.root / layer.replace("_", "-") / "report.json"
        path.write_text(json.dumps({"status": "PASS"}) + "\n")
    (runner.root / "commands.jsonl").write_text("")
    (runner.root / "commands.txt").write_text("")
    checksum = runner.finalize()
    assert "timeline.log" in checksum.read_text()
