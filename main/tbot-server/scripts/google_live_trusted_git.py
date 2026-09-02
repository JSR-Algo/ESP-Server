"""Run read-only Git queries without trusting caller-controlled process state."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitExecutableIdentity:
    path: Path
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int
    sha256: str
    version: str
    parent_chain: tuple[tuple[int, int, int, int, int, int, int], ...]


def _fixed_git_candidates() -> list[Path]:
    return [Path(directory) / "git" for directory in os.get_exec_path({"PATH": os.defpath})]


def _safe_git_environment() -> dict[str, str]:
    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }


def _is_writable_by_current_user(opened: os.stat_result) -> bool:
    if opened.st_mode & stat.S_IWOTH:
        return True
    if (
        opened.st_gid in {*os.getgroups(), os.getegid()}
        and opened.st_mode & stat.S_IWGRP
    ):
        return True
    return opened.st_uid == os.geteuid() and bool(opened.st_mode & stat.S_IWUSR)


def _read_executable(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        content = b""
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            content += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
        after.st_nlink,
    ):
        raise RuntimeError("trusted Git executable changed while being read")
    return content, after


def _trusted_parent_chain(path: Path) -> tuple[tuple[int, int, int, int, int, int, int], ...]:
    if not path.is_absolute():
        raise RuntimeError("trusted Git executable is invalid")
    chain = []
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        opened = current.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(opened.st_mode)
            or _is_writable_by_current_user(opened)
        ):
            raise RuntimeError("trusted Git executable is invalid")
        chain.append(
            (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
                opened.st_mode,
                opened.st_nlink,
            )
        )
    return tuple(chain)


def executable_identity(path: Path) -> GitExecutableIdentity:
    if path.is_symlink():
        raise RuntimeError("trusted Git executable is invalid")
    parent_chain = _trusted_parent_chain(path)
    content, opened = _read_executable(path)
    if not stat.S_ISREG(opened.st_mode) or _is_writable_by_current_user(opened):
        raise RuntimeError("trusted Git executable is invalid")
    completed = subprocess.run(
        _git_command(path, Path("/"), "--version"),
        check=False,
        capture_output=True,
        env=_safe_git_environment(),
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("trusted Git executable is unavailable")
    try:
        version = completed.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("trusted Git executable version is invalid") from exc
    if re.fullmatch(r"git version [0-9][0-9A-Za-z.() -]*", version) is None:
        raise RuntimeError("trusted Git executable version is invalid")
    verified_content, verified = _read_executable(path)
    if (
        verified.st_dev,
        verified.st_ino,
        verified.st_size,
        verified.st_mtime_ns,
        verified.st_ctime_ns,
        verified.st_mode,
        hashlib.sha256(verified_content).hexdigest(),
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        opened.st_mode,
        hashlib.sha256(content).hexdigest(),
    ):
        raise RuntimeError("trusted Git executable changed")
    if _trusted_parent_chain(path) != parent_chain:
        raise RuntimeError("trusted Git executable changed")
    return GitExecutableIdentity(
        path=path,
        device=opened.st_dev,
        inode=opened.st_ino,
        size=opened.st_size,
        modified_ns=opened.st_mtime_ns,
        changed_ns=opened.st_ctime_ns,
        mode=opened.st_mode,
        sha256=hashlib.sha256(content).hexdigest(),
        version=version,
        parent_chain=parent_chain,
    )


def resolve_trusted_git(*, refresh: bool = False) -> GitExecutableIdentity:
    del refresh
    identities = []
    seen = set()
    for candidate in _fixed_git_candidates():
        try:
            identity = executable_identity(candidate)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            continue
        key = (identity.device, identity.inode)
        if key not in seen:
            seen.add(key)
            identities.append(identity)
    if len(identities) != 1:
        raise RuntimeError("trusted Git executable is unavailable or ambiguous")
    return identities[0]


def require_executable_unchanged(identity: GitExecutableIdentity) -> None:
    if _trusted_parent_chain(identity.path) != identity.parent_chain:
        raise RuntimeError("trusted Git executable changed")
    content, opened = _read_executable(identity.path)
    observed = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        opened.st_mode,
        hashlib.sha256(content).hexdigest(),
    )
    expected = (
        identity.device,
        identity.inode,
        identity.size,
        identity.modified_ns,
        identity.changed_ns,
        identity.mode,
        identity.sha256,
    )
    if observed != expected:
        raise RuntimeError("trusted Git executable changed")


def _git_command(executable: Path, repo: Path, *arguments: str) -> list[str]:
    return [
        str(executable),
        "--no-pager",
        "--no-optional-locks",
        "--no-replace-objects",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.excludesFile=/dev/null",
        "-c",
        "diff.external=",
        "-c",
        f"core.worktree={repo}",
        "-c",
        "core.bare=false",
        "-C",
        str(repo),
        *arguments,
    ]


def git_output(repo_root: Path, *arguments: str) -> bytes:
    repo = repo_root.resolve(strict=True)
    identity = resolve_trusted_git()
    require_executable_unchanged(identity)
    command = _git_command(identity.path, repo, *arguments)
    completed = subprocess.run(
        command,
        cwd=repo,
        check=False,
        capture_output=True,
        env=_safe_git_environment(),
        timeout=30,
    )
    require_executable_unchanged(identity)
    if completed.returncode != 0:
        raise RuntimeError("git repository verification failed")
    return completed.stdout
