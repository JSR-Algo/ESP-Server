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

TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")

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


def read_stamped(path: Path, strip_prefix: bool) -> list[tuple[str, int, str]]:
    """Return (timestamp, ordinal, line). Ordinal keeps same-second lines stable."""
    out: list[tuple[str, int, str]] = []
    last = ""
    for index, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        match = TIMESTAMP.match(raw)
        if match:
            last = match.group(1).replace("T", " ")
            line = raw[match.end():].lstrip() if strip_prefix else raw
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

    # Ordering within a shared timestamp is the whole problem: both logs stamp whole
    # seconds, so a naive (timestamp, ordinal) sort ZIPS the two files by line number --
    # device line 12 lands beside server line 12 regardless of when either happened.
    # That is what put the device's `serial RX lesson_prepare` ahead of the server's
    # manifest fetch and broke the verifier's ordered cursor.
    #
    # The lesson wire sequence is the reliable tiebreak: frames carry a monotonic
    # `sequence`, the server logs it on emit and the device echoes it as seq=/acks=.
    # Lines naming no sequence inherit the last one seen, so setup lines stay ahead of
    # frame 1 and per-step render lines stay with their step. Within one sequence the
    # server comes first: it emits, then the device receives and acks.
    def keyed(rows, source):
        out = []
        carried = -1
        for ts, index, line in rows:
            sequence = _wire_sequence(line)
            if sequence is not None:
                carried = sequence
            out.append((ts, carried, source, index, line))
        return out

    merged = sorted(
        keyed(server, 0) + keyed(device, 1),
        key=lambda row: (row[0], row[1], row[2], row[3]),
    )

    Path(args.out).write_text("\n".join(row[4] for row in merged) + "\n", encoding="utf-8")
    print(f"merged {len(server)} server + {len(device)} device lines -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
