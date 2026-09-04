"""Execute structured Google Live evidence commands with redacted provenance."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import hmac
import io
import json
import math
import os
import platform
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
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
from scripts.google_live_trusted_git import _git_command as _trusted_git_command
from scripts.google_live_trusted_git import git_output as _trusted_git_output
from scripts.google_live_trusted_git import trusted_git_session

COMMAND_PROVENANCE_SCHEMA = "google-live-command-provenance.v1"
COMMAND_EXECUTION_POLICY = "candidate-git-python-source.v1"
COMMAND_ID = re.compile(r"(?:diagnostic\.)?[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
PYTHON_EXECUTABLE_SCHEMA = "google-live-python-executables.v1"
PYTHON_EXECUTABLE_MANIFEST_GIT_PATH = (
    "main/tbot-server/tests/fixtures/google_live_python_executable_manifest.json"
)
RUNTIME_CLOSURE_SCHEMA = "google-live-runtime-closure.v1"
RUNTIME_CLOSURE_MANIFEST_GIT_PATH = (
    "main/tbot-server/tests/fixtures/google_live_runtime_closure_manifest.json"
)
_MAX_CLOSURE_DISTRIBUTIONS = 32
_MAX_CLOSURE_FILES_PER_DISTRIBUTION = 2048
_MAX_CLOSURE_FILE_BYTES = 32 * 1024 * 1024
_MAX_CLOSURE_TOTAL_FILES = 4096
_MAX_CLOSURE_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_CLOSURE_PATH_DEPTH = 32
_MAX_PROVENANCE_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_GENERATION_FILES = 1024
PAIR_SCHEMA = "google-live-command-provenance-pair.v1"
PENDING_SCHEMA = "google-live-command-provenance-pending.v1"
GENERATION_TOMBSTONE_SCHEMA = "google-live-command-provenance-tombstone.v1"
GENERATION_ID = re.compile(r"[0-9a-f]{32}")
GENERATION_FILE = re.compile(r"([0-9a-f]{32})\.(?:jsonl|txt)")
GENERATION_QUARANTINE = re.compile(
    r"\.([0-9a-f]{32})\.(jsonl|txt)\.([0-9]+)\.([0-9]+)\.([0-9a-f]{64})\.pending"
)
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
executable = sys.argv[2]
keep = [int(value) for value in sys.argv[3].split(",") if value]
argv = sys.argv[4:]
try:
    os.fchdir(cwd)
    os.close(cwd)
    cwd = -1
    os.execve(executable, argv, os.environ)
except BaseException:
    for descriptor in [cwd, *keep]:
        try:
            os.close(descriptor)
        except OSError:
            pass
    os._exit(126)
"""
_GIT_SOURCE_EXEC = """import importlib.machinery, importlib.util, os, subprocess, sys, threading
bootstrap_path = os.path.abspath(sys.argv[0])
source_root, original_root, project_root, script_relative, original_file, dependency_roots_json = sys.argv[1:7]
dependency_roots = __import__('json').loads(dependency_roots_json)
script_arguments = sys.argv[7:]
class BlockedProjectPath:
    @staticmethod
    def find_spec(fullname, target=None):
        del fullname, target
        return None
class CandidateSourceLoader:
    def __init__(self, snapshot_file, original_file, package_directory=None):
        self.snapshot_file = snapshot_file
        self.original_file = original_file
        self.package_directory = package_directory
    def create_module(self, spec):
        del spec
        return None
    def exec_module(self, module):
        module.__file__ = self.snapshot_file
        if self.package_directory is not None:
            module.__path__ = [self.package_directory]
        with open(self.snapshot_file, "rb") as source_file:
            source = source_file.read()
        exec(compile(source, self.snapshot_file, "exec"), module.__dict__, module.__dict__)
class CandidateSourceFinder:
    @staticmethod
    def find_spec(fullname, path=None, target=None):
        del path, target
        relative = fullname.replace(".", "/")
        package_directory = os.path.join(source_root, relative)
        package_file = os.path.join(package_directory, "__init__.py")
        module_file = os.path.join(source_root, relative + ".py")
        if os.path.isfile(package_file):
            loader = CandidateSourceLoader(
                package_file,
                os.path.join(original_root, relative, "__init__.py"),
                package_directory,
            )
            return importlib.util.spec_from_loader(fullname, loader, is_package=True)
        if os.path.isfile(module_file):
            loader = CandidateSourceLoader(
                module_file, os.path.join(original_root, relative + ".py")
            )
            return importlib.util.spec_from_loader(fullname, loader, is_package=False)
        if os.path.isdir(package_directory):
            spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = [package_directory]
            return spec
        return None
sys.meta_path.insert(0, CandidateSourceFinder())
sys.path_importer_cache[original_root] = BlockedProjectPath()
if dependency_roots:
    sys.path[:] = [source_root, *dependency_roots,
        __import__('sysconfig').get_paths()['stdlib'],
        __import__('sysconfig').get_paths()['platstdlib'],
        __import__('sysconfig').get_config_var('DESTSHARED')]
else:
    site_paths = [__import__('sysconfig').get_paths()['purelib']]
    try:
        site_paths.append(__import__('site').getusersitepackages())
    except Exception:
        pass
    sys.path[:] = [source_root, *site_paths, *[item for item in sys.path if item and item != original_root]]
if dependency_roots:
    os.environ['PYTHONNOUSERSITE'] = '1'
original_popen = subprocess.Popen
original_fork_exec = subprocess._fork_exec
spawn_state = threading.local()
def blocked_process_escape(*args, **kwargs):
    del args, kwargs
    raise PermissionError("candidate process escape API is not approved")
def candidate_audit(event, args):
    del args
    if event in {"ctypes.dlopen", "ctypes.dlsym"}:
        raise PermissionError("candidate native FFI is not approved")
    if event in {"os.system", "os.fork", "os.forkpty", "os.exec", "os.posix_spawn", "subprocess.Popen"} and not getattr(spawn_state, "approved", False):
        raise PermissionError("candidate process escape API is not approved")
sys.addaudithook(candidate_audit)
def blocked_cffi_dlopen(*args, **kwargs):
    del args, kwargs
    raise PermissionError("candidate native FFI is not approved")
try:
    import opuslib_next
except Exception:
    pass
try:
    import _cffi_backend, cffi.api
except Exception:
    pass
else:
    cffi.api.FFI.dlopen = blocked_cffi_dlopen
    cffi.api._make_ffi_library = blocked_cffi_dlopen
    _cffi_backend.FFI = blocked_cffi_dlopen
def guarded_fork_exec(*args, **kwargs):
    if not getattr(spawn_state, "approved", False):
        raise PermissionError("candidate process escape API is not approved")
    return original_fork_exec(*args, **kwargs)
subprocess._fork_exec = guarded_fork_exec
try:
    __import__('_posixsubprocess').fork_exec = guarded_fork_exec
except (ImportError, AttributeError):
    pass
os.system = blocked_process_escape
process_module = __import__('posix') if os.name == 'posix' else None
for _name in (
    "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
    "fork", "forkpty", "posix_spawn", "posix_spawnp", "setpgid", "setpgrp",
    "setsid",
):
    if hasattr(os, _name):
        setattr(os, _name, blocked_process_escape)
    if process_module is not None and hasattr(process_module, _name):
        setattr(process_module, _name, blocked_process_escape)
def python_script_index(arguments):
    no_value = {"-b", "-B", "-d", "-E", "-i", "-I", "-O", "-OO", "-P", "-q", "-R", "-s", "-S", "-u", "-v", "-V", "-x"}
    with_value = {"-W", "-X", "--check-hash-based-pycs"}
    index = 1
    while index < len(arguments):
        value = os.fspath(arguments[index])
        if value == "--":
            return index + 1 if index + 1 < len(arguments) else None
        if value in {"-c", "-m"}:
            return None
        if not value.startswith("-"):
            return index
        if value in no_value or value.startswith(("-W", "-X")) and len(value) > 2:
            index += 1
            continue
        if value in with_value and index + 1 < len(arguments):
            index += 2
            continue
        return None
    return None
def candidate_popen(arguments, *args, **kwargs):
    if kwargs.get("shell", False):
        raise PermissionError("shell execution is not approved")
    if (
        kwargs.get("executable") is not None
        or kwargs.get("preexec_fn") is not None
        or kwargs.get("start_new_session", False)
        or kwargs.get("process_group") not in {None, -1}
        or kwargs.get("creationflags", 0) != 0
    ):
        raise PermissionError("candidate child process overrides are not approved")
    if not isinstance(arguments, (list, tuple)) or not arguments:
        raise PermissionError("candidate child executable is not approved")
    script_index = python_script_index(arguments) if isinstance(arguments, (list, tuple)) else None
    if isinstance(arguments, (list, tuple)) and arguments:
        executable = os.path.abspath(os.fspath(arguments[0]))
        approved_executable = os.path.abspath(sys.executable)
        try:
            aliases_approved_interpreter = os.path.samefile(executable, approved_executable)
        except OSError:
            aliases_approved_interpreter = False
        script_shape = (
            script_index is not None
            and isinstance(arguments[script_index], (str, os.PathLike))
            and os.fspath(arguments[script_index]).endswith(".py")
        )
        module_shape = len(arguments) > 1 and arguments[1] in {"-c", "-m"}
        python_execution = executable == approved_executable or aliases_approved_interpreter or script_shape or module_shape
        if not python_execution:
            raise PermissionError("candidate child executable is not approved")
        if python_execution and executable != approved_executable:
            raise PermissionError("Alternate Python interpreter is not approved")
        if python_execution and script_index is None:
            raise PermissionError("Python module and command execution are not approved")
        if python_execution and (
            not isinstance(arguments[script_index], (str, os.PathLike))
            or not os.fspath(arguments[script_index]).endswith(".py")
        ):
            raise PermissionError("Python child execution requires an approved script")
    if (
        isinstance(arguments, (list, tuple))
        and script_index is not None
        and os.path.abspath(os.fspath(arguments[0])) == os.path.abspath(sys.executable)
        and isinstance(arguments[script_index], (str, os.PathLike))
        and os.fspath(arguments[script_index]).endswith(".py")
    ):
        child_file = os.path.abspath(os.fspath(arguments[script_index]))
        try:
            inside_project = os.path.commonpath([project_root, child_file]) == project_root
            inside_original = os.path.commonpath([original_root, child_file]) == original_root
            inside_snapshot = os.path.commonpath([source_root, child_file]) == source_root
        except ValueError:
            inside_project = inside_original = inside_snapshot = False
        if inside_snapshot:
            child_original_root = original_root
            child_relative = os.path.relpath(child_file, source_root).replace(os.sep, "/")
        elif inside_project:
            child_original_root = project_root
            child_relative = os.path.relpath(child_file, project_root).replace(os.sep, "/")
        elif inside_original:
            child_original_root = original_root
            child_relative = os.path.relpath(child_file, original_root).replace(os.sep, "/")
        else:
            child_original_root = None
        if child_original_root is not None:
            arguments = [
                arguments[0], "-I", "-S", "-B", *arguments[1:script_index], bootstrap_path,
                source_root, child_original_root, project_root, child_relative, child_file,
                dependency_roots_json,
                *arguments[script_index + 1:]
            ]
        else:
            raise PermissionError("Python child script is outside candidate project")
    spawn_state.approved = True
    try:
        return original_popen(arguments, *args, **kwargs)
    finally:
        spawn_state.approved = False
subprocess.Popen = candidate_popen
sys.argv = [original_file, *script_arguments]
source_path = os.path.join(source_root, *script_relative.split("/"))
with open(source_path, "rb") as source_file:
    source = source_file.read()
namespace = {
    "__name__": "__main__",
    "__file__": source_path,
    "__package__": None,
    "__cached__": None,
}
exec(compile(source, source_path, "exec"), namespace, namespace)
"""
_MAX_PYTHON_SOURCE_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_PYTHON_SOURCE_FILE_BYTES = 16 * 1024 * 1024
_MAX_PYTHON_SOURCE_MEMBERS = 5000
_RESOURCE_ARCHIVE_TIMEOUT_SEC = 30.0


