#!/usr/bin/env python3
"""Merge the ESP server log and the simulated device's serial log into one timeline.

`scripts/lesson_e2e_log_verify.py` walks a MONOTONIC cursor through its input: each
ordered checkpoint must be found at or after the index of the previous one. Passing two
log files therefore compares them in **file order**, not event order, and the verdict
changes with the order the files are given (measured on one identical capture:
esp,serial -> 76/101 but serial,esp -> 73/101 — F-T53-15).

Interleaving by timestamp is what makes the cursor meaningful. `lesson_e2e_live_capture.py`
already emits a merged `timeline.log` for live runs; this is the simulated equivalent.

Inputs:
  * the ESP server log, whose lines start `YYYY-MM-DD HH:MM:SS`;
  * the simulator's `--timeline-log`, the same serial lines with a wall-clock prefix
    (firmware serial itself carries only boot-relative ticks and cannot be merged).

Lines with no parseable timestamp inherit the timestamp of the previous line, so
multi-line payloads and banners stay attached to the event they belong to instead of
sorting to the top.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# Two stamp shapes reach this script and they are NOT interchangeable:
#   * the file sink  `YYYY-MM-DD HH:MM:SS` (config log_format_file), and
#   * the stdout sink `YYMMDD HH:MM:SS.SSS` (config log_format) — which is what
#     `docker logs` gives, and therefore what run-e2e.sh captures.
# Matching only the first silently treated EVERY stdout line as "no timestamp of its
# own", so the whole server log inherited one carried value and sorted as a single
# block — the exact failure this script exists to prevent.
ISO_TIMESTAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?")
CONSOLE_TIMESTAMP = re.compile(r"^(\d{2})(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?")


def _parse_timestamp(raw: str):
    """Return (sortable 'YYYY-MM-DD HH:MM:SS.mmm', end offset) or (None, 0)."""
    match = ISO_TIMESTAMP.match(raw)
    if match:
        year, month, day, hour, minute, second, frac = match.groups()
    else:
        match = CONSOLE_TIMESTAMP.match(raw)
        if not match:
            return None, 0
        yy, month, day, hour, minute, second, frac = match.groups()
        year = f"20{yy}"
    millis = (frac or "0").ljust(3, "0")[:3]
    return f"{year}-{month}-{day} {hour}:{minute}:{second}.{millis}", match.end()

WIRE_SEQUENCE_PATTERNS = (
    re.compile(r'"sequence"\s*:\s*(\d+)'),
    re.compile(r"\bsequence=(\d+)"),
    re.compile(r"\bseq=(\d+)"),
    re.compile(r"\backs=(\d+)"),
)


def _wire_sequence(line: str):
    """The lesson wire sequence a line names, if any (mirrors the verifier's helper)."""
    for pattern in WIRE_SEQUENCE_PATTERNS:
        match = pattern.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _same_millisecond_causal_phase(line: str) -> int:
    """Order the drain handshake when wall clocks collapse it to one millisecond."""
    if "emit lesson_" in line:
        return 5
    if "tts_stop_sent" in line:
        return 10
    if "serial RX lesson_" in line or "Audio playback complete" in line:
        return 20
    if "serial TX lesson_ack" in line:
        return 30
    if "Received lesson_ack message" in line or "Received tts_ack message" in line:
        return 40
    if "lesson_prompt_device_drain_ack" in line:
        return 50
    if "LessonRuntime event lesson_" in line or "lesson_child_response_window_open" in line:
        return 60
    return 55


def read_stamped(path: Path, strip_prefix: bool) -> list[tuple[str, int, str]]:
    """Return (timestamp, ordinal, line). Ordinal keeps same-second lines stable."""
    out: list[tuple[str, int, str]] = []
    last = ""
    for index, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        stamp, end = _parse_timestamp(raw)
        if stamp is not None:
            last = stamp
            line = raw[end:].lstrip() if strip_prefix else raw
        else:
            # No timestamp of its own: it belongs to whatever was logged last.
            line = raw
        out.append((last, index, line))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-log", required=True)
    parser.add_argument("--device-timeline", required=True, help="simulator --timeline-log output")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    server = read_stamped(Path(args.server_log), strip_prefix=False)
    # The wall-clock prefix is scaffolding for merging only; strip it so the verifier
    # sees byte-faithful firmware serial lines.
    device = read_stamped(Path(args.device_timeline), strip_prefix=True)

    # Both streams now stamp MILLISECONDS (server via `log.log_format` in the sim
    # config, device via the simulator's --timeline-log), so the timestamp itself
    # carries the ordering and dominates this key. That is the difference from the
    # session-6 attempt, where whole-second stamps left the true order unrecorded and
    # the tiebreak below decided everything -- which is why merging reshuffled the
    # verdict instead of converging it.
    #
    # The wire sequence remains the tiebreak WITHIN one millisecond: frames carry a
    # monotonic `sequence`, the server logs it on emit and the device echoes it as
    # seq=/acks=. Lines naming no sequence inherit the last one seen, so setup lines
    # stay ahead of frame 1 and per-step render lines stay with their step. Within one
    # sequence the server normally comes first: it emits, then the device receives
    # and acks. The drain handshake is the exception when both clocks round the
    # causal chain into one millisecond; its explicit phase preserves stop ->
    # playback -> ack receipt -> ack acceptance -> child-window ordering.
    def keyed(rows, source):
        out = []
        carried = -1
        for ts, index, line in rows:
            sequence = _wire_sequence(line)
            if sequence is not None:
                carried = sequence
            out.append(
                (
                    ts,
                    carried,
                    _same_millisecond_causal_phase(line),
                    source,
                    index,
                    line,
                )
            )
        return out

    merged = sorted(
        keyed(server, 0) + keyed(device, 1),
        key=lambda row: (row[0], row[1], row[2], row[3], row[4]),
    )

    Path(args.out).write_text("\n".join(row[5] for row in merged) + "\n", encoding="utf-8")
    print(f"merged {len(server)} server + {len(device)} device lines -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
