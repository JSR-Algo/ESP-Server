#!/usr/bin/env python3
"""Create candidate-bound evidence for the trusted-operator precondition."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    _manifest = importlib.import_module("scripts.course_mode_candidate_manifest")
except ModuleNotFoundError:
    _manifest = importlib.import_module("course_mode_candidate_manifest")

MAX_CANDIDATE_BYTES = _manifest.MAX_CANDIDATE_BYTES
strict_json_loads = _manifest.strict_json_loads
validate_candidate = _manifest.validate_candidate
_open_directory_secure = _manifest._open_directory_secure


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
        metadata.st_mode, metadata.st_nlink, metadata.st_uid,
    )


def _read_candidate_snapshot(path: Path) -> tuple[dict, bytes, tuple[int, ...]]:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parent_fd = _open_directory_secure(absolute.parent)
    file_fd: int | None = None
    try:
        file_fd = os.open(
            absolute.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CANDIDATE_BYTES:
            raise OSError("invalid candidate")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("candidate changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(file_fd)
        current = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = _metadata_identity(before)
        if identity != _metadata_identity(after) or identity != _metadata_identity(current):
            raise OSError("candidate changed")
        raw = b"".join(chunks)
        candidate = strict_json_loads(raw)
        if not isinstance(candidate, dict):
            raise ValueError("invalid candidate")
        return candidate, raw, identity
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _candidate_unchanged(path: Path, raw: bytes, identity: tuple[int, ...]) -> bool:
    try:
        _candidate, final_raw, final_identity = _read_candidate_snapshot(path)
        return final_raw == raw and final_identity == identity
    except (OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False


def _secure_output_parent(evidence_root: Path, output: Path) -> tuple[int, str]:
    if not evidence_root.is_absolute() or not output.is_absolute():
        raise OSError("absolute paths required")
    normalized_root = Path(os.path.abspath(evidence_root))
    normalized_output = Path(os.path.abspath(output))
    relative = normalized_output.relative_to(normalized_root)
    if len(relative.parts) < 1 or relative.name in {"", ".", ".."}:
        raise OSError("invalid output")

    effective_uid = os.geteuid()
    directory_fd = _open_directory_secure(normalized_root)
    try:
        for component in (Path(), *relative.parts[:-1]):
            if component != Path():
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            metadata = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != effective_uid
                or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise OSError("insecure output parent")
        return directory_fd, relative.name
    except Exception:
        os.close(directory_fd)
        raise


def _write_all(file_fd: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(file_fd, raw[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _parent_still_bound(path: Path, parent_fd: int) -> bool:
    verification_fd: int | None = None
    try:
        verification_fd = _open_directory_secure(Path(os.path.abspath(path)))
        expected = os.fstat(parent_fd)
        observed = os.fstat(verification_fd)
        effective_uid = os.geteuid()
        return (
            (expected.st_dev, expected.st_ino) == (observed.st_dev, observed.st_ino)
            and expected.st_uid == effective_uid
            and observed.st_uid == effective_uid
            and not expected.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            and not observed.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        )
    except OSError:
        return False
    finally:
        if verification_fd is not None:
            os.close(verification_fd)


def _remove_owned_output(
    parent_fd: int, name: str, file_fd: int, identity: tuple[int, int, int],
) -> None:
    descriptor_open = True
    try:
        descriptor = os.fstat(file_fd)
    except OSError:
        descriptor_open = False
    else:
        if (descriptor.st_dev, descriptor.st_ino, descriptor.st_uid) != identity:
            raise OSError("created output descriptor changed")
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        metadata = None
    except OSError:
        if descriptor_open:
            _invalidate_owned_descriptor(file_fd)
        raise
    if metadata is not None and (
        (metadata.st_dev, metadata.st_ino, metadata.st_uid) == identity
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
    ):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError as unlink_error:
            if descriptor_open:
                _invalidate_owned_descriptor(file_fd)
            raise OSError("could not remove failed output") from unlink_error
        os.fsync(parent_fd)
        return
    if descriptor_open:
        _invalidate_owned_descriptor(file_fd)
        return
    raise OSError("could not identify failed output")


def _invalidate_owned_descriptor(file_fd: int) -> None:
    os.ftruncate(file_fd, 0)
    os.fsync(file_fd)


def _close_after_failure(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as first_error:
        try:
            os.close(fd)
        except OSError:
            raise first_error


def _output_still_bound(file_fd: int, parent_fd: int, name: str) -> bool:
    try:
        descriptor = os.fstat(file_fd)
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return (
            _metadata_identity(descriptor) == _metadata_identity(entry)
            and stat.S_ISREG(descriptor.st_mode)
            and stat.S_IMODE(descriptor.st_mode) == 0o444
            and descriptor.st_nlink == 1
            and descriptor.st_uid == os.geteuid()
        )
    except OSError:
        return False


def _canonical_payload(candidate: dict) -> bytes:
    payload = {
        "candidateId": candidate["candidateId"],
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "effectiveUid": os.geteuid(),
        "gateSha": candidate["repositories"]["adminEsp"]["sha"],
        "hostName": socket.gethostname(),
        "sameUidThreatModel": "malicious-process-excluded",
        "schemaVersion": 1,
        "trustedOperatorAccountConfirmed": True,
        "untrustedAutomationStoppedConfirmed": True,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _create(candidate_path: Path, output: Path) -> None:
    candidate, candidate_raw, candidate_identity = _read_candidate_snapshot(candidate_path)
    if validate_candidate(candidate):
        raise ValueError("candidate validation failed")

    parent_fd: int | None = None
    file_fd: int | None = None
    created_identity: tuple[int, int, int] | None = None
    complete = False
    name = ""
    try:
        parent_fd, name = _secure_output_parent(Path(candidate["evidenceRoot"]), output)
        file_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o444,
            dir_fd=parent_fd,
        )
        metadata = os.fstat(file_fd)
        created_identity = (metadata.st_dev, metadata.st_ino, metadata.st_uid)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
        ):
            raise OSError("invalid output")
        os.fchmod(file_fd, 0o444)
        _write_all(file_fd, _canonical_payload(candidate))
        os.fsync(file_fd)
        if (
            not _output_still_bound(file_fd, parent_fd, name)
            or not _parent_still_bound(output.parent, parent_fd)
        ):
            raise OSError("output changed")
        os.fsync(parent_fd)
        if not _candidate_unchanged(candidate_path, candidate_raw, candidate_identity):
            raise OSError("candidate changed")
        if (
            not _parent_still_bound(output.parent, parent_fd)
            or not _output_still_bound(file_fd, parent_fd, name)
        ):
            raise OSError("output changed")
        if not _candidate_unchanged(candidate_path, candidate_raw, candidate_identity):
            raise OSError("candidate changed")
        try:
            os.close(file_fd)
        except OSError:
            raise OSError("output close failed")
        file_fd = None
        complete = True
    finally:
        try:
            if (
                not complete and parent_fd is not None and file_fd is not None
                and created_identity is not None
            ):
                _remove_owned_output(parent_fd, name, file_fd, created_identity)
        finally:
            try:
                if file_fd is not None:
                    _close_after_failure(file_fd)
            finally:
                if parent_fd is not None:
                    try:
                        _close_after_failure(parent_fd)
                    except OSError:
                        if not complete:
                            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record the Course Mode operator precondition.", allow_abbrev=False,
    )
    parser.add_argument("--candidate", required=True, type=Path, metavar="PATH")
    parser.add_argument("--output", required=True, type=Path, metavar="PATH")
    parser.add_argument("--confirm-trusted-operator-account", action="store_true")
    parser.add_argument("--confirm-untrusted-automation-stopped", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not (
        args.confirm_trusted_operator_account
        and args.confirm_untrusted_automation_stopped
    ):
        return 2
    try:
        _create(args.candidate, args.output)
        return 0
    except (
        KeyError, OSError, TypeError, UnicodeDecodeError, ValueError,
        json.JSONDecodeError,
    ):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
