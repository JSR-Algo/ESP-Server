#!/usr/bin/env python3
"""Safely sanitize explicitly listed course-mode evidence artifacts.

The default is a read-only dry run. ``--apply`` performs a preflighted,
identity-bound transaction and emits path/hash-only audit manifests.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import hashlib
import io
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError
except ModuleNotFoundError:  # Local test runtimes may provide the compatible PyYAML API only.
    YAML = None
    import yaml

    YAMLError = yaml.YAMLError


SCHEMA_VERSION = 1
MANIFEST_NAME = "SANITIZATION-MANIFEST.json"
SUMMARY_NAME = "SANITIZED-SUMMARY.json"
OUTPUT_NAMES = {MANIFEST_NAME, SUMMARY_NAME}
ALLOWED_ACTIONS = {"delete-file", "delete-tree", "replace-text", "redact-yaml"}
ALLOWED_CLASSIFICATIONS = {
    "private-content",
    "raw-capture",
    "unsupported-artifact",
    "superseded-artifact",
    "quarantined-artifact",
}
ALLOWED_REASONS = {
    "raw capture excluded",
    "private content removed",
    "unsupported artifact",
    "superseded artifact",
    "quarantined before audit",
}
CLASSIFICATION_REASON = {
    "private-content": "private content removed",
    "raw-capture": "raw capture excluded",
    "unsupported-artifact": "unsupported artifact",
    "superseded-artifact": "superseded artifact",
    "quarantined-artifact": "quarantined before audit",
}
ENTRY_KEYS = {"action", "classification", "path", "reason", "matches", "pointers"}
REDACTION = "<redacted>"


class SanitizationError(Exception):
    """A validation or filesystem safety failure without secret-bearing text."""


@dataclass(frozen=True)
class Identity:
    device: int
    inode: int
    mode: int
    links: int
    owner: int
    size: int
    modified_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> Identity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
            links=metadata.st_nlink,
            owner=metadata.st_uid,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
        )


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    bytes: int
    identity: Identity


@dataclass
class PlannedAction:
    path: PurePosixPath
    action: str
    classification: str
    reason: str
    identity: Identity
    records: list[FileRecord]
    replacement: bytes | None = None
    replacement_mode: int | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_json(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SanitizationError("remediation spec must be a regular file")
        if metadata.st_nlink != 1:
            raise SanitizationError("remediation spec must not be a hard link")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise SanitizationError("remediation spec has unsafe ownership or mode")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            return json.load(stream, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SanitizationError("remediation spec is not valid strict JSON") from error
    finally:
        os.close(descriptor)


def _parse_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SanitizationError("entry path must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path in (PurePosixPath("."), PurePosixPath("")):
        raise SanitizationError("entry path must be a safe relative path")
    if any(part in ("", ".", "..") for part in path.parts):
        raise SanitizationError("entry path must be a safe relative path")
    if path.as_posix() in OUTPUT_NAMES:
        raise SanitizationError("entry path collides with sanitizer output")
    return path


def _parse_entries(document: object) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "entries"}:
        raise SanitizationError("remediation spec has an invalid schema")
    if type(document.get("schemaVersion")) is not int or document["schemaVersion"] != SCHEMA_VERSION:
        raise SanitizationError("remediation spec has an unsupported schema version")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise SanitizationError("remediation spec entries must be a non-empty list")
    parsed: list[dict[str, Any]] = []
    paths: list[PurePosixPath] = []
    for raw in entries:
        if not isinstance(raw, dict) or not set(raw) <= ENTRY_KEYS:
            raise SanitizationError("remediation entry has an invalid schema")
        required = {"action", "classification", "path", "reason"}
        if not required <= set(raw):
            raise SanitizationError("remediation entry is missing required fields")
        action = raw.get("action")
        classification = raw.get("classification")
        reason = raw.get("reason")
        if action not in ALLOWED_ACTIONS:
            raise SanitizationError("remediation entry uses an unsupported action")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise SanitizationError("remediation entry uses an unsupported classification")
        if reason not in ALLOWED_REASONS:
            raise SanitizationError("remediation entry uses an unsupported reason")
        if CLASSIFICATION_REASON[classification] != reason:
            raise SanitizationError("remediation classification and reason do not match")
        path = _parse_relative_path(raw.get("path"))
        if action == "replace-text":
            matches = raw.get("matches")
            if set(raw) != required | {"matches"} or not isinstance(matches, list) or not matches:
                raise SanitizationError("replace-text requires a non-empty matches list")
            if any(not isinstance(item, str) or not item or item == REDACTION for item in matches):
                raise SanitizationError("replace-text matches must be non-empty strings")
            if len(set(matches)) != len(matches):
                raise SanitizationError("replace-text matches must be unique")
        elif action == "redact-yaml":
            pointers = raw.get("pointers")
            if set(raw) != required | {"pointers"} or not isinstance(pointers, list) or not pointers:
                raise SanitizationError("redact-yaml requires a non-empty pointers list")
            if any(not isinstance(item, str) or not item.startswith("/") for item in pointers):
                raise SanitizationError("redact-yaml pointers must be JSON Pointers")
        elif set(raw) != required:
            raise SanitizationError("delete actions do not accept action-specific fields")
        for existing in paths:
            if path == existing or path.is_relative_to(existing) or existing.is_relative_to(path):
                raise SanitizationError("remediation entry paths overlap")
        paths.append(path)
        parsed.append(dict(raw, path=path))
    return parsed


def _identity(metadata: os.stat_result) -> Identity:
    return Identity.from_stat(metadata)


def _validate_directory(metadata: os.stat_result, label: str) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise SanitizationError(f"{label} must not be a symbolic link")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SanitizationError(f"{label} must be a directory")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise SanitizationError(f"{label} is an insecure parent")


def _root_fd(root: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        _validate_directory(os.fstat(descriptor), "artifact root")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_parent(root_fd: int, path: PurePosixPath) -> tuple[int, str]:
    current = os.dup(root_fd)
    try:
        for part in path.parts[:-1]:
            flags = (
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise SanitizationError("target parent is missing or a symbolic link") from error
            os.close(current)
            current = child
            _validate_directory(os.fstat(current), "target parent")
        return current, path.name
    except Exception:
        os.close(current)
        raise


def _validate_file(metadata: os.stat_result, label: str = "target") -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise SanitizationError(f"{label} must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise SanitizationError(f"{label} must be a regular file")
    if metadata.st_nlink != 1:
        raise SanitizationError(f"{label} must not be a hard link")
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise SanitizationError(f"{label} has unsafe ownership or mode")


def _read_file_at(parent_fd: int, name: str) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise SanitizationError("target must not be a symbolic link") from error
        raise
    try:
        metadata = os.fstat(descriptor)
        _validate_file(metadata)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(), metadata
    finally:
        os.close(descriptor)


def _file_record(path: PurePosixPath, data: bytes, metadata: os.stat_result) -> FileRecord:
    return FileRecord(path.as_posix(), hashlib.sha256(data).hexdigest(), len(data), _identity(metadata))


def _decode_pointer(pointer: str) -> list[str]:
    if pointer == "":
        raise SanitizationError("redact-yaml cannot replace the document root")
    if any(
        character == "~" and (index + 1 == len(pointer) or pointer[index + 1] not in "01")
        for index, character in enumerate(pointer)
    ):
        raise SanitizationError("redact-yaml pointer has an invalid escape")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _redact_yaml(data: bytes, pointers: list[str]) -> bytes:
    try:
        text = data.decode("utf-8")
        if YAML is not None:
            parser = YAML(typ="safe")
            document = parser.load(text)
        else:
            document = yaml.safe_load(text)
    except (UnicodeError, YAMLError, ValueError) as error:
        raise SanitizationError("redact-yaml target is not valid UTF-8 YAML") from error
    for pointer in pointers:
        parts = _decode_pointer(pointer)
        current = document
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                raise SanitizationError("redact-yaml pointer does not exist")
        final = parts[-1]
        if isinstance(current, dict) and final in current:
            current[final] = REDACTION
        elif isinstance(current, list) and final.isdigit() and int(final) < len(current):
            current[int(final)] = REDACTION
        else:
            raise SanitizationError("redact-yaml pointer does not exist")
    try:
        if YAML is not None:
            emitter = YAML(typ="safe")
            emitter.default_flow_style = False
            stream = io.StringIO()
            emitter.dump(document, stream)
            rendered = stream.getvalue()
        else:
            rendered = yaml.safe_dump(document, sort_keys=False, allow_unicode=False)
    except (ValueError, TypeError) as error:
        raise SanitizationError("redact-yaml target could not be rendered safely") from error
    return rendered.encode("utf-8")


def _scan_tree(parent_fd: int, name: str, path: PurePosixPath) -> tuple[Identity, list[FileRecord]]:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    _validate_directory(metadata, "delete-tree target")
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    opened_metadata = os.fstat(descriptor)
    if _identity(opened_metadata) != _identity(metadata):
        os.close(descriptor)
        raise SanitizationError("delete-tree target changed while opening")
    records: list[FileRecord] = []
    try:

        def walk(directory_fd: int, relative: PurePosixPath) -> None:
            for child_name in sorted(os.listdir(directory_fd)):
                child_path = relative / child_name
                child_metadata = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(child_metadata.st_mode):
                    _validate_directory(child_metadata, "delete-tree parent")
                    child_fd = os.open(
                        child_name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    if _identity(os.fstat(child_fd)) != _identity(child_metadata):
                        os.close(child_fd)
                        raise SanitizationError("delete-tree parent changed while opening")
                    try:
                        walk(child_fd, child_path)
                    finally:
                        os.close(child_fd)
                else:
                    data, secure_metadata = _read_file_at(directory_fd, child_name)
                    if _identity(secure_metadata) != _identity(child_metadata):
                        raise SanitizationError("delete-tree member changed during preflight")
                    records.append(_file_record(child_path, data, secure_metadata))

        walk(descriptor, path)
    finally:
        os.close(descriptor)
    return _identity(metadata), records


def _preflight(root_fd: int, entries: list[dict[str, Any]]) -> list[PlannedAction]:
    for output_name in OUTPUT_NAMES:
        with contextlib.suppress(FileNotFoundError):
            os.stat(output_name, dir_fd=root_fd, follow_symlinks=False)
            raise SanitizationError("sanitizer output already exists")
    actions: list[PlannedAction] = []
    for entry in entries:
        path = entry["path"]
        parent_fd, name = _open_parent(root_fd, path)
        try:
            if entry["action"] == "delete-tree":
                identity, records = _scan_tree(parent_fd, name, path)
                replacement = None
                replacement_mode = None
            else:
                try:
                    before_lstat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError as error:
                    raise SanitizationError("target does not exist") from error
                data, metadata = _read_file_at(parent_fd, name)
                if _identity(before_lstat) != _identity(metadata):
                    raise SanitizationError("target changed during preflight")
                identity = _identity(metadata)
                records = [_file_record(path, data, metadata)]
                replacement = None
                replacement_mode = stat.S_IMODE(metadata.st_mode)
                if entry["action"] == "replace-text":
                    try:
                        text = data.decode("utf-8")
                    except UnicodeError as error:
                        raise SanitizationError("replace-text target is not valid UTF-8") from error
                    for match in entry["matches"]:
                        if match not in text:
                            raise SanitizationError("replace-text match was not found")
                        text = text.replace(match, REDACTION)
                    replacement = text.encode("utf-8")
                elif entry["action"] == "redact-yaml":
                    replacement = _redact_yaml(data, entry["pointers"])
            actions.append(
                PlannedAction(
                    path=path,
                    action=entry["action"],
                    classification=entry["classification"],
                    reason=entry["reason"],
                    identity=identity,
                    records=records,
                    replacement=replacement,
                    replacement_mode=replacement_mode,
                )
            )
        finally:
            os.close(parent_fd)
    return actions


def _manifest(actions: list[PlannedAction], timestamp: str) -> bytes:
    entries = []
    for action in actions:
        entries.extend(
            {
                "action": action.action,
                "bytes": record.bytes,
                "classification": action.classification,
                "path": record.path,
                "reason": action.reason,
                "sha256": record.sha256,
                "timestamp": timestamp,
            }
            for record in action.records
        )
    document = {"entries": sorted(entries, key=lambda entry: entry["path"]), "schemaVersion": SCHEMA_VERSION}
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_staged(directory_fd: int, name: str, data: bytes, mode: int) -> Identity:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        return _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _identity_at(parent_fd: int, name: str) -> Identity:
    return _identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))


def _remove_tree_at(parent_fd: int, name: str) -> None:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    _validate_directory(metadata, "quarantined tree")
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    if _identity(os.fstat(descriptor)) != _identity(metadata):
        os.close(descriptor)
        raise SanitizationError("quarantined tree changed while opening")
    try:
        for child_name in sorted(os.listdir(descriptor)):
            child_metadata = os.stat(child_name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(child_metadata.st_mode):
                _remove_tree_at(descriptor, child_name)
            else:
                _validate_file(child_metadata, "quarantined file")
                os.unlink(child_name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_fd)


def _remove_at(transaction_fd: int, name: str, *, directory: bool) -> None:
    if directory:
        _remove_tree_at(transaction_fd, name)
    else:
        metadata = os.stat(name, dir_fd=transaction_fd, follow_symlinks=False)
        _validate_file(metadata, "quarantined file")
        os.unlink(name, dir_fd=transaction_fd)


def _create_transaction(root_fd: int) -> tuple[str, int]:
    for _attempt in range(32):
        name = f".course-mode-sanitize-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
        except FileExistsError:
            continue
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            _validate_directory(os.fstat(descriptor), "transaction directory")
            return name, descriptor
        except Exception:
            with contextlib.suppress(UnboundLocalError, OSError):
                os.close(descriptor)
            with contextlib.suppress(OSError):
                os.rmdir(name, dir_fd=root_fd)
            raise
    raise SanitizationError("could not allocate a private transaction directory")


def _transaction(root_fd: int, actions: list[PlannedAction]) -> None:
    transaction_name, transaction_fd = _create_transaction(root_fd)
    moved: list[tuple[int, PlannedAction, int, str]] = []
    replacements: list[tuple[int, str, Identity]] = []
    retain_transaction = False
    transaction_removed = False
    try:
        for index, action in enumerate(actions):
            if action.replacement is not None:
                _write_staged(
                    transaction_fd, f"replacement-{index}", action.replacement, action.replacement_mode or 0o400
                )
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        payload = _manifest(actions, timestamp)
        for output_name in sorted(OUTPUT_NAMES):
            _write_staged(transaction_fd, f"output-{output_name}", payload, 0o444)
        os.fsync(transaction_fd)

        try:
            for index, action in enumerate(actions):
                parent_fd, name = _open_parent(root_fd, action.path)
                if _identity_at(parent_fd, name) != action.identity:
                    os.close(parent_fd)
                    raise SanitizationError("target identity changed before commit")
                backup = f"backup-{index}"
                os.rename(name, backup, src_dir_fd=parent_fd, dst_dir_fd=transaction_fd)
                moved.append((index, action, parent_fd, name))
                if _identity_at(transaction_fd, backup) != action.identity:
                    raise SanitizationError("target identity changed during commit")
                if action.action == "delete-tree":
                    quarantined_identity, quarantined_records = _scan_tree(transaction_fd, backup, action.path)
                    if quarantined_identity != action.identity or quarantined_records != action.records:
                        raise SanitizationError("delete-tree members changed during commit")
                else:
                    quarantined_data, quarantined_metadata = _read_file_at(transaction_fd, backup)
                    quarantined_record = _file_record(action.path, quarantined_data, quarantined_metadata)
                    if quarantined_record != action.records[0]:
                        raise SanitizationError("target content changed during commit")
                if action.replacement is not None:
                    os.link(
                        f"replacement-{index}",
                        name,
                        src_dir_fd=transaction_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(f"replacement-{index}", dir_fd=transaction_fd)
                    replacements.append((parent_fd, name, _identity_at(parent_fd, name)))
                os.fsync(parent_fd)
        except Exception as commit_error:
            rollback_ok = True
            for parent_fd, name, expected in reversed(replacements):
                try:
                    if _identity_at(parent_fd, name) != expected:
                        rollback_ok = False
                        continue
                    os.unlink(name, dir_fd=parent_fd)
                except OSError:
                    rollback_ok = False
            for index, _action, parent_fd, name in reversed(moved):
                try:
                    try:
                        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                        exists = True
                    except FileNotFoundError:
                        exists = False
                    if exists:
                        rollback_ok = False
                        continue
                    os.rename(f"backup-{index}", name, src_dir_fd=transaction_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError:
                    rollback_ok = False
            if not rollback_ok:
                retain_transaction = True
                raise SanitizationError("commit failed and rollback was incomplete") from commit_error
            raise

        cleanup_failed = False
        for index, action, parent_fd, _name in moved:
            try:
                _remove_at(
                    transaction_fd,
                    f"backup-{index}",
                    directory=action.action == "delete-tree",
                )
            except (OSError, SanitizationError):
                cleanup_failed = True
            finally:
                os.close(parent_fd)
        os.fsync(transaction_fd)
        if cleanup_failed:
            retain_transaction = True
            raise SanitizationError("sanitization committed but secure backup cleanup failed")

        output_publications: list[tuple[str, Identity]] = []
        try:
            for output_name in sorted(OUTPUT_NAMES):
                staged = f"output-{output_name}"
                os.link(
                    staged,
                    output_name,
                    src_dir_fd=transaction_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
                os.unlink(staged, dir_fd=transaction_fd)
                output_publications.append((output_name, _identity_at(root_fd, output_name)))
            os.fsync(root_fd)
        except OSError as publish_error:
            for output_name, expected in reversed(output_publications):
                with contextlib.suppress(OSError):
                    if _identity_at(root_fd, output_name) == expected:
                        os.unlink(output_name, dir_fd=root_fd)
            with contextlib.suppress(OSError):
                os.fsync(root_fd)
            raise SanitizationError("sanitization committed but manifest publication failed") from publish_error

        try:
            _remove_tree_at(root_fd, transaction_name)
            transaction_removed = True
            os.fsync(root_fd)
        except (OSError, SanitizationError) as cleanup_error:
            for output_name, expected in reversed(output_publications):
                with contextlib.suppress(OSError):
                    if _identity_at(root_fd, output_name) == expected:
                        os.unlink(output_name, dir_fd=root_fd)
            with contextlib.suppress(OSError):
                os.fsync(root_fd)
            retain_transaction = True
            raise SanitizationError("sanitization committed but transaction cleanup failed") from cleanup_error
    finally:
        for _index, _action, parent_fd, _name in moved:
            with contextlib.suppress(OSError):
                os.close(parent_fd)
        os.close(transaction_fd)
        if not retain_transaction and not transaction_removed:
            with contextlib.suppress(OSError, SanitizationError):
                _remove_tree_at(root_fd, transaction_name)


def _summary(actions: list[PlannedAction], apply: bool, status: str) -> dict[str, object]:
    return {
        "actionCount": len(actions),
        "apply": apply,
        "fileCount": sum(len(action.records) for action in actions),
        "status": status,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--remediation-spec", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="apply the preflighted remediation")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root_fd: int | None = None
    try:
        document = _safe_json(args.remediation_spec)
        entries = _parse_entries(document)
        root_fd = _root_fd(args.artifact_root)
        actions = _preflight(root_fd, entries)
        if not args.apply:
            print(json.dumps(_summary(actions, False, "dry-run"), sort_keys=True, separators=(",", ":")))
            return 0
        try:
            _transaction(root_fd, actions)
        except SanitizationError as error:
            print(f"sanitizer commit error: {error}", file=sys.stderr)
            return 4 if "committed" in str(error) or "rollback was incomplete" in str(error) else 3
        except OSError:
            print("sanitizer commit error: filesystem operation failed", file=sys.stderr)
            return 3
        print(json.dumps(_summary(actions, True, "sanitized"), sort_keys=True, separators=(",", ":")))
        return 0
    except SanitizationError as error:
        print(f"sanitizer preflight error: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("sanitizer preflight error: filesystem operation failed", file=sys.stderr)
        return 2
    finally:
        if root_fd is not None:
            os.close(root_fd)


if __name__ == "__main__":
    raise SystemExit(run())
