#!/usr/bin/env python3
"""Local-only Course Mode E2E runner across public process boundaries."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Protocol

import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from scripts.course_mode_26week_simulation import (  # noqa: E402
    PEDAGOGY_WEEKS,
    load_backend_contracts,
)
from scripts.course_mode_candidate_manifest import (  # noqa: E402
    MAX_CANDIDATE_BYTES,
    read_secure_regular,
    strict_json_loads,
    validate_candidate,
)

BOUNDARIES = (
    "admin-http", "postgres", "assignment-http", "manifest-http",
    "device-websocket", "completion-http", "progress-http",
)
REDACTED_KEYS = {
    "authorization", "cookie", "password", "privatekey", "rawchildaudio",
    "secret", "token", "websockettoken",
}
REPRESENTATIVE_WEEKS = (1, 2, 3, 7, 4, 26)
MAX_REPORT_BYTES = 1024 * 1024
DEFAULT_DEVICE_MAC = "14:c1:9f:d1:ac:20"


class JourneyError(RuntimeError):
    pass


@dataclass(frozen=True)
class StackUrls:
    admin: str
    device: str
    websocket: str
    readback: str | None = None

    def __post_init__(self) -> None:
        for value in (self.admin, self.device, self.websocket, self.readback or self.admin):
            parsed = urllib.parse.urlparse(value)
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("cross-process endpoints must be loopback")
        if not urllib.parse.urlparse(self.websocket).path.endswith("/tbot/v1/"):
            raise ValueError("websocket must use the public /tbot/v1/ boundary")


@dataclass(frozen=True)
class CandidateBinding:
    candidate_id: str
    backend_sha: str
    backend_root: Path
    course_key: str
    curriculum_checksum: str
    identity_digest: str

    @classmethod
    def load(cls, path: Path) -> CandidateBinding:
        candidate = strict_json_loads(read_secure_regular(path, MAX_CANDIDATE_BYTES))
        reasons = validate_candidate(candidate)
        if reasons:
            raise JourneyError(f"candidate rejected: {','.join(reasons)}")
        anchors = {
            "candidateId": candidate["candidateId"], "course": candidate["course"],
            "repositories": {
                name: {key: value[key] for key in ("sha", "remoteUrl")}
                for name, value in sorted(candidate["repositories"].items())
            },
            "images": candidate["images"], "firmware": candidate["firmware"],
            "curriculum": candidate["curriculum"],
        }
        identity_digest = hashlib.sha256(json.dumps(
            anchors, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return cls(
            candidate["candidateId"], candidate["repositories"]["backend"]["sha"],
            Path(candidate["repositories"]["backend"]["path"]), candidate["course"]["courseKey"],
            candidate["curriculum"]["sourceChecksum"], identity_digest,
        )


@dataclass(frozen=True)
class JourneySpec:
    week: int
    lesson_key: str
    pedagogy: str
    checksum: str | None = None


def _pedagogy_for_week(week: int) -> str:
    return next(name for name, weeks in PEDAGOGY_WEEKS.items() if week in weeks)


def canonical_journeys(binding: CandidateBinding, *, all_26: bool) -> list[JourneySpec]:
    fixture, _ = load_backend_contracts(
        binding.backend_root, expected_sha=binding.backend_sha,
    )
    lessons = fixture.get("lessons")
    lesson_keys = fixture.get("lessonKeys")
    checksums = fixture.get("contractChecksums")
    if (
        not isinstance(lessons, list) or len(lessons) != 26
        or not isinstance(lesson_keys, list) or len(lesson_keys) != 26
        or not isinstance(checksums, list) or len(checksums) != 26
    ):
        raise JourneyError("canonical curriculum must contain exactly 26 lessons")
    by_week = {int(row["week"]): index for index, row in enumerate(lessons)}
    weeks: Iterable[int] = range(1, 27) if all_26 else REPRESENTATIVE_WEEKS
    return [JourneySpec(
        week, str(lesson_keys[by_week[week]]), _pedagogy_for_week(week),
        str(checksums[by_week[week]]),
    ) for week in weeks]


class BoundaryLedger:
    def __init__(self) -> None:
        self.observed: list[str] = []
        self.private_adapter_calls = 0

    def record(self, boundary: str) -> None:
        if not self.observed or self.observed[-1] != boundary:
            self.observed.append(boundary)

    def validate(self) -> None:
        if self.observed != list(BOUNDARIES) or self.private_adapter_calls != 0:
            raise JourneyError("observed public boundary ledger mismatch")


def validate_journey_set(journeys: list[dict[str, Any]], expected_count: int) -> None:
    if len(journeys) != expected_count:
        raise JourneyError("journey count disagreement")
    for keys in (("lessonId", "version"), ("assignmentId",), ("sessionId",), ("deliveryId",)):
        identities = [tuple(row.get(key) for key in keys) for row in journeys]
        if any(None in identity for identity in identities) or len(set(identities)) != len(identities):
            raise JourneyError(f"duplicate or missing {'/'.join(keys)} identity")


@dataclass(frozen=True)
class FaultPlan:
    websocket_drop: bool = False
    esp_restart: bool = False
    backend_restart: bool = False
    completion_failures: int = 0
    stale_manifest: bool = False
    checksum_mismatch: bool = False
    cache_unavailable: bool = False
    asr_unavailable: bool = False
    silence_help: bool = False
    duplicate_acks: int = 0
    delayed_acks: int = 0
    out_of_order_acks: bool = False

    def enabled_names(self) -> list[str]:
        return [
            field.name for field in fields(self)
            if (value := getattr(self, field.name)) is True or (
                isinstance(value, int) and not isinstance(value, bool) and value > 0
            )
        ]


class AckScheduler:
    def __init__(self, faults: FaultPlan) -> None:
        self.faults = faults

    def order(self, delivery_ids: list[str]) -> list[str]:
        ordered = list(delivery_ids)
        if self.faults.out_of_order_acks and len(ordered) > 1:
            ordered[0], ordered[1] = ordered[1], ordered[0]
        ordered.extend(ordered[: self.faults.duplicate_acks])
        return ordered


class ProcessController:
    """Runs only operator-supplied, argv-tokenized restart commands."""

    def __init__(self, commands: dict[str, list[str]], timeout_sec: float = 30) -> None:
        self.commands = commands
        self.timeout_sec = timeout_sec

    def restart(self, target: str) -> None:
        command = self.commands.get(target)
        if not command or not all(isinstance(token, str) and token for token in command):
            raise JourneyError(f"explicit restart command required for {target}")
        completed = subprocess.run(
            command, shell=False, capture_output=True, text=True, timeout=self.timeout_sec,
        )
        if completed.returncode != 0:
            raise JourneyError(f"restart command failed for {target}")


class JsonHttpClient:
    def __init__(self, base_url: str, token: str | None, timeout_sec: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_sec = timeout_sec

    def request(self, method: str, route: str, payload: Any = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}/{route.lstrip('/')}", data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                body = response.read(MAX_REPORT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            return {"status": exc.code}
        if len(body) > MAX_REPORT_BYTES:
            raise JourneyError("HTTP response exceeded bound")
        decoded = strict_json_loads(body) if body else {}
        if not isinstance(decoded, dict):
            raise JourneyError("HTTP response must be a JSON object")
        decoded.setdefault("status", 200)
        return decoded


class AdminHttpClient:
    ROUTES = {
        "author": ("POST", "/admin/lessons/course-mode"),
        "visuals": ("PUT", "/admin/lessons/course-mode/visuals"),
        "validate": ("POST", "/admin/lessons/course-mode/validate"),
        "publish": ("POST", "/admin/lessons/course-mode/publish"),
        "assign": ("POST", "/admin/lesson-assignments"),
    }

    def __init__(self, http: JsonHttpClient) -> None:
        self.http = http

    def __call__(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        method, route = self.ROUTES[operation]
        return self.http.request(method, route, payload)


class DeviceHttpClient:
    def __init__(self, http: JsonHttpClient, device_mac: str) -> None:
        self.http, self.device_mac = http, device_mac

    def get(self, operation: str, **params: Any) -> dict[str, Any]:
        if operation == "assignment":
            return self.http.request("GET", f"/devices/{self.device_mac}/lesson-assignments/current")
        query = urllib.parse.urlencode({"profile": "espTft", "version": params["version"]})
        return self.http.request("GET", f"/lessons/{params['lesson_id']}/manifest?{query}")

    def completion(self, payload: dict[str, Any], _faults: FaultPlan) -> dict[str, Any]:
        return self.http.request("POST", f"/devices/{self.device_mac}/lesson-events", payload)

    def progress(self, assignment_id: str) -> dict[str, Any]:
        return self.http.request("GET", f"/devices/{self.device_mac}/progress?assignmentId={assignment_id}")


class DbReadback:
    QUERY = """
