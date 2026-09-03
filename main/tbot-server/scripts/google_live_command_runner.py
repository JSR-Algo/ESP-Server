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
import sys
import threading
import time
import unicodedata
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
_MAX_PROVENANCE_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_GENERATION_FILES = 1024
PAIR_SCHEMA = "google-live-command-provenance-pair.v1"
GENERATION_ID = re.compile(r"[0-9a-f]{32}")
GENERATION_FILE = re.compile(r"([0-9a-f]{32})\.(?:jsonl|txt)")
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
_FCHDIR_EXEC = """import os, sys
cwd = int(sys.argv[1])
keep = [int(value) for value in sys.argv[2].split(",") if value]
argv = sys.argv[3:]
try:
    os.fchdir(cwd)
    os.close(cwd)
    cwd = -1
    os.execve(argv[0], argv, os.environ)
except BaseException:
    for descriptor in [cwd, *keep]:
        try:
            os.close(descriptor)
        except OSError:
            pass
    os._exit(126)
"""


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
        if not Path(self.argv[0]).is_absolute():
            raise ValueError("command executable must be absolute")
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


@dataclass(frozen=True)
class BoundWorkingDirectory:
    root: Path
    path: Path
    descriptor: int
    chain: tuple[tuple[int, int, int, int, int, int], ...]


@dataclass(frozen=True)
class PublishedArtifact:
    device: int
    inode: int
    digest: str


@dataclass(frozen=True)
class ProvenanceFileSnapshot:
    content: bytes
    identity: tuple[int, ...]
    version: PublishedArtifact


@dataclass(frozen=True)
class ProvenancePairSnapshot:
    jsonl: ProvenanceFileSnapshot | None
    projection: ProvenanceFileSnapshot | None


@dataclass(frozen=True)
class PointerFileSnapshot:
    content: bytes
    version: PublishedArtifact
    identity: tuple[int, ...]


@dataclass(frozen=True)
class PairPointer:
    generation: str
    jsonl_sha256: str
    projection_sha256: str
    entry_count: int
    spec_digest_sha256: str


@dataclass(frozen=True)
class CommittedPairSnapshot:
    pointer: PairPointer | None
    pointer_content: bytes | None
    pointer_version: PublishedArtifact | None
    jsonl: bytes | None
    projection: bytes | None


@dataclass(frozen=True)
class BoundArgumentArtifacts:
    argv: tuple[str, ...]
    descriptors: tuple[int, ...]
    identities: Mapping[Path, tuple[int, int]]


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


def _component_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]+", value) is None:
        raise ValueError("evidence path component is invalid")
    return unicodedata.normalize("NFC", value).casefold()


def _reject_component_alias(directory_fd: int, component: str) -> None:
    expected_key = _component_key(component)
    for existing in os.listdir(directory_fd):
        if unicodedata.normalize("NFC", existing).casefold() == expected_key and existing != component:
            raise ValueError("evidence path component aliases an existing name")


def _component_race_hook(stage: str, parent_fd: int, component: str) -> None:
    del stage, parent_fd, component


def _require_exact_opened_component(
    parent_fd: int, component: str, child_fd: int
) -> os.stat_result:
    expected_key = _component_key(component)
    matches = [
        existing
        for existing in os.listdir(parent_fd)
        if unicodedata.normalize("NFC", existing).casefold() == expected_key
    ]
    if matches != [component]:
        raise ValueError("evidence path component aliases an existing name")
    linked = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(child_fd)
    if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
        raise RuntimeError("evidence path component changed")
    return opened


def _secure_materialize_directory(root: Path, path: Path) -> BoundWorkingDirectory:
    _absolute_clean(root)
    _absolute_clean(path)
    if path != root and not _inside(root, path):
        raise ValueError("evidence directory is outside the evidence root")
    bound_root = _open_bound_working_directory(root, root)
    descriptor = bound_root.descriptor
    chain = list(bound_root.chain)
    try:
        for component in path.relative_to(root).parts:
            _reject_component_alias(descriptor, component)
            _component_race_hook("after_scan", descriptor, component)
            try:
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    _component_race_hook("after_eexist", descriptor, component)
                next_descriptor = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            _component_race_hook("after_open", descriptor, component)
            try:
                opened = _require_exact_opened_component(
                    descriptor, component, next_descriptor
                )
            except BaseException:
                os.close(next_descriptor)
                raise
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink < 1
            ):
                os.close(next_descriptor)
                raise RuntimeError("evidence directory is invalid")
            _component_race_hook("before_return", descriptor, component)
            try:
                opened = _require_exact_opened_component(
                    descriptor, component, next_descriptor
                )
            except BaseException:
                os.close(next_descriptor)
                raise
            _component_race_hook("before_advance", descriptor, component)
            try:
                opened = _require_exact_opened_component(
                    descriptor, component, next_descriptor
                )
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
            chain.append(
                (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mode,
                    opened.st_ctime_ns,
                    opened.st_uid,
                    opened.st_nlink,
                )
            )
        return BoundWorkingDirectory(root, path, descriptor, tuple(chain))
    except BaseException:
        os.close(descriptor)
        raise


