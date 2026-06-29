import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "config._manage_api_client_edges",
        Path(__file__).resolve().parents[1] / "config" / "manage_api_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload=None, *, status_code=200, content=b"{}", headers=None, error=None):
        self._payload = payload if payload is not None else {"code": 0, "data": {"ok": True}}
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self._payload

    async def aclose(self):
        self.closed = True


class _RequestClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _CloseClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    async def aclose(self):
        self.closed = True
        if self.fail:
            raise RuntimeError("close failed")


def test_device_bind_exception_keeps_bind_code_and_message():
    mac = _load_module()

    exc = mac.DeviceBindException("123456")

    assert exc.bind_code == "123456"
    assert "123456" in str(exc)


def test_client_initialization_validation_singleton_and_service_wrappers(monkeypatch):
    mac = _load_module()

    for config, message in (
        ({}, "manager-api config error"),
        ({"manager-api": {"secret": "s"}}, "manager-api url or secret"),
        ({"manager-api": {"url": "http://api", "secret": "You must set it"}}, "Configure manager-api"),
    ):
        mac.ManageApiClient._instance = None
        with pytest.raises(Exception, match=message):
            mac.ManageApiClient(config)

    config = {
        "manager-api": {
            "url": "http://manager.test",
            "secret": "secret",
            "max_retries": 2,
            "retry_delay": 0.25,
            "timeout": 7,
        }
    }
    client = mac.ManageApiClient(config)
    assert mac.ManageApiClient(config) is client
    assert mac.ManageApiClient._secret == "secret"
    assert mac.ManageApiClient.max_retries == 2
    assert mac.ManageApiClient.retry_delay == 0.25
    assert mac.ManageApiClient._async_clients == {}

    calls = []
    monkeypatch.setattr(mac.ManageApiClient, "safe_close", classmethod(lambda cls: calls.append("closed")))
    mac.manage_api_http_safe_close()
    assert calls == ["closed"]

    mac.ManageApiClient._instance = None
    mac.init_service(config)
    assert isinstance(mac.ManageApiClient._instance, mac.ManageApiClient)


@pytest.mark.asyncio
async def test_ensure_async_client_builds_one_client_per_loop(monkeypatch):
    mac = _load_module()
    created = []

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)

    monkeypatch.setattr(mac.httpx, "AsyncClient", _FakeAsyncClient)
    mac.ManageApiClient.config = {"url": "http://manager.test", "timeout": 9}
    mac.ManageApiClient._secret = "secret"
    mac.ManageApiClient._async_clients = {}

    first = await mac.ManageApiClient._ensure_async_client()
    second = await mac.ManageApiClient._ensure_async_client()

    assert first is second
    assert len(created) == 1
    assert first.kwargs["base_url"] == "http://manager.test"
    assert first.kwargs["timeout"] == 9
    assert first.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert first.kwargs["headers"]["Accept"] == "application/json"
    assert "PythonClient/2.0" in first.kwargs["headers"]["User-Agent"]


def test_ensure_async_client_rejects_sync_context():
    mac = _load_module()
    coro = mac.ManageApiClient._ensure_async_client()
    try:
        with pytest.raises(Exception, match="Must be called in async context"):
            coro.send(None)
    finally:
        coro.close()


@pytest.mark.asyncio
async def test_async_request_success_and_business_error_paths(monkeypatch):
    mac = _load_module()

    async def install_client(client):
        async def _ensure(cls):
            return client

        monkeypatch.setattr(mac.ManageApiClient, "_ensure_async_client", classmethod(_ensure))

    success = _Response({"code": 0, "data": {"value": 3}})
    client = _RequestClient([success])
    await install_client(client)
    assert await mac.ManageApiClient._async_request("POST", "/demo", json={"x": 1}) == {"value": 3}
    assert client.calls == [("POST", "demo", {"json": {"x": 1}})]
    assert success.closed is True

    for payload, expected in (
        ({"code": 10041, "msg": "missing"}, mac.DeviceNotFoundException),
        ({"code": 10042, "msg": "bind-code"}, mac.DeviceBindException),
        ({"code": 50000, "msg": "bad"}, Exception),
    ):
        response = _Response(payload)
        await install_client(_RequestClient([response]))
        with pytest.raises(expected):
            await mac.ManageApiClient._async_request("GET", "status")
        assert response.closed is True


