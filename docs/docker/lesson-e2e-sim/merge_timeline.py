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

    # Sort by (timestamp, source, ordinal): a stable sort keeps each file's internal
    # order intact within the same second, which is the only ordering either file
    # actually guarantees at one-second resolution.
    merged = sorted(
        [(ts, 0, i, line) for ts, i, line in server]
        + [(ts, 1, i, line) for ts, i, line in device],
        key=lambda row: (row[0], row[2]),
    )

    Path(args.out).write_text("\n".join(row[3] for row in merged) + "\n", encoding="utf-8")
    print(f"merged {len(server)} server + {len(device)} device lines -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
