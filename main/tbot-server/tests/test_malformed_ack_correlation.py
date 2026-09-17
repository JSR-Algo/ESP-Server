"""ACK regressions using repository fixtures; native provenance is tested separately."""
import asyncio
from copy import deepcopy

import pytest

from core.lesson.runtime import RENDERER_V5
from tests.test_course_terminal_lifecycle import runtime_with_store, _frames, _control_ack
from tests.test_lesson_cinematic_phase_routing import _v5_ack


MALFORMED = [True, False, None, [], {}, "abc", "", 1.5, 1.0, "missing"]


def snapshot(runtime):
    return deepcopy((runtime._last_inbound_sequence, runtime._outstanding,
        runtime._retired_conversation_ack_sequences, _frames(runtime),
        runtime._cinematic_phase, runtime._cinematic_pending_command,
        runtime.state, runtime._step_acked, runtime._cinematic_deferred_step_ack))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", MALFORMED)
@pytest.mark.parametrize("stage", ["prepare", "start", "retired-prepare", "retired-start", "terminal"])
async def test_malformed_v5_ack_preserves_state_then_valid_ack_progresses(stage, value):
    runtime = runtime_with_store()
    try:
        assert runtime.negotiated_version == RENDERER_V5
        assert runtime._queue_course_cinematic_phase("listen")
        await asyncio.sleep(0)
        frame = _frames(runtime)[-1]
        inbound = 1
        if stage.endswith("start"):
            await runtime.on_lesson_ack(_v5_ack(runtime, frame, inbound))
            inbound += 1
            await asyncio.sleep(0)
            frame = _frames(runtime)[-1]
            assert frame["type"] == "lesson_start"
        valid = _v5_ack(runtime, frame, inbound)
        if stage.startswith("retired"):
            await runtime.cancel()
            assert frame["sequence"] in runtime._retired_conversation_ack_sequences
        elif stage == "terminal":
            await runtime.cancel()
            frame = _frames(runtime)[-1]
            valid = _control_ack(runtime, frame, inbound)
            # Recovery keeps its original contract when new admission is disabled.
            runtime.conn.config["lesson"]["renderer_v5_enabled"] = False
        invalid = deepcopy(valid)
        if value == "missing":
            invalid["body"].pop("acks")
        else:
            invalid["body"]["acks"] = value
        before = snapshot(runtime)
        await runtime.on_lesson_ack(invalid)
        assert snapshot(runtime) == before
        await runtime.on_lesson_ack(valid)
        assert runtime._last_inbound_sequence == inbound
        assert frame["sequence"] not in runtime._outstanding
        assert frame["sequence"] not in runtime._retired_conversation_ack_sequences
        after = snapshot(runtime)
        await runtime.on_lesson_ack(valid)
        assert snapshot(runtime) == after
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("format_string", ["{}", " {} "])
async def test_outstanding_v5_ack_preserves_numeric_string_compatibility(format_string):
    runtime = runtime_with_store()
    try:
        assert runtime._queue_course_cinematic_phase("listen")
        await asyncio.sleep(0)
        frame = _frames(runtime)[-1]
        valid = _v5_ack(runtime, frame, 1)
        valid["body"]["acks"] = format_string.format(frame["sequence"])
        await runtime.on_lesson_ack(valid)
        assert runtime._last_inbound_sequence == 1
        assert frame["sequence"] not in runtime._outstanding
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("negotiated", [None, "teebot-lesson-renderer.v4"])
@pytest.mark.parametrize("value", MALFORMED)
async def test_review_retained_v5_guard_when_negotiated_version_differs(negotiated, value):
    runtime = runtime_with_store()
    try:
        await runtime.cancel()
        frame = _frames(runtime)[-1]
        valid = _control_ack(runtime, frame, 1)
        # Explicit unit state mutation isolates the retained-contract OR branch.
        # The native journeys never mutate negotiated/RUNNING/entrance state.
        runtime.negotiated_version = negotiated
        assert runtime.negotiated_version != RENDERER_V5
        assert runtime._terminal_cinematic_version() == RENDERER_V5
        invalid = deepcopy(valid)
        if value == "missing":
            invalid["body"].pop("acks")
        else:
            invalid["body"]["acks"] = value
        before = snapshot(runtime)
        await runtime.on_lesson_ack(invalid)
        assert snapshot(runtime) == before
        await runtime.on_lesson_ack(valid)
        assert runtime._last_inbound_sequence == 1
        assert runtime.state == "COMPLETED"
        assert frame["sequence"] not in runtime._outstanding
        after = snapshot(runtime)
        await runtime.on_lesson_ack(valid)
        assert snapshot(runtime) == after
    finally:
        await runtime.close()
