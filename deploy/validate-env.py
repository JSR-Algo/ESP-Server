#!/usr/bin/env python3
"""Validate dotenv assignment syntax without evaluating or displaying values."""

from __future__ import annotations

import re
import sys
from argparse import ArgumentParser
from pathlib import Path

ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class EnvSyntaxError(ValueError):
    def __init__(self, line: int, key: str | None, reason: str) -> None:
        self.line = line
        self.key = key
        self.reason = reason
        super().__init__(reason)


def _has_command_syntax(value: str) -> bool:
    return "$(" in value or "`" in value or "${" in value or any(char in value for char in ";|&<>")


def _quoted_value(lines: list[str], start: int, key: str, raw: str) -> tuple[int, str]:
    quote = raw[0]
    value = raw[1:]
    parts: list[str] = []
    index = start
    escaped = False
    while True:
        for offset, char in enumerate(value):
            if quote == '"' and char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                trailing = value[offset + 1 :].strip()
                if trailing and not trailing.startswith("#"):
                    raise EnvSyntaxError(index + 1, key, "unexpected text after closing quote")
                parts.append(value[:offset])
                return index, "\n".join(parts)
            escaped = False
        index += 1
        if index >= len(lines):
            raise EnvSyntaxError(start + 1, key, "unterminated quoted value")
        parts.append(value)
        value = lines[index]


def validate(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assignments: dict[str, str] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        match = ASSIGNMENT.fullmatch(stripped)
        if not match:
            raise EnvSyntaxError(index + 1, None, "expected NAME=value assignment")
        key, raw = match.groups()
        if key in assignments:
            raise EnvSyntaxError(index + 1, key, "duplicate assignment")
        if _has_command_syntax(raw):
            raise EnvSyntaxError(index + 1, key, "executable shell syntax is not allowed")
        if raw.startswith(("'", '"')):
            end, value = _quoted_value(lines, index, key, raw)
            for continued in lines[index + 1 : end + 1]:
                if _has_command_syntax(continued):
                    raise EnvSyntaxError(index + 1, key, "executable shell syntax is not allowed")
            index = end
        else:
            before_comment = raw.split("#", 1)[0].rstrip()
            if any(char.isspace() for char in before_comment):
                raise EnvSyntaxError(index + 1, key, "unquoted whitespace creates a shell command")
            if "\\" in before_comment:
                raise EnvSyntaxError(index + 1, key, "unquoted escapes are not allowed")
            value = before_comment
        assignments[key] = value
        index += 1
    return assignments


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--expect", nargs=2, metavar=("KEY", "VALUE"))
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    try:
        assignments = validate(args.env_file)
    except (OSError, UnicodeError) as exc:
        print(f"env validation failed: cannot read file ({exc.__class__.__name__})", file=sys.stderr)
        return 1
    except EnvSyntaxError as exc:
        key = f" {exc.key}" if exc.key else ""
        print(f"env validation failed: line {exc.line}{key}: {exc.reason}", file=sys.stderr)
        return 1
    if args.expect:
        key, expected = args.expect
        if assignments.get(key) != expected:
            print(f"env validation failed: {key} does not match the reviewed release", file=sys.stderr)
            return 1
    print(f"env validation ok: {len(assignments)} assignments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
