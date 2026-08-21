#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T5.4 — renderer-v5 must publish one step_started after an accepted step-scoped
# lesson_start ACK, and a duplicate ACK must not publish a second event.
set -uo pipefail

ROOT="${T54_REPO_ROOT:-$(pwd)}"
SERVER="$ROOT/main/tbot-server"
[ -d "$SERVER" ] || { echo "FATAL: no main/tbot-server under $ROOT"; exit 2; }

cd "$SERVER" || exit 2
python3 - <<'PYEOF'
import asyncio
import json
import sys

sys.path.insert(0, "tests")
import test_lesson_cinematic_phase_routing as routing
from core.lesson.runtime import S_RUNNING


async def main():
    runtime = routing._v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 1
    runtime._step = runtime._steps[1]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 21
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._child_response_window_open = True

    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    await runtime.on_lesson_ack(routing._v5_ack(runtime, prepare, 1))
    start = json.loads(runtime.conn.websocket.sent[-1])
    ack = routing._v5_ack(runtime, start, 2)
    await runtime.on_lesson_ack(ack)
    await runtime.on_lesson_ack(ack)
    assert await task is True

    events = [
        event
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
        if event.get("type") == "step_started"
    ]
    assert len(events) == 1, events
    assert events[0]["stepId"] == runtime._step_id, events
    assert events[0]["stepType"] == runtime._step["type"], events
    print("renderer-v5 step_started ACK contract OK")


asyncio.run(main())
PYEOF
