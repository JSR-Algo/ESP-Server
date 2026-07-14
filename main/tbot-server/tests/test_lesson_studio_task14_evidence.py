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


def authentic_soak_logs(session_sizes=(40, 40, 20), *, repeated_step_ids=False):
    server_lines = []
    serial_lines = []
    for session_index, step_count in enumerate(session_sizes, start=1):
        assignment = f"assignment-{session_index}"
        session = f"session-{session_index}"
        for step_index in range(1, step_count + 1):
            step_id = f"step-{step_index}" if repeated_step_ids else f"s{session_index}-{step_index}"
            sequence = step_index + 2
            server_lines.append(
                "[LessonRuntime]-INFO-emit lesson_step "
                f"stepId={step_id} stepType=model assignment_id={assignment} "
                f"session_id={session}"
            )
            serial_lines.extend([
                f"I (1) WS: ws text lesson frame type=lesson_step seq={sequence} bytes=100",
                (
                    "I (2) SystemInfo: heap_checkpoint phase=lesson_render.complete "
                    f"internal_free=30000 lifetime_min_internal=25000 "
                    f"phase_min_internal={24576 - (step_index % 3)} "
                    f"largest_internal=20000 psram_free={8_000_000 - step_index * 128}"
                ),
                (
                    "I (3) Lesson: lesson_step rendered "
                    f"stepId={step_id} passive=0 degraded=0 renderElapsedMs=12"
                ),
            ])
    return "\n".join(serial_lines), "\n".join(server_lines)


def authentic_soak_timeline(session_sizes=(40, 40, 20)):
    lines = []
    timestamp = 1
    for session_index, step_count in enumerate(session_sizes, start=1):
        assignment = f"assignment-{session_index}"
        session = f"session-{session_index}"
        lines.append(
            f"{timestamp} server [LessonRuntime]-INFO-emit lesson_prepare stepId= "
            f"assignment_id={assignment} session_id={session}"
        )
        timestamp += 1
        lines.append(
            f"{timestamp} serial I (1) WS: ws text lesson frame type=lesson_prepare seq=1 bytes=100"
        )
        timestamp += 1
        for step_index in range(1, step_count + 1):
            step_id = f"step-{step_index}"
            sequence = step_index + 2
            lines.extend([
                (
                    f"{timestamp} server [LessonRuntime]-INFO-emit lesson_step "
                    f"stepId={step_id} assignment_id={assignment} session_id={session}"
                ),
                (
                    f"{timestamp + 1} serial I (2) WS: ws text lesson frame "
                    f"type=lesson_step seq={sequence} bytes=100"
                ),
                (
                    f"{timestamp + 2} serial I (3) SystemInfo: heap_checkpoint "
                    "phase=lesson_render.complete internal_free=30000 "
                    "lifetime_min_internal=25000 phase_min_internal=24576 "
                    f"largest_internal=20000 psram_free={8_000_000 - step_index * 128}"
                ),
                (
                    f"{timestamp + 3} serial I (4) Lesson: lesson_step rendered "
                    f"stepId={step_id} passive=0 degraded=0 renderElapsedMs=12"
                ),
            ])
            timestamp += 4
    return "\n".join(lines)


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
        "sessionId": "session-1",
        "assignmentVersion": 1,
        "lessonId": "lesson-1",
        "lessonVersion": 1,
        "manifestChecksum": "d" * 64,
        "packChecksum": "d" * 64,
        "cacheKey": "lesson-1/v1-" + "d" * 64,
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