def test_should_retry_only_transient_failures():
    mac = _load_module()
    request = httpx.Request("GET", "http://manager.test")

    assert mac.ManageApiClient._should_retry(httpx.ConnectError("offline")) is True
    assert mac.ManageApiClient._should_retry(httpx.TimeoutException("slow")) is True
    assert mac.ManageApiClient._should_retry(httpx.NetworkError("network")) is True
    for status in (408, 429, 500, 502, 503, 504):
        exc = httpx.HTTPStatusError("retry", request=request, response=httpx.Response(status))
        assert mac.ManageApiClient._should_retry(exc) is True
    exc = httpx.HTTPStatusError("no", request=request, response=httpx.Response(404))
    assert mac.ManageApiClient._should_retry(exc) is False
    assert mac.ManageApiClient._should_retry(ValueError("bad")) is False


@pytest.mark.asyncio
async def test_execute_async_request_retries_transient_and_raises_nonretry(monkeypatch):
    mac = _load_module()
    attempts = []
    sleeps = []

    async def _sleep(delay):
        sleeps.append(delay)

    async def _request(cls, method, endpoint, **kwargs):
        attempts.append((method, endpoint, kwargs))
        if len(attempts) == 1:
            raise mac.httpx.ConnectError("offline")
        return {"ok": True}

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(mac.ManageApiClient, "_async_request", classmethod(_request))
    mac.ManageApiClient.max_retries = 2
    mac.ManageApiClient.retry_delay = 0.01

    assert await mac.ManageApiClient._execute_async_request("POST", "/x", json={}) == {"ok": True}
    assert len(attempts) == 2
    assert sleeps == [0.01]

    async def _bad_request(cls, method, endpoint, **kwargs):
        raise ValueError("no retry")

    monkeypatch.setattr(mac.ManageApiClient, "_async_request", classmethod(_bad_request))
    with pytest.raises(ValueError, match="no retry"):
        await mac.ManageApiClient._execute_async_request("GET", "/bad")


def test_safe_close_sync_context_closes_and_swallows_errors():
    mac = _load_module()
    good = _CloseClient()
    bad = _CloseClient(fail=True)
    mac.ManageApiClient._async_clients = {"good": good, "bad": bad}
    mac.ManageApiClient._instance = object()

    mac.ManageApiClient.safe_close()

    assert good.closed is True
    assert bad.closed is True
    assert mac.ManageApiClient._async_clients == {}
    assert mac.ManageApiClient._instance is None


@pytest.mark.asyncio
async def test_close_async_clients_swallows_close_errors():
    mac = _load_module()
    good = _CloseClient()
    bad = _CloseClient(fail=True)

    await mac.ManageApiClient._close_async_clients([good, bad])

    assert good.closed is True
    assert bad.closed is True


@pytest.mark.asyncio
async def test_manager_endpoint_wrappers_and_error_fallbacks(capsys):
    mac = _load_module()

    class _Instance:
        def __init__(self):
            self.calls = []
            self.fail = False

        async def _execute_async_request(self, method, endpoint, **kwargs):
            self.calls.append((method, endpoint, kwargs))
            if self.fail:
                raise RuntimeError("backend down")
            return {"endpoint": endpoint}

    instance = _Instance()
    mac.ManageApiClient._instance = instance

    assert await mac.get_server_config() == {"endpoint": "/config/server-base"}
    assert await mac.get_agent_models("mac", "client", {"LLM": "x"}) == {"endpoint": "/config/agent-models"}
    assert await mac.get_correct_words("mac") == {"endpoint": "/config/correct-words"}
    assert await mac.generate_and_save_chat_summary("s1") == {"endpoint": "/agent/chat-summary/s1/save"}
    assert await mac.generate_and_save_chat_title("s1") == {"endpoint": "/agent/chat-title/s1/generate"}
    assert await mac.report("mac", "s1", 1, "hello", b"abc", "now") == {"endpoint": "/agent/chat-history/report"}

    report_call = instance.calls[-1]
    assert report_call[2]["json"]["audioBase64"] == "YWJj"
    assert await mac.report("mac", "s1", 1, "hello", None, "now") == {"endpoint": "/agent/chat-history/report"}
    assert instance.calls[-1][2]["json"]["audioBase64"] is None
    assert await mac.report("mac", "s1", 1, "", b"x", "now") is None
    mac.ManageApiClient._instance = None
    assert await mac.report("mac", "s1", 1, "hello", b"x", "now") is None

    mac.ManageApiClient._instance = instance
    instance.fail = True
    assert await mac.get_correct_words("mac") is None
    assert await mac.generate_and_save_chat_summary("s2") is None
    assert await mac.generate_and_save_chat_title("s2") is None
    assert await mac.report("mac", "s1", 1, "hello", b"abc", "now") is None
    output = capsys.readouterr().out
    assert "GetReplacement wordFail" in output
    assert "Generate and save chat history summaryFail" in output
    assert "Generate and save chat titleFail" in output
    assert "TTSReport Failed" in output


