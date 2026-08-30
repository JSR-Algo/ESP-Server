from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.course_mode_cross_process_e2e import (
    BOUNDARIES,
    AckScheduler,
    AdminHttpClient,
    CandidateBinding,
    DbReadback,
    DeviceHttpClient,
    EspWebSocket,
    FaultPlan,
    JourneyError,
    JourneyRunner,
    JsonHttpClient,
    ProcessController,
    ReadbackClient,
    ReportWriter,
    StackUrls,
    canonical_journeys,
    validate_journey_set,
)


class PublicStack:
    def __init__(self) -> None:
        self.calls = []
        self.completion_attempts = 0
        self.active_fault = None

    def fault(self, name):
        self.active_fault = name

    def admin(self, operation, payload):
        self.calls.append(("admin-http", operation))
        return {
            "author": {"lessonId": "lesson-1", "version": 7},
            "visuals": {"saved": True},
            "validate": {"valid": True},
            "publish": {"checksum": "a" * 64},
            "assign": {"assignmentId": "assignment-1", "deliveryId": "delivery-1"},
        }[operation]

    def postgres(self, assignment_id):
        self.calls.append(("postgres", assignment_id))
        return {"assignmentId": assignment_id, "lessonId": "lesson-1", "lessonVersion": 7}

    def device_get(self, operation, **params):
        boundary = "assignment-http" if operation == "assignment" else "manifest-http"
        self.calls.append((boundary, operation))
        if operation == "assignment":
            return {
                "assignmentId": "assignment-1", "lessonId": "lesson-1", "version": 7,
                "deliveryId": "delivery-1",
            }
        if self.active_fault == "stale_manifest":
            return {"lessonId": "lesson-1", "version": params["version"] - 1, "checksum": "a" * 64}
        if self.active_fault == "recover_stale_manifest":
            self.active_fault = None
        if self.active_fault == "checksum_mismatch":
            return {"lessonId": "lesson-1", "version": params["version"], "checksum": "0" * 64}
        if self.active_fault == "recover_checksum_mismatch":
            self.active_fault = None
        return {"lessonId": "lesson-1", "version": params["version"], "checksum": "a" * 64}

    def websocket(self, assignment, manifest, responses, faults):
        self.calls.append(("device-websocket", assignment["assignmentId"]))
        return {
            "sessionId": "session-1", "deliveryId": "delivery-1",
            "terminalState": "COMPLETED", "activityCount": 10,
            "ackCount": 10, "duplicateAcksIgnored": faults.duplicate_acks,
            "assignmentDeliveryId": assignment["deliveryId"],
            "faultEvidence": faults.enabled_names(),
        }

    def completion(self, payload, faults):
        self.calls.append(("completion-http", payload["sessionId"]))
        self.completion_attempts += 1
        if self.completion_attempts <= faults.completion_failures:
            return {"status": 429 if self.completion_attempts == 1 else 503}
        return {"status": 200, "completionId": "completion-1", "activityCount": 10}

    def progress(self, assignment_id):
        self.calls.append(("progress-http", assignment_id))
        return {
            "assignmentId": assignment_id, "completed": True, "activityCount": 10,
            "insightsActivityCount": 10, "parentActivityCount": 10,
        }


def test_journey_uses_only_the_public_boundary_ledger():
    stack = PublicStack()
    report = JourneyRunner(stack, candidate_id="candidate-1").run(
        lesson_key="w01-greetings-politeness", pedagogy="tprGesture",
    )

    assert report["boundaries"] == list(BOUNDARIES)
    assert report["privateAdapterCalls"] == 0
    assert [call[0] for call in stack.calls] == [
        "admin-http", "admin-http", "admin-http", "admin-http", "admin-http",
        *BOUNDARIES[1:],
    ]
    assert report["candidateId"] == "candidate-1"
    assert report["checksum"] == "a" * 64


