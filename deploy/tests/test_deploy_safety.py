from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*args: str | Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def make_release(root: Path, tag: str = "fixture") -> Path:
    release = root / tag
    release.mkdir(parents=True)
    shutil.copy2(DEPLOY_DIR / "docker-compose.prod.yml", release / "docker-compose.prod.yml")
    shutil.copy2(DEPLOY_DIR / "backup-db.sh", release / "backup-db.sh")
    shutil.copy2(DEPLOY_DIR / "validate-env.py", release / "validate-env.py")
    shutil.copy2(DEPLOY_DIR / "server-only-remote.sh", release / "server-only-remote.sh")
    (release / f"tbot-server-{tag}.tar.gz").write_bytes(b"fixture-server-image")
    (release / "server-image.ref").write_text(f"local/tbot-server:{tag}\n", encoding="utf-8")
    subprocess.run(
        ["shasum", "-a", "256", f"tbot-server-{tag}.tar.gz"],
        cwd=release,
        text=True,
        stdout=(release / "checksums.sha256").open("w", encoding="utf-8"),
        check=True,
    )
    (release / "release.json").write_text(
        json.dumps(
            {
                "tag": tag,
                "mode": "server-only",
                "images": {"server": f"local/tbot-server:{tag}"},
                "artifacts": {"server": {"file": f"tbot-server-{tag}.tar.gz"}},
            }
        ),
        encoding="utf-8",
    )
    return release


def make_fake_bin(
    tmp_path: Path, *, changed_web_id: bool = False, low_space: bool = False, cleanup_needed: bool = True
) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    log.touch()
    state = tmp_path / "state"
    state.write_text("before", encoding="utf-8")

    executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {log!s}
state=$(cat {state!s})
if [[ "$1 ${{2:-}}" == "compose version" ]]; then exit 0; fi
if [[ "$1" == "inspect" && "${{2:-}}" == "--format" ]]; then
  target="${{4:-}}"
  case "$target" in
    tbot-esp32-server-db) echo db-id ;;
    tbot-esp32-server-web) [[ "$state" == after && "{int(changed_web_id)}" == 1 ]] && echo web-id-new || echo web-id ;;
    server-a) echo sha256:active ;;
    server-b) echo sha256:active-secondary ;;
    *) exit 1 ;;
  esac
  exit 0
fi
if [[ "$1 ${{2:-}}" == "image ls" ]]; then
  printf '%s\\n' 'sha256:active 2026-08-20T00:00:00Z' 'sha256:active-secondary 2026-08-20T00:00:00Z' 'sha256:rollback 2026-08-19T00:00:00Z' 'sha256:old 2026-08-18T00:00:00Z'
  exit 0
fi
if [[ "$1 ${{2:-}}" == "ps -aq" ]]; then
  [[ "$*" == *sha256:old* ]] && exit 0
  echo used-container
  exit 0
fi
if [[ "$1 ${{2:-}}" == "image rm" ]]; then touch {tmp_path!s}/cleaned; exit 0; fi
if [[ "$1" == "load" ]]; then cat >/dev/null; echo 'Loaded image: local/tbot-server:fixture'; exit 0; fi
if [[ "$1" == "compose" && "$*" == *' up -d --no-deps tbot-esp32-server'* ]]; then
  echo after > {state!s}
  exit 0
fi
if [[ "$1" == "compose" && "$*" == *' ps -q tbot-esp32-server'* ]]; then printf '%s\\n' server-a server-b; exit 0; fi
if [[ "$1" == "compose" && "$*" == *' ps tbot-esp32-server'* ]]; then exit 0; fi
echo "unexpected docker command: $*" >&2
exit 91
""",
    )
    executable(
        fake_bin / "df",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {log!s}
if [[ "{int(low_space)}" == 1 ]]; then
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/fixture 100000 98976 1024 99%% /fixture\\n'
elif [[ "{int(cleanup_needed)}" == 1 && ! -f {tmp_path!s}/cleaned ]]; then
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/fixture 100000 98976 1024 99%% /fixture\\n'
else
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/fixture 20000000 9514240 10485760 48%% /fixture\\n'
fi
""",
    )
    executable(fake_bin / "sha256sum", "#!/usr/bin/env bash\nset -euo pipefail\n[[ \"$1\" == -c ]]\nexec shasum -a 256 -c \"$2\"\n")
    executable(fake_bin / "gunzip", "#!/usr/bin/env bash\nset -euo pipefail\ncat \"$2\"\n")
    executable(
        fake_bin / "backup-db.sh",
        f"#!/usr/bin/env bash\nprintf '%s\\n' backup >> {log!s}\n",
    )
    return fake_bin, log


