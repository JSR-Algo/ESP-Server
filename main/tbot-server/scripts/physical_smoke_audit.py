#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


FATAL_PATTERNS = (
    "Traceback",
    "Logging error",
    "FRAME_ACK_TIMEOUT",
    "lesson_error",
    "duplicate session",
    "stale audio",
    "self-interrupt",
    "fallback_triggered",
    "JPEG decode failed",
    "undecodable image; skipping",
    "lesson_step rejected",
    "lesson_step poster fetch failed",
    "lesson_step teaching object fetch failed",
    "lesson_step robot overlay fetch failed",
    "lesson image fetch: status",
    "lesson image fetch: read error",
    "lesson image fetch: empty body",
)

IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS = (
    re.compile(r"(?i)\bpronunciation\b.*\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b"),
    re.compile(r"(?i)\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b.*\bpronunciation\b"),
    re.compile(r"(?i)\b(child response|child_response|recognized[_-]?text|transcript)\b.*\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b"),
    re.compile(r"(?i)\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b.*\b(child response|child_response|recognized[_-]?text|transcript)\b"),
    re.compile(r"(?i)\bscore\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(phát âm|phat am)\b.*\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi|sửa|sua|đánh giá|danh gia|chấm|cham)\b"),
    re.compile(r"(?i)\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi|sửa|sua|đánh giá|danh gia|chấm|cham)\b.*\b(phát âm|phat am)\b"),
    re.compile(r"(?i)\bsai rồi\b"),
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


def _extract_step_id(line):
    match = re.search(r"\bstepId=([^\s,'\"}]+)", line)
    if match:
        return match.group(1)
    match = re.search(r'"stepId"\s*:\s*"([^"]+)"', line)
    if match:
        return match.group(1)
    return None


def _extract_step_type(line):
    match = re.search(r"\bstepType=([^\s,'\"}]+)", line)
    if match:
        return match.group(1)
    match = re.search(r'"stepType"\s*:\s*"([^"]+)"', line)
    if match:
        return match.group(1)
    return None


def _extract_prompt_text(line):
    match = re.search(r"\btext\s*=\s*'([^']*)'", line)
    if match:
        return match.group(1)
    match = re.search(r'\btext\s*=\s*"([^"]*)"', line)
    if match:
        return match.group(1)
    match = re.search(r'"text"\s*:\s*"([^"]*)"', line)
    if match:
        return match.group(1)
    return ""


def _is_lesson_step_prompt_line(line):
    lowered = line.lower()
    return (
        "lesson_step_prompt queued via tts" in lowered
        or "lesson_step_prompt queued via live text" in lowered
    )


def _is_guided_question(text):
    normalized = text.strip().lower()
    if not normalized or "?" not in normalized:
        return False
    command_starts = (
        "say ",
        "repeat ",
        "read ",
        "tell ",
        "hãy ",
        "hay ",
        "nói ",
        "noi ",
        "lặp lại ",
        "lap lai ",
    )
    if normalized.startswith(command_starts):
        return False
    guided_markers = (
        "can you",
        "could you",
        "what",
        "which",
        "where",
        "how",
        "do you",
        "con có",
        "con co",
        "con thấy",
        "con thay",
        "con nghe",
        "mình cùng",
        "minh cung",
    )
    return any(marker in normalized for marker in guided_markers)

def _is_child_response_window_line(line):
    lowered = line.lower()
    if (
        "child response window opened" in lowered
        or "lesson_child_response_window opened" in lowered
        or "open_lesson_child_response_window" in lowered
    ):
        return True
    return "user_audio_window_open" in lowered and re.search(
        r"\breason\s*=\s*['\"]?lesson_child_response\b", lowered
    )


def _has_observable_child_input(line):
    if _has_contradictory_child_response_state(line):
        return False
    observable_patterns = (
        re.compile(r"(?i)\brecognized[_-]?text\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\btranscript\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\butterance\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\bchild[_-]?response\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\bchoice[_-]?id\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\btap[_-]?target\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
    )
    placeholder_values = {
        "unknown",
        "unrecognized",
        "noise",
        "silence",
        "no_speech",
        "none",
        "null",
        "undefined",
    }
    for pattern in observable_patterns:
        match = pattern.search(line)
        if not match:
            continue
        value = match.group(1).strip("'\" .,!?[]<>").lower()
        if value and value not in placeholder_values:
            return True
    return False


def _has_contradictory_child_response_state(line):
    lowered = line.lower()
    false_markers = (
        "accepted=false",
        '"accepted":false',
        "accepted=0",
        '"accepted":0',
        "recognized=false",
        '"recognized":false',
        "recognized=0",
        '"recognized":0',
        "handled=false",
        '"handled":false',
        "handled=0",
        '"handled":0',
    )
    if any(marker in lowered for marker in false_markers):
        return True
    confidence_match = re.search(
        r"(?i)\b(?:confidence|asrConfidence|asr_confidence)\s*[:=]\s*['\"]?([^,'\"}\s;]+)",
        line,
    )
    if not confidence_match:
        return False
    try:
        return float(confidence_match.group(1)) <= 0
    except ValueError:
        return True


def _immediate_pronunciation_scoring_count(lines):
    lesson_context = (
        "lesson",
        "stepid",
        "step_id",
        "prompt",
        "tts",
        "child response",
        "child_response",
        "recognizedtext",
        "recognized_text",
        "transcript",
        "robot",
        "teebot",
    )
    count = 0
    for line in lines:
        lowered = line.lower()
        if not any(token in lowered for token in lesson_context):
            continue
        if any(pattern.search(line) for pattern in IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS):
            count += 1
    return count


def _lesson_prompt_after_render_count(lines):
    current_step_id = None
    emitted_fallback = 0
    rendered = {}
    prompts = {}

    for index, line in enumerate(lines):
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif re.search(r"\blesson_step rendered\b", line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                rendered.setdefault(step_id, index)
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, prompt_index in prompts.items()
            if step_id in rendered and rendered[step_id] < prompt_index
        ]
    )


def _interactive_child_response_ordered_count(lines):
    current_step_id = None
    emitted_fallback = 0
    windows = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_child_response_window_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                windows.setdefault(step_id, index)
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in windows and windows[step_id] < response_index
        ]
    )