def _open_bound_working_directory(root: Path, cwd: Path) -> BoundWorkingDirectory:
    _absolute_clean(root)
    _absolute_clean(cwd)
    if cwd != root and not _inside(root, cwd):
        raise ValueError("cwd must be the evidence root or a descendant")
    if cwd.parts[: len(root.parts)] != root.parts:
        raise ValueError("cwd must be contained by the evidence root")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(cwd.anchor, flags)
    chain = []
    try:
        opened = os.fstat(descriptor)
        chain.append(
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_ctime_ns,
                opened.st_uid,
                opened.st_nlink,
            )
        )
        for component in cwd.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                raise RuntimeError("cwd component is not a directory")
            chain.append(
                (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mode,
                    opened.st_ctime_ns,
                    opened.st_uid,
                    opened.st_nlink,
                )
            )
        root_index = len(root.parts) - 1
        if root_index >= len(chain):
            raise RuntimeError("cwd is outside the evidence root")
        return BoundWorkingDirectory(root, cwd, descriptor, tuple(chain[root_index:]))
    except BaseException:
        os.close(descriptor)
        raise


def _require_working_directory_unchanged(
    bound: BoundWorkingDirectory, *, require_ctime: bool
) -> None:
    try:
        current = _open_bound_working_directory(bound.root, bound.path)
    except OSError as exc:
        raise RuntimeError("cwd changed") from exc
    try:
        observed = (
            current.chain
            if require_ctime
            else tuple((item[0], item[1], item[2], item[4]) for item in current.chain)
        )
        expected = (
            bound.chain
            if require_ctime
            else tuple((item[0], item[1], item[2], item[4]) for item in bound.chain)
        )
        if observed != expected:
            raise RuntimeError("cwd changed")
    finally:
        os.close(current.descriptor)


def _prepare_paths(
    spec: CommandSpec, root: Path
) -> tuple[
    dict[Path, tuple[Any, tuple[tuple[int, int], ...]]],
    dict[Path, tuple[tuple[int, int, int, int, int, int], ...]],
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
        parent = _secure_materialize_directory(root, output.parent)
        try:
            try:
                os.stat(output.name, dir_fd=parent.descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("evidence output already exists")
            parents[output] = parent.chain
        finally:
            os.close(parent.descriptor)
    return inputs, parents


def _bind_argument_artifacts(
    spec: CommandSpec,
    root: Path,
    inputs: Mapping[Path, tuple[Any, tuple[tuple[int, int], ...]]],
) -> BoundArgumentArtifacts:
    declared = {*spec.inputs, *spec.outputs}
    descriptors: dict[Path, int] = {}
    argv = []
    try:
        for index, argument in enumerate(spec.argv):
            candidate = Path(argument)
            if index == 0 or not candidate.is_absolute():
                argv.append(argument)
                continue
            if candidate not in declared:
                raise ValueError("absolute command argument must be an exact declared artifact")
            if candidate in descriptors:
                argv.append(f"/dev/fd/{descriptors[candidate]}")
                continue
            parent = _open_bound_working_directory(root, candidate.parent)
            try:
                if candidate in inputs:
                    descriptor = os.open(
                        candidate.name,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent.descriptor,
                    )
                    opened = os.fstat(descriptor)
                    bound = inputs[candidate][0]
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                        != (bound.device, bound.inode, bound.size, bound.modified_ns)
                    ):
                        os.close(descriptor)
                        raise RuntimeError("absolute input argument changed")
                else:
                    descriptor = os.open(
                        candidate.name,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent.descriptor,
                    )
                descriptors[candidate] = descriptor
                argv.append(f"/dev/fd/{descriptor}")
            finally:
                os.close(parent.descriptor)
        return BoundArgumentArtifacts(
            tuple(argv),
            tuple(descriptors.values()),
            MappingProxyType(
                {
                    path: (opened.st_dev, opened.st_ino)
                    for path, descriptor in descriptors.items()
                    for opened in (os.fstat(descriptor),)
                }
            ),
        )
    except BaseException:
        for descriptor in descriptors.values():
            os.close(descriptor)
        raise


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


def _validate_executable(path: str) -> None:
    try:
        opened = os.stat(path)
    except OSError as exc:
        raise RuntimeError("spawn_failed") from exc
    if not stat.S_ISREG(opened.st_mode) or not os.access(path, os.X_OK):
        raise RuntimeError("spawn_failed")


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
    cwd_descriptor: int,
    argument_artifacts: BoundArgumentArtifacts,
) -> tuple[int | None, str, bool]:
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _FCHDIR_EXEC,
                str(cwd_descriptor),
                ",".join(str(value) for value in argument_artifacts.descriptors),
                *argument_artifacts.argv,
            ],
            shell=False,
            cwd=None,
            env=dict(child_env),
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(cwd_descriptor, *argument_artifacts.descriptors),
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


def _cleanup_declared_outputs(
    root: Path,
    outputs: tuple[Path, ...],
    parents: Mapping[Path, tuple[tuple[int, int, int, int, int, int], ...]],
) -> None:
    for path in outputs:
        try:
            current = _open_bound_working_directory(root, path.parent)
            try:
                if tuple((item[0], item[1]) for item in current.chain) != tuple(
                    (item[0], item[1]) for item in parents[path]
                ):
                    continue
            finally:
                os.close(current.descriptor)
            opened = os.stat(path, follow_symlinks=False)
            if stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1:
                path.unlink()
        except (FileNotFoundError, OSError, RuntimeError):
            continue


def _require_argument_artifacts_unchanged(bound: BoundArgumentArtifacts) -> None:
    for path, identity in bound.identities.items():
        try:
            opened = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError("absolute argument artifact changed") from exc
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != identity:
            raise RuntimeError("absolute argument artifact changed")


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


