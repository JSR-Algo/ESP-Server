#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


FATAL_PATTERNS = (
    "Traceback",
    "Logging error",
    "duplicate session",
    "stale audio",
    "self-interrupt",
    "fallback_triggered",
)


def _has_physical_ws_connection(log_text, device_id, client_id, server_ip=None):
    for line in log_text.splitlines():
        if "Headers:" not in line:
            continue
        if device_id not in line or client_id not in line:
            continue
        if "127.0.0.1 conn" in line:
            continue
        if server_ip and f"{server_ip} conn" in line:
            continue
        if "Python/" in line or "websockets/" in line:
            continue
        return True
    return False


def _audit_lesson_flow(log_text, expected_lesson_steps):
    lesson_prepare = len(re.findall(r"\blesson_prepare\b", log_text))
    lesson_start = len(re.findall(r"\blesson_start\b", log_text))
    lesson_stop = len(re.findall(r"\blesson_stop\b", log_text))
    lesson_completed = len(re.findall(r"lesson_completed|stepsCompleted=\d+", log_text))
    lesson_step_lines = [
        line for line in log_text.splitlines() if re.search(r"\blesson_step\b", line)
    ]
    emitted_steps = len([line for line in lesson_step_lines if re.search(r"\bemit lesson_step\b", line)])
    rendered_steps = len([line for line in lesson_step_lines if re.search(r"\blesson_step rendered\b", line)])
    lesson_steps = emitted_steps if emitted_steps else rendered_steps
    layer_complete = len(
        [
            line
            for line in lesson_step_lines
            if "backgroundScene=1" in line
            and "teachingObject=1" in line
            and "robotOverlay=1" in line
        ]
    )
    lesson_prompt_tts = len(
        re.findall(
            r"lesson_step_prompt queued via tts|lesson_start_ack queued via tts",
            log_text,
        )
    )
    firmware_rendered = len(re.findall(r"lesson_step rendered .*degraded=0", log_text))
    poster_drawn = len(re.findall(r"lesson_step poster fetched\+drawn from URL", log_text))
    object_drawn = len(re.findall(r"lesson_step teaching object fetched\+drawn from URL", log_text))

    missing = []
    if lesson_prepare < 1:
        missing.append("lesson_prepare")
    if lesson_start < 1:
        missing.append("lesson_start")
    if lesson_steps < expected_lesson_steps:
        missing.append(f"lesson_steps>={expected_lesson_steps}")
    if layer_complete < expected_lesson_steps:
        missing.append("lesson_step_layers_complete")
    if lesson_prompt_tts < expected_lesson_steps:
        missing.append(f"lesson_prompt_tts>={expected_lesson_steps}")
    if firmware_rendered < expected_lesson_steps:
        missing.append(f"lesson_firmware_rendered>={expected_lesson_steps}")
    if poster_drawn < expected_lesson_steps:
        missing.append(f"lesson_posters_drawn>={expected_lesson_steps}")
    if object_drawn < expected_lesson_steps:
        missing.append(f"lesson_objects_drawn>={expected_lesson_steps}")
    if lesson_stop < 1:
        missing.append("lesson_stop")
    if lesson_completed < 1:
        missing.append("lesson_completed")

    return {
        "lesson_prepare": lesson_prepare,
        "lesson_start": lesson_start,
        "lesson_steps": lesson_steps,
        "lesson_step_layers_complete": layer_complete,
        "lesson_prompt_tts": lesson_prompt_tts,
        "lesson_firmware_rendered": firmware_rendered,
        "lesson_posters_drawn": poster_drawn,
        "lesson_objects_drawn": object_drawn,
        "lesson_stop": lesson_stop,
        "lesson_completed": lesson_completed,
        "missing": missing,
    }

def audit_log(
    log_text,
    device_id,
    client_id,
    min_interrupts=10,
    server_ip=None,
    require_lesson=False,
    expected_lesson_steps=9,
):
    audio_interrupts = len(re.findall(r"user_interrupted reason=audio_input", log_text))
    input_audio_diag = len(re.findall(r"input_audio_diag .*rms=", log_text))
    user_transcripts = len(
        re.findall(r"Google Live transcript source=user chars=\d+", log_text)
    )
    physical_ws_connected = _has_physical_ws_connection(
        log_text,
        device_id,
        client_id,
        server_ip=server_ip,
    )
    fatal_hits = [pattern for pattern in FATAL_PATTERNS if pattern in log_text]

    missing = []
    if not physical_ws_connected:
        missing.append("physical_ws_connected")
    if input_audio_diag < 1:
        missing.append("input_audio_diag")
    if user_transcripts < 1:
        missing.append("user_transcript")
    if audio_interrupts < min_interrupts:
        missing.append(f"audio_interrupts>={min_interrupts}")
    if fatal_hits:
        missing.append("no_fatal_patterns")

    lesson = None
    if require_lesson:
        lesson = _audit_lesson_flow(log_text, expected_lesson_steps)
        missing.extend(lesson["missing"])

    result = {
        "passed": not missing,
        "physical_ws_connected": physical_ws_connected,
        "input_audio_diag": input_audio_diag,
        "user_transcripts": user_transcripts,
        "audio_interrupts": audio_interrupts,
        "fatal_hits": fatal_hits,
        "missing": missing,
    }
    if lesson is not None:
        for key, value in lesson.items():
            if key != "missing":
                result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Audit tbot_server logs for physical Google Live voice interrupt smoke evidence."
    )
    parser.add_argument("log_file", type=Path)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--min-interrupts", type=int, default=10)
    parser.add_argument("--server-ip")
    parser.add_argument("--require-lesson", action="store_true")
    parser.add_argument("--expected-lesson-steps", type=int, default=9)
    args = parser.parse_args()

    log_text = args.log_file.read_text(encoding="utf-8", errors="replace")
    result = audit_log(
        log_text,
        device_id=args.device_id,
        client_id=args.client_id,
        min_interrupts=args.min_interrupts,
        server_ip=args.server_ip,
        require_lesson=args.require_lesson,
        expected_lesson_steps=args.expected_lesson_steps,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