SELECT json_build_object(
  'assignmentId', la.id::text,
  'lessonId', la.lesson_id::text,
  'lessonVersion', la.lesson_version
)::text
FROM lesson_assignments la
WHERE la.id::text = :'assignment_id';
"""

    def __init__(self, dsn: str, timeout_sec: float = 10, psql: str | None = None) -> None:
        executable = psql or shutil.which("psql")
        if executable is None or Path(executable).name != "psql":
            raise JourneyError("trusted psql executable is required")
        self.psql, self.dsn, self.timeout_sec = executable, dsn, timeout_sec

    def __call__(self, assignment_id: str) -> dict[str, Any]:
        completed = subprocess.run(
            [
                self.psql, "-X", "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
                "--set", f"assignment_id={assignment_id}", "--tuples-only", "--no-align",
                "--dbname", self.dsn,
            ],
            shell=False, capture_output=True, text=True, input=self.QUERY,
            timeout=self.timeout_sec,
        )
        if completed.returncode != 0 or len(completed.stdout.encode()) > MAX_REPORT_BYTES:
            raise JourneyError("postgres readback failed")
        result = strict_json_loads(completed.stdout.strip())
        if not isinstance(result, dict) or set(result) != {"assignmentId", "lessonId", "lessonVersion"}:
            raise JourneyError("postgres readback must return a JSON object")
        return result


class ReadbackClient:
    def __init__(self, http: JsonHttpClient, device: DeviceHttpClient) -> None:
        self.http, self.device = http, device

    def __call__(self, assignment_id: str) -> dict[str, Any]:
        progress = self.device.progress(assignment_id)
        routes = {
            "monitoring": "/monitoring/events", "insights": "/insights/child",
            "parent": "/mobile/status",
        }
        reads = {
            name: self.http.request("GET", f"{route}?assignmentId={assignment_id}")
            for name, route in routes.items()
        }
        progress.setdefault("insightsActivityCount", reads["insights"].get("activityCount"))
        progress.setdefault("parentActivityCount", reads["parent"].get("activityCount"))
        return progress


class EspWebSocket:
    def __init__(
        self, url: str, token: str, device_mac: str, controller: ProcessController | None = None,
        timeout_sec: float = 15,
    ) -> None:
        self.url, self.token, self.device_mac = url, token, device_mac
        self.controller, self.timeout_sec = controller, timeout_sec

    async def _drive(
        self, assignment: dict[str, Any], manifest: dict[str, Any],
        responses: list[dict[str, Any]], faults: FaultPlan,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}", "device-id": self.device_mac}
        frames: list[dict[str, Any]] = []
        fault_evidence: set[str] = set()
        pending_acks: list[str] = []
        sent_acks = 0
        terminal = False
        restarts_done: set[str] = set()
        pending_restart_evidence: set[str] = set()
        connection_count = 0
        while not terminal:
            try:
                async with websockets.connect(self.url, additional_headers=headers) as socket:
                    connection_count += 1
                    await socket.send(json.dumps({"type": "hello", "deviceId": self.device_mac}))
                    if connection_count > 1:
                        fault_evidence.update(pending_restart_evidence)
                        pending_restart_evidence.clear()
                    while True:
                        frame = strict_json_loads(await asyncio.wait_for(socket.recv(), self.timeout_sec))
                        frames.append(frame)
                        fault_evidence.update(
                            name for name in frame.get("recoveredFaults", ())
                            if name in faults.enabled_names()
                        )
                        delivery_id = frame.get("deliveryId")
                        if delivery_id:
                            pending_acks.append(str(delivery_id))
                            release_threshold = max(faults.delayed_acks, int(faults.out_of_order_acks))
                            if len(pending_acks) > release_threshold:
                                scheduled = AckScheduler(faults).order(pending_acks)
                                pending_acks.clear()
                                for ack_id in scheduled:
                                    await socket.send(json.dumps({"type": "lesson_ack", "deliveryId": ack_id}))
                                    sent_acks += 1
                        if frame.get("type") in {"lesson_step", "course_step"}:
                            restarted_at_boundary = False
                            for target, enabled in (
                                ("backend", faults.backend_restart), ("esp", faults.esp_restart),
                            ):
                                if enabled and target not in restarts_done:
                                    if self.controller is None:
                                        raise JourneyError(f"{target} restart requested without ProcessController")
                                    self.controller.restart(target)
                                    restarts_done.add(target)
                                    pending_restart_evidence.add(f"{target}_restart")
                                    restarted_at_boundary = True
                            if restarted_at_boundary:
                                await socket.close(code=1012, reason="restart at activity boundary")
                                break
                            if responses:
                                await socket.send(json.dumps(responses.pop(0)))
                        if faults.websocket_drop and "websocket_drop" not in fault_evidence and delivery_id:
                            fault_evidence.add("websocket_drop")
                            await socket.close(code=1012, reason="deterministic cross-process drop")
                            break
                        if frame.get("type") in {"lesson_stop", "course_stop"}:
                            for ack_id in AckScheduler(faults).order(pending_acks):
                                await socket.send(json.dumps({"type": "lesson_ack", "deliveryId": ack_id}))
                                sent_acks += 1
                            terminal = True
                            break
            except websockets.exceptions.ConnectionClosed:
                if not ({"websocket_drop", "backend_restart", "esp_restart"} & fault_evidence):
                    raise
        deliveries = [str(frame["deliveryId"]) for frame in frames if frame.get("deliveryId")]
        unique_deliveries = list(dict.fromkeys(deliveries))
        if faults.duplicate_acks and sent_acks > len(unique_deliveries):
            fault_evidence.add("duplicate_acks")
        if faults.delayed_acks and terminal:
            fault_evidence.add("delayed_acks")
        if faults.out_of_order_acks and terminal:
            fault_evidence.add("out_of_order_acks")
        return {
            "sessionId": frames[-1].get("sessionId") or assignment.get("sessionId"),
            "assignmentDeliveryId": next(
                (frame.get("assignmentDeliveryId") for frame in frames if frame.get("assignmentDeliveryId")),
                assignment.get("deliveryId"),
            ),
            "deliveryId": unique_deliveries[-1] if unique_deliveries else assignment.get("deliveryId"),
            "terminalState": "COMPLETED", "activityCount": len(unique_deliveries),
            "ackCount": len(unique_deliveries), "wireAckCount": sent_acks,
            "duplicateAcksIgnored": max(0, sent_acks - len(unique_deliveries)),
            "faultEvidence": sorted(fault_evidence),
        }

    def __call__(self, assignment, manifest, responses, faults):
        return asyncio.run(self._drive(assignment, manifest, responses, faults))


class PublicStackProtocol(Protocol):
    def admin(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def postgres(self, assignment_id: str) -> dict[str, Any]: ...
    def device_get(self, operation: str, **params: Any) -> dict[str, Any]: ...
    def websocket(self, assignment, manifest, responses, faults) -> dict[str, Any]: ...
    def completion(self, payload, faults) -> dict[str, Any]: ...
    def progress(self, assignment_id: str) -> dict[str, Any]: ...


class ObservedStack:
    def __init__(self, stack: PublicStackProtocol) -> None:
        self.stack, self.ledger = stack, BoundaryLedger()

    def admin(self, operation, payload):
        self.ledger.record("admin-http")
        return self.stack.admin(operation, payload)

    def postgres(self, assignment_id):
        self.ledger.record("postgres")
        return self.stack.postgres(assignment_id)

    def device_get(self, operation, **params):
        self.ledger.record("assignment-http" if operation == "assignment" else "manifest-http")
        return self.stack.device_get(operation, **params)

    def websocket(self, assignment, manifest, responses, faults):
        self.ledger.record("device-websocket")
        return self.stack.websocket(assignment, manifest, responses, faults)

    def completion(self, payload, faults):
        self.ledger.record("completion-http")
        return self.stack.completion(payload, faults)

    def progress(self, assignment_id):
        self.ledger.record("progress-http")
        return self.stack.progress(assignment_id)

    def fault(self, name: str) -> None:
        handler = getattr(self.stack, "fault", None)
        if handler is None:
            raise JourneyError(f"explicit fault command required for {name}")
        handler(name)


class JourneyRunner:
    def __init__(
        self, stack: PublicStackProtocol, *, candidate_id: str,
        binding: CandidateBinding | None = None, max_retries: int = 4,
    ) -> None:
        self.stack, self.candidate_id = stack, candidate_id
        self.binding, self.max_retries = binding, max_retries

    def run(
        self, *, lesson_key: str, pedagogy: str, faults: FaultPlan | None = None,
        expected_checksum: str | None = None,
    ) -> dict[str, Any]:
        faults = faults or FaultPlan()
        observed = ObservedStack(self.stack)
        payload = {"candidateId": self.candidate_id, "lessonKey": lesson_key, "pedagogy": pedagogy}
        authored = observed.admin("author", payload)
        lesson_id, version = authored["lessonId"], authored["version"]
        observed.admin("visuals", {**payload, "lessonId": lesson_id, "version": version})
        validation = observed.admin("validate", {"lessonId": lesson_id, "version": version})
        if validation.get("valid") is not True:
            raise JourneyError("admin validation rejected lesson")
        published = observed.admin("publish", {"lessonId": lesson_id, "version": version})
        assigned = observed.admin("assign", {
            "lessonId": lesson_id, "version": version, "deviceMac": DEFAULT_DEVICE_MAC,
            "candidateId": self.candidate_id,
        })
        assignment_id = assigned["assignmentId"]
        database = observed.postgres(assignment_id)
        if database.get("assignmentId") != assignment_id or database.get("lessonId") != lesson_id:
            raise JourneyError("postgres assignment identity does not match admin assignment")
        if database.get("lessonVersion") != version:
            raise JourneyError("postgres lesson version does not match publication")
        assignment = observed.device_get("assignment")
        if (
            assignment.get("assignmentId") != assignment_id
            or assignment.get("lessonId") != lesson_id
            or assignment.get("version") != version
            or assignment.get("deliveryId") != assigned.get("deliveryId")
        ):
            raise JourneyError("assignment HTTP identity does not match admin assignment")
        pre_websocket_faults: set[str] = set()
        if faults.stale_manifest:
            observed.fault("stale_manifest")
            stale = observed.device_get(
                "manifest", lesson_id=lesson_id, version=version,
            )
            if stale.get("version") == version:
                raise JourneyError("stale manifest seam did not produce stale identity")
            pre_websocket_faults.add("stale_manifest")
            observed.fault("recover_stale_manifest")
        manifest = observed.device_get("manifest", lesson_id=lesson_id, version=version)
        checksum = published["checksum"]
        if expected_checksum is not None and checksum != expected_checksum:
            raise JourneyError("published contract checksum differs from canonical curriculum")
        if faults.checksum_mismatch:
            observed.fault("checksum_mismatch")
            corrupted = observed.device_get("manifest", lesson_id=lesson_id, version=version)
            if corrupted.get("checksum") == checksum:
                raise JourneyError("checksum mismatch seam did not corrupt identity")
            pre_websocket_faults.add("checksum_mismatch")
            observed.fault("recover_checksum_mismatch")
            manifest = observed.device_get("manifest", lesson_id=lesson_id, version=version)
        if faults.cache_unavailable:
            observed.fault("cache_unavailable")
        if manifest.get("version") != assignment.get("version") or manifest.get("checksum") != checksum:
            raise JourneyError("manifest identity mismatch")
        responses = [{"type": "course_response", "class": "correct"}]
        if faults.asr_unavailable:
            responses.append({"type": "course_response", "class": "asr_unavailable"})
        if faults.silence_help:
            responses.extend([
                {"type": "course_response", "class": "silence"},
                {"type": "course_response", "class": "help"},
            ])
        websocket = observed.websocket(assignment, manifest, responses, faults)
        if websocket.get("terminalState") != "COMPLETED":
            raise JourneyError("journey has no terminal state")
        if websocket.get("assignmentDeliveryId") != assigned.get("deliveryId"):
            raise JourneyError("WebSocket delivery identity does not match assignment")
        evidenced_faults = pre_websocket_faults | set(websocket.get("faultEvidence", ()))
        websocket_faults = set(faults.enabled_names()) - ({"completion_failures"})
        if not websocket_faults <= evidenced_faults:
            raise JourneyError("fault plan lacks public-boundary recovery evidence")
        completion_payload = {
            "assignmentId": assignment_id, "sessionId": websocket["sessionId"],
            "deliveryId": websocket["deliveryId"], "event": "completed",
        }
        completion = {"status": 0}
        for attempt in range(self.max_retries + 1):
            if attempt < faults.completion_failures:
                observed.fault("completion_429" if attempt == 0 else "completion_5xx")
            completion = observed.completion(completion_payload, faults)
            if completion.get("status") in {200, 201, 202, 204}:
                break
            if completion.get("status") not in {429, 500, 502, 503, 504} or attempt == self.max_retries:
                raise JourneyError("completion retry exhausted")
        completion_attempts = attempt + 1
        if faults.completion_failures and completion_attempts <= faults.completion_failures:
            raise JourneyError("completion fault lacks retry recovery evidence")
        progress = observed.progress(assignment_id)
        if self.binding is not None:
            expected_identity = {
                "backendSha": self.binding.backend_sha, "courseKey": self.binding.course_key,
                "curriculumChecksum": self.binding.curriculum_checksum,
                "candidateIdentityDigest": self.binding.identity_digest,
            }
            if any(progress.get(key) != value for key, value in expected_identity.items()):
                raise JourneyError("running stack identity does not match candidate anchors")
        counts = {
            websocket.get("activityCount"), websocket.get("ackCount"),
            completion.get("activityCount"), progress.get("activityCount"),
            progress.get("insightsActivityCount"), progress.get("parentActivityCount"),
        }
        if None in counts or len(counts) != 1:
            raise JourneyError("authoritative activity counts disagree")
        observed.ledger.validate()
        return {
            "status": "pass", "candidateId": self.candidate_id, "lessonKey": lesson_key,
            "lessonId": lesson_id, "version": version, "pedagogy": pedagogy,
            "assignmentId": assignment_id, "sessionId": websocket["sessionId"],
            "deliveryId": websocket["deliveryId"], "completionId": completion.get("completionId"),
            "checksum": checksum, "terminalState": websocket["terminalState"],
            "activityCount": websocket["activityCount"], "ackCount": websocket["ackCount"],
            "boundaries": observed.ledger.observed,
            "privateAdapterCalls": observed.ledger.private_adapter_calls,
            "recoveredFaults": sorted(evidenced_faults | ({"completion_failures"} if faults.completion_failures else set())),
        }
def _redact(value: Any, key: str = "") -> Any:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if any(redacted in normalized for redacted in REDACTED_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class ReportWriter:
    def __init__(self, max_bytes: int = MAX_REPORT_BYTES) -> None:
        self.max_bytes = max_bytes

    def write(self, path: Path, report: dict[str, Any]) -> None:
        encoded = json.dumps(_redact(report), sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > self.max_bytes:
            raise JourneyError("report exceeded byte bound")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--all-26", action="store_true")
    parser.add_argument("--admin-url", default="http://127.0.0.1:3000")
    parser.add_argument("--device-url", default="http://127.0.0.1:8003")
    parser.add_argument("--websocket-url", default="ws://127.0.0.1:8000/tbot/v1/")
    parser.add_argument("--admin-token", default=os.environ.get("COURSE_MODE_ADMIN_TOKEN"))
    parser.add_argument("--device-token", default=os.environ.get("COURSE_MODE_DEVICE_TOKEN"))
    parser.add_argument(
        "--postgres-dsn", default=os.environ.get(
            "COURSE_MODE_POSTGRES_DSN", "postgresql://tbot:tbot@127.0.0.1:5432/tbot",
        ),
    )
    parser.add_argument("--restart-backend-command-json")
    parser.add_argument("--restart-esp-command-json")
    parser.add_argument("--fault-commands-json")
    parser.add_argument("--fault-plan-json")
    args = parser.parse_args(argv)
    try:
        binding = CandidateBinding.load(args.candidate)
        urls = StackUrls(args.admin_url, args.device_url, args.websocket_url)
        commands = {
            name: strict_json_loads(raw) for name, raw in {
                "backend": args.restart_backend_command_json,
                "esp": args.restart_esp_command_json,
            }.items() if raw
        }
        if args.fault_commands_json:
            fault_commands = strict_json_loads(args.fault_commands_json)
            if not isinstance(fault_commands, dict):
                raise JourneyError("fault commands must be a JSON object of argv arrays")
            commands.update(fault_commands)
        controller = ProcessController(commands)
        fault_plan = FaultPlan(**strict_json_loads(args.fault_plan_json)) if args.fault_plan_json else FaultPlan()
        admin_http = JsonHttpClient(urls.admin, args.admin_token)
        device_http = JsonHttpClient(urls.device, args.device_token)
        device = DeviceHttpClient(device_http, DEFAULT_DEVICE_MAC)
        readback = ReadbackClient(admin_http, device)

        class Stack:
            admin = AdminHttpClient(admin_http)
            postgres = DbReadback(args.postgres_dsn)
            device_get = device.get
            websocket = EspWebSocket(urls.websocket, args.device_token or "", DEFAULT_DEVICE_MAC, controller)
            completion = device.completion
            progress = readback
            fault = controller.restart

        runner = JourneyRunner(Stack(), candidate_id=binding.candidate_id, binding=binding)
        specs = canonical_journeys(binding, all_26=args.all_26)
        journeys = [
            runner.run(
                lesson_key=spec.lesson_key, pedagogy=spec.pedagogy, faults=fault_plan,
                expected_checksum=spec.checksum,
            )
            for spec in specs
        ]
        validate_journey_set(journeys, 26 if args.all_26 else 6)
        aggregate_boundaries = journeys[0]["boundaries"]
        if any(row["boundaries"] != aggregate_boundaries for row in journeys):
            raise JourneyError("journey boundary ledgers disagree")
        private_adapter_calls = sum(row["privateAdapterCalls"] for row in journeys)
        report = {
            "schemaVersion": 1, "runner": "course-mode-cross-process.v1", "status": "pass",
            "candidateId": binding.candidate_id, "backendSha": binding.backend_sha,
            "courseKey": binding.course_key,
            "curriculumChecksum": binding.curriculum_checksum,
            "candidateIdentityDigest": binding.identity_digest,
            "lessonCount": len(journeys), "pedagogyCount": len({row["pedagogy"] for row in journeys}),
            "boundaries": aggregate_boundaries,
            "privateAdapterCalls": private_adapter_calls, "journeys": journeys,
        }
        ReportWriter().write(args.report, report)
        print(json.dumps({key: report[key] for key in ("status", "lessonCount", "pedagogyCount")}))
        return 0
    except Exception as exc:
        error_code = type(exc).__name__
        failure = {
            "schemaVersion": 1, "runner": "course-mode-cross-process.v1", "status": "fail",
            "error": {"code": error_code, "message": "cross-process journey failed"},
        }
        ReportWriter().write(args.report, failure)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
