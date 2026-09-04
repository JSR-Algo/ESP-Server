import json
from pathlib import Path

import pytest

from scripts.google_live_evidence_runner import (
    EvidenceRunner,
    EvidenceStateError,
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
    return EvidenceRunner.initialize(tmp_path, run_id=run_id, identity=IDENTITY)


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

