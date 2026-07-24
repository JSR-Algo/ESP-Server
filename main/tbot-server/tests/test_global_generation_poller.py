from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import pytest

from core.lesson.global_generation_poller import (
    GlobalGenerationPoller,
    canonical_json,
)

CMS_URL = "https://cms.example/v1/public/lesson-assets/latest"
NOW = datetime(2026, 7, 24, 0, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _asset(
    key: str = "robot teach.png",
    *,
    size: int = 0,
    cache_key: str = f"lesson-a/v3-{HASH_B}",
) -> dict:
    encoded = quote(key, safe="-_.!~*'()")
    url = f"https://cdn.example/assets/{encoded}"
    path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    return {
        "key": key,
        "sdPath": path,
        "localPath": path,
        "onlineUrl": url,
        "url": url,
        "sha256": HASH_A,
        "size": size,
        "mediaType": "image/png",
        "critical": True,
    }


def _pack(*, lesson_id: str = "lesson-a", classification: str = "curriculum") -> dict:
    checksum = HASH_B
    cache_key = f"{lesson_id}/v3-{checksum}"
    return {
        "lessonId": lesson_id,
        "lessonVersion": 3,
        "profile": "espTft",
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "classification": classification,
        "assets": [_asset(cache_key=cache_key)],
    }


def _payload(*, generation: int = 8, index: list[dict] | None = None) -> dict:
    packs = deepcopy(index if index is not None else [_pack()])
    return {
        "data": {
            "generation": generation,
            "publishedAt": "2026-07-24T00:00:00.000Z",
            "indexChecksum": hashlib.sha256(canonical_json(packs)).hexdigest(),
            "curriculumLessonCount": sum(
                pack["classification"] == "curriculum" for pack in packs
            ),
            "packCount": len(packs),
            "index": packs,
        }
    }


class FakeStore:
    def __init__(self, *, etag: str | None = None) -> None:
        self.restored_etag = etag
        self.desired: list[tuple[int, str, str]] = []
        self.polled: list[str] = []

    async def snapshot(self) -> dict:
        return {"etag": self.restored_etag, "acceptedGeneration": 7}

    async def set_desired(self, generation: int, index_checksum: str, etag: str) -> None:
        self.desired.append((generation, index_checksum, etag))

    async def mark_polled(self, polled_at: str) -> None:
        self.polled.append(polled_at)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _response(payload: dict, *, etag: str | None = None, status: int = 200) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if etag is not None:
        headers["etag"] = etag
    return httpx.Response(status, headers=headers, content=json.dumps(payload).encode())


def _config() -> dict:
    return {
        "lesson": {
            "generation_cms_url": CMS_URL,
            "asset_allowed_origins": "https://cdn.example",
        }
    }


@pytest.mark.asyncio
async def test_valid_200_stores_desired_before_callback_and_then_uses_etag() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = FakeStore()
    events: list[tuple[str, object]] = []

    async def callback(data: dict) -> None:
        events.append(("callback", deepcopy(data)))
        assert store.desired == [(8, checksum, etag)]

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    assert (await poller.run_once())["state"] == "accepted"
    assert (await poller.run_once())["state"] == "not_modified"
    assert "if-none-match" not in requests[0].headers
    assert requests[1].headers["if-none-match"] == etag
    assert len(events) == 1


@pytest.mark.asyncio
async def test_cold_start_does_not_send_restored_etag_and_rejects_304() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(304)

    store = FakeStore(etag='"lesson-assets-g7-restored"')
    poller = GlobalGenerationPoller(
        _config(), store, lambda data: None, http=_client(handler), clock=lambda: NOW
    )
    result = await poller.run_once()
    assert result == {"state": "rejected", "errorCode": "cms_cold_304"}
    assert "if-none-match" not in requests[0].headers
    assert store.desired == []


@pytest.mark.asyncio
async def test_valid_redirects_are_manual_and_limited_to_two_same_origin_hops() -> None:
    urls: list[str] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if len(urls) == 1:
            return httpx.Response(302, headers={"location": "/hop-one"})
        if len(urls) == 2:
            return httpx.Response(307, headers={"location": "https://cms.example/hop-two"})
        return _response(payload, etag=f'"lesson-assets-g8-{checksum}"')

    store = FakeStore()
    poller = GlobalGenerationPoller(
        _config(), store, lambda data: None, http=_client(handler), clock=lambda: NOW
    )
    assert (await poller.run_once())["state"] == "accepted"
    assert urls == [CMS_URL, "https://cms.example/hop-one", "https://cms.example/hop-two"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("https://evil.example/index", "cms_redirect_rejected"),
        ("http://cms.example/index", "cms_redirect_rejected"),
        ("https://cms.example:444/index", "cms_redirect_rejected"),
        ("", "cms_redirect_rejected"),
    ],
)
async def test_redirect_must_preserve_exact_cms_origin(location: str, code: str) -> None:
    client = _client(lambda request: httpx.Response(302, headers={"location": location}))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_third_redirect_is_rejected() -> None:
    client = _client(lambda request: httpx.Response(302, headers={"location": "/again"}))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_too_many_redirects"