def test_faults_retry_completion_and_keep_exactly_once_counts():
    stack = PublicStack()
    faults = FaultPlan(
        websocket_drop=True, esp_restart=True, backend_restart=True,
        completion_failures=2, stale_manifest=True, checksum_mismatch=True,
        cache_unavailable=True, asr_unavailable=True, silence_help=True,
        duplicate_acks=2, delayed_acks=2, out_of_order_acks=True,
    )

    report = JourneyRunner(stack, candidate_id="candidate-1").run(
        lesson_key="w26-celebration-showcase", pedagogy="celebrationShowcase",
        faults=faults,
    )

    assert stack.completion_attempts == 3
    assert report["activityCount"] == report["ackCount"] == 10
    assert report["recoveredFaults"] == sorted(faults.enabled_names())


def test_requested_fault_is_not_reported_without_observed_recovery():
    stack = PublicStack()
    original = stack.websocket
    stack.websocket = lambda *args: {**original(*args), "faultEvidence": []}
    with pytest.raises(JourneyError, match="recovery evidence"):
        JourneyRunner(stack, candidate_id="candidate-1").run(
            lesson_key="w01-greetings-politeness", pedagogy="tprGesture",
            faults=FaultPlan(asr_unavailable=True),
        )


def test_candidate_bound_runner_rejects_running_stack_identity_drift():
    binding = CandidateBinding(
        "candidate-1", "a" * 40, Path("/backend"), "course-key", "b" * 64, "c" * 64,
    )
    with pytest.raises(JourneyError, match="candidate anchors"):
        JourneyRunner(PublicStack(), candidate_id="candidate-1", binding=binding).run(
            lesson_key="w01-greetings-politeness", pedagogy="tprGesture",
        )


@pytest.mark.parametrize("drift", ["database", "assignment", "delivery"])
def test_journey_rejects_unrelated_identity_at_every_public_hop(drift):
    stack = PublicStack()
    if drift == "database":
        stack.postgres = lambda assignment_id: {
            "assignmentId": "stale", "lessonId": "lesson-1", "lessonVersion": 7,
        }
    elif drift == "assignment":
        original = stack.device_get
        stack.device_get = lambda operation, **params: (
            {"assignmentId": "unrelated", "lessonId": "lesson-1", "version": 7, "deliveryId": "delivery-1"}
            if operation == "assignment" else original(operation, **params)
        )
    else:
        original_ws = stack.websocket
        stack.websocket = lambda *args: {**original_ws(*args), "assignmentDeliveryId": "unrelated"}
    with pytest.raises(JourneyError, match="identity"):
        JourneyRunner(stack, candidate_id="candidate-1").run(
            lesson_key="w01-greetings-politeness", pedagogy="tprGesture",
        )


def test_representatives_cover_six_pedagogies_and_all_26_use_canonical_keys(monkeypatch):
    fixture = {
        "lessons": [{"week": week} for week in range(1, 27)],
        "lessonKeys": [f"canonical-week-{week}" for week in range(1, 27)],
        "contractChecksums": [f"sum-{week}" for week in range(1, 27)],
    }
    monkeypatch.setattr(
        "scripts.course_mode_cross_process_e2e.load_backend_contracts",
        lambda root, expected_sha: (fixture, []),
    )
    binding = CandidateBinding("candidate-1", "a" * 40, Path("/backend"), "course", "b" * 64, "c" * 64)
    rows = canonical_journeys(binding, all_26=False)
    assert [(row.week, row.pedagogy) for row in rows] == [
        (1, "tprGesture"), (2, "pictureDiscovery"), (3, "storyContext"),
        (7, "rolePlay"), (4, "spiralCheckpoint"), (26, "celebrationShowcase"),
    ]
    assert rows[0].lesson_key == "canonical-week-1"
    all_rows = canonical_journeys(binding, all_26=True)
    assert [row.week for row in all_rows] == list(range(1, 27))
    assert len({row.lesson_key for row in all_rows}) == 26


def test_aggregate_rejects_duplicate_or_missing_public_identities():
    rows = [{
        "lessonId": f"lesson-{index}", "version": 1, "assignmentId": f"a-{index}",
        "sessionId": f"s-{index}", "deliveryId": "duplicate",
    } for index in range(2)]
    with pytest.raises(JourneyError, match="deliveryId"):
        validate_journey_set(rows, 2)