def _candidate_resource_archive_bound(manifest: Mapping[str, Any]) -> int:
    limits = manifest["limits"]
    metadata = int(limits["maxResourceCount"]) * (
        int(limits["maxPathDepth"]) + 3
    ) * 512
    return int(limits["maxResourceBytes"]) + metadata + 10 * 1024


def _load_candidate_resource_archive(
    expected_git_sha: str, manifest: Mapping[str, Any], *, code_root: Path | None = None
) -> bytes:
    """Stream a manifest-scoped Git archive while enforcing its hard byte cap."""
    code_root = (Path(__file__).resolve().parents[1] if code_root is None else code_root).resolve(strict=True)
    limits = manifest["limits"]
    cap = _candidate_resource_archive_bound(manifest)
    paths = [resource["path"] for resource in manifest["resources"]]
    with trusted_git_session() as identity:
        repo_root = Path(
            _trusted_git_output(code_root, "rev-parse", "--show-toplevel")
            .decode()
            .strip()
        ).resolve(strict=True)
        if _trusted_git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha:
            raise ValueError("candidate git SHA does not match repository HEAD")
        process = subprocess.Popen(
            _trusted_git_command(
                identity.path,
                repo_root,
                "archive",
                "--format=tar",
                expected_git_sha,
                "--",
                *paths,
            ),
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"GIT_ATTR_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
                 "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
                 "LANG": "C", "LC_ALL": "C", "PATH": os.defpath},
        )
        output = bytearray()
        deadline = time.monotonic() + _RESOURCE_ARCHIVE_TIMEOUT_SEC
        try:
            assert process.stdout is not None
            try:
                descriptor = process.stdout.fileno()
            except (AttributeError, io.UnsupportedOperation):
                descriptor = None
            if descriptor is None:
                while True:
                    chunk = process.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > cap:
                        process.kill()
                        process.wait()
                        raise ValueError("candidate resource archive exceeds bound")
            else:
                os.set_blocking(descriptor, False)
                with selectors.DefaultSelector() as selector:
                    selector.register(descriptor, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise TimeoutError
                        chunk = os.read(descriptor, min(1024 * 1024, cap + 1 - len(output)))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > cap:
                            raise OverflowError
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if process.wait(timeout=remaining) != 0:
                raise ValueError("candidate resource archive is unavailable")
        except OverflowError:
            process.kill()
            process.wait(timeout=1)
            raise ValueError("candidate resource archive exceeds bound") from None
        except TimeoutError:
            process.kill()
            process.wait(timeout=1)
            raise ValueError("candidate resource archive timed out") from None
        except (OSError, subprocess.TimeoutExpired) as exc:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            raise ValueError("candidate resource archive is unavailable") from exc
        if _trusted_git_output(code_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha:
            raise ValueError("candidate repository changed during resource validation")
    return bytes(output)


def _materialize_candidate_resources(
    content: bytes, destination: Path, manifest: Mapping[str, Any]
) -> None:
    """Extract only verified regular members from a bounded candidate archive."""
    limits = manifest["limits"]
    if not content or len(content) > _candidate_resource_archive_bound(manifest):
        raise ValueError("candidate resource archive is invalid")
    expected = {item["path"]: item for item in manifest["resources"]}
    if len(expected) != len(manifest["resources"]):
        raise ValueError("candidate resource archive has duplicate paths")
    destination.mkdir(mode=0o700)
    os.chmod(destination, 0o700)

    def ensure_directory(parts: tuple[str, ...]) -> Path:
        current = destination
        for part in parts:
            current /= part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                opened = current.lstat()
                if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
                    raise ValueError("candidate resource archive is invalid")
            os.chmod(current, 0o700, follow_symlinks=False)
        return current

    seen: set[str] = set()
    seen_members: set[str] = set()
    allowed_directories = {
        "/".join(Path(path).parts[:index])
        for path in expected
        for index in range(1, len(Path(path).parts))
    }
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            for member in archive:
                path = Path(member.name)
                canonical_name = member.name.rstrip("/") if member.isdir() else member.name
                if (
                    path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts)
                    or "\\" in member.name or len(path.parts) > int(limits["maxPathDepth"])
                    or any(ord(character) < 32 or ord(character) > 126 for character in member.name)
                    or path.as_posix() != canonical_name
                    or canonical_name in seen_members
                ):
                    raise ValueError("candidate resource archive is invalid")
                seen_members.add(canonical_name)
                if member.isdir():
                    if canonical_name not in allowed_directories:
                        raise ValueError("candidate resource archive is invalid")
                    ensure_directory(path.parts)
                    continue
                if len(seen) >= int(limits["maxResourceCount"]):
                    raise ValueError("candidate resource archive exceeds bound")
                if (
                    not member.isfile()
                    or member.sparse is not None
                    or member.name not in expected
                    or member.name in seen
                ):
                    raise ValueError("candidate resource archive is invalid")
                item = expected[member.name]
                if member.size != item["size"] or member.size > int(limits["maxResourceFileBytes"]):
                    raise ValueError("candidate resource archive exceeds bound")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("candidate resource archive is invalid")
                data = source.read(member.size + 1)
                if len(data) != member.size or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("candidate resource archive digest mismatch")
                target = ensure_directory(path.parts[:-1]) / path.name
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
                try:
                    os.fchmod(descriptor, 0o400)
                    offset = 0
                    while offset < len(data):
                        written = os.write(descriptor, data[offset:])
                        if written <= 0:
                            raise OSError(errno.EIO, "short write")
                        offset += written
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                seen.add(member.name)
                total += len(data)
                if total > int(limits["maxResourceBytes"]):
                    raise ValueError("candidate resource archive exceeds bound")
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ValueError("candidate resource archive is invalid") from exc
    except ValueError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if seen != set(expected):
        shutil.rmtree(destination, ignore_errors=True)
        raise ValueError("candidate resource archive is missing a member")
    for directory, subdirectories, _files in os.walk(destination, topdown=False):
        for name in subdirectories:
            os.chmod(Path(directory) / name, 0o500)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    os.chmod(destination, 0o500)


def _cleanup_candidate_snapshot(destination: Path) -> None:
    """Restore ownership-safe write bits before removing a read-only snapshot."""
    if not destination.exists():
        return
    for directory, subdirectories, files in os.walk(destination, topdown=False):
        for name in files:
            os.chmod(Path(directory) / name, 0o600, follow_symlinks=False)
        for name in subdirectories:
            os.chmod(Path(directory) / name, 0o700, follow_symlinks=False)
        os.chmod(directory, 0o700, follow_symlinks=False)
    os.chmod(destination, 0o700, follow_symlinks=False)
    shutil.rmtree(destination)


@contextmanager
def _candidate_execution_directory():
    root = Path(tempfile.mkdtemp(prefix="google-live-exec-"))
    try:
        yield root
    finally:
        candidate = root / "candidate-source"
        if candidate.exists():
            _cleanup_candidate_snapshot(candidate)
        shutil.rmtree(root)


def _runtime_platform_tuple() -> str:
    return (
        f"{platform.system().lower()}-{platform.machine().lower()}-"
        f"cp{sys.version_info.major}{sys.version_info.minor}"
    )


def _parse_runtime_closure_manifest(
    content: bytes, *, platform_name: str | None = None
) -> Mapping[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("runtime closure manifest is invalid") from exc
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if canonical != content:
        raise ValueError("runtime closure manifest is not canonical")
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "distributions",
            "limits",
            "platform",
            "resourceInventorySha256",
            "resources",
            "runtime",
            "schemaVersion",
        }
        or value.get("schemaVersion") != RUNTIME_CLOSURE_SCHEMA
    ):
        raise ValueError("runtime closure manifest is invalid")
    expected_platform = platform_name or _runtime_platform_tuple()
    if value.get("platform") != expected_platform:
        raise ValueError("runtime closure platform is unsupported")
    runtime = value.get("runtime")
    if (
        not isinstance(runtime, dict)
        or set(runtime)
        != {"interpreterSha256", "pythonImplementation", "pythonMajorMinor"}
        or SHA256.fullmatch(runtime.get("interpreterSha256", "")) is None
    ):
        raise ValueError("runtime closure runtime binding is invalid")
    if runtime.get("pythonImplementation") != sys.implementation.name:
        raise ValueError("runtime closure runtime binding is invalid")
    match = re.fullmatch(r"[^-]+-[^-]+-cp([0-9]{2,3})", expected_platform)
    if (
        match is None
        or str(runtime.get("pythonMajorMinor", "")).replace(".", "")
        != match.group(1)
    ):
        raise ValueError("runtime closure runtime binding is invalid")
    limits = value.get("limits")
    if (
        not isinstance(limits, dict)
        or set(limits)
        != {
            "maxPathDepth",
            "maxResourceBytes",
            "maxResourceCount",
            "maxResourceFileBytes",
        }
        or any(type(limits.get(key)) is not int or limits[key] <= 0 for key in limits)
        or limits["maxResourceCount"] > 5000
        or limits["maxResourceFileBytes"] > 16 * 1024 * 1024
        or limits["maxResourceBytes"] > 64 * 1024 * 1024
        or limits["maxPathDepth"] > 64
    ):
        raise ValueError("runtime closure limits are invalid")
    distributions = value.get("distributions")
    if not isinstance(distributions, list) or len(distributions) > _MAX_CLOSURE_DISTRIBUTIONS:
        raise ValueError("runtime closure distributions are invalid")
    distribution_names: list[str] = []
    for distribution in distributions:
        if not isinstance(distribution, dict) or set(distribution) != {
            "fileCount", "files", "importRoots", "name", "root", "totalBytes", "version"
        }:
            raise ValueError("runtime closure distributions are invalid")
        name, version, root = distribution.get("name"), distribution.get("version"), distribution.get("root")
        if (not isinstance(name, str) or not name or not isinstance(version, str) or not version
                or not isinstance(root, str) or not Path(root).is_absolute()):
            raise ValueError("runtime closure distribution root is invalid")
        roots = distribution.get("importRoots")
        if (not isinstance(roots, list) or not roots or any(
            not isinstance(item, str) or not item or "." in item or "/" in item for item in roots
        ) or roots != sorted(set(roots))):
            raise ValueError("runtime closure import roots are invalid")
        files = distribution.get("files")
        if not isinstance(files, list) or not files or len(files) > _MAX_CLOSURE_FILES_PER_DISTRIBUTION:
            raise ValueError("runtime closure distribution files are invalid")
        paths: list[str] = []
        total = 0
        for item in files:
            if not isinstance(item, dict) or set(item) not in (
                {"path", "sha256", "size"}, {"kind", "path", "sha256", "size"}
            ):
                raise ValueError("runtime closure distribution files are invalid")
            path = item.get("path")
            if (not isinstance(path, str) or not path or Path(path).is_absolute()
                    or any(part in {"", ".", ".."} for part in Path(path).parts)
                    or "\\" in path or len(Path(path).parts) > _MAX_CLOSURE_PATH_DEPTH
                    or SHA256.fullmatch(item.get("sha256", "")) is None
                    or type(item.get("size")) is not int or item["size"] < 0
                    or item["size"] > _MAX_CLOSURE_FILE_BYTES):
                raise ValueError("runtime closure distribution file is invalid")
            if path.endswith(".pth") or "/direct_url.json" in path:
                raise ValueError("runtime closure rejects editable or site injection")
            native = path.endswith((".so", ".dylib"))
            if item.get("kind", "native" if native else "data") != (
                "native" if native else "data"
            ):
                raise ValueError("runtime closure distribution file kind is invalid")
            paths.append(path); total += item["size"]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("runtime closure distribution files are not sorted or unique")
        if distribution["fileCount"] != len(files) or distribution["totalBytes"] != total:
            raise ValueError("runtime closure distribution totals are invalid")
        distribution_names.append(name.lower().replace("-", "_"))
    if (distribution_names != sorted(distribution_names)
            or len(distribution_names) != len(set(distribution_names))
            or sum(item["fileCount"] for item in distributions) > _MAX_CLOSURE_TOTAL_FILES
            or sum(item["totalBytes"] for item in distributions) > _MAX_CLOSURE_TOTAL_BYTES):
        raise ValueError("runtime closure distributions are not sorted or unique")
    resources = value.get("resources")
    if (
        not isinstance(resources, list)
        or not resources
        or len(resources) > limits["maxResourceCount"]
    ):
        raise ValueError("runtime closure resource inventory is invalid")
    paths: list[str] = []
    total = 0
    for resource in resources:
        if not isinstance(resource, dict) or set(resource) != {
            "gitBlob",
            "kind",
            "path",
            "sha256",
            "size",
        }:
            raise ValueError("runtime closure resource inventory is invalid")
        path = resource.get("path")
        if (
            not isinstance(path, str) or not path or Path(path).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(path).parts)
            or "\\" in path
            or any(ord(character) < 32 or ord(character) > 126 for character in path)
            or len(Path(path).parts) > limits["maxPathDepth"]
        ):
            raise ValueError("runtime closure resource path is not relative")
        if resource.get("kind") not in {"python", "config", "wav", "json", "yaml"}:
            raise ValueError("runtime closure resource kind is invalid")
        if (
            not isinstance(resource.get("gitBlob"), str)
            or re.fullmatch(r"[0-9a-f]{40}", resource["gitBlob"]) is None
        ):
            raise ValueError("runtime closure resource blob is invalid")
        if (
            SHA256.fullmatch(resource.get("sha256", "")) is None
            or type(resource.get("size")) is not int
        ):
            raise ValueError("runtime closure resource digest is invalid")
        if resource["size"] < 0 or resource["size"] > limits["maxResourceFileBytes"]:
            raise ValueError("runtime closure resource size is invalid")
        paths.append(path)
        total += resource["size"]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("runtime closure resource inventory is not sorted or unique")
    if total > limits["maxResourceBytes"]:
        raise ValueError("runtime closure resource inventory exceeds bound")
    inventory = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    inventory_digest = value.get("resourceInventorySha256")
    if (
        not isinstance(inventory_digest, str)
        or SHA256.fullmatch(inventory_digest) is None
        or not hmac.compare_digest(
            inventory_digest, hashlib.sha256(inventory).hexdigest()
        )
    ):
        raise ValueError("runtime closure resource inventory digest mismatch")
    return value


