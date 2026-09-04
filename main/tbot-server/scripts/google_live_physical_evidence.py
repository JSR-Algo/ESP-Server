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
from pathlib import Path
from typing import Any, Callable

NORMALIZATION_VERSION = "google-live-transcript-nfkc-casefold.v1"


class PhysicalEvidenceError(RuntimeError):
    pass


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

    def capture(self, *, journey_id: str, enrollment: dict[str, Any], candidate_identity: dict[str, Any] | None = None, timeout_sec: float = 300.0, poll_interval_sec: float = 1.0) -> dict[str, Any]:
        path = f"/internal/devices/{self.device_id}/google-live-evidence/{journey_id}"
        completed = False
        try:
            self._request("POST", f"/internal/devices/{self.device_id}/google-live-evidence", enrollment)
            if candidate_identity is not None:
                self._request("PUT", path + "/candidate-identity", {"candidateIdentity": candidate_identity})
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                snapshot = self._request("GET", path)
                if snapshot.get("readyToFinalize"):
                    validate_ready_snapshot(snapshot, journey_id=journey_id)
                    result = self._request("POST", path + "/finalize")
                    if result.get("status") != "PASS":
                        raise PhysicalEvidenceError("physical evidence finalization failed")
                    completed = True
                    return result
                time.sleep(poll_interval_sec)
            raise PhysicalEvidenceError("physical evidence timed out")
        finally:
            if not completed:
                try:
                    self._request("DELETE", path)
                except Exception:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture privacy-safe physical Google Live evidence")
    parser.add_argument("--candidate-soak-report", type=Path, required=True)
    parser.add_argument("--server-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate-git-sha", required=True)
    parser.add_argument("--candidate-image-digest", required=True)
    parser.add_argument("--firmware-identity", required=True)
    parser.add_argument("--config-fingerprint", required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--device-id")
    parser.add_argument("--base-url")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--transcript-plan-stdin", action="store_true")
    args = parser.parse_args(argv)
    if not args.operator_confirmed or not args.transcript_plan_stdin or not args.base_url or not args.device_id:
        parser.error("physical evidence requires --operator-confirmed, --transcript-plan-stdin, --base-url, and --device-id")
    try:
        plan = json.load(__import__("sys").stdin)
        key = bytearray(__import__("secrets").token_bytes(32))
        enrollment = build_enrollment(device_id=args.device_id, client_id=str(plan["clientId"]), journey_id=str(plan["journeyId"]), transcript_plan=list(plan["transcriptPlan"]), hmac_key=key, ttl_sec=int(plan.get("ttlSec", 300)))
        identity = {"gitSha": args.candidate_git_sha, "imageDigest": args.candidate_image_digest, "firmwareIdentity": args.firmware_identity, "configFingerprint": args.config_fingerprint, "fixtureSha256": args.fixture_sha256}
        result = PhysicalEvidenceClient(args.base_url, args.device_id, __import__("os").environ.get("TBOT_DEVICE_MINT_SECRET", "")).capture(journey_id=str(plan["journeyId"]), enrollment=enrollment, candidate_identity=identity)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        print("READY journey_id=" + str(plan["journeyId"]))
        return 0
    except Exception:
        return 1
    finally:
        if "key" in locals():
            for index in range(len(key)):
                key[index] = 0


if __name__ == "__main__":
    raise SystemExit(main())
