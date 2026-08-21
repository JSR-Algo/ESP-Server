import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UP_SCRIPT = ROOT / "docs" / "docker" / "lesson-e2e-sim" / "up.sh"
SIM_DEVICE = ROOT / "docs" / "docker" / "lesson-e2e-sim" / "sim_device.py"
MERGE_TIMELINE = ROOT / "docs" / "docker" / "lesson-e2e-sim" / "merge_timeline.py"


def _tbot_root() -> Path:
    for candidate in (ROOT, *ROOT.parents):
        if (candidate / "tbot-backend").is_dir() and (candidate / "robot").is_dir():
            return candidate
    raise AssertionError("could not locate TBOT workspace root")


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
    assert str(_tbot_root() / "tbot-backend" / "Dockerfile") in commands
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


def test_simulator_keeps_audio_bound_to_the_step_that_started_the_turn():
    script = SIM_DEVICE.read_text(encoding="utf-8")

    assert 'audio["step_id"] = current_step_id' in script
    assert 'audio_step_id = audio.get("step_id")' in script
    assert 'f"{prefix} step_started"' in script


def test_simulator_advertises_and_emits_device_drain_ack_after_playback_evidence():
    script = SIM_DEVICE.read_text(encoding="utf-8")

    assert '"lessonAudioDrainAck": True' in script
    playback = script.index("serial Audio playback complete {context}")
    ack = script.index('"type": "tts_ack"')
    assert playback < ack


def test_simulator_records_lesson_ack_at_send_boundary():
    script = SIM_DEVICE.read_text(encoding="utf-8")

    ack_log = script.index("serial TX lesson_ack {ftype}")
    ack_send = script.index("await client.send(json.dumps(ack))")

    assert ack_log < ack_send


def test_timeline_merge_preserves_same_millisecond_drain_ack_causality(tmp_path: Path):
    server = tmp_path / "server.log"
    device = tmp_path / "device.log"
    merged = tmp_path / "merged.log"
    server.write_text(
        "260821 04:26:08.458[GoogleLive]-INFO-tts_stop_sent\n"
        "260821 04:26:08.458[core.handle]-INFO-Received tts_ack message\n"
        "260821 04:26:08.458[GoogleLive]-INFO-Google Live lesson_prompt_device_drain_ack drain_id=lesson-4\n"
        "260821 04:26:08.458[GoogleLive]-INFO-Google Live lesson_child_response_window_open\n"
        "260821 04:26:08.458[LessonRuntime]-INFO-lesson_progress step_completed stepId=s4\n",
        encoding="utf-8",
    )
    device.write_text(
        "2026-08-21 04:26:08.458 serial Audio playback complete stepId=s4 chunks=58 bytes=9092\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "python3",
            str(MERGE_TIMELINE),
            "--server-log",
            str(server),
            "--device-timeline",
            str(device),
            "--out",
            str(merged),
        ],
        check=True,
    )

    lines = merged.read_text(encoding="utf-8").splitlines()
    stop = next(i for i, line in enumerate(lines) if "tts_stop_sent" in line)
    playback = next(i for i, line in enumerate(lines) if "Audio playback complete" in line)
    received = next(i for i, line in enumerate(lines) if "Received tts_ack" in line)
    accepted = next(i for i, line in enumerate(lines) if "device_drain_ack" in line)
    window = next(i for i, line in enumerate(lines) if "response_window_open" in line)
    progress = next(i for i, line in enumerate(lines) if "step_completed" in line)

    assert stop < playback < received < accepted < window
    assert playback < progress


def test_timeline_merge_preserves_same_millisecond_lesson_start_ack_causality(
    tmp_path: Path,
):
    server = tmp_path / "server.log"
    device = tmp_path / "device.log"
    merged = tmp_path / "merged.log"
    server.write_text(
        "260821 04:32:48.518[LessonRuntime]-INFO-emit lesson_start type=lesson_start sequence=2\n"
        "260821 04:32:48.518[core.handle]-INFO-Received lesson_ack message\n"
        "260821 04:32:48.518[LessonRuntime]-INFO-LessonRuntime event lesson_started\n",
        encoding="utf-8",
    )
    device.write_text(
        "2026-08-21 04:32:48.518 serial RX lesson_start seq=2\n"
        "2026-08-21 04:32:48.518 serial TX lesson_ack lesson_start acks=2 seq=2\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "python3",
            str(MERGE_TIMELINE),
            "--server-log",
            str(server),
            "--device-timeline",
            str(device),
            "--out",
            str(merged),
        ],
        check=True,
    )

    lines = merged.read_text(encoding="utf-8").splitlines()
    emit = next(i for i, line in enumerate(lines) if "emit lesson_start" in line)
    received = next(i for i, line in enumerate(lines) if "serial RX lesson_start" in line)
    sent = next(i for i, line in enumerate(lines) if "serial TX lesson_ack" in line)
    accepted = next(i for i, line in enumerate(lines) if "Received lesson_ack" in line)
    started = next(i for i, line in enumerate(lines) if "event lesson_started" in line)

    assert emit < received < sent < accepted < started
