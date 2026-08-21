#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

repo_root="${TBOT_REPRO_REPO_ROOT:-$(git rev-parse --show-toplevel)}"
server_root="$repo_root/main/tbot-server"
python_bin="${TBOT_REPRO_PYTHON:-$(command -v python3)}"

if [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found for the T54 SD-sync probe" >&2
  exit 2
fi

cd "$server_root"
PYTHONPATH="$server_root" "$python_bin" - <<'PY'
import asyncio
from unittest.mock import patch

from core.lesson import runtime as runtime_module
from tests import test_lesson_republish_on_connect as helpers


async def main():
    conn = helpers._FakeConn(busy=False)
    sync_release = asyncio.Event()
    preload_seen = asyncio.Event()
    order = []

    async def cached_sd_sync():
        order.append("sync_started")
        await sync_release.wait()
        order.append("sync_finished")

    async def record_preload(_runtime):
        order.append("preloaded")
        preload_seen.set()
        return True

    async def record_start(_runtime, *, preloaded=False):
        order.append("started")

    conn.sd_pack_sync_task = asyncio.create_task(cached_sd_sync())
    case = helpers.RepublishOnConnectTest(methodName="runTest")
    patches = case._patches(
        assignment=helpers._assignment(lesson_version=4, assignment_version=2),
        manifest=helpers._manifest(),
        etag=helpers.ETAG_V2,
    )
    for active_patch in patches:
        active_patch.start()

    start_task = None
    try:
        with patch.object(
            helpers._FakeNewRuntime, "preload_only", new=record_preload
        ), patch.object(
            helpers._FakeNewRuntime, "start_protocol", new=record_start
        ):
            start_task = asyncio.create_task(
                runtime_module._maybe_start_lesson_on_connect_impl(conn)
            )
            await asyncio.wait_for(preload_seen.wait(), timeout=1.0)
            assert "started" not in order, (
                "lesson protocol started while cached SD sync was active: "
                f"{order!r}"
            )
            sync_release.set()
            result = await asyncio.wait_for(start_task, timeout=1.0)
    finally:
        sync_release.set()
        if start_task is not None and not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)
        for active_patch in reversed(patches):
            active_patch.stop()

    assert order.index("sync_finished") < order.index("started"), order
    assert conn.lesson_runtime is result
    print("T54 power-cycle SD sync ordering: PASS")


asyncio.run(asyncio.wait_for(main(), timeout=5.0))
PY