@pytest.mark.asyncio
async def test_checksum_or_etag_mismatch_never_replaces_prior_state() -> None:
    payload = _payload()
    payload["data"]["indexChecksum"] = "0" * 64
    store = FakeStore()
    callback_calls: list[dict] = []
    client = _client(
        lambda request: _response(payload, etag=f'"lesson-assets-g8-{"0" * 64}"')
    )
    result = await GlobalGenerationPoller(
        _config(), store, callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_index_checksum_mismatch"
    assert store.desired == []
    assert callback_calls == []

    valid = _payload()
    wrong_etag_client = _client(lambda request: _response(valid, etag='"wrong"'))
    result = await GlobalGenerationPoller(
        _config(), store, callback_calls.append, http=wrong_etag_client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_etag_mismatch"
    assert store.desired == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda body: body.update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"]["index"][0].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"].update(generation=0), "cms_invalid_generation"),
        (lambda body: body["data"].update(generation=1.0), "cms_invalid_generation"),
        (lambda body: body["data"].update(indexChecksum=HASH_A.upper()), "cms_invalid_checksum"),
        (lambda body: body["data"]["index"][0].update(profile="web"), "cms_invalid_profile"),
        (lambda body: body["data"]["index"][0].update(classification="private"), "cms_invalid_classification"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(size=-1), "cms_invalid_asset_size"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(size=1.0), "cms_invalid_asset_size"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(url="https://cdn.example/other"), "cms_asset_alias_mismatch"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(onlineUrl="http://cdn.example/x", url="http://cdn.example/x"), "cms_asset_url_rejected"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(onlineUrl="https://other.example/x", url="https://other.example/x"), "cms_asset_origin_rejected"),
    ],
)
async def test_strict_schema_rejections_have_sanitized_codes(mutate, code: str) -> None:
    payload = _payload()
    mutate(payload)
    # Recompute the checksum when testing a semantic field rather than checksum integrity.
    if code not in {"cms_invalid_checksum", "cms_index_checksum_mismatch"}:
        payload["data"]["indexChecksum"] = hashlib.sha256(
            canonical_json(payload["data"]["index"])
        ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    client = _client(
        lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"')
    )
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_requires_unique_js_utf16_sorted_packs_and_assets() -> None:
    first = _pack(lesson_id="a-astral")
    second = _pack(lesson_id="a-astral")
    duplicate = _payload(index=[first, second])
    checksum = duplicate["data"]["indexChecksum"]
    client = _client(lambda request: _response(duplicate, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_duplicate_lesson_id"

    unsorted = _payload(index=[_pack(lesson_id="z-last"), _pack(lesson_id="a-first")])
    checksum = unsorted["data"]["indexChecksum"]
    client = _client(lambda request: _response(unsorted, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_index_not_sorted"

    asset_unsorted_pack = _pack()
    cache_key = asset_unsorted_pack["cacheKey"]
    asset_unsorted_pack["assets"] = [
        _asset("\ue000", cache_key=cache_key),
        _asset("\U00010000", cache_key=cache_key),
    ]
    asset_unsorted = _payload(index=[asset_unsorted_pack])
    checksum = asset_unsorted["data"]["indexChecksum"]
    client = _client(
        lambda request: _response(asset_unsorted, etag=f'"lesson-assets-g8-{checksum}"')
    )
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_assets_not_sorted"


@pytest.mark.asyncio
async def test_sd_path_uses_javascript_encode_uri_component_and_200_byte_boundary() -> None:
    payload = _payload()
    asset = payload["data"]["index"][0]["assets"][0]
    asset["key"] = "bang!~'()"
    encoded = "bang!~'()"
    cache_key = payload["data"]["index"][0]["cacheKey"]
    path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    asset["sdPath"] = asset["localPath"] = path
    payload["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(payload["data"]["index"])
    ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    store = FakeStore()
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    assert (
        await GlobalGenerationPoller(
            _config(), store, lambda data: None, http=client, clock=lambda: NOW
        ).run_once()
    )["state"] == "accepted"

    too_long = _payload()
    too_long["data"]["index"][0]["assets"][0]["key"] = "x" * 201
    too_long["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(too_long["data"]["index"])
    ).hexdigest()
    checksum = too_long["data"]["indexChecksum"]
    client = _client(lambda request: _response(too_long, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_asset_key_too_long"


@pytest.mark.asyncio
async def test_cache_key_over_200_bytes_fails_with_stable_sanitized_code() -> None:
    lesson_id = "a" * 128
    version = 1_234_567_890
    cache_key = f"{lesson_id}/v{version}-{HASH_B}"
    pack = _pack(lesson_id=lesson_id)
    pack["lessonVersion"] = version
    pack["cacheKey"] = cache_key
    pack["assets"] = [_asset(cache_key=cache_key)]
    payload = _payload(index=[pack])
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert len(cache_key.encode("ascii")) == 205
    assert result == {"state": "rejected", "errorCode": "cms_cache_key_too_long"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published_at",
    ["2026-07-24 00:00:00+00:00", "2026-07-24T00:00+00:00"],
)
async def test_published_at_rejects_non_rfc3339_datetime_forms(published_at: str) -> None:
    payload = _payload()
    payload["data"]["publishedAt"] = published_at
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "cms_invalid_published_at"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published_at",
    [
        "2026-07-24T00:00:00Z",
        "2026-07-24T00:00:00.123456Z",
        "2026-07-24T00:00:00+00:00",
        "2026-07-24T00:00:00.1+00:00",
    ],
)
async def test_published_at_accepts_strict_utc_rfc3339(published_at: str) -> None:
    payload = _payload()
    payload["data"]["publishedAt"] = published_at
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["state"] == "accepted"


@pytest.mark.parametrize(
    "origin",
    ["https://bad host", "https://bad\thost", "https://-bad.example", "https://bad_.example"],
)
def test_configured_allowed_origin_rejects_malformed_hostname(origin: str) -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = origin
    with pytest.raises(ValueError, match="HTTPS origins"):
        GlobalGenerationPoller(config, FakeStore(), lambda data: None)


@pytest.mark.asyncio
async def test_malformed_matching_asset_origin_never_reaches_callback() -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = "https://bad host"
    callback_calls: list[dict] = []
    with pytest.raises(ValueError, match="HTTPS origins"):
        GlobalGenerationPoller(config, FakeStore(), callback_calls.append)
    assert callback_calls == []


@pytest.mark.asyncio
async def test_asset_url_with_parser_stripped_hostname_control_is_rejected() -> None:
    payload = _payload()
    asset = payload["data"]["index"][0]["assets"][0]
    asset["onlineUrl"] = asset["url"] = "https://cdn.example\t/assets/file.png"
    payload["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(payload["data"]["index"])
    ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    callback_calls: list[dict] = []
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "cms_asset_url_rejected"}
    assert callback_calls == []


@pytest.mark.parametrize(
    "origin",
    ["https://cdn.example", "https://127.0.0.1:8443", "https://[2001:db8::1]:9443"],
)
def test_configured_allowed_origin_accepts_valid_host_kinds(origin: str) -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = origin
    poller = GlobalGenerationPoller(config, FakeStore(), lambda data: None, http=object())
    assert poller.allowed_origins


@pytest.mark.asyncio
async def test_rejects_nan_oversized_body_and_non_200_without_leaking_details() -> None:
    cases = [
        (httpx.Response(200, content=b'{"data":{"generation":NaN}}'), "cms_invalid_json"),
        (httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)), "cms_response_too_large"),
        (httpx.Response(503, content=b"secret backend body"), "cms_http_status"),
    ]
    for response, code in cases:
        client = _client(lambda request, response=response: response)
        result = await GlobalGenerationPoller(
            _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
        ).run_once()
        assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_start_polls_immediately_and_uses_exact_bounded_backoff() -> None:
    attempts = 0
    sleeps: list[float] = []
    first_request = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        first_request.set()
        return httpx.Response(503)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 8:
            raise asyncio.CancelledError

    poller = GlobalGenerationPoller(
        _config(),
        FakeStore(),
        lambda data: None,
        http=_client(handler),
        clock=lambda: NOW,
        sleep=sleep,
    )
    assert poller.start() is None
    await asyncio.wait_for(first_request.wait(), timeout=1)
    while len(sleeps) < 8:
        await asyncio.sleep(0)
    await poller.stop()
    assert attempts == 8
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300]


@pytest.mark.asyncio
async def test_successful_loop_poll_returns_to_30_second_interval() -> None:
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW, sleep=sleep
    )
    poller.start()
    while not sleeps:
        await asyncio.sleep(0)
    await poller.stop()
    assert sleeps == [30]


@pytest.mark.asyncio
async def test_default_client_disables_redirects_and_environment(monkeypatch) -> None:
    captured: dict = {}

    class Client:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    poller = GlobalGenerationPoller(_config(), FakeStore(), lambda data: None)
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["timeout"].connect == 3
    assert captured["timeout"].read == 15
    await poller.stop()
    assert captured["closed"] is True
