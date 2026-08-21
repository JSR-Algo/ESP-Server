from __future__ import annotations

import asyncio
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.lesson.runtime import S_RUNNING
from core.voice.session_provider.google_live import GoogleLiveProvider
from test_lesson_cinematic_phase_routing import _v5_runtime


@pytest.mark.asyncio
async def test_google_live_receive_timeout_after_resend_is_bounded() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 0
    runtime._step = runtime._steps[0]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 20
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._step_completed = False
    runtime._child_response_window_open = False
    timeout_gates: list[asyncio.Event] = []

    async def controlled_sleep(_seconds: float) -> None:
        gate = asyncio.Event()
        timeout_gates.append(gate)
        await gate.wait()

    async def open_response_window() -> bool:
        runtime._child_response_window_open = True
        return True

    runtime._sleep = controlled_sleep
    runtime._apply_authored_cinematic_effect = mock.AsyncMock(return_value=True)
    runtime._speak_lesson_prompt_text = mock.AsyncMock(return_value=True)
    runtime._open_child_response_window = open_response_window
    provider = GoogleLiveProvider(runtime.conn)
    runtime.conn.session_mode = "LESSON"
    provider._last_lesson_prompt_text = "Say barn."
    provider._lesson_prompt_resend_count = 1
    runtime.conn.voice_provider = provider

    assert await provider._handle_receive_timeout_event()
    while len(timeout_gates) < 1:
        await asyncio.sleep(0)

    assert runtime._safe_speaking().attempts == 1
    assert runtime._child_response_timeout_task is not None

    timeout_gates[0].set()
    while runtime._safe_speaking().attempts < 2:
        await asyncio.sleep(0)
    while len(timeout_gates) < 2:
        await asyncio.sleep(0)

    timeout_gates[1].set()
    while runtime._safe_speaking_session is not None:
        await asyncio.sleep(0)

    assert runtime._step_id != "barn" or runtime._step_completed