def test_lesson_helpers_normalize_auth_and_transient_edges(monkeypatch):
    mac = _load_module()
    request = httpx.Request("GET", "http://backend.test/v1/x")

    assert mac._lesson_auth_headers(None) == {"Accept": "application/json"}
    assert mac._lesson_auth_headers("token") == {"Accept": "application/json", "Authorization": "Bearer token"}
    assert mac._lesson_base("http://backend.test/v1/") == "http://backend.test/v1"
    assert mac._lesson_base(None) == ""
    assert mac._lesson_is_transient(httpx.ConnectError("offline")) is True
    assert mac._lesson_is_transient(httpx.HTTPStatusError("retry", request=request, response=httpx.Response(503))) is True
    assert mac._lesson_is_transient(httpx.HTTPStatusError("no", request=request, response=httpx.Response(404))) is False
    monkeypatch.setattr(mac, "httpx", None)
    assert mac._lesson_is_transient(RuntimeError("x")) is False

    assert mac._normalize_lesson_event({"result": "ok", "sequence": -3, "detail": {"utterance": "secret", "score": 1}}) == {
        "outcome": "ok",
        "sequence": -3,
    }
    assert mac._normalize_lesson_event(
        {
            "result": "success",
            "detail": {
                "recognizedText": "con nói barn",
                "childResponse": "barn",
                "transcript": "barn raw",
                "source": "voice_transcript",
                "nested": {"recognizedText": "nested raw", "keep": 1},
                "attempts": [{"childResponse": "attempt raw", "score": 0.5, "kept": "metadata"}],
                "evaluation": "pass",
                "correctness": "correct",
            },
        }
    ) == {
        "outcome": "success",
        "detail": {"source": "voice_transcript", "nested": {"keep": 1}, "attempts": [{"kept": "metadata"}]},
    }
    assert mac._normalize_lesson_event(
        {"result": "success", "detail": {"nested": {"recognizedText": "nested only", "keep": True}}}
    ) == {"outcome": "success", "detail": {"nested": {"keep": True}}}
    assert mac._normalize_lesson_event(
        {
            "result": "success",
            "recognizedText": "top-level raw",
            "attempts": [{"transcript": "nested raw", "kept": "metadata"}],
        }
    ) == {"outcome": "success", "attempts": [{"kept": "metadata"}]}
    assert mac._normalize_lesson_event(
        {
            "result": "success",
            "recognized_text": "top-level snake raw",
            "child_response": "snake child raw",
            "detail": {
                "RecognizedText": "case variant raw",
                "pronunciation": "too early",
                "phonemeScore": 87,
                "source": "voice_transcript",
            },
            "attempts": [
                {
                    "ChildResponse": "case variant nested raw",
                    "phoneme_score": 0.9,
                    "phonemeAssessment": "pass",
                    "kept": "metadata",
                }
            ],
        }
    ) == {"outcome": "success", "detail": {"source": "voice_transcript"}, "attempts": [{"kept": "metadata"}]}
    assert mac._normalize_lesson_event({"result": "old", "outcome": "kept", "detail": {"utterance": "secret"}}) == {
        "outcome": "kept"
    }
    assert mac._normalize_lesson_event({"detail": "text"}) == {"detail": "text"}


