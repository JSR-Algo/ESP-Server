#!/usr/bin/env python3
"""Fail fast when tbot-server runs with an unsupported Python version."""

import sys
from typing import Optional, Sequence


MINIMUM_VERSION = (3, 10)


def validate_version(version: Sequence[int]) -> Optional[str]:
    """Return an actionable error for unsupported versions."""
    if tuple(version[:2]) >= MINIMUM_VERSION:
        return None

    detected = ".".join(str(part) for part in version[:3])
    return f"Python 3.10 or newer is required; detected {detected}."


def require_supported_runtime(version: Optional[Sequence[int]] = None) -> None:
    """Stop application bootstrap before importing unsupported dependencies."""
    message = validate_version(sys.version_info if version is None else version)
    if message is not None:
        raise SystemExit(message)


def main() -> int:
    message = validate_version(sys.version_info)
    if message is not None:
        print(message, file=sys.stderr)
        return 1

    detected = ".".join(str(part) for part in sys.version_info[:3])
    print(f"Python runtime OK: {detected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