def _load_runtime_closure_manifest(
    expected_git_sha: str,
    *,
    code_root: Path | None = None,
    platform_name: str | None = None,
) -> Mapping[str, Any]:
    code_root = (
        Path(__file__).resolve().parents[1] if code_root is None else code_root
    ).resolve(strict=True)
    with trusted_git_session():
        repo_root = Path(
            _trusted_git_output(code_root, "rev-parse", "--show-toplevel")
            .decode()
            .strip()
        ).resolve(strict=True)
        observed = _trusted_git_output(repo_root, "rev-parse", "HEAD").decode().strip()
        if observed != expected_git_sha:
            raise ValueError("candidate git SHA does not match repository HEAD")
        if _trusted_git_output(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
        ):
            raise ValueError("candidate worktree contains tracked or staged modifications")
        try:
            content = _trusted_git_output(
                repo_root,
                "show",
                f"{expected_git_sha}:{RUNTIME_CLOSURE_MANIFEST_GIT_PATH}",
            )
        except Exception as exc:
            raise ValueError("runtime closure manifest is missing from Git") from exc
        manifest = _parse_runtime_closure_manifest(content, platform_name=platform_name)
        executable_manifest = _parse_python_executable_manifest(
            _load_trusted_python_executable_manifest(expected_git_sha)
        )
        try:
            executable_content = _trusted_executable_content(
                sys.executable, executable_manifest
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("runtime closure interpreter is not trusted") from exc
        if not hmac.compare_digest(
            hashlib.sha256(executable_content).hexdigest(),
            manifest["runtime"]["interpreterSha256"],
        ):
            raise ValueError("runtime closure interpreter digest mismatch")
        for resource in manifest["resources"]:
            spec = f"{expected_git_sha}:{resource['path']}"
            try:
                tree_entry = _trusted_git_output(
                    repo_root,
                    "ls-tree",
                    "-z",
                    "--full-name",
                    expected_git_sha,
                    "--",
                    resource["path"],
                )
                tree_meta, blob_name = tree_entry.rstrip(b"\0").decode().split("\t", 1)
                mode, _type, tree_blob = tree_meta.split()
                kind = _trusted_git_output(repo_root, "cat-file", "-t", spec).decode().strip()
                blob = _trusted_git_output(repo_root, "rev-parse", spec).decode().strip()
                data = _trusted_git_output(repo_root, "show", spec)
            except Exception as exc:
                raise ValueError("runtime closure resource is missing from Git") from exc
            if (
                mode not in {"100644", "100755"}
                or _type != "blob"
                or tree_blob != resource["gitBlob"]
                or blob_name != resource["path"]
                or kind != "blob"
                or blob != resource["gitBlob"]
                or len(data) != resource["size"]
                or hashlib.sha256(data).hexdigest() != resource["sha256"]
            ):
                raise ValueError("runtime closure resource is not a regular verified blob")
        if (
            _trusted_git_output(repo_root, "rev-parse", "HEAD").decode().strip()
            != expected_git_sha
            or _trusted_git_output(
                repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
            )
        ):
            raise ValueError("candidate repository changed during closure validation")
    return manifest


def _materialize_distribution_closure(
    manifest: Mapping[str, Any], destination: Path
) -> list[str]:
    if not manifest.get("distributions"):
        return []
    destination.mkdir(mode=0o700)
    for distribution in manifest.get("distributions", []):
        root = Path(distribution["root"]).resolve(strict=True)
        has_native = any(item.get("kind") == "native" for item in distribution["files"])
        for item in distribution["files"]:
            path = root.joinpath(*item["path"].split("/"))
            if not path.is_file() or path.is_symlink() or path.stat().st_size != item["size"]:
                raise ValueError("runtime closure distribution file changed")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("runtime closure distribution file digest mismatch")
            target = destination.joinpath(*item["path"].split("/"))
            if has_native:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not target.exists():
                    os.symlink(path, target)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError("runtime closure distributions overlap")
                continue
            mode = 0o500 if item.get("kind") == "native" else 0o400
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                mode,
            )
            try:
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return [str(destination.resolve(strict=True))]


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
        if (
            type(self.timeout_sec) not in {int, float}
            or not math.isfinite(self.timeout_sec)
            or self.timeout_sec <= 0
        ):
            raise ValueError("timeout must be positive")
        if (
            type(self.cleanup_grace_sec) not in {int, float}
            or not math.isfinite(self.cleanup_grace_sec)
            or self.cleanup_grace_sec < 0
        ):
            raise ValueError("cleanup grace must be non-negative")
        if self.stdin_source not in {
            None,
            "protected_transcript_plan",
            "protected_candidate_plan",
        }:
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
class PlannedArtifact:
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
    if re.fullmatch(r"<env:[A-Z][A-Z0-9_]*>", value) or value in {
        "<stdin:protected_transcript_plan>",
        "<stdin:protected_candidate_plan>",
    }:
        return False
    if "<env:" in value or "<stdin:" in value:
        return True
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
    spec: CommandSpec, root: Path, *, materialize_outputs: bool = True
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
    for output in spec.outputs if materialize_outputs else ():
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


