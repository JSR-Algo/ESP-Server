#!/usr/bin/env bash
# repo: robot/esp32-server
# T6.1 repro — manager API client retention/cleanup.
#
# Behaviour under test:
#   - each _ensure_async_client() call creates a fresh request-owned client
#   - _async_request() closes the request-owned client after closing its response
#
# RED at pre-patch base: _ensure_async_client() retains one AsyncClient per event loop
# and _async_request() leaves that retained client open.
# GREEN at lesson-prod/t61-soak-fixes: clients are request-scoped and closed.
set -euo pipefail

SERVER="main/tbot-server"
[ -d "$SERVER/config" ] || { echo "FATAL: run from the robot/esp32-server repo root"; exit 2; }

python3 - <<'PY'
import asyncio
import importlib.util
from pathlib import Path


def load_manage_api_client():
    path = Path("main/tbot-server/config/manage_api_client.py").resolve()
    spec = importlib.util.spec_from_file_location("t61_manage_api_client_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self):
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 0, "data": {"ok": True}}

    async def aclose(self):
        self.closed = True


async def test_ensure_async_client_creates_fresh_clients():
    mac = load_manage_api_client()
    created = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        async def aclose(self):
            self.closed = True

    mac.httpx.AsyncClient = FakeAsyncClient
    mac.ManageApiClient.config = {"url": "http://manager.test", "timeout": 9}
    mac.ManageApiClient._secret = "secret"
    mac.ManageApiClient._async_clients = {}

    first = await mac.ManageApiClient._ensure_async_client()
    second = await mac.ManageApiClient._ensure_async_client()

    assert first is not second, "_ensure_async_client() retained a client instead of creating a request-owned one"
    assert len(created) == 2, f"expected 2 created clients, got {len(created)}"
    assert first.kwargs["base_url"] == "http://manager.test"
    assert first.kwargs["timeout"] == 9
    assert first.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert first.kwargs["headers"]["Accept"] == "application/json"
    assert mac.ManageApiClient._async_clients == {}, "_ensure_async_client() retained clients in _async_clients"


async def test_async_request_closes_request_owned_client():
    mac = load_manage_api_client()
    response = Response()

    class RequestClient:
        def __init__(self):
            self.calls = []
            self.closed = False

        async def request(self, method, endpoint, **kwargs):
            self.calls.append((method, endpoint, kwargs))
            return response

        async def aclose(self):
            self.closed = True

    client = RequestClient()

    async def ensure(cls):
        return client

    mac.ManageApiClient._ensure_async_client = classmethod(ensure)

    result = await mac.ManageApiClient._async_request("POST", "/demo", json={"x": 1})

    assert result == {"ok": True}
    assert len(client.calls) == 1
    method, _endpoint, kwargs = client.calls[0]
    assert method == "POST"
    assert kwargs == {"json": {"x": 1}}
    assert response.closed is True, "_async_request() did not close its response"
    assert client.closed is True, "_async_request() did not close its request-owned client"


async def main():
    failures = []
    for name, probe in (
        ("fresh client per _ensure_async_client call", test_ensure_async_client_creates_fresh_clients),
        ("request-owned client cleanup", test_async_request_closes_request_owned_client),
    ):
        try:
            await probe()
        except AssertionError as exc:
            failures.append(f"FAIL {name}: {exc}")
        except Exception as exc:
            failures.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")

    if failures:
        for failure in failures:
            print(failure)
        raise SystemExit(1)

    print("REPRO PASS: T6.1 manager API clients are request-scoped and closed after each request.")


asyncio.run(main())
PY