def _spec_digest_summary(entries: list[dict[str, Any]]) -> str:
    joined = "\n".join(entry["specSha256"] for entry in entries).encode()
    return hashlib.sha256(joined).hexdigest()


def _render_pair_pointer(pointer: PairPointer) -> bytes:
    if (
        type(pointer.generation) is not str
        or GENERATION_ID.fullmatch(pointer.generation) is None
        or type(pointer.jsonl_sha256) is not str
        or SHA256.fullmatch(pointer.jsonl_sha256) is None
        or type(pointer.projection_sha256) is not str
        or SHA256.fullmatch(pointer.projection_sha256) is None
        or type(pointer.entry_count) is not int
        or pointer.entry_count < 0
        or type(pointer.spec_digest_sha256) is not str
        or SHA256.fullmatch(pointer.spec_digest_sha256) is None
    ):
        raise ValueError("provenance pair pointer is invalid")
    value = {
        "entryCount": pointer.entry_count,
        "generation": pointer.generation,
        "jsonl": f"{pointer.generation}.jsonl",
        "jsonlSha256": pointer.jsonl_sha256,
        "projection": f"{pointer.generation}.txt",
        "projectionSha256": pointer.projection_sha256,
        "schemaVersion": PAIR_SCHEMA,
        "specDigestSha256": pointer.spec_digest_sha256,
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _parse_pair_pointer(content: bytes) -> PairPointer:
    if len(content) > 4096 or not content.endswith(b"\n"):
        raise ValueError("provenance pair pointer is invalid")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("provenance pair pointer is invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "entryCount",
        "generation",
        "jsonl",
        "jsonlSha256",
        "projection",
        "projectionSha256",
        "schemaVersion",
        "specDigestSha256",
    }:
        raise ValueError("provenance pair pointer is invalid")
    pointer = PairPointer(
        value.get("generation"),
        value.get("jsonlSha256"),
        value.get("projectionSha256"),
        value.get("entryCount"),
        value.get("specDigestSha256"),
    )
    if (
        value.get("schemaVersion") != PAIR_SCHEMA
        or value.get("jsonl") != f"{pointer.generation}.jsonl"
        or value.get("projection") != f"{pointer.generation}.txt"
        or _render_pair_pointer(pointer) != content
    ):
        raise ValueError("provenance pair pointer is invalid")
    return pointer


def _provenance_read_hook(stage: str, directory_fd: int, name: str) -> None:
    del stage, directory_fd, name


def _provenance_pair_hook(
    stage: str, directory_fd: int, jsonl_name: str, projection_name: str
) -> None:
    del stage, directory_fd, jsonl_name, projection_name


def _provenance_file_identity(opened: os.stat_result) -> tuple[int, ...]:
    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        opened.st_uid,
        opened.st_nlink,
    )


def _read_existing_version_at(
    directory_fd: int, name: str
) -> tuple[bytes, PublishedArtifact] | None:
    snapshot = _read_existing_snapshot_at(directory_fd, name)
    return None if snapshot is None else (snapshot.content, snapshot.version)


def _read_existing_snapshot_at(
    directory_fd: int, name: str
) -> PointerFileSnapshot | None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeError("provenance artifact alias detected")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        _provenance_read_hook("after_read", directory_fd, name)
        observed = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        content = b"".join(chunks)
        if (
            _provenance_file_identity(observed) != _provenance_file_identity(opened)
            or _provenance_file_identity(linked) != _provenance_file_identity(opened)
            or len(content) != opened.st_size
        ):
            raise RuntimeError("provenance artifact changed")
        return PointerFileSnapshot(
            content,
            PublishedArtifact(
                opened.st_dev,
                opened.st_ino,
                hashlib.sha256(content).hexdigest(),
            ),
            _provenance_file_identity(opened),
        )
    finally:
        os.close(descriptor)


def _open_optional_provenance_at(directory_fd: int, name: str) -> int | None:
    try:
        return os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None


def _read_open_provenance_at(
    directory_fd: int,
    name: str,
    descriptor: int,
    initial_identity: tuple[int, ...],
) -> ProvenanceFileSnapshot:
    opened = os.fstat(descriptor)
    if _provenance_file_identity(opened) != initial_identity:
        raise RuntimeError("provenance pair changed")
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size > _MAX_PROVENANCE_ARTIFACT_BYTES
    ):
        raise RuntimeError("provenance artifact alias detected")
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, _MAX_PROVENANCE_ARTIFACT_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_PROVENANCE_ARTIFACT_BYTES:
            raise RuntimeError("provenance artifact is too large")
    _provenance_read_hook("after_read", directory_fd, name)
    content = b"".join(chunks)
    identity = _provenance_file_identity(opened)
    return ProvenanceFileSnapshot(
        content,
        identity,
        PublishedArtifact(
            opened.st_dev,
            opened.st_ino,
            hashlib.sha256(content).hexdigest(),
        ),
    )


def _require_open_snapshot_current(
    directory_fd: int,
    name: str,
    descriptor: int,
    snapshot: ProvenanceFileSnapshot,
) -> None:
    observed = os.fstat(descriptor)
    try:
        linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("provenance pair changed") from exc
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, _MAX_PROVENANCE_ARTIFACT_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_PROVENANCE_ARTIFACT_BYTES:
            raise RuntimeError("provenance artifact is too large")
    if (
        _provenance_file_identity(observed) != snapshot.identity
        or _provenance_file_identity(linked) != snapshot.identity
        or b"".join(chunks) != snapshot.content
    ):
        raise RuntimeError("provenance pair changed")


