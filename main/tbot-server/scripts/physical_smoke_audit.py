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


def audit_log(log_text, device_id, client_id, min_interrupts=10, server_ip=None):
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

    return {
        "passed": not missing,
        "physical_ws_connected": physical_ws_connected,
        "input_audio_diag": input_audio_diag,
        "user_transcripts": user_transcripts,
        "audio_interrupts": audio_interrupts,
        "fatal_hits": fatal_hits,
        "missing": missing,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Audit tbot_server logs for physical Google Live voice interrupt smoke evidence."
    )
    parser.add_argument("log_file", type=Path)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--min-interrupts", type=int, default=10)
    parser.add_argument("--server-ip")
    args = parser.parse_args()

    log_text = args.log_file.read_text(encoding="utf-8", errors="replace")
    result = audit_log(
        log_text,
        device_id=args.device_id,
        client_id=args.client_id,
        min_interrupts=args.min_interrupts,
        server_ip=args.server_ip,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
