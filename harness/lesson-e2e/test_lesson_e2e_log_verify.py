from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lesson_e2e_log_verify.py"


def load_module():
    spec = importlib.util.spec_from_file_location("lesson_e2e_log_verify", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def by_name(report: dict):
    return {check["name"]: check for check in report["checks"]}

def cp7_valid_sidecar_lines() -> list[str]:
    return [
        "cp7_panel_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 passive=true interactive=true st77922=true three_layer=true file=cp7-panel-evidence.md",
        "cp7_lifecycle_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 conversation_mode_restored=true idle_face_restored=true",
        "cp8_alarm_snapshot device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 p95_ms=842 alarm_active=false reset_available=true source=internal_lesson_runtime_preload_voice_alarm",
        "cp7_render_fetch_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 render_latency_ms=118 audio_glitch=0 decode_drop=0 encode_drop=0 stale_frames=0 interrupts=0",
    ]

def seed_manifest_line(steps: list[dict[str, str]]) -> str:
    return "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft " + json.dumps(
        {"steps": steps, "totalSteps": len(steps)},
        separators=(",", ":"),
    )


def three_layer_scene(step_id: str) -> dict:
    return {
        "backgroundScene": {"poster": {"src": f"https://ota.example/poster-{step_id}.jpg"}},
        "teachingObject": {
            "asset": {"src": f"https://ota.example/object-{step_id}.png"},
            "subject": {"primaryWord": "barn"},
        },
        "robotOverlay": {
            "asset": {"src": f"https://ota.example/overlay-{step_id}.png"},
            "robotState": "talking",
        },
    }


def lesson_step_frame(
    *,
    step_id: str = "s4",
    scene: dict | None = None,
    include_step_id: bool = True,
    pretty: bool = False,
) -> str:
    frame = {
        "type": "lesson_step",
        "assignmentId": "assign-1",
        "sessionId": "sess-1",
        "sequence": 3,
        "body": {"scene": scene if scene is not None else three_layer_scene(step_id)},
    }
    if include_step_id:
        frame["stepId"] = step_id
    return json.dumps(frame, indent=2 if pretty else None, separators=None if pretty else (",", ":"))

def one_step_flow_lines(*, manifest_line: str, step_id: str = "s4", include_child_response: bool = False) -> list[str]:
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        manifest_line,
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        lesson_step_frame(step_id=step_id),
        f"LessonRuntime event step_started assignmentId=assign-1 stepId={step_id}",
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} lesson_step poster fetched+drawn from URL url=https://ota.example/poster-{step_id}.jpg",
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} teachingObject rendered primaryWord=barn",
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} robotOverlay rendered robotState=talking pose=teach",
        f'{{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"{step_id}","body":{{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}}}',
        f"LessonRuntime step prompt stepId={step_id} text=Can you say {step_id} with TeeBot?",
        f"serial Audio TTS played stepId={step_id} primaryWord=barn",
    ]
    if include_child_response:
        lines.append(f"LessonRuntime child response window opened stepId={step_id} listening=true")
        lines.append(f"serial interactive child response accepted stepId={step_id} recognizedText=barn")
    lines.extend(
        [
            f'{{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"{step_id}","body":{{"event":"step_completed","result":"success"}}}}',
            f"backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId={step_id} event=step_completed result=success persisted=true",
            '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
            "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
            "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
            "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
            "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
        ]
    )
    return lines

def test_lesson_e2e_log_verify_rejects_oversized_lesson_prepare_frame():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": ["poster"],
                "assetPack": {
                    "ready": False,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": f"backgroundScene.poster.{index}",
                            "localPath": f"sd://sdcard/tbot/lesson-assets/poster-{index}.jpg",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        }
                        for index in range(900)
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_wire_frame_size_budget"]["ok"] is False
    assert "lesson_prepare" in checks["lesson_wire_frame_size_budget"]["evidence"]

