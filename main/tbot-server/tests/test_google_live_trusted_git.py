import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import google_live_trusted_git as trusted_git
from scripts import google_live_deterministic_evidence as deterministic
from scripts import google_live_release_gate as release_gate


def test_trusted_git_ignores_fake_path_and_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GIT_PAGER",
        "PAGER",
    ):
        monkeypatch.setenv(name, "attacker")

    output = trusted_git.git_output(Path(__file__).parents[1], "rev-parse", "HEAD")

    assert len(output.decode().strip()) == 40
    assert not sentinel.exists()


def test_trusted_git_uses_absolute_binary_pinned_repo_and_sanitized_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/usr/bin/git")
    identity = trusted_git.executable_identity(executable)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(trusted_git, "resolve_trusted_git", lambda: identity)
    monkeypatch.setattr(trusted_git.subprocess, "run", run)
    monkeypatch.setattr(trusted_git, "require_executable_unchanged", lambda _identity: None)

    assert trusted_git.git_output(tmp_path, "status", "--porcelain=v1") == b"ok\n"
    command, kwargs = calls[0]
    assert command[0] == str(executable)
    assert command[-3:] == [str(tmp_path.resolve()), "status", "--porcelain=v1"]
    assert command[:4] == [
        str(executable),
        "--no-pager",
        "--no-optional-locks",
        "--no-replace-objects",
    ]
    assert command[3:-3] == [
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
        f"core.worktree={tmp_path.resolve()}",
        "-c",
        "core.bare=false",
        "-C",
    ]
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["env"]["PATH"] == os.defpath
    assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not any(name in kwargs["env"] for name in ("GIT_DIR", "GIT_WORK_TREE", "HOME"))


def test_trusted_git_rejects_missing_or_user_writable_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git = tmp_path / "git"
    fake_git.write_bytes(b"not trusted")
    fake_git.chmod(0o700)
    monkeypatch.setattr(trusted_git, "_fixed_git_candidates", lambda: [fake_git])

    with pytest.raises(RuntimeError, match="trusted Git executable"):
        trusted_git.resolve_trusted_git(refresh=True)


def test_trusted_git_rejects_executable_in_user_writable_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_git = tmp_path / "git"
    fake_git.write_bytes(b"not trusted")
    fake_git.chmod(0o555)
    monkeypatch.setattr(trusted_git, "_fixed_git_candidates", lambda: [fake_git])
    monkeypatch.setattr(
        trusted_git.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"git version 2.42.0\n", stderr=b""
        ),
    )

    with pytest.raises(RuntimeError, match="trusted Git executable"):
        trusted_git.resolve_trusted_git(refresh=True)


def test_trusted_git_requires_privileged_owner_even_when_mode_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/usr/bin/git")
    monkeypatch.setattr(trusted_git, "_fixed_git_candidates", lambda: [executable])
    monkeypatch.setattr(
        trusted_git,
        "_expected_privileged_uid",
        lambda: executable.stat().st_uid + 1,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="trusted Git executable"):
        trusted_git.resolve_trusted_git(refresh=True)


def test_trusted_git_rejects_effectively_writable_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/usr/bin/git")
    real_access = trusted_git.os.access

    def access(path, mode, **kwargs):
        if Path(path) == executable and mode == os.W_OK:
            return True
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(trusted_git, "_fixed_git_candidates", lambda: [executable])
    monkeypatch.setattr(trusted_git.os, "access", access)

    with pytest.raises(RuntimeError, match="trusted Git executable"):
        trusted_git.resolve_trusted_git(refresh=True)


def test_trusted_git_identity_includes_filesystem_root() -> None:
    identity = trusted_git.executable_identity(Path("/usr/bin/git"))
    root = Path("/").stat()

    assert identity.parent_chain[0][:2] == (root.st_dev, root.st_ino)


def test_trusted_git_session_reuses_one_identity_for_every_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = trusted_git.executable_identity(Path("/usr/bin/git"))
    resolutions = []
    monkeypatch.setattr(
        trusted_git,
        "resolve_trusted_git",
        lambda: resolutions.append(identity) or identity,
    )
    monkeypatch.setattr(
        trusted_git.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"ok\n", stderr=b""
        ),
    )

    with trusted_git.trusted_git_session():
        trusted_git.git_output(tmp_path, "status")
        trusted_git.git_output(tmp_path, "rev-parse", "HEAD")

    assert resolutions == [identity]