def test_report_writer_is_bounded_redacted_and_atomic(tmp_path: Path):
    path = tmp_path / "report.json"
    writer = ReportWriter(max_bytes=4096)
    writer.write(path, {
        "status": "pass", "token": "secret-token", "authorization": "Bearer secret",
        "nested": {"password": "secret-password", "rawChildAudio": "base64-secret"},
        "journeys": [{"lessonKey": "w01", "status": "pass"}],
    })

    raw = path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert not list(tmp_path.glob(".report.json.*.tmp"))
    assert json.loads(raw)["nested"]["rawChildAudio"] == "[REDACTED]"


def test_stack_urls_reject_non_loopback_or_non_public_websocket():
    with pytest.raises(ValueError, match="loopback"):
        StackUrls(admin="https://prod.example", device="http://127.0.0.1:8003", websocket="ws://127.0.0.1:8000/tbot/v1/")
    with pytest.raises(ValueError, match="/tbot/v1/"):
        StackUrls(admin="http://127.0.0.1:3000", device="http://127.0.0.1:8003", websocket="ws://127.0.0.1:8000/private")


def test_candidate_binding_fails_closed_on_manifest_reasons(monkeypatch, tmp_path: Path):
    path = tmp_path / "candidate.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.course_mode_cross_process_e2e.validate_candidate",
        lambda candidate: ["repositories.backend.sha"],
    )
    with pytest.raises(JourneyError, match="repositories.backend.sha"):
        CandidateBinding.load(path)


def test_process_controller_requires_an_explicit_tokenized_command():
    with pytest.raises(JourneyError, match="explicit restart command"):
        ProcessController({}).restart("backend")


def test_ack_scheduler_is_deterministic_for_duplicate_and_out_of_order_faults():
    scheduler = AckScheduler(FaultPlan(duplicate_acks=2, out_of_order_acks=True))
    assert scheduler.order(["d1", "d2", "d3"]) == ["d2", "d1", "d3", "d2", "d1"]


def test_runner_source_has_no_private_runtime_adapter_imports():
    source = (Path(__file__).parents[1] / "scripts/course_mode_cross_process_e2e.py").read_text()
    assert "CourseModeRuntimeAdapter" not in source
    assert "course_orchestrator" not in source
    assert "core.lesson.runtime" not in source


def test_failure_report_does_not_copy_exception_text(tmp_path: Path):
    candidate = tmp_path / "candidate-secret-token.json"
    report = tmp_path / "report.json"
    candidate.write_text("{}", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts/course_mode_cross_process_e2e.py"
    result = subprocess.run([
        sys.executable, str(script), "--candidate", str(candidate), "--report", str(report),
        "--postgres-dsn", "postgresql://secret-token@127.0.0.1:1/nope",
    ], capture_output=True, text=True)
    assert result.returncode == 1
    raw = report.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    assert json.loads(raw)["error"]["message"] == "cross-process journey failed"


def test_runner_is_directly_executable_from_server_root():
    script = Path(__file__).parents[1] / "scripts/course_mode_cross_process_e2e.py"
    result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--report" in result.stdout
    assert "--postgres-dsn" in result.stdout


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.02)
    raise AssertionError(f"loopback process did not listen on {port}")