def _interactive_child_response_before_progress_count(lines):
    responses = {}
    progress = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id:
                responses.setdefault(step_id, index)
        elif (
            "lesson_progress" in lowered
            and "step_completed" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id:
                progress.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, progress_index in progress.items()
            if step_id in responses and responses[step_id] < progress_index
        ]
    )


def _interactive_child_response_after_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in prompts and prompts[step_id] < response_index
        ]
    )


def _interactive_child_response_window_after_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    windows = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)
        elif _is_child_response_window_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                windows.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, window_index in windows.items()
            if step_id in prompts and prompts[step_id] < window_index
        ]
    )


def _interactive_child_response_observed_count(lines):
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id and _has_observable_child_input(line):
                responses.setdefault(step_id, index)

    return len(responses)


def _passive_child_response_activity_count(lines):
    interactive_types = {"model", "listen", "repeat", "fillblank"}
    passive_steps = set()
    activity_count = 0
    current_step_id = None

    for line in lines:
        if re.search(r"\bemit lesson_step\b", line):
            step_id = _extract_step_id(line)
            current_step_id = step_id or current_step_id
            step_type = (_extract_step_type(line) or "").lower()
            if step_id and step_type and step_type not in interactive_types:
                passive_steps.add(step_id)
            continue

        lowered = line.lower()
        if not (
            _is_child_response_window_line(line)
            or "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            continue
        step_id = _extract_step_id(line) or current_step_id
        if step_id in passive_steps:
            activity_count += 1

    return activity_count


def _lesson_step_layers_drawn_by_step_count(lines):
    emitted_step_ids = []
    layers_by_step = {}

    for line in lines:
        if re.search(r"\bemit lesson_step\b", line):
            step_id = _extract_step_id(line)
            if step_id:
                emitted_step_ids.append(step_id)
                layers_by_step.setdefault(step_id, set())
            continue

        step_id = _extract_step_id(line)
        if not step_id:
            continue
        if "lesson_step poster fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("backgroundScene")
        elif "lesson_step teaching object fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("teachingObject")
        elif "lesson_step robot overlay fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("robotOverlay")

    return len(
        [
            step_id
            for step_id in set(emitted_step_ids)
            if {"backgroundScene", "teachingObject", "robotOverlay"}.issubset(
                layers_by_step.get(step_id, set())
            )
        ]
    )


def _interactive_guided_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, (index, _extract_prompt_text(line)))
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in prompts
            and prompts[step_id][0] < response_index
            and _is_guided_question(prompts[step_id][1])
        ]
    )