def cold_raw_evidence(result):
    return "\n".join([
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "lesson_preload_ready cacheKey={cacheKey} assetCount=2 downloadedCount=2 "
            "skippedCount=0 failedCount=0 durationMs={elapsedMs} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
        (
            "checksum_verified cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "assetCount=2 assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def warm_raw_evidence(result):
    return "\n".join([
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "asset_cache_hit cacheKey={cacheKey} assetCount=2 downloadedCount=0 "
            "skippedCount=2 failedCount=0 durationMs={elapsedMs} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def checksum_raw_evidence(result):
    return "\n".join([
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "checksum_mismatch cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "mismatchDetected=true partialCleaned=true ready=false "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
        (
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def test_common_metadata_requires_real_screenshots_and_release_fields(tmp_path):
    result = complete_result(tmp_path)
    assert fault.validate_result("cold", result, cold_raw_evidence(result)) == []
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


def test_malformed_scenario_fields_fail_closed_instead_of_crashing(tmp_path):
    malformed = {
        "cold": {"bytesDownloaded": "100"},
        "warm": {"elapsedMs": []},
        "sd-full": {"freeRatio": "0.04"},
    }
    for scenario, fields in malformed.items():
        result = complete_result(tmp_path)
        result["scenario"] = scenario
        result.update(fields)
        errors = fault.validate_result(scenario, result, "lesson_preload_ready checksum_verified")
        assert f"{scenario} decisive signals are incomplete" in errors


def test_evidence_report_hashes_screenshots(tmp_path):
    result = complete_result(tmp_path)
    evidence = fault.build_evidence_report("cold", result, {}, cold_raw_evidence(result))
    assert evidence["status"] == "PASS"
    assert len(evidence["screenshots"]) == 2
    assert all(len(item["sha256"]) == 64 for item in evidence["screenshots"])


def test_relative_screenshot_paths_resolve_from_evidence_directory(tmp_path):
    screenshot = tmp_path / "hardware.png"
    write_png(screenshot)
    result = complete_result(tmp_path)
    result["screenshots"] = [{"role": "hardware", "path": "hardware.png"}]
    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), tmp_path
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
    huge = tmp_path / "huge.png"
    write_png(huge, 1, 1)
    data = bytearray(huge.read_bytes())
    data[16:24] = struct.pack(">II", 100_000, 100_000)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])))
    huge.write_bytes(data)
    result["screenshots"] = [{"role": "hardware", "path": "huge.png"}]
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
    no_pixels = tmp_path / "no-pixels.png"
    no_pixels.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 480, 320, 8, 2, 0, 0, 0)
        + struct.pack(">I", zlib.crc32(b"IHDR" + struct.pack(">IIBBBBB", 480, 320, 8, 2, 0, 0, 0)))
        + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    )
    result["screenshots"] = [{"role": "hardware", "path": "no-pixels.png"}]
    assert "screenshots must be valid PNG or JPEG images" in fault.validate_result(
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


def test_progress_identity_uses_real_envelope_and_text_semantic_event():
    envelopes = [
        {"type": "lesson_progress", "sessionId": "s", "stepId": "a", "body": {"event": "step_started"}},
        {"type": "lesson_progress", "sessionId": "s", "stepId": "a", "body": {"event": "step_completed"}},
    ]
    assert audit.audit("\n".join(map(json.dumps, envelopes)))["status"] == "PASS"
    text_logs = "\n".join([
        "lesson_progress event=step_started session_id=s step_id=a",
        "lesson_progress event=step_completed session_id=s step_id=a",
    ])
    assert audit.audit(text_logs)["status"] == "PASS"
    duplicate = "\n".join([json.dumps(envelopes[1]), json.dumps(envelopes[1])])
    report = audit.audit(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"][0]["eventType"] == "step_completed"


def test_soak_rejects_duplicate_or_out_of_order_transition_identity():
    duplicate = "\n".join(
        f"lesson_step_transition sequence={0 if index == 50 else index} psram_free=8000000 internal_min_free=32768"
        for index in range(100)
    )
    report = soak.analyze(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_sequence_strictly_increasing"] is False


def test_soak_rejects_large_psram_loss_with_small_recovery_blips():
    lines = []
    for index in range(100):
        psram = 8_000_000 - index * 2_048
        if index == 50:
            psram += 4_096
        lines.append(
            f"lesson_step_transition sequence={index} psram_free={psram} "
            "internal_min_free=32768"
        )
    report = soak.analyze("\n".join(lines))
    assert report["status"] == "NOT_PASS"
    assert report["checks"]["no_psram_loss_above_tolerance"] is False


def test_soak_accepts_authentic_three_session_transitions_with_sequence_resets():
    serial_log, server_log = authentic_soak_logs()

    report = soak.analyze_logs(serial_log, server_log, count=99)

    assert report["status"] == "PASS"
    assert report["metrics"]["transitions"] == 100
    assert report["metrics"]["sessions"] == 3
    assert report["metrics"]["internalSramMin"] == 24574


def test_production_soak_requires_timeline_instead_of_zip_binding_sources():
    serial_log, server_log = authentic_soak_logs()
    foreign_server = server_log.replace("assignment-", "foreign-assignment-").replace(
        "session-", "foreign-session-"
    )

    for candidate in (server_log, foreign_server):
        report = soak.analyze_logs(serial_log, candidate)
        assert report["status"] == "NOT_PASS"
        assert report["checks"]["timeline_binding_required"] is False


def test_soak_fails_closed_when_cross_source_transition_binding_is_incomplete():
    serial_log, server_log = authentic_soak_logs()
    server_log = server_log.replace(" assignment_id=assignment-2", "", 1)

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_binding_complete"] is False


def test_soak_reads_camel_case_ack_telemetry_heap_samples():
    serial_log, server_log = authentic_soak_logs()
    serial_log = "\n".join(
        line for line in serial_log.splitlines() if "heap_checkpoint" not in line
    )
    telemetry = "\n".join(
        '{"type":"lesson_ack","body":{"telemetry":{"internalMinimumFreeBytes":24576,'
        f'"psramFreeBytes":{8_000_000 - index}}}}}'
        for index in range(100)
    )

    report = soak.analyze_logs(
        serial_log + "\n" + telemetry + "\nlifetime_min_internal=24576",
        server_log,
        count=99,
    )

    assert report["status"] == "PASS"
    assert report["metrics"]["internalSramMin"] == 24576


def test_soak_lifetime_internal_sram_below_gate_blocks_release():
    serial_log, server_log = authentic_soak_logs()
    serial_log = serial_log.replace("lifetime_min_internal=25000", "lifetime_min_internal=1024")

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["lifetime_internal_sram_above_gate"] is False
    assert report["metrics"]["activeInternalSramMin"] == 24574
    assert report["metrics"]["lifetimeInternalSramMin"] == 1024


def test_soak_missing_lifetime_internal_sram_fails_closed():
    serial_log, server_log = authentic_soak_logs()
    serial_log = "\n".join(
        line.replace(" lifetime_min_internal=25000", "")
        for line in serial_log.splitlines()
    )

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["lifetime_internal_sram_present"] is False


def test_soak_requires_timeline_for_repeated_step_ids_across_sessions():
    serial_log, server_log = authentic_soak_logs(repeated_step_ids=True)

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_binding_unambiguous"] is False


def test_soak_timeline_binds_repeated_step_ids_to_their_session_boundaries():
    report = soak.analyze_timeline(authentic_soak_timeline())

    assert report["status"] == "PASS"
    assert report["metrics"]["transitions"] == 100
    assert report["metrics"]["sessions"] == 3


def test_log_audit_rejects_firmware_crash_and_heap_corruption_markers():
    for marker in (
        "assert failed: runtime.cpp:42",
        "stack overflow in task lesson",
        "CORRUPT HEAP: bad tail",
        "abort() was called at PC 0x1234",
        "LoadProhibited exception",
    ):
        report = audit.audit(marker)
        assert report["status"] == "NOT_PASS", marker
        assert report["findings"]["firmwareCrash"] == 1


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


def test_log_audit_requires_both_sources_and_one_hundred_bound_transitions():
    serial_log, server_log = authentic_soak_logs()
    timeline = authentic_soak_timeline()

    assert audit.audit_logs(serial_log, server_log)["status"] == "NOT_PASS"
    assert audit.audit_logs(serial_log, server_log, timeline_text=timeline)["status"] == "PASS"
    assert audit.audit_logs(serial_log, "")["status"] == "NOT_PASS"
    short_serial, short_server = authentic_soak_logs((33, 33, 33))
    short_timeline = authentic_soak_timeline((33, 33, 33))
    assert audit.audit_logs(
        short_serial, short_server, timeline_text=short_timeline
    )["status"] == "NOT_PASS"


def test_log_audit_uses_timeline_session_boundaries_for_repeated_step_ids():
    serial_log, server_log = authentic_soak_logs(repeated_step_ids=True)

    assert audit.audit_logs(serial_log, server_log)["status"] == "NOT_PASS"
    report = audit.audit_logs(
        serial_log,
        server_log,
        timeline_text=authentic_soak_timeline(),
    )

    assert report["status"] == "PASS"
    assert report["metrics"] == {"transitions": 100, "sessions": 3}


def test_log_audit_recognizes_actual_runtime_failure_vocabulary_and_drop_deltas():
    serial_log, server_log = authentic_soak_logs()
    failures = (
        "Failed to allocate download buffer",
        "lesson image: decoded JPEG rejected; skipping",
        "sequence gap: got 7, expected 6",
        "audio_metrics decode_drop=0 encode_drop=0",
        "audio_metrics decode_drop=1 encode_drop=0",
    )

    report = audit.audit_logs(serial_log + "\n" + "\n".join(failures), server_log)

    assert report["status"] == "NOT_PASS"
    assert report["findings"]["allocationFailure"] == 1
    assert report["findings"]["decodeFailure"] == 1
    assert report["findings"]["sequenceDivergence"] == 1
    assert report["findings"]["audioUnderrun"] == 1


def test_fault_driver_rejects_unscoped_operator_marker_text_for_cold(tmp_path):
    result = complete_result(tmp_path)

    errors = fault.validate_result(
        "cold",
        result,
        "lesson_preload_ready checksum_verified operator says downloadedCount=2",
    )

    assert "cold raw evidence is not bound to result identity and cache" in errors


def test_fault_driver_rejects_cross_session_cold_markers(tmp_path):
    result = complete_result(tmp_path)
    raw = cold_raw_evidence(result).replace("session_id=session-1", "session_id=session-other")

    errors = fault.validate_result("cold", result, raw)

    assert "cold raw evidence is not bound to result identity and cache" in errors


def test_fault_driver_binds_warm_and_checksum_decisive_fields_to_raw_logs(tmp_path):
    warm = complete_result(tmp_path)
    warm.update({
        "scenario": "warm",
        "cacheHit": True,
        "bytesDownloaded": 0,
        "logMarkers": ["asset_cache_hit"],
    })
    assert fault.validate_result("warm", warm, warm_raw_evidence(warm), tmp_path) == []
    warm_errors = fault.validate_result(
        "warm",
        warm,
        warm_raw_evidence(warm).replace(warm["cacheKey"], "wrong-cache"),
        tmp_path,
    )
    assert "warm raw evidence is not bound to result identity and cache" in warm_errors

    checksum = complete_result(tmp_path)
    checksum.update({
        "scenario": "checksum",
        "mismatchDetected": True,
        "partialCleaned": True,
        "ready": False,
        "logMarkers": ["checksum_mismatch", "partial_cleaned"],
    })
    assert fault.validate_result("checksum", checksum, checksum_raw_evidence(checksum), tmp_path) == []
    checksum_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace("partialCleaned=true", "partialCleaned=false"),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in checksum_errors
    cleanup_cache_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace(
            "partial_cleaned cacheKey=" + checksum["cacheKey"],
            "partial_cleaned cacheKey=foreign-cache",
        ),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in cleanup_cache_errors
    cleanup_checksum_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace(
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={manifestChecksum}".format(**checksum),
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={wrong}".format(
                **checksum, wrong="e" * 64
            ),
        ),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in cleanup_checksum_errors


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
    assert "canonical `partial_cleaned` line must bind" in probes
    assert "`cacheKey`, `manifestChecksum`, `assignment_id`, and `session_id`" in probes
    statuses = [line for line in live.splitlines() if line.startswith("| `")]
    assert statuses
    assert all("NOT PASS" in line for line in statuses)
