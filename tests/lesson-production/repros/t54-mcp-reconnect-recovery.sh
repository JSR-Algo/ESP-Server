#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
campaign_root="$(CDPATH= cd -- "$script_dir/../.." && pwd -P)"
fixture_rel="docs/stories/US-006-learning-course-runtime/fixtures/lesson-protocol.v1.json"
fixture_source="$campaign_root/robot/$fixture_rel"
fixture_target="$PWD/$fixture_rel"
if [ ! -f "$fixture_source" ]; then
  echo "FATAL: campaign lesson protocol fixture not found: $fixture_source" >&2
  exit 2
fi

probe="$(mktemp "${TMPDIR:-/tmp}/t54-mcp-reconnect-recovery.XXXXXX")"
created_fixture=false
created_dirs=()
cleanup() {
  rm -f "$probe"
  if [ "$created_fixture" = true ]; then
    rm -f "$fixture_target"
    for created_dir in "${created_dirs[@]}"; do
      rmdir "$created_dir" 2>/dev/null || true
    done
  fi
}
trap cleanup EXIT

if [ ! -f "$fixture_target" ]; then
  fixture_dir="$(dirname "$fixture_target")"
  missing_dir="$fixture_dir"
  while [ ! -d "$missing_dir" ]; do
    created_dirs+=("$missing_dir")
    missing_dir="$(dirname "$missing_dir")"
  done
  mkdir -p "$fixture_dir"
  created_fixture=true
  cp "$fixture_source" "$fixture_target"
fi

cat >"$probe" <<'PY'
import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


class DelayedMcpClient:
    def __init__(self, ready_after):
        self.ready_after = ready_after
        self.ready_checks = 0

    async def is_ready(self):
        self.ready_checks += 1
        return self.ready_checks >= self.ready_after


def load_runtime_test_helpers(server_root):
    test_path = server_root / "tests" / "test_lesson_runtime.py"
    spec = importlib.util.spec_from_file_location("t54_runtime_helpers", test_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load stable runtime helpers from {test_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main():
    server_root = Path.cwd() / "main" / "tbot-server"
    helpers = load_runtime_test_helpers(server_root)

    from core.lesson import runtime as runtime_module

    case = helpers.RepublishOnConnectTest(methodName="runTest")
    setup_complete = False
    undo_backend = None
    try:
        case.setUp()
        setup_complete = True

        conn = helpers._RepublishConn()
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

        assignment = case._assignment(lesson_version=3, assignment_version=1)
        undo_backend = case._patch_backend(assignment, helpers._build_manifest())
        with patch.object(
            runtime_module.LessonRuntime, "preload_only", new=record_preload
        ), patch.object(
            runtime_module.LessonRuntime, "start_protocol", new=record_start
        ):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)

        assert readiness_at_preload, "preload was never reached"
        assert readiness_at_preload[0] >= 3, (
            "preload ran before MCP reconnect readiness: "
            f"ready_checks={readiness_at_preload[0]}, expected>=3"
        )
        assert protocol_starts == [True], (
            f"protocol start did not use the preloaded runtime: {protocol_starts!r}"
        )
        assert result is not None, "connect-time lesson start returned no runtime"
        assert conn.lesson_runtime is result, "returned runtime did not become active"
        assert conn.lesson_start_status["code"] == "STARTED", (
            f"unexpected lesson start status: {conn.lesson_start_status!r}"
        )
    finally:
        try:
            if undo_backend is not None:
                undo_backend()
        finally:
            if setup_complete:
                case.tearDown()

    print("T54 MCP reconnect recovery: PASS")


asyncio.run(asyncio.wait_for(main(), timeout=5.0))
PY

if [ -n "${TBOT_GATE_PYTHON:-}" ]; then
  python_bin="$TBOT_GATE_PYTHON"
  if [ ! -x "$python_bin" ]; then
    echo "FATAL: TBOT_GATE_PYTHON is not an executable file: $python_bin" >&2
    exit 2
  fi
else
  python_bin="${TBOT_REPRO_PYTHON:-python3}"
  if [ ! -x "$python_bin" ]; then
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$common_dir" ]; then
      shared_root="$(dirname "$common_dir")"
      python_bin="${TBOT_REPRO_PYTHON:-python3}"
    fi
  fi
  if [ ! -x "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
  if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
    echo "FATAL: no executable Python interpreter found for the T54 MCP probe" >&2
    exit 2
  fi
fi

TBOT_ROBOT_REPO="$campaign_root/robot" \
  PYTHONPATH="$PWD/main/tbot-server" \
  "$python_bin" "$probe"