def remote_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    stale_release = tmp_path / "out" / "fixture"
    stale_release.mkdir(parents=True)
    (stale_release / "tbot-server-web-fixture.tar.gz").write_bytes(b"stale")
    env["TBOT_BACKUP_COMMAND"] = str(fake_bin / "backup-db.sh")
    env["TBOT_DEPLOY_SKIP_HEALTH_WAIT"] = "1"
    return env


def test_env_validator_accepts_quoted_and_multiline_values() -> None:
    result = run("python3", DEPLOY_DIR / "validate-env.py", FIXTURES / "valid.env")
    assert result.returncode == 0, result.stderr
    assert "5 assignments" in result.stdout


def test_env_validator_rejects_bare_trailing_token_without_printing_value() -> None:
    result = run("python3", DEPLOY_DIR / "validate-env.py", FIXTURES / "invalid-bare-token.env")
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "line 2" in combined
    assert "PUBLIC_LABEL" in combined
    assert "ESP PUBLIC endpoint" not in combined


def test_env_validator_accepts_shell_operators_inside_single_quoted_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN='fixture;&|<>$value'\n", encoding="utf-8")
    result = run("python3", DEPLOY_DIR / "validate-env.py", env_file)
    assert result.returncode == 0, result.stderr
    assert "fixture;&|<>$value" not in result.stdout


def test_env_validator_rejects_double_quoted_expansion_without_echoing_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('TOKEN="fixture-${EXPANSION}"\n', encoding="utf-8")
    result = run("python3", DEPLOY_DIR / "validate-env.py", env_file)
    assert result.returncode != 0
    assert "TOKEN" in result.stderr
    assert "fixture-${EXPANSION}" not in result.stderr


