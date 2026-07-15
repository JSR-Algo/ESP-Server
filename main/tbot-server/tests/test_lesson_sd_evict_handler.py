import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

CANONICAL = "pip-farm-3m/v1-" + "a" * 64


class _FakeRequest:
    def __init__(self, *, secret="secret", body=None, json_error=None, device_id="device-1"):
        self.match_info = {"deviceId": device_id}
        self.headers = {}
        if secret is not None:
            self.headers["X-Mint-Secret"] = secret
        self._body = {"cacheKey": CANONICAL} if body is None else body
        self._json_error = json_error

    async def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._body


def _payload(response):
    return json.loads(response.text)


@pytest.fixture(autouse=True)
def _mint_secret(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")


@pytest.mark.asyncio
async def test_secret_not_configured_returns_503_before_lookup_or_service(monkeypatch):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    monkeypatch.delenv("TBOT_DEVICE_MINT_SECRET", raising=False)
    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock()

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=AsyncMock()) as evict:
        response = await handler.handle_post(_FakeRequest(secret=None))

    assert response.status == 503
    assert _payload(response)["error"] == "MINT_SECRET_NOT_CONFIGURED"
    handler._shared._find_connection.assert_not_awaited()
    evict.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "wrong"])
async def test_missing_or_wrong_secret_returns_401_without_device_lookup(secret):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock()

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=AsyncMock()) as evict:
        response = await handler.handle_post(
            _FakeRequest(secret=secret, device_id="private-device-uuid")
        )

    assert response.status == 401
    assert "private-device-uuid" not in response.text
    handler._shared._find_connection.assert_not_awaited()
    evict.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "json_error"),
    [
        (None, ValueError("invalid json private-token")),
        ([], None),
        ({}, None),
        ({"cacheKey": None}, None),
    ],
)
async def test_invalid_json_object_or_missing_cache_key_returns_400(body, json_error):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock()
    request = _FakeRequest(body=body, json_error=json_error)
    if body is None and json_error is None:
        request._body = {}

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=AsyncMock()) as evict:
        response = await handler.handle_post(request)

    assert response.status == 400
    assert _payload(response)["error"] == "INVALID_REQUEST"
    assert "private-token" not in response.text
    handler._shared._find_connection.assert_not_awaited()
    evict.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_canonical_key_returns_400_without_mcp():
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock()

    response = await handler.handle_post(_FakeRequest(body={"cacheKey": "../secret"}))

    assert response.status == 400
    assert _payload(response) == {
        "data": {
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "invalid_cache_key",
        }
    }
    handler._shared._find_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_offline_returns_202_without_deletion_claim():
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock(return_value=None)

    response = await handler.handle_post(_FakeRequest())

    assert response.status == 202
    assert _payload(response) == {
        "data": {
            "cacheKey": CANONICAL,
            "status": "device-offline",
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "device-offline",
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "voice-busy",
        "lesson-render-busy",
        "protected-active",
        "protected-candidate",
        "protected-preloading",
        "protected-current",
        "protected-previous-known-good",
        "protected-activation-current",
        "protected-activation-previous-known-good",
        "protected-activation-candidate",
        "firmware-timeout",
        "firmware-unknown-tool",
        "firmware-malformed-result",
        "firmware-key-mismatch",
        "firmware-refused",
    ],
)
async def test_busy_protected_and_firmware_failure_return_sanitized_409(reason):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler
    from core.lesson.sd_pack_evict import CacheEvictionRefused

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock(return_value=object())
    service = AsyncMock(side_effect=CacheEvictionRefused(reason))

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=service):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 409
    assert _payload(response) == {
        "data": {
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": reason,
        }
    }
    assert "path" not in response.text.lower()
    assert "token" not in response.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {
                "cacheKey": CANONICAL,
                "status": "evicted",
                "evicted": True,
                "notFound": False,
                "fileCount": 4,
                "reason": "evicted",
            },
            {
                "cacheKey": CANONICAL,
                "status": "evicted",
                "evicted": True,
                "notFound": False,
                "fileCount": 4,
                "reason": "evicted",
            },
        ),
        (
            {
                "cacheKey": CANONICAL,
                "status": "not_found",
                "evicted": False,
                "notFound": True,
                "fileCount": 0,
                "reason": "not_found",
            },
            {
                "cacheKey": CANONICAL,
                "status": "not_found",
                "evicted": False,
                "notFound": True,
                "fileCount": 0,
                "reason": "not_found",
            },
        ),
    ],
)
async def test_success_returns_only_normalized_result(result, expected):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock(return_value=object())
    service = AsyncMock(return_value={**result, "privatePath": "/sdcard/private", "token": "secret"})

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=service):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 200
    assert _payload(response) == {"data": expected}
    service.assert_awaited_once_with(
        handler.connections,
        "device-1",
        CANONICAL,
        find_connection=handler._shared._find_connection,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"cacheKey": "pip-farm-5m/v1-" + "b" * 64},
        {"evicted": False},
        {"notFound": True},
        {"fileCount": -1},
        {"reason": "private-refusal"},
        {
            "status": "not_found",
            "evicted": False,
            "notFound": True,
            "fileCount": 1,
            "reason": "not_found",
        },
    ],
)
async def test_incoherent_service_result_never_claims_deletion(updates):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock(return_value=object())
    result = {
        "cacheKey": CANONICAL,
        "status": "evicted",
        "evicted": True,
        "notFound": False,
        "fileCount": 4,
        "reason": "evicted",
    }
    result.update(updates)

    with patch(
        "core.api.lesson_sd_evict_handler.evict_exact_cache_key",
        new=AsyncMock(return_value=result),
    ):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 409
    assert _payload(response) == {
        "data": {
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "firmware-refused",
        }
    }


