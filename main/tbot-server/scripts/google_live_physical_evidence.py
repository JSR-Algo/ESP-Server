"""Authenticated physical evidence lifecycle; never controls robot hardware."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.request
import argparse
import base64
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

NORMALIZATION_VERSION = "google-live-transcript-nfkc-casefold.v1"


class PhysicalEvidenceError(RuntimeError):
    pass


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _normalize(value: str) -> str:
    import unicodedata
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def build_enrollment(*, device_id: str, client_id: str, journey_id: str, transcript_plan: list[dict[str, Any]], hmac_key: bytearray, ttl_sec: int) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", journey_id):
        raise ValueError("journey ID is invalid")
    plan = []
    for item in transcript_plan:
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError("transcript plan must be protected text input")
        mac = hmac.new(bytes(hmac_key), _normalize(text).encode(), hashlib.sha256).hexdigest()
        plan.append({"slot": item["slot"], "phase": item["phase"], "expectedMac": mac})
    return {"clientId": client_id, "journeyId": journey_id, "ttlSec": ttl_sec, "normalizationVersion": NORMALIZATION_VERSION, "hmacKeyBase64": base64.b64encode(bytes(hmac_key)).decode("ascii"), "transcriptPlan": plan}


def validate_ready_snapshot(snapshot: dict[str, Any], *, journey_id: str) -> None:
    if snapshot.get("journeyId") != journey_id or snapshot.get("status") != "ACTIVE":
        raise PhysicalEvidenceError("evidence journey is not active")
    if snapshot.get("transcriptExpectedCount") != 11 or snapshot.get("transcriptObservedCount") != 11 or snapshot.get("transcriptMatchedCount") != 11 or snapshot.get("transcriptMismatchCount") != 0 or snapshot.get("transcriptMissingCount") != 0:
        raise PhysicalEvidenceError("transcript proof is incomplete")
    if snapshot.get("transcriptMatchedSlots") != list(range(1, 12)) or snapshot.get("transcriptMatchedPhases") != ["interrupt"] * 10 + ["post_lesson"]:
        raise PhysicalEvidenceError("transcript proof ordering is invalid")
    if not snapshot.get("transcriptOrderingProof") or not snapshot.get("postInterruptVerdict") or not snapshot.get("postLessonVerdict") or not snapshot.get("readyToFinalize"):
        raise PhysicalEvidenceError("physical evidence is not ready to finalize")


class PhysicalEvidenceClient:
    def __init__(self, base_url: str, device_id: str, mint_secret: str, *, request: Callable[..., dict[str, Any]] | None = None):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id
        self.mint_secret = mint_secret
        self._request = request or self._http_request

    def _http_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers={"X-Mint-Secret": self.mint_secret, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    def capture(self, *, journey_id: str, enrollment: dict[str, Any], candidate_identity: dict[str, Any] | None = None, timeout_sec: float = 300.0, poll_interval_sec: float = 1.0, on_ready: Callable[[str], None] | None = None) -> dict[str, Any]:
        path = f"/internal/devices/{self.device_id}/google-live-evidence/{journey_id}"
        completed = False
        try:
            self._request("POST", f"/internal/devices/{self.device_id}/google-live-evidence", enrollment)
            if candidate_identity is not None:
                self._request("PUT", path + "/candidate-identity", {"candidateIdentity": candidate_identity})
            if on_ready is not None:
                on_ready(journey_id)
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                snapshot = self._request("GET", path)
                if snapshot.get("readyToFinalize"):
                    validate_ready_snapshot(snapshot, journey_id=journey_id)
                    result = self._request("POST", path + "/finalize")
                    if result.get("status") != "PASS":
                        raise PhysicalEvidenceError("physical evidence finalization failed")
                    while time.monotonic() < deadline:
                        terminal = self._request("GET", path)
                        if terminal.get("status") == "PASS":
                            if terminal.get("journeyId") != journey_id:
                                raise PhysicalEvidenceError("physical evidence terminal journey mismatch")
                            if candidate_identity is not None and terminal.get("candidateIdentity") not in (None, candidate_identity):
                                raise PhysicalEvidenceError("physical evidence terminal candidate mismatch")
                            completed = True
                            return terminal
                        if terminal.get("status") in {"FAIL", "EXPIRED"}:
                            raise PhysicalEvidenceError("physical evidence terminal status failed")
                        time.sleep(poll_interval_sec)
                    raise PhysicalEvidenceError("physical evidence terminal status timed out")
                time.sleep(poll_interval_sec)
            raise PhysicalEvidenceError("physical evidence timed out")
        finally:
            if not completed:
                try:
                    self._request("DELETE", path)
                except Exception:
                    pass


def compose_physical_report(*, raw_server_log: Path, journey_id: str, candidate_soak_report: dict[str, Any], candidate_identity: dict[str, Any], device_id: str, client_id: str, output: Path, terminal_snapshot: dict[str, Any] | None = None, bounded_log_output: Path | None = None, server_report_output: Path | None = None, audit_fn: Callable[..., dict[str, Any]] | None = None, selector_fn: Callable[[list[str], str], list[str]] | None = None, analyzer_fn: Callable[[Path], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the existing production physical validator over one bounded log."""
    if not raw_server_log.is_file() or raw_server_log.is_symlink():
        raise PhysicalEvidenceError("bounded server log is unavailable")
    if not isinstance(journey_id, str) or not journey_id:
        raise PhysicalEvidenceError("bounded server journey is unavailable")
    if terminal_snapshot is not None:
        if terminal_snapshot.get("journeyId") != journey_id or terminal_snapshot.get("status") != "PASS":
            raise PhysicalEvidenceError("terminal snapshot is not authoritative")
        if terminal_snapshot.get("candidateIdentity") not in (None, candidate_identity):
            raise PhysicalEvidenceError("terminal snapshot candidate identity mismatch")
    if selector_fn is None:
        from scripts.analyze_google_live_log import _bounded_server_window
        selector_fn = _bounded_server_window
    selected = selector_fn(
        raw_server_log.read_text(encoding="utf-8", errors="replace").splitlines(),
        journey_id,
    )
    bounded_log = "\n".join(selected) + "\n"
    bounded_log_output = bounded_log_output or output.with_name("server-window.log")
    _atomic_write(bounded_log_output, bounded_log.encode())
    if analyzer_fn is None:
        from scripts.analyze_google_live_log import analyze_reliability_window
        analyzer_fn = analyze_reliability_window
    server_report = dict(analyzer_fn(bounded_log_output))
    scope = server_report.get("evidenceScope", {})
    if server_report.get("status") != "PASS" or scope.get("journeyId") != journey_id:
        raise PhysicalEvidenceError("physical server evidence does not match terminal journey")
    if server_report.get("candidateIdentity") is None:
        server_report["candidateIdentity"] = dict(candidate_identity)
    elif server_report.get("candidateIdentity") != candidate_identity:
        raise PhysicalEvidenceError("physical server evidence candidate mismatch")
    if server_report_output is not None:
        _atomic_write(server_report_output, (json.dumps(server_report, sort_keys=True, separators=(",", ":")) + "\n").encode())
    if audit_fn is None:
        from scripts.physical_smoke_audit import audit_log
        audit_fn = audit_log
    report = audit_fn(
        bounded_log,
        device_id=device_id,
        client_id=client_id,
        min_interrupts=10,
        min_audio_interrupts=0,
        require_lesson=True,
        require_aec_live_vad_forward=True,
        min_aec_live_vad_forward=10,
        min_aec_interruption_chains=10,
        require_live_server_interruption=True,
        min_live_server_interruptions=10,
        min_interrupt_tts_stops=10,
        min_interrupt_stop_chains=10,
        min_interrupt_user_chains=10,
        min_interrupt_relisten_chains=10,
        min_post_interrupt_user_transcripts=1,
        min_realtime_tts_stops=1,
        min_output_relisten_chains=1,
        max_first_audio_p50_ms=1200.0,
        max_first_audio_p95_ms=1800.0,
        max_interrupt_stop_latency_ms=250.0,
        max_physical_bargein_p95_ms=500.0,
        max_server_output_gap_ms=250.0,
        min_first_audio_samples=11,
        min_interrupt_stop_samples=10,
        min_physical_bargein_samples=10,
        min_server_output_gap_samples=10,
        candidate_identity=candidate_identity,
        reliability_report=server_report,
        candidate_soak_report=candidate_soak_report,
        require_receive_loop_balance=True,
        require_post_lesson_response=True,
        require_lesson_live_text=True,
    )
    report = dict(report)
    report["logEvidence"] = server_report
    report["candidateSoakEvidence"] = candidate_soak_report
    report["candidateIdentity"] = dict(candidate_identity)
    if terminal_snapshot is not None:
        report["terminalSnapshot"] = dict(terminal_snapshot)
    _atomic_write(output, (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture privacy-safe physical Google Live evidence")
    parser.add_argument("--candidate-soak-report", type=Path, required=True)
    parser.add_argument("--server-report", type=Path)
    parser.add_argument("--server-report-output", type=Path)
    parser.add_argument("--terminal-report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate-git-sha", required=True)
    parser.add_argument("--candidate-image-digest", required=True)
    parser.add_argument("--firmware-identity", required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--device-id")
    parser.add_argument("--client-id")
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--transcript-plan-stdin", action="store_true")
    args = parser.parse_args(argv)
    if not args.operator_confirmed or not args.transcript_plan_stdin or not args.base_url or not args.device_id or not args.client_id or args.server_log is None:
        parser.error("physical evidence requires --operator-confirmed, --transcript-plan-stdin, --base-url, and --device-id")
    try:
        plan = json.load(__import__("sys").stdin)
        candidate_report = json.loads(args.candidate_soak_report.read_text(encoding="utf-8"))
        config_fingerprint = candidate_report.get("candidateIdentity", {}).get("configFingerprint")
        if not isinstance(config_fingerprint, str):
            raise PhysicalEvidenceError("candidate configuration identity is unavailable")
        key = bytearray(__import__("secrets").token_bytes(32))
        enrollment = build_enrollment(device_id=args.device_id, client_id=str(plan["clientId"]), journey_id=str(plan["journeyId"]), transcript_plan=list(plan["transcriptPlan"]), hmac_key=key, ttl_sec=int(plan.get("ttlSec", 300)))
        identity = {"gitSha": args.candidate_git_sha, "imageDigest": args.candidate_image_digest, "firmwareIdentity": args.firmware_identity, "configFingerprint": config_fingerprint, "fixtureSha256": args.fixture_sha256}
        result = PhysicalEvidenceClient(args.base_url, args.device_id, __import__("os").environ.get("TBOT_DEVICE_MINT_SECRET", "")).capture(journey_id=str(plan["journeyId"]), enrollment=enrollment, candidate_identity=identity, on_ready=lambda journey: print("READY journey_id=" + journey, flush=True))
        if args.terminal_report is not None:
            _atomic_write(args.terminal_report, (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode())
        compose_physical_report(raw_server_log=args.server_log, journey_id=str(result.get("journeyId") or plan["journeyId"]), terminal_snapshot=result, candidate_soak_report=candidate_report, candidate_identity=identity, device_id=args.device_id, client_id=args.client_id, output=args.report, bounded_log_output=args.report.with_name("server-window.log"), server_report_output=args.server_report_output or args.report.with_name("server-report.json"))
        return 0
    except Exception:
        return 1
    finally:
        if "key" in locals():
            for index in range(len(key)):
                key[index] = 0


if __name__ == "__main__":
    raise SystemExit(main())