def _audit_lesson_flow(log_text, expected_lesson_steps, expected_interactive_steps=0):
    lines = log_text.splitlines()
    lesson_prepare = len(re.findall(r"\blesson_prepare\b", log_text))
    lesson_start = len(re.findall(r"\blesson_start\b", log_text))
    lesson_stop = len(re.findall(r"\blesson_stop\b", log_text))
    lesson_completed = len(re.findall(r"lesson_completed|stepsCompleted=\d+", log_text))
    lesson_step_lines = [
        line for line in lines if re.search(r"\blesson_step\b", line)
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
    lesson_prompt_tts = sum(1 for line in lines if _is_lesson_step_prompt_line(line))
    firmware_rendered = len(re.findall(r"lesson_step rendered .*degraded=0", log_text))
    poster_drawn = len(re.findall(r"lesson_step poster fetched\+drawn from URL", log_text))
    object_drawn = len(re.findall(r"lesson_step teaching object fetched\+drawn from URL", log_text))
    overlay_drawn = len(re.findall(r"lesson_step robot overlay fetched\+drawn from URL", log_text))
    layers_drawn_by_step = _lesson_step_layers_drawn_by_step_count(lines)
    prompt_after_render = _lesson_prompt_after_render_count(lines)
    child_response_windows = sum(1 for line in lines if _is_child_response_window_line(line))
    child_responses = len(
        re.findall(
            r"interactive child response accepted|lesson_child_response accepted",
            log_text,
            flags=re.IGNORECASE,
        )
    )
    child_responses_observed = _interactive_child_response_observed_count(lines)
    child_response_ordered = _interactive_child_response_ordered_count(lines)
    child_response_after_prompt = _interactive_child_response_after_prompt_count(lines)
    child_response_window_after_prompt = _interactive_child_response_window_after_prompt_count(lines)
    child_response_before_progress = _interactive_child_response_before_progress_count(lines)
    interactive_guided_prompts = _interactive_guided_prompt_count(lines)
    immediate_pronunciation_scoring = _immediate_pronunciation_scoring_count(lines)
    passive_child_response_activity = _passive_child_response_activity_count(lines)

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
    if prompt_after_render < expected_lesson_steps:
        missing.append("lesson_prompt_after_render")
    if firmware_rendered < expected_lesson_steps:
        missing.append(f"lesson_firmware_rendered>={expected_lesson_steps}")
    if poster_drawn < expected_lesson_steps:
        missing.append(f"lesson_posters_drawn>={expected_lesson_steps}")
    if object_drawn < expected_lesson_steps:
        missing.append(f"lesson_objects_drawn>={expected_lesson_steps}")
    if overlay_drawn < expected_lesson_steps:
        missing.append(f"lesson_robot_overlays_drawn>={expected_lesson_steps}")
    if layers_drawn_by_step < expected_lesson_steps:
        missing.append("lesson_step_layers_drawn_by_step")
    if expected_interactive_steps > 0 and child_response_windows < expected_interactive_steps:
        missing.append(f"interactive_child_response_windows>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_responses < expected_interactive_steps:
        missing.append(f"interactive_child_responses>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_responses_observed < expected_interactive_steps:
        missing.append(f"interactive_child_responses_observed>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_ordered < expected_interactive_steps:
        missing.append(f"interactive_child_response_ordered>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_after_prompt < expected_interactive_steps:
        missing.append(f"interactive_child_response_after_prompt>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_window_after_prompt < expected_interactive_steps:
        missing.append(f"interactive_child_response_window_after_prompt>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and interactive_guided_prompts < expected_interactive_steps:
        missing.append(f"interactive_guided_prompts>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_before_progress < expected_interactive_steps:
        missing.append(f"interactive_child_response_before_progress>={expected_interactive_steps}")
    if immediate_pronunciation_scoring:
        missing.append("no_immediate_pronunciation_scoring")
    if passive_child_response_activity:
        missing.append("no_passive_child_response_activity")
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
        "lesson_prompt_after_render": prompt_after_render,
        "lesson_firmware_rendered": firmware_rendered,
        "lesson_posters_drawn": poster_drawn,
        "lesson_objects_drawn": object_drawn,
        "lesson_robot_overlays_drawn": overlay_drawn,
        "lesson_step_layers_drawn_by_step": layers_drawn_by_step,
        "interactive_child_response_windows": child_response_windows,
        "interactive_child_responses": child_responses,
        "interactive_child_responses_observed": child_responses_observed,
        "interactive_child_response_ordered": child_response_ordered,
        "interactive_child_response_after_prompt": child_response_after_prompt,
        "interactive_child_response_window_after_prompt": child_response_window_after_prompt,
        "interactive_guided_prompts": interactive_guided_prompts,
        "interactive_child_response_before_progress": child_response_before_progress,
        "immediate_pronunciation_scoring": immediate_pronunciation_scoring,
        "passive_child_response_activity": passive_child_response_activity,
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
    expected_interactive_steps=0,
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
        lesson = _audit_lesson_flow(log_text, expected_lesson_steps, expected_interactive_steps)
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
    parser.add_argument("--expected-interactive-steps", type=int, default=0)
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
        expected_interactive_steps=args.expected_interactive_steps,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
