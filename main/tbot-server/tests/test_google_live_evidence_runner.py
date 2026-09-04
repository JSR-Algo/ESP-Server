import json
import contextlib
import hashlib
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
    runner = EvidenceRunner.initialize(
        tmp_path, run_id=run_id, identity=IDENTITY, verify_repository=False
    )
    for relative in ("fixture.wav", "server.log", "baseline/report.json", "lesson-manifest.json"):
        path = runner.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}\n")
    candidate_identity = runner.root / "candidate-identity.json"
    candidate_identity.write_text(json.dumps(IDENTITY, sort_keys=True) + "\n")
    runner.operator_config = {
        "fixture": str(runner.root / "fixture.wav"),
        "websocket_url": "ws://127.0.0.1/ws",
        "device_id": "device-1",
        "client_id": "client-1",
        "journey_id": "journey-1",
        "server_log": str(runner.root / "server.log"),
        "config_json": "{}",
        "expected_candidate_json": str(candidate_identity),
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
        EvidenceRunner.initialize(tmp_path, run_id=runner.run_id, identity=IDENTITY, verify_repository=False)
    with pytest.raises(EvidenceStateError):
        EvidenceRunner.open(runner.root, resume=True)
    assert _runner(tmp_path, "20260904T010204Z").run_id == "20260904T010204Z"


def test_invalid_run_id_and_evidence_root_alias_fail_closed(tmp_path: Path):
    with pytest.raises(ValueError):
        EvidenceRunner.initialize(tmp_path, run_id="latest", identity=IDENTITY)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        EvidenceRunner.initialize(alias, run_id="20260904T010205Z", identity=IDENTITY, verify_repository=False)


def test_production_init_binds_effective_config_and_fixture(tmp_path: Path):
    config = tmp_path / "config.json"
    fixture = tmp_path / "fixture.wav"
    config.write_text("{}\n")
    fixture.write_bytes(b"fixture")
    identity = dict(IDENTITY)
    identity["fixtureSha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="identity mismatch"):
        EvidenceRunner.initialize(
            tmp_path,
            run_id="20260904T010205Z",
            identity=identity,
            effective_config_json=config,
            fixture_path=fixture,
        )


def test_reopen_rejects_unrelated_repository_change(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path)
    repository = tmp_path / "repo"
    repository.mkdir()
    runner._state["repositoryRoot"] = str(repository)
    runner._save()

    import scripts.google_live_trusted_git as trusted_git

    monkeypatch.setattr(
        trusted_git, "trusted_git_session", lambda: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        trusted_git,
        "git_output",
        lambda _root, *args: (
            (IDENTITY["gitSha"] + "\n").encode()
            if args == ("rev-parse", "HEAD")
            else b"?? unrelated.txt\0"
        ),
    )

    with pytest.raises(EvidenceStateError, match="repository changed"):
        EvidenceRunner.open(runner.root)


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


def test_live_server_log_is_runtime_source_not_immutable_command_input(tmp_path: Path):
    runner = _runner(tmp_path)
    specs = {spec.command_id: spec for spec in runner.command_specs()}

    assert runner.root / "server.log" not in specs["candidate_soak.produce"].inputs
    assert runner.root / "server.log" not in specs["physical.capture_and_audit"].inputs
    assert specs["physical.capture_and_audit"].outputs == (
        runner.root / "server-regression/report.json",
        runner.root / "physical/terminal-snapshot.json",
        runner.root / "physical/report.json",
    )


def test_release_gate_trusts_the_same_authoritative_server_report_paths(tmp_path: Path):
    from scripts.google_live_release_gate import _trusted_command_specs

    runner = _runner(tmp_path)
    actual = {spec.command_id: spec for spec in runner.command_specs()}
    trusted = _trusted_command_specs(IDENTITY)

    for command_id in COMMAND_ORDER:
        assert tuple(path.relative_to(runner.root).as_posix() for path in actual[command_id].inputs) == trusted[command_id].input_labels
        assert tuple(path.relative_to(runner.root).as_posix() for path in actual[command_id].outputs) == trusted[command_id].output_labels


@pytest.mark.parametrize("value", ["../outside.log", "alias.log"])
def test_live_server_log_must_be_regular_file_inside_evidence_root(tmp_path: Path, value: str):
    runner = _runner(tmp_path)
    outside = runner.root.parent / "outside.log"
    outside.write_text("x")
    if value == "alias.log":
        (runner.root / value).symlink_to(outside)
    else:
        value = str(runner.root / value)
    runner.operator_config["server_log"] = value
    with pytest.raises(EvidenceStateError, match="server log"):
        runner.command_specs()


def test_multi_command_layers_stay_running_until_authoritative_output(tmp_path: Path):
    runner = _runner(tmp_path)
    calls = []

    def execute(spec, **kwargs):
        calls.append((spec.command_id, kwargs))
        for output in spec.outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("{}\n")
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


def test_command_order_is_enforced_independently_of_layer_state(tmp_path: Path):
    runner = _runner(tmp_path)
    with pytest.raises(EvidenceStateError, match="command order"):
        runner.execute_layer(
            "real_api.round_trip",
            executor=lambda *args, **kwargs: SimpleNamespace(policy_satisfied=True),
        )


def test_synthetic_dry_run_uses_execute_and_finalize_paths(tmp_path: Path):
    runner = _runner(tmp_path)
    seen = []

    def execute(spec, **kwargs):
        seen.append(spec.command_id)
        return SimpleNamespace(policy_satisfied=True)

    with pytest.raises(EvidenceStateError, match="command provenance is required"):
        runner.synthetic_dry_run(executor=execute)
    assert seen == list(COMMAND_ORDER)
    assert not (runner.root / "release-verdict.json").is_file()


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
        report = {"status": "PASS"}
        if layer == "server_regression":
            report.update(evidenceScope={"journeyId": "journey-1"}, logWindow={"windowId": "window-1", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"})
        path.write_text(json.dumps(report) + "\n")
    (runner.root / "commands.jsonl").write_text("")
    (runner.root / "commands.txt").write_text("")
    checksum = runner.finalize(
        release_gate_fn=lambda identity, paths, checksums, output: {
            "status": "PASS",
            "candidateIdentity": identity,
        }
    )
    assert "timeline.log" in checksum.read_text()
    assert json.loads((runner.root / "release-verdict.json").read_text())["status"] == "PASS"


def test_finalize_timeline_uses_each_layers_own_window_metadata(tmp_path: Path):
    runner = _runner(tmp_path)
    for layer in LAYERS:
        runner._state["layers"][layer].update(state="PASS")
    runner._save()
    reports = {
        "deterministic": {"status": "PASS"},
        "server_regression": {"evidenceScope": {"journeyId": "websocket"}, "logWindow": {"windowId": "ws-window", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"}},
        "real_api": {"status": "PASS"},
        "websocket_e2e": {"logEvidence": {"evidenceScope": {"journeyId": "websocket"}, "logWindow": {"windowId": "ws-window", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"}}},
        "physical": {"logEvidence": {"evidenceScope": {"journeyId": "physical"}, "logWindow": {"windowId": "physical-window", "start": "2026-01-01T02:00:00Z", "end": "2026-01-01T02:01:00Z"}}},
        "candidate_soak": {"evidenceAnchors": {"serverStartUtc": "2026-01-01T01:00:00Z", "serverEndUtc": "2026-01-01T01:30:00Z"}, "evidenceExecutions": [{"journeyId": "soak-1", "windowId": "soak-window-1", "logWindow": {"windowId": "soak-window-1", "start": "2026-01-01T01:00:00Z", "end": "2026-01-01T01:10:00Z"}}], "quietPadding": [{"journeyId": "padding-1", "windowId": "padding-window-1", "logWindow": {"windowId": "padding-window-1", "start": "2026-01-01T01:10:00Z", "end": "2026-01-01T01:30:00Z"}}]},
    }
    for layer, report in reports.items():
        path = runner.root / layer.replace("_", "-") / "report.json"
        path.write_text(json.dumps(report) + "\n")
    for relative in ("deterministic/node-manifest.txt", "deterministic/pytest.xml", "physical/server-window.log", "physical/server-report.json"):
        path = runner.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    (runner.root / "commands.jsonl").write_text("")

    runner.finalize(release_gate_fn=lambda identity, paths, checksums, output: {"status": "PASS"})

    rows = {row["layer"]: row for row in map(json.loads, (runner.root / "timeline.log").read_text().splitlines())}
    assert rows["deterministic"]["journeyId"] is None
    assert rows["websocket_e2e"]["journeyId"] == "websocket"
    assert rows["physical"]["journeyId"] == "physical"
    assert [item["journeyId"] for item in rows["candidate_soak"]["windows"]] == ["soak-1", "padding-1"]
