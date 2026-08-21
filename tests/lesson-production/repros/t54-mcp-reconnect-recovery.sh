#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

repo_root="${TBOT_REPRO_REPO_ROOT:-$(git rev-parse --show-toplevel)}"
server_root="$repo_root/main/tbot-server"
python_bin="${TBOT_REPRO_PYTHON:-$(command -v python3)}"

if [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found for the T54 MCP probe" >&2
  exit 2
fi

cd "$server_root"
PYTHONPATH="$server_root" "$python_bin" - <<'PY'
import asyncio
from unittest.mock import patch

from core.lesson import runtime as runtime_module
from tests import test_lesson_republish_on_connect as helpers


class DelayedMcpClient:
    def __init__(self, ready_after):
        self.ready_after = ready_after
        self.ready_checks = 0

    @property
    def ready(self):
        return self.ready_checks >= self.ready_after

    async def is_ready(self):
        self.ready_checks += 1
        return self.ready


async def main():
    conn = helpers._FakeConn(busy=False)
    conn.features["mcp"] = True
    conn.config["lesson"].update(
        {
            "asset_delivery_mode": "sd_pack",
            "mcp_reconnect_ready_timeout_sec": 0.1,
            "mcp_reconnect_ready_poll_sec": 0.001,
        }
    )
    conn.mcp_client = DelayedMcpClient(ready_after=3)

    readiness_at_preload = []
    protocol_starts = []

    async def record_preload(_runtime):
        readiness_at_preload.append(conn.mcp_client.ready_checks)
        return True

    async def record_start(_runtime, *, preloaded=False):
        protocol_starts.append(preloaded)

    case = helpers.RepublishOnConnectTest(methodName="runTest")
    patches = case._patches(
        assignment=helpers._assignment(lesson_version=4, assignment_version=2),
        manifest=helpers._manifest(),
        etag=helpers.ETAG_V2,
    )
    for active_patch in patches:
        active_patch.start()
    try:
        with patch.object(
            helpers._FakeNewRuntime, "preload_only", new=record_preload
        ), patch.object(
            helpers._FakeNewRuntime, "start_protocol", new=record_start
        ):
            result = await runtime_module._maybe_start_lesson_on_connect_impl(conn)
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()

    assert readiness_at_preload and readiness_at_preload[0] >= 3, (
        "preload ran before MCP reconnect readiness: "
        f"ready_checks={readiness_at_preload!r}, expected>=3"
    )
    assert protocol_starts == [True], (
        f"protocol start did not use the preloaded runtime: {protocol_starts!r}"
    )
    assert result is not None, "connect-time lesson start returned no runtime"
    assert conn.lesson_runtime is result, "returned runtime did not become active"
    print("T54 MCP reconnect recovery: PASS")


asyncio.run(asyncio.wait_for(main(), timeout=5.0))
PY