def _require_absent_at(directory_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise RuntimeError("provenance pair changed")


def _read_provenance_pair_once_at(
    directory_fd: int,
    jsonl_name: str,
    projection_name: str,
    *,
    require_complete: bool,
) -> ProvenancePairSnapshot:
    jsonl_fd = _open_optional_provenance_at(directory_fd, jsonl_name)
    projection_fd = None
    try:
        _provenance_pair_hook(
            "after_jsonl_lookup", directory_fd, jsonl_name, projection_name
        )
        projection_fd = _open_optional_provenance_at(directory_fd, projection_name)
        _provenance_pair_hook(
            "after_projection_lookup", directory_fd, jsonl_name, projection_name
        )
        if require_complete and (jsonl_fd is None) != (projection_fd is None):
            raise RuntimeError("provenance pair changed")
        jsonl_identity = (
            None if jsonl_fd is None else _provenance_file_identity(os.fstat(jsonl_fd))
        )
        projection_identity = (
            None
            if projection_fd is None
            else _provenance_file_identity(os.fstat(projection_fd))
        )
        jsonl = (
            None
            if jsonl_fd is None
            else _read_open_provenance_at(
                directory_fd, jsonl_name, jsonl_fd, jsonl_identity
            )
        )
        _provenance_pair_hook(
            "after_jsonl_read", directory_fd, jsonl_name, projection_name
        )
        projection = (
            None
            if projection_fd is None
            else _read_open_provenance_at(
                directory_fd,
                projection_name,
                projection_fd,
                projection_identity,
            )
        )
        _provenance_pair_hook(
            "after_projection_read", directory_fd, jsonl_name, projection_name
        )
        if jsonl_fd is None:
            _require_absent_at(directory_fd, jsonl_name)
        else:
            _require_open_snapshot_current(
                directory_fd, jsonl_name, jsonl_fd, jsonl
            )
            _provenance_pair_hook(
                "after_jsonl_final_check",
                directory_fd,
                jsonl_name,
                projection_name,
            )
        if projection_fd is None:
            _require_absent_at(directory_fd, projection_name)
        else:
            _require_open_snapshot_current(
                directory_fd, projection_name, projection_fd, projection
            )
            _provenance_pair_hook(
                "after_projection_final_check",
                directory_fd,
                jsonl_name,
                projection_name,
            )
        if jsonl_fd is not None:
            _require_open_snapshot_current(
                directory_fd, jsonl_name, jsonl_fd, jsonl
            )
        if projection_fd is not None:
            _require_open_snapshot_current(
                directory_fd, projection_name, projection_fd, projection
            )
        return ProvenancePairSnapshot(jsonl, projection)
    finally:
        if projection_fd is not None:
            os.close(projection_fd)
        if jsonl_fd is not None:
            os.close(jsonl_fd)


def _read_provenance_pair_at(
    directory_fd: int,
    jsonl_name: str,
    projection_name: str,
    *,
    require_complete: bool = True,
) -> ProvenancePairSnapshot:
    first = _read_provenance_pair_once_at(
        directory_fd,
        jsonl_name,
        projection_name,
        require_complete=require_complete,
    )
    _provenance_pair_hook(
        "between_snapshots", directory_fd, jsonl_name, projection_name
    )
    second = _read_provenance_pair_once_at(
        directory_fd,
        jsonl_name,
        projection_name,
        require_complete=require_complete,
    )
    if first != second:
        raise RuntimeError("provenance pair changed")
    return second


def _read_existing_at(directory_fd: int, name: str) -> bytes | None:
    existing = _read_existing_version_at(directory_fd, name)
    return None if existing is None else existing[0]


def _read_pair_pointer_snapshot_at(
    directory_fd: int, name: str
) -> PointerFileSnapshot | None:
    snapshot = _read_existing_snapshot_at(directory_fd, name)
    if snapshot is None:
        return None
    mode = snapshot.identity[2]
    uid = snapshot.identity[6]
    if stat.S_IMODE(mode) != 0o600 or uid != os.geteuid():
        raise RuntimeError("provenance pair pointer is invalid")
    return snapshot


def _pair_pointer_name(jsonl_name: str) -> str:
    _component_key(jsonl_name)
    return f".{jsonl_name}.pair"


def _generation_directory_name(jsonl_name: str) -> str:
    _component_key(jsonl_name)
    return f".{jsonl_name}.generations"


def _open_generation_directory(
    directory_fd: int, jsonl_name: str, *, create: bool = True
) -> int:
    name = _generation_directory_name(jsonl_name)
    _reject_component_alias(directory_fd, name)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
        except FileExistsError:
            pass
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        opened = _require_exact_opened_component(directory_fd, name, descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_nlink < 1
        ):
            raise RuntimeError("provenance generation directory is invalid")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_generation_at(directory_fd: int, name: str, content: bytes) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o400,
        dir_fd=directory_fd,
    )
    try:
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            if count <= 0:
                raise OSError("provenance generation write failed")
            remaining = remaining[count:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_nlink != 1
            or opened.st_size != len(content)
        ):
            raise RuntimeError("provenance generation is invalid")
    finally:
        os.close(descriptor)


