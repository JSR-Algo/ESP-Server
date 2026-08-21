#!/usr/bin/env bash
# repo: robot/esp32-server
# regate: skip
set -euo pipefail

probe="main/tbot-server/tests/_t54_gate_reconnect_nudge.py"
cleanup() { rm -f "$probe"; }
trap cleanup EXIT

cat >"$probe" <<'PY'
import asyncio
import json
import os
from types import SimpleNamespace

import pytest


class Request:
    match_info = {"deviceId": "device-1"}
    headers = {"X-Mint-Secret": "secret"}
    host = "localhost"
    remote = "127.0.0.1"


@pytest.mark.asyncio
async def test_nudge_reconnects_to_the_replacement_socket(monkeypatch):
    from core.api.lesson_nudge_handler import LessonNudgeHandler
    import core.lesson.runtime as runtime

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    started = asyncio.Event()
    release = asyncio.Event()
    events = []

    async def old_transition():
        events.append("old")
        started.set()
        await release.wait()
        return True

    async def new_transition():
        events.append("new")
        return True

    old = SimpleNamespace(transition_to_lesson_start=old_transition)
    new = SimpleNamespace(transition_to_lesson_start=new_transition)
    connections = {"device-1": old}

    async def pull(conn):
        events.append(("pull", conn))

    monkeypatch.setattr(runtime, "maybe_start_lesson_on_connect", pull)
    task = asyncio.create_task(LessonNudgeHandler({}, connections).handle_post(Request()))
    await asyncio.wait_for(started.wait(), timeout=2)
    connections["device-1"] = new
    release.set()
    response = await asyncio.wait_for(task, timeout=2)

    assert response.status == 202
    assert json.loads(response.text)["data"] == {"nudged": True}
    assert events == ["old", "new", ("pull", new)]
PY

cd main/tbot-server
${TBOT_REPRO_PYTHON:-python3} -m pytest -q "tests/$(basename "$probe")"
