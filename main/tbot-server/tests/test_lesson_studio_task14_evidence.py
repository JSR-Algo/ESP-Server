import importlib.util
import json
import struct
import zlib
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


def write_png(path: Path, width=480, height=320, color=(0, 0, 0)):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + bytes(color) * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def complete_result(tmp_path: Path) -> dict:
    preview = tmp_path / "preview.png"
    hardware = tmp_path / "hardware.png"
    write_png(preview, color=(1, 2, 3))
    write_png(hardware, color=(3, 2, 1))
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
        "screenshots": [
            {"role": "preview", "path": str(preview)},
            {"role": "hardware", "path": str(hardware)},
        ],
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
    result["screenshots"] = [{"role": "hardware", "path": str(tmp_path / "missing.png")}]
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified")
    assert "screenshots must reference non-empty regular files" in errors


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
    write_png(screenshot)
    result = complete_result(tmp_path)
    result["screenshots"] = [{"role": "hardware", "path": "hardware.png"}]
    report = fault.build_evidence_report(
        "cold", result, {}, "lesson_preload_ready checksum_verified", tmp_path
    )
    assert report["status"] == "PASS"
    assert report["screenshots"][0]["path"] == str(screenshot)


def test_preview_parity_requires_distinct_480x320_preview_and_hardware_images(tmp_path):
    result = complete_result(tmp_path)
    result.update({
        "scenario": "preview-parity",
        "logMarkers": ["lesson_step_started", "motion_preset"],
        "previewLayerRects": {"background": [0, 0, 480, 320]},
        "hardwareLayerRects": {"background": [0, 0, 480, 320]},
        "previewWordText": "BARN",
        "hardwareWordText": "BARN",
        "previewPathOutcome": "correct",
        "hardwarePathOutcome": "correct",
        "previewMotionTimeline": ["teach"],
        "hardwareMotionTimeline": ["teach"],
    })
    assert fault.validate_result("preview-parity", result, "lesson_step_started motion_preset", tmp_path) == []
    small = tmp_path / "small.png"
    write_png(small, 320, 240)
    result["screenshots"][1]["path"] = str(small)
    assert "preview-parity screenshots must be exactly 480x320" in fault.validate_result(
        "preview-parity", result, "lesson_step_started motion_preset", tmp_path
    )
    result["screenshots"][1]["path"] = result["screenshots"][0]["path"]
    assert "preview and hardware screenshots must not have identical content" in fault.validate_result(
        "preview-parity", result, "lesson_step_started motion_preset", tmp_path
    )


def test_screenshot_paths_are_confined_regular_bounded_images(tmp_path):
    outside = tmp_path.parent / "outside.png"
    write_png(outside)
    result = complete_result(tmp_path)
    result["screenshots"] = [{"role": "hardware", "path": "../outside.png"}]
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified", tmp_path)
    assert "screenshot path escapes evidence directory" in errors
    result["screenshots"] = [42]
    assert "malformed screenshot entry" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    result["screenshots"] = [{"role": "hardware", "path": "fake.png"}]
    assert "screenshots must be valid PNG or JPEG images" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    link = tmp_path / "linked.png"
    link.symlink_to(outside)
    result["screenshots"] = [{"role": "hardware", "path": "linked.png"}]
    assert "screenshot paths must not be symlinks" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    oversized = tmp_path / "oversized.png"
    write_png(oversized)
    with oversized.open("ab") as handle:
        handle.truncate(fault.MAX_SCREENSHOT_BYTES + 1)
    result["screenshots"] = [{"role": "hardware", "path": "oversized.png"}]
    assert "screenshot exceeds maximum size" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )


def test_malformed_structured_json_never_crashes_progress_audit():
    for payload in ("[]", '"lesson_progress"', "123", "null"):
        assert audit.audit(payload)["status"] == "PASS"


def test_progress_identity_distinguishes_event_type_and_sequence():
    legitimate = "\n".join([
        json.dumps({"type": "lesson_progress_started", "session_id": "s", "step_id": "a", "sequence": 7}),
        json.dumps({"type": "lesson_progress_completed", "session_id": "s", "step_id": "a", "sequence": 7}),
    ])
    assert audit.audit(legitimate)["status"] == "PASS"
    duplicate = legitimate + "\n" + json.dumps(
        {"type": "lesson_progress_completed", "session_id": "s", "step_id": "a", "sequence": 7}
    )
    report = audit.audit(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"][0]["eventType"] == "lesson_progress_completed"
    assert report["duplicateProgress"][0]["identity"] == "sequence:7"


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
    assert report["duplicateProgress"] == [{
        "eventType": "lesson_progress",
        "session": "session-1",
        "step": "step-a",
        "identity": "identity:missing",
    }]


def test_task14_docs_include_preview_and_keep_every_live_gate_not_pass():
    probes = (ROOT / "docs" / "lesson-studio-task14-probes.md").read_text()
    matrix = (ROOT / "docs" / "TEST_MATRIX_TASK14.md").read_text()
    live = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    assert "`preview-parity`" in probes
    assert "preview-parity" in matrix
    assert "common metadata" in probes.lower()
    assert '"role": "preview"' in probes
    assert "480x320" in probes
    assert "10 MiB" in probes
    statuses = [line for line in live.splitlines() if line.startswith("| `")]
    assert statuses
    assert all("NOT PASS" in line for line in statuses)
