"""Fail-closed orchestration and state for the unified Google Live evidence run."""

from __future__ import annotations

import json
import os
import re
import tempfile
import argparse
import hashlib
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYERS = ("deterministic", "server_regression", "real_api", "websocket_e2e", "physical", "candidate_soak")
DEPENDENCIES = {
    "real_api": ("deterministic",),
    "websocket_e2e": ("deterministic", "real_api"),
    "candidate_soak": ("deterministic", "real_api", "websocket_e2e"),
    "physical": ("deterministic", "real_api", "websocket_e2e", "candidate_soak", "server_regression"),
    "server_regression": ("websocket_e2e",),
}
SAFE_FAILURE_CODES = {"AUTH", "CONFIG", "QUOTA", "PROTOCOL", "TIMEOUT", "NETWORK", "PROVIDER", "CLEANUP", "RESOURCE", "IDENTITY", "PRIVACY", "EVIDENCE_INTEGRITY", "CANCELLED"}
COMMAND_ORDER = ("deterministic.produce", "real_api.round_trip", "websocket.transport", "websocket.log_analysis", "websocket.correlation", "candidate_soak.produce", "candidate_soak.replay", "physical.capture_and_audit")
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")


class EvidenceStateError(RuntimeError):
    pass


class TerminalLayerStateError(EvidenceStateError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _window_metadata(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    window = value.get("logWindow")
    scope = value.get("evidenceScope")
    if not isinstance(window, dict):
        return None
    journey_id = scope.get("journeyId") if isinstance(scope, dict) else None
    journey_id = journey_id or value.get("journeyId") or window.get("journeyId")
    fields = (journey_id, window.get("windowId"), window.get("start"), window.get("end"))
    if not all(isinstance(item, str) and item for item in fields):
        return None
    return {"journeyId": fields[0], "windowId": fields[1], "startedAtUtc": fields[2], "endedAtUtc": fields[3]}


def _timeline_metadata(layer: str, report: dict[str, Any]) -> dict[str, Any]:
    source = report.get("logEvidence") if layer in {"websocket_e2e", "physical"} else report
    single = _window_metadata(source)
    if single is not None:
        return {**single, "windows": [single]}
    if layer == "candidate_soak":
        windows = []
        for item in [*report.get("evidenceExecutions", []), *report.get("quietPadding", [])]:
            window = _window_metadata(item)
            if window is not None:
                windows.append(window)
        anchors = report.get("evidenceAnchors", {})
        return {
            "journeyId": None,
            "windowId": None,
            "startedAtUtc": anchors.get("serverStartUtc"),
            "endedAtUtc": anchors.get("serverEndUtc"),
            "windows": windows,
        }
    return {"journeyId": None, "windowId": None, "startedAtUtc": None, "endedAtUtc": None, "windows": []}


class EvidenceRunner:
    def __init__(self, root: Path, run_id: str, identity: dict[str, str], state: dict[str, Any], operator_config: dict[str, str] | None = None):
        self.root = root
        self.run_id = run_id
        self.identity = dict(identity)
        self.state_path = root / "run-state.json"
        self._state = state
        self.operator_config = dict(operator_config or {})

    @classmethod
    def initialize(cls, evidence_root: Path, *, run_id: str, identity: dict[str, str], operator_config: dict[str, str] | None = None, verify_repository: bool = True, repository_root: Path | None = None) -> "EvidenceRunner":
        evidence_root = Path(evidence_root)
        if evidence_root.is_symlink() or not evidence_root.is_absolute():
            raise ValueError("evidence root must be an absolute non-alias path")
        if RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run ID is invalid")
        required = {"gitSha", "imageDigest", "firmwareIdentity", "configFingerprint", "fixtureSha256"}
        if (
            set(identity) != required
            or re.fullmatch(r"[0-9a-f]{40}", identity.get("gitSha", "")) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", identity.get("imageDigest", "")) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", identity.get("configFingerprint", "")) is None
            or re.fullmatch(r"[0-9a-f]{64}", identity.get("fixtureSha256", "")) is None
            or re.fullmatch(r"[A-Za-z0-9._:+/-]{1,128}", identity.get("firmwareIdentity", "")) is None
        ):
            raise ValueError("candidate identity is invalid")
        if verify_repository:
            from scripts.google_live_trusted_git import git_output, trusted_git_session
            repository_root = Path(__file__).resolve().parents[3] if repository_root is None else repository_root
            with trusted_git_session():
                head = git_output(repository_root, "rev-parse", "HEAD").decode().strip()
                status = git_output(repository_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            if head != identity["gitSha"]:
                raise ValueError("candidate git SHA does not match repository HEAD")
            if status:
                raise ValueError("candidate worktree must be clean before evidence init")
        root = evidence_root / run_id
        if root.exists() or root.is_symlink():
            raise FileExistsError(root)
        root.mkdir(mode=0o700, parents=True)
        for directory in ("deterministic", "server-regression", "real-api", "websocket-e2e", "physical", "candidate-soak"):
            (root / directory).mkdir(mode=0o700)
        closure_source = Path(__file__).resolve().parents[1] / "tests/fixtures/google_live_runtime_closure_manifest.json"
        _atomic(root / "runtime-closure.json", closure_source.read_bytes())
        state = {"schemaVersion": "google-live-evidence-run.v1", "unified": True, "runId": run_id, "nextCommandIndex": 0, "candidateIdentity": dict(identity), "createdAt": _now(), "layers": {name: {"state": "PENDING", "startedAt": None, "endedAt": None, "firstFailure": None, "artifactSha256": {}} for name in LAYERS}}
        _atomic(root / "run-state.json", (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode())
        return cls(root, run_id, identity, state, operator_config)

    @classmethod
    def open(cls, root: Path, *, resume: bool = False) -> "EvidenceRunner":
        state = json.loads(Path(root, "run-state.json").read_text(encoding="utf-8"))
        failed = any(item["state"] == "FAIL" or (item["state"] == "SKIPPED" and name != "websocket_e2e") for name, item in state["layers"].items())
        if resume or failed:
            raise EvidenceStateError("failed evidence runs cannot be resumed; create a new RUN_ID")
        return cls(Path(root), state["runId"], state["candidateIdentity"], state)

    def _save(self) -> None:
        _atomic(self.state_path, (json.dumps(self._state, sort_keys=True, separators=(",", ":")) + "\n").encode())

    def state(self, layer: str) -> str:
        self._check_layer(layer)
        return self._state["layers"][layer]["state"]

    def can_start(self, layer: str) -> bool:
        self._check_layer(layer)
        record = self._state["layers"][layer]
        if layer == "server_regression":
            return record["state"] == "PENDING" and self.state("websocket_e2e") in {"RUNNING", "PASS"}
        if layer == "websocket_e2e" and record["state"] == "SKIPPED":
            return False
        return record["state"] == "PENDING" and all(self.state(dep) == "PASS" for dep in DEPENDENCIES.get(layer, ()))

    def start_layer(self, layer: str) -> None:
        self._check_layer(layer)
        record = self._state["layers"][layer]
        if record["state"] != "PENDING":
            raise TerminalLayerStateError("layer is not pending")
        if not self.can_start(layer):
            raise EvidenceStateError("layer dependency is not passing")
        record["state"] = "RUNNING"
        record["startedAt"] = _now()
        self._save()

    def finish_layer(self, layer: str, state: str, *, failure: dict[str, Any] | None = None, artifact_sha256: dict[str, str] | None = None) -> None:
        self._check_layer(layer)
        if state not in {"PASS", "FAIL", "SKIPPED"}:
            raise ValueError("terminal layer state is invalid")
        record = self._state["layers"][layer]
        if record["state"] != "RUNNING":
            raise TerminalLayerStateError("layer is already terminal or not running")
        record["state"] = state
        record["endedAt"] = _now()
        if state != "PASS":
            code = (failure or {}).get("code", "EVIDENCE_INTEGRITY")
            record["firstFailure"] = {"code": code if code in SAFE_FAILURE_CODES else "EVIDENCE_INTEGRITY", "at": record["endedAt"]}
        if artifact_sha256:
            record["artifactSha256"] = {str(k): str(v) for k, v in artifact_sha256.items()}
        self._save()

    def require_physical_confirmation(self, *, operator_confirmed: bool, transcript_plan_stdin: bool) -> None:
        if not operator_confirmed or not transcript_plan_stdin:
            raise PermissionError("physical evidence requires operator confirmation and protected transcript plan stdin")

    def all_passed(self) -> bool:
        return all(self.state(layer) == "PASS" for layer in LAYERS)

    def command_specs(self) -> tuple[Any, ...]:
        """Return the immutable command declarations used by this run."""
        from scripts.google_live_command_runner import CommandSpec
        root = self.root
        py = Path(sys.executable)
        required_operator = {"fixture", "websocket_url", "device_id", "client_id", "journey_id", "server_log", "config_json", "expected_candidate_json", "evidence_control_url", "baseline_report", "lesson_manifest", "base_url"}
        if set(self.operator_config) != required_operator:
            raise EvidenceStateError("validated operator configuration is required")
        op = self.operator_config
        runtime_server_log = os.path.relpath(op["server_log"], root)
        common = ("--candidate-git-sha", self.identity["gitSha"], "--candidate-image-digest", self.identity["imageDigest"], "--firmware-identity", self.identity["firmwareIdentity"], "--fixture-sha256", self.identity["fixtureSha256"])
        specs = (
            CommandSpec("deterministic.produce", (str(py), "scripts/google_live_deterministic_evidence.py", "--manifest", str(root / "deterministic/node-manifest.txt"), "--junit-out", str(root / "deterministic/pytest.xml"), "--report", str(root / "deterministic/report.json"), *common[:6], "--config-fingerprint", self.identity["configFingerprint"], *common[6:]), cwd=root, candidate_identity=self.identity, outputs=(root / "deterministic/report.json", root / "deterministic/node-manifest.txt", root / "deterministic/pytest.xml")),
            CommandSpec("real_api.round_trip", (str(py), "scripts/google_live_smoke.py", "--round-trip", "--audio-file", op["fixture"], "--report", str(root / "real-api/report.json"), *common[:6], "--config-fingerprint", self.identity["configFingerprint"], *common[6:]), cwd=root, candidate_identity=self.identity, secret_env=("GOOGLE_API_KEY",), inputs=(Path(op["fixture"]),), outputs=(root / "real-api/report.json",)),
            CommandSpec("websocket.transport", (str(py), "scripts/voice_mode_websocket_audio_bargein.py", "--websocket-url", op["websocket_url"], "--device-id", op["device_id"], "--client-id", op["client_id"], "--journey-id", op["journey_id"], *common[:6], "--config-json", op["config_json"], *common[6:], "--report", str(root / "websocket-e2e/transport.json")), cwd=root, candidate_identity=self.identity, secret_env=("TBOT_DEVICE_MINT_SECRET",), outputs=(root / "websocket-e2e/transport.json",), expected_exit_codes=(0, 1)),
            CommandSpec("websocket.log_analysis", (str(py), "scripts/analyze_google_live_log.py", "--log", op["server_log"], "--reliability-window", "--journey-id", op["journey_id"], "--out-json", str(root / "server-regression/report.json")), cwd=root, candidate_identity=self.identity, inputs=(Path(op["server_log"]),), outputs=(root / "server-regression/report.json",)),
            CommandSpec("websocket.correlation", (str(py), "scripts/analyze_google_live_log.py", "--log", op["server_log"], "--correlate-transport", str(root / "websocket-e2e/transport.json"), "--expected-candidate-json", op["expected_candidate_json"], "--out-json", str(root / "websocket-e2e/report.json")), cwd=root, candidate_identity=self.identity, inputs=(Path(op["server_log"]), root / "websocket-e2e/transport.json", root / "server-regression/report.json"), outputs=(root / "websocket-e2e/report.json",)),
            CommandSpec("candidate_soak.produce", (str(py), "scripts/google_live_robot_soak.py", "--mode", "candidate", "--produce-candidate-evidence", str(root / "candidate-soak/journey-evidence.json"), "--evidence-control-url", op["evidence_control_url"], "--server-log", runtime_server_log, "--run-id", self.run_id, "--baseline-report", op["baseline_report"], "--real-api-report", str(root / "real-api/report.json"), "--transport-report", str(root / "websocket-e2e/transport.json"), "--correlated-transport-report", str(root / "websocket-e2e/report.json"), "--log-reliability-report", str(root / "server-regression/report.json"), "--lesson-manifest", op["lesson_manifest"], "--config-json", op["config_json"], *common), cwd=root, candidate_identity=self.identity, secret_env=("TBOT_DEVICE_MINT_SECRET",), stdin_source="protected_candidate_plan", inputs=(Path(op["baseline_report"]), root / "real-api/report.json", root / "websocket-e2e/transport.json", root / "websocket-e2e/report.json", root / "server-regression/report.json", Path(op["lesson_manifest"])), outputs=(root / "candidate-soak/journey-evidence.json",)),
            CommandSpec("candidate_soak.replay", (str(py), "scripts/google_live_robot_soak.py", "--mode", "candidate", "--journey-evidence", str(root / "candidate-soak/journey-evidence.json"), "--report", str(root / "candidate-soak/report.json"), "--baseline-report", op["baseline_report"], "--real-api-report", str(root / "real-api/report.json"), "--transport-report", str(root / "websocket-e2e/transport.json"), "--correlated-transport-report", str(root / "websocket-e2e/report.json"), "--log-reliability-report", str(root / "server-regression/report.json"), "--lesson-manifest", op["lesson_manifest"], "--config-json", op["config_json"], *common), cwd=root, candidate_identity=self.identity, inputs=(root / "candidate-soak/journey-evidence.json", Path(op["baseline_report"]), root / "real-api/report.json", root / "websocket-e2e/transport.json", root / "websocket-e2e/report.json", root / "server-regression/report.json", Path(op["lesson_manifest"])), outputs=(root / "candidate-soak/report.json",)),
            CommandSpec("physical.capture_and_audit", (str(py), "scripts/google_live_physical_evidence.py", "--candidate-soak-report", str(root / "candidate-soak/report.json"), "--report", str(root / "physical/report.json"), "--operator-confirmed", "--transcript-plan-stdin", "--base-url", op["base_url"], "--device-id", op["device_id"], "--client-id", op["client_id"], "--server-log", runtime_server_log, *common), cwd=root, candidate_identity=self.identity, secret_env=("TBOT_DEVICE_MINT_SECRET",), stdin_source="protected_transcript_plan", inputs=(root / "candidate-soak/report.json",), outputs=(root / "physical/server-window.log", root / "physical/server-report.json", root / "physical/report.json")),
        )
        return specs

    def execute_layer(self, command_id: str, *, stdin_bytes: bytes | None = None, env: dict[str, str] | None = None, executor: Any | None = None) -> Any:
        if command_id not in COMMAND_ORDER:
            raise ValueError("unknown command ID")
        expected_index = self._state.get("nextCommandIndex", 0)
        if expected_index >= len(COMMAND_ORDER) or COMMAND_ORDER[expected_index] != command_id:
            raise EvidenceStateError("command order is invalid")
        layer = {"deterministic.produce": "deterministic", "real_api.round_trip": "real_api", "websocket.transport": "websocket_e2e", "websocket.log_analysis": "server_regression", "websocket.correlation": "websocket_e2e", "candidate_soak.produce": "candidate_soak", "candidate_soak.replay": "candidate_soak", "physical.capture_and_audit": "physical"}[command_id]
        if self.state(layer) == "PENDING":
            self.start_layer(layer)
        elif command_id == "websocket.correlation" and self.state(layer) == "SKIPPED":
            self._state["layers"][layer]["state"] = "RUNNING"
            self._state["layers"][layer]["startedAt"] = _now()
            self._save()
        spec = next(item for item in self.command_specs() if item.command_id == command_id)
        if executor is None:
            from scripts.google_live_command_runner import execute_and_record
            executor = execute_and_record
        try:
            result = executor(spec, provenance=self.root / "commands.jsonl", env=env, stdin_bytes=stdin_bytes)
            self._state["nextCommandIndex"] = expected_index + 1
            self._save()
            terminal_command = command_id in {"deterministic.produce", "real_api.round_trip", "websocket.log_analysis", "websocket.correlation", "candidate_soak.replay", "physical.capture_and_audit"}
            if getattr(result, "policy_satisfied", False) and terminal_command:
                self.finish_layer(layer, "PASS")
            elif not getattr(result, "policy_satisfied", False):
                self.finish_layer(layer, "SKIPPED" if command_id == "websocket.transport" else "FAIL", failure={"code": "PROVIDER"})
            return result
        except KeyboardInterrupt:
            self.finish_layer(layer, "FAIL", failure={"code": "CANCELLED"})
            raise
        except Exception:
            if self.state(layer) == "RUNNING":
                self.finish_layer(layer, "FAIL", failure={"code": "EVIDENCE_INTEGRITY"})
            raise

    def finalize(self, *, release_gate_fn: Any | None = None) -> Path:
        if not self.all_passed():
            raise EvidenceStateError("all evidence layers must pass before finalize")
        provenance = self.root / "commands.jsonl"
        projection = self.root / "commands.txt"
        if not provenance.is_file():
            raise EvidenceStateError("command provenance is required before finalize")
        from scripts.google_live_command_runner import parse_provenance, render_commands_projection
        entries = parse_provenance(provenance.read_bytes())
        _atomic(projection, render_commands_projection(entries))
        rows = []
        for layer in LAYERS:
            report = next(self.root.glob(f"{layer.replace('_', '-')}/report.json"), None)
            if report is not None:
                payload = json.loads(report.read_text(encoding="utf-8"))
                rows.append(json.dumps({"layer": layer, "artifact": report.relative_to(self.root).as_posix(), **_timeline_metadata(layer, payload)}, sort_keys=True))
        timeline = self.root / "timeline.log"
        _atomic(timeline, ("\n".join(rows) + "\n").encode())
        artifacts = [self.root / name for name in ("deterministic/report.json", "deterministic/node-manifest.txt", "deterministic/pytest.xml", "server-regression/report.json", "real-api/report.json", "websocket-e2e/report.json", "physical/report.json", "candidate-soak/report.json", "commands.jsonl", "commands.txt", "timeline.log", "runtime-closure.json")]
        checksum = self.root / "checksums.sha256"
        lines = []
        for artifact in artifacts:
            if artifact.is_file():
                lines.append(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.relative_to(self.root).as_posix()}")
        _atomic(checksum, ("\n".join(lines) + "\n").encode())
        if release_gate_fn is None:
            from scripts.google_live_release_gate import produce_release_verdict
            release_gate_fn = lambda identity, paths, manifest, output: produce_release_verdict(identity, paths, manifest, output, unified=True)
        paths = {layer: self.root / layer.replace("_", "-") / "report.json" for layer in LAYERS}
        paths.update({"deterministic_manifest": self.root / "deterministic/node-manifest.txt", "deterministic_junit": self.root / "deterministic/pytest.xml", "command_provenance": provenance, "command_projection": projection, "timeline_index": timeline, "runtime_closure_manifest": self.root / "runtime-closure.json"})
        verdict_path = self.root / "release-verdict.json"
        verdict = release_gate_fn(self.identity, paths, checksum, verdict_path)
        if not verdict_path.exists():
            _atomic(verdict_path, (json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n").encode())
        if verdict.get("status") != "PASS":
            raise EvidenceStateError("release gate rejected unified evidence")
        return checksum

    def synthetic_dry_run(self, *, executor: Any | None = None) -> dict[str, Any]:
        """Exercise the production orchestration path with deterministic fakes."""
        class Result:
            policy_satisfied = True
        entries = []
        for index, command_id in enumerate(COMMAND_ORDER):
            layer = {"deterministic.produce": "deterministic", "real_api.round_trip": "real_api", "websocket.transport": "websocket_e2e", "websocket.log_analysis": "server_regression", "websocket.correlation": "websocket_e2e", "candidate_soak.produce": "candidate_soak", "candidate_soak.replay": "candidate_soak", "physical.capture_and_audit": "physical"}[command_id]
            for output in next(item for item in self.command_specs() if item.command_id == command_id).outputs:
                output.parent.mkdir(parents=True, exist_ok=True)
                if not output.exists():
                    if output == self.root / "server-regression/report.json":
                        output.write_text(json.dumps({"evidenceScope": {"journeyId": "synthetic-journey"}, "logWindow": {"windowId": "synthetic-window", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"}}) + "\n")
                    else:
                        output.write_bytes(b"{}\n")
            spec = next(item for item in self.command_specs() if item.command_id == command_id)
            self.execute_layer(command_id, executor=executor or (lambda *args, **kwargs: Result()))
            entries.append({"argv": list(spec.argv), "candidateIdentity": dict(self.identity), "commandId": command_id, "cwd": ".", "endedAtUtc": f"2026-01-01T00:00:{index:02d}.500000Z", "environmentSources": [], "exitCode": 0, "inputs": [], "outputs": [], "schemaVersion": "google-live-command-provenance.v1", "secretSources": [f"<env:{name}>" for name in spec.secret_env], "specSha256": hashlib.sha256(command_id.encode()).hexdigest(), "startedAtUtc": f"2026-01-01T00:00:{index:02d}.000000Z", "stdinSource": None if spec.stdin_source is None else f"<stdin:{spec.stdin_source}>", "terminalPolicy": {"classification": "expected_exit", "cleanupGraceSec": 2.0, "expectedExitCodes": [0], "satisfied": True, "timeoutSec": 300.0}})
        from scripts.google_live_command_runner import render_provenance
        _atomic(self.root / "commands.jsonl", render_provenance(entries))
        self.finalize(release_gate_fn=lambda identity, paths, manifest, output: {"status": "PASS", "candidateIdentity": identity})
        return {"status": "PASS", "runId": self.run_id}

    def _check_layer(self, layer: str) -> None:
        if layer not in LAYERS:
            raise ValueError("unknown evidence layer")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google Live unified evidence runner")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("evidence_root", type=Path)
    init.add_argument("--run-id", required=True)
    init.add_argument("--identity-json", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("run_root", type=Path)
    cli_commands = {
        "deterministic": "deterministic.produce",
        "real-api": "real_api.round_trip",
        "websocket-transport": "websocket.transport",
        "websocket-log-analysis": "websocket.log_analysis",
        "websocket-correlation": "websocket.correlation",
        "candidate-soak-produce": "candidate_soak.produce",
        "candidate-soak-replay": "candidate_soak.replay",
    }
    for name in cli_commands:
        command = sub.add_parser(name)
        command.add_argument("run_root", type=Path)
        command.add_argument("--operator-config", type=Path, required=True)
    physical = sub.add_parser("physical")
    physical.add_argument("run_root", type=Path)
    physical.add_argument("--operator-confirmed", action="store_true")
    physical.add_argument("--transcript-plan-stdin", action="store_true")
    physical.add_argument("--operator-config", type=Path, required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("run_root", type=Path)
    sub.add_parser("synthetic-dry-run").add_argument("evidence_root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "init":
        identity = json.loads(args.identity_json.read_text(encoding="utf-8"))
        runner = EvidenceRunner.initialize(args.evidence_root, run_id=args.run_id, identity=identity)
        print(json.dumps(runner._state, sort_keys=True))
        return 0
    if args.command == "status":
        print(Path(args.run_root, "run-state.json").read_text(encoding="utf-8"), end="")
        return 0
    if args.command == "finalize":
        EvidenceRunner.open(args.run_root).finalize()
        return 0
    if args.command == "physical":
        runner = EvidenceRunner.open(args.run_root)
        runner.operator_config = json.loads(args.operator_config.read_text(encoding="utf-8"))
        runner.require_physical_confirmation(operator_confirmed=args.operator_confirmed, transcript_plan_stdin=args.transcript_plan_stdin)
        runner.execute_layer("physical.capture_and_audit", stdin_bytes=__import__("sys").stdin.buffer.read())
        return 0
    if args.command in cli_commands:
        runner = EvidenceRunner.open(args.run_root)
        runner.operator_config = json.loads(args.operator_config.read_text(encoding="utf-8"))
        runner.execute_layer(cli_commands[args.command], stdin_bytes=__import__("sys").stdin.buffer.read() if args.command == "candidate-soak-produce" else None)
        return 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    from scripts.google_live_trusted_git import git_output, trusted_git_session
    repository_root = Path(__file__).resolve().parents[3]
    with trusted_git_session():
        git_sha = git_output(repository_root, "rev-parse", "HEAD").decode().strip()
    identity = {"gitSha": git_sha, "imageDigest": "sha256:" + "0" * 64, "firmwareIdentity": "synthetic", "configFingerprint": "sha256:" + "0" * 64, "fixtureSha256": "0" * 64}
    runner = EvidenceRunner.initialize(args.evidence_root, run_id=run_id, identity=identity, verify_repository=False)
    for relative in ("fixture.wav", "server.log", "baseline/report.json", "lesson-manifest.json"):
        path = runner.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}\n")
    runner.operator_config = {"fixture": str(runner.root / "fixture.wav"), "websocket_url": "ws://127.0.0.1/disabled", "device_id": "synthetic-device", "client_id": "synthetic-client", "journey_id": "synthetic-journey", "server_log": str(runner.root / "server.log"), "config_json": "{}", "expected_candidate_json": json.dumps(identity, sort_keys=True), "evidence_control_url": "http://127.0.0.1/disabled", "baseline_report": str(runner.root / "baseline/report.json"), "lesson_manifest": str(runner.root / "lesson-manifest.json"), "base_url": "http://127.0.0.1/disabled"}
    result = runner.synthetic_dry_run()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
