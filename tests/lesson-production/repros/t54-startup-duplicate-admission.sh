#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
campaign_root="$(CDPATH= cd -- "$script_dir/../.." && pwd -P)"
python_bin="${TBOT_REPRO_PYTHON:-python3}"

if [ ! -x "$python_bin" ]; then
  python_bin="$(command -v python3 || true)"
fi
if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found" >&2
  exit 2
fi

PYTHONPATH="$PWD/main/tbot-server" "$python_bin" - <<'PY'
import asyncio
from pathlib import Path

from plugins_func.functions import start_lesson as start_lesson_module


class Logger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None


class Conn:
    def __init__(self, loop, release):
        self.loop = loop
        self.logger = Logger()
        self.lesson_pull_task = None
        self.device_id = "robot-01"
        self.config = {
            "lesson": {
                "runtime_enabled": True,
                "sample_lesson": False,
                "rollout_device_allowlist": [self.device_id],
            }
        }
        self.release = release
        self.pull_calls = 0

    def _lesson_runtime_enabled(self):
        return True

    async def _lesson_pull_on_connect(self):
        self.pull_calls += 1
        await self.release.wait()
        return object()


async def verify_tool_coalescing():
    release = asyncio.Event()
    conn = Conn(asyncio.get_running_loop(), release)
    start_lesson_module.start_lesson(conn)
    first_task = conn.lesson_pull_task
    await asyncio.sleep(0)
    start_lesson_module.start_lesson(conn)
    assert conn.lesson_pull_task is first_task, "duplicate replaced the active spoken startup"
    assert not first_task.cancelled(), "duplicate cancelled the active spoken startup"
    assert conn.pull_calls == 1, f"expected one startup pull, got {conn.pull_calls}"
    release.set()
    await first_task


asyncio.run(verify_tool_coalescing())

source = Path(
    "main/tbot-server/core/voice/session_provider/google_live.py"
).read_text(encoding="utf-8")
dispatch_start = source.index("async def _dispatch_lesson_start_intent")
dispatch_end = source.index("def _lesson_start_tool_dispatch_scope", dispatch_start)
dispatch = source[dispatch_start:dispatch_end]
pending_gate = dispatch.index("reason=start_pending")
transition = dispatch.index("if not await self.transition_to_lesson_start()")
assert pending_gate < transition, "pending startup is not suppressed before realtime transition"

print("T54 startup duplicate admission: PASS")
PY
