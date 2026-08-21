from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_lesson_cinematic_phase_routing import _v5_runtime
from core.lesson.runtime import S_RUNNING


@pytest.mark.asyncio
async def test_phase_less_later_step_publishes_ordered_progress_once() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 0
    runtime._step = runtime._steps[0]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 1
    runtime._semantic_step_sequence = 1
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._started_step_ids.add(runtime._step_id)
    runtime._steps[1]["scene"].pop("robotOverlay", None)

    await runtime._emit_step()

    events = [
        event
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
        if event.get("type") == "step_started"
    ]
    assert events == [
        {
            "type": "step_started",
            "sequence": 2,
            "stepId": runtime._steps[1]["id"],
            "stepType": runtime._steps[1]["type"],
            "retryCount": 0,
        }
    ]