@pytest.mark.asyncio
async def test_lesson_request_with_retry_empty_response_headers_and_final_error(monkeypatch):
    mac = _load_module()
    sleeps = []

    async def _sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    ok = _Response({"ignored": True}, status_code=204, content=b"", headers={"ETag": "v1"})
    client = _RequestClient([ok])

    body, headers = await mac._lesson_request_with_retry(client, "GET", "http://backend.test/x", return_headers=True)

    assert body is None
    assert headers == {"ETag": "v1"}
    assert ok.closed is True

    request = httpx.Request("GET", "http://backend.test/x")
    retry = _Response(error=httpx.HTTPStatusError("retry", request=request, response=httpx.Response(503)))
    recovered = _Response({"ok": True})
    client = _RequestClient([retry, recovered])
    assert await mac._lesson_request_with_retry(client, "GET", "http://backend.test/x", retry_delay=0.02) == {"ok": True}
    assert retry.closed is True
    assert recovered.closed is True
    assert sleeps == [0.02]

    response = _Response(error=httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404)))
    client = _RequestClient([response])
    with pytest.raises(httpx.HTTPStatusError):
        await mac._lesson_request_with_retry(client, "GET", "http://backend.test/x")
    assert response.closed is True
    assert sleeps == [0.02]


@pytest.mark.asyncio
async def test_lesson_endpoint_helpers_extract_payloads_and_normalize_events(monkeypatch):
    mac = _load_module()

    assignment_response = _Response({"data": {"assignment": {"id": "a1"}}})
    client = _RequestClient([assignment_response])
    assert await mac.get_current_assignment(client, "http://backend.test/v1/", "device-1", token="t") == {"id": "a1"}
    method, url, kwargs = client.calls[0]
    assert method == "GET"
    assert url == "http://backend.test/v1/devices/device-1/assignment/current"
    assert kwargs["headers"]["Authorization"] == "Bearer t"

    assert await mac.get_current_assignment(_RequestClient([_Response([])]), "http://b", "d") is None
    assert await mac.get_current_assignment(_RequestClient([_Response({"data": None})]), "http://b", "d") is None

    manifest_client = _RequestClient([
        _Response(
            {"data": {"manifest": {"lesson": "real"}}},
            headers={"ETag": "strong"},
        )
    ])
    manifest, etag = await mac.get_lesson_manifest(
        manifest_client,
        "http://backend.test/v1/",
        "lesson-1",
        "espTft",
        token="t",
        renderer_capabilities=["v1", "v2"],
        lesson_version=4,
    )
    assert manifest == {"lesson": "real"}
    assert etag == "strong"
    _, manifest_url, manifest_kwargs = manifest_client.calls[0]
    assert manifest_url == "http://backend.test/v1/lessons/lesson-1/manifest"
    assert manifest_kwargs["params"] == {
        "profile": "espTft",
        "version": "4",
        "rendererCapabilities": "v1,v2",
    }
    assert manifest_kwargs["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer t",
        "X-Renderer-Capabilities": "v1,v2",
    }
    assert await mac.get_lesson_manifest(_RequestClient([]), "http://b", "", "profile") == (None, None)

    original_lesson_request = mac._lesson_request_with_retry
    async def _manifest_request(client, method, url, **kwargs):
        return {"data": {"manifest": {"lesson": "L"}}}, {"etag": "weak"}

    monkeypatch.setattr(mac, "_lesson_request_with_retry", _manifest_request)
    manifest, etag = await mac.get_lesson_manifest(_RequestClient([]), "http://b", "lesson", "profile")
    assert manifest == {"lesson": "L"}
    assert etag == "weak"

    async def _manifest_no_headers(client, method, url, **kwargs):
        return None, None

    monkeypatch.setattr(mac, "_lesson_request_with_retry", _manifest_no_headers)
    assert await mac.get_lesson_manifest(_RequestClient([]), "http://b", "lesson", "profile") == (None, None)

    monkeypatch.setattr(mac, "_lesson_request_with_retry", original_lesson_request)

    event_response = _Response({"data": {"stored": True}})
    client = _RequestClient([event_response])
    result = await mac.post_lesson_event(
        client,
        "http://backend.test/v1",
        "device-1",
        {"batchId": "b1", "events": [{"result": "pass", "detail": {"utterance": "hide"}}]},
        token="t",
    )
    assert result == {"stored": True}
    sent_json = client.calls[0][2]["json"]
    assert sent_json["events"] == [{"outcome": "pass"}]
    assert client.calls[0][2]["headers"]["Authorization"] == "Bearer t"

    assert await mac.post_lesson_event(_RequestClient([_Response({"queued": True})]), "http://b", "d", {}) == {"queued": True}
    assert await mac.post_lesson_event(_RequestClient([_Response([])]), "http://b", "d", {}) is None
