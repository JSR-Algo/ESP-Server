import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fault = load_script("lesson_studio_task14_fault_driver.py")
soak = load_script("lesson_studio_task14_soak.py")
audit = load_script("lesson_studio_task14_log_audit.py")


def complete_result(tmp_path: Path) -> dict:
    preview = tmp_path / "preview.png"
    hardware = tmp_path / "hardware.png"
    preview.write_bytes(b"preview")
    hardware.write_bytes(b"hardware")
    return {
        "scenario": "cold",
        "status": "PASS",
        "utcStart": "2026-07-12T00:00:00Z",
        "utcEnd": "2026-07-12T00:10:00Z",
        "backendCommit": "a" * 40,
        "espServerCommit": "b" * 40,
        "firmwareCommit": "c" * 40,
        "firmwareVersion": "2.2.37",
        "deviceId": "28:84:85:85:1a:80",
        "assignmentId": "assignment-1",
        "assignmentVersion": 1,
        "lessonId": "lesson-1",
        "lessonVersion": 1,
        "manifestChecksum": "d" * 64,
        "packChecksum": "d" * 64,
        "internalSramMin": 32768,
        "psramFirst": 8_000_000,
        "psramLast": 7_999_000,
        "screenshots": [str(preview), str(hardware)],
        "operator": "lab-operator",
        "commandExitCode": 0,
        "bytesDownloaded": 100,
        "elapsedMs": 1000,
        "ready": True,
        "checksumVerified": True,
        "logMarkers": ["lesson_preload_ready", "checksum_verified"],
    }


def test_common_metadata_requires_real_screenshots_and_release_fields(tmp_path):
    result = complete_result(tmp_path)
    assert fault.validate_result("cold", result, "lesson_preload_ready checksum_verified") == []
    result["screenshots"] = [str(tmp_path / "missing.png")]
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified")
    assert "screenshots must reference at least one non-empty existing file" in errors


def test_scenario_markers_must_be_present_in_raw_logs(tmp_path):
    result = complete_result(tmp_path)
    errors = fault.validate_result("cold", result, "unrelated output")
    assert "raw logs missing declared marker: lesson_preload_ready" in errors
    assert "raw logs missing declared marker: checksum_verified" in errors


def test_scenario_requires_decisive_marker_names_not_arbitrary_log_text(tmp_path):
    result = complete_result(tmp_path)
    result["logMarkers"] = ["operator_says_pass"]
    errors = fault.validate_result("cold", result, "operator_says_pass")
    assert "cold requires log marker: lesson_preload_ready" in errors
    assert "cold requires log marker: checksum_verified" in errors


def test_common_metadata_rejects_invalid_time_identity_and_heap_types(tmp_path):
    result = complete_result(tmp_path)
    result.update({"utcEnd": "before-start", "deviceId": "", "internalSramMin": True})
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified")
    assert "invalid UTC evidence interval" in errors
    assert "invalid deviceId" in errors
    assert "invalid internalSramMin" in errors


def test_evidence_report_hashes_screenshots(tmp_path):
    result = complete_result(tmp_path)
    evidence = fault.build_evidence_report("cold", result, {}, "lesson_preload_ready checksum_verified")
    assert evidence["status"] == "PASS"
    assert len(evidence["screenshots"]) == 2
    assert all(len(item["sha256"]) == 64 for item in evidence["screenshots"])


def test_relative_screenshot_paths_resolve_from_evidence_directory(tmp_path):
    screenshot = tmp_path / "hardware.png"
    screenshot.write_bytes(b"hardware")
    result = complete_result(tmp_path)
    result["screenshots"] = ["hardware.png"]
    report = fault.build_evidence_report(
        "cold", result, {}, "lesson_preload_ready checksum_verified", tmp_path
    )
    assert report["status"] == "PASS"
    assert report["screenshots"][0]["path"] == str(screenshot)


def test_soak_rejects_duplicate_or_out_of_order_transition_identity():
    duplicate = "\n".join(
        f"lesson_step_transition sequence={0 if index == 50 else index} psram_free=8000000 internal_min_free=32768"
        for index in range(100)
    )
    report = soak.analyze(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_sequence_strictly_increasing"] is False


def test_log_audit_detects_duplicate_progress_regardless_of_field_order():
    lines = [
        json.dumps({"type": "lesson_progress", "step_id": "step-a", "session_id": "session-1"}),
        "lesson_progress step id=step-a session id=session-1",
    ]
    report = audit.audit("\n".join(lines))
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"] == [{"session": "session-1", "step": "step-a"}]


def test_task14_docs_include_preview_and_keep_every_live_gate_not_pass():
    probes = (ROOT / "docs" / "lesson-studio-task14-probes.md").read_text()
    matrix = (ROOT / "docs" / "TEST_MATRIX_TASK14.md").read_text()
    live = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    assert "`preview-parity`" in probes
    assert "preview-parity" in matrix
    assert "common metadata" in probes.lower()
    statuses = [line for line in live.splitlines() if line.startswith("| `")]
    assert statuses
    assert all("NOT PASS" in line for line in statuses)