@pytest.mark.asyncio
async def test_arbitrary_service_exception_is_sanitized():
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    handler._shared._find_connection = AsyncMock(return_value=object())
    service = AsyncMock(side_effect=RuntimeError("/sdcard/private token=secret config=raw"))

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=service):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 409
    assert _payload(response) == {
        "data": {
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "firmware-refused",
        }
    }
    assert "sdcard" not in response.text
    assert "secret" not in response.text
    assert "config" not in response.text


@pytest.mark.asyncio
async def test_unknown_refusal_code_is_not_reflected():
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler
    from core.lesson.sd_pack_evict import CacheEvictionRefused

    handler = LessonSdEvictHandler({}, {})
    service = AsyncMock(
        side_effect=CacheEvictionRefused("private-token=secret path=/sdcard/private")
    )

    with patch("core.api.lesson_sd_evict_handler.evict_exact_cache_key", new=service):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 409
    assert _payload(response)["data"]["reason"] == "firmware-refused"
    assert "private" not in response.text
    assert "secret" not in response.text
    assert "sdcard" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, [], "private-token=secret"])
async def test_non_object_service_result_is_sanitized(result):
    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler

    handler = LessonSdEvictHandler({}, {})
    with patch(
        "core.api.lesson_sd_evict_handler.evict_exact_cache_key",
        new=AsyncMock(return_value=result),
    ):
        response = await handler.handle_post(_FakeRequest())

    assert response.status == 409
    assert _payload(response)["data"]["reason"] == "firmware-refused"
    assert "private" not in response.text
    assert "secret" not in response.text


def test_http_server_registers_exact_route_once_and_no_variant():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "core" / "http_server.py").read_text()
    route = '"/internal/devices/{deviceId}/lesson-assets/evict-cache-key"'

    assert source.count(route) == 1
    assert "lesson_sd_evict_handler.handle_post" in source
    assert "evict-cache-key?" not in source


@pytest.mark.asyncio
async def test_http_server_builds_one_exact_post_route_without_network():
    from core.http_server import SimpleHttpServer

    captured = {}

    class _Runner:
        def __init__(self, app):
            captured["app"] = app

        async def setup(self):
            return None

    class _Site:
        def __init__(self, runner, host, port):
            captured["site"] = (runner, host, port)

        async def start(self):
            return None

    server = SimpleHttpServer(
        {"server": {"http_port": 8003, "auth_key": "test-key"}}, {}
    )
    runner_patch = patch("core.http_server.web.AppRunner", side_effect=_Runner)
    site_patch = patch("core.http_server.web.TCPSite", side_effect=_Site)
    sleep_patch = patch(
        "core.http_server.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    )
    with runner_patch, site_patch, sleep_patch:
        with pytest.raises(asyncio.CancelledError):
            await server.start()

    target = "/internal/devices/{deviceId}/lesson-assets/evict-cache-key"
    matching = [
        route
        for route in captured["app"].router.routes()
        if "evict-cache-key" in route.resource.canonical
    ]
    assert len(matching) == 1
    assert matching[0].resource.canonical == target
    assert matching[0].method == "POST"
    assert matching[0].handler == server.lesson_sd_evict_handler.handle_post