def test_concrete_clients_cross_http_websocket_and_command_processes(tmp_path: Path):
    http_port, websocket_port, postgres_port = _free_port(), _free_port(), _free_port()
    http_server = tmp_path / "http_stack.py"
    http_server.write_text(
        """import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
CHECKSUM = 'a' * 64
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, body):
        encoded=json.dumps(body).encode(); self.send_response(200)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(encoded)))
        self.end_headers(); self.wfile.write(encoded)
    def do_PUT(self): self.reply({'saved': True})
    def do_POST(self):
        if self.path.endswith('/course-mode'): return self.reply({'lessonId':'lesson-1','version':7})
        if self.path.endswith('/validate'): return self.reply({'valid':True})
        if self.path.endswith('/publish'): return self.reply({'checksum':CHECKSUM})
        if self.path.endswith('/lesson-assignments'): return self.reply({'assignmentId':'assignment-1','deliveryId':'delivery-1'})
        if self.path.endswith('/lesson-events'): return self.reply({'completionId':'completion-1','activityCount':10})
        self.reply({})
    def do_GET(self):
        if '/lesson-assignments/current' in self.path: return self.reply({'assignmentId':'assignment-1','lessonId':'lesson-1','version':7,'deliveryId':'delivery-1'})
        if '/manifest?' in self.path: return self.reply({'lessonId':'lesson-1','version':7,'checksum':CHECKSUM})
        if '/progress?' in self.path: return self.reply({'assignmentId':'assignment-1','completed':True,'activityCount':10})
        if '/insights/child?' in self.path or '/mobile/status?' in self.path: return self.reply({'activityCount':10})
        self.reply({'events':[]})
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
""", encoding="utf-8",
    )
    websocket_server = tmp_path / "websocket_stack.py"
    websocket_server.write_text(
        """import asyncio, json, sys, websockets
async def handler(socket):
    await socket.recv()
    for index in range(10):
        await socket.send(json.dumps({'type':'lesson_step','deliveryId':f'd-{index}','assignmentDeliveryId':'delivery-1','sessionId':'session-1'}))
        while json.loads(await socket.recv()).get('type') != 'lesson_ack': pass
    await socket.send(json.dumps({'type':'lesson_stop','sessionId':'session-1'}))
async def main():
    async with websockets.serve(handler, '127.0.0.1', int(sys.argv[1])):
        await asyncio.Future()
asyncio.run(main())
""", encoding="utf-8",
    )
    processes = [
        subprocess.Popen(
            [sys.executable, str(http_server), str(http_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ),
        subprocess.Popen(
            [sys.executable, str(websocket_server), str(websocket_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ),
    ]
    try:
        _wait_for_port(http_port)
        _wait_for_port(websocket_port)
        base = JsonHttpClient(f"http://127.0.0.1:{http_port}", "local-test-token")
        device = DeviceHttpClient(base, "14:c1:9f:d1:ac:20")
        pgdata = tmp_path / "pgdata"
        subprocess.run(["initdb", "-A", "trust", "-U", "postgres", "-D", str(pgdata)], check=True, capture_output=True)
        subprocess.run([
            "pg_ctl", "-D", str(pgdata), "-o", f"-h 127.0.0.1 -p {postgres_port}", "-w", "start",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        postgres_dsn = f"postgresql://postgres@127.0.0.1:{postgres_port}/postgres"
        subprocess.run([
            "psql", postgres_dsn, "-v", "ON_ERROR_STOP=1", "-c",
            "CREATE TABLE lesson_assignments (id text PRIMARY KEY, lesson_id text, lesson_version int);"
            "INSERT INTO lesson_assignments VALUES ('assignment-1','lesson-1',7);",
        ], check=True, capture_output=True)
        probe = subprocess.run([
            "psql", "-X", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--set",
            "assignment_id=assignment-1", "--tuples-only", "--no-align", "--dbname",
            postgres_dsn,
        ], capture_output=True, text=True, input=DbReadback.QUERY)
        assert probe.returncode == 0, probe.stderr
        stack = SimpleNamespace(
            admin=AdminHttpClient(base), postgres=DbReadback(postgres_dsn), device_get=device.get,
            websocket=EspWebSocket(
                f"ws://127.0.0.1:{websocket_port}/tbot/v1/", "local-test-token",
                "14:c1:9f:d1:ac:20",
            ), completion=device.completion, progress=ReadbackClient(base, device),
        )
        report = JourneyRunner(stack, candidate_id="candidate-1").run(
            lesson_key="w01-greetings-politeness", pedagogy="tprGesture",
            faults=FaultPlan(websocket_drop=True),
        )
        assert report["boundaries"] == list(BOUNDARIES)
        assert report["activityCount"] == 10
        assert report["privateAdapterCalls"] == 0
        assert report["recoveredFaults"] == ["websocket_drop"]
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=5)
        if (tmp_path / "pgdata").exists():
            subprocess.run(["pg_ctl", "-D", str(tmp_path / "pgdata"), "-m", "fast", "-w", "stop"], check=False)