@pytest.mark.parametrize("swap", ["executable", "parent"])
def test_trusted_git_session_rejects_identity_swap_between_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    swap: str,
) -> None:
    identity = trusted_git.executable_identity(Path("/usr/bin/git"))
    monkeypatch.setattr(trusted_git, "resolve_trusted_git", lambda: identity)
    monkeypatch.setattr(
        trusted_git.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=b"ok\n", stderr=b""
        ),
    )

    with pytest.raises(RuntimeError, match="trusted Git executable changed"):
        with trusted_git.trusted_git_session():
            trusted_git.git_output(tmp_path, "status")
            if swap == "parent":
                changed = list(identity.parent_chain)
                changed[-1] = (changed[-1][0], changed[-1][1] + 1, *changed[-1][2:])
                monkeypatch.setattr(
                    trusted_git, "_trusted_parent_chain", lambda _path: tuple(changed)
                )
            else:
                real_read = trusted_git._read_executable

                def changed_executable(path):
                    content, opened = real_read(path)
                    values = list(opened)
                    values[1] += 1
                    return content, os.stat_result(values)

                monkeypatch.setattr(trusted_git, "_read_executable", changed_executable)
            trusted_git.git_output(tmp_path, "rev-parse", "HEAD")


def test_trusted_git_disables_repository_replace_refs(tmp_path: Path) -> None:
    executable = "/usr/bin/git"
    repo = tmp_path / "repo"
    repo.mkdir()
    environment = {
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "HOME": str(tmp_path),
        "PATH": os.defpath,
    }
    subprocess.run([executable, "init", "-q", str(repo)], check=True, env=environment)
    original = subprocess.run(
        [executable, "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"original\n",
        capture_output=True,
        check=True,
        env=environment,
    ).stdout.decode().strip()
    replacement = subprocess.run(
        [executable, "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"replacement\n",
        capture_output=True,
        check=True,
        env=environment,
    ).stdout.decode().strip()
    subprocess.run(
        [executable, "-C", str(repo), "replace", original, replacement],
        check=True,
        env=environment,
    )

    observed = trusted_git.git_output(repo, "cat-file", "blob", original)

    assert observed == b"original\n"


def test_git_version_probe_uses_the_same_sanitized_absolute_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/usr/bin/git")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, stdout=b"git version 2.42.0\n", stderr=b""
        )

    monkeypatch.setattr(trusted_git.subprocess, "run", run)

    identity = trusted_git.executable_identity(executable)

    command, kwargs = calls[0]
    assert identity.version == "git version 2.42.0"
    assert command[0] == str(executable)
    assert command[1:4] == [
        "--no-pager",
        "--no-optional-locks",
        "--no-replace-objects",
    ]
    assert command[-3:] == ["-C", str(Path("/").resolve()), "--version"]
    assert kwargs["env"] == trusted_git._safe_git_environment()


@pytest.mark.parametrize(
    "git_output",
    [deterministic._git_output, release_gate._git_output],
)
def test_google_live_git_consumers_ignore_caller_process_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_output,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "consumer-fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker.git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "alias.rev-parse")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "!touch consumer-alias-ran")

    output = git_output(Path(__file__).parents[1], "rev-parse", "HEAD")

    assert len(output.decode().strip()) == 40
    assert not sentinel.exists()


def test_runtime_manifest_generator_is_path_independent_and_byte_identical(
    tmp_path: Path,
) -> None:
    module_root = Path(__file__).parents[1]
    manifest = module_root / "tests/fixtures/google_live_pytest_runtime_manifest.json"
    before = manifest.read_bytes()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sentinel = tmp_path / "generator-fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    fake_git.chmod(0o755)
    environment = dict(os.environ)
    environment["PATH"] = str(fake_bin)
    environment["GIT_DIR"] = str(tmp_path / "attacker.git")

    completed = subprocess.run(
        [sys.executable, str(module_root / "scripts/update_google_live_pytest_runtime_manifest.py")],
        cwd=module_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert manifest.read_bytes() == before
    assert not sentinel.exists()
