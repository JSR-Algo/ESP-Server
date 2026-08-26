#!/usr/bin/env python3
"""Fail closed unless an authorized firmware readback targets the local lab."""

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: verify_firmware_endpoints.py APP_READBACK OTA_URL WEBSOCKET_URL",
            file=sys.stderr,
        )
        return 2

    image_path = Path(sys.argv[1])
    try:
        image = image_path.read_bytes()
    except OSError as exc:
        print(f"cannot read authorized firmware image {image_path}: {exc}", file=sys.stderr)
        return 1

    labels = ("OTA", "WebSocket")
    for label, endpoint in zip(labels, sys.argv[2:]):
        if endpoint.encode("ascii") not in image:
            print(
                f"authorized firmware does not contain the local {label} endpoint: {endpoint}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
