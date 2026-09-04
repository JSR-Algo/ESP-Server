"""Fail-closed orchestration and state for the unified Google Live evidence run."""

from __future__ import annotations

import json
import os
import re
import tempfile
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAYERS = ("deterministic", "server_regression", "real_api", "websocket_e2e", "physical", "candidate_soak")
DEPENDENCIES = {
    "real_api": ("deterministic",),
    "websocket_e2e": ("deterministic", "real_api"),
    "candidate_soak": ("deterministic", "real_api", "websocket_e2e"),
    "physical": ("deterministic", "real_api", "websocket_e2e", "candidate_soak"),
    "server_regression": ("websocket_e2e",),
}
SAFE_FAILURE_CODES = {"AUTH", "CONFIG", "QUOTA", "PROTOCOL", "TIMEOUT", "NETWORK", "PROVIDER", "CLEANUP", "RESOURCE", "IDENTITY", "PRIVACY", "EVIDENCE_INTEGRITY", "CANCELLED"}
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


class EvidenceRunner:
    def __init__(self, root: Path, run_id: str, identity: dict[str, str], state: dict[str, Any]):
        self.root = root
        self.run_id = run_id
        self.identity = dict(identity)
        self.state_path = root / "run-state.json"
        self._state = state

    @classmethod
    def initialize(cls, evidence_root: Path, *, run_id: str, identity: dict[str, str]) -> "EvidenceRunner":
        evidence_root = Path(evidence_root)
        if evidence_root.is_symlink() or not evidence_root.is_absolute():
            raise ValueError("evidence root must be an absolute non-alias path")
        if RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run ID is invalid")
        required = {"gitSha", "imageDigest", "firmwareIdentity", "configFingerprint", "fixtureSha256"}
        if set(identity) != required or any(not isinstance(v, str) or not v for v in identity.values()):
            raise ValueError("candidate identity is invalid")
        root = evidence_root / run_id
        if root.exists() or root.is_symlink():
            raise FileExistsError(root)
        root.mkdir(mode=0o700, parents=True)
        for directory in ("deterministic", "server-regression", "real-api", "websocket-e2e", "physical", "candidate-soak"):
            (root / directory).mkdir(mode=0o700)
        state = {"schemaVersion": "google-live-evidence-run.v1", "runId": run_id, "candidateIdentity": dict(identity), "createdAt": _now(), "layers": {name: {"state": "PENDING", "startedAt": None, "endedAt": None, "firstFailure": None, "artifactSha256": {}} for name in LAYERS}}
        _atomic(root / "run-state.json", (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode())
        return cls(root, run_id, identity, state)

    @classmethod
    def open(cls, root: Path, *, resume: bool = False) -> "EvidenceRunner":
        if resume:
            raise EvidenceStateError("failed evidence runs cannot be resumed; create a new RUN_ID")
        state = json.loads(Path(root, "run-state.json").read_text(encoding="utf-8"))
        return cls(Path(root), state["runId"], state["candidateIdentity"], state)

    def _save(self) -> None:
        _atomic(self.state_path, (json.dumps(self._state, sort_keys=True, separators=(",", ":")) + "\n").encode())

    def state(self, layer: str) -> str:
        self._check_layer(layer)
        return self._state["layers"][layer]["state"]

    def can_start(self, layer: str) -> bool:
        self._check_layer(layer)
        record = self._state["layers"][layer]
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
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    identity = {"gitSha": "0" * 40, "imageDigest": "sha256:" + "0" * 64, "firmwareIdentity": "synthetic", "configFingerprint": "sha256:" + "0" * 64, "fixtureSha256": "0" * 64}
    runner = EvidenceRunner.initialize(args.evidence_root, run_id=run_id, identity=identity)
    for layer in ("deterministic", "real_api", "websocket_e2e", "candidate_soak", "physical"):
        runner.start_layer(layer)
        runner.finish_layer(layer, "PASS")
    print(json.dumps(runner._state, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
