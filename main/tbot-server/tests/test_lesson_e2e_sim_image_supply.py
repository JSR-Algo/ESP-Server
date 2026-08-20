import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UP_SCRIPT = ROOT / "docs" / "docker" / "lesson-e2e-sim" / "up.sh"


def _run_up_with_fake_docker(
    tmp_path: Path,
    *,
    base_override: str | None = None,
    corrupt_redis_aof: bool = False,
):
    log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "if [[ \"$1 $2\" == 'image inspect' ]]; then exit 1; fi\n"
        "if [[ \"$1\" == 'build' ]]; then exit 0; fi\n"
        "if [[ \"$1\" == 'compose' ]]; then\n"
        "  [[ \"$*\" == *' rm -sf redis' ]] && exit 0\n"
        "  if [[ \"${FAKE_CORRUPT_REDIS_AOF:-0}\" == '1' && \"$*\" == *' up -d '* ]]; then\n"
        "    count_file=\"${FAKE_DOCKER_LOG}.compose-count\"\n"
        "    count=0; [[ ! -f \"$count_file\" ]] || count=$(cat \"$count_file\")\n"
        "    count=$((count + 1)); printf '%s' \"$count\" > \"$count_file\"\n"
        "    [[ \"$count\" -gt 1 ]] && exit 79\n"
        "  fi\n"
        "  exit 79\n"
        "fi\n"
        "if [[ \"$1 $2\" == 'logs tbot-ls-e2e-redis' && \"${FAKE_CORRUPT_REDIS_AOF:-0}\" == '1' ]]; then\n"
        "  echo 'Bad file format reading the append only file appendonlydir/appendonly.aof.1.incr.aof'\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1 $2\" == 'volume rm' ]]; then exit 0; fi\n"
        "exit 79\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "JWT_PUBLIC_KEY": "fixture-public-key",
        }
    )
    if base_override is not None:
        env["TBOT_SERVER_BASE_IMAGE"] = base_override
    else:
        env.pop("TBOT_SERVER_BASE_IMAGE", None)
    if corrupt_redis_aof:
        env["FAKE_CORRUPT_REDIS_AOF"] = "1"
    result = subprocess.run(
        [str(UP_SCRIPT), "--rebuild"],
        cwd=UP_SCRIPT.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log.read_text(encoding="utf-8")


def test_default_simulation_builds_checkout_local_base_before_runtime(tmp_path: Path):
    result, commands = _run_up_with_fake_docker(tmp_path)

    assert result.returncode == 79
    base_marker = "-f " + str(ROOT / "Dockerfile-server-base")
    runtime_marker = "-f " + str(ROOT / "Dockerfile-server")
    command_lines = commands.splitlines()
    base_line = next(line for line in command_lines if base_marker in line)
    runtime_line = next(line for line in command_lines if runtime_marker + " " in line)
    assert "local/tbot-server-base:lesson-e2e-sim-" in commands
    assert command_lines.index(base_line) < command_lines.index(runtime_line)
    assert "main-dd48f39d-local-20260805" not in commands
    assert "local/tbot-backend:lesson-studio-e2e" in commands
    assert "local/tbot-server-web:lesson-studio-e2e" in commands
    assert str(ROOT.parents[3] / "tbot-backend" / "Dockerfile") in commands
    assert str(ROOT / "Dockerfile-web") in commands
    assert "WEB_NODE_IMAGE=node:20" in commands


def test_explicit_simulation_base_override_skips_local_base_build(tmp_path: Path):
    result, commands = _run_up_with_fake_docker(
        tmp_path,
        base_override="registry.example/tbot-server-base:approved",
    )

    assert result.returncode == 79
    assert str(ROOT / "Dockerfile-server-base") not in commands
    assert "TBOT_SERVER_BASE_IMAGE=registry.example/tbot-server-base:approved" in commands


def test_corrupt_simulation_redis_aof_is_removed_before_single_retry(tmp_path: Path):
    result, commands = _run_up_with_fake_docker(tmp_path, corrupt_redis_aof=True)

    assert result.returncode == 79
    assert "logs tbot-ls-e2e-redis" in commands
    assert "compose" in commands and " rm -sf redis" in commands
    assert "volume rm tbot-ls-e2e-redis-data" in commands


def test_manager_cache_is_cleared_before_esp_boot_reads_base_config():
    script = UP_SCRIPT.read_text(encoding="utf-8")

    cache_clear = script.index("docker restart tbot-ls-e2e-web")
    esp_start = script.index('echo "[up] starting ESP lesson server"')

    assert cache_clear < esp_start