@pytest.mark.parametrize(
    "payload",
    ["TOKEN=$(whoami)\n", "TOKEN=`whoami`\n", "TOKEN=$HOME\n", 'TOKEN="$HOME"\n', "export TOKEN=value\n"],
)
def test_env_validator_rejects_executable_shell_syntax(tmp_path: Path, payload: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(payload, encoding="utf-8")
    result = run("python3", DEPLOY_DIR / "validate-env.py", env_file)
    assert result.returncode != 0
    assert payload.strip() not in result.stderr


def test_server_only_package_contains_reviewed_operations_helpers(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable(
        fake_bin / "docker",
        "#!/usr/bin/env bash\nset -euo pipefail\n[[ \"$1 $2\" == 'image inspect' ]] && exit 0\n[[ \"$1\" == save ]] && printf fixture-image && exit 0\nexit 9\n",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = run(
        "bash",
        DEPLOY_DIR / "package-release.sh",
        "--tag",
        "fixture",
        "--server-only",
        "--out-dir",
        tmp_path / "out",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    release = tmp_path / "out" / "fixture"
    assert {"backup-db.sh", "validate-env.py", "server-only-remote.sh"} <= {p.name for p in release.iterdir()}
    assert not list(release.glob("tbot-server-web-*.tar.gz"))
    assert json.loads((release / "release.json").read_text(encoding="utf-8"))["mode"] == "server-only"
    checksums = (release / "checksums.sha256").read_text(encoding="utf-8")
    assert "backup-db.sh" in checksums
    assert "validate-env.py" in checksums
    assert "server-only-remote.sh" in checksums
    assert "server-image.ref" in checksums


def test_full_stack_package_remains_valid_json_with_web_artifact(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable(
        fake_bin / "docker",
        "#!/usr/bin/env bash\nset -euo pipefail\n[[ \"$1 $2\" == 'image inspect' ]] && exit 0\n[[ \"$1\" == save ]] && printf fixture-image && exit 0\nexit 9\n",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = run(
        "bash",
        DEPLOY_DIR / "package-release.sh",
        "--tag",
        "fixture",
        "--out-dir",
        tmp_path / "out",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "out" / "fixture" / "release.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "full-stack"
    assert manifest["artifacts"]["web"]["file"] == "tbot-server-web-fixture.tar.gz"


def test_backup_keeps_password_out_of_host_command_and_output(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "docker.log"
    executable(
        fake_bin / "docker",
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > {log!s}\nprintf fixture-dump\n",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["MYSQL_ROOT_PASSWORD"] = "host-secret-must-not-appear"
    env["TBOT_BACKUP_DIR"] = str(tmp_path / "backups")
    result = run("bash", DEPLOY_DIR / "backup-db.sh", env=env)
    combined = result.stdout + result.stderr + log.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "host-secret-must-not-appear" not in combined
    assert 'password=${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}' in combined


def test_remote_transaction_preserves_active_and_one_rollback_and_protected_ids(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "NODE_ENV=production\nTBOT_SERVER_IMAGE=local/tbot-server:fixture\nPUBLIC_LABEL='ESP PUBLIC endpoint'\n",
        encoding="utf-8",
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, log = make_fake_bin(tmp_path)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        "--min-free-bytes",
        "1048576",
        "--min-free-percent",
        "5",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8")
    assert "image rm sha256:old" in commands
    assert "image rm sha256:active" not in commands
    assert "image rm sha256:active-secondary" not in commands
    assert "image rm sha256:rollback" not in commands
    assert "up -d --no-deps tbot-esp32-server" in commands
    assert "up -d tbot-esp32-server-db" not in commands
    assert "up -d tbot-esp32-server-web" not in commands
    assert "protected container IDs unchanged" in result.stdout


def test_remote_transaction_fails_when_web_id_changes(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "NODE_ENV=production\nTBOT_SERVER_IMAGE=local/tbot-server:fixture\n", encoding="utf-8"
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, _ = make_fake_bin(tmp_path, changed_web_id=True)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        "--min-free-bytes",
        "1048576",
        "--min-free-percent",
        "5",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode != 0
    assert "web container ID changed" in result.stderr


def test_remote_transaction_skips_cleanup_when_thresholds_already_pass(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "NODE_ENV=production\nTBOT_SERVER_IMAGE=local/tbot-server:fixture\n", encoding="utf-8"
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, log = make_fake_bin(tmp_path, cleanup_needed=False)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        "--min-free-bytes",
        "1048576",
        "--min-free-percent",
        "5",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode == 0, result.stderr
    assert "image rm" not in log.read_text(encoding="utf-8")


def test_remote_transaction_rejects_invalid_env_before_docker_mutation(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "TBOT_SERVER_IMAGE=local/tbot-server:fixture\nPUBLIC_LABEL=ESP PUBLIC endpoint\n", encoding="utf-8"
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, log = make_fake_bin(tmp_path)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode != 0
    assert "PUBLIC_LABEL" in result.stderr
    assert "ESP PUBLIC endpoint" not in result.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_remote_transaction_refuses_low_space_before_backup_or_image_load(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "NODE_ENV=production\nTBOT_SERVER_IMAGE=local/tbot-server:fixture\n", encoding="utf-8"
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, log = make_fake_bin(tmp_path, low_space=True)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        "--min-free-bytes",
        "1048576",
        "--min-free-percent",
        "5",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode != 0
    commands = log.read_text(encoding="utf-8")
    assert "backup" not in commands
    assert "load" not in commands


def test_remote_transaction_rejects_server_image_tag_mismatch_before_mutation(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    release = make_release(remote_root / "releases")
    (remote_root / ".env").write_text(
        "NODE_ENV=production\nTBOT_SERVER_IMAGE=local/tbot-server:older\n", encoding="utf-8"
    )
    (remote_root / "current").symlink_to(release)
    fake_bin, log = make_fake_bin(tmp_path)
    result = run(
        "bash",
        release / "server-only-remote.sh",
        "--remote-root",
        remote_root,
        "--release-dir",
        release,
        "--tag",
        "fixture",
        env=remote_env(tmp_path, fake_bin),
    )
    assert result.returncode != 0
    assert "TBOT_SERVER_IMAGE does not match" in result.stderr
    assert "older" not in result.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_deploy_dry_run_is_server_only_and_does_not_execute_transport(tmp_path: Path) -> None:
    release = make_release(tmp_path / "releases")
    result = run(
        "bash",
        DEPLOY_DIR / "deploy-vps.sh",
        "--host",
        "fixture.invalid",
        "--user",
        "fixture",
        "--tag",
        "fixture",
        "--release-root",
        release.parent,
        "--server-only",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert "server-only-remote.sh" in result.stdout
    assert "--no-deps" in result.stdout
    assert "skip smoke checks" in result.stdout
    assert result.stdout.index("stream") < result.stdout.index("mkdir\\ -p")


def test_deploy_rejects_invalid_candidate_env_before_transport(tmp_path: Path) -> None:
    release = make_release(tmp_path / "releases")
    env_file = tmp_path / "candidate.env"
    env_file.write_text("PUBLIC_LABEL=ESP PUBLIC endpoint\n", encoding="utf-8")
    result = run(
        "bash",
        DEPLOY_DIR / "deploy-vps.sh",
        "--host",
        "fixture.invalid",
        "--user",
        "fixture",
        "--tag",
        "fixture",
        "--release-root",
        release.parent,
        "--env-file",
        env_file,
        "--server-only",
        "--dry-run",
    )
    assert result.returncode != 0
    assert "PUBLIC_LABEL" in result.stderr
    assert "ESP PUBLIC endpoint" not in result.stderr
    assert "ssh" not in result.stdout
    assert "scp" not in result.stdout


def test_rollback_dry_run_is_secret_safe_and_server_only(tmp_path: Path) -> None:
    env_file = tmp_path / "rollback.env"
    secret = "rollback-secret-must-not-appear"
    env_file.write_text(
        "TBOT_REMOTE_ROOT=/opt/tbot\n"
        "TBOT_SERVER_IMAGE=local/tbot-server:previous\n"
        f"MYSQL_ROOT_PASSWORD='{secret}'\n",
        encoding="utf-8",
    )

    result = run(
        "bash",
        DEPLOY_DIR / "rollback-vps.sh",
        "--host",
        "fixture.invalid",
        "--user",
        "fixture",
        "--tag",
        "previous",
        "--env-file",
        env_file,
        "--server-only",
        "--dry-run",
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert "validate-env.py" in result.stdout
    assert "up -d --no-deps tbot-esp32-server" in result.stdout
    assert "up -d tbot-esp32-server-db" not in result.stdout
    assert "up -d tbot-esp32-server-web" not in result.stdout
    assert secret not in combined


def test_rollback_rejects_invalid_env_before_transport_without_echoing_value(tmp_path: Path) -> None:
    env_file = tmp_path / "rollback.env"
    env_file.write_text(
        "TBOT_REMOTE_ROOT=/opt/tbot\nPUBLIC_LABEL=ESP PUBLIC endpoint\n",
        encoding="utf-8",
    )

    result = run(
        "bash",
        DEPLOY_DIR / "rollback-vps.sh",
        "--host",
        "fixture.invalid",
        "--user",
        "fixture",
        "--tag",
        "previous",
        "--env-file",
        env_file,
        "--server-only",
        "--dry-run",
    )

    assert result.returncode != 0
    assert "PUBLIC_LABEL" in result.stderr
    assert "ESP PUBLIC endpoint" not in result.stderr
    assert "ssh" not in result.stdout


def test_rollback_executes_only_target_release_server_image(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    target = remote_root / "releases" / "previous"
    current_release = remote_root / "releases" / "current"
    target.mkdir(parents=True)
    current_release.mkdir(parents=True)
    shutil.copy2(DEPLOY_DIR / "validate-env.py", target / "validate-env.py")
    (target / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (target / "server-image.ref").write_text(
        "local/tbot-server:previous\n", encoding="utf-8"
    )
    env_backup = remote_root / ".env.rollback-20260821-current"
    env_backup.write_text(
        f"TBOT_REMOTE_ROOT={remote_root}\nTBOT_SERVER_IMAGE=local/tbot-server:previous\n",
        encoding="utf-8",
    )
    (current_release / "env-backup-path").write_text(f"{env_backup}\n", encoding="utf-8")
    (remote_root / "current").symlink_to(current_release)
    local_env = tmp_path / "rollback.env"
    local_env.write_text(f"TBOT_REMOTE_ROOT={remote_root}\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    executable(
        fake_bin / "ssh",
        "#!/usr/bin/env bash\nset -euo pipefail\ncommand=${!#}\nexec bash -c \"$command\"\n",
    )
    executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {docker_log!s}
if [[ "$1" == inspect && "${{4:-}}" == tbot-esp32-server-db ]]; then echo db-id; exit 0; fi
if [[ "$1" == inspect && "${{4:-}}" == tbot-esp32-server-web ]]; then echo web-id; exit 0; fi
if [[ "$1" == compose && "$*" == *' up -d --no-deps tbot-esp32-server'* ]]; then exit 0; fi
exit 91
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = run(
        "bash",
        DEPLOY_DIR / "rollback-vps.sh",
        "--host",
        "fixture.invalid",
        "--user",
        "fixture",
        "--tag",
        "previous",
        "--env-file",
        local_env,
        "--server-only",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert (remote_root / "current").resolve() == target.resolve()
    commands = docker_log.read_text(encoding="utf-8")
    assert "up -d --no-deps tbot-esp32-server" in commands
    assert "tbot-esp32-server-db" not in next(
        line for line in commands.splitlines() if " up " in f" {line} "
    )
