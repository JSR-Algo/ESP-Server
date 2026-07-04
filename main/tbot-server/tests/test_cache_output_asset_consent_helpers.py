import base64
import os
import types
from pathlib import Path

import pytest

from config import device_token_client
from config import voice_consent_client
from config.voice_consent_client import VoiceConsentClient
from core.api.lesson_asset_handler import LessonAssetHandler
from core.utils.cache.strategies import CacheEntry, CacheStrategy
from core.utils import output_counter


def _token(value):
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class _Logger:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    def bind(self, **kwargs):
        if self.fail:
            raise RuntimeError("closed")
        self.messages.append(("bind", kwargs))
        return self

    def warning(self, message):
        self.messages.append(("warning", message))


class _Conn:
    def __init__(self):
        self.device_id = "14:c1:9f:d1:a8:48"
        self.config = {"server": {"api_url": "https://backend.test/v1/"}}
        self.logger = _Logger()


def test_cache_entry_strategy_enum_expiry_and_touch(monkeypatch):
    assert CacheStrategy.TTL.value == "ttl"
    assert CacheStrategy.LRU.value == "lru"
    assert CacheStrategy.FIXED_SIZE.value == "fixed_size"
    assert CacheStrategy.TTL_LRU.value == "ttl_lru"

    entry = CacheEntry("value", timestamp=10.0)
    assert entry.last_access == 10.0
    assert not entry.is_expired()

    expiring = CacheEntry("value", timestamp=10.0, ttl=5.0)
    monkeypatch.setattr("core.utils.cache.strategies.time.time", lambda: 14.0)
    assert not expiring.is_expired()

    monkeypatch.setattr("core.utils.cache.strategies.time.time", lambda: 16.0)
    assert expiring.is_expired()
    expiring.touch()
    assert expiring.last_access == 16.0
    assert expiring.access_count == 1


def test_output_counter_resets_on_first_call_date_change_and_empty_device(monkeypatch):
    class _DayOneDatetime:
        @staticmethod
        def now():
            return types.SimpleNamespace(date=lambda: output_counter.datetime.date(2026, 1, 1))

    class _DayTwoDatetime:
        @staticmethod
        def now():
            return types.SimpleNamespace(date=lambda: output_counter.datetime.date(2026, 1, 2))

    output_counter.reset_device_output()
    output_counter._last_check_date = None
    monkeypatch.setattr(output_counter.datetime, "datetime", _DayOneDatetime)

    assert not output_counter.check_device_output_limit("", 1)
    assert output_counter.get_device_output("device-1") == 0
    output_counter.add_device_output("device-1", 3)
    output_counter.add_device_output("device-1", 4)
    assert output_counter.get_device_output("device-1") == 7
    assert output_counter.check_device_output_limit("device-1", 7)

    monkeypatch.setattr(output_counter.datetime, "datetime", _DayTwoDatetime)
    output_counter.add_device_output("device-1", 2)
    assert output_counter.get_device_output("device-1") == 2
    output_counter.reset_device_output()
    assert output_counter.get_device_output("device-1") == 0


@pytest.mark.asyncio
async def test_lesson_asset_handler_serves_safe_cached_files_and_rejects_bad_paths(tmp_path):
    cache_root = tmp_path / "assets"
    safe_dir = cache_root / "lesson-1"
    safe_dir.mkdir(parents=True)
    asset = safe_dir / "scene.png"
    asset.write_bytes(b"png")

    handler = LessonAssetHandler({"lesson": {"asset_cache_root": str(cache_root)}})
    good_request = types.SimpleNamespace(
        match_info={"cacheToken": _token("lesson-1"), "assetKey": "scene.png"}
    )
    response = await handler.handle_get(good_request)
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert Path(response._path) == asset

    render_safe = safe_dir / "scene.png.render.jpg"
    render_safe.write_bytes(b"baseline-jpeg")
    derivative_response = await handler.handle_get(good_request)
    assert Path(derivative_response._path) == render_safe

    bad_token = await handler.handle_get(types.SimpleNamespace(match_info={"cacheToken": "???", "assetKey": "x"}))
    missing_key = await handler.handle_get(types.SimpleNamespace(match_info={"cacheToken": _token("lesson-1"), "assetKey": ""}))
    missing_file = await handler.handle_get(types.SimpleNamespace(match_info={"cacheToken": _token("lesson-1"), "assetKey": "missing.png"}))
    escaped_cache = await handler.handle_get(types.SimpleNamespace(match_info={"cacheToken": _token("../outside"), "assetKey": "scene.png"}))

    assert bad_token.status == 400
    assert missing_key.status == 400
    assert escaped_cache.status == 400
    assert missing_file.status == 404
    assert LessonAssetHandler._decode_cache_token("not ascii \u2603") == ""


def test_lesson_asset_handler_defaults_malformed_lesson_config():
    handler = LessonAssetHandler({"lesson": "bad"})

    assert handler.cache_root.endswith(os.path.join("data", "lesson_assets"))

@pytest.mark.asyncio
async def test_voice_consent_client_edges_and_singleton(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"active": False}}

    class _AutoClient:
        def __init__(self, timeout):
            self.timeout = timeout
            self.closed = False

        async def post(self, *_args, **_kwargs):
            return types.SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": {"deviceUuid": "device-uuid", "token": "jwt"}},
            )

        async def get(self, *_args, **_kwargs):
            return _Response()

        async def aclose(self):
            self.closed = True

    created = []

    def make_client(timeout):
        client = _AutoClient(timeout)
        created.append(client)
        return client

    conn = _Conn()
    monkeypatch.setattr(voice_consent_client.httpx, "AsyncClient", make_client)
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("TBOT_VOICE_CONSENT_NEGATIVE_CACHE_TTL_SECONDS", "bad-float")
    device_token_client._cache.clear()

    consent = VoiceConsentClient()
    assert not await consent.ensure_voice_allowed(conn)
    assert created[0].timeout == 5.0
    assert created[0].closed is True

    assert consent._base_url(types.SimpleNamespace(config=None)) == ""
    assert consent._base_url(types.SimpleNamespace(config={"server": None})) == ""
    assert consent._env_float("MISSING_FLOAT", 7.0) == 7.0
    consent._log(types.SimpleNamespace(logger=None), "warning", "ignored")
    consent._log(types.SimpleNamespace(logger=_Logger(fail=True)), "warning", "ignored")

    voice_consent_client._client = None
    first = voice_consent_client.get_voice_consent_client()
    second = voice_consent_client.get_voice_consent_client()
    assert first is second


@pytest.mark.asyncio
async def test_voice_consent_client_denies_missing_inputs_identity_and_backend_errors(monkeypatch):
    conn = _Conn()
    consent = VoiceConsentClient(client=types.SimpleNamespace())

    monkeypatch.delenv("TBOT_BYPASS_VOICE_CONSENT", raising=False)
    monkeypatch.delenv("TBOT_DEVICE_MINT_SECRET", raising=False)
    assert not await consent.ensure_voice_allowed(conn)
    assert any("missing device/backend/secret" in message for level, message in conn.logger.messages if level == "warning")

    async def missing_identity(*_args, **_kwargs):
        return None, None

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setattr(voice_consent_client, "resolve_device_identity", missing_identity)
    assert not await consent.ensure_voice_allowed(conn)

    class _ExplodingClient:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("backend down")

    async def resolved_identity(*_args, **_kwargs):
        return "device-uuid", "jwt"

    monkeypatch.setattr(voice_consent_client, "resolve_device_identity", resolved_identity)
    assert not await VoiceConsentClient(client=_ExplodingClient()).ensure_voice_allowed(conn)
