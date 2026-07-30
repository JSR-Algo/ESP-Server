import json

import pytest

from core.api.generation_retry_handler import GenerationRetryHandler


class Request:
    def __init__(self, secret="secret"):
        self.headers = {"X-Mint-Secret": secret} if secret is not None else {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("poll_result", "expected"),
    [
        ({"state": "accepted"}, {"state": "accepted"}),
        ({"state": "not_modified"}, {"state": "not_modified"}),
        (
            {"state": "rejected", "errorCode": "generation_callback_retry"},
            {"state": "rejected", "errorCode": "generation_callback_retry"},
        ),
    ],
)
async def test_generation_retry_invokes_global_poller_and_returns_strict_result(
    monkeypatch, poll_result, expected
):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    calls = []

    class Poller:
        async def run_once(self):
            calls.append("run_once")
            return poll_result

    response = await GenerationRetryHandler(Poller()).handle_post(Request())

    assert response.status == 200
    assert json.loads(response.text) == expected
    assert calls == ["run_once"]


@pytest.mark.asyncio
async def test_generation_retry_rejects_missing_or_invalid_internal_auth(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")

    class Poller:
        async def run_once(self):
            raise AssertionError("unauthorized request must not invoke poller")

    missing = await GenerationRetryHandler(Poller()).handle_post(Request(None))
    invalid = await GenerationRetryHandler(Poller()).handle_post(Request("wrong"))

    assert missing.status == 401
    assert invalid.status == 401


@pytest.mark.asyncio
async def test_generation_retry_never_leaks_raw_poller_failures(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")

    class Poller:
        async def run_once(self):
            raise RuntimeError("https://private.example/?token=secret")

    response = await GenerationRetryHandler(Poller()).handle_post(Request())

    assert response.status == 200
    assert json.loads(response.text) == {
        "state": "rejected",
        "errorCode": "generation_retry_failed",
    }
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_generation_retry_rejects_non_strict_poller_results(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")

    class Poller:
        async def run_once(self):
            return {"state": "accepted", "leaked": "secret"}

    response = await GenerationRetryHandler(Poller()).handle_post(Request())

    assert json.loads(response.text) == {
        "state": "rejected",
        "errorCode": "generation_retry_invalid_result",
    }


@pytest.mark.asyncio
async def test_generation_retry_rejects_when_global_poller_is_not_configured(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")

    response = await GenerationRetryHandler(None).handle_post(Request())

    assert json.loads(response.text) == {
        "state": "rejected",
        "errorCode": "generation_poller_unavailable",
    }
