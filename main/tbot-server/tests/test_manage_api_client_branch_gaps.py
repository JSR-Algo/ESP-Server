"""Close the lone remaining *branch* gap in ``config.manage_api_client``.

``--cov-branch`` flags ``134->exit`` in ``_execute_async_request``: the
``while retry_count <= cls.max_retries:`` loop exiting via its condition becoming
False *without* an early return/raise. Every existing retry test either returns
on success or raises on a non-retryable error, so the natural loop-exit edge is
never taken. A ``max_retries < 0`` makes the guard False on entry, the body never
runs, and the method falls through returning ``None`` -- the defensive
misconfiguration path.

Harness mirrors ``test_manage_api_client_edges.py``: the REAL module is
importlib-loaded under an alias so coverage attributes the lines to the shared
source file.
"""

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "config._manage_api_client_branch_gaps",
        Path(__file__).resolve().parents[1] / "config" / "manage_api_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_execute_async_request_negative_max_retries_falls_through(monkeypatch):
    """max_retries = -1 -> ``while retry_count <= cls.max_retries`` is False on
    entry (0 <= -1), the loop body never executes, and the method returns None via
    the natural loop-exit edge (branch 134->exit). Mutation guard: if the loop were
    a ``do/while`` (body ran once before the check), _async_request would be called
    and this assertion of zero calls would fail."""
    mac = _load_module()
    calls = []

    async def _request(cls, method, endpoint, **kwargs):
        calls.append((method, endpoint))
        return {"unreachable": True}

    monkeypatch.setattr(mac.ManageApiClient, "_async_request", classmethod(_request))
    mac.ManageApiClient.max_retries = -1

    result = await mac.ManageApiClient._execute_async_request("GET", "/x")

    assert result is None
    assert calls == []