def _remove_new_output_parents(root: Path, outputs: tuple[Path, ...], existing: set[Path]) -> None:
    candidates = set()
    for output in outputs:
        current = output.parent
        while current != root and _inside(root, current):
            if current in existing:
                break
            candidates.add(current)
            current = current.parent
    for directory in sorted(candidates, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


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


def _canonical_spec(spec: CommandSpec, root: Path, closure_digest: str | None = None) -> dict[str, Any]:
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
    value = {
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
        "executionPolicy": COMMAND_EXECUTION_POLICY,
    }
    if closure_digest:
        value["runtimeClosureSha256"] = closure_digest
    return value


def _digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _load_trusted_python_executable_manifest(expected_git_sha: str) -> bytes:
    code_root = Path(__file__).resolve().parents[1]
    with trusted_git_session():
        repo_root = Path(
            _trusted_git_output(code_root, "rev-parse", "--show-toplevel")
            .decode()
            .strip()
        ).resolve(strict=True)
        if (
            _trusted_git_output(repo_root, "rev-parse", "HEAD").decode().strip()
            != expected_git_sha
        ):
            raise ValueError("candidate git SHA does not match repository HEAD")
        if _trusted_git_output(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
        ):
            raise ValueError("candidate worktree contains tracked or staged modifications")
        content = _trusted_git_output(
            repo_root,
            "show",
            f"{expected_git_sha}:{PYTHON_EXECUTABLE_MANIFEST_GIT_PATH}",
        )
        if (
            _trusted_git_output(repo_root, "rev-parse", "HEAD").decode().strip()
            != expected_git_sha
            or _trusted_git_output(
                repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
            )
        ):
            raise ValueError("candidate repository changed during executable validation")
    return content


def _load_candidate_python_archive(expected_git_sha: str) -> bytes:
    code_root = Path(__file__).resolve().parents[1]
    with trusted_git_session():
        observed_head = _trusted_git_output(code_root, "rev-parse", "HEAD").decode().strip()
        if observed_head != expected_git_sha:
            raise ValueError("candidate git SHA does not match repository HEAD")
        content = _trusted_git_output(
            code_root,
            "archive",
            "--format=tar",
            expected_git_sha,
            "--",
            ":(glob)**/*.py",
            ":(glob)*.py",
        )
        if (
            len(content) > _MAX_PYTHON_SOURCE_ARCHIVE_BYTES
            or _trusted_git_output(code_root, "rev-parse", "HEAD").decode().strip()
            != expected_git_sha
        ):
            raise ValueError("candidate repository changed during source validation")
    return content


def _materialize_candidate_python_sources(content: bytes, destination: Path) -> None:
    if not content or len(content) > _MAX_PYTHON_SOURCE_ARCHIVE_BYTES:
        raise ValueError("candidate Python source archive is invalid")
    destination.mkdir(mode=0o700)
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            for member in archive:
                count += 1
                path = Path(member.name)
                if (
                    count > _MAX_PYTHON_SOURCE_MEMBERS
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or any(ord(character) < 32 or ord(character) > 126 for character in member.name)
                ):
                    raise ValueError("candidate Python source archive is invalid")
                target = destination.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if (
                    not member.isfile()
                    or path.suffix != ".py"
                    or member.size < 0
                    or member.size > _MAX_PYTHON_SOURCE_FILE_BYTES
                ):
                    raise ValueError("candidate Python source archive is invalid")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("candidate Python source archive is invalid")
                data = source.read(_MAX_PYTHON_SOURCE_FILE_BYTES + 1)
                if len(data) != member.size:
                    raise ValueError("candidate Python source archive is invalid")
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                )
                try:
                    offset = 0
                    while offset < len(data):
                        offset += os.write(descriptor, data[offset:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
    except (OSError, tarfile.TarError) as exc:
        raise ValueError("candidate Python source archive is invalid") from exc
    for directory, subdirectories, _files in os.walk(destination, topdown=False):
        for name in subdirectories:
            os.chmod(Path(directory) / name, 0o500)
    os.chmod(destination, 0o500)


def _relative_python_script(argv: tuple[str, ...]) -> tuple[str, int] | None:
    no_value = {
        "-b", "-B", "-d", "-E", "-i", "-I", "-O", "-OO", "-P", "-q",
        "-R", "-s", "-S", "-u", "-v", "-V", "-x",
    }
    with_value = {"-W", "-X", "--check-hash-based-pycs"}
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == "--":
            index += 1
            break
        if value in {"-c", "-m"}:
            return None
        if not value.startswith("-"):
            break
        if value in no_value or value.startswith(("-W", "-X")) and len(value) > 2:
            index += 1
            continue
        if value in with_value and index + 1 < len(argv):
            index += 2
            continue
        raise ValueError("Python command options are invalid")
    if index >= len(argv) or not argv[index].endswith(".py"):
        return None
    script = Path(argv[index])
    if script.is_absolute() or any(part in {"", ".", ".."} for part in script.parts):
        raise ValueError("relative Python script path is invalid")
    return script.as_posix(), index


def _git_bound_python_argv(
    spec: CommandSpec,
    runtime_argv: tuple[str, ...],
    source_root: Path,
    bootstrap_path: Path,
    script_relative: str,
    script_index: int,
    dependency_roots: list[str] | None = None,
) -> tuple[str, ...]:
    source_root = source_root.resolve(strict=True)
    bootstrap_path = bootstrap_path.resolve(strict=True)
    script_path = source_root.joinpath(*script_relative.split("/"))
    if not script_path.is_file():
        raise ValueError("relative Python script is absent from candidate Git source")
    original_root = spec.cwd.resolve(strict=True)
    project_root = Path(__file__).resolve().parents[1]
    project_file = project_root.joinpath(*script_relative.split("/"))
    original_file = (
        project_file if project_file.is_file() else original_root.joinpath(*script_relative.split("/"))
    )
    return (
        runtime_argv[0],
        "-I",
        "-S",
        "-B",
        *runtime_argv[1:script_index],
        str(bootstrap_path),
        str(source_root),
        str(original_root),
        str(project_root),
        script_relative,
        str(original_file),
        json.dumps(dependency_roots or [], separators=(",", ":")),
        *runtime_argv[script_index + 1 :],
    )


def _candidate_snapshot_project_root(
    destination: Path, manifest: Mapping[str, Any], script_relative: str
) -> Path:
    suffix = "/" + script_relative
    matches = []
    for resource in manifest["resources"]:
        path = resource["path"]
        if path == script_relative:
            matches.append(destination)
        elif path.endswith(suffix):
            prefix = path[: -len(suffix)]
            matches.append(destination.joinpath(*prefix.split("/")))
    if len(matches) != 1:
        raise ValueError("relative Python script is absent or ambiguous in candidate snapshot")
    return matches[0]


def _resolve_candidate_resource_arguments(
    spec: CommandSpec,
    argv: tuple[str, ...],
    *,
    script_index: int,
    snapshot_tree: Path,
    source_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[str, ...]:
    protected = {str(path) for path in (*spec.inputs, *spec.outputs)}
    project_root = Path(__file__).resolve().parents[1]
    relative_resources: dict[str, str] = {}
    for resource in manifest["resources"]:
        target = snapshot_tree.joinpath(*resource["path"].split("/"))
        try:
            relative = target.relative_to(source_root).as_posix()
        except ValueError:
            continue
        relative_resources[relative] = str(target)
    resolved = list(argv)
    for index in range(script_index + 1, len(resolved)):
        value = resolved[index]
        if value in protected:
            continue
        replacement = relative_resources.get(value)
        if replacement is None and os.path.isabs(value):
            try:
                relative = Path(value).relative_to(project_root).as_posix()
            except ValueError:
                pass
            else:
                replacement = relative_resources.get(relative)
        if replacement is not None:
            resolved[index] = replacement
    return tuple(resolved)


def _parse_python_executable_manifest(content: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("trusted Python executable manifest is invalid") from exc
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (
        canonical != content
        or not isinstance(value, dict)
        or set(value) != {"profiles", "schemaVersion"}
        or value.get("schemaVersion") != PYTHON_EXECUTABLE_SCHEMA
        or not isinstance(value.get("profiles"), list)
        or not value["profiles"]
    ):
        raise ValueError("trusted Python executable manifest is invalid")
    keys = []
    for profile in value["profiles"]:
        if (
            not isinstance(profile, dict)
            or set(profile)
            != {
                "machine",
                "pythonImplementation",
                "pythonMajorMinor",
                "sha256",
                "size",
                "system",
            }
            or any(
                type(profile.get(field)) is not str or not profile[field]
                for field in (
                    "machine",
                    "pythonImplementation",
                    "pythonMajorMinor",
                    "sha256",
                    "system",
                )
            )
            or SHA256.fullmatch(profile["sha256"]) is None
            or re.fullmatch(r"[0-9]+\.[0-9]+", profile["pythonMajorMinor"]) is None
            or type(profile.get("size")) is not int
            or profile["size"] <= 0
            or profile["size"] > 128 * 1024 * 1024
        ):
            raise ValueError("trusted Python executable manifest is invalid")
        keys.append(
            (
                profile["system"],
                profile["machine"],
                profile["pythonImplementation"],
                profile["pythonMajorMinor"],
            )
        )
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("trusted Python executable manifest is invalid")
    return value


def _trusted_executable_content(
    path: str, manifest: Mapping[str, Any]
) -> bytes:
    descriptor = None
    try:
        resolved = Path(path).resolve(strict=True)
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > 128 * 1024 * 1024
            or before.st_mode & 0o111 == 0
        ):
            raise RuntimeError("spawn_failed")
        content = bytearray()
        remaining = before.st_size
        magic = b""
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("spawn_failed")
            if not magic:
                magic = chunk[:4]
            content.extend(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise RuntimeError("spawn_failed")
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        allowed_names = {"python", "python3", f"python{version}"}
        matches = [
            profile
            for profile in manifest.get("profiles", [])
            if isinstance(profile, Mapping)
            and profile.get("system") == platform.system().lower()
            and profile.get("machine") == platform.machine().lower()
            and profile.get("pythonImplementation") == sys.implementation.name
            and profile.get("pythonMajorMinor") == version
            and Path(path).name in allowed_names
            and profile.get("size") == before.st_size
            and type(profile.get("sha256")) is str
            and hmac.compare_digest(
                profile["sha256"], hashlib.sha256(content).hexdigest()
            )
        ]
        if len(matches) != 1 or magic not in {
            b"\x7fELF",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xce\xfa\xed\xfe",
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xce",
            b"\xfe\xed\xfa\xcf",
        } and not magic.startswith(b"MZ"):
            raise RuntimeError("spawn_failed")
        return bytes(content)
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise RuntimeError("spawn_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_executable_snapshot(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500)
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise RuntimeError("spawn_failed")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _write_python_executable_snapshot(
    directory: Path, name: str, source: str, content: bytes
) -> Path:
    if platform.system() != "Darwin":
        return _write_executable_snapshot(directory, name, content)
    path = directory / name
    try:
        os.link(Path(source).resolve(strict=True), path, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError("spawn_failed") from exc
    if path.read_bytes() != content:
        raise RuntimeError("spawn_failed")
    return path


def _terminate_group(process: subprocess.Popen[bytes], pgid: int, grace: float) -> None:
    while True:
        try:
            os.killpg(pgid, signal.SIGTERM)
            break
        except ProcessLookupError:
            break
        except KeyboardInterrupt:
            continue
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            break
        except KeyboardInterrupt:
            continue
        try:
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            continue
    while True:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            break
        except KeyboardInterrupt:
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
            break
        except ProcessLookupError:
            break
        except KeyboardInterrupt:
            continue
    while True:
        try:
            process.communicate()
            break
        except KeyboardInterrupt:
            continue


def _run_process(
    spec: CommandSpec,
    child_env: Mapping[str, str],
    stdin_bytes: bytes | None,
    cancel_event: threading.Event | None,
    cwd_descriptor: int,
    argument_artifacts: BoundArgumentArtifacts,
    executable_path: Path,
    launcher_path: Path,
    runtime_argv: tuple[str, ...] | None = None,
) -> tuple[int | None, str, bool]:
    command_argv = argument_artifacts.argv if runtime_argv is None else runtime_argv
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _FCHDIR_EXEC,
                str(cwd_descriptor),
                str(executable_path),
                ",".join(str(value) for value in argument_artifacts.descriptors),
                *command_argv,
            ],
            executable=str(launcher_path),
            shell=False,
            cwd=None,
            env=dict(child_env),
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _artifact_rows(
    root: Path, paths: tuple[Path, ...]
) -> tuple[list[dict[str, str]], dict[Path, Any]]:
    result = []
    bindings = {}
    for path in paths:
        try:
            first = read_bound_file(path)
        except OSError as exc:
            raise RuntimeError("required evidence output is missing") from exc
        if not stat.S_ISREG(first.mode) or first.links != 1:
            raise RuntimeError("evidence output alias detected")
        require_file_unchanged(path, first)
        result.append({"label": _label(root, path), "sha256": hashlib.sha256(first.content).hexdigest()})
        bindings[path] = first
    return result, bindings


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


def _cleanup_bound_outputs(
    root: Path,
    bindings: Mapping[Path, Any],
    parents: Mapping[Path, tuple[tuple[int, int, int, int, int, int], ...]],
) -> None:
    for path, expected in bindings.items():
        try:
            parent = _open_bound_working_directory(root, path.parent)
            try:
                if tuple((item[0], item[1], item[2], item[4]) for item in parent.chain) != tuple(
                    (item[0], item[1], item[2], item[4]) for item in parents[path]
                ):
                    continue
                current = read_bound_file(path)
                if current != expected:
                    continue
                quarantine = f".{path.name}.{secrets.token_hex(12)}.cleanup"
                os.rename(
                    path.name,
                    quarantine,
                    src_dir_fd=parent.descriptor,
                    dst_dir_fd=parent.descriptor,
                )
                quarantined = read_bound_file(path.with_name(quarantine))
                unchanged = (
                    quarantined.content == expected.content
                    and quarantined.device == expected.device
                    and quarantined.inode == expected.inode
                    and quarantined.size == expected.size
                    and quarantined.modified_ns == expected.modified_ns
                    and quarantined.mode == expected.mode
                    and quarantined.links == expected.links
                )
                if unchanged:
                    os.unlink(quarantine, dir_fd=parent.descriptor)
                else:
                    try:
                        os.link(
                            quarantine,
                            path.name,
                            src_dir_fd=parent.descriptor,
                            dst_dir_fd=parent.descriptor,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        continue
                    os.unlink(quarantine, dir_fd=parent.descriptor)
                os.fsync(parent.descriptor)
            finally:
                os.close(parent.descriptor)
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


def _provenance_has_unsafe_placeholder(value: Any, path: tuple[Any, ...] = ()) -> bool:
    if isinstance(value, dict):
        return any(
            _provenance_has_unsafe_placeholder(item, (*path, key))
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(
            _provenance_has_unsafe_placeholder(item, (*path, index))
            for index, item in enumerate(value)
        )
    if type(value) is not str or ("<env:" not in value and "<stdin:" not in value):
        return False
    root = path[0] if path else None
    if root in {"argv", "environmentSources", "secretSources"} and re.fullmatch(
        r"<env:[A-Z][A-Z0-9_]*>", value
    ):
        return False
    if root in {"argv", "stdinSource"} and value in {
        "<stdin:protected_transcript_plan>",
        "<stdin:protected_candidate_plan>",
    }:
        return False
    return True


def validate_provenance_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError("provenance is invalid")
    seen_ids = set()
    seen_digests = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _EXACT_ENTRY_FIELDS:
            raise ValueError("provenance schema is invalid")
        if _provenance_has_unsafe_placeholder(entry):
            raise ValueError("provenance privacy contract is invalid")
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
            or not math.isfinite(terminal["timeoutSec"])
            or terminal["timeoutSec"] <= 0
            or type(terminal.get("cleanupGraceSec")) not in {int, float}
            or not math.isfinite(terminal["cleanupGraceSec"])
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
        if not isinstance(argv, list) or not argv or any(
            type(item) is not str or not item for item in argv
        ):
            raise ValueError("provenance argv is invalid")
        if any(_unsafe_recorded_value(item) for item in argv):
            raise ValueError("provenance privacy contract is invalid")
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
        if entry.get("stdinSource") not in {
            None,
            "<stdin:protected_transcript_plan>",
            "<stdin:protected_candidate_plan>",
        }:
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
        scrubbed = re.sub(
            r"<env:[A-Z][A-Z0-9_]*>|<stdin:protected_(?:transcript|candidate)_plan>",
            "<source>",
            rendered,
        )
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


def _pending_generation_name(jsonl_name: str) -> str:
    _component_key(jsonl_name)
    return f".{jsonl_name}.pending"


def _render_pending_generation(
    generation: str,
    artifacts: Mapping[str, PublishedArtifact | PlannedArtifact | None] | None = None,
) -> bytes:
    if type(generation) is not str or GENERATION_ID.fullmatch(generation) is None:
        raise ValueError("provenance pending generation is invalid")
    owned = {"jsonl": None, "txt": None} if artifacts is None else dict(artifacts)
    if set(owned) != {"jsonl", "txt"}:
        raise ValueError("provenance pending generation is invalid")
    rendered = {}
    for suffix, artifact in owned.items():
        if artifact is None:
            rendered[suffix] = None
        elif isinstance(artifact, PlannedArtifact):
            rendered[suffix] = {
                "device": None,
                "inode": None,
                "sha256": artifact.digest,
            }
        elif isinstance(artifact, PublishedArtifact):
            rendered[suffix] = {
                "device": artifact.device,
                "inode": artifact.inode,
                "sha256": artifact.digest,
            }
        else:
            raise ValueError("provenance pending generation is invalid")
    return (
        json.dumps(
            {
                "artifacts": rendered,
                "generation": generation,
                "schemaVersion": PENDING_SCHEMA,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _parse_pending_generation(
    content: bytes,
) -> tuple[str, dict[str, PublishedArtifact | PlannedArtifact | None]]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("provenance pending generation is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"artifacts", "generation", "schemaVersion"}
        or value.get("schemaVersion") != PENDING_SCHEMA
        or not isinstance(value.get("artifacts"), dict)
        or set(value["artifacts"]) != {"jsonl", "txt"}
    ):
        raise ValueError("provenance pending generation is invalid")
    artifacts: dict[str, PublishedArtifact | PlannedArtifact | None] = {}
    for suffix, artifact in value["artifacts"].items():
        if artifact is None:
            artifacts[suffix] = None
        elif (
            isinstance(artifact, dict)
            and set(artifact) == {"device", "inode", "sha256"}
            and type(artifact.get("sha256")) is str
            and SHA256.fullmatch(artifact["sha256"]) is not None
        ):
            if artifact.get("device") is None and artifact.get("inode") is None:
                artifacts[suffix] = PlannedArtifact(artifact["sha256"])
            elif type(artifact.get("device")) is int and type(artifact.get("inode")) is int:
                artifacts[suffix] = PublishedArtifact(
                    artifact["device"], artifact["inode"], artifact["sha256"]
                )
            else:
                raise ValueError("provenance pending generation is invalid")
        else:
            raise ValueError("provenance pending generation is invalid")
    generation = value.get("generation")
    if _render_pending_generation(generation, artifacts) != content:
        raise ValueError("provenance pending generation is invalid")
    return generation, artifacts


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


def _write_generation_at(
    directory_fd: int,
    name: str,
    content: bytes,
) -> None:
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
    artifacts: dict[str, PublishedArtifact | PlannedArtifact | None] = {
        "jsonl": None,
        "txt": None,
    }
    try:
        _atomic_replace_at(
            directory_fd,
            _pending_generation_name(jsonl_name),
            _render_pending_generation(generation),
        )
        _provenance_transaction_hook("after_generation_pending_journal")
        artifacts["jsonl"] = PlannedArtifact(hashlib.sha256(jsonl).hexdigest())
        _atomic_replace_at(
            directory_fd,
            _pending_generation_name(jsonl_name),
            _render_pending_generation(generation, artifacts),
        )
        _provenance_transaction_hook("after_generation_jsonl_planned_journal")
        _write_generation_at(generation_fd, f"{generation}.jsonl", jsonl)
        _provenance_transaction_hook("after_generation_jsonl_before_journal")
        jsonl_snapshot = _read_existing_snapshot_at(
            generation_fd, f"{generation}.jsonl"
        )
        if jsonl_snapshot is None:
            raise RuntimeError("provenance generation changed")
        artifacts["jsonl"] = jsonl_snapshot.version
        _atomic_replace_at(
            directory_fd,
            _pending_generation_name(jsonl_name),
            _render_pending_generation(generation, artifacts),
        )
        _provenance_transaction_hook("after_generation_jsonl")
        artifacts["txt"] = PlannedArtifact(hashlib.sha256(projection).hexdigest())
        _atomic_replace_at(
            directory_fd,
            _pending_generation_name(jsonl_name),
            _render_pending_generation(generation, artifacts),
        )
        _provenance_transaction_hook("after_generation_projection_planned_journal")
        _write_generation_at(generation_fd, f"{generation}.txt", projection)
        _provenance_transaction_hook("after_generation_projection_before_journal")
        projection_snapshot = _read_existing_snapshot_at(
            generation_fd, f"{generation}.txt"
        )
        if projection_snapshot is None:
            raise RuntimeError("provenance generation changed")
        artifacts["txt"] = projection_snapshot.version
        _atomic_replace_at(
            directory_fd,
            _pending_generation_name(jsonl_name),
            _render_pending_generation(generation, artifacts),
        )
        _provenance_transaction_hook("after_generation_projection")
        os.fsync(generation_fd)
        _unlink_if_exists_at(directory_fd, _pending_generation_name(jsonl_name))
    except BaseException:
        _recover_pending_generation_at(directory_fd, jsonl_name)
        raise
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


def _generation_quarantine_name(name: str, snapshot: PointerFileSnapshot) -> str:
    generation, suffix = name.rsplit(".", 1)
    if GENERATION_ID.fullmatch(generation) is None or suffix not in {"jsonl", "txt"}:
        raise ValueError("provenance generation name is invalid")
    return (
        f".{generation}.{suffix}.{snapshot.version.device}."
        f"{snapshot.version.inode}.{snapshot.version.digest}.pending"
    )


def _generation_snapshot_identity_matches(
    observed: PointerFileSnapshot | None, expected: PointerFileSnapshot
) -> bool:
    return observed is not None and (
        observed.version == expected.version
        and observed.identity[2] == expected.identity[2]
        and observed.identity[3] == expected.identity[3]
        and observed.identity[6] == expected.identity[6]
        and observed.identity[7] == expected.identity[7]
    )


def _rename_noreplace_at(
    directory_fd: int, source: str, destination: str
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000004,
        )
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(
            directory_fd,
            source_bytes,
            directory_fd,
            destination_bytes,
            0x00000001,
        )
    else:
        raise RuntimeError("atomic no-replace rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        raise OSError(error, os.strerror(error), destination)


def _render_generation_tombstone(artifact: PublishedArtifact) -> bytes:
    return (
        json.dumps(
            {
                "device": artifact.device,
                "inode": artifact.inode,
                "schemaVersion": GENERATION_TOMBSTONE_SCHEMA,
                "sha256": artifact.digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _generation_tombstone_matches(
    snapshot: PointerFileSnapshot | None, artifact: PublishedArtifact
) -> bool:
    return snapshot is not None and (
        snapshot.version.device == artifact.device
        and snapshot.version.inode == artifact.inode
        and snapshot.content == _render_generation_tombstone(artifact)
        and stat.S_ISREG(snapshot.identity[2])
        and stat.S_IMODE(snapshot.identity[2]) == 0o400
        and snapshot.identity[6] == os.geteuid()
        and snapshot.identity[7] == 1
    )


def _generation_quarantine_inode_is_owned(
    snapshot: PointerFileSnapshot | None, artifact: PublishedArtifact
) -> bool:
    return snapshot is not None and (
        snapshot.version.device == artifact.device
        and snapshot.version.inode == artifact.inode
        and stat.S_ISREG(snapshot.identity[2])
        and stat.S_IMODE(snapshot.identity[2]) in {0o400, 0o600}
        and snapshot.identity[6] == os.geteuid()
        and snapshot.identity[7] == 1
    )


def _compact_generation_quarantine_at(
    directory_fd: int,
    quarantine: str,
    expected: PointerFileSnapshot,
) -> None:
    readonly = os.open(
        quarantine,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    writable = None
    try:
        opened = os.fstat(readonly)
        if (opened.st_dev, opened.st_ino) != (
            expected.version.device,
            expected.version.inode,
        ):
            raise RuntimeError("provenance generation changed before compaction")
        os.fchmod(readonly, 0o600)
        writable = os.open(
            quarantine,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        rebound = os.fstat(writable)
        if (rebound.st_dev, rebound.st_ino) != (
            expected.version.device,
            expected.version.inode,
        ):
            raise RuntimeError("provenance generation changed during compaction")
        content = _render_generation_tombstone(expected.version)
        os.ftruncate(writable, 0)
        remaining = memoryview(content)
        while remaining:
            count = os.write(writable, remaining)
            if count <= 0:
                raise OSError("provenance generation compaction failed")
            remaining = remaining[count:]
        os.fsync(writable)
        os.fchmod(writable, 0o400)
    finally:
        if writable is not None:
            os.close(writable)
        os.close(readonly)
    if not _generation_tombstone_matches(
        _read_existing_snapshot_at(directory_fd, quarantine), expected.version
    ):
        raise RuntimeError("provenance generation changed after compaction")


def _remove_generation_tombstone_at(
    directory_fd: int,
    name: str,
    artifact: PublishedArtifact,
    *,
    original: str,
) -> None:
    if not _generation_tombstone_matches(
        _read_existing_snapshot_at(directory_fd, name), artifact
    ):
        raise RuntimeError("provenance tombstone changed before cleanup")
    _generation_cleanup_hook("tombstone_before_unlink", original)
    if not _generation_tombstone_matches(
        _read_existing_snapshot_at(directory_fd, name), artifact
    ):
        raise RuntimeError("provenance tombstone changed during cleanup")
    os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def _remove_generation_snapshot_at(
    directory_fd: int,
    name: str,
    expected: PointerFileSnapshot,
    *,
    hook_stage: str,
    before_rename: Any | None = None,
    after_rename: Any | None = None,
) -> None:
    def require_safe_after_rename() -> None:
        if after_rename is None:
            return
        try:
            after_rename()
        except BaseException:
            observed = _read_existing_snapshot_at(directory_fd, quarantine)
            if not _generation_snapshot_identity_matches(observed, expected):
                raise RuntimeError(
                    "provenance generation changed before rollback"
                )
            _rename_noreplace_at(directory_fd, quarantine, name)
            raise

    quarantine = _generation_quarantine_name(name, expected)
    _generation_cleanup_hook(hook_stage, name)
    if before_rename is not None:
        before_rename()
    _rename_noreplace_at(directory_fd, name, quarantine)
    after_stage = hook_stage.replace("_before_remove", "_after_quarantine")
    _generation_cleanup_hook(after_stage, name)
    require_safe_after_rename()
    observed = _read_existing_snapshot_at(directory_fd, quarantine)
    if not _generation_snapshot_identity_matches(observed, expected):
        raise RuntimeError("provenance generation changed during cleanup")
    _generation_cleanup_hook("generation_after_validation", name)
    require_safe_after_rename()
    if not _generation_snapshot_identity_matches(
        _read_existing_snapshot_at(directory_fd, quarantine), expected
    ):
        raise RuntimeError("provenance generation changed after cleanup")
    _compact_generation_quarantine_at(directory_fd, quarantine, expected)
    _remove_generation_tombstone_at(
        directory_fd,
        quarantine,
        expected.version,
        original=name,
    )


def _recover_generation_quarantines_at(
    generation_fd: int, generation: str
) -> None:
    for name in os.listdir(generation_fd):
        match = GENERATION_QUARANTINE.fullmatch(name)
        if match is None or match.group(1) != generation:
            continue
        _generation, suffix, device, inode, digest = match.groups()
        original = f"{generation}.{suffix}"
        if _read_existing_snapshot_at(generation_fd, original) is not None:
            raise RuntimeError("provenance generation recovery is ambiguous")
        snapshot = _read_existing_snapshot_at(generation_fd, name)
        artifact = PublishedArtifact(int(device), int(inode), digest)
        if snapshot is None:
            raise RuntimeError("provenance generation changed during recovery")
        if snapshot.version == artifact:
            _compact_generation_quarantine_at(generation_fd, name, snapshot)
        elif _generation_quarantine_inode_is_owned(snapshot, artifact):
            _compact_generation_quarantine_at(
                generation_fd,
                name,
                PointerFileSnapshot(snapshot.content, artifact, snapshot.identity),
            )
        elif not _generation_tombstone_matches(snapshot, artifact):
            raise RuntimeError("provenance generation changed during recovery")
        _generation_cleanup_hook("pending_quarantine_after_validation", original)
        _remove_generation_tombstone_at(
            generation_fd,
            name,
            artifact,
            original=original,
        )


def _require_pending_generation_uncommitted_at(
    directory_fd: int,
    jsonl_name: str,
    generation: str,
    expected_pointer: PointerFileSnapshot | None,
) -> None:
    current = _read_pair_pointer_snapshot_at(
        directory_fd, _pair_pointer_name(jsonl_name)
    )
    if current != expected_pointer:
        if (
            current is not None
            and _parse_pair_pointer(current.content).generation == generation
        ):
            raise RuntimeError("provenance pending generation is committed")
        raise RuntimeError("provenance pair pointer changed during recovery")


def _recover_pending_generation_at(directory_fd: int, jsonl_name: str) -> None:
    pending_name = _pending_generation_name(jsonl_name)
    pending = _read_pair_pointer_snapshot_at(directory_fd, pending_name)
    if pending is None:
        return
    generation, owned_artifacts = _parse_pending_generation(pending.content)
    pointer_snapshot = _read_pair_pointer_snapshot_at(
        directory_fd, _pair_pointer_name(jsonl_name)
    )
    if (
        pointer_snapshot is not None
        and _parse_pair_pointer(pointer_snapshot.content).generation == generation
    ):
        raise RuntimeError("provenance pending generation is committed")
    generation_fd = _open_generation_directory(directory_fd, jsonl_name, create=False)
    removed = False
    try:
        _recover_generation_quarantines_at(generation_fd, generation)
        snapshots = {}
        for suffix in ("jsonl", "txt"):
            name = f"{generation}.{suffix}"
            snapshot = _read_existing_snapshot_at(generation_fd, name)
            owned = owned_artifacts[suffix]
            if snapshot is not None:
                ownership_matches = (
                    isinstance(owned, PublishedArtifact)
                    and snapshot.version == owned
                ) or (
                    isinstance(owned, PlannedArtifact)
                    and hmac.compare_digest(snapshot.version.digest, owned.digest)
                )
                if (
                    not ownership_matches
                    or stat.S_IMODE(snapshot.identity[2]) != 0o400
                    or snapshot.identity[6] != os.geteuid()
                    or snapshot.identity[7] != 1
                ):
                    raise RuntimeError("provenance pending generation is invalid")
            snapshots[name] = snapshot
        for name, snapshot in snapshots.items():
            if snapshot is not None:
                _remove_generation_snapshot_at(
                    generation_fd,
                    name,
                    snapshot,
                    hook_stage="pending_before_remove",
                    before_rename=lambda: _require_pending_generation_uncommitted_at(
                        directory_fd,
                        jsonl_name,
                        generation,
                        pointer_snapshot,
                    ),
                    after_rename=lambda: _require_pending_generation_uncommitted_at(
                        directory_fd,
                        jsonl_name,
                        generation,
                        pointer_snapshot,
                    ),
                )
                removed = True
        if removed:
            os.fsync(generation_fd)
    finally:
        os.close(generation_fd)
    if _read_pair_pointer_snapshot_at(directory_fd, pending_name) != pending:
        raise RuntimeError("provenance pending generation changed")
    _require_pending_generation_uncommitted_at(
        directory_fd, jsonl_name, generation, pointer_snapshot
    )
    _unlink_if_exists_at(directory_fd, pending_name)


def _cleanup_generations_at(
    directory_fd: int, jsonl_name: str, keep_generations: set[str]
) -> None:
    generation_fd = _open_generation_directory(directory_fd, jsonl_name)
    try:
        names = os.listdir(generation_fd)
        generation_file_count = 0
        inventory: dict[str, set[str]] = {}
        for name in names:
            match = GENERATION_FILE.fullmatch(name)
            if match is None:
                quarantine = GENERATION_QUARANTINE.fullmatch(name)
                if quarantine is None:
                    raise RuntimeError("provenance generation inventory is invalid")
                _generation, _suffix, device, inode, digest = quarantine.groups()
                snapshot = _read_existing_snapshot_at(generation_fd, name)
                if not _generation_tombstone_matches(
                    snapshot, PublishedArtifact(int(device), int(inode), digest)
                ):
                    raise RuntimeError("provenance generation inventory is invalid")
                continue
            generation_file_count += 1
            generation, suffix = name.rsplit(".", 1)
            inventory.setdefault(generation, set()).add(suffix)
        if generation_file_count > _MAX_GENERATION_FILES:
            raise RuntimeError("provenance generation inventory is too large")
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
            owned = {}
            snapshots = {}
            for suffix in ("jsonl", "txt"):
                name = f"{generation}.{suffix}"
                snapshot = _read_existing_snapshot_at(generation_fd, name)
                if snapshot is None:
                    raise RuntimeError("provenance generation pair changed")
                snapshots[suffix] = snapshot
                owned[suffix] = snapshot.version
            _atomic_replace_at(
                directory_fd,
                _pending_generation_name(jsonl_name),
                _render_pending_generation(generation, owned),
            )
            for suffix in ("jsonl", "txt"):
                name = f"{generation}.{suffix}"
                _remove_generation_snapshot_at(
                    generation_fd,
                    name,
                    snapshots[suffix],
                    hook_stage="stale_before_remove",
                )
            os.fsync(generation_fd)
            _unlink_if_exists_at(directory_fd, _pending_generation_name(jsonl_name))
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


def _generation_cleanup_hook(stage: str, name: str) -> None:
    del stage, name


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
        _recover_pending_generation_at(parent.descriptor, provenance.name)
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
        _cleanup_generations_at(
            parent.descriptor,
            provenance.name,
            set() if old_pair.pointer is None else {old_pair.pointer.generation},
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
    _process_runner: Any | None = None,
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
    pending_path = provenance.with_name(_pending_generation_name(provenance.name))
    control_paths = {
        provenance,
        provenance.with_suffix(".txt"),
        provenance.with_name(f".{provenance.name}.lock"),
        pointer_path,
        generation_path,
        pending_path,
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
    pre_cancelled = cancel_event is not None and cancel_event.is_set()
    existing_output_parents = {
        parent
        for output in spec.outputs
        for parent in output.parents
        if parent != root and _inside(root, parent) and parent.exists()
    }
    inputs, output_parents = _prepare_paths(
        spec, root, materialize_outputs=not pre_cancelled
    )
    try:
        bound_cwd = _open_bound_working_directory(root, spec.cwd)
    except OSError as exc:
        raise RuntimeError("cwd is invalid") from exc
    try:
        try:
            argument_artifacts = (
                BoundArgumentArtifacts(spec.argv, (), MappingProxyType({}))
                if pre_cancelled
                else _bind_argument_artifacts(spec, root, inputs)
            )
            relative_script = _relative_python_script(spec.argv)
            closure_manifest = None
            closure_digest = None
            if not pre_cancelled and relative_script is not None:
                closure_manifest = _load_runtime_closure_manifest(
                    str(spec.candidate_identity.get("gitSha", ""))
                )
                closure_digest = hashlib.sha256(
                    (json.dumps(closure_manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
                ).hexdigest()
            canonical_spec = _canonical_spec(spec, root, closure_digest)
            spec_digest = _digest(canonical_spec)
            child_env = _child_environment(spec, env)
            started = _utc_now()
            interrupted = False
            if pre_cancelled:
                exit_code, classification, satisfied = None, "cancelled", False
            else:
                expected_git_sha = str(spec.candidate_identity.get("gitSha", ""))
                executable_manifest = _parse_python_executable_manifest(
                    _load_trusted_python_executable_manifest(expected_git_sha)
                )
                executable_content = _trusted_executable_content(
                    spec.argv[0], executable_manifest
                )
                trusted_executable_path = Path(spec.argv[0]).resolve(strict=True)
                if _before_spawn is not None:
                    _before_spawn()
                _require_working_directory_unchanged(bound_cwd, require_ctime=True)
                with _candidate_execution_directory() as snapshot_root:
                    executable_path = _write_python_executable_snapshot(
                        snapshot_root,
                        "command-python",
                        str(trusted_executable_path),
                        executable_content,
                    )
                    runtime_argv = argument_artifacts.argv
                    if relative_script is not None:
                        script_relative, script_index = relative_script
                        assert closure_manifest is not None
                        snapshot_tree = snapshot_root / "candidate-source"
                        source_archive = _load_candidate_resource_archive(
                            expected_git_sha, closure_manifest
                        )
                        _materialize_candidate_resources(
                            source_archive, snapshot_tree, closure_manifest
                        )
                        source_root = _candidate_snapshot_project_root(
                            snapshot_tree, closure_manifest, script_relative
                        )
                        runtime_argv = _resolve_candidate_resource_arguments(
                            spec,
                            runtime_argv,
                            script_index=script_index,
                            snapshot_tree=snapshot_tree,
                            source_root=source_root,
                            manifest=closure_manifest,
                        )
                        bootstrap_path = _write_executable_snapshot(
                            snapshot_root,
                            "candidate-bootstrap.py",
                            _GIT_SOURCE_EXEC.encode(),
                        )
                        dependency_roots = _materialize_distribution_closure(
                            closure_manifest, snapshot_root / "dependencies"
                        )
                        runtime_argv = _git_bound_python_argv(
                            spec,
                            runtime_argv,
                            source_root,
                            bootstrap_path,
                            script_relative,
                            script_index,
                            dependency_roots,
                        )
                    executable_snapshot = executable_path.stat()
                    if cancel_event is not None and cancel_event.is_set():
                        exit_code, classification, satisfied = None, "cancelled", False
                    else:
                        try:
                            process_runner = _run_process if _process_runner is None else _process_runner
                            exit_code, classification, satisfied = process_runner(
                                spec,
                                child_env,
                                stdin_bytes,
                                cancel_event,
                                bound_cwd.descriptor,
                                argument_artifacts,
                                executable_path,
                                executable_path,
                                runtime_argv,
                            )
                        except KeyboardInterrupt:
                            exit_code, classification, satisfied = (
                                None,
                                "keyboard_interrupt",
                                False,
                            )
                            interrupted = True
                    if closure_manifest is not None:
                        _materialize_distribution_closure(
                            closure_manifest, snapshot_root / "dependencies-recheck"
                        )
                    current_snapshot = executable_path.stat()
                    if (
                        (
                            current_snapshot.st_dev,
                            current_snapshot.st_ino,
                            current_snapshot.st_size,
                            current_snapshot.st_mtime_ns,
                            current_snapshot.st_mode,
                        )
                        != (
                            executable_snapshot.st_dev,
                            executable_snapshot.st_ino,
                            executable_snapshot.st_size,
                            executable_snapshot.st_mtime_ns,
                            executable_snapshot.st_mode,
                        )
                        or executable_path.read_bytes() != executable_content
                    ):
                        raise RuntimeError("spawn executable changed")
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
    if not satisfied and output_parents:
        _cleanup_declared_outputs(root, spec.outputs, output_parents)
        _remove_new_output_parents(root, spec.outputs, existing_output_parents)
    if satisfied:
        output_rows, output_bindings = _artifact_rows(root, spec.outputs)
    else:
        output_rows, output_bindings = [], {}
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
    try:
        _commit_entry(provenance, entry)
    except BaseException:
        _cleanup_bound_outputs(root, output_bindings, output_parents)
        raise
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