def _read_committed_pair_at(
    directory_fd: int,
    jsonl_name: str,
    projection_name: str,
    *,
    require_public: bool = True,
) -> CommittedPairSnapshot:
    pointer_name = _pair_pointer_name(jsonl_name)
    pointer_snapshot = _read_pair_pointer_snapshot_at(directory_fd, pointer_name)
    if pointer_snapshot is None:
        legacy = _read_provenance_pair_at(directory_fd, jsonl_name, projection_name)
        return CommittedPairSnapshot(
            None,
            None,
            None,
            None if legacy.jsonl is None else legacy.jsonl.content,
            None if legacy.projection is None else legacy.projection.content,
        )
    pointer_content = pointer_snapshot.content
    pointer_version = pointer_snapshot.version
    pointer = _parse_pair_pointer(pointer_content)
    generation_fd = _open_generation_directory(directory_fd, jsonl_name, create=False)
    try:
        generation_directory_identity = _provenance_file_identity(
            os.fstat(generation_fd)
        )
        generation_pair = _read_provenance_pair_at(
            generation_fd,
            f"{pointer.generation}.jsonl",
            f"{pointer.generation}.txt",
        )
    finally:
        os.close(generation_fd)
    if generation_pair.jsonl is None or generation_pair.projection is None:
        raise RuntimeError("provenance committed pair is invalid")
    for artifact in (generation_pair.jsonl, generation_pair.projection):
        mode = artifact.identity[2]
        uid = artifact.identity[6]
        links = artifact.identity[7]
        if (
            not stat.S_ISREG(mode)
            or stat.S_IMODE(mode) != 0o400
            or uid != os.geteuid()
            or links != 1
        ):
            raise RuntimeError("provenance committed pair is invalid")
    jsonl = generation_pair.jsonl.content
    projection = generation_pair.projection.content
    entries = parse_provenance(jsonl)
    if (
        hashlib.sha256(jsonl).hexdigest() != pointer.jsonl_sha256
        or hashlib.sha256(projection).hexdigest() != pointer.projection_sha256
        or projection != render_commands_projection(entries)
        or len(entries) != pointer.entry_count
        or _spec_digest_summary(entries) != pointer.spec_digest_sha256
    ):
        raise RuntimeError("provenance committed pair is invalid")
    pointer_epoch = os.fstat(directory_fd)
    _provenance_pair_hook(
        "before_pointer_recheck", directory_fd, jsonl_name, projection_name
    )
    rebound = _read_pair_pointer_snapshot_at(directory_fd, pointer_name)
    rebound_epoch = os.fstat(directory_fd)
    if rebound != pointer_snapshot or (
        rebound_epoch.st_dev,
        rebound_epoch.st_ino,
        rebound_epoch.st_ctime_ns,
    ) != (
        pointer_epoch.st_dev,
        pointer_epoch.st_ino,
        pointer_epoch.st_ctime_ns,
    ):
        raise RuntimeError("provenance pair pointer changed")
    public = None
    if require_public:
        public = _read_provenance_pair_at(directory_fd, jsonl_name, projection_name)
        if (
            public.jsonl is None
            or public.projection is None
            or public.jsonl.content != jsonl
            or public.projection.content != projection
        ):
            raise RuntimeError("provenance public pair is inconsistent")
    generation_fd = _open_generation_directory(directory_fd, jsonl_name, create=False)
    try:
        if (
            _provenance_file_identity(os.fstat(generation_fd))
            != generation_directory_identity
        ):
            raise RuntimeError("provenance committed pair changed")
        final_generation_pair = _read_provenance_pair_at(
            generation_fd,
            f"{pointer.generation}.jsonl",
            f"{pointer.generation}.txt",
        )
    finally:
        os.close(generation_fd)
    if final_generation_pair != generation_pair:
        raise RuntimeError("provenance committed pair changed")
    if require_public and _read_provenance_pair_at(
        directory_fd, jsonl_name, projection_name
    ) != public:
        raise RuntimeError("provenance public pair changed")
    final_pointer = _read_pair_pointer_snapshot_at(directory_fd, pointer_name)
    if final_pointer != pointer_snapshot:
        raise RuntimeError("provenance pair pointer changed")
    return CommittedPairSnapshot(
        pointer, pointer_content, pointer_version, jsonl, projection
    )


