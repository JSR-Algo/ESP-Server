#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
campaign_root="$(CDPATH= cd -- "$script_dir/../.." && pwd -P)"
fixture_rel="docs/stories/US-006-learning-course-runtime/fixtures/lesson-protocol.v1.json"
fixture_source="$campaign_root/robot/$fixture_rel"
fixture_target="$PWD/$fixture_rel"
probe="$(mktemp "${TMPDIR:-/tmp}/t54-power-cycle-sd-sync.XXXXXX")"
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

if [ ! -f "$fixture_source" ]; then
  echo "FATAL: campaign lesson protocol fixture not found: $fixture_source" >&2
  exit 2
fi
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


def load_runtime_test_helpers(server_root):
    test_path = server_root / "tests" / "test_lesson_runtime.py"
    spec = importlib.util.spec_from_file_location("t54_sd_sync_helpers", test_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runtime helpers from {test_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main():
    server_root = Path.cwd() / "main" / "tbot-server"
    helpers = load_runtime_test_helpers(server_root)
    from core.lesson import runtime as runtime_module

    case = helpers.RepublishOnConnectTest(methodName="runTest")
    undo_backend = None
    start_task = None
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

    try:
        case.setUp()
        conn = helpers._RepublishConn()
        conn.sd_pack_sync_task = asyncio.create_task(cached_sd_sync())
        undo_backend = case._patch_backend(
            case._assignment(lesson_version=3, assignment_version=1),
            helpers._build_manifest(),
        )
        with patch.object(
            runtime_module.LessonRuntime, "preload_only", new=record_preload
        ), patch.object(
            runtime_module.LessonRuntime, "start_protocol", new=record_start
        ):
            start_task = asyncio.create_task(
                runtime_module.maybe_start_lesson_on_connect(conn)
            )
            await asyncio.wait_for(preload_seen.wait(), timeout=1.0)

            assert "preloaded" in order, f"preload not reached: {order!r}"
            assert "started" not in order, (
                "lesson protocol started while cached SD sync was active: "
                f"{order!r}"
            )
            sync_release.set()
            result = await asyncio.wait_for(start_task, timeout=1.0)
            assert order.index("sync_finished") < order.index("started"), order
            assert conn.lesson_runtime is result
            assert conn.lesson_start_status["code"] == "STARTED"
    finally:
        sync_release.set()
        if start_task is not None and not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)
        if undo_backend is not None:
            undo_backend()
        case.tearDown()

    print("T54 power-cycle SD sync ordering: PASS")


asyncio.run(asyncio.wait_for(main(), timeout=5.0))
PY

if [ -n "${TBOT_GATE_PYTHON:-}" ]; then
  python_bin="$TBOT_GATE_PYTHON"
else
  python_bin="$(command -v python3 || true)"
fi
if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found" >&2
  exit 2
fi

TBOT_ROBOT_REPO="$campaign_root/robot" \
  PYTHONPATH="$PWD/main/tbot-server" \
  "$python_bin" "$probe"
