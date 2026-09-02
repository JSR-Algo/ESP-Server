"""Execute structured Google Live evidence commands with redacted provenance."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from scripts.google_live_deterministic_evidence import (
    _junit_value_is_sensitive,
    read_bound_file,
    require_file_unchanged,
)
from scripts.google_live_reliability import forbidden_report_fields


COMMAND_PROVENANCE_SCHEMA = "google-live-command-provenance.v1"
COMMAND_ID = re.compile(r"(?:diagnostic\.)?[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
_EXACT_ENTRY_FIELDS = {
    "argv",
    "candidateIdentity",
    "commandId",
    "cwd",
    "endedAtUtc",
    "environmentSources",
    "exitCode",
    "inputs",
    "outputs",
    "schemaVersion",
    "secretSources",
    "specSha256",
    "startedAtUtc",
    "stdinSource",
    "terminalPolicy",
}
_EXACT_TERMINAL_FIELDS = {
    "classification",
    "cleanupGraceSec",
    "expectedExitCodes",
    "satisfied",
    "timeoutSec",
}


def _immutable_identity(value: Mapping[str, str] | None) -> Mapping[str, str]:
    copied = dict(value or {})
    if copied:
        if set(copied) != {
            "gitSha",
            "imageDigest",
            "firmwareIdentity",
            "configFingerprint",
            "fixtureSha256",
        }:
            raise ValueError("candidate identity is invalid")
        if any(type(item) is not str or not item for item in copied.values()):
            raise ValueError("candidate identity is invalid")
        if re.fullmatch(r"[0-9a-f]{40}", copied["gitSha"]) is None:
            raise ValueError("candidate identity is invalid")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", copied["imageDigest"]) is None:
            raise ValueError("candidate identity is invalid")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", copied["configFingerprint"]) is None:
            raise ValueError("candidate identity is invalid")
        if SHA256.fullmatch(copied["fixtureSha256"]) is None:
            raise ValueError("candidate identity is invalid")
    return MappingProxyType(copied)


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    argv: tuple[str, ...]
    cwd: Path | None = None
    candidate_identity: Mapping[str, str] = field(default_factory=dict)
    env_allowlist: tuple[str, ...] = ()
    secret_env: tuple[str, ...] = ()
    inputs: tuple[Path, ...] = ()
    outputs: tuple[Path, ...] = ()
    expected_exit_codes: tuple[int, ...] = (0,)
    timeout_sec: float = 300.0
    cleanup_grace_sec: float = 2.0
    stdin_source: str | None = None

    def __post_init__(self) -> None:
        if type(self.command_id) is not str or COMMAND_ID.fullmatch(self.command_id) is None:
            raise ValueError("command ID is invalid")
        if type(self.argv) is not tuple or not self.argv:
            raise TypeError("argv must be a non-empty tuple")
        if any(type(item) is not str or not item or "\x00" in item for item in self.argv):
            raise ValueError("argv contains an invalid argument")
        if any(_unsafe_recorded_value(item) for item in self.argv):
            raise ValueError("argv violates the privacy contract")
        for name, values in (
            ("environment allowlist", self.env_allowlist),
            ("secret environment", self.secret_env),
        ):
            if type(values) is not tuple or len(values) != len(set(values)):
                raise TypeError(f"{name} must be a unique tuple")
            if any(type(item) is not str or ENV_NAME.fullmatch(item) is None for item in values):
                raise ValueError(f"{name} contains an invalid name")
        if set(self.env_allowlist) & set(self.secret_env):
            raise ValueError("public and secret environment sources overlap")
        for name, paths in (("inputs", self.inputs), ("outputs", self.outputs)):
            if type(paths) is not tuple or any(not isinstance(path, Path) for path in paths):
                raise TypeError(f"{name} must be a tuple of Paths")
            if len(paths) != len(set(paths)):
                raise ValueError(f"{name} contains duplicate paths")
        if type(self.expected_exit_codes) is not tuple or not self.expected_exit_codes:
            raise TypeError("expected exit codes must be a non-empty tuple")
        if len(self.expected_exit_codes) != len(set(self.expected_exit_codes)) or any(
            type(code) is not int or code < 0 or code > 255 for code in self.expected_exit_codes
        ):
            raise ValueError("expected exit policy is invalid")
        if type(self.timeout_sec) not in {int, float} or self.timeout_sec <= 0:
            raise ValueError("timeout must be positive")
        if type(self.cleanup_grace_sec) not in {int, float} or self.cleanup_grace_sec < 0:
            raise ValueError("cleanup grace must be non-negative")
        if self.stdin_source not in {None, "protected_transcript_plan"}:
            raise ValueError("stdin source is invalid")
        cwd = Path.cwd() if self.cwd is None else self.cwd
        if not isinstance(cwd, Path) or not cwd.is_absolute():
            raise ValueError("cwd must be an absolute Path")
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "candidate_identity", _immutable_identity(self.candidate_identity))


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    exit_code: int | None
    classification: str
    policy_satisfied: bool
    spec_digest: str
    output_hashes: Mapping[str, str]


def _unsafe_recorded_value(value: str) -> bool:
    if value.startswith("<env:") or value.startswith("<stdin:"):
        return False
    return _junit_value_is_sensitive(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _absolute_clean(path: Path) -> Path:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("evidence path must be absolute and normalized")
    return path


def _label(root: Path, path: Path) -> str:
    path = _absolute_clean(path)
    if not _inside(root, path):
        raise ValueError("evidence path is outside the evidence root")
    relative = path.relative_to(root).as_posix()
    if not relative or _unsafe_recorded_value(relative):
        raise ValueError("evidence label violates the privacy contract")
    return relative


def _same_file(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _directory_chain(path: Path) -> tuple[tuple[int, int], ...]:
    current = Path(path.anchor)
    result = []
    for component in path.parts[1:]:
        current = current / component
        opened = os.stat(current, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode):
            raise RuntimeError("evidence parent is invalid")
        result.append((opened.st_dev, opened.st_ino))
    return tuple(result)


def _prepare_paths(
    spec: CommandSpec, root: Path
) -> tuple[
    dict[Path, tuple[Any, tuple[tuple[int, int], ...]]],
    dict[Path, tuple[tuple[int, int], ...]],
]:
    all_paths = (*spec.inputs, *spec.outputs)
    for path in all_paths:
        _label(root, path)
    for index, left in enumerate(all_paths):
        for right in all_paths[index + 1 :]:
            if _same_file(left, right):
                raise ValueError("evidence paths alias")
    inputs = {}
    for path in spec.inputs:
        bound = read_bound_file(path)
        if not stat.S_ISREG(bound.mode) or bound.links != 1:
            raise RuntimeError("evidence input alias detected")
        inputs[path] = (bound, _directory_chain(path.parent))
    parents = {}
    for output in spec.outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() or output.is_symlink():
            raise ValueError("evidence output already exists")
        parents[output] = _directory_chain(output.parent)
    return inputs, parents


def _canonical_spec(spec: CommandSpec, root: Path) -> dict[str, Any]:
    recorded_argv = []
    for index, argument in enumerate(spec.argv):
        candidate = Path(argument)
        if candidate.is_absolute():
            if index == 0:
                recorded_argv.append(argument)
                continue
            if not _inside(root, candidate):
                raise ValueError("absolute command argument is outside the evidence root")
            recorded_argv.append(f"<evidence:{_label(root, candidate)}>")
        else:
            recorded_argv.append(argument)
    return {
        "argv": recorded_argv,
        "commandId": spec.command_id,
        "cwd": "." if spec.cwd == root else _label(root, spec.cwd),
        "environmentSources": [f"<env:{name}>" for name in spec.env_allowlist],
        "expectedExitCodes": list(spec.expected_exit_codes),
        "inputs": [_label(root, path) for path in spec.inputs],
        "outputs": [_label(root, path) for path in spec.outputs],
        "secretSources": [f"<env:{name}>" for name in spec.secret_env],
        "stdinSource": (
            f"<stdin:{spec.stdin_source}>" if spec.stdin_source is not None else None
        ),
        "timeoutSec": float(spec.timeout_sec),
        "cleanupGraceSec": float(spec.cleanup_grace_sec),
    }


def _digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _spec_digest(spec: CommandSpec) -> str:
    return _digest(
        {
            "argv": list(spec.argv),
            "commandId": spec.command_id,
            "cwd": str(spec.cwd),
            "environmentNames": list(spec.env_allowlist),
            "expectedExitCodes": list(spec.expected_exit_codes),
            "inputs": [str(path) for path in spec.inputs],
            "outputs": [str(path) for path in spec.outputs],
            "secretEnvironmentNames": list(spec.secret_env),
            "stdinSource": spec.stdin_source,
            "timeoutSec": float(spec.timeout_sec),
            "cleanupGraceSec": float(spec.cleanup_grace_sec),
        }
    )


def _child_environment(spec: CommandSpec, source: Mapping[str, str] | None) -> dict[str, str]:
    supplied = os.environ if source is None else source
    declared = set(spec.env_allowlist) | set(spec.secret_env)
    if source is not None and set(source) - declared:
        raise ValueError("environment contains a non-allowlisted source")
    result = {}
    for name in (*spec.env_allowlist, *spec.secret_env):
        value = supplied.get(name)
        if type(value) is not str or "\x00" in value:
            raise ValueError("declared environment source is missing or invalid")
        result[name] = value
    return result


def _terminate_group(process: subprocess.Popen[bytes], pgid: int, grace: float) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            break
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        process.communicate()
        return
    os.killpg(pgid, signal.SIGKILL)
    process.communicate()


def _run_process(
    spec: CommandSpec,
    child_env: Mapping[str, str],
    stdin_bytes: bytes | None,
    cancel_event: threading.Event | None,
) -> tuple[int | None, str, bool]:
    try:
        process = subprocess.Popen(
            list(spec.argv),
            shell=False,
            cwd=spec.cwd,
            env=dict(child_env),
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError("spawn_failed") from exc
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        process.communicate()
        pgid = process.pid
    started = time.monotonic()
    pending_input = stdin_bytes
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_group(process, pgid, float(spec.cleanup_grace_sec))
                return process.returncode, "cancelled", False
            remaining = float(spec.timeout_sec) - (time.monotonic() - started)
            if remaining <= 0:
                _terminate_group(process, pgid, float(spec.cleanup_grace_sec))
                return process.returncode, "timeout", False
            try:
                process.communicate(input=pending_input, timeout=min(0.05, remaining))
                code = process.returncode
                satisfied = code in spec.expected_exit_codes
                return code, "expected_exit" if satisfied else "unexpected_exit", satisfied
            except subprocess.TimeoutExpired:
                pending_input = None
    except KeyboardInterrupt:
        _terminate_group(process, pgid, float(spec.cleanup_grace_sec))
        raise
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        _terminate_group(process, pgid, float(spec.cleanup_grace_sec))
        raise RuntimeError("communicate_failed") from exc


def _artifact_rows(root: Path, paths: tuple[Path, ...]) -> list[dict[str, str]]:
    result = []
    for path in paths:
        try:
            first = read_bound_file(path)
        except OSError as exc:
            raise RuntimeError("required evidence output is missing") from exc
        if not stat.S_ISREG(first.mode) or first.links != 1:
            raise RuntimeError("evidence output alias detected")
        require_file_unchanged(path, first)
        result.append({"label": _label(root, path), "sha256": hashlib.sha256(first.content).hexdigest()})
    return result


def _parse_time(value: Any) -> None:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("provenance timestamp is invalid")
    datetime.fromisoformat(value[:-1] + "+00:00")


def validate_provenance_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError("provenance is invalid")
    seen_ids = set()
    seen_digests = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _EXACT_ENTRY_FIELDS:
            raise ValueError("provenance schema is invalid")
        if entry.get("schemaVersion") != COMMAND_PROVENANCE_SCHEMA:
            raise ValueError("provenance schema is invalid")
        command_id = entry.get("commandId")
        digest = entry.get("specSha256")
        if type(command_id) is not str or COMMAND_ID.fullmatch(command_id) is None:
            raise ValueError("provenance command ID is invalid")
        if command_id in seen_ids or type(digest) is not str or SHA256.fullmatch(digest) is None:
            raise ValueError("provenance duplicate or digest is invalid")
        if digest in seen_digests:
            raise ValueError("provenance spec digest is not unique")
        seen_ids.add(command_id)
        seen_digests.add(digest)
        _parse_time(entry.get("startedAtUtc"))
        _parse_time(entry.get("endedAtUtc"))
        if entry["startedAtUtc"] > entry["endedAtUtc"]:
            raise ValueError("provenance order is invalid")
        terminal = entry.get("terminalPolicy")
        if not isinstance(terminal, dict) or set(terminal) != _EXACT_TERMINAL_FIELDS:
            raise ValueError("provenance terminal policy is invalid")
        expected_codes = terminal.get("expectedExitCodes")
        classification = terminal.get("classification")
        exit_code = entry.get("exitCode")
        if (
            type(terminal.get("satisfied")) is not bool
            or not isinstance(expected_codes, list)
            or not expected_codes
            or len(expected_codes) != len(set(expected_codes))
            or any(type(code) is not int or code < 0 or code > 255 for code in expected_codes)
            or type(terminal.get("timeoutSec")) not in {int, float}
            or terminal["timeoutSec"] <= 0
            or type(terminal.get("cleanupGraceSec")) not in {int, float}
            or terminal["cleanupGraceSec"] < 0
            or classification not in {
                "expected_exit",
                "unexpected_exit",
                "timeout",
                "cancelled",
                "keyboard_interrupt",
            }
            or (exit_code is not None and type(exit_code) is not int)
        ):
            raise ValueError("provenance terminal policy is invalid")
        if classification == "expected_exit" and not (
            terminal["satisfied"] is True and exit_code in expected_codes
        ):
            raise ValueError("provenance terminal policy is invalid")
        if classification == "unexpected_exit" and not (
            terminal["satisfied"] is False and type(exit_code) is int and exit_code not in expected_codes
        ):
            raise ValueError("provenance terminal policy is invalid")
        if classification in {"timeout", "cancelled", "keyboard_interrupt"} and terminal["satisfied"] is not False:
            raise ValueError("provenance terminal policy is invalid")
        argv = entry.get("argv")
        if not isinstance(argv, list) or not argv or any(type(item) is not str or not item for item in argv):
            raise ValueError("provenance argv is invalid")
        if type(entry.get("cwd")) is not str or not entry["cwd"]:
            raise ValueError("provenance cwd is invalid")
        for field, pattern in (
            ("environmentSources", re.compile(r"<env:[A-Z][A-Z0-9_]*>")),
            ("secretSources", re.compile(r"<env:[A-Z][A-Z0-9_]*>")),
        ):
            sources = entry.get(field)
            if (
                not isinstance(sources, list)
                or len(sources) != len(set(sources))
                or any(type(item) is not str or pattern.fullmatch(item) is None for item in sources)
            ):
                raise ValueError("provenance source is invalid")
        if set(entry["environmentSources"]) & set(entry["secretSources"]):
            raise ValueError("provenance source is invalid")
        if entry.get("stdinSource") not in {None, "<stdin:protected_transcript_plan>"}:
            raise ValueError("provenance stdin source is invalid")
        for field in ("inputs", "outputs"):
            artifacts = entry.get(field)
            if not isinstance(artifacts, list):
                raise ValueError("provenance artifact is invalid")
            labels = []
            for artifact in artifacts:
                if (
                    not isinstance(artifact, dict)
                    or set(artifact) != {"label", "sha256"}
                    or type(artifact.get("label")) is not str
                    or not artifact["label"]
                    or artifact["label"].startswith("/")
                    or any(part in {"", ".", ".."} for part in artifact["label"].split("/"))
                    or type(artifact.get("sha256")) is not str
                    or SHA256.fullmatch(artifact["sha256"]) is None
                ):
                    raise ValueError("provenance artifact is invalid")
                labels.append(artifact["label"])
            if len(labels) != len(set(labels)):
                raise ValueError("provenance artifact is invalid")
        if forbidden_report_fields(entry):
            raise ValueError("provenance privacy contract is invalid")
        rendered = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        scrubbed = re.sub(r"<env:[A-Z][A-Z0-9_]*>|<stdin:protected_transcript_plan>", "<source>", rendered)
        if _junit_value_is_sensitive(scrubbed):
            raise ValueError("provenance privacy contract is invalid")
    return entries


def parse_provenance(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("provenance is invalid") from exc
    if not text:
        return []
    if not text.endswith("\n") or "\r" in text:
        raise ValueError("provenance is invalid")
    entries = []
    for line in text[:-1].split("\n"):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("provenance is invalid") from exc
        if json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) != line:
            raise ValueError("provenance is not canonical")
        entries.append(entry)
    return validate_provenance_entries(entries)


def render_provenance(entries: list[dict[str, Any]]) -> bytes:
    validate_provenance_entries(entries)
    return ("".join(json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n" for item in entries)).encode()


def render_commands_projection(entries: list[dict[str, Any]]) -> bytes:
    validate_provenance_entries(entries)
    lines = []
    for entry in entries:
        argv = " ".join(json.dumps(item, ensure_ascii=True) for item in entry["argv"])
        sources = [*entry["environmentSources"], *entry["secretSources"]]
        if entry["stdinSource"] is not None:
            sources.append(entry["stdinSource"])
        suffix = " " + " ".join(sources) if sources else ""
        lines.append(f"{entry['commandId']} [{entry['specSha256']}] {argv}{suffix}\n")
    return "".join(lines).encode()


def _safe_existing(path: Path) -> bytes | None:
    try:
        bound = read_bound_file(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(bound.mode) or bound.links != 1:
        raise RuntimeError("provenance artifact alias detected")
    return bound.content


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    name = f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor = None
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError("provenance write failed")
            remaining = remaining[count:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeError("provenance temporary file is invalid")
        os.replace(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        name = ""
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if name:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def _commit_entry(provenance: Path, entry: dict[str, Any]) -> None:
    projection = provenance.with_suffix(".txt")
    if projection == provenance:
        raise ValueError("provenance projection path aliases JSONL")
    provenance.parent.mkdir(parents=True, exist_ok=True)
    lock_path = provenance.with_name(f".{provenance.name}.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        opened_lock = os.fstat(lock_fd)
        if not stat.S_ISREG(opened_lock.st_mode) or opened_lock.st_nlink != 1:
            raise RuntimeError("provenance lock is invalid")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        old_jsonl = _safe_existing(provenance)
        old_projection = _safe_existing(projection)
        entries = parse_provenance(old_jsonl or b"")
        expected_old_projection = render_commands_projection(entries)
        if (old_jsonl is None) != (old_projection is None) or (
            old_projection is not None and old_projection != expected_old_projection
        ):
            raise ValueError("provenance projection is inconsistent")
        if any(item["commandId"] == entry["commandId"] for item in entries):
            raise ValueError("duplicate command ID")
        next_entries = [*entries, entry]
        next_jsonl = render_provenance(next_entries)
        next_projection = render_commands_projection(next_entries)
        try:
            _atomic_replace(provenance, next_jsonl)
            _atomic_replace(projection, next_projection)
            if _safe_existing(provenance) != next_jsonl or _safe_existing(projection) != next_projection:
                raise RuntimeError("provenance projection is inconsistent")
        except BaseException:
            if old_jsonl is None:
                provenance.unlink(missing_ok=True)
            else:
                _atomic_replace(provenance, old_jsonl)
            if old_projection is None:
                projection.unlink(missing_ok=True)
            else:
                _atomic_replace(projection, old_projection)
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _preflight_provenance(provenance: Path, command_id: str) -> None:
    projection = provenance.with_suffix(".txt")
    provenance.parent.mkdir(parents=True, exist_ok=True)
    lock_path = provenance.with_name(f".{provenance.name}.lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        old_jsonl = _safe_existing(provenance)
        old_projection = _safe_existing(projection)
        entries = parse_provenance(old_jsonl or b"")
        if (old_jsonl is None) != (old_projection is None) or (
            old_projection is not None and old_projection != render_commands_projection(entries)
        ):
            raise ValueError("provenance projection is inconsistent")
        if any(item["commandId"] == command_id for item in entries):
            raise ValueError("duplicate command ID")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def execute_and_record(
    spec: CommandSpec,
    *,
    provenance: Path,
    env: Mapping[str, str] | None = None,
    stdin_bytes: bytes | None = None,
    cancel_event: threading.Event | None = None,
) -> CommandResult:
    if type(spec) is not CommandSpec:
        raise TypeError("spec must be an immutable CommandSpec")
    if not isinstance(provenance, Path) or not provenance.is_absolute():
        raise ValueError("provenance path must be absolute")
    root = provenance.parent
    control_paths = {
        provenance,
        provenance.with_suffix(".txt"),
        provenance.with_name(f".{provenance.name}.lock"),
    }
    if any(path in control_paths or any(_same_file(path, item) for item in control_paths) for path in (*spec.inputs, *spec.outputs)):
        raise ValueError("command artifacts alias provenance control files")
    if spec.cwd != root and not _inside(root, spec.cwd):
        raise ValueError("cwd must be the evidence root or a descendant")
    if (stdin_bytes is None) != (spec.stdin_source is None):
        raise ValueError("protected stdin bytes and source must be supplied together")
    if stdin_bytes is not None and type(stdin_bytes) is not bytes:
        raise TypeError("protected stdin must be bytes")
    _preflight_provenance(provenance, spec.command_id)
    inputs, output_parents = _prepare_paths(spec, root)
    canonical_spec = _canonical_spec(spec, root)
    spec_digest = _spec_digest(spec)
    child_env = _child_environment(spec, env)
    started = _utc_now()
    interrupted = False
    try:
        exit_code, classification, satisfied = _run_process(
            spec, child_env, stdin_bytes, cancel_event
        )
    except KeyboardInterrupt:
        exit_code, classification, satisfied = None, "keyboard_interrupt", False
        interrupted = True
    ended = _utc_now()
    for path, (bound, parent_chain) in inputs.items():
        if _directory_chain(path.parent) != parent_chain:
            raise RuntimeError("evidence input parent changed")
        require_file_unchanged(path, bound)
    for path, chain in output_parents.items():
        if _directory_chain(path.parent) != chain:
            raise RuntimeError("evidence output parent changed")
    if not satisfied:
        for path, chain in output_parents.items():
            if _directory_chain(path.parent) != chain:
                continue
            try:
                opened = os.stat(path, follow_symlinks=False)
                if stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1:
                    path.unlink()
            except FileNotFoundError:
                pass
    output_rows = _artifact_rows(root, spec.outputs) if satisfied else []
    input_rows = [
        {"label": _label(root, path), "sha256": hashlib.sha256(bound.content).hexdigest()}
        for path, (bound, _parent_chain) in inputs.items()
    ]
    entry = {
        "argv": canonical_spec["argv"],
        "candidateIdentity": dict(spec.candidate_identity),
        "commandId": spec.command_id,
        "cwd": canonical_spec["cwd"],
        "endedAtUtc": ended,
        "environmentSources": canonical_spec["environmentSources"],
        "exitCode": exit_code,
        "inputs": input_rows,
        "outputs": output_rows,
        "schemaVersion": COMMAND_PROVENANCE_SCHEMA,
        "secretSources": canonical_spec["secretSources"],
        "specSha256": spec_digest,
        "startedAtUtc": started,
        "stdinSource": canonical_spec["stdinSource"],
        "terminalPolicy": {
            "classification": classification,
            "cleanupGraceSec": float(spec.cleanup_grace_sec),
            "expectedExitCodes": list(spec.expected_exit_codes),
            "satisfied": satisfied,
            "timeoutSec": float(spec.timeout_sec),
        },
    }
    _commit_entry(provenance, entry)
    result = CommandResult(
        spec.command_id,
        exit_code,
        classification,
        satisfied,
        spec_digest,
        MappingProxyType({item["label"]: item["sha256"] for item in output_rows}),
    )
    if interrupted:
        raise KeyboardInterrupt
    return result
