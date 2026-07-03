"""Tests for the keyless Open-Meteo backed ``get_weather`` tool."""

import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

import plugins_func.functions.get_weather as gw  # noqa: E402
from plugins_func.register import Action  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeCache:
    """Minimal stand-in for the global cache_manager."""

    def __init__(self, seed=None):
        self.store = dict(seed or {})
        self.sets = []

    def get(self, cache_type, key, namespace=""):
        return self.store.get((cache_type, key))

    def set(self, cache_type, key, value, ttl=None, namespace=""):
        self.store[(cache_type, key)] = value
        self.sets.append((cache_type, key, value))


class _FakeConn:
    def __init__(self, config=None, client_ip=None):
        self.config = config or {"plugins": {"get_weather": {}}}
        self.client_ip = client_ip


_GEOCODE = {
    "results": [
        {
            "name": "Hà Nội",
            "latitude": 21.03,
            "longitude": 105.85,
            "country": "Việt Nam",
            "admin1": "Hà Nội",
            "timezone": "Asia/Ho_Chi_Minh",
        }
    ]
}

_FORECAST = {
    "current": {
        "temperature_2m": 31.2,
        "relative_humidity_2m": 74,
        "apparent_temperature": 38.1,
        "weather_code": 2,
        "wind_speed_10m": 11.0,
    },
    "daily": {
        "time": [
            "2026-06-24",
            "2026-06-25",
            "2026-06-26",
            "2026-06-27",
            "2026-06-28",
            "2026-06-29",
            "2026-06-30",
        ],
        "weather_code": [95, 80, 3, 2, 1, 0, 61],
        "temperature_2m_max": [33, 32, 31, 34, 35, 36, 30],
        "temperature_2m_min": [26, 26, 25, 27, 27, 28, 25],
    },
}


@pytest.fixture
def patched(monkeypatch):
    """Patch HTTP + cache; return a recorder of geocode/forecast calls."""
    calls = {"geocode": [], "forecast": []}
    cache = _FakeCache()

    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        if url == gw.GEOCODE_URL:
            calls["geocode"].append(params)
            return _FakeResponse(_GEOCODE)
        if url == gw.FORECAST_URL:
            calls["forecast"].append(params)
            return _FakeResponse(_FORECAST)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(gw.requests, "get", fake_get)
    monkeypatch.setattr(
        "core.utils.cache.manager.cache_manager", cache, raising=True
    )
    return types.SimpleNamespace(calls=calls, cache=cache)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_named_location_vi_report(patched):
    conn = _FakeConn()
    result = gw.get_weather(conn, location="Hà Nội", lang="vi_VN")

    assert result.action == Action.REQLLM
    assert "Thời tiết tại Hà Nội" in result.result
    assert "Việt Nam" in result.result
    assert "Dự báo 7 ngày" in result.result
    assert "Dông" in result.result  # weather_code 95 -> Vietnamese
    assert "26~33°C" in result.result  # first day low~high
    # geocoding used the normalised 2-letter language code
    assert patched.calls["geocode"][0]["language"] == "vi"
    # report was cached
    assert patched.cache.sets, "expected the report to be cached"


def test_city_alias_works_for_live_model_compatibility(patched):
    conn = _FakeConn()
    result = gw.get_weather(conn, city="Tokyo")

    assert result.action == Action.REQLLM
    assert patched.calls["geocode"][0]["name"] == "Tokyo"
    assert patched.calls["geocode"][0]["language"] == "vi"

def test_weather_http_timeout_is_bounded_for_voice_latency():
    assert gw.REQUEST_TIMEOUT_SEC <= 3

def test_english_language_path(patched):
    conn = _FakeConn()
    result = gw.get_weather(conn, location="Hanoi", lang="en_US")

    assert "Weather for" in result.result
    assert "7-day forecast" in result.result
    assert "Thunderstorm" in result.result  # weather_code 95 -> English
    assert patched.calls["geocode"][0]["language"] == "en"


def test_default_location_when_no_location_and_no_ip(patched):
    conn = _FakeConn(
        config={"plugins": {"get_weather": {"default_location": "Ho Chi Minh City"}}},
        client_ip=None,
    )
    gw.get_weather(conn, location=None, lang="vi")

    assert patched.calls["geocode"][0]["name"] == "Ho Chi Minh City"


def test_ip_resolved_location(patched, monkeypatch):
    monkeypatch.setattr(
        gw, "get_ip_info", lambda ip, log: {"city": "Da Nang"}
    )
    conn = _FakeConn(client_ip="203.0.113.5")
    gw.get_weather(conn, location=None, lang="vi")

    assert patched.calls["geocode"][0]["name"] == "Da Nang"


def test_place_not_found_skips_forecast(patched, monkeypatch):
    def only_empty_geocode(url, params=None, headers=None, timeout=None):
        assert url == gw.GEOCODE_URL, "forecast must not be called when geocode is empty"
        return _FakeResponse({"results": []})

    monkeypatch.setattr(gw.requests, "get", only_empty_geocode)
    conn = _FakeConn()
    result = gw.get_weather(conn, location="Nowhereville", lang="vi")

    assert result.action == Action.REQLLM
    assert "Không tìm thấy địa điểm" in result.result
    assert patched.calls["forecast"] == []


def test_network_error_is_graceful(patched, monkeypatch):
    def boom(url, params=None, headers=None, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(gw.requests, "get", boom)
    conn = _FakeConn()
    result = gw.get_weather(conn, location="Hà Nội", lang="vi")

    assert result.action == Action.REQLLM
    assert "không lấy được thông tin thời tiết" in result.result


def test_cache_hit_short_circuits(monkeypatch):
    cache = _FakeCache(
        seed={(__import__("core.utils.cache.config", fromlist=["CacheType"]).CacheType.WEATHER,
               "full_weather_om_Hà Nội_vi"): "CACHED REPORT"}
    )

    def must_not_call(*a, **k):
        raise AssertionError("HTTP must not be called on cache hit")

    monkeypatch.setattr(gw.requests, "get", must_not_call)
    monkeypatch.setattr("core.utils.cache.manager.cache_manager", cache, raising=True)

    result = gw.get_weather(_FakeConn(), location="Hà Nội", lang="vi")
    assert result.result == "CACHED REPORT"


def test_short_lang_normalisation():
    assert gw._short_lang("zh_CN") == "zh"
    assert gw._short_lang("vi-VN") == "vi"
    assert gw._short_lang("en_US") == "en"
    assert gw._short_lang("") == "vi"
    assert gw._short_lang(None) == "vi"


def test_description_allows_local_weather_calls():
    desc = gw.GET_WEATHER_FUNCTION_DESC["function"]["description"]
    assert "Never call" not in desc
    # local-weather phrasing is present so the model calls it for "thời tiết hôm nay"
    assert "local" in desc.lower()