def test_lesson_e2e_log_verify_rejects_inline_media_inside_asset_pack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                            "imageData": "data:image/jpeg;base64,AAAA",
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = (
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=1 "
        "body.acks=1 rendered=false assetPack.ready=true cacheKey=w01-d01-barn-say-it/v3-abcdef12"
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_no_inline_media_payloads"]["ok"] is False
    assert "backgroundscene.poster:imageData" in checks[
        "lesson_asset_pack_no_inline_media_payloads"
    ]["evidence"]


def test_lesson_e2e_log_verify_rejects_ready_asset_pack_missing_layer_groups():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": ["backgroundScene.poster"],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        }
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = (
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=1 "
        "body.acks=1 rendered=false assetPack.ready=true cacheKey=w01-d01-barn-say-it/v3-abcdef12"
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_required_layer_groups"]["ok"] is False
    assert "missing_groups=robotOverlay,teachingObject" in checks[
        "lesson_asset_pack_required_layer_groups"
    ]["evidence"]


def test_lesson_e2e_log_verify_rejects_backend_post_alias_as_robot_runtime_evidence():
    module = load_module()

    assert module._lesson_start_requested(
        'backend_post start_lesson intent handled=true text="bat dau bai hoc" persisted=true'
    ) is False
    assert module._lesson_start_acknowledged(
        "backend_post tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 persisted=true"
    ) is False
    assert module._lesson_preload_ready(
        "backend_post preload_ready assignmentId=assign-1 criticalAssets=ready persisted=true"
    ) is False
    assert module._lesson_preload_ready(
        "backend_post [LessonRuntime]-INFO-lesson_preload_ready "
        "assignmentId=assign-1 assetCount=3 downloadedCount=3 skippedCount=0 failedCount=0"
    ) is False
    assert module._lesson_started(
        "backend_post lesson_started assignmentId=assign-1 started=true persisted=true"
    ) is False
    assert module._lesson_step_started(
        "backend_post step_started assignmentId=assign-1 stepId=s1 started=true persisted=true"
    ) is False
    assert module._background_rendered(
        "backend_post background rendered stepId=s1 url=https://ota.example/poster.jpg persisted=true"
    ) is False
    assert module._lesson_content_rendered(
        "backend_post teachingObject rendered stepId=s1 primaryWord=barn persisted=true"
    ) is False
    assert module._robot_overlay_rendered(
        "backend_post robotOverlay rendered stepId=s1 robotState=talking persisted=true"
    ) is False
    assert module._lesson_audio_played(
        "backend_post tts playback complete stepId=s1 primaryWord=barn bytes=4096 duration_ms=900 persisted=true"
    ) is False
    assert module._lesson_step_rendered_ack(
        "backend_post lesson_ack assignmentId=assign-1 stepId=s1 body.acks=3 rendered=true robotState=talking persisted=true"
    ) is False
    assert module._lesson_ack_positive(1)(
        "backend_post lesson_ack assignmentId=assign-1 body.acks=1 rendered=false persisted=true"
    ) is False
    assert module._lesson_start_requested(
        'backend.post start_lesson intent handled=true text="bat dau bai hoc" persisted=true'
    ) is False
    assert module._lesson_audio_played(
        "backend: tts playback complete stepId=s1 primaryWord=barn bytes=4096 duration_ms=900 persisted=true"
    ) is False
    assert module._lesson_started(
        "server.send lesson_started assignmentId=assign-1 started=true persisted=true"
    ) is False


def test_lesson_e2e_log_verify_accepts_server_timeline_transport_for_runtime_preload_owner():
    module = load_module()

    assert module._lesson_preload_ready(
        "1784106112240633000 server "
        "260715 17:01:52[0.9.3_00000000000000][LessonRuntime]-INFO-"
        "lesson_preload_ready cacheKey=pip-farm-3m/v1-abc assetCount=20 "
        "downloadedCount=20 skippedCount=0 failedCount=0 durationMs=315 "
        "assignment_id=assign-1 session_id=sess-1"
    ) is True


def test_lesson_e2e_log_verify_requires_cp7_sidecar_evidence_for_hardware_completion():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["cp7_panel_sidecar_evidence"]["ok"] is False
    assert checks["cp7_conversation_idle_sidecar_evidence"]["ok"] is False
    assert checks["cp8_alarm_snapshot_sidecar_evidence"]["ok"] is False
    assert checks["render_latency_audio_sidecar_evidence"]["ok"] is False

def test_lesson_e2e_log_verify_accepts_cp7_sidecar_evidence_markers():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.extend(
        [
            "cp7_panel_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 passive=true interactive=true st77922=true three_layer=true file=cp7-panel-evidence.md",
            "cp7_lifecycle_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 conversation_mode_restored=true idle_face_restored=true",
            "cp8_alarm_snapshot device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 p95_ms=842 alarm_active=false reset_available=true source=internal_lesson_runtime_preload_voice_alarm",
            "cp7_render_fetch_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 render_latency_ms=118 audio_glitch=0 decode_drop=0 encode_drop=0 stale_frames=0 interrupts=0",
        ]
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert checks["cp7_panel_sidecar_evidence"]["ok"] is True
    assert checks["cp7_conversation_idle_sidecar_evidence"]["ok"] is True
    assert checks["cp8_alarm_snapshot_sidecar_evidence"]["ok"] is True
    assert checks["render_latency_audio_sidecar_evidence"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_cp7_logs_with_unredacted_secrets():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.extend(
        [
            "cp7_panel_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 passive=true interactive=true st77922=true three_layer=true file=cp7-panel-evidence.md",
            "cp7_lifecycle_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 conversation_mode_restored=true idle_face_restored=true",
            "cp8_alarm_snapshot device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 p95_ms=842 alarm_active=false reset_available=true source=internal_lesson_runtime_preload_voice_alarm",
            "cp7_render_fetch_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-1 sessionId=sess-1 render_latency_ms=118 audio_glitch=0 decode_drop=0 encode_drop=0 stale_frames=0 interrupts=0",
            "backend ingest Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwYXJlbnQifQ.sig parent=parent@example.com",
        ]
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    dumped = json.dumps(report)
    assert report["ok"] is False
    assert checks["cp7_log_secret_scrub"]["ok"] is False
    assert "Authorization" not in dumped
    assert "eyJhbGciOiJIUzI1NiJ9" not in dumped
    assert "parent@example.com" not in dumped
    assert "<redacted>" in dumped

def test_lesson_e2e_log_verify_rejects_cp7_api_key_header_and_query_secret_markers():
    module = load_module()
    leaks = [
        ("render fetch x-api-key: live_api_key_123456789", "live_api_key_123456789"),
        ("backend ingest api_key=live_api_key_123456789", "live_api_key_123456789"),
        ("backend ingest Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ]

    for leak_line, secret_value in leaks:
        lines = one_step_flow_lines(
            manifest_line=seed_manifest_line(
                [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ]
            ),
            include_child_response=True,
        )
        lines.extend(cp7_valid_sidecar_lines())
        lines.append(leak_line)

        report = module.evaluate_lesson_logs(
            lines,
            device_id="14:c1:9f:d1:a8:48",
            require_cp7_sidecar_evidence=True,
        )

        checks = by_name(report)
        dumped = json.dumps(report)
        assert report["ok"] is False
        assert checks["cp7_log_secret_scrub"]["ok"] is False
        assert secret_value not in dumped
        assert "<redacted>" in dumped

def test_lesson_e2e_log_verify_accepts_redacted_cp7_credential_markers():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.extend(cp7_valid_sidecar_lines())
    lines.extend(
        [
            "backend ingest Authorization: Bearer <redacted>",
            "backend ingest Authorization: Basic <redacted>",
            "render fetch x-api-key: <redacted>",
            "backend ingest api_key=<redacted>",
            "backend ingest Cookie: <redacted>",
            "backend ingest access_token=<redacted>",
        ]
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["cp7_log_secret_scrub"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_cp7_sidecar_markers_for_wrong_device():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.extend(
        [
            "cp7_panel_evidence device_id=wrong-device assignmentId=assign-1 sessionId=sess-1 passive=true interactive=true st77922=true three_layer=true file=cp7-panel-evidence.md",
            "cp7_lifecycle_evidence device_id=wrong-device assignmentId=assign-1 sessionId=sess-1 conversation_mode_restored=true idle_face_restored=true",
            "cp8_alarm_snapshot device_id=wrong-device assignmentId=assign-1 sessionId=sess-1 p95_ms=842 alarm_active=false reset_available=true source=internal_lesson_runtime_preload_voice_alarm",
            "cp7_render_fetch_evidence device_id=wrong-device assignmentId=assign-1 sessionId=sess-1 render_latency_ms=118 audio_glitch=0 decode_drop=0 encode_drop=0 stale_frames=0 interrupts=0",
        ]
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["cp7_panel_sidecar_evidence"]["ok"] is False
    assert checks["cp7_conversation_idle_sidecar_evidence"]["ok"] is False
    assert checks["cp8_alarm_snapshot_sidecar_evidence"]["ok"] is False
    assert checks["render_latency_audio_sidecar_evidence"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_stale_cp7_sidecar_markers_for_same_device():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.extend(
        [
            "cp7_panel_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-old sessionId=sess-old passive=true interactive=true st77922=true three_layer=true file=cp7-panel-evidence.md",
            "cp7_lifecycle_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-old sessionId=sess-old conversation_mode_restored=true idle_face_restored=true",
            "cp8_alarm_snapshot device_id=14:c1:9f:d1:a8:48 assignmentId=assign-old sessionId=sess-old p95_ms=842 alarm_active=false reset_available=true source=internal_lesson_runtime_preload_voice_alarm",
            "cp7_render_fetch_evidence device_id=14:c1:9f:d1:a8:48 assignmentId=assign-old sessionId=sess-old render_latency_ms=118 audio_glitch=0 decode_drop=0 encode_drop=0 stale_frames=0 interrupts=0",
        ]
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        require_cp7_sidecar_evidence=True,
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["cp7_panel_sidecar_evidence"]["ok"] is False
    assert checks["cp7_conversation_idle_sidecar_evidence"]["ok"] is False
    assert checks["cp8_alarm_snapshot_sidecar_evidence"]["ok"] is False
    assert checks["render_latency_audio_sidecar_evidence"]["ok"] is False

def test_lesson_audio_played_does_not_count_start_lesson_ack_audio():
    module = load_module()

    assert module._lesson_audio_played(
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900"
    ) is False
    assert module._lesson_audio_played(
        "serial Audio TTS played stepId=s1 primaryWord=barn"
    ) is True


def test_lesson_e2e_log_verify_rejects_immediate_pronunciation_scoring_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(
        19,
        "LessonRuntime prompt stepId=s4 text=Say barn now so TeeBot can give you a pronunciation score.",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "pronunciation" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]


def test_lesson_e2e_log_verify_rejects_generic_child_response_scoring_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(19, "LessonRuntime child response feedback stepId=s4 score=23 correct=false grade=F")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "score" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]


def test_lesson_e2e_log_verify_rejects_split_line_child_response_scoring_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.insert(
        lines.index(response_line) + 1,
        "LessonRuntime feedback stepId=s4 score=23 correct=false grade=F",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "score" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_correction_after_child_response():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.insert(lines.index(response_line) + 1, "LessonRuntime prompt stepId=s4 text=Sai rồi, con nói lại nhé.")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "Sai rồi" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]


def test_lesson_e2e_log_verify_rejects_english_correction_after_child_response():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.insert(
        lines.index(response_line) + 1,
        "LessonRuntime prompt stepId=s4 text=That was wrong, try again.",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "wrong" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_pronunciation_quality_after_child_response():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.insert(
        lines.index(response_line) + 1,
        "LessonRuntime prompt stepId=s4 text=Con phát âm chưa chuẩn, mình nói lại nhé.",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "phát âm chưa chuẩn" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]


def test_lesson_e2e_log_verify_rejects_vietnamese_positive_pronunciation_quality_after_child_response():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.insert(
        lines.index(response_line) + 1,
        "LessonRuntime prompt stepId=s4 text=Con phát âm chuẩn rồi, giỏi lắm.",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_no_immediate_pronunciation_scoring"]["ok"] is False
    assert "phát âm chuẩn rồi" in checks["lesson_no_immediate_pronunciation_scoring"]["evidence"]


def test_lesson_e2e_log_verify_rejects_cancelled_robot_progress_as_step_completed():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._robot_lesson_progress_success(
            f"serial TX lesson_progress event=step_completed result=success stepId=s1 {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_robot_progress_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._robot_lesson_progress_success(
            f"serial TX lesson_progress event=step_completed result=success stepId=s1 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_aborted_rendered_step_ack():
    module = load_module()

    for marker in ("aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_step_rendered_ack(
            f"serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_rendered_step_ack_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_step_rendered_ack(
            f"serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_invisible_rendered_step_ack():
    module = load_module()

    for marker in ("visible=false", "displayed=false", "visible false", '"visible":false'):
        assert module._lesson_step_rendered_ack(
            f"serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_false_backend_completion_payload_as_posted():
    module = load_module()

    for marker in ("completed=false", "complete=false", "success=false"):
        assert module._backend_completion_posted(
            f"backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker} persisted=true status=200"
        ) is False


def test_lesson_e2e_log_verify_rejects_backend_completion_numeric_false_payload_as_posted():
    module = load_module()

    for marker in (
        "completed=0",
        '"completed":0',
        "complete=0",
        '"complete":0',
        "success=0",
        '"success":0',
        "accepted=0",
        '"accepted":0',
    ):
        assert module._backend_completion_posted(
            f"backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker} persisted=true status=200"
        ) is False

def test_lesson_e2e_log_verify_rejects_cancelled_backend_completion_payload_as_posted():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._backend_completion_posted(
            f"backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker} persisted=true status=200"
        ) is False

def test_lesson_e2e_log_verify_rejects_backend_completion_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._backend_completion_posted(
            f"backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker} persisted=true status=200"
        ) is False

def test_lesson_e2e_log_verify_rejects_false_assignment_completion_payload():
    module = load_module()

    for marker in ("persisted=false", "finalized=false", "accepted=false"):
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_assignment_completion_numeric_false_payload():
    module = load_module()

    for marker in (
        "completed=0",
        '"completed":0',
        "complete=0",
        '"complete":0',
        "success=0",
        '"success":0',
        "persisted=0",
        '"persisted":0',
        "finalized=0",
        '"finalized":0',
        "accepted=0",
        '"accepted":0',
    ):
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_assignment_completion_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_assignment_completion_numeric_true_terminal_flags():
    module = load_module()

    markers = (
        "archived=1",
        '"archived":1',
        "expired=1",
        '"expired":1',
    )

    for marker in markers:
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_assignment_completion_numeric_true_cache_flags():
    module = load_module()

    markers = (
        "cached=1",
        '"cached":1',
        "cache_hit=1",
        '"cache_hit":1',
        "stale=1",
        '"stale":1',
        "offline=1",
        '"offline":1',
    )

    for marker in markers:
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_inactive_assignment_current_payload():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "archived=true", "expired=true", "completed=true"):
        assert module._active_assignment_current(
            f"assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_invalid_active_assignment_current_payload():
    module = load_module()

    markers = (
        "deleted=true",
        '"deleted":true',
        "deleted=1",
        '"deleted":1',
        "removed=true",
        '"removed":true',
        "removed=1",
        '"removed":1',
        "revoked=true",
        '"revoked":true',
        "revoked=1",
        '"revoked":1',
        "disabled=true",
        '"disabled":true',
        "disabled=1",
        '"disabled":1',
        "valid=false",
        '"valid":false',
        "valid=0",
        '"valid":0',
    )

    for marker in markers:
        assert module._active_assignment_current(
            f"assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_active_assignment_numeric_true_terminal_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "archived=1",
        '"archived":1',
        "expired=1",
        '"expired":1',
        "completed=1",
        '"completed":1',
    )

    for marker in markers:
        assert module._active_assignment_current(
            f"assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_active_assignment_numeric_true_cache_flags():
    module = load_module()

    markers = (
        "cached=1",
        '"cached":1',
        "cache_hit=1",
        '"cache_hit":1',
        "stale=1",
        '"stale":1',
        "offline=1",
        '"offline":1',
    )

    for marker in markers:
        assert module._active_assignment_current(
            f"assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_active_assignment_numeric_false_flags():
    module = load_module()

    for marker in ("assigned=0", '"assigned":0', "active=0", '"active":0', "available=0", '"available":0'):
        assert module._active_assignment_current(
            f"assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_terminal_assignment_completed_payload():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "archived=true", "expired=true"):
        assert module._assignment_completed(
            f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"
        ) is False


def test_lesson_e2e_log_verify_requires_robot_boot_before_websocket():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_booted"]["ok"] is False
    assert checks["websocket_connected"]["ok"] is True

def test_lesson_e2e_log_verify_requires_wifi_ip_before_websocket():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-real-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_booted"]["ok"] is True
    assert checks["wifi_connected"]["ok"] is False
    assert checks["websocket_connected"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_false_wifi_connected_flag():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected=false ssid=Van_Phong_Tam_Dentist ip=0.0.0.0",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "LessonRuntime child response window opened stepId=s4 listening=true",
        "serial interactive child response accepted stepId=s4 recognizedText=barn",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_booted"]["ok"] is True
    assert checks["wifi_connected"]["ok"] is False
    assert checks["websocket_connected"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_failed_websocket_session_as_connected():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket connect failed device_id=14:c1:9f:d1:a8:48 session=sess-1 error=server_unavailable",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["wifi_connected"]["ok"] is True
    assert checks["websocket_connected"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_websocket_opening_session_as_connected():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket session pending device_id=14:c1:9f:d1:a8:48 session=sess-1 state=opening handshake=pending",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["wifi_connected"]["ok"] is True
    assert checks["websocket_connected"]["ok"] is False

def test_lesson_e2e_log_verify_accepts_complete_server_and_serial_flow():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "LessonRuntime step prompt stepId=s4 text=Can you say barn with TeeBot?",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "LessonRuntime child response window opened stepId=s4 listening=true",
        "serial interactive child response accepted stepId=s4 recognizedText=barn",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    assert report["ok"] is True
    checks = by_name(report)
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_completed"]["ok"] is True
    assert "secret-token" not in json.dumps(report)
    assert "<redacted>" in json.dumps(report)


def test_lesson_e2e_log_verify_rejects_interactive_completion_without_child_response():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 completionClass=interactive backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_manifest_steps_without_completion_class():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say"}]),
        step_id="s4",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_completion_classes"]["ok"] is False
    assert "missing_completionClass=s4" in checks["lesson_manifest_completion_classes"]["evidence"]

def test_lesson_e2e_log_verify_rejects_totalsteps_only_manifest_without_steps_array():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        step_id="s4",
        include_child_response=True,
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_step_ids"]["ok"] is False
    assert checks["lesson_manifest_completion_classes"]["ok"] is False
    assert "manifest_steps=not_declared" in checks["lesson_manifest_step_ids"]["evidence"]
    assert "manifest_completionClass=not_declared" in checks["lesson_manifest_completion_classes"]["evidence"]

def test_lesson_e2e_log_verify_uses_manifest_completion_class_for_child_response_gate():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "completionClass": "interactive"}]),
        step_id="s4",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_completion_classes"]["ok"] is True
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "interactive=s4" in checks["interactive_child_response_observed"]["evidence"]
    assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_interactive_step_without_story_prompt_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    lines = [line for line in lines if "LessonRuntime step prompt" not in line]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_prompt_after_frame"]["ok"] is False
    assert "missing_prompt=s4" in checks["lesson_step_prompt_after_frame"]["evidence"]

def test_lesson_e2e_log_verify_rejects_failed_story_prompt_handoff_as_prompt_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    lines = [
        "LessonRuntime lesson step prompt handoff stepId=s4 handoff=0"
        if "LessonRuntime step prompt" in line
        else line
        for line in lines
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_prompt_after_frame"]["ok"] is False
    assert "missing_prompt=s4" in checks["lesson_step_prompt_after_frame"]["evidence"]

def test_lesson_e2e_log_verify_rejects_child_response_before_robot_prompt_turn():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
    )
    lines.insert(17, "serial interactive child response accepted stepId=s4 recognizedText=barn")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_ordered"]["ok"] is False
    assert "response_before_robot_turn=s4" in checks["interactive_child_response_ordered"]["evidence"]


def test_lesson_e2e_log_verify_rejects_child_response_without_open_window():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    lines = [line for line in lines if "child response window opened" not in line.lower()]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "missing_window=s4" in checks["interactive_child_response_window_opened"]["evidence"]

def test_lesson_e2e_log_verify_accepts_google_live_prompt_and_window_without_step_id_in_current_step_context():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    lines = [
        "Google Live lesson_step_prompt queued via live text='Can you say barn with TeeBot?'"
        if "LessonRuntime step prompt stepId=s4" in line
        else "Google Live user_audio_window_open reason=lesson_child_response window_ms=25000"
        if "LessonRuntime child response window opened stepId=s4" in line
        else line
        for line in lines
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_step_prompt_after_frame"]["ok"] is True
    assert checks["lesson_step_prompt_after_render_ack"]["ok"] is True
    assert checks["interactive_guided_prompt"]["ok"] is True
    assert checks["interactive_child_response_window_opened"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_child_response_before_open_window():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_index = lines.index("serial interactive child response accepted stepId=s4 recognizedText=barn")
    window_index = lines.index("LessonRuntime child response window opened stepId=s4 listening=true")
    lines[response_index], lines[window_index] = lines[window_index], lines[response_index]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "window_after_response=s4" in checks["interactive_child_response_window_opened"]["evidence"]

def test_lesson_e2e_log_verify_rejects_response_window_before_robot_prompt_turn():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    window_line = "LessonRuntime child response window opened stepId=s4 listening=true"
    lines.remove(window_line)
    lines.insert(17, window_line)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "window_before_robot_turn=s4" in checks["interactive_child_response_window_opened"]["evidence"]

def test_lesson_e2e_log_verify_rejects_child_response_from_different_assignment():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    response_index = lines.index(response_line)
    lines[response_index] = "serial interactive child response accepted assignmentId=assign-old sessionId=sess-1 stepId=s4 recognizedText=barn"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_consistent"]["ok"] is False
    assert "assign-1" in checks["assignment_consistent"]["evidence"]
    assert "assign-old" in checks["assignment_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_response_window_from_different_session():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    window_line = "LessonRuntime child response window opened stepId=s4 listening=true"
    window_index = lines.index(window_line)
    lines[window_index] = "LessonRuntime child response window opened assignmentId=assign-1 sessionId=sess-old stepId=s4 listening=true"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["session_consistent"]["ok"] is False
    assert "sess-1" in checks["session_consistent"]["evidence"]
    assert "sess-old" in checks["session_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_response_window_without_open_or_ready_state():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    window_line = "LessonRuntime child response window opened stepId=s4 listening=true"
    window_index = lines.index(window_line)
    lines[window_index] = "LessonRuntime interactive child response window stepId=s4"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "missing_window=s4" in checks["interactive_child_response_window_opened"]["evidence"]

def test_lesson_e2e_log_verify_rejects_response_window_with_false_open_or_ready_state():
    module = load_module()
    false_cases = (
        ("LessonRuntime lesson interactive listening ready stepId=s4 {flag}", "ready=false"),
        ("LessonRuntime lesson interactive listening ready stepId=s4 {flag}", '"ready":false'),
        ("LessonRuntime lesson interactive listening ready stepId=s4 {flag}", "ready=0"),
        ("LessonRuntime lesson interactive listening ready stepId=s4 {flag}", '"opened":0'),
        ("LessonRuntime lesson/manual listening rearm stepId=s4 {flag}", "rearm=false"),
        ("LessonRuntime lesson/manual listening rearm stepId=s4 {flag}", '"rearm":0'),
    )
    for template, false_state in false_cases:
        lines = one_step_flow_lines(
            manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
            step_id="s4",
            include_child_response=True,
        )
        window_line = "LessonRuntime child response window opened stepId=s4 listening=true"
        window_index = lines.index(window_line)
        lines[window_index] = template.format(flag=false_state)

        report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

        checks = by_name(report)
        assert report["ok"] is False, false_state
        assert checks["interactive_child_response_window_opened"]["ok"] is False, false_state
        assert "missing_window=s4" in checks["interactive_child_response_window_opened"]["evidence"], false_state

def test_lesson_e2e_log_verify_rejects_child_response_without_observable_input():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    response_index = lines.index(response_line)
    lines[response_index] = "serial interactive child response accepted stepId=s4"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_voice_transcript_source_without_input_text():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    response_index = lines.index(response_line)
    lines[response_index] = "serial interactive child response accepted stepId=s4 source=voice_transcript"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_placeholder_child_response_input():
    module = load_module()
    for placeholder in (
        "unknown",
        "unrecognized",
        "[noise]",
        "[inaudible]",
        "silence",
        "no_speech",
        "no-speech",
        "...",
        "???",
        "!!!",
        "---",
        "<unk>",
        "unknown.",
        "[noise].",
        "n/a",
        "na",
        "0",
        "false",
    ):
        lines = one_step_flow_lines(
            manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
            step_id="s4",
            include_child_response=True,
        )
        response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
        response_index = lines.index(response_line)
        lines[response_index] = f"serial interactive child response accepted stepId=s4 recognizedText={placeholder}"

        report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

        checks = by_name(report)
        assert report["ok"] is False, placeholder
        assert checks["interactive_child_response_observed"]["ok"] is False, placeholder
        assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"], placeholder

def test_lesson_e2e_log_verify_rejects_contradictory_child_response_recognition_evidence():
    module = load_module()
    for contradiction in (
        "recognized=false",
        "recognized = false",
        "recognized false",
        '"recognized":false',
        '"recognized": false',
        "recognized=0",
        "recognized = 0",
        '"recognized":0',
        '"recognized": 0',
        "accepted=0",
        '"accepted":0',
        '"accepted": 0',
        "confidence=0",
        '"confidence":0',
        "asrConfidence=0.0",
        "asr_confidence=0",
        "confidence=-0.1",
        "asrConfidence=-1",
        "confidence=nan",
        "confidence=inf",
        "confidence=-inf",
        "confidence=abc",
        "confidence=",
        "confidence=,",
        'confidence="NaN"',
        "asrConfidence=NaN",
        '"asr_confidence":"abc"',
        "rejected=true",
        "rejected = true",
        "rejected true",
        '"rejected":true',
        '"rejected": true',
        "rejected=1",
        '"rejected":1',
        '"rejected": 1',
    ):
        lines = one_step_flow_lines(
            manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
            step_id="s4",
            include_child_response=True,
        )
        response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
        response_index = lines.index(response_line)
        lines[response_index] = f"serial interactive child response accepted stepId=s4 recognizedText=barn {contradiction}"

        report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

        checks = by_name(report)
        assert report["ok"] is False, contradiction
        assert checks["interactive_child_response_observed"]["ok"] is False, contradiction
        assert "missing_child_response=s4" in checks["interactive_child_response_observed"]["evidence"], contradiction

def test_lesson_e2e_log_verify_accepts_positive_finite_confidence_and_unrelated_fields():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    response_index = lines.index(response_line)
    lines[response_index] = (
        "serial interactive child response accepted stepId=s4 recognizedText=barn "
        "confidence=0.91 asrConfidence=0.88 asr_confidence=0.87 no_confidence=abc"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["interactive_child_response_observed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_passive_step_opening_child_response_window():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s2", "type": "review", "completionClass": "passive"}]),
        step_id="s2",
    )
    progress_index = next(index for index, line in enumerate(lines) if '"type":"lesson_progress"' in line)
    lines.insert(progress_index, "LessonRuntime child response window opened stepId=s2 listening=true")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "passive_window=s2" in checks["interactive_child_response_window_opened"]["evidence"]

def test_lesson_e2e_log_verify_rejects_passive_step_with_child_response_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s2", "type": "review", "completionClass": "passive"}]),
        step_id="s2",
    )
    progress_index = next(index for index, line in enumerate(lines) if '"type":"lesson_progress"' in line)
    lines.insert(progress_index, "serial interactive child response accepted stepId=s2 recognizedText=barn")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "passive_response=s2" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_conversation_flow_with_no_interactive_steps():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s2", "type": "review", "completionClass": "passive"}]),
        step_id="s2",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_observed"]["ok"] is False
    assert "interactive=none" in checks["interactive_child_response_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_cancelled_prepare_and_start_ack():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_ack_positive(1)(
            f"serial TX lesson_ack body.acks=1 rendered=false {marker}"
        ) is False
        assert module._lesson_ack_positive(2)(
            f"serial TX lesson_ack body.acks=2 rendered=false {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_prepare_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false cancelled=true",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True
    assert checks["lesson_prepare_ack"]["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is True
    assert checks["lesson_step_ack"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_flow_with_aborted_rendered_step_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4 aborted=true",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is True

def test_lesson_e2e_log_verify_cli_rejects_unexpected_lesson_id(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            [
                "I (100) Application: TBOT firmware boot complete",
                "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
                "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
                "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
                "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
                "tts playback complete reason=start_lesson_ack",
                "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
                "LessonRuntime manifest fetched lesson=lesson-b profile=espTft",
                "server send lesson_prepare assignmentId=assign-1 sequence=1",
                "serial RX lesson_prepare seq=1",
                "serial TX lesson_ack body.acks=1 rendered=false",
                "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
                "server send lesson_start assignmentId=assign-1 sequence=2",
                "serial RX lesson_start seq=2",
                "serial TX lesson_ack body.acks=2 rendered=false",
                "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
                "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
                "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
                "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
                "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
                "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
                "serial Audio TTS played stepId=s4",
                "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
                "serial TX lesson_progress event=step_completed result=success stepId=s4",
                "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
                "server send lesson_stop assignmentId=assign-1 sequence=4",
                "serial RX lesson_stop seq=4",
                "LessonRuntime event lesson_completed assignmentId=assign-1",
                "backend post lesson_completed assignmentId=assign-1",
                "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-lesson-id",
            "lesson-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_lesson_identity"]["ok"] is False
    assert "expected=lesson-a" in checks["expected_lesson_identity"]["evidence"]
    assert "observed=lesson-b" in checks["expected_lesson_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_rejects_unexpected_course_id(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            [
                "I (100) Application: TBOT firmware boot complete",
                "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
                "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
                "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
                "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
                "tts playback complete reason=start_lesson_ack",
                "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
                "LessonRuntime manifest fetched lesson=lesson-a course=course-b profile=espTft",
                "server send lesson_prepare assignmentId=assign-1 sequence=1",
                "serial RX lesson_prepare seq=1",
                "serial TX lesson_ack body.acks=1 rendered=false",
                "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
                "server send lesson_start assignmentId=assign-1 sequence=2",
                "serial RX lesson_start seq=2",
                "serial TX lesson_ack body.acks=2 rendered=false",
                "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
                "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
                "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
                "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
                "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
                "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
                "serial Audio TTS played stepId=s4",
                "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
                "serial TX lesson_progress event=step_completed result=success stepId=s4",
                "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
                "server send lesson_stop assignmentId=assign-1 sequence=4",
                "serial RX lesson_stop seq=4",
                "LessonRuntime event lesson_completed assignmentId=assign-1",
                "backend post lesson_completed assignmentId=assign-1",
                "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-course-id",
            "course-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_course_identity"]["ok"] is False
    assert "expected=course-a" in checks["expected_course_identity"]["evidence"]
    assert "observed=course-b" in checks["expected_course_identity"]["evidence"]

def test_lesson_e2e_log_verify_accepts_expected_backend_url_when_capture_mentions_it():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines.insert(5, "config lesson api_base=https://tbot-backend-8wmh.onrender.com/v1")

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_backend_url="https://tbot-backend-8wmh.onrender.com/v1",
    )

    checks = by_name(report)
    assert checks["expected_backend_url"]["ok"] is True
    assert "tbot-backend-8wmh.onrender.com/v1" in checks["expected_backend_url"]["evidence"]

def test_lesson_e2e_log_verify_accepts_expected_backend_url_inside_full_endpoint():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines.insert(
        5,
        "assignment/current GET https://tbot-backend-8wmh.onrender.com/v1/devices/14:c1/assignments/current?active=1",
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_backend_url="https://tbot-backend-8wmh.onrender.com/v1",
    )

    checks = by_name(report)
    assert checks["expected_backend_url"]["ok"] is True
    assert "tbot-backend-8wmh.onrender.com/v1/devices" in checks["expected_backend_url"]["evidence"]

def test_lesson_e2e_log_verify_accepts_json_escaped_expected_backend_url():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines.insert(
        5,
        '{"api_base":"https:\\/\\/tbot-backend-8wmh.onrender.com\\/v1","event":"assignment_current"}',
    )

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_backend_url="https://tbot-backend-8wmh.onrender.com/v1",
    )

    checks = by_name(report)
    assert checks["expected_backend_url"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_unexpected_backend_url():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines.insert(5, "config lesson api_base=https://staging.example.com/v1")

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_backend_url="https://tbot-backend-8wmh.onrender.com/v1",
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["expected_backend_url"]["ok"] is False
    assert "expected=https://tbot-backend-8wmh.onrender.com/v1" in checks["expected_backend_url"]["evidence"]
    assert "observed=https://staging.example.com/v1" in checks["expected_backend_url"]["evidence"]


def test_lesson_e2e_log_verify_rejects_asset_cache_url_as_backend_evidence():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines[6] += " assetUrl=https://assets.example.com/pip-farm-3m/v1-deadbeef/poster.jpg"

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_backend_url="https://assets.example.com",
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["expected_backend_url"]["ok"] is False
    assert "observed=none" in checks["expected_backend_url"]["evidence"]


def test_lesson_e2e_log_verify_accepts_expected_course_from_authoritative_manifest():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines = [line.replace(" courseId=course-1", "") for line in lines]

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_course_id="course-1",
    )

    checks = by_name(report)
    assert checks["expected_course_identity"]["ok"] is True
    assert "manifest=course-1" in checks["expected_course_identity"]["evidence"]
    assert "assignment_current=none" in checks["expected_course_identity"]["evidence"]


def test_lesson_e2e_log_verify_rejects_assignment_course_conflicting_with_manifest():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines = [line.replace("courseId=course-1", "courseId=course-wrong") for line in lines]

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_course_id="course-1",
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["expected_course_identity"]["ok"] is False
    assert "manifest=course-1" in checks["expected_course_identity"]["evidence"]
    assert "assignment_current=course-wrong" in checks["expected_course_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_requires_assignment_current_lesson_identity(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            [
                "I (100) Application: TBOT firmware boot complete",
                "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
                "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
                "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
                "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
                "tts playback complete reason=start_lesson_ack",
                "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
                "LessonRuntime manifest fetched lesson=lesson-a profile=espTft",
                "server send lesson_prepare assignmentId=assign-1 sequence=1",
                "serial RX lesson_prepare seq=1",
                "serial TX lesson_ack body.acks=1 rendered=false",
                "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
                "server send lesson_start assignmentId=assign-1 sequence=2",
                "serial RX lesson_start seq=2",
                "serial TX lesson_ack body.acks=2 rendered=false",
                "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
                "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
                "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
                "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
                "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
                "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
                "serial Audio TTS played stepId=s4",
                "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
                "serial TX lesson_progress event=step_completed result=success stepId=s4",
                "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
                "server send lesson_stop assignmentId=assign-1 sequence=4",
                "serial RX lesson_stop seq=4",
                "LessonRuntime event lesson_completed assignmentId=assign-1",
                "backend post lesson_completed assignmentId=assign-1",
                "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-lesson-id",
            "lesson-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_lesson_identity"]["ok"] is False
    assert "assignment_current=none" in checks["expected_lesson_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_accepts_manifest_course_when_assignment_omits_it(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            line.replace(" courseId=course-a", "")
            for line in _live01_assignment_lines(assignment_extra=" lessonVersion=7")
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-course-id",
            "course-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_course_identity"]["ok"] is True
    assert "manifest=course-a" in checks["expected_course_identity"]["evidence"]
    assert "assignment_current=none" in checks["expected_course_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_accepts_expected_lesson_and_course_from_assignment_current(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            [
                "I (100) Application: TBOT firmware boot complete",
                "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
                "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
                "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
                "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
                "tts playback complete reason=start_lesson_ack",
                "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-a state=ASSIGNED",
                'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
                "server send lesson_prepare assignmentId=assign-1 sequence=1",
                "serial RX lesson_prepare seq=1",
                "serial TX lesson_ack body.acks=1 rendered=false",
                "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
                "server send lesson_start assignmentId=assign-1 sequence=2",
                "serial RX lesson_start seq=2",
                "serial TX lesson_ack body.acks=2 rendered=false",
                "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
                "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
                "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
                "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
                "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
                "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
                "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
                "LessonRuntime step prompt stepId=s4 text=Can you say barn with TeeBot?",
                "serial Audio TTS played stepId=s4 primaryWord=barn",
                "LessonRuntime child response window opened stepId=s4 listening=true",
                "serial interactive child response accepted stepId=s4 recognizedText=barn",
                "serial TX lesson_progress event=step_completed result=success stepId=s4",
                "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
                "server send lesson_stop assignmentId=assign-1 sequence=4",
                "serial RX lesson_stop seq=4",
                "LessonRuntime event lesson_completed assignmentId=assign-1",
                "backend post lesson_completed assignmentId=assign-1",
                "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-a state=COMPLETED",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-lesson-id",
            "lesson-a",
            "--expected-course-id",
            "course-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_lesson_identity"]["ok"] is True
    assert checks["expected_course_identity"]["ok"] is True
    assert "assignment_current=lesson-a" in checks["expected_lesson_identity"]["evidence"]
    assert "assignment_current=course-a" in checks["expected_course_identity"]["evidence"]


def test_lesson_e2e_log_verify_fails_when_step_never_reaches_firmware():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "manifest fetched lesson=w01-d01-barn-say-it profile=espTft",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial TX lesson_ack body.acks=1",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial TX lesson_ack body.acks=2",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is False
    assert checks["background_rendered"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_cancelled_lesson_step_frame():
    module = load_module()
    lesson_step_frame = module._positive_frame("lesson_step")

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert lesson_step_frame(
            f"server send lesson_step stepId=s1 backgroundScene.poster.src=https://ota.example/poster.jpg {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_lesson_step_frame():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking cancelled=true",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_requires_explicit_visual_render_evidence_not_only_ack():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "manifest fetched lesson=w01-d01-barn-say-it profile=espTft",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "backend post lesson_completed assignmentId=assign-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True
    assert checks["background_rendered"]["ok"] is False


def test_lesson_e2e_log_verify_requires_audio_response_after_visual_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_audio_played"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_stale_render_and_progress_before_current_step():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "serial Lesson assignmentId=assign-1 sessionId=sess-1 old lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_render_audio_or_progress_for_different_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s3",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s3","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_audio_played"]["ok"] is True
    assert checks["lesson_progress"]["ok"] is True
    assert checks["step_consistent"]["ok"] is False
    assert "s3" in checks["step_consistent"]["evidence"]
    assert "s4" in checks["step_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_stale_completion_from_different_assignment():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-new state=ASSIGNED",
        "manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-new",
        "server send lesson_prepare assignmentId=assign-new sequence=1",
        "serial TX lesson_ack assignmentId=assign-new body.acks=1 rendered=false",
        "server send lesson_start assignmentId=assign-new sequence=2",
        "serial TX lesson_ack assignmentId=assign-new body.acks=2 rendered=false",
        "server send lesson_step assignmentId=assign-new stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson assignmentId=assign-new lesson_step poster fetched+drawn from URL",
        "serial TX lesson_ack assignmentId=assign-new body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress assignmentId=assign-new event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-new sequence=4",
        "backend post lesson_completed assignmentId=assign-old",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_consistent"]["ok"] is False
    assert "assign-new" in checks["assignment_consistent"]["evidence"]
    assert "assign-old" in checks["assignment_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_stale_completion_from_json_assignment_id():
    module = load_module()
    lines = [
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-real-1",
        '{"type":"lesson_prepare","assignmentId":"assign-new","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-new","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-new",
        '{"type":"lesson_start","assignmentId":"assign-new","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-new","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-new","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-new lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-new","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-new","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-new","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-old",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_consistent"]["ok"] is False
    assert "assign-new" in checks["assignment_consistent"]["evidence"]
    assert "assign-old" in checks["assignment_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_mixed_session_evidence_for_same_assignment():
    module = load_module()
    lines = [
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-new",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-new","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-new","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-new","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-new","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-new","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: sessionId=sess-new lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-new","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-new","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-new","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-old",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["session_consistent"]["ok"] is False
    assert "sess-new" in checks["session_consistent"]["evidence"]
    assert "sess-old" in checks["session_consistent"]["evidence"]

def test_lesson_e2e_log_verify_requires_backend_assignment_current_evidence():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial TX lesson_ack assignmentId=assign-1 body.acks=1 rendered=false",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial TX lesson_ack assignmentId=assign-1 body.acks=2 rendered=false",
        "server send lesson_step assignmentId=assign-1 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson assignmentId=assign-1 lesson_step poster fetched+drawn from URL",
        "serial TX lesson_ack assignmentId=assign-1 body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress assignmentId=assign-1 event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "backend post lesson_completed assignmentId=assign-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_current"]["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True

def test_lesson_e2e_log_verify_requires_voice_start_lesson_intent_before_flow():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is False
    assert checks["assignment_current"]["ok"] is True
    assert checks["lesson_start_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_unhandled_start_lesson_intent():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=false",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_cancelled_start_lesson_request():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_start_requested(
            f"voice intent start_lesson text=\"bắt đầu bài học\" handled=true {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_start_lesson_request_numeric_false_flags():
    module = load_module()

    for marker in ("handled=0", '"handled":0'):
        assert module._lesson_start_requested(
            f"voice intent start_lesson text=\"bắt đầu bài học\" {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_start_lesson_request():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true cancelled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is True
    assert checks["lesson_started"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_start_request_as_spoken_start():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "backend post start_lesson intent handled=true text=\"bắt đầu bài học\" persisted=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is True
    assert checks["lesson_started"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True

def test_lesson_e2e_log_verify_requires_audible_start_lesson_ack_before_assignment():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is True
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["assignment_current"]["ok"] is True

def test_lesson_e2e_log_verify_requires_start_lesson_ack_playback_not_only_sentence_start():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_start_ack_as_robot_acknowledgement():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "backend post tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 persisted=true",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is True
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_start_lesson_ack_playback_complete_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete=false reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_start_lesson_ack_numeric_false_flags():
    module = load_module()

    for marker in ("complete=0", '"complete":0', "played=0", '"played":0'):
        assert module._lesson_start_acknowledged(
            f"tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_cancelled_start_lesson_ack_playback():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_start_acknowledged(
            f"tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_start_lesson_ack_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_start_acknowledged(
            f"tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_start_lesson_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900 cancelled=true",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_requested"]["ok"] is True
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_zero_payload_start_lesson_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack bytes=0 duration_ms=0",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_requires_lesson_started_or_running_before_steps():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is True
    assert checks["lesson_started"]["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_lesson_started_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 started=false state=STARTING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is True
    assert checks["lesson_started"]["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_cancelled_lesson_started_event():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_started(
            f"LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_lesson_started_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_started(
            f"LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_lesson_started_numeric_false_flags():
    module = load_module()

    for marker in ("started=0", '"started":0'):
        assert module._lesson_started(
            f"LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_cancelled_step_started_event():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_step_started(
            f"LessonRuntime event step_started assignmentId=assign-1 stepId=s4 started=true {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_step_started_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_step_started(
            f"LessonRuntime event step_started assignmentId=assign-1 stepId=s4 started=true {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_lesson_started_event():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING cancelled=true",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is True
    assert checks["lesson_started"]["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_step_started_event():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4 started=true cancelled=true",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_started"]["ok"] is True
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["lesson_step_started"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_backend_start_as_lesson_started():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "backend post lesson_started assignmentId=assign-1 sessionId=sess-1 status=started persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_sent"]["ok"] is True
    assert checks["lesson_start_acknowledged"]["ok"] is True
    assert checks["lesson_started"]["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True

def test_lesson_e2e_log_verify_requires_step_started_before_render_and_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_started"]["ok"] is True
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["lesson_step_started"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_step_started_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4 started=false",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_started"]["ok"] is True
    assert checks["lesson_step_started"]["ok"] is False
    assert checks["lesson_progress"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_step_started_numeric_false_flags():
    module = load_module()

    for marker in ("started=0", '"started":0'):
        assert module._lesson_step_started(
            f"LessonRuntime event step_started assignmentId=assign-1 stepId=s4 {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_backend_step_as_lesson_step_started():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "backend post step_started assignmentId=assign-1 sessionId=sess-1 stepId=s1 started=true persisted=true",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_started"]["ok"] is True
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["lesson_step_started"]["ok"] is False

def test_lesson_e2e_log_verify_requires_teaching_content_rendered_before_audio():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_content_rendered"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_requires_robot_overlay_rendered_before_audio():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_content_rendered"]["ok"] is True
    assert checks["robot_overlay_rendered"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_cancelled_robot_overlay_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach cancelled=true",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_overlay_rendered"]["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_overlay=s4" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_requires_rendered_ack_to_report_robot_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false",
        "serial TX lesson_progress event=step_completed result=success",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True
    assert checks["lesson_step_ack_robot_state"]["ok"] is False

def test_lesson_e2e_log_verify_requires_each_completed_step_ack_to_report_robot_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "lesson_completed persisted assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_step_ack_robot_state"]["ok"] is False
    assert "missing_ack_robot_state=s2" in checks["lesson_step_ack_robot_state"]["evidence"]

def test_lesson_e2e_log_verify_requires_active_assignment_current_state():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=FAILED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_current"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_active_assignment_status_500():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 500 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_current"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_manifest_for_different_assigned_lesson():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-b course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_current"]["ok"] is True
    assert checks["manifest_fetched"]["ok"] is True
    assert checks["lesson_content_consistent"]["ok"] is False
    assert "lesson-a" in checks["lesson_content_consistent"]["evidence"]
    assert "lesson-b" in checks["lesson_content_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_non_esp_tft_manifest_profile():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=mobile assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["manifest_profile_esp_tft"]["ok"] is False
    assert "profiles=mobile" in checks["manifest_profile_esp_tft"]["evidence"]

def test_lesson_e2e_log_verify_ignores_camelcase_device_id_for_other_robot():
    module = load_module()
    lines = [
        "websocket hello deviceId=otherRobot session=sess-1",
        "deviceId=otherRobot assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "deviceId=otherRobot LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        'deviceId=otherRobot {"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        'deviceId=otherRobot {"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        'deviceId=otherRobot {"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        'deviceId=otherRobot {"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        'deviceId=otherRobot {"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "deviceId=otherRobot I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        'deviceId=otherRobot {"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        'deviceId=otherRobot {"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        'deviceId=otherRobot {"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "deviceId=otherRobot LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False
    assert checks["assignment_current"]["ok"] is False

def test_lesson_e2e_log_verify_ignores_json_device_id_for_other_robot():
    module = load_module()
    lines = [
        'I (100) Application: TBOT firmware boot complete "deviceId":"otherRobot"',
        'I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23 "deviceId":"otherRobot"',
        'websocket hello "deviceId":"otherRobot" session=sess-1',
        'voice intent start_lesson text="bắt đầu bài học" handled=true "deviceId":"otherRobot"',
        'tts playback complete reason=start_lesson_ack "deviceId":"otherRobot"',
        'assignment/current -> 200 "deviceId":"otherRobot" assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED',
        'LessonRuntime manifest fetched "deviceId":"otherRobot" lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1',
        '{"deviceId":"otherRobot","type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"deviceId":"otherRobot","type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        'LessonRuntime preload_ready "deviceId":"otherRobot" assignmentId=assign-1 criticalAssets=ready',
        '{"deviceId":"otherRobot","type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"deviceId":"otherRobot","type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        'LessonRuntime event lesson_started "deviceId":"otherRobot" assignmentId=assign-1 state=RUNNING',
        '{"deviceId":"otherRobot","type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        'LessonRuntime event step_started "deviceId":"otherRobot" assignmentId=assign-1 stepId=s1',
        'I (666) Lesson: "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg',
        'I (666) Lesson: "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn',
        'I (666) Lesson: "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking',
        'serial Audio TTS played "deviceId":"otherRobot" stepId=s1 primaryWord=barn',
        '{"deviceId":"otherRobot","type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"deviceId":"otherRobot","type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        'backend post lesson_progress "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true',
        '{"deviceId":"otherRobot","type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        'serial RX lesson_stop "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1 seq=5',
        'LessonRuntime event lesson_completed "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1',
        'backend post lesson_completed "deviceId":"otherRobot" assignmentId=assign-1 sessionId=sess-1',
        'assignment/current -> 200 "deviceId":"otherRobot" assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED',
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False
    assert checks["assignment_current"]["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is False

def test_lesson_e2e_log_verify_requires_exact_explicit_device_id_match():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48-shadow session=sess-1",
        "device_id=14:c1:9f:d1:a8:48-shadow assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False
    assert checks["assignment_current"]["ok"] is False

def test_lesson_e2e_log_verify_requires_actual_manifest_fetch_not_only_prepare_ref():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["manifest_fetched"]["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_false_manifest_fetch_flag():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 fetched=false status=200",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["manifest_fetched"]["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_manifest_fetch_numeric_false_flags():
    module = load_module()

    for marker in ("fetched=0", '"fetched":0', "loaded=0", '"loaded":0', "valid=0", '"valid":0'):
        assert module._manifest_fetched_with_identity(
            f"LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_cancelled_manifest_fetch_flag():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._manifest_fetched_with_identity(
            f"LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_manifest_fetch_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._manifest_fetched_with_identity(
            f"LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_manifest_fetch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 cancelled=true",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["manifest_fetched"]["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True

def test_lesson_e2e_log_verify_requires_preload_ready_before_lesson_start():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is True
    assert checks["lesson_preload_ready"]["ok"] is False
    assert checks["lesson_start_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_preload_as_lesson_preload_ready():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "backend post preload_ready assignmentId=assign-1 sessionId=sess-1 criticalAssets=ready persisted=true",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is True
    assert checks["lesson_preload_ready"]["ok"] is False
    assert checks["lesson_start_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_preload_ready_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 ready=false criticalAssets=missing",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_preload_ready"]["ok"] is False
    assert checks["lesson_preload_critical_assets_ready"]["ok"] is False
    assert "ready=none" in checks["lesson_preload_critical_assets_ready"]["evidence"]


def test_lesson_e2e_log_verify_rejects_partial_or_incomplete_preload_ready():
    module = load_module()

    for marker in (
        "partial=true",
        "complete=false",
        "allReady=false",
        "downloaded=false",
        "verified=false",
        "missingAssets=video",
    ):
        assert module._lesson_preload_ready(
            f"LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_preload_ready_numeric_false_flags():
    module = load_module()

    for marker in (
        "ready=0",
        '"ready":0',
        "preload_ready=0",
        '"preload_ready":0',
        "complete=0",
        '"complete":0',
        "allReady=0",
        '"allReady":0',
        "downloaded=0",
        '"downloaded":0',
        "verified=0",
        '"verified":0',
    ):
        assert module._lesson_preload_ready(
            f"LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_cancelled_preload_ready():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_preload_ready(
            f"LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_preload_ready_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_preload_ready(
            f"LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_partial_critical_asset_preload_confirmation():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster","video"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready partial=true missingAssets=video",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"},"video":{"src":"https://ota.example/clip.mp4"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson video rendered stepId=s4 url=https://ota.example/clip.mp4",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_preload_ready"]["ok"] is False
    assert checks["lesson_preload_critical_assets_ready"]["ok"] is False
    assert "ready=none" in checks["lesson_preload_critical_assets_ready"]["evidence"]


def test_lesson_e2e_log_verify_rejects_cancelled_preload_ready_flow():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster","video"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready cancelled=true",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"},"video":{"src":"https://ota.example/clip.mp4"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson video rendered stepId=s4 url=https://ota.example/clip.mp4",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_preload_ready"]["ok"] is False
    assert checks["lesson_preload_critical_assets_ready"]["ok"] is False
    assert checks["lesson_started"]["ok"] is True

def test_lesson_e2e_log_verify_requires_preload_ready_to_confirm_declared_critical_assets():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster","video"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"},"video":{"src":"https://ota.example/clip.mp4"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson video rendered stepId=s4 url=https://ota.example/clip.mp4",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_preload_critical_assets_ready"]["ok"] is False
    assert "expected=poster,video" in checks["lesson_preload_critical_assets_ready"]["evidence"]
    assert "ready=none" in checks["lesson_preload_critical_assets_ready"]["evidence"]

def test_lesson_e2e_log_verify_requires_manifest_lesson_or_course_identity():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_booted"]["ok"] is True
    assert checks["manifest_fetched"]["ok"] is False
    assert checks["lesson_prepare_sent"]["ok"] is True

def test_lesson_e2e_log_verify_requires_explicit_lesson_progress_event():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        "debug assignmentId=assign-1 sessionId=sess-1 last_step_completed_cache_marker=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True

def test_lesson_e2e_log_verify_requires_backend_progress_post_before_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_serial_progress_persisted_as_backend_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "serial TX lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_backend_progress_persisted_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=false status=200",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_posted"]["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is False
    assert "missing_posted=s1" in checks["lesson_progress_posted_steps"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_status_500():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true status=500",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_posted"]["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is False
    assert "missing_posted=s1" in checks["lesson_progress_posted_steps"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_accepted_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true accepted=false status=200",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_posted"]["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is False
    assert "missing_posted=s1" in checks["lesson_progress_posted_steps"]["evidence"]

def test_lesson_e2e_log_verify_requires_step_ids_for_multi_step_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"},{"id":"s2","completionClass":"interactive"},{"id":"s3","completionClass":"interactive"}],"totalSteps":3}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}},"teachingObject":{"subject":{"primaryWord":"hay"}},"robotOverlay":{"robotState":"listening"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s3","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s3.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s3",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s3.jpg",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 teachingObject rendered primaryWord=cow",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 robotOverlay rendered robotState=celebrating pose=celebrate",
        "serial Audio TTS played stepId=s3",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s3","body":{"acks":5,"rendered":true,"degraded":false,"robotState":"celebrating"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":8,"body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":9}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=9",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is True
    assert checks["lesson_progress_step_identity"]["ok"] is False

def test_lesson_e2e_log_verify_requires_all_manifest_steps_completed():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"},{"id":"s2","completionClass":"interactive"},{"id":"s3","completionClass":"interactive"}],"totalSteps":3}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_count"]["ok"] is False
    assert "1/3" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_rejects_more_completed_steps_than_declared_total_steps():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s3","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s3.jpg"}}}}}',
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s3.jpg",
        "serial Audio TTS played stepId=s3",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s3","body":{"acks":5,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":8,"stepId":"s3","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":9}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=9",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is False
    assert "3/2" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_rejects_manifest_total_steps_mismatch_steps_array_count():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2 {"steps":[{"title":"one"},{"title":"two"},{"title":"three"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime child response window opened stepId=s1 listening=true",
        "serial interactive child response accepted stepId=s1 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_step_count_consistent"]["ok"] is False
    assert "totalSteps=2" in checks["lesson_manifest_step_count_consistent"]["evidence"]
    assert "steps_array=3" in checks["lesson_manifest_step_count_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_inconsistent_total_steps_across_manifest_fetches():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"},{"id":"s2","completionClass":"interactive"},{"id":"s3","completionClass":"interactive"}],"totalSteps":3}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime step prompt stepId=s1 text=Can you say barn?",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime child response window opened stepId=s1 listening=true",
        "serial interactive child response accepted stepId=s1 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime step prompt stepId=s2 text=Can you say hay?",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_step_count_consistent"]["ok"] is False
    assert "totalSteps=2,3" in checks["lesson_manifest_step_count_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_inconsistent_manifest_steps_across_fetches():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s2"}]}',
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s3"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_steps_consistent"]["ok"] is False
    assert "s1,s2" in checks["lesson_manifest_steps_consistent"]["evidence"]
    assert "s1,s3" in checks["lesson_manifest_steps_consistent"]["evidence"]

def test_lesson_e2e_log_verify_parses_json_string_total_steps_for_completion_count():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 "totalSteps":"3"',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is False
    assert "1/3" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_infers_step_count_from_manifest_steps_array():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s2"},{"id":"s3"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is False
    assert "1/3" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_rejects_empty_manifest_steps_array():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":3}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is False
    assert "expected_steps=0" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_counts_unique_completed_step_ids_not_duplicate_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=3",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":8}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=8",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is True
    assert checks["lesson_progress_count"]["ok"] is False
    assert "unique step_completed count=2/3" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_steps_without_render_and_audio_evidence():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=3",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is True
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_sent=s2,s3" in checks["lesson_steps_observed"]["evidence"]
    assert "missing_render=s2,s3" in checks["lesson_steps_observed"]["evidence"]
    assert "missing_audio=s2,s3" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_single_step_progress_without_step_id():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"}],"totalSteps":1}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_step_identity"]["ok"] is False
    assert "progress_without_stepId=1" in checks["lesson_progress_step_identity"]["evidence"]
    assert "posted_without_stepId=1" in checks["lesson_progress_step_identity"]["evidence"]
    assert "posted_without_stepId=1" in checks["lesson_progress_step_identity"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_steps_without_rendered_ack_evidence():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=3",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s3.jpg"}}},"teachingObject":{"subject":{"primaryWord":"cow"}},"robotOverlay":{"robotState":"celebrating"}}}',
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s3.jpg",
        "serial Audio TTS played stepId=s3",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_ack=s2,s3" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_step_without_step_started_evidence():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_started=s2" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_steps_without_content_layers():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_content=s2" in checks["lesson_step_content_layers"]["evidence"]
    assert "missing_overlay=s2" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_step_without_background_media_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s1.png"},"subject":{"primaryWord":"barn"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s1.png"},"robotState":"talking"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime step prompt stepId=s1 text=Can you say barn?",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        "LessonRuntime child response window opened stepId=s1 listening=true",
        "serial interactive child response accepted stepId=s1 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s2.png"},"subject":{"primaryWord":"hay"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s2.png"},"robotState":"listening"}}}}',
        "LessonRuntime step prompt stepId=s2 text=Can you say hay?",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step rendered degraded=false",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listening",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_render=s2" in checks["lesson_steps_observed"]["evidence"]
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_background=s2" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_background_barn_render_as_teaching_object():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say", "completionClass": "interactive"}]
        ),
        step_id="s4",
        include_child_response=True,
    )
    lines = [
        line
        for line in lines
        if "teachingObject rendered primaryWord=barn" not in line
    ]
    lines = [
        line.replace(
            "lesson_step poster fetched+drawn from URL",
            "lesson_step poster fetched+drawn from URL barn rendered",
        )
        for line in lines
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_content=s4" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_step_without_declared_three_layer_frame():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = (
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,'
        '"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s4.jpg"}}}}}'
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_declares_three_layers"]["ok"] is False
    assert "missing_teachingObject=s4" in checks["lesson_step_declares_three_layers"]["evidence"]
    assert "missing_robotOverlay=s4" in checks["lesson_step_declares_three_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_json_step_layers_outside_scene():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = (
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,'
        '"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s4.jpg"}}},'
        '"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}'
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_declares_three_layers"]["ok"] is False
    assert "missing_teachingObject=s4" in checks["lesson_step_declares_three_layers"]["evidence"]
    assert "missing_robotOverlay=s4" in checks["lesson_step_declares_three_layers"]["evidence"]


def test_lesson_e2e_log_verify_accepts_pretty_json_declared_three_layer_frame():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = lesson_step_frame(pretty=True)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_declares_three_layers"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_json_step_id_from_log_prefix_for_declared_layers():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = "server send lesson_step stepId=s4 " + lesson_step_frame(include_step_id=False)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_declares_three_layers"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_truncated_json_step_instead_of_text_fallback():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = (
        'server send lesson_step stepId=s4 {"type":"lesson_step","body":{"scene":'
        '{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s4.jpg"}},'
        '"teachingObject":{"asset":{"src":"https://ota.example/object-s4.png"}},'
        '"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s4.png"}}'
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_declares_three_layers"]["ok"] is False
    assert "missing_backgroundScene=s4" in checks["lesson_step_declares_three_layers"]["evidence"]


def test_lesson_e2e_log_verify_rejects_json_step_layers_without_required_image_sources():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = lesson_step_frame(
        scene={
            "backgroundScene": {"poster": {}},
            "teachingObject": {"asset": {}},
            "robotOverlay": {"asset": {}},
        }
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_declares_three_layers"]["ok"] is False
    assert "missing_backgroundScene=s4" in checks["lesson_step_declares_three_layers"]["evidence"]
    assert "missing_teachingObject=s4" in checks["lesson_step_declares_three_layers"]["evidence"]
    assert "missing_robotOverlay=s4" in checks["lesson_step_declares_three_layers"]["evidence"]


def test_lesson_e2e_log_verify_rejects_inline_data_uri_layer_sources():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = lesson_step_frame(
        scene={
            "backgroundScene": {"poster": {"src": "data:image/jpeg;base64," + "A" * 8192}},
            "teachingObject": {"asset": {"src": "https://ota.example/object-s4.png"}},
            "robotOverlay": {"asset": {"src": "https://ota.example/overlay-s4.png"}},
        }
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_no_inline_media_sources"]["ok"] is False
    assert "inline_source=s4:backgroundScene.poster.src" in checks[
        "lesson_step_no_inline_media_sources"
    ]["evidence"]


def test_lesson_e2e_log_verify_rejects_ambiguous_expected_and_actual_json_step_payloads():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    invalid_actual = lesson_step_frame(
        scene={
            "backgroundScene": {"poster": {"src": "https://ota.example/poster-s4.jpg"}},
        }
    )
    lines[13] = f"server send lesson_step stepId=s4 expected={lesson_step_frame()} actual={invalid_actual}"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_declares_three_layers"]["ok"] is False
    assert "missing_teachingObject=s4" in checks["lesson_step_declares_three_layers"]["evidence"]


def test_lesson_e2e_log_verify_rejects_step_prompt_before_lesson_step_frame():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(13, "LessonRuntime step prompt stepId=s4 text=Con nhìn hình rồi nói barn nhé")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_prompt_after_frame"]["ok"] is False
    assert "prompt_before_frame=s4" in checks["lesson_step_prompt_after_frame"]["evidence"]


def test_lesson_e2e_log_verify_rejects_step_prompt_before_render_ack():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    prompt_line = lines.pop(prompt_index)
    frame_index = next(i for i, line in enumerate(lines) if '"type":"lesson_step"' in line)
    lines.insert(frame_index + 1, prompt_line)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert "lesson_step_prompt_after_render_ack" in checks
    assert checks["lesson_step_prompt_after_render_ack"]["ok"] is False
    assert "prompt_before_render_ack=s4" in checks[
        "lesson_step_prompt_after_render_ack"
    ]["evidence"]


def test_lesson_e2e_log_verify_rejects_interactive_step_without_guided_speaking_prompt():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Look at the picture."

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]


def test_lesson_e2e_log_verify_rejects_command_only_prompt_as_guided_question():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Say barn with TeeBot."

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]


def test_lesson_e2e_log_verify_rejects_command_only_prompt_with_question_mark_as_guided_question():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Say barn?"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]

def test_lesson_e2e_log_verify_rejects_polite_command_only_prompt_with_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Please say barn?"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_command_only_prompt_with_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Hãy nói barn?"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]


def test_lesson_e2e_log_verify_accepts_where_guided_prompt_without_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Where is the barn"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["interactive_guided_prompt"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_which_guided_prompt_without_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Which animal is hiding"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["interactive_guided_prompt"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_how_many_guided_prompt_without_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=How many animals are there"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["interactive_guided_prompt"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_where_do_you_see_guided_prompt_without_question_mark():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_index = next(i for i, line in enumerate(lines) if "LessonRuntime step prompt" in line)
    lines[prompt_index] = "LessonRuntime step prompt stepId=s4 text=Where do you see the barn"

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["interactive_guided_prompt"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_guided_prompt_after_child_response():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    prompt_line = "LessonRuntime step prompt stepId=s4 text=Can you say s4 with TeeBot?"
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn"
    lines.remove(prompt_line)
    lines.insert(lines.index(response_line) + 1, prompt_line)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "guided_prompt_after_response=s4" in checks["interactive_guided_prompt"]["evidence"]

def test_lesson_e2e_log_verify_rejects_step_progress_before_render_audio_and_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_steps_ordered"]["ok"] is False
    assert "out_of_order=s2" in checks["lesson_steps_ordered"]["evidence"]

def test_lesson_e2e_log_verify_rejects_step_started_after_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_steps_ordered"]["ok"] is False
    assert "out_of_order=s2" in checks["lesson_steps_ordered"]["evidence"]

def test_lesson_e2e_log_verify_rejects_content_layers_after_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_step_content_layers"]["ok"] is True
    assert checks["lesson_steps_ordered"]["ok"] is False
    assert "out_of_order=s1" in checks["lesson_steps_ordered"]["evidence"]

def test_lesson_e2e_log_verify_rejects_background_media_render_after_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step rendered degraded=false",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is True
    assert checks["lesson_step_content_layers"]["ok"] is True
    assert checks["lesson_steps_ordered"]["ok"] is False
    assert "out_of_order=s1" in checks["lesson_steps_ordered"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_before_robot_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_steps_ordered"]["ok"] is True
    assert checks["lesson_progress_posted_steps"]["ok"] is True
    assert checks["lesson_backend_progress_ordered"]["ok"] is False
    assert "posted_before_progress=s2" in checks["lesson_backend_progress_ordered"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_for_different_session():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-other stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is True
    assert checks["lesson_backend_progress_ordered"]["ok"] is True
    assert checks["lesson_backend_progress_session"]["ok"] is False
    assert "session_mismatch=s2" in checks["lesson_backend_progress_session"]["evidence"]
    assert checks["session_consistent"]["ok"] is False
    assert "sess-1" in checks["session_consistent"]["evidence"]
    assert "sess-other" in checks["session_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_for_different_assignment():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-old sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is True
    assert checks["lesson_backend_progress_ordered"]["ok"] is True
    assert checks["lesson_backend_progress_session"]["ok"] is True
    assert checks["assignment_consistent"]["ok"] is False
    assert "assign-1" in checks["assignment_consistent"]["evidence"]
    assert "assign-old" in checks["assignment_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_for_different_lesson_or_course():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-b courseId=course-2 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(
        lines,
        device_id="14:c1:9f:d1:a8:48",
        expected_lesson_id="lesson-a",
        expected_course_id="course-1",
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_posted_steps"]["ok"] is True
    assert checks["lesson_backend_progress_ordered"]["ok"] is True
    assert checks["lesson_backend_progress_session"]["ok"] is True
    assert checks["assignment_consistent"]["ok"] is True
    assert checks["lesson_content_consistent"]["ok"] is False
    assert checks["expected_lesson_identity"]["ok"] is False
    assert checks["expected_course_identity"]["ok"] is False
    assert "lesson-a" in checks["lesson_content_consistent"]["evidence"]
    assert "lesson-b" in checks["lesson_content_consistent"]["evidence"]
    assert "course-2" in checks["lesson_content_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_persisted_after_stop():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_backend_progress_ordered"]["ok"] is True
    assert checks["lesson_backend_progress_session"]["ok"] is True
    assert checks["lesson_backend_progress_before_stop"]["ok"] is False
    assert "posted_after_stop=s2" in checks["lesson_backend_progress_before_stop"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completion_before_backend_progress_persisted():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_backend_progress_ordered"]["ok"] is True
    assert checks["lesson_backend_progress_session"]["ok"] is True
    assert checks["lesson_backend_progress_before_stop"]["ok"] is True
    assert checks["lesson_completion_after_backend_progress"]["ok"] is False
    assert "posted_after_completion=s2" in checks["lesson_completion_after_backend_progress"]["evidence"]

def test_lesson_e2e_log_verify_rejects_runtime_completion_before_backend_progress_persisted():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_after_backend_progress"]["ok"] is True
    assert checks["lesson_runtime_completion_after_backend_progress"]["ok"] is False
    assert "posted_after_runtime_completion=s2" in checks["lesson_runtime_completion_after_backend_progress"]["evidence"]

def test_lesson_e2e_log_verify_rejects_runtime_completion_failed_flag():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 failed=true",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "failed=true" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_accepts_all_manifest_steps_completed():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"},{"id":"s2","completionClass":"interactive"},{"id":"s3","completionClass":"interactive"}],"totalSteps":3}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s1.png"},"subject":{"primaryWord":"barn"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s1.png"},"robotState":"talking"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime step prompt stepId=s1 text=Can you say barn?",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        "LessonRuntime child response window opened stepId=s1 listening=true",
        "serial interactive child response accepted stepId=s1 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s2.png"},"subject":{"primaryWord":"hay"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s2.png"},"robotState":"listening"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        "LessonRuntime step prompt stepId=s2 text=Can you say hay?",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        "LessonRuntime child response window opened stepId=s2 listening=true",
        "serial interactive child response accepted stepId=s2 recognizedText=hay",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s3.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s3.png"},"subject":{"primaryWord":"cow"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s3.png"},"robotState":"celebrating"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s3",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s3.jpg",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 teachingObject rendered primaryWord=cow",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s3 robotOverlay rendered robotState=celebrating pose=celebrate",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"acks":5,"rendered":true,"degraded":false,"robotState":"celebrating"}}',
        "LessonRuntime step prompt stepId=s3 text=Can you say cow?",
        "serial Audio TTS played stepId=s3 primaryWord=cow",
        "LessonRuntime child response window opened stepId=s3 listening=true",
        "serial interactive child response accepted stepId=s3 recognizedText=cow",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s3","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s3 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_progress_count"]["ok"] is True
    assert "3/3" in checks["lesson_progress_count"]["evidence"]

def test_lesson_e2e_log_verify_rejects_completed_step_not_declared_in_manifest_steps():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s2"},{"id":"s3"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s4.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s4.jpg",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=duck",
        "I (668) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=celebrating pose=celebrate",
        "serial Audio TTS played stepId=s4 primaryWord=duck",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s4","body":{"acks":5,"rendered":true,"degraded":false,"robotState":"celebrating"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is True
    assert checks["lesson_manifest_step_ids"]["ok"] is False
    assert "missing_completed=s3" in checks["lesson_manifest_step_ids"]["evidence"]
    assert "unexpected_completed=s4" in checks["lesson_manifest_step_ids"]["evidence"]

def test_lesson_e2e_log_verify_rejects_manifest_steps_completed_out_of_order():
    module = load_module()

    def completed_step(step_id: str, sequence: int, word: str, robot_state: str) -> list[str]:
        return [
            json.dumps(
                {
                    "type": "lesson_step",
                    "assignmentId": "assign-1",
                    "sessionId": "sess-1",
                    "sequence": sequence,
                    "stepId": step_id,
                    "body": {"scene": {"backgroundScene": {"poster": {"src": f"https://ota.example/poster-{step_id}.jpg"}}}},
                },
                separators=(",", ":"),
            ),
            f"LessonRuntime event step_started assignmentId=assign-1 stepId={step_id}",
            f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} lesson_step poster fetched+drawn from URL url=https://ota.example/poster-{step_id}.jpg",
            f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} teachingObject rendered primaryWord={word}",
            f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId={step_id} robotOverlay rendered robotState={robot_state} pose=teach",
            f"serial Audio TTS played stepId={step_id} primaryWord={word}",
            json.dumps(
                {
                    "type": "lesson_ack",
                    "assignmentId": "assign-1",
                    "sessionId": "sess-1",
                    "sequence": sequence,
                    "stepId": step_id,
                    "body": {"acks": sequence, "rendered": True, "degraded": False, "robotState": robot_state},
                },
                separators=(",", ":"),
            ),
            json.dumps(
                {
                    "type": "lesson_progress",
                    "assignmentId": "assign-1",
                    "sessionId": "sess-1",
                    "sequence": sequence + 1,
                    "stepId": step_id,
                    "body": {"event": "step_completed", "result": "success"},
                },
                separators=(",", ":"),
            ),
            f"backend post lesson_progress assignmentId=assign-1 stepId={step_id} event=step_completed result=success persisted=true",
        ]

    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s2"},{"id":"s3"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        *completed_step("s2", 3, "hay", "listening"),
        *completed_step("s1", 5, "barn", "talking"),
        *completed_step("s3", 7, "cow", "celebrating"),
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":9}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=9",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_step_ids"]["ok"] is True
    assert checks["lesson_manifest_step_order"]["ok"] is False
    assert "manifest=s1,s2,s3" in checks["lesson_manifest_step_order"]["evidence"]
    assert "progress=s2,s1,s3" in checks["lesson_manifest_step_order"]["evidence"]

def test_lesson_e2e_log_verify_rejects_later_step_from_different_lesson_or_course():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-b courseId=course-2 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":6}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=6",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_content_consistent"]["ok"] is False
    assert "lesson-a" in checks["lesson_content_consistent"]["evidence"]
    assert "lesson-b" in checks["lesson_content_consistent"]["evidence"]
    assert "course-2" in checks["lesson_content_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_capture_with_fatal_server_or_lesson_error():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "voice status: Server unavailable. Retrying...",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "Server unavailable" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_ignores_benign_wifi_beacon_and_config_error_keys():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(
        1,
        "I (7211) wifi:set rx beacon pti, rx_bcn_pti: 14, bcn_timeout: 25000, mt_pti: 14, mt_time: 10000",
    )
    lines.insert(
        2,
        'server config fetched successfully: {"voice_mode":{"fallback_to_classic_on_error":true,"type":"google_live"}}',
    )
    lines.insert(
        3,
        "I (12701) WifiStation: Setting WiFi power save level: LOW_POWER (MAX_MODEM)",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["fatal_errors"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_firmware_asset_pack_not_ready_error():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(
        9,
        '{"type":"lesson_error","assignmentId":"assign-1","sessionId":"sess-1",'
        '"sequence":2,"body":{"code":"ASSET_PACK_NOT_READY",'
        '"message":"lesson_prepare assetPack requires manifestRef.manifestChecksum"}}',
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "ASSET_PACK_NOT_READY" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_any_firmware_lesson_error_frame():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines.insert(
        14,
        '{"type":"lesson_error","assignmentId":"assign-1","sessionId":"sess-1",'
        '"sequence":4,"stepId":"s4","body":{"code":"LESSON_FRAME_INVALID",'
        '"message":"missing robotOverlay.asset.src"}}',
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "LESSON_FRAME_INVALID" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_terminal_lesson_failure_even_if_later_completed():
    module = load_module()
    terminal_events = [
        "LessonRuntime event lesson_failed assignmentId=assign-1 sessionId=sess-1 reason=runtime_stall",
        "LessonRuntime event lesson_abandoned assignmentId=assign-1 sessionId=sess-1 reason=child_inactive",
    ]

    for terminal_event in terminal_events:
        lines = [
            "I (100) Application: TBOT firmware boot complete",
            "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
            "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
            "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
            "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
            "tts playback complete reason=start_lesson_ack",
            "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
            "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft",
            "server send lesson_prepare assignmentId=assign-1 sequence=1",
            "serial RX lesson_prepare seq=1",
            "serial TX lesson_ack body.acks=1 rendered=false",
            "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
            "server send lesson_start assignmentId=assign-1 sequence=2",
            "serial RX lesson_start seq=2",
            "serial TX lesson_ack body.acks=2 rendered=false",
            "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
            "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
            "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
            "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
            "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
            "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
            "serial Audio TTS played stepId=s4",
            "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
            "serial TX lesson_progress event=step_completed result=success stepId=s4",
            "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
            terminal_event,
            "server send lesson_stop assignmentId=assign-1 sequence=4",
            "serial RX lesson_stop seq=4",
            "LessonRuntime event lesson_completed assignmentId=assign-1",
            "backend post lesson_completed assignmentId=assign-1",
            "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
        ]

        report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

        checks = by_name(report)
        assert report["ok"] is False
        assert checks["fatal_errors"]["ok"] is False
        assert terminal_event in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_asset_checksum_mismatch_even_if_lesson_completes():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"}],"totalSteps":1}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "LessonRuntime asset verify error code=ASSET_CHECKSUM_MISMATCH asset=poster url=https://ota.example/poster-s1.jpg",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 lessonId=lesson-a courseId=course-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "asset verify error" in checks["fatal_errors"]["evidence"]
    assert "asset=poster" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_prepare_manifest_checksum_mismatch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1 manifestChecksum=manifest-good",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"manifest-stale"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_manifest_checksum_consistent"]["ok"] is False
    assert "manifest=manifest-good" in checks["lesson_manifest_checksum_consistent"]["evidence"]
    assert "prepare=manifest-stale" in checks["lesson_manifest_checksum_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_assignment_manifest_checksum_mismatch():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft "
        "assignmentId=assign-1 totalSteps=1 manifestChecksum=manifest-stale "
        + json.dumps(
            {
                "steps": [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[5] = (
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a "
        "courseId=course-1 state=ASSIGNED manifestChecksum=manifest-current"
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "manifest-stale"},
                "criticalAssets": ["poster"],
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_assignment_manifest_checksum_consistent"]["ok"] is False
    assert "assignment=manifest-current" in checks[
        "lesson_assignment_manifest_checksum_consistent"
    ]["evidence"]
    assert "manifest=manifest-stale" in checks[
        "lesson_assignment_manifest_checksum_consistent"
    ]["evidence"]


def test_lesson_e2e_log_verify_accepts_multiple_runs_with_unpaired_optional_checksums():
    module = load_module()
    lines = [
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"session-1","body":{"manifestRef":{"manifestChecksum":"checksum-a"}}}',
        "assignment/current -> 200 assignmentId=assign-2 state=ASSIGNED",
        "LessonRuntime manifest fetched assignmentId=assign-2 sessionId=session-2 lesson=lesson-a profile=espTft manifestChecksum=checksum-b",
    ]

    assignment_gate = module._assignment_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )
    prepare_gate = module._lesson_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )

    assert assignment_gate["ok"] is True
    assert prepare_gate["ok"] is True
    assert "assign-1" in assignment_gate["evidence"]
    assert "session-1" in prepare_gate["evidence"]


def test_lesson_e2e_log_verify_rejects_crossed_checksums_between_run_identities():
    module = load_module()
    lines = [
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-b",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"session-1","body":{"manifestRef":{"manifestChecksum":"checksum-a"}}}',
        "assignment/current -> 200 assignmentId=assign-2 state=ASSIGNED manifestChecksum=checksum-b",
        "LessonRuntime manifest fetched assignmentId=assign-2 sessionId=session-2 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        '{"type":"lesson_prepare","assignmentId":"assign-2","sessionId":"session-2","body":{"manifestRef":{"manifestChecksum":"checksum-b"}}}',
    ]

    assignment_gate = module._assignment_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )
    prepare_gate = module._lesson_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )

    assert assignment_gate["ok"] is False
    assert prepare_gate["ok"] is False
    assert "assign-1" in assignment_gate["evidence"]
    assert "session-1" in prepare_gate["evidence"]


def test_assignment_manifest_checksum_pairing_keeps_same_assignment_sessions_isolated():
    module = load_module()
    lines = [
        "assignment/current -> 200 assignmentId=assign-1 sessionId=session-1 state=ASSIGNED manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        "assignment/current -> 200 assignmentId=assign-1 sessionId=session-2 state=ASSIGNED manifestChecksum=checksum-b",
        # A delayed duplicate from session-1 must still pair with session-1, not
        # the newer session-2 assignment checksum.
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-2 lesson=lesson-a profile=espTft manifestChecksum=checksum-b",
    ]

    gate = module._assignment_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )

    assert gate["ok"] is True
    assert "assign-1/session-1" in gate["evidence"]
    assert "assign-1/session-2" in gate["evidence"]


def test_sessionless_assignment_candidates_keep_delayed_old_session_manifest_valid():
    module = load_module()
    lines = [
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED manifestChecksum=checksum-b",
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-2 lesson=lesson-a profile=espTft manifestChecksum=checksum-b",
        # Delayed duplicate from the old session must use session-1's bound
        # assignment candidate instead of the newest sessionless value B.
        "LessonRuntime manifest fetched assignmentId=assign-1 sessionId=session-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
    ]

    gate = module._assignment_manifest_checksum_consistency_check(
        lines, module._device_scope("14:c1:9f:d1:a8:48", [])
    )

    assert gate["ok"] is True
    assert gate["evidence"].count("assign-1/session-1") == 2
    assert "assign-1/session-2" in gate["evidence"]

def test_lesson_e2e_log_verify_rejects_audio_failure_or_silent_response():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "voice status: Không nghe phản hồi",
        "serial audio playback done stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is True
    assert checks["fatal_errors"]["ok"] is False
    assert "Không nghe phản hồi" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_audio_as_lesson_step_audio():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"}],"totalSteps":1}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "backend post tts playback complete assignmentId=assign-1 sessionId=sess-1 stepId=s1 primaryWord=barn bytes=4096 duration_ms=1200 persisted=true",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert checks["lesson_ack_sequence_match"]["ok"] is True
    assert checks["lesson_progress"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_lesson_audio_playback_complete_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS playback complete=false stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_lesson_audio_numeric_false_flags():
    module = load_module()

    for marker in ("complete=0", '"complete":0', "played=0", '"played":0'):
        assert module._lesson_audio_played(
            f"serial Audio TTS playback complete stepId=s4 primaryWord=barn {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_lesson_audio_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_audio_played(
            f"serial Audio TTS playback complete stepId=s4 primaryWord=barn {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_cancelled_lesson_audio_playback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS playback complete stepId=s4 primaryWord=barn cancelled=true",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_zero_byte_lesson_audio_playback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS playback complete stepId=s4 primaryWord=barn bytes=0 duration_ms=0",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_audio=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_zero_byte_lesson_audio_json_spacing():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        'serial Audio TTS playback complete stepId=s4 primaryWord=barn {"bytes": 0, "duration_ms": 0}',
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_zero_duration_lesson_audio_playback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS playback complete stepId=s4 primaryWord=barn duration=0",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_muted_lesson_audio_playback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it-age3-20260617 profile=espTft assignmentId=assign-1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS playback complete stepId=s4 primaryWord=barn muted=true volume=0",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_accepts_nonzero_fractional_lesson_audio_volume():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack volume=0.75",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1","completionClass":"interactive"}],"totalSteps":1}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object-s1.png"},"subject":{"primaryWord":"barn"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay-s1.png"},"robotState":"talking"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime step prompt stepId=s1 text=Can you say barn?",
        "serial Audio TTS playback complete stepId=s1 primaryWord=barn volume=0.75",
        "LessonRuntime child response window opened stepId=s1 listening=true",
        "serial interactive child response accepted stepId=s1 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_start_acknowledged"]["ok"] is True
    assert checks["lesson_audio_played"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_decimal_zero_lesson_audio_volume():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack volume=0.0",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS playback complete stepId=s1 primaryWord=barn volume=0.0",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_acknowledged"]["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_speaker_disabled_lesson_audio_playback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack speaker_enabled=true",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS playback complete stepId=s1 primaryWord=barn speaker_enabled=false",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_missing_audio_output_route():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack audio_route=speaker output_device=i2s",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS playback complete stepId=s1 primaryWord=barn audio_route=none output_device=none",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert "missing_audio=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_server_unavailable_error():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "lcd status: Máy chủ không khả dụng",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "Máy chủ không khả dụng" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_robot_confirmation_timeout():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "ui status: Hết thời gian chờ robot xác nhận",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "Hết thời gian chờ robot xác nhận" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_robot_confirmation_waiting_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "ui status: Đang chờ robot xác thực",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "Đang chờ robot xác thực" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_abnormal_mid_lesson_runtime_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "LessonRuntime event lesson_paused assignmentId=assign-1 reason=ROBOT_BUSY detail=LOW_BATTERY",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "lesson_paused" in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_battery_low_wording_after_lesson_start():
    module = load_module()
    base_lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]
    fatal_messages = [
        "serial power warning: battery low while lesson running",
        "lcd warning: pin yếu khi đang học bài",
    ]

    for fatal_message in fatal_messages:
        report = module.evaluate_lesson_logs(
            [*base_lines[:16], fatal_message, *base_lines[16:]],
            device_id="14:c1:9f:d1:a8:48",
        )

        checks = by_name(report)
        assert report["ok"] is False
        assert checks["fatal_errors"]["ok"] is False
        assert fatal_message in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_no_lesson_errors():
    module = load_module()
    base_lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    fatal_messages = [
        "ui status: Chưa có bài học",
        "lcd status: Không có bài học",
        "voice status: chua co bai hoc",
        "backend status: khong co assignment",
    ]

    for fatal_message in fatal_messages:
        report = module.evaluate_lesson_logs(
            [*base_lines[:3], fatal_message, *base_lines[3:]],
            device_id="14:c1:9f:d1:a8:48",
        )

        checks = by_name(report)
        assert report["ok"] is False
        assert checks["fatal_errors"]["ok"] is False
        assert fatal_message in checks["fatal_errors"]["evidence"]

def test_lesson_e2e_log_verify_requires_positive_lesson_completed_event():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "debug assignmentId=assign-1 sessionId=sess-1 lesson_completed not forwarded yet",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_runtime_completion_false_flag():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 completed=false",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_cancelled_runtime_completion_event():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert module._lesson_completed_positive(
            f"LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_runtime_completion_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_completed_positive(
            f"LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_runtime_completion_numeric_false_flags():
    module = load_module()

    for marker in (
        "completed=0",
        '"completed":0',
        "complete=0",
        '"complete":0',
        "success=0",
        '"success":0',
    ):
        assert module._lesson_completed_positive(
            f"LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_runtime_completion_event():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1 cancelled=true",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_backend_completion_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 persisted=true status=200 cancelled=true",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False
    assert checks["assignment_completed"]["ok"] is True

def test_lesson_e2e_log_verify_requires_runtime_completion_not_only_backend_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_server_completion_event_as_runtime_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "server send lesson_completed event assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is True

def test_lesson_e2e_log_verify_requires_firmware_stop_received_before_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True
    assert checks["lesson_stop_received"]["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is True

def test_lesson_e2e_log_verify_requires_backend_completion_post_after_robot_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_generic_completion_accepted_as_backend_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "ui completion accepted lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_cancelled_lesson_stop_received():
    module = load_module()

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true"):
        assert module._lesson_stop_received(
            f"serial Lesson lesson_stop background cleared assignmentId=assign-1 sessionId=sess-1 stopped=true {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_lesson_stop_stopped_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial Lesson lesson_stop lesson stopped assignmentId=assign-1 sessionId=sess-1 stopped=false",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True
    assert checks["lesson_stop_received"]["ok"] is False
    assert checks["lesson_completed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_lesson_stop_received_numeric_false_flags():
    module = load_module()

    for marker in ("received=0", '"received":0', "ack=0", '"ack":0'):
        assert module._lesson_stop_received(
            f"serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_lesson_stop_received_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
    )

    for marker in markers:
        assert module._lesson_stop_received(
            f"serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_cancelled_lesson_stop_received_flow():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial Lesson lesson_stop background cleared assignmentId=assign-1 sessionId=sess-1 stopped=true cancelled=true",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True
    assert checks["lesson_stop_received"]["ok"] is False
    assert checks["lesson_completed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_stop_as_robot_stop():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "backend post lesson_stop ack assignmentId=assign-1 sessionId=sess-1 background cleared stopped=true persisted=true",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True
    assert checks["lesson_stop_received"]["ok"] is False
    assert checks["lesson_completed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_completion_json_status_500():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        'backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {"status":500,"persisted":true}',
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False

def test_lesson_e2e_log_verify_requires_assignment_completed_state_after_completion_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_generic_assignment_completed_as_final_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "ui assignment completed assignmentId=assign-1 lessonId=lesson-a courseId=course-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is False
    assert checks["assignment_final_completed"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_stale_completed_state_before_completion_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is False
    assert checks["assignment_final_completed"]["ok"] is False
    assert "after_completion=none" in checks["assignment_final_completed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_assignment_completed_status_500():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 500 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is False
    assert checks["assignment_final_completed"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_assignment_completed_false_flag():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED completed=false",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_posted"]["ok"] is True
    assert checks["assignment_completed"]["ok"] is False
    assert checks["assignment_final_completed"]["ok"] is False


def test_lesson_e2e_log_verify_rejects_invalid_final_assignment_completed_payload():
    module = load_module()
    base_lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    for marker in (
        "persisted=false",
        "finalized=false",
        "accepted=false",
        "cancelled=true",
        "canceled=true",
        "archived=true",
        "expired=true",
    ):
        report = module.evaluate_lesson_logs(
            [*base_lines, f"assignment/current -> 200 assignmentId=assign-1 state=COMPLETED {marker}"],
            device_id="14:c1:9f:d1:a8:48",
        )

        checks = by_name(report)
        assert report["ok"] is False
        assert checks["lesson_completion_posted"]["ok"] is True
        assert checks["assignment_completed"]["ok"] is False
        assert checks["assignment_final_completed"]["ok"] is False


def test_lesson_e2e_log_verify_rejects_cached_assignment_completed_as_final_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 persisted=true",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED cached=true source=cache",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_completed"]["ok"] is False
    assert checks["assignment_final_completed"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_lesson_activity_after_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_completed"]["ok"] is True
    assert checks["lesson_quiescent_after_completion"]["ok"] is False
    assert "activity_after_completion=s2" in checks["lesson_quiescent_after_completion"]["evidence"]

def test_lesson_e2e_log_verify_rejects_audio_or_render_after_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
        "serial Audio TTS played stepId=s1 primaryWord=barn replay_after_completion=true",
        "I (777) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach after completion",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_quiescent_after_completion"]["ok"] is False
    assert "activity_after_completion=s1" in checks["lesson_quiescent_after_completion"]["evidence"]

def test_lesson_e2e_log_verify_rejects_child_response_after_completion():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    lines.append("serial interactive child response accepted stepId=s4 recognizedText=barn late_after_completion=true")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_quiescent_after_completion"]["ok"] is False
    assert "activity_after_completion=s4" in checks["lesson_quiescent_after_completion"]["evidence"]

def test_lesson_e2e_log_verify_rejects_assignment_state_regression_after_completed():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
        "assignment/current -> 200 assignmentId=assign-1 state=RUNNING",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["assignment_completed"]["ok"] is True
    assert checks["assignment_final_completed"]["ok"] is False
    assert "final_state=running" in checks["assignment_final_completed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_websocket_disconnected_as_connected():
    module = load_module()
    lines = [
        "websocket disconnected device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_websocket_opened_false_as_connected():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket opened=false device_id=14:c1:9f:d1:a8:48 session=sess-1 state=connecting",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_websocket_numeric_false_flags():
    module = load_module()

    for marker in ("opened=0", '"opened":0', "connected=0", '"connected":0'):
        assert module._websocket_connected(
            f"websocket opened device_id=14:c1:9f:d1:a8:48 session=sess-1 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_websocket_expired_session_as_connected():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket session expired device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["websocket_connected"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_wifi_disconnect_or_failure_errors():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (115) WiFi: wifi failed to connect ssid=Van_Phong_Tam_Dentist",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["wifi_connected"]["ok"] is False
    assert checks["fatal_errors"]["ok"] is False
    assert "wifi failed" in checks["fatal_errors"]["evidence"].lower()

def test_lesson_e2e_log_verify_requires_step_ack_not_any_rendered_ack():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 body.acks=2 rendered=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_step_ack"]["ok"] is False

def test_lesson_e2e_log_verify_requires_prepare_and_start_ack_counts_not_only_sequence():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=1 body.acks=0 rendered=false",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=2 body.acks=0 rendered=false",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_negative_prepare_and_start_ack_flags():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=1 body.acks=1 accepted=false rendered=false",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=2 body.acks=2 ack=false rendered=false",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_ack_as_robot_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        "backend post lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=1 body.acks=1 rendered=false persisted=true",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "backend post lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=2 body.acks=2 rendered=false persisted=true",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        "backend post lesson_ack assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 body.acks=3 rendered=true degraded=false robotState=talking persisted=true",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_ack=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_requires_exact_ack_counts_not_prefix_matches():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 body.acks=10 rendered=false",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 body.acks=20 rendered=false",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 body.acks=30 rendered=true degraded=false",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_prepare_ack"]["ok"] is False
    assert checks["lesson_start_ack"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_degraded_render_as_not_normal_learning():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":true}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["render_not_degraded"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_degraded_step_ack_as_missing_normal_ack():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":true,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert checks["render_not_degraded"]["ok"] is False
    assert "missing_ack=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_passive_step_render_as_not_normal_learning():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "I (667) Lesson: lesson_step rendered stepId=s1 passive=1 degraded=0",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["render_not_degraded"]["ok"] is False
    assert "passive=1" in checks["render_not_degraded"]["evidence"]

def test_lesson_e2e_log_verify_rejects_numeric_degraded_render_flag():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step rendered degraded=1 url=https://ota.example/poster.jpg",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["render_not_degraded"]["ok"] is False
    assert "degraded=1" in checks["render_not_degraded"]["evidence"]

def test_lesson_e2e_log_verify_rejects_fallback_default_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 fallback default background rendered",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_step_ack"]["ok"] is True
    assert checks["render_not_degraded"]["ok"] is True
    assert checks["render_not_fallback"]["ok"] is False
    assert "fallback default background" in checks["render_not_fallback"]["evidence"]

def test_lesson_e2e_log_verify_rejects_primitive_fallback_card_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 primitiveFallbackCard displayed",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["render_not_fallback"]["ok"] is False
    assert "primitiveFallbackCard" in checks["render_not_fallback"]["evidence"]

def test_lesson_e2e_log_verify_rejects_rendered_background_from_different_media_url():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-a.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-b.jpg",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert checks["render_media_consistent"]["ok"] is False
    assert "poster-a.jpg" in checks["render_media_consistent"]["evidence"]
    assert "poster-b.jpg" in checks["render_media_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_wrong_background_media_on_later_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["render_media_consistent"]["ok"] is False
    assert "s1" in checks["render_media_consistent"]["evidence"]
    assert "s2" in checks["render_media_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_step_without_declared_media_url():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=none",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["lesson_step_media_declared"]["ok"] is False
    assert checks["background_rendered"]["ok"] is True
    assert "stepMedia=none" in checks["lesson_step_media_declared"]["evidence"]

def test_lesson_e2e_log_verify_rejects_background_render_without_rendered_media_url():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_media_declared"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True
    assert checks["background_render_media_declared"]["ok"] is False
    assert "renderMedia=none" in checks["background_render_media_declared"]["evidence"]

def test_lesson_e2e_log_verify_rejects_zero_byte_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg bytes=0",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_render=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_zero_frame_video_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"video":{"src":"https://ota.example/clip.mp4"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 video rendered url=https://ota.example/clip.mp4 frames=0",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_render=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_poster_drawn_false_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg drawn=false",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_render=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_background_render_numeric_false_flags():
    module = load_module()

    for marker in ("drawn=0", '"drawn":0', "rendered=0", '"rendered":0', "visible=0", '"visible":0'):
        assert module._background_rendered(
            f"I (666) Lesson: stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_background_render_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._background_rendered(
            f"I (666) Lesson: stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_cancelled_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 background rendered url=https://ota.example/poster.jpg cancelled=true",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_render=s4" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_negative_background_render_evidence():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 background rendered=false url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["background_rendered"]["ok"] is False
    assert checks["background_render_media_declared"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_teaching_object_rendered_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn rendered=false",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_content_rendered"]["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_content=s4" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_teaching_object_numeric_false_flags():
    module = load_module()

    for marker in ("rendered=0", '"rendered":0', "displayed=0", '"displayed":0', "visible=0", '"visible":0'):
        assert module._lesson_content_rendered(
            f"I (666) Lesson: stepId=s4 teachingObject rendered primaryWord=barn {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_teaching_object_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._lesson_content_rendered(
            f"I (666) Lesson: stepId=s4 teachingObject rendered primaryWord=barn {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_cancelled_teaching_object_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn cancelled=true",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_content_rendered"]["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_content=s4" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_teaching_object_primary_word_mismatch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=hay",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["teaching_object_primary_word_consistent"]["ok"] is False
    assert "stepPrimaryWords=barn" in checks["teaching_object_primary_word_consistent"]["evidence"]
    assert "renderedPrimaryWords=hay" in checks["teaching_object_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_teaching_object_without_primary_word_when_step_declares_word():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered objectId=obj-barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["teaching_object_primary_word_consistent"]["ok"] is False
    assert "missing_rendered_primary_word=s1" in checks["teaching_object_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_wrong_teaching_object_on_later_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=barn",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["teaching_object_primary_word_consistent"]["ok"] is False
    assert "s2" in checks["teaching_object_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_robot_overlay_rendered_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach rendered=false",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_overlay_rendered"]["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_overlay=s4" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_robot_overlay_numeric_false_flags():
    module = load_module()

    for marker in ("rendered=0", '"rendered":0', "displayed=0", '"displayed":0', "visible=0", '"visible":0'):
        assert module._robot_overlay_rendered(
            f"I (666) Lesson: stepId=s4 robotOverlay rendered robotState=talking pose=teach {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_robot_overlay_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert module._robot_overlay_rendered(
            f"I (666) Lesson: stepId=s4 robotOverlay rendered robotState=talking pose=teach {marker}"
        ) is False


def test_lesson_e2e_log_verify_rejects_robot_overlay_state_mismatch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_overlay_state_consistent"]["ok"] is False
    assert "stepRobotStates=talking" in checks["robot_overlay_state_consistent"]["evidence"]
    assert "renderedRobotStates=listening" in checks["robot_overlay_state_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_robot_overlay_without_robot_state_when_step_declares_state():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_overlay_state_consistent"]["ok"] is False
    assert "missing_rendered_robot_state=s1" in checks["robot_overlay_state_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_wrong_robot_overlay_state_on_later_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s2 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["robot_overlay_state_consistent"]["ok"] is False
    assert "s2" in checks["robot_overlay_state_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_audio_primary_word_mismatch_when_logged():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 lesson_step poster fetched+drawn from URL url=https://ota.example/poster.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s4 primaryWord=hay",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_primary_word_consistent"]["ok"] is False
    assert "stepPrimaryWords=barn" in checks["lesson_audio_primary_word_consistent"]["evidence"]
    assert "audioPrimaryWords=hay" in checks["lesson_audio_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_audio_without_primary_word_when_step_declares_word():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_primary_word_consistent"]["ok"] is False
    assert "missing_audio_primary_word=s1" in checks["lesson_audio_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_rejects_wrong_audio_primary_word_on_later_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=5 stepId=s2 backgroundScene.poster.src=https://ota.example/poster-s2.jpg teachingObject.subject.primaryWord=hay robotOverlay.robotState=listening",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "serial Audio TTS played stepId=s2 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"listening"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_primary_word_consistent"]["ok"] is False
    assert "s2" in checks["lesson_audio_primary_word_consistent"]["evidence"]

def test_lesson_e2e_log_verify_requires_successful_lesson_progress():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"failed"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_non_success_progress_prefix_match():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        "serial TX lesson_progress assignmentId=assign-1 sessionId=sess-1 event=step_completed result=not_success",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_lesson_progress_success_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success","success":false}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success success=false persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False
    assert checks["lesson_progress_posted"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_server_declared_render_as_background_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step rendered=true stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["background_rendered"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_server_declared_content_and_overlay_render():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s1 backgroundScene.poster.src=https://ota.example/poster-s1.jpg teachingObject rendered=true primaryWord=barn robotOverlay rendered=true robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sent"]["ok"] is True
    assert checks["background_rendered"]["ok"] is True
    assert checks["lesson_content_rendered"]["ok"] is False
    assert checks["robot_overlay_rendered"]["ok"] is False
    assert checks["lesson_step_content_layers"]["ok"] is False
    assert "missing_content=s1" in checks["lesson_step_content_layers"]["evidence"]
    assert "missing_overlay=s1" in checks["lesson_step_content_layers"]["evidence"]

def test_lesson_e2e_log_verify_rejects_start_ack_tts_as_lesson_step_audio():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_audio=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_step_scoped_start_ack_tts_as_lesson_audio():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS playback complete reason=start_lesson_ack stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_audio_played"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_audio=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_requires_robot_progress_not_only_backend_post():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False
    assert checks["lesson_progress_posted"]["ok"] is True

def test_lesson_e2e_log_verify_requires_robot_progress_for_manifest_completion_checks():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s1"},{"id":"s2"}]}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}},"teachingObject":{"subject":{"primaryWord":"cow"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s2",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=cow",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s2 primaryWord=cow",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s2 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":7}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=7",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress"]["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is False
    assert checks["lesson_manifest_step_ids"]["ok"] is False
    assert checks["lesson_manifest_step_order"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_duplicate_lesson_progress_for_same_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true duplicate=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":6}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=6",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_unique"]["ok"] is False
    assert "duplicate_progress=s1:2" in checks["lesson_progress_unique"]["evidence"]
    assert "duplicate_posted=s1:2" in checks["lesson_progress_unique"]["evidence"]

def test_lesson_e2e_log_verify_rejects_duplicate_step_playback_for_same_step():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg replay=true",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn replay=true",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach replay=true",
        "serial Audio TTS played stepId=s1 primaryWord=barn replay=true",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"acks":4,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":6}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=6",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_playback_unique"]["ok"] is False
    assert "duplicate_step=s1:2" in checks["lesson_step_playback_unique"]["evidence"]
    assert "duplicate_audio=s1:2" in checks["lesson_step_playback_unique"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_step_ack_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking","ack":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_ack"]["ok"] is False
    assert checks["lesson_steps_observed"]["ok"] is False
    assert "missing_ack=s1" in checks["lesson_steps_observed"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_step_ack_numeric_false_flags():
    module = load_module()

    markers = (
        "ack=0",
        '"ack":0',
        "acked=0",
        '"acked":0',
        "accepted=0",
        '"accepted":0',
        "displayed=0",
        '"displayed":0',
        "visible=0",
        '"visible":0',
    )

    for marker in markers:
        assert module._lesson_step_rendered_ack(
            f"serial TX lesson_ack body.acks=3 rendered=true degraded=false stepId=s1 {marker}"
        ) is False

    for marker in ("ack=0", '"ack":0', "acked=0", '"acked":0', "accepted=0", '"accepted":0'):
        assert module._lesson_ack_positive(3)(
            f"serial TX lesson_ack body.acks=3 rendered=true degraded=false stepId=s1 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_lesson_ack_positive_numeric_true_cancellation_flags():
    module = load_module()

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for ack_count in (1, 2, 3):
        for marker in markers:
            assert module._lesson_ack_positive(ack_count)(
                f"serial TX lesson_ack body.acks={ack_count} rendered=true degraded=false stepId=s1 {marker}"
            ) is False

def test_lesson_e2e_log_verify_rejects_lesson_frame_sequence_rollback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=2",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_sequence_monotonic"]["ok"] is False
    assert "lesson_stop:2" in checks["lesson_sequence_monotonic"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_step_ack_sequence_mismatch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_ack_sequence_match"]["ok"] is False
    assert "missing_ack=lesson_step:3" in checks["lesson_ack_sequence_match"]["evidence"]
    assert "unexpected_ack=lesson_step:2" in checks["lesson_ack_sequence_match"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_stop_receive_sequence_mismatch():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sequence_match"]["ok"] is False
    assert "sent=5" in checks["lesson_stop_sequence_match"]["evidence"]
    assert "received=4" in checks["lesson_stop_sequence_match"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_stop_received_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5 received=false",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_received"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_lesson_progress_sequence_rollback():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_sequence_after_step"]["ok"] is False
    assert "stale_progress=s1:2<3" in checks["lesson_progress_sequence_after_step"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_stop_sequence_before_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sequence_after_progress"]["ok"] is False
    assert "stale_stop=5<6" in checks["lesson_stop_sequence_after_progress"]["evidence"]

def test_lesson_e2e_log_verify_requires_positive_lesson_stop_frame():
    module = load_module()
    lines = [
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 lesson_step poster fetched+drawn from URL",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"body":{"event":"step_completed","result":"success"}}',
        "debug assignmentId=assign-1 sessionId=sess-1 lesson_stop not sent yet",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is False
    assert checks["lesson_completed"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_lesson_stop_sent_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"sent":false}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is False
    assert checks["lesson_stop_received"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_cancelled_lesson_stop_frame():
    module = load_module()
    lesson_stop_frame = module._sent_frame("lesson_stop")

    for marker in ("cancelled=true", "canceled=true", "aborted=true", "interrupted=true", "stopped=true"):
        assert lesson_stop_frame(
            f"server send lesson_stop assignmentId=assign-1 sessionId=sess-1 sequence=5 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_lesson_stop_frame_numeric_true_cancellation_flags():
    module = load_module()
    lesson_stop_frame = module._sent_frame("lesson_stop")

    markers = (
        "cancelled=1",
        '"cancelled":1',
        "canceled=1",
        '"canceled":1',
        "aborted=1",
        '"aborted":1',
        "interrupted=1",
        '"interrupted":1',
        "stopped=1",
        '"stopped":1',
    )

    for marker in markers:
        assert lesson_stop_frame(
            f"server send lesson_stop assignmentId=assign-1 sessionId=sess-1 sequence=5 {marker}"
        ) is False

def test_lesson_e2e_log_verify_rejects_flow_with_cancelled_lesson_stop_frame():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5,"cancelled":true}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is False
    assert checks["lesson_stop_received"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_lesson_stop_cleared_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5 cleared=false",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_sent"]["ok"] is True
    assert checks["lesson_stop_received"]["ok"] is False

def test_lesson_e2e_log_verify_rejects_lesson_stop_before_all_step_progress():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=2",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":7,"stepId":"s2","body":{"event":"step_completed","result":"success"}}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_progress_count"]["ok"] is True
    assert checks["lesson_stop_after_progress"]["ok"] is False
    assert "progress_after_stop=s2" in checks["lesson_stop_after_progress"]["evidence"]

def test_lesson_e2e_log_verify_rejects_lesson_activity_after_stop_before_completion():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}}}}',
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "serial Audio TTS played stepId=s1",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s2.jpg"}}}}}',
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s2.jpg",
        "serial Audio TTS played stepId=s2",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":6,"stepId":"s2","body":{"acks":4,"rendered":true,"degraded":false}}',
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_stop_after_progress"]["ok"] is True
    assert checks["lesson_quiescent_after_stop"]["ok"] is False
    assert "activity_after_stop=s2" in checks["lesson_quiescent_after_stop"]["evidence"]

def test_lesson_e2e_log_verify_rejects_child_response_after_stop_before_completion():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line([{"id": "s4", "type": "say", "completionClass": "interactive"}]),
        step_id="s4",
        include_child_response=True,
    )
    response_line = "serial interactive child response accepted stepId=s4 recognizedText=barn late_after_stop=true"
    completion_index = lines.index("LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1")
    lines.insert(completion_index, response_line)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_quiescent_after_stop"]["ok"] is False
    assert "activity_after_stop=s4" in checks["lesson_quiescent_after_stop"]["evidence"]

def test_lesson_e2e_log_verify_rejects_content_and_overlay_render_after_stop():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 teachingObject rendered primaryWord=hay",
        "I (667) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s2 robotOverlay rendered robotState=listening pose=listen",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 persisted=true",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_quiescent_after_stop"]["ok"] is False
    assert "activity_after_stop=s2" in checks["lesson_quiescent_after_stop"]["evidence"]

def test_lesson_e2e_log_verify_cli_reads_multiple_files(tmp_path):
    first_log = tmp_path / "timeline-1.log"
    second_log = tmp_path / "timeline-2.log"
    first_log.write_text(
        "\n".join(
            [
                "I (100) Application: TBOT firmware boot complete",
                "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
                "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
                "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
                "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
                "tts playback complete reason=start_lesson_ack",
                "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
                'manifest fetched lesson=w01-d01-barn-say-it profile=espTft {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
                "server send lesson_prepare assignmentId=assign-1 sequence=1",
                "serial RX lesson_prepare seq=1",
                "serial TX lesson_ack body.acks=1 rendered=false",
                "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
                "server send lesson_start assignmentId=assign-1 sequence=2",
                "serial RX lesson_start seq=2",
                "serial TX lesson_ack body.acks=2 rendered=false",
                "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
            ]
        ),
        encoding="utf-8",
    )
    second_log.write_text(
        "\n".join(
            [
                "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
                "serial RX lesson_step stepId=s4",
                "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
                "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
                "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
                "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
                "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
                "LessonRuntime step prompt stepId=s4 text=Can you say barn?",
                "serial Audio TTS played stepId=s4 primaryWord=barn",
                "LessonRuntime child response window opened stepId=s4 listening=true",
                "serial interactive child response accepted stepId=s4 recognizedText=barn",
                "serial TX lesson_progress event=step_completed result=success stepId=s4",
                "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
                "server send lesson_stop assignmentId=assign-1 sequence=4",
                "serial RX lesson_stop seq=4",
                "LessonRuntime event lesson_completed assignmentId=assign-1",
                "backend post lesson_completed assignmentId=assign-1",
                "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
                "--log-file",
                str(first_log),
                "--log-file",
                str(second_log),
            ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True


def test_lesson_e2e_log_verify_accepts_actual_firmware_and_json_frame_strings():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-real-1",
        "I (330) Voice: intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "I (331) Audio: tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "I (332) Audio: tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        'LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1 {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}},"teachingObject":{"asset":{"src":"https://ota.example/object.png"},"subject":{"primaryWord":"barn"}},"robotOverlay":{"asset":{"src":"https://ota.example/overlay.png"},"robotState":"talking"}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "I (666) Lesson: teachingObject rendered primaryWord=barn stepId=s4",
        "I (666) Lesson: robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "I (667) Lesson: lesson_step rendered stepId=s4 passive=0 degraded=0",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        "LessonRuntime step prompt stepId=s4 text=Can you say barn?",
        "I (668) Audio: tts playback complete stepId=s4 primaryWord=barn",
        "LessonRuntime child response window opened stepId=s4 listening=true",
        "serial interactive child response accepted stepId=s4 recognizedText=barn",
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-real-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-real-1","sequence":4}',
        "I (669) Lesson: lesson_stop background cleared assignmentId=assign-1 sessionId=sess-real-1",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-real-1",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    assert report["ok"] is True

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_missing_asset_key():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = (
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1",'
        '"sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},'
        '"criticalAssets":["backgroundScene.poster","teachingObject.barn","robotOverlay.teach"],'
        '"assetPack":{"ready":true,"cacheKey":"w01-d01-barn-say-it/v3-abcdef12",'
        '"assets":[{"localPath":"sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/backgroundScene.poster",'
        '"state":"READY","checksumOk":true},'
        '{"key":"teachingObject.barn","localPath":"sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/teachingObject.barn",'
        '"state":"READY","checksumOk":true},'
        '{"key":"robotOverlay.teach","localPath":"sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/robotOverlay.teach",'
        '"state":"READY","checksumOk":true}]}}}'
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "missing_key=1" in checks["lesson_asset_pack_keys_present"]["evidence"]
    assert "missing_required=backgroundscene.poster" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_duplicate_asset_key():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {"key": "backgroundScene.poster", "localPath": f"{local_root}/backgroundScene.poster"},
                        {"key": "backgroundScene.poster", "localPath": f"{local_root}/backgroundScene.poster-copy"},
                        {"key": "teachingObject.barn", "localPath": f"{local_root}/teachingObject.barn"},
                        {"key": "robotOverlay.teach", "localPath": f"{local_root}/robotOverlay.teach"},
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "duplicate_key=backgroundscene.poster" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_missing_local_path():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {"key": "backgroundScene.poster"},
                        {"key": "teachingObject.barn", "localPath": f"{local_root}/teachingObject.barn"},
                        {"key": "robotOverlay.teach", "localPath": f"{local_root}/robotOverlay.teach"},
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "missing_localPath=1" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_remote_local_path():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {"key": "backgroundScene.poster", "localPath": "https://cdn.example/backgroundScene.poster"},
                        {"key": "teachingObject.barn", "localPath": f"{local_root}/teachingObject.barn"},
                        {"key": "robotOverlay.teach", "localPath": f"{local_root}/robotOverlay.teach"},
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "remote_localPath=1" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_without_declared_size():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "missing_size=1" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_non_ready_asset_state():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "DOWNLOADING",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "non_ready_asset=1" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_asset_pack_with_failed_checksum():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": False,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_keys_present"]["ok"] is False
    assert "checksum_failed=1" in checks["lesson_asset_pack_keys_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_step_source_not_from_ready_pack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-abcdef12"},
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {"poster": {"src": "https://ota.example/poster-s4.jpg"}},
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "invalid_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_accepts_sd_pack_step_sources_from_ready_pack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-abcdef12"},
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is True
    assert "invalid_sources=none" in checks["lesson_step_sd_pack_sources_attested"]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_step_sources_without_ready_asset_pack_evidence():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "assetPack=not_ready" in checks["lesson_step_sd_pack_sources_attested"]["evidence"]
    assert "unattested_local_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]


def test_lesson_e2e_log_verify_rejects_text_sd_step_sources_without_ready_asset_pack_evidence():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[13] = (
        "server send lesson_step assignmentId=assign-1 sessionId=sess-1 sequence=3 stepId=s4 "
        f"backgroundScene.poster.src={local_root}/backgroundScene.poster "
        f"teachingObject.asset.src={local_root}/teachingObject.barn "
        f"robotOverlay.asset.src={local_root}/robotOverlay.teach"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "assetPack=not_ready" in checks["lesson_step_sd_pack_sources_attested"]["evidence"]
    assert "unattested_local_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_accepts_text_asset_pack_ready_ack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = (
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 "
        "sequence=1 body.acks=1 rendered=false assetPack.ready=true "
        "cacheKey=w01-d01-barn-say-it/v3-abcdef12"
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is True
    assert "ack_ready=assign-1:sess-1:w01-d01-barn-say-it/v3-abcdef12" in checks[
        "lesson_asset_pack_ack_ready"
    ]["evidence"]

def test_lesson_e2e_log_verify_accepts_text_asset_pack_ready_ack_without_identity_when_single_pack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = (
        "serial TX lesson_ack sequence=1 body.acks=1 rendered=false "
        "assetPack.ready=true cacheKey=w01-d01-barn-say-it/v3-abcdef12"
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is True
    assert "ack_ready=assign-1:sess-1:w01-d01-barn-say-it/v3-abcdef12" in checks[
        "lesson_asset_pack_ack_ready"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_asset_pack_ready_ack_after_lesson_start():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}'
    lines.insert(
        11,
        "serial TX lesson_ack assignmentId=assign-1 sessionId=sess-1 "
        "sequence=1 body.acks=1 rendered=false assetPack.ready=true "
        "cacheKey=w01-d01-barn-say-it/v3-abcdef12",
    )
    lines[14] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[17] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is False
    assert "late_ack=assign-1:sess-1:w01-d01-barn-say-it/v3-abcdef12" in checks[
        "lesson_asset_pack_ack_ready"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_json_asset_pack_ready_ack_with_blank_primary_cache_key_even_with_alias():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[8] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {
                    "ready": True,
                    "cacheKey": "",
                    "cache_key": "w01-d01-barn-say-it/v3-abcdef12",
                },
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is False
    assert "invalid_ack_cache_key=assign-1:sess-1" in checks["lesson_asset_pack_ack_ready"]["evidence"]

def test_lesson_e2e_log_verify_rejects_text_asset_pack_ready_ack_without_identity_when_multiple_packs():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    ready_pack = {
        "manifestRef": {"manifestChecksum": "abc"},
        "criticalAssets": [
            "backgroundScene.poster",
            "teachingObject.barn",
            "robotOverlay.teach",
        ],
        "assetPack": {
            "ready": True,
            "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
            "assets": [
                {
                    "key": "backgroundScene.poster",
                    "localPath": f"{local_root}/backgroundScene.poster",
                    "state": "READY",
                    "checksumOk": True,
                    "size": 1234,
                },
                {
                    "key": "teachingObject.barn",
                    "localPath": f"{local_root}/teachingObject.barn",
                    "state": "READY",
                    "checksumOk": True,
                    "size": 1234,
                },
                {
                    "key": "robotOverlay.teach",
                    "localPath": f"{local_root}/robotOverlay.teach",
                    "state": "READY",
                    "checksumOk": True,
                    "size": 1234,
                },
            ],
        },
    }
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": ready_pack,
        },
        separators=(",", ":"),
    )
    lines.insert(
        8,
        json.dumps(
            {
                "type": "lesson_prepare",
                "assignmentId": "assign-2",
                "sessionId": "sess-2",
                "sequence": 1,
                "body": ready_pack,
            },
            separators=(",", ":"),
        ),
    )
    lines[9] = (
        "serial TX lesson_ack sequence=1 body.acks=1 rendered=false "
        "assetPack.ready=true cacheKey=w01-d01-barn-say-it/v3-abcdef12"
    )
    lines[14] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is False
    assert "ack_ready=?:?:w01-d01-barn-say-it/v3-abcdef12" in checks[
        "lesson_asset_pack_ack_ready"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_without_firmware_asset_pack_ready_ack():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "teachingObject": {
                        "asset": {"key": "teachingObject.barn", "src": f"{local_root}/teachingObject.barn"}
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )
    lines[15] = (
        f"I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s4 "
        f"lesson_step poster fetched+drawn from URL url={local_root}/backgroundScene.poster"
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_ack_ready"]["ok"] is False
    assert "ack_ready=none" in checks["lesson_asset_pack_ack_ready"]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_step_source_from_wrong_asset_key():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{local_root}/teachingObject.barn",
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{local_root}/robotOverlay.teach",
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {
                        "poster": {
                            "key": "backgroundScene.poster",
                            "src": f"{local_root}/teachingObject.barn",
                        }
                    },
                    "teachingObject": {
                        "asset": {
                            "key": "teachingObject.barn",
                            "src": f"{local_root}/backgroundScene.poster",
                        }
                    },
                    "robotOverlay": {
                        "asset": {"key": "robotOverlay.teach", "src": f"{local_root}/robotOverlay.teach"}
                    },
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "wrong_asset_key_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_step_source_without_layer_asset_key():
    module = load_module()
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {"key": "backgroundScene.poster", "localPath": f"{local_root}/backgroundScene.poster"},
                        {"key": "teachingObject.barn", "localPath": f"{local_root}/teachingObject.barn"},
                        {"key": "robotOverlay.teach", "localPath": f"{local_root}/robotOverlay.teach"},
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {"poster": {"src": f"{local_root}/backgroundScene.poster"}},
                    "teachingObject": {"asset": {"src": f"{local_root}/teachingObject.barn"}},
                    "robotOverlay": {"asset": {"src": f"{local_root}/robotOverlay.teach"}},
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "missing_asset_key_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_step_source_from_previous_session_pack():
    module = load_module()
    old_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v2-oldcache"
    current_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    old_prepare = {
        "type": "lesson_prepare",
        "assignmentId": "assign-1",
        "sessionId": "sess-old",
        "sequence": 1,
        "body": {
            "manifestRef": {"manifestChecksum": "old"},
            "criticalAssets": [
                "backgroundScene.poster",
                "teachingObject.barn",
                "robotOverlay.teach",
            ],
            "assetPack": {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v2-oldcache",
                "assets": [
                    {"key": "backgroundScene.poster", "localPath": f"{old_root}/backgroundScene.poster"},
                    {"key": "teachingObject.barn", "localPath": f"{old_root}/teachingObject.barn"},
                    {"key": "robotOverlay.teach", "localPath": f"{old_root}/robotOverlay.teach"},
                ],
            },
        },
    }
    current_prepare = {
        "type": "lesson_prepare",
        "assignmentId": "assign-1",
        "sessionId": "sess-1",
        "sequence": 1,
        "body": {
            "manifestRef": {"manifestChecksum": "abc"},
            "criticalAssets": [
                "backgroundScene.poster",
                "teachingObject.barn",
                "robotOverlay.teach",
            ],
            "assetPack": {
                "ready": True,
                "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                "assets": [
                    {"key": "backgroundScene.poster", "localPath": f"{current_root}/backgroundScene.poster"},
                    {"key": "teachingObject.barn", "localPath": f"{current_root}/teachingObject.barn"},
                    {"key": "robotOverlay.teach", "localPath": f"{current_root}/robotOverlay.teach"},
                ],
            },
        },
    }
    lines.insert(7, json.dumps(old_prepare, separators=(",", ":")))
    lines[8] = json.dumps(current_prepare, separators=(",", ":"))
    lines[14] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {"poster": {"src": f"{old_root}/backgroundScene.poster"}},
                    "teachingObject": {"asset": {"key": "teachingObject.barn", "src": f"{old_root}/teachingObject.barn"}},
                    "robotOverlay": {"asset": {"key": "robotOverlay.teach", "src": f"{old_root}/robotOverlay.teach"}},
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "stale_session_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_sd_pack_step_source_without_current_session_pack():
    module = load_module()
    old_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v2-oldcache"
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [
                {
                    "id": "s4",
                    "type": "say_it",
                    "completionClass": "interactive",
                }
            ]
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-old",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "old"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v2-oldcache",
                    "assets": [
                        {"key": "backgroundScene.poster", "localPath": f"{old_root}/backgroundScene.poster"},
                        {"key": "teachingObject.barn", "localPath": f"{old_root}/teachingObject.barn"},
                        {"key": "robotOverlay.teach", "localPath": f"{old_root}/robotOverlay.teach"},
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[13] = json.dumps(
        {
            "type": "lesson_step",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 3,
            "stepId": "s4",
            "body": {
                "scene": {
                    "backgroundScene": {"poster": {"src": f"{old_root}/backgroundScene.poster"}},
                    "teachingObject": {"asset": {"key": "teachingObject.barn", "src": f"{old_root}/teachingObject.barn"}},
                    "robotOverlay": {"asset": {"key": "robotOverlay.teach", "src": f"{old_root}/robotOverlay.teach"}},
                },
                "subject": {"primaryWord": "barn"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_step_sd_pack_sources_attested"]["ok"] is False
    assert "missing_identity_pack_sources=s4:backgroundScene.poster.src" in checks[
        "lesson_step_sd_pack_sources_attested"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_sd_pack_cache_key_from_stale_manifest_checksum():
    module = load_module()
    old_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-oldcache1"
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft manifestChecksum=abcdef1234567890 "
        + json.dumps(
            {
                "steps": [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abcdef1234567890"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-oldcache1",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{old_root}/backgroundScene.poster",
                            "size": 120,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{old_root}/teachingObject.barn",
                            "size": 121,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{old_root}/robotOverlay.teach",
                            "size": 122,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[9] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-oldcache1"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_cache_key_matches_manifest_checksum"]["ok"] is False
    assert "stale_cache_key=assign-1:sess-1:w01-d01-barn-say-it/v3-oldcache1" in checks[
        "lesson_asset_pack_cache_key_matches_manifest_checksum"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_sd_pack_cache_key_with_only_checksum_prefix():
    module = load_module()
    prefix_only_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft manifestChecksum=abcdef1234567890 "
        + json.dumps(
            {
                "steps": [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abcdef1234567890"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{prefix_only_root}/backgroundScene.poster",
                            "size": 120,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{prefix_only_root}/teachingObject.barn",
                            "size": 121,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{prefix_only_root}/robotOverlay.teach",
                            "size": 122,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[9] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-abcdef12"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_cache_key_matches_manifest_checksum"]["ok"] is False
    assert "stale_cache_key=assign-1:sess-1:w01-d01-barn-say-it/v3-abcdef12" in checks[
        "lesson_asset_pack_cache_key_matches_manifest_checksum"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_ready_sd_pack_non_string_primary_cache_key_even_with_alias():
    module = load_module()
    current_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef1234567890"
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft manifestChecksum=abcdef1234567890 "
        + json.dumps(
            {
                "steps": [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abcdef1234567890"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": 123,
                    "cache_key": "w01-d01-barn-say-it/v3-abcdef1234567890",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{current_root}/backgroundScene.poster",
                            "size": 120,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{current_root}/teachingObject.barn",
                            "size": 121,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{current_root}/robotOverlay.teach",
                            "size": 122,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[9] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-abcdef1234567890"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_asset_pack_cache_key_matches_manifest_checksum"]["ok"] is False
    assert "invalid_cache_key=assign-1:sess-1" in checks[
        "lesson_asset_pack_cache_key_matches_manifest_checksum"
    ]["evidence"]

def test_lesson_e2e_log_verify_accepts_ready_sd_pack_cache_key_for_current_manifest_checksum():
    module = load_module()
    current_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef1234567890"
    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft manifestChecksum=abcdef1234567890 "
        + json.dumps(
            {
                "steps": [
                    {
                        "id": "s4",
                        "type": "say_it",
                        "completionClass": "interactive",
                    }
                ],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abcdef1234567890"},
                "criticalAssets": [
                    "backgroundScene.poster",
                    "teachingObject.barn",
                    "robotOverlay.teach",
                ],
                "assetPack": {
                    "ready": True,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef1234567890",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{current_root}/backgroundScene.poster",
                            "size": 120,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "teachingObject.barn",
                            "localPath": f"{current_root}/teachingObject.barn",
                            "size": 121,
                            "state": "READY",
                            "checksumOk": True,
                        },
                        {
                            "key": "robotOverlay.teach",
                            "localPath": f"{current_root}/robotOverlay.teach",
                            "size": 122,
                            "state": "READY",
                            "checksumOk": True,
                        },
                    ],
                },
            },
        },
        separators=(",", ":"),
    )
    lines[9] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": "w01-d01-barn-say-it/v3-abcdef1234567890"},
            },
        },
        separators=(",", ":"),
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_cache_key_matches_manifest_checksum"]["ok"] is True

def test_lesson_e2e_log_verify_rejects_backend_completion_for_different_session():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-1",
        "I (330) Voice: intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "I (331) Audio: tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "I (332) Audio: tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "I (666) Lesson: teachingObject rendered primaryWord=barn stepId=s4",
        "I (666) Lesson: robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "I (667) Lesson: lesson_step rendered stepId=s4 passive=0 degraded=0",
        "I (668) Audio: tts playback complete stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":4}',
        "I (669) Lesson: lesson_stop background cleared assignmentId=assign-1 sessionId=sess-1",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-other",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_session_match"]["ok"] is False
    assert "completion_sessions=sess-other" in checks["lesson_completion_session_match"]["evidence"]
    assert "progress_sessions=sess-1" in checks["lesson_completion_session_match"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_completion_without_session_when_progress_has_session():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-1",
        "I (330) Voice: intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "I (331) Audio: tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "I (332) Audio: tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "I (666) Lesson: teachingObject rendered primaryWord=barn stepId=s4",
        "I (666) Lesson: robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "I (667) Lesson: lesson_step rendered stepId=s4 passive=0 degraded=0",
        "I (668) Audio: tts playback complete stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":4}',
        "I (669) Lesson: lesson_stop background cleared assignmentId=assign-1 sessionId=sess-1",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 persisted=true",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completion_session_match"]["ok"] is False
    assert "completion_sessions=none" in checks["lesson_completion_session_match"]["evidence"]
    assert "progress_sessions=sess-1" in checks["lesson_completion_session_match"]["evidence"]

def test_lesson_e2e_log_verify_rejects_backend_progress_without_session_when_robot_progress_has_session():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "I (2589) Application: passive_lesson_websocket_opened",
        "I (319) WebsocketProtocol: Session ID: sess-1",
        "I (330) Voice: intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "I (331) Audio: tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "I (332) Audio: tts playback complete reason=start_lesson_ack",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster.jpg"}}}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "I (666) Lesson: lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "I (666) Lesson: teachingObject rendered primaryWord=barn stepId=s4",
        "I (666) Lesson: robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "I (667) Lesson: lesson_step rendered stepId=s4 passive=0 degraded=0",
        "I (668) Audio: tts playback complete stepId=s4",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s4","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s4","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":4}',
        "I (669) Lesson: lesson_stop background cleared assignmentId=assign-1 sessionId=sess-1",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 persisted=true",
        "assignment/current -> 200 assignmentId=assign-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_backend_progress_session"]["ok"] is False
    assert "missing_posted_sessions=s4" in checks["lesson_backend_progress_session"]["evidence"]
    assert "progress_sessions=s4:sess-1" in checks["lesson_backend_progress_session"]["evidence"]

def test_lesson_e2e_log_verify_rejects_failed_backend_completion_status():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        "backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 status=500 persisted=false",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False

# Backend no-assignment scenario -- start phrase yields a no-current-assignment
# status and the robot does NOT fetch a default/fallback lesson.
def _no_assignment_passing_lines() -> list[str]:
    return [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 status=no_current_assignment assignment=null",
        "LessonRuntime start_lesson aborted reason=no_current_assignment status=no_current_assignment",
        "voice say text=\"Con chưa có bài học nào hôm nay.\" reason=no_current_assignment",
    ]


def test_lesson_e2e_log_verify_no_assignment_accepts_no_current_assignment_status():
    module = load_module()

    report = module.evaluate_no_assignment_logs(
        _no_assignment_passing_lines(), device_id="14:c1:9f:d1:a8:48"
    )

    assert report["ok"] is True
    checks = by_name(report)
    assert checks["start_lesson_requested"]["ok"] is True
    assert checks["no_current_assignment_status"]["ok"] is True
    assert checks["no_default_lesson_fetch"]["ok"] is True


def test_lesson_e2e_log_verify_no_assignment_rejects_default_manifest_fetch_after_start():
    module = load_module()
    lines = _no_assignment_passing_lines() + [
        "LessonRuntime manifest fetched lesson=default-fallback course=course-1 profile=espTft",
    ]

    report = module.evaluate_no_assignment_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["start_lesson_requested"]["ok"] is True
    assert checks["no_current_assignment_status"]["ok"] is True
    assert checks["no_default_lesson_fetch"]["ok"] is False


def test_lesson_e2e_log_verify_no_assignment_rejects_default_lesson_prepare_after_start():
    module = load_module()
    lines = _no_assignment_passing_lines() + [
        '{"type":"lesson_prepare","assignmentId":"default-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"}}}',
    ]

    report = module.evaluate_no_assignment_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["no_default_lesson_fetch"]["ok"] is False


def test_lesson_e2e_log_verify_no_assignment_rejects_missing_no_current_assignment_status():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900",
    ]

    report = module.evaluate_no_assignment_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["start_lesson_requested"]["ok"] is True
    assert checks["no_current_assignment_status"]["ok"] is False


def test_lesson_e2e_log_verify_no_assignment_rejects_missing_start_phrase():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 status=no_current_assignment assignment=null",
    ]

    report = module.evaluate_no_assignment_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["start_lesson_requested"]["ok"] is False


def test_lesson_e2e_log_verify_no_assignment_redacts_sensitive_tokens():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 status=no_current_assignment assignment=null",
        "LessonRuntime start_lesson aborted reason=no_current_assignment status=no_current_assignment",
    ]

    report = module.evaluate_no_assignment_logs(lines, device_id="14:c1:9f:d1:a8:48")

    assert report["ok"] is True
    assert "secret-token" not in json.dumps(report)

def test_lesson_e2e_log_verify_cli_accepts_no_assignment_scenario(tmp_path):
    log_file = tmp_path / "no-assignment.log"
    log_file.write_text("\n".join(_no_assignment_passing_lines()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scenario",
            "no-assignment",
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["no_current_assignment_status"]["ok"] is True
    assert checks["no_default_lesson_fetch"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_backend_completion_json_persisted_false():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts sentence_start text=\"Bat dau bai hoc nhe.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft assignmentId=assign-1 totalSteps=1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"acks":1,"rendered":false}}',
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":2,"body":{"acks":2,"rendered":false}}',
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":{"src":"https://ota.example/poster-s1.jpg"}}},"teachingObject":{"subject":{"primaryWord":"barn"}},"robotOverlay":{"robotState":"talking"}}}',
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s1",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 lesson_step poster fetched+drawn from URL url=https://ota.example/poster-s1.jpg",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 teachingObject rendered primaryWord=barn",
        "I (666) Lesson: assignmentId=assign-1 sessionId=sess-1 stepId=s1 robotOverlay rendered robotState=talking pose=teach",
        "serial Audio TTS played stepId=s1 primaryWord=barn",
        '{"type":"lesson_ack","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,"stepId":"s1","body":{"acks":3,"rendered":true,"degraded":false,"robotState":"talking"}}',
        '{"type":"lesson_progress","assignmentId":"assign-1","sessionId":"sess-1","sequence":4,"stepId":"s1","body":{"event":"step_completed","result":"success"}}',
        "backend post lesson_progress assignmentId=assign-1 sessionId=sess-1 stepId=s1 event=step_completed result=success persisted=true",
        '{"type":"lesson_stop","assignmentId":"assign-1","sessionId":"sess-1","sequence":5}',
        "serial RX lesson_stop assignmentId=assign-1 sessionId=sess-1 seq=5",
        "LessonRuntime event lesson_completed assignmentId=assign-1 sessionId=sess-1",
        'backend post lesson_completed assignmentId=assign-1 sessionId=sess-1 {"status":200,"persisted":false}',
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 state=COMPLETED",
    ]

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_completed"]["ok"] is True
    assert checks["lesson_completion_posted"]["ok"] is False


# --- Start phrase scoping (positive VI/EN phrases start, negatives do not) ---

def test_lesson_start_requested_accepts_additional_positive_phrases():
    module = load_module()

    # VI: "vào khóa học của con" (enter your course) — diacritic and ASCII-folded.
    assert module._lesson_start_requested(
        'voice intent text="vào khóa học của con" command=start_lesson handled=true'
    ) is True
    assert module._lesson_start_requested(
        'voice intent text="vao khoa hoc cua con" command=start_lesson handled=true'
    ) is True
    # VI: "tiếp tục khóa học" (continue the course) — diacritic and ASCII-folded.
    assert module._lesson_start_requested(
        'voice intent text="tiếp tục khóa học" command=start_lesson handled=true'
    ) is True
    assert module._lesson_start_requested(
        'voice intent text="tiep tuc khoa hoc" command=start_lesson handled=true'
    ) is True
    # The originally-supported phrasing must keep working.
    assert module._lesson_start_requested(
        'voice intent text="bắt đầu bài học" command=start_lesson handled=true'
    ) is True


def test_lesson_start_requested_rejects_negative_phrases():
    module = load_module()

    # "không vào khóa học" (do NOT enter the course) contains the positive substring
    # "vào khóa học" but must NOT be read as a start request.
    assert module._lesson_start_requested(
        'voice intent text="không vào khóa học" command=none handled=true'
    ) is False
    assert module._lesson_start_requested(
        'voice intent text="khong vao khoa hoc" command=none handled=true'
    ) is False
    assert module._lesson_start_requested(
        'voice intent text="đừng bắt đầu bài học" command=none handled=true'
    ) is False
    assert module._lesson_start_requested(
        'voice intent text="dung bat dau bai hoc" command=none handled=true'
    ) is False


def test_lesson_e2e_log_verify_rejects_negative_phrase_that_started_a_lesson():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "passive"}]
        )
    )
    # A negative phrase that nonetheless dispatched start_lesson is a scope violation:
    # the robot started a lesson for a "do not start" utterance.
    lines.insert(
        4,
        'voice intent text="không vào khóa học" command=start_lesson dispatch=true handled=true',
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_start_scoped_to_positive_phrases"]["ok"] is False
    assert "không vào khóa học" in checks["lesson_start_scoped_to_positive_phrases"]["evidence"]


def test_lesson_e2e_log_verify_accepts_positive_phrase_start_scope():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "passive"}]
        )
    )
    # The default flow uses a positive phrase ("bắt đầu bài học"); the scope gate
    # must not flag it. Add another positive phrasing to confirm it is clean too.
    lines.insert(
        4,
        'voice intent text="tiếp tục khóa học" command=start_lesson dispatch=true handled=true',
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_start_scoped_to_positive_phrases"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_negative_phrase_that_did_not_start():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "passive"}]
        )
    )
    # A negative phrase that correctly did NOT dispatch start_lesson is valid evidence:
    # the robot heard "do not start" and did not start. The scope gate must accept it.
    lines.insert(
        3,
        'voice intent text="không vào khóa học" command=none handled=true dispatch=false',
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_start_scoped_to_positive_phrases"]["ok"] is True


# --- Manifest-pin abort: mismatch surfaces an error AND sends no frames ---


def _manifest_pin_mismatch_prefix() -> list[str]:
    """Boot through a clean assignment/current with a pinned checksum that the
    fetched manifest will NOT match (assignment=manifest-current, manifest=manifest-stale)."""
    return [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bat dau bai hoc\" handled=true",
        "tts playback complete reason=start_lesson_ack bytes=4096 duration_ms=900",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 "
        "state=ASSIGNED manifestChecksum=manifest-current",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft "
        "assignmentId=assign-1 totalSteps=1 manifestChecksum=manifest-stale",
    ]


def test_manifest_pin_accepts_mismatch_blocked_with_error_and_no_frames():
    module = load_module()
    lines = _manifest_pin_mismatch_prefix() + [
        "LessonRuntime lesson_start_blocked reason=manifest_checksum_mismatch "
        "assignmentId=assign-1 assignmentChecksum=manifest-current fetchedChecksum=manifest-stale",
        "LessonRuntime start status error: manifest pin mismatch, no lesson frames sent",
    ]

    report = module.evaluate_manifest_pin_abort_logs(
        lines, device_id="14:c1:9f:d1:a8:48"
    )

    checks = by_name(report)
    assert report["ok"] is True
    gate = checks["lesson_manifest_pin_blocks_frames_on_mismatch"]
    assert gate["ok"] is True
    assert "mismatch=" in gate["evidence"]
    assert "frames=none" in gate["evidence"]


def test_manifest_pin_rejects_mismatch_that_still_sends_lesson_frames():
    module = load_module()
    lines = _manifest_pin_mismatch_prefix() + [
        "LessonRuntime start status error: manifest pin mismatch detected",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,'
        '"body":{"manifestRef":{"manifestChecksum":"manifest-stale"}}}',
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
        '{"type":"lesson_step","assignmentId":"assign-1","sessionId":"sess-1","sequence":3,'
        '"stepId":"s1","body":{"scene":{"backgroundScene":{"poster":'
        '{"src":"https://ota.example/poster-s1.jpg"}}}}}',
    ]

    report = module.evaluate_manifest_pin_abort_logs(
        lines, device_id="14:c1:9f:d1:a8:48"
    )

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_manifest_pin_blocks_frames_on_mismatch"]
    assert gate["ok"] is False
    assert "lesson_prepare" in gate["evidence"]


def test_manifest_pin_rejects_mismatch_swallowed_without_start_status_error():
    module = load_module()
    lines = _manifest_pin_mismatch_prefix() + [
        "LessonRuntime returned to idle assignmentId=assign-1",
    ]

    report = module.evaluate_manifest_pin_abort_logs(
        lines, device_id="14:c1:9f:d1:a8:48"
    )

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_manifest_pin_blocks_frames_on_mismatch"]
    assert gate["ok"] is False
    assert "start_status_error=none" in gate["evidence"]


def test_manifest_pin_not_applicable_when_checksums_match():
    module = load_module()
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-1 "
        "state=ASSIGNED manifestChecksum=manifest-good",
        "LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft "
        "assignmentId=assign-1 totalSteps=1 manifestChecksum=manifest-good",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,'
        '"body":{"manifestRef":{"manifestChecksum":"manifest-good"}}}',
    ]

    report = module.evaluate_manifest_pin_abort_logs(
        lines, device_id="14:c1:9f:d1:a8:48"
    )

    checks = by_name(report)
    gate = checks["lesson_manifest_pin_blocks_frames_on_mismatch"]
    assert report["ok"] is False
    assert checks["manifest_pin_mismatch_present"]["ok"] is False
    assert "no_mismatch" in gate["evidence"]


def test_manifest_pin_abort_does_not_treat_asset_checksum_failure_as_pin_mismatch():
    module = load_module()
    lines = [
        "assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED manifestChecksum=checksum-a",
        "LessonRuntime manifest fetched assignmentId=assign-1 lesson=lesson-a profile=espTft manifestChecksum=checksum-a",
        "LessonRuntime asset verify error code=ASSET_CHECKSUM_MISMATCH asset=poster",
        "LessonRuntime lesson_error code=ASSET_CHECKSUM_MISMATCH",
    ]

    report = module.evaluate_manifest_pin_abort_logs(
        lines, device_id="14:c1:9f:d1:a8:48"
    )

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["manifest_pin_mismatch_present"]["ok"] is False
    assert "no_mismatch" in checks["lesson_manifest_pin_blocks_frames_on_mismatch"]["evidence"]


def test_manifest_pin_abort_cli_accepts_clean_abort(tmp_path):
    log_file = tmp_path / "manifest-pin-abort.log"
    log_file.write_text(
        "\n".join(
            _manifest_pin_mismatch_prefix()
            + [
                "LessonRuntime lesson_start_blocked reason=manifest_checksum_mismatch assignmentId=assign-1",
                "LessonRuntime start status error: manifest pin mismatch, no lesson frames sent",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scenario",
            "manifest-pin-abort",
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["scenario"] == "manifest-pin-abort"

# ---------------------------------------------------------------------------
# T14-LIVE-02 cold SD first download: the verifier-gate evidence-validators
# for "verify sha256" (recomputed digest must equal the expected digest, not just
# a self-reported checksumOk boolean) and for the canonical
# "<lesson>/v<version>-<checksum>" cache-key directory shape.
# ---------------------------------------------------------------------------

LIVE04_MANIFEST_CHECKSUM = "abcdef1234567890"
LIVE04_LESSON_VERSION = 3
LIVE04_CACHE_KEY = "w01-d01-barn-say-it/v3-abcdef1234567890"
LIVE04_LOCAL_ROOT = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef1234567890"
LIVE04_ASSET_SHA = {
    "backgroundScene.poster": "1111111111111111111111111111111111111111111111111111111111111111",
    "teachingObject.barn": "2222222222222222222222222222222222222222222222222222222222222222",
    "robotOverlay.teach": "3333333333333333333333333333333333333333333333333333333333333333",
}


def live04_first_download_lines(
    *,
    asset_overrides: dict[str, dict] | None = None,
    cache_key: str = LIVE04_CACHE_KEY,
    lesson_version: int | None = LIVE04_LESSON_VERSION,
    manifest_checksum: str = LIVE04_MANIFEST_CHECKSUM,
) -> list[str]:
    """T14-LIVE-02 empty-cache first-download flow with per-asset sha256 attestation.

    Each ready asset reports both the manifest-declared expected digest (``sha256``)
    and the recomputed on-SD digest (``computedSha256``); a correct first download
    has them equal. Callers mutate one asset via ``asset_overrides`` to exercise the
    failure shapes (digest mismatch, missing recomputed digest, etc.).
    """
    overrides = asset_overrides or {}
    assets = []
    for key, sha in LIVE04_ASSET_SHA.items():
        asset = {
            "key": key,
            "localPath": f"{LIVE04_LOCAL_ROOT}/{key}",
            "size": 120 + len(assets),
            "state": "READY",
            "checksumOk": True,
            "sha256": sha,
            "computedSha256": sha,
        }
        asset.update(overrides.get(key, {}))
        assets.append(asset)

    body: dict = {
        "manifestRef": {"manifestChecksum": manifest_checksum},
        "criticalAssets": list(LIVE04_ASSET_SHA.keys()),
        "assetPack": {
            "ready": True,
            "cacheKey": cache_key,
            "firstDownload": True,
            "cacheHit": False,
            "assets": assets,
        },
    }
    if lesson_version is not None:
        body["lessonVersion"] = lesson_version

    lines = one_step_flow_lines(
        manifest_line="LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft "
        f"manifestChecksum={manifest_checksum} "
        + json.dumps(
            {
                "steps": [{"id": "s4", "type": "say_it", "completionClass": "interactive"}],
                "totalSteps": 1,
            },
            separators=(",", ":"),
        ),
        include_child_response=True,
    )
    lines[7] = json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": body,
        },
        separators=(",", ":"),
    )
    lines[9] = json.dumps(
        {
            "type": "lesson_ack",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "acks": 1,
                "rendered": False,
                "assetPack": {"ready": True, "cacheKey": cache_key},
            },
        },
        separators=(",", ":"),
    )
    return lines


def test_lesson_e2e_log_verify_accepts_live04_first_download_with_matching_sha256():
    module = load_module()
    lines = live04_first_download_lines()

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_sha256_attested"]["ok"] is True
    assert checks["lesson_asset_pack_cache_key_version_segment"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_live04_recomputed_sha256_mismatch_despite_checksum_ok():
    module = load_module()
    # Firmware claims checksumOk=true but the recomputed on-SD digest differs from
    # the manifest-declared expected digest: wrong bytes masquerading as verified.
    lines = live04_first_download_lines(
        asset_overrides={
            "teachingObject.barn": {
                "computedSha256": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef0",
            }
        }
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_asset_pack_sha256_attested"]
    assert gate["ok"] is False
    assert "mismatch=" in gate["evidence"]
    assert "teachingobject.barn" in gate["evidence"]


def test_lesson_e2e_log_verify_rejects_live04_checksum_ok_without_recomputed_sha256():
    module = load_module()
    # checksumOk=true but no recomputed digest provided at all for one asset -> the
    # self-report cannot be corroborated against the expected digest.
    lines = live04_first_download_lines(
        asset_overrides={"robotOverlay.teach": {"computedSha256": None}}
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_asset_pack_sha256_attested"]
    assert gate["ok"] is False
    assert "unattested=" in gate["evidence"]
    assert "robotoverlay.teach" in gate["evidence"]


def test_lesson_e2e_log_verify_accepts_live04_sha256_attested_via_digest_alias():
    module = load_module()
    # The recomputed digest may arrive under the "digest" alias instead of
    # "computedSha256"; an equal digest still attests.
    lines = live04_first_download_lines(
        asset_overrides={
            key: {"computedSha256": None, "digest": sha}
            for key, sha in LIVE04_ASSET_SHA.items()
        }
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_sha256_attested"]["ok"] is True


def test_lesson_e2e_log_verify_skips_sha256_attestation_when_no_digest_evidence():
    module = load_module()
    # Legacy evidence with neither expected nor recomputed digests: the attestation
    # gate must stay neutral (skipped pass), not fail.
    lines = live04_first_download_lines(
        asset_overrides={
            key: {"sha256": None, "computedSha256": None}
            for key in LIVE04_ASSET_SHA
        }
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    gate = checks["lesson_asset_pack_sha256_attested"]
    assert gate["ok"] is True
    assert "skipped" in gate["evidence"]


def test_lesson_e2e_log_verify_rejects_live04_cache_key_version_segment_disagreeing_with_lesson_version():
    module = load_module()
    # cacheKey encodes v9 but the prepared lessonVersion is 3: the version segment
    # of "<lesson>/v<version>-<checksum>" does not match the lesson content version.
    bad_cache_key = "w01-d01-barn-say-it/v9-abcdef1234567890"
    lines = live04_first_download_lines(cache_key=bad_cache_key)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_asset_pack_cache_key_version_segment"]
    assert gate["ok"] is False
    assert "version_mismatch=" in gate["evidence"]
    assert bad_cache_key in gate["evidence"]


def test_lesson_e2e_log_verify_rejects_live04_cache_key_without_version_segment():
    module = load_module()
    # cacheKey contains the checksum but has no "v<version>-" directory segment, so
    # the canonical "<lesson>/v<version>-<checksum>" shape is not actually encoded.
    shapeless_cache_key = "w01-d01-barn-say-it-abcdef1234567890"
    lines = live04_first_download_lines(cache_key=shapeless_cache_key)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_asset_pack_cache_key_version_segment"]
    assert gate["ok"] is False
    assert "no_version_segment=" in gate["evidence"]


def test_lesson_e2e_log_verify_rejects_live04_cache_key_version_segment_wrong_checksum():
    module = load_module()
    # The version segment checksum tail must equal the manifest checksum, even when
    # the version number matches.
    wrong_checksum_key = "w01-d01-barn-say-it/v3-00000000feedface"
    lines = live04_first_download_lines(cache_key=wrong_checksum_key)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    gate = checks["lesson_asset_pack_cache_key_version_segment"]
    assert gate["ok"] is False
    assert "checksum_mismatch=" in gate["evidence"]


def test_lesson_e2e_log_verify_accepts_live04_cache_key_version_segment_without_lesson_version():
    module = load_module()
    # When the prepare body omits lessonVersion, the version segment cannot be
    # cross-checked, but the checksum tail is still validated and must pass.
    lines = live04_first_download_lines(lesson_version=None)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_asset_pack_cache_key_version_segment"]["ok"] is True


# --- Republish (same lessonVersion, new checksum) cross-run eviction gate ---
#
# Republish eviction is a cross-run differential: republish the same lessonVersion so the
# manifest checksum changes, then the ESP must EVICT the old SD cache dir and use
# a NEW one (cacheKey carries the new checksum) instead of reusing stale images.
# A single capture cannot express this — the per-capture consistency gates reject
# logs mixing two checksums/sessions. ``evaluate_republish_eviction`` consumes the
# two single-checksum captures (before + after republish) and asserts the eviction.


def _republish_capture(
    *,
    lesson_version: int,
    manifest_checksum: str,
    cache_key: str,
    session_id: str = "sess-1",
) -> list[str]:
    """One single-checksum capture with a ready SD asset pack on lesson_prepare.

    Mirrors the wire shape emitted by
    ``esp32-server/.../core/lesson/asset_cache.py::asset_pack_manifest``:
    ``lessonVersion`` + ``manifestChecksum`` + ``cacheKey`` + ``localRoot``.
    """
    local_root = f"sd://sdcard/tbot/lesson-assets/{cache_key}"
    asset_pack = {
        "ready": True,
        "cacheKey": cache_key,
        "lessonVersion": lesson_version,
        "manifestChecksum": manifest_checksum,
        "localRoot": local_root,
        "layers": ["backgroundScene", "teachingObject", "robotOverlay"],
        "assets": [
            {
                "key": "backgroundScene.poster.src",
                "localPath": f"{local_root}/backgroundScene.poster.src",
                "state": "READY",
                "checksumOk": True,
                "size": 2048,
            },
            {
                "key": "teachingObject.asset.src",
                "localPath": f"{local_root}/teachingObject.asset.src",
                "state": "READY",
                "checksumOk": True,
                "size": 2048,
            },
            {
                "key": "robotOverlay.asset.src",
                "localPath": f"{local_root}/robotOverlay.asset.src",
                "state": "READY",
                "checksumOk": True,
                "size": 2048,
            },
        ],
    }
    prepare = {
        "type": "lesson_prepare",
        "assignmentId": "assign-1",
        "sessionId": session_id,
        "sequence": 1,
        "body": {
            "manifestRef": {"manifestChecksum": manifest_checksum},
            "criticalAssets": ["backgroundScene.poster.src"],
            "assetPack": asset_pack,
        },
    }
    return [
        f"LessonRuntime manifest fetched lesson=lesson-a course=course-1 profile=espTft "
        f"assignmentId=assign-1 sessionId={session_id} manifestChecksum={manifest_checksum}",
        json.dumps(prepare, separators=(",", ":")),
    ]


def test_lesson_e2e_log_verify_accepts_republish_evicts_old_cache_dir():
    module = load_module()
    before = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-before",
    )
    after = _republish_capture(
        lesson_version=3,
        manifest_checksum="def222",
        cache_key="lesson-a/v3-def222",
        session_id="sess-after",
    )

    report = module.evaluate_republish_eviction(before, after, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is True
    assert checks["republish_same_lesson_version"]["ok"] is True
    assert checks["republish_checksum_changed"]["ok"] is True
    assert checks["republish_cache_dir_evicted"]["ok"] is True
    assert checks["republish_no_stale_cache_reuse"]["ok"] is True

def test_lesson_e2e_log_verify_cli_accepts_republish_eviction_scenario(tmp_path):
    before_log = tmp_path / "before.log"
    after_log = tmp_path / "after.log"
    before_log.write_text(
        "\n".join(
            _republish_capture(
                lesson_version=3,
                manifest_checksum="abc111",
                cache_key="lesson-a/v3-abc111",
                session_id="sess-before",
            )
        ),
        encoding="utf-8",
    )
    after_log.write_text(
        "\n".join(
            _republish_capture(
                lesson_version=3,
                manifest_checksum="def222",
                cache_key="lesson-a/v3-def222",
                session_id="sess-after",
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scenario",
            "republish-eviction",
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--before-log-file",
            str(before_log),
            "--after-log-file",
            str(after_log),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["republish_same_lesson_version"]["ok"] is True
    assert checks["republish_no_stale_cache_reuse"]["ok"] is True


def test_lesson_e2e_log_verify_rejects_republish_reusing_stale_cache_dir():
    module = load_module()
    before = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-before",
    )
    # Failure shape: checksum changed but the robot reused the OLD cache dir whose
    # cacheKey still carries the old checksum -> stale SD images served.
    after = _republish_capture(
        lesson_version=3,
        manifest_checksum="def222",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-after",
    )

    report = module.evaluate_republish_eviction(before, after, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["republish_cache_dir_evicted"]["ok"] is False
    assert checks["republish_no_stale_cache_reuse"]["ok"] is False


def test_lesson_e2e_log_verify_rejects_republish_with_changed_lesson_version():
    module = load_module()
    before = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-before",
    )
    # Not a same-version republish: lessonVersion bumped 3 -> 4. The intended
    # (checksum changes while lessonVersion stays the SAME) is not demonstrated.
    after = _republish_capture(
        lesson_version=4,
        manifest_checksum="def222",
        cache_key="lesson-a/v4-def222",
        session_id="sess-after",
    )

    report = module.evaluate_republish_eviction(before, after, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["republish_same_lesson_version"]["ok"] is False


def test_lesson_e2e_log_verify_rejects_republish_without_checksum_change():
    module = load_module()
    before = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-before",
    )
    # No actual republish happened: identical checksum across both captures, so the
    # before/after pair proves nothing about eviction.
    after = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-after",
    )

    report = module.evaluate_republish_eviction(before, after, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["republish_checksum_changed"]["ok"] is False


def test_lesson_e2e_log_verify_rejects_republish_after_cache_key_missing_new_checksum():
    module = load_module()
    before = _republish_capture(
        lesson_version=3,
        manifest_checksum="abc111",
        cache_key="lesson-a/v3-abc111",
        session_id="sess-before",
    )
    # The after capture has a new checksum and a distinct cacheKey, but that cacheKey
    # does NOT contain the new checksum (e.g. a stale/legacy dir name) -> stale bytes
    # can be served even though the dir differs.
    after = _republish_capture(
        lesson_version=3,
        manifest_checksum="def222",
        cache_key="lesson-a/v3-legacy",
        session_id="sess-after",
    )

    report = module.evaluate_republish_eviction(before, after, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["republish_no_stale_cache_reuse"]["ok"] is False


# --- T14-LIVE-03: SD warm start (re-attest cached bytes, no re-download) -----
# The verifier-gate below validates captured hardware evidence for T14-LIVE-03.
# It is conditional: cold-start runs (no warm-cache marker) skip the gate so
# the existing first-download flow stays green. When a warm-cache marker IS
# present, the gate asserts that no asset download happened this session.

def warm_cache_prepare_frame(*, downloaded: int = 0, cache_hit: bool = True, reattested: bool = True) -> str:
    local_root = "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12"
    return json.dumps(
        {
            "type": "lesson_prepare",
            "assignmentId": "assign-1",
            "sessionId": "sess-1",
            "sequence": 1,
            "body": {
                "manifestRef": {"manifestChecksum": "abc"},
                "criticalAssets": ["poster"],
                "assetPack": {
                    "ready": True,
                    "cacheHit": cache_hit,
                    "reattested": reattested,
                    "downloaded": downloaded,
                    "cacheKey": "w01-d01-barn-say-it/v3-abcdef12",
                    "assets": [
                        {
                            "key": "backgroundScene.poster",
                            "localPath": f"{local_root}/backgroundScene.poster",
                            "state": "READY",
                            "checksumOk": True,
                            "size": 1234,
                        }
                    ],
                },
            },
        },
        separators=(",", ":"),
    )


def test_lesson_e2e_log_verify_skips_warm_cache_gate_on_cold_start():
    module = load_module()
    lines = one_step_flow_lines(manifest_line=seed_manifest_line([{"id": "s4", "type": "say_it"}]))
    # Cold start: an asset download happened, no warm-cache marker present.
    lines.insert(
        10,
        "LessonRuntime asset download stepId=s4 key=backgroundScene.poster bytes=20480 sha256=ok -> sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/backgroundScene.poster",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert checks["lesson_warm_cache_no_redownload"]["ok"] is True
    assert "skipped" in checks["lesson_warm_cache_no_redownload"]["evidence"]


def test_lesson_e2e_log_verify_accepts_warm_cache_text_marker_without_download():
    module = load_module()
    lines = one_step_flow_lines(manifest_line=seed_manifest_line([{"id": "s4", "type": "say_it"}]))
    lines.insert(
        10,
        "LessonRuntime assetPack ready=true reused cached pack downloaded=0 cacheHit=true reattested=3 assets",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    gate = checks["lesson_warm_cache_no_redownload"]
    assert gate["ok"] is True
    assert "warmCacheMarker=present" in gate["evidence"]
    assert "asset_downloads=none" in gate["evidence"]


def test_lesson_e2e_log_verify_accepts_warm_cache_asset_pack_marker_without_download():
    module = load_module()
    lines = one_step_flow_lines(manifest_line=seed_manifest_line([{"id": "s4", "type": "say_it"}]))
    lines[7] = warm_cache_prepare_frame(downloaded=0, cache_hit=True, reattested=True)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    gate = checks["lesson_warm_cache_no_redownload"]
    assert gate["ok"] is True
    assert "warmCacheMarker=present" in gate["evidence"]
    assert "asset_downloads=none" in gate["evidence"]


def test_lesson_e2e_log_verify_rejects_generic_live_text_as_step_prompt():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines = [
        line
        for line in lines
        if not line.startswith("LessonRuntime step prompt stepId=s4")
    ]
    render_ack_index = next(
        index
        for index, line in enumerate(lines)
        if '"type":"lesson_ack"' in line and '"stepId":"s4"' in line
    )
    lines.insert(
        render_ack_index + 1,
        "GoogleLive acknowledgement queued via live text='What do you see?'",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_prompt_after_frame"]["ok"] is False
    assert "missing_prompt=s4" in checks["lesson_step_prompt_after_frame"]["evidence"]
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]

def test_lesson_e2e_log_verify_rejects_stepidless_tts_sentence_as_step_prompt():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    lines = [
        line
        for line in lines
        if not line.startswith("LessonRuntime step prompt stepId=s4")
    ]
    render_ack_index = next(
        index
        for index, line in enumerate(lines)
        if '"type":"lesson_ack"' in line and '"stepId":"s4"' in line
    )
    lines.insert(render_ack_index + 1, "tts sentence_start text='What do you see?'")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["lesson_step_prompt_after_frame"]["ok"] is False
    assert "missing_prompt=s4" in checks["lesson_step_prompt_after_frame"]["evidence"]
    assert checks["interactive_guided_prompt"]["ok"] is False
    assert "missing_guided_prompt=s4" in checks["interactive_guided_prompt"]["evidence"]

def test_lesson_e2e_log_verify_rejects_listening_window_before_guided_prompt():
    module = load_module()
    lines = one_step_flow_lines(
        manifest_line=seed_manifest_line(
            [{"id": "s4", "type": "say_it", "completionClass": "interactive"}]
        ),
        include_child_response=True,
    )
    window_line = next(
        line
        for line in lines
        if "child response window opened" in line and "stepId=s4" in line
    )
    prompt_line = next(
        line
        for line in lines
        if line.startswith("LessonRuntime step prompt stepId=s4")
    )
    lines.remove(prompt_line)
    lines.remove(window_line)
    audio_index = next(
        index
        for index, line in enumerate(lines)
        if "Audio TTS played stepId=s4" in line
    )
    lines.insert(audio_index + 1, window_line)
    lines.insert(audio_index + 2, prompt_line)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["interactive_child_response_window_opened"]["ok"] is False
    assert "window_before_guided_prompt=s4" in checks[
        "interactive_child_response_window_opened"
    ]["evidence"]

def test_lesson_e2e_log_verify_rejects_warm_cache_marker_with_asset_redownload():
    module = load_module()
    lines = one_step_flow_lines(manifest_line=seed_manifest_line([{"id": "s4", "type": "say_it"}]))
    lines[7] = warm_cache_prepare_frame(downloaded=0, cache_hit=True, reattested=True)
    # Failure shape: claims warm cache yet still downloads an asset this session.
    lines.insert(
        10,
        "LessonRuntime asset download stepId=s4 key=backgroundScene.poster bytes=20480 sha256=ok -> sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/backgroundScene.poster",
    )

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    gate = checks["lesson_warm_cache_no_redownload"]
    assert gate["ok"] is False
    assert "warmCacheMarker=present" in gate["evidence"]
    assert "backgroundscene.poster" in gate["evidence"].lower()


def test_lesson_e2e_log_verify_rejects_self_contradictory_warm_cache_marker():
    module = load_module()
    lines = one_step_flow_lines(manifest_line=seed_manifest_line([{"id": "s4", "type": "say_it"}]))
    # Marker claims a cache hit but reports a positive downloaded count.
    lines[7] = warm_cache_prepare_frame(downloaded=2, cache_hit=True, reattested=True)

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    gate = checks["lesson_warm_cache_no_redownload"]
    assert gate["ok"] is False
    assert "downloaded" in gate["evidence"].lower()


def test_lesson_e2e_log_verify_warm_cache_render_from_local_is_not_a_download():
    module = load_module()
    # firmware rendering an asset from a local SD path must NOT be treated as a
    # download; only origin/byte fetches count.
    assert module._asset_download_evidence(
        "I (666) Lesson: stepId=s4 lesson_step poster fetched+drawn from URL url=sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef12/poster.jpg"
    ) is False
    assert module._asset_download_evidence(
        "LessonRuntime asset download stepId=s4 key=poster bytes=20480 sha256=ok -> sd://sdcard/tbot/poster.jpg"
    ) is True
    assert module._asset_download_evidence(
        "LessonRuntime asset fetch+store key=poster bytes=20480 sha256 verified -> sd://sdcard/tbot/poster.jpg"
    ) is True


def test_lesson_e2e_log_verify_warm_cache_marker_detection():
    module = load_module()
    for marker in (
        "assetPack ready=true cacheHit=true downloaded=0",
        "assetPack ready=true cache_hit=1 downloaded=0",
        "assetPack reattested=3 downloaded=0",
        "reused cached pack downloaded=0",
        '{"type":"lesson_prepare","body":{"assetPack":{"ready":true,"cacheHit":true,"downloaded":0,"cacheKey":"k"}}}',
    ):
        assert module._warm_cache_marker(marker) is True
    for non_marker in (
        "LessonRuntime preload_ready criticalAssets=ready",
        "LessonRuntime asset download key=poster bytes=20480",
        "assetPack ready=true downloaded=4",
    ):
        assert module._warm_cache_marker(non_marker) is False


# --- Production assignment pull (child/device/lessonVersion correctness) ---


def _live01_assignment_lines(
    *,
    assignment_extra: str = "",
    closing_state: str = "COMPLETED",
) -> list[str]:
    """A green assignment evidence bundle whose assignment/current line carries the
    full identity envelope. assignment_extra is appended to the opening
    assignment/current line so individual tests can drop/mutate one field."""

    return [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "tts sentence_start text=\"Bắt đầu bài học nhé.\" reason=start_lesson_ack",
        "tts playback complete reason=start_lesson_ack",
        (
            "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 "
            "assignmentId=assign-1 lessonId=lesson-a courseId=course-a "
            "manifestChecksum=sha256:abc123 state=ASSIGNED" + assignment_extra
        ),
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft manifestChecksum=sha256:abc123 {"steps":[{"id":"s4","completionClass":"interactive"}],"totalSteps":1}',
        "server send lesson_prepare assignmentId=assign-1 sequence=1",
        "serial RX lesson_prepare seq=1",
        "serial TX lesson_ack body.acks=1 rendered=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        "server send lesson_start assignmentId=assign-1 sequence=2",
        "serial RX lesson_start seq=2",
        "serial TX lesson_ack body.acks=2 rendered=false",
        "LessonRuntime event lesson_started assignmentId=assign-1 state=RUNNING",
        "server send lesson_step stepId=s4 backgroundScene.poster.src=https://ota.example/poster.jpg teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking",
        "LessonRuntime event step_started assignmentId=assign-1 stepId=s4",
        "serial Lesson lesson_step poster fetched+drawn from URL stepId=s4 url=https://ota.example/poster.jpg",
        "serial Lesson teachingObject rendered primaryWord=barn stepId=s4",
        "serial Lesson robotOverlay rendered robotState=talking pose=teach stepId=s4",
        "serial TX lesson_ack body.acks=3 rendered=true degraded=false robotState=talking stepId=s4",
        "LessonRuntime step prompt stepId=s4 text=Can you say barn with TeeBot?",
        "serial Audio TTS played stepId=s4 primaryWord=barn",
        "LessonRuntime child response window opened stepId=s4 listening=true",
        "serial interactive child response accepted stepId=s4 recognizedText=barn",
        "serial TX lesson_progress event=step_completed result=success stepId=s4",
        "backend post lesson_progress assignmentId=assign-1 stepId=s4 event=step_completed result=success persisted=true",
        "server send lesson_stop assignmentId=assign-1 sequence=4",
        "serial RX lesson_stop seq=4",
        "LessonRuntime event lesson_completed assignmentId=assign-1",
        "backend post lesson_completed assignmentId=assign-1",
        (
            "assignment/current -> 200 assignmentId=assign-1 lessonId=lesson-a courseId=course-a "
            "manifestChecksum=sha256:abc123 state=" + closing_state
        ),
    ]


def test_lesson_e2e_log_verify_accepts_lesson_version_on_assignment_current():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" lessonVersion=7")
    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert "lesson_version_present" in checks
    assert checks["lesson_version_present"]["ok"] is True
    assert "lessonVersion=7" in checks["lesson_version_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_missing_lesson_version_on_assignment_current():
    module = load_module()
    # assignment/current is present and active but carries NO lessonVersion field.
    lines = _live01_assignment_lines(assignment_extra="")
    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_lesson_version=True
    )
    checks = by_name(report)
    assert checks["lesson_version_present"]["ok"] is False
    assert "lessonVersion" in checks["lesson_version_present"]["missing"]


def test_lesson_e2e_log_verify_lesson_version_advisory_when_not_required():
    module = load_module()
    # Same missing-lessonVersion evidence, but the operator did NOT opt in:
    # the gate stays advisory so the legacy fixture corpus is not failed.
    lines = _live01_assignment_lines(assignment_extra="")
    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert checks["lesson_version_present"]["ok"] is True
    assert "not_required" in checks["lesson_version_present"]["evidence"]


def test_lesson_e2e_log_verify_lesson_version_gate_non_blocking_without_assignment_evidence():
    module = load_module()
    # No assignment/current line at all -> gate must not fail the bundle on its own.
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
    ]
    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert checks["lesson_version_present"]["ok"] is True
    assert "no assignment_current evidence" in checks["lesson_version_present"]["evidence"]


def test_lesson_e2e_log_verify_accepts_assignment_version_on_assignment_current():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert "assignment_version_present" in checks
    assert checks["assignment_version_present"]["ok"] is True
    assert "assignmentVersion=3" in checks["assignment_version_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_missing_assignment_version_on_assignment_current():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" lessonVersion=7")
    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_assignment_version=True
    )
    checks = by_name(report)
    assert checks["assignment_version_present"]["ok"] is False
    assert "assignmentVersion" in checks["assignment_version_present"]["missing"]


def test_lesson_e2e_log_verify_assignment_version_advisory_when_not_required():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" lessonVersion=7")
    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert checks["assignment_version_present"]["ok"] is True
    assert "not_required" in checks["assignment_version_present"]["evidence"]


def test_lesson_e2e_log_verify_cli_accepts_required_assignment_version(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--require-assignment-version",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["assignment_version_present"]["ok"] is True
    assert "assignmentVersion=3" in checks["assignment_version_present"]["evidence"]


def test_lesson_e2e_log_verify_accepts_story_evidence_on_manifest():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"What animal do you see?","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert "lesson_story_present" in checks
    assert checks["lesson_story_present"]["ok"] is True
    assert "story" in checks["lesson_story_present"]["evidence"].lower()

def test_lesson_e2e_log_verify_rejects_story_without_wait_for_child_when_required():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"What animal do you see?","waitForChild":false}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "waitForChild" in checks["lesson_story_present"]["missing"]

def test_lesson_e2e_log_verify_rejects_interactive_manifest_step_without_wait_for_child():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"What animal do you see?","waitForChild":true}},'
        '{"id":"s5","completionClass":"interactive",'
        '"storyBeat":{"ask":"What sound does it make?"}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":2}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_waitForChild=s5" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_interactive_manifest_step_without_guided_question():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_guided_question=s4" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_command_only_manifest_story_prompt_as_guided_question():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"prompt":"Say barn?","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_guided_question=s4" in checks["lesson_story_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_polite_command_only_manifest_story_prompt():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"prompt":"Please say barn?","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_guided_question=s4" in checks["lesson_story_present"]["evidence"]

def test_lesson_e2e_log_verify_rejects_vietnamese_command_only_manifest_story_prompt():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"prompt":"Hãy nói barn?","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_guided_question=s4" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_accepts_where_manifest_story_prompt_without_question_mark():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"Where is the barn","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_which_manifest_story_prompt_without_question_mark():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"Which animal is hiding","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_how_many_manifest_story_prompt_without_question_mark():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"How many animals are there","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_where_do_you_see_manifest_story_prompt_without_question_mark():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"Where do you see the barn","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True


def test_lesson_e2e_log_verify_accepts_story_evidence_on_lesson_step():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[19] = (
        "server send lesson_step stepId=s4 "
        "backgroundScene.poster.src=https://ota.example/poster.jpg "
        "teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking "
        'storyBeat={"ask":"What animal do you see?","waitForChild":true}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True
    assert "lesson_step" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_lesson_step_story_without_guided_question():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[19] = (
        "server send lesson_step stepId=s4 "
        "backgroundScene.poster.src=https://ota.example/poster.jpg "
        "teachingObject.subject.primaryWord=barn robotOverlay.robotState=talking "
        'storyBeat={"waitForChild":true}'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "missing_guided_question=s4" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_rejects_missing_story_when_required():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "story" in checks["lesson_story_present"]["missing"]


def test_lesson_e2e_log_verify_rejects_unscoped_debug_story_when_required():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines.append(
        'debug authoring preview storyText="TeeBot and the child visit a barn" '
        'note="not manifest or lesson_step evidence"'
    )

    report = module.evaluate_lesson_logs(
        lines, device_id="14:c1:9f:d1:a8:48", require_story=True
    )
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is False
    assert "manifest" in checks["lesson_story_present"]["missing"]

def test_lesson_e2e_log_verify_story_advisory_when_not_required():
    module = load_module()
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")

    report = module.evaluate_lesson_logs(lines, device_id="14:c1:9f:d1:a8:48")
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True
    assert "not_required" in checks["lesson_story_present"]["evidence"]


def test_lesson_e2e_log_verify_cli_accepts_required_story(tmp_path):
    lines = _live01_assignment_lines(assignment_extra=" assignmentVersion=3 lessonVersion=7")
    lines[7] = (
        'LessonRuntime manifest fetched lesson=lesson-a course=course-a profile=espTft '
        'manifestChecksum=sha256:abc123 '
        '{"steps":[{"id":"s4","completionClass":"interactive",'
        '"storyBeat":{"ask":"What animal do you see?","waitForChild":true}}],'
        '"storyText":"TeeBot and the child visit a barn.","totalSteps":1}'
    )
    log_file = tmp_path / "lesson.log"
    log_file.write_text("\n".join(lines), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--require-story",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["lesson_story_present"]["ok"] is True


def test_lesson_e2e_log_verify_cli_accepts_expected_child_id(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(assignment_extra=" lessonVersion=7 childId=child-a")
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-child-id",
            "child-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_child_identity"]["ok"] is True
    assert "assignment_current=child-a" in checks["expected_child_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_rejects_unexpected_child_id(tmp_path):
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(assignment_extra=" lessonVersion=7 childId=child-b")
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-child-id",
            "child-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_child_identity"]["ok"] is False
    assert "expected=child-a" in checks["expected_child_identity"]["evidence"]
    assert "observed=child-b" in checks["expected_child_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_requires_assignment_current_child_identity(tmp_path):
    # The expected child is named elsewhere (progress post) but NOT on the
    # active assignment/current line -> the assignment binding is unproven.
    lines = _live01_assignment_lines(assignment_extra=" lessonVersion=7")
    lines = [
        line.replace(
            "backend post lesson_progress assignmentId=assign-1 stepId=s4",
            "backend post lesson_progress assignmentId=assign-1 childId=child-a stepId=s4",
        )
        for line in lines
    ]
    log_file = tmp_path / "lesson.log"
    log_file.write_text("\n".join(lines), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-child-id",
            "child-a",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_child_identity"]["ok"] is False
    assert "assignment_current=none" in checks["expected_child_identity"]["evidence"]


def test_lesson_e2e_log_verify_cli_accepts_expected_device_binding(tmp_path):
    backend_device_id = "14140000-0000-4000-8000-000000000004"
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(
                assignment_extra=(
                    " lessonVersion=7 deviceId=14:c1:9f:d1:a8:48"
                    f" backendDeviceId={backend_device_id}"
                )
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-device-binding",
            backend_device_id,
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["expected_device_binding"]["ok"] is True
    assert f"assignment_current={backend_device_id}" in checks["expected_device_binding"]["evidence"]


def test_lesson_e2e_log_verify_cli_uses_backend_device_id_for_binding(tmp_path):
    backend_device_id = "14140000-0000-4000-8000-000000000004"
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(
                assignment_extra=(
                    " lessonVersion=7 deviceId=14:c1:9f:d1:a8:48"
                    f" backendDeviceId={backend_device_id}"
                )
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-device-binding",
            backend_device_id,
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    binding = checks["expected_device_binding"]
    assert binding["ok"] is True
    assert f"assignment_current={backend_device_id}" in binding["evidence"]
    assert "assignment_current=14:c1:9f:d1:a8:48" not in binding["evidence"]


def test_lesson_e2e_log_verify_cli_rejects_unexpected_device_binding(tmp_path):
    expected_backend_device_id = "14140000-0000-4000-8000-000000000004"
    wrong_backend_device_id = "14140000-0000-4000-8000-000000000099"
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(
                assignment_extra=(
                    " lessonVersion=7 deviceId=aa:bb:cc:dd:ee:ff"
                    f" backendDeviceId={wrong_backend_device_id}"
                )
            )
        ),
        encoding="utf-8",
    )

    # The foreign MAC is registered as an alias so the line stays in device
    # scope and the gate actually observes the wrong binding value, rather than
    # the line being silently scoped-out. The binding still fails because the
    # active assignment names a device other than the production-expected one.
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--device-alias",
            "aa:bb:cc:dd:ee:ff",
            "--expected-device-binding",
            expected_backend_device_id,
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    checks = by_name(report)
    binding = checks["expected_device_binding"]
    assert binding["ok"] is False
    assert f"expected={expected_backend_device_id}" in binding["evidence"]
    # The active assignment names the WRONG device -> binding rejected.
    assert f"assignment_current={wrong_backend_device_id}" in binding["evidence"]


def test_lesson_e2e_log_verify_live01_full_identity_envelope_accepts(tmp_path):
    # The full assignment expected result: one active assignment with correct
    # child, device, assignmentVersion, lessonVersion, and manifestChecksum.
    log_file = tmp_path / "lesson.log"
    log_file.write_text(
        "\n".join(
            _live01_assignment_lines(
                assignment_extra=(
                    " assignmentVersion=3 lessonVersion=7 childId=child-a"
                    " deviceId=14:c1:9f:d1:a8:48"
                    " backendDeviceId=14140000-0000-4000-8000-000000000004"
                )
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--expected-lesson-id",
            "lesson-a",
            "--expected-course-id",
            "course-a",
            "--expected-child-id",
            "child-a",
            "--expected-device-binding",
            "14140000-0000-4000-8000-000000000004",
            "--require-lesson-version",
            "--require-assignment-version",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["assignment_version_present"]["ok"] is True
    assert "assignmentVersion=3" in checks["assignment_version_present"]["evidence"]
    assert checks["lesson_version_present"]["ok"] is True
    assert "lessonVersion=7" in checks["lesson_version_present"]["evidence"]
    assert checks["expected_child_identity"]["ok"] is True
    assert checks["expected_device_binding"]["ok"] is True
    assert checks["lesson_assignment_manifest_checksum_consistent"]["ok"] is True
    assert checks["assignment_consistent"]["ok"] is True


# Network loss during preload -- ESP does not report READY, firmware
# does not start an incomplete lesson, and a retry resumes safely. The
# false-READY / incomplete-start halves are covered by _lesson_preload_ready
# and the green-path sequencing. These cases cover the remaining
# "retry resumes safely" clause: after a preload failure/retry marker, a clean
# same-assignment preload_ready must precede any lesson_start frame.
def _preload_recovery_passing_lines() -> list[str]:
    return [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1 token=secret-token",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a-1f1b-4437-9401-9ca2bc4adc69/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        '{"type":"lesson_prepare","assignmentId":"assign-1","sessionId":"sess-1","sequence":1,"body":{"manifestRef":{"manifestChecksum":"abc"},"criticalAssets":["poster"]}}',
        # Internet drops mid-download: asset cache records a network failure, no READY.
        "LessonRuntime asset download reason=network_error assignmentId=assign-1 ready=false",
        "LessonRuntime notify lesson_terminal preload_failed assignmentId=assign-1 retryable=true",
        # Retry: a fresh attempt re-preloads and reaches a legitimate READY.
        "LessonRuntime preload retry assignmentId=assign-1 attempt=2",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
    ]


def test_lesson_e2e_log_verify_preload_recovery_accepts_clean_retry_resume():
    module = load_module()

    report = module.evaluate_preload_recovery_logs(
        _preload_recovery_passing_lines(), device_id="14:c1:9f:d1:a8:48"
    )

    assert report["ok"] is True
    checks = by_name(report)
    assert checks["start_lesson_requested"]["ok"] is True
    assert checks["preload_failure_observed"]["ok"] is True
    assert checks["retry_resumes_safely"]["ok"] is True

def test_lesson_e2e_log_verify_cli_accepts_preload_recovery_scenario(tmp_path):
    log_file = tmp_path / "preload-recovery.log"
    log_file.write_text("\n".join(_preload_recovery_passing_lines()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--scenario",
            "preload-recovery",
            "--device-id",
            "14:c1:9f:d1:a8:48",
            "--log-file",
            str(log_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    checks = by_name(report)
    assert checks["preload_failure_observed"]["ok"] is True
    assert checks["retry_resumes_safely"]["ok"] is True


def test_lesson_e2e_log_verify_preload_recovery_rejects_start_without_ready_after_failure():
    module = load_module()
    # A preload failure is followed straight by lesson_start with NO intervening
    # clean preload_ready: firmware would start an incomplete lesson.
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "LessonRuntime notify lesson_terminal preload_failed assignmentId=assign-1 retryable=true",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
    ]

    report = module.evaluate_preload_recovery_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["preload_failure_observed"]["ok"] is True
    assert checks["retry_resumes_safely"]["ok"] is False
    assert "lesson_start" in checks["retry_resumes_safely"]["evidence"]


def test_lesson_e2e_log_verify_preload_recovery_rejects_failure_with_no_recovery_ready():
    module = load_module()
    # The retry never produced a legitimate READY -- the run stalled after the
    # failure and no clean same-assignment preload_ready ever appeared (the only
    # post-failure ready is itself partial/incomplete).
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "LessonRuntime asset download reason=network_error assignmentId=assign-1 ready=false",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready partial=true missingAssets=poster",
    ]

    report = module.evaluate_preload_recovery_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["preload_failure_observed"]["ok"] is True
    assert checks["retry_resumes_safely"]["ok"] is False


def test_lesson_e2e_log_verify_preload_recovery_rejects_ready_for_other_assignment():
    module = load_module()
    # The post-failure clean READY belongs to a DIFFERENT assignment; the failed
    # assignment never recovered, so the resume is not safe for assign-1.
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "LessonRuntime notify lesson_terminal preload_failed assignmentId=assign-1 retryable=true",
        "LessonRuntime preload_ready assignmentId=assign-OTHER criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
    ]

    report = module.evaluate_preload_recovery_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["preload_failure_observed"]["ok"] is True
    assert checks["retry_resumes_safely"]["ok"] is False


def test_lesson_e2e_log_verify_preload_recovery_requires_failure_premise():
    module = load_module()
    # No preload failure/retry marker at all: this is not a recovery capture
    # drop-then-recover run, so the premise gate fails (wrong evidence shape).
    lines = [
        "I (100) Application: TBOT firmware boot complete",
        "I (120) WiFi: connected ssid=Van_Phong_Tam_Dentist ip=192.168.1.23",
        "websocket hello device_id=14:c1:9f:d1:a8:48 session=sess-1",
        "voice intent start_lesson text=\"bắt đầu bài học\" handled=true",
        "GET /v1/devices/4206ee1a/assignment/current -> 200 assignmentId=assign-1 state=ASSIGNED",
        "LessonRuntime manifest fetched lesson=w01-d01-barn-say-it profile=espTft assignmentId=assign-1",
        "LessonRuntime preload_ready assignmentId=assign-1 criticalAssets=ready",
        '{"type":"lesson_start","assignmentId":"assign-1","sessionId":"sess-1","sequence":2}',
    ]

    report = module.evaluate_preload_recovery_logs(lines, device_id="14:c1:9f:d1:a8:48")

    checks = by_name(report)
    assert report["ok"] is False
    assert checks["preload_failure_observed"]["ok"] is False


def test_lesson_e2e_log_verify_preload_recovery_redacts_sensitive_tokens():
    module = load_module()

    report = module.evaluate_preload_recovery_logs(
        _preload_recovery_passing_lines(), device_id="14:c1:9f:d1:a8:48"
    )

    assert report["ok"] is True
    assert "secret-token" not in json.dumps(report)