def _atomic_replace_at(
    directory_fd: int, target_name: str, content: bytes
) -> PublishedArtifact:
    name = f".{target_name}.{secrets.token_hex(12)}.tmp"
    descriptor = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
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
        published = PublishedArtifact(
            opened.st_dev,
            opened.st_ino,
            hashlib.sha256(content).hexdigest(),
        )
        os.replace(
            name,
            target_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        name = ""
        try:
            os.fsync(directory_fd)
        except BaseException as exc:
            setattr(exc, "_provenance_published", published)
            raise
        return published
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if name:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _create_generation_pair_at(
    directory_fd: int,
    jsonl_name: str,
    jsonl: bytes,
    projection: bytes,
    entries: list[dict[str, Any]],
) -> tuple[PairPointer, bytes]:
    generation = secrets.token_hex(16)
    generation_fd = _open_generation_directory(directory_fd, jsonl_name)
    try:
        _write_generation_at(generation_fd, f"{generation}.jsonl", jsonl)
        _provenance_transaction_hook("after_generation_jsonl")
        _write_generation_at(generation_fd, f"{generation}.txt", projection)
        _provenance_transaction_hook("after_generation_projection")
        os.fsync(generation_fd)
    finally:
        os.close(generation_fd)
    pointer = PairPointer(
        generation,
        hashlib.sha256(jsonl).hexdigest(),
        hashlib.sha256(projection).hexdigest(),
        len(entries),
        _spec_digest_summary(entries),
    )
    return pointer, _render_pair_pointer(pointer)


def _cleanup_generations_at(
    directory_fd: int, jsonl_name: str, keep_generations: set[str]
) -> None:
    generation_fd = _open_generation_directory(directory_fd, jsonl_name)
    try:
        names = os.listdir(generation_fd)
        if len(names) > _MAX_GENERATION_FILES:
            raise RuntimeError("provenance generation inventory is too large")
        inventory: dict[str, set[str]] = {}
        for name in names:
            match = GENERATION_FILE.fullmatch(name)
            if match is None:
                raise RuntimeError("provenance generation inventory is invalid")
            generation, suffix = name.rsplit(".", 1)
            inventory.setdefault(generation, set()).add(suffix)
        removable = []
        for generation, suffixes in inventory.items():
            if suffixes != {"jsonl", "txt"}:
                raise RuntimeError("provenance generation pair is incomplete")
            try:
                pair = _read_provenance_pair_at(
                    generation_fd, f"{generation}.jsonl", f"{generation}.txt"
                )
                if pair.jsonl is None or pair.projection is None:
                    raise RuntimeError("provenance generation pair is incomplete")
                for artifact in (pair.jsonl, pair.projection):
                    if (
                        not stat.S_ISREG(artifact.identity[2])
                        or stat.S_IMODE(artifact.identity[2]) != 0o400
                        or artifact.identity[6] != os.geteuid()
                        or artifact.identity[7] != 1
                    ):
                        raise RuntimeError("provenance generation pair is invalid")
                entries = parse_provenance(pair.jsonl.content)
                if pair.projection.content != render_commands_projection(entries):
                    raise RuntimeError("provenance generation pair is invalid")
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError("provenance generation inventory is invalid") from exc
            if generation not in keep_generations:
                removable.append(generation)
        for generation in removable:
            os.unlink(f"{generation}.jsonl", dir_fd=generation_fd)
            os.unlink(f"{generation}.txt", dir_fd=generation_fd)
        if removable:
            os.fsync(generation_fd)
    finally:
        os.close(generation_fd)


def _unlink_if_exists_at(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    os.fsync(directory_fd)


def _open_provenance_lock(directory_fd: int, name: str) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        try:
            return os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            return os.open(name, flags, dir_fd=directory_fd)


def _require_bound_lock(directory_fd: int, name: str, lock_fd: int) -> None:
    opened = os.fstat(lock_fd)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
    ):
        raise RuntimeError("provenance lock is invalid")
    try:
        linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("provenance lock changed") from exc
    if (
        linked.st_dev,
        linked.st_ino,
        linked.st_nlink,
        linked.st_uid,
        linked.st_mode,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_nlink,
        opened.st_uid,
        opened.st_mode,
    ):
        raise RuntimeError("provenance lock changed")


def _require_transaction_binding(
    parent: BoundWorkingDirectory, lock_name: str, lock_fd: int
) -> None:
    _require_bound_lock(parent.descriptor, lock_name, lock_fd)
    _require_working_directory_unchanged(parent, require_ctime=False)


def _rollback_published_at(
    directory_fd: int,
    jsonl_name: str,
    projection_name: str,
    originals: Mapping[str, bytes | None],
    expected_current: Mapping[str, PublishedArtifact | None],
    published_names: set[str],
) -> None:
    current_pair = _read_provenance_pair_at(
        directory_fd,
        jsonl_name,
        projection_name,
        require_complete=False,
    )
    current_states = {
        jsonl_name: current_pair.jsonl,
        projection_name: current_pair.projection,
    }
    already_restored = set()
    for name, expected in expected_current.items():
        current = current_states[name]
        observed = None if current is None else current.version
        if observed != expected:
            content = None if current is None else current.content
            if content != originals[name]:
                raise RuntimeError("provenance rollback conflict")
            already_restored.add(name)
    rollback_error = None
    for name in published_names:
        if name in already_restored:
            continue
        try:
            original = originals[name]
            if original is None:
                _unlink_if_exists_at(directory_fd, name)
            else:
                _atomic_replace_at(directory_fd, name, original)
        except BaseException as exc:
            if rollback_error is None:
                rollback_error = exc
    restored_pair = _read_provenance_pair_at(
        directory_fd, jsonl_name, projection_name
    )
    restored = {
        jsonl_name: None if restored_pair.jsonl is None else restored_pair.jsonl.content,
        projection_name: (
            None
            if restored_pair.projection is None
            else restored_pair.projection.content
        ),
    }
    for name, original in originals.items():
        if restored[name] != original:
            raise RuntimeError("provenance rollback failed") from rollback_error
    if rollback_error is not None:
        raise rollback_error


def _provenance_transaction_hook(stage: str) -> None:
    del stage


def _commit_entry(provenance: Path, entry: dict[str, Any]) -> None:
    projection = provenance.with_suffix(".txt")
    if projection == provenance:
        raise ValueError("provenance projection path aliases JSONL")
    lock_path = provenance.with_name(f".{provenance.name}.lock")
    parent = _open_bound_working_directory(provenance.parent, provenance.parent)
    lock_fd = None
    directory_locked = False
    try:
        _require_working_directory_unchanged(parent, require_ctime=False)
        fcntl.flock(parent.descriptor, fcntl.LOCK_EX)
        directory_locked = True
        _require_working_directory_unchanged(parent, require_ctime=False)
        lock_fd = _open_provenance_lock(parent.descriptor, lock_path.name)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        _provenance_transaction_hook("after_lock")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        _provenance_transaction_hook("before_read")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        old_committed = _read_committed_pair_at(
            parent.descriptor, provenance.name, projection.name
        )
        old_jsonl = old_committed.jsonl
        old_projection = old_committed.projection
        old_public = _read_provenance_pair_at(
            parent.descriptor, provenance.name, projection.name
        )
        _require_transaction_binding(parent, lock_path.name, lock_fd)
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
        _cleanup_generations_at(
            parent.descriptor,
            provenance.name,
            set()
            if old_committed.pointer is None
            else {old_committed.pointer.generation},
        )
        next_pointer, next_pointer_content = _create_generation_pair_at(
            parent.descriptor,
            provenance.name,
            next_jsonl,
            next_projection,
            next_entries,
        )
        pointer_name = _pair_pointer_name(provenance.name)
        originals = {
            provenance.name: old_jsonl,
            projection.name: old_projection,
        }
        expected_current = {
            provenance.name: (
                None if old_public.jsonl is None else old_public.jsonl.version
            ),
            projection.name: (
                None if old_public.projection is None else old_public.projection.version
            ),
        }
        published_names: set[str] = set()
        published_pointer = None
        try:
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            try:
                expected_current[provenance.name] = _atomic_replace_at(
                    parent.descriptor, provenance.name, next_jsonl
                )
            except BaseException as exc:
                published = getattr(exc, "_provenance_published", None)
                if published is not None:
                    expected_current[provenance.name] = published
                    published_names.add(provenance.name)
                raise
            published_names.add(provenance.name)
            _provenance_transaction_hook("after_public_jsonl")
            _provenance_transaction_hook("after_jsonl_replace")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            try:
                expected_current[projection.name] = _atomic_replace_at(
                    parent.descriptor, projection.name, next_projection
                )
            except BaseException as exc:
                published = getattr(exc, "_provenance_published", None)
                if published is not None:
                    expected_current[projection.name] = published
                    published_names.add(projection.name)
                raise
            published_names.add(projection.name)
            _provenance_transaction_hook("after_public_projection")
            _provenance_transaction_hook("after_projection_replace")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            _provenance_transaction_hook("before_pointer")
            try:
                published_pointer = _atomic_replace_at(
                    parent.descriptor, pointer_name, next_pointer_content
                )
            except BaseException as exc:
                published_pointer = getattr(exc, "_provenance_published", None)
                raise
            _provenance_transaction_hook("after_pointer")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            _provenance_transaction_hook("before_verify")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            published_pair = _read_committed_pair_at(
                parent.descriptor, provenance.name, projection.name
            )
            if (
                published_pair.pointer != next_pointer
                or published_pair.jsonl != next_jsonl
                or published_pair.projection != next_projection
            ):
                raise RuntimeError("provenance projection is inconsistent")
            _provenance_transaction_hook("post_publish")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
            _provenance_transaction_hook("before_unlock")
            _require_transaction_binding(parent, lock_path.name, lock_fd)
        except BaseException:
            _provenance_transaction_hook("before_rollback")
            try:
                _require_transaction_binding(parent, lock_path.name, lock_fd)
            except (OSError, RuntimeError):
                pass
            pointer_rollback_error = None
            if published_pointer is not None:
                current_pointer = _read_existing_version_at(
                    parent.descriptor, pointer_name
                )
                if current_pointer is None or current_pointer[1] != published_pointer:
                    raise RuntimeError("provenance rollback conflict")
                try:
                    if old_committed.pointer_content is None:
                        _unlink_if_exists_at(parent.descriptor, pointer_name)
                    else:
                        _atomic_replace_at(
                            parent.descriptor,
                            pointer_name,
                            old_committed.pointer_content,
                        )
                except BaseException as exc:
                    observed = _read_existing_at(parent.descriptor, pointer_name)
                    if observed != old_committed.pointer_content:
                        raise
                    pointer_rollback_error = exc
            _rollback_published_at(
                parent.descriptor,
                provenance.name,
                projection.name,
                originals,
                expected_current,
                published_names,
            )
            if pointer_rollback_error is not None:
                raise pointer_rollback_error
            raise
    finally:
        unlock_error = None
        if lock_fd is not None:
            try:
                _require_bound_lock(parent.descriptor, lock_path.name, lock_fd)
            except BaseException as exc:
                unlock_error = exc
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if directory_locked:
            fcntl.flock(parent.descriptor, fcntl.LOCK_UN)
        os.close(parent.descriptor)
        if unlock_error is not None:
            raise unlock_error


def _preflight_provenance(provenance: Path, command_id: str) -> None:
    projection = provenance.with_suffix(".txt")
    lock_path = provenance.with_name(f".{provenance.name}.lock")
    parent = _open_bound_working_directory(provenance.parent, provenance.parent)
    lock_fd = None
    directory_locked = False
    try:
        _require_working_directory_unchanged(parent, require_ctime=False)
        fcntl.flock(parent.descriptor, fcntl.LOCK_EX)
        directory_locked = True
        _require_working_directory_unchanged(parent, require_ctime=False)
        lock_fd = _open_provenance_lock(parent.descriptor, lock_path.name)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        _provenance_transaction_hook("preflight_after_lock")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        _provenance_transaction_hook("preflight_before_read")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        old_pair = _read_committed_pair_at(
            parent.descriptor,
            provenance.name,
            projection.name,
            require_public=False,
        )
        if old_pair.pointer is not None:
            public = _read_provenance_pair_at(
                parent.descriptor,
                provenance.name,
                projection.name,
                require_complete=False,
            )
            if (
                public.jsonl is None
                or public.projection is None
                or public.jsonl.content != old_pair.jsonl
                or public.projection.content != old_pair.projection
            ):
                _atomic_replace_at(
                    parent.descriptor, provenance.name, old_pair.jsonl or b""
                )
                _provenance_transaction_hook("preflight_after_repair_jsonl")
                _require_transaction_binding(parent, lock_path.name, lock_fd)
                _atomic_replace_at(
                    parent.descriptor, projection.name, old_pair.projection or b""
                )
                _provenance_transaction_hook("preflight_after_repair_projection")
                _require_transaction_binding(parent, lock_path.name, lock_fd)
                old_pair = _read_committed_pair_at(
                    parent.descriptor, provenance.name, projection.name
                )
        old_jsonl = old_pair.jsonl
        old_projection = old_pair.projection
        entries = parse_provenance(old_jsonl or b"")
        if (old_jsonl is None) != (old_projection is None) or (
            old_projection is not None and old_projection != render_commands_projection(entries)
        ):
            raise ValueError("provenance projection is inconsistent")
        if any(item["commandId"] == command_id for item in entries):
            raise ValueError("duplicate command ID")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
        _provenance_transaction_hook("preflight_before_unlock")
        _require_transaction_binding(parent, lock_path.name, lock_fd)
    finally:
        unlock_error = None
        if lock_fd is not None:
            try:
                _require_bound_lock(parent.descriptor, lock_path.name, lock_fd)
            except BaseException as exc:
                unlock_error = exc
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if directory_locked:
            fcntl.flock(parent.descriptor, fcntl.LOCK_UN)
        os.close(parent.descriptor)
        if unlock_error is not None:
            raise unlock_error


def execute_and_record(
    spec: CommandSpec,
    *,
    provenance: Path,
    env: Mapping[str, str] | None = None,
    stdin_bytes: bytes | None = None,
    cancel_event: threading.Event | None = None,
    _before_spawn: Any | None = None,
) -> CommandResult:
    if type(spec) is not CommandSpec:
        raise TypeError("spec must be an immutable CommandSpec")
    if not isinstance(provenance, Path) or not provenance.is_absolute():
        raise ValueError("provenance path must be absolute")
    root = provenance.parent
    pointer_path = provenance.with_name(_pair_pointer_name(provenance.name))
    generation_path = provenance.with_name(
        _generation_directory_name(provenance.name)
    )
    control_paths = {
        provenance,
        provenance.with_suffix(".txt"),
        provenance.with_name(f".{provenance.name}.lock"),
        pointer_path,
        generation_path,
    }
    if any(
        path in control_paths
        or generation_path in path.parents
        or any(_same_file(path, item) for item in control_paths)
        for path in (*spec.inputs, *spec.outputs)
    ):
        raise ValueError("command artifacts alias provenance control files")
    if (stdin_bytes is None) != (spec.stdin_source is None):
        raise ValueError("protected stdin bytes and source must be supplied together")
    if stdin_bytes is not None and type(stdin_bytes) is not bytes:
        raise TypeError("protected stdin must be bytes")
    _preflight_provenance(provenance, spec.command_id)
    inputs, output_parents = _prepare_paths(spec, root)
    try:
        bound_cwd = _open_bound_working_directory(root, spec.cwd)
    except OSError as exc:
        raise RuntimeError("cwd is invalid") from exc
    try:
        try:
            argument_artifacts = _bind_argument_artifacts(spec, root, inputs)
            canonical_spec = _canonical_spec(spec, root)
            spec_digest = _spec_digest(spec)
            child_env = _child_environment(spec, env)
            _validate_executable(spec.argv[0])
            started = _utc_now()
            interrupted = False
            if _before_spawn is not None:
                _before_spawn()
            _require_working_directory_unchanged(bound_cwd, require_ctime=True)
            try:
                exit_code, classification, satisfied = _run_process(
                    spec,
                    child_env,
                    stdin_bytes,
                    cancel_event,
                    bound_cwd.descriptor,
                    argument_artifacts,
                )
            except KeyboardInterrupt:
                exit_code, classification, satisfied = None, "keyboard_interrupt", False
                interrupted = True
            ended = _utc_now()
            _require_working_directory_unchanged(bound_cwd, require_ctime=False)
            _require_argument_artifacts_unchanged(argument_artifacts)
        finally:
            if "argument_artifacts" in locals():
                for descriptor in argument_artifacts.descriptors:
                    try:
                        os.fsync(descriptor)
                    except OSError:
                        pass
                    os.close(descriptor)
    except BaseException:
        _cleanup_declared_outputs(root, spec.outputs, output_parents)
        raise
    finally:
        os.close(bound_cwd.descriptor)
    for path, (bound, parent_chain) in inputs.items():
        if _directory_chain(path.parent) != parent_chain:
            raise RuntimeError("evidence input parent changed")
        require_file_unchanged(path, bound)
    for path, chain in output_parents.items():
        current_parent = _open_bound_working_directory(root, path.parent)
        try:
            if tuple((item[0], item[1]) for item in current_parent.chain) != tuple(
                (item[0], item[1]) for item in chain
            ):
                raise RuntimeError("evidence output parent changed")
        finally:
            os.close(current_parent.descriptor)
    if not satisfied:
        _cleanup_declared_outputs(root, spec.outputs, output_parents)
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
