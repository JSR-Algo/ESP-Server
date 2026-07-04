import inspect
import builtins
import types

import pytest

from core.voice import live_admission as live_module
from core.voice.live_admission import (
    AdmissionDecision,
    AdmissionReason,
    InMemoryLiveAdmissionStore,
    InMemoryResumptionStore,
    LiveAdmissionGate,
    RedisLiveStateStore,
    _call_store,
    create_live_state_store,
)


class _Redis:
    def __init__(self, *, close_result=None, use_close=False):
        self.values = {}
        self.expirations = {}
        self.zsets = {}
        self.close_result = close_result
        self.closed = False
        if use_close:
            self.close = self._close
        else:
            self.aclose = self._close

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.expirations[key] = ex

    async def get(self, key):
        return self.values.get(key)

    async def incrbyfloat(self, key, amount):
        self.values[key] = str(float(self.values.get(key, 0.0)) + float(amount))

    async def expire(self, key, seconds):
        self.expirations[key] = seconds

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, _minimum, maximum):
        zset = self.zsets.setdefault(key, {})
        for member, score in list(zset.items()):
            if score <= float(maximum):
                zset.pop(member)

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def _close(self):
        self.closed = True
        return self.close_result


class _SyncFallbackStore:
    def __init__(self):
        self.usage = 0.0
        self.reconnects = 0

    def add_usage(self, _device_id, _household_id, seconds):
        self.usage += seconds

    def device_usage_sec(self, _device_id):
        return self.usage

    def household_usage_sec(self, _household_id):
        return self.usage

    def record_reconnect(self, _device_id, *, now=None):
        self.reconnects += 1

    def reconnect_count(self, _device_id, *, window_sec, now=None):
        return self.reconnects


class _AwaitableSyncStore(_SyncFallbackStore):
    async def _value(self):
        return self.usage

    def device_usage_sec(self, _device_id):
        return self._value()


def test_live_admission_result_compares_to_decision_and_not_other_values():
    result = live_module.LiveAdmissionResult(AdmissionDecision.ALLOW_LIVE)

    assert result == AdmissionDecision.ALLOW_LIVE
    assert result != "allow_live"


@pytest.mark.asyncio
async def test_in_memory_resumption_store_ignores_empty_handles():
    store = InMemoryResumptionStore()

    await store.save("device-1", "")
    await store.save("device-1", "handle")

    assert store.saved == [("device-1", "handle")]
    assert await store.load("device-1") == "handle"


@pytest.mark.asyncio
async def test_in_memory_live_store_noops_and_window_prunes_reconnects():
    store = InMemoryLiveAdmissionStore()

    await store.save("device-1", "")
    await store.save("device-1", "handle")
    store.add_usage("", "", -5)
    await store.add_usage_async("device-1", "house-1", 30)
    store.record_reconnect("", now=1)
    await store.record_reconnect_async("device-1", now=1)
    await store.record_reconnect_async("device-1", now=100)

    assert await store.load("device-1") == "handle"
    assert await store.device_usage_sec_async("device-1") == 30
    assert await store.household_usage_sec_async("house-1") == 30
    assert store.reconnect_count("", window_sec=60, now=100) == 0
    assert store.reconnect_count("device-1", window_sec=60, now=100) == 1


@pytest.mark.asyncio
async def test_redis_live_state_store_empty_ids_bytes_usage_and_close_variants():
    redis = _Redis()
    store = RedisLiveStateStore(redis, namespace="test", day_key="2026-06-20")

    await store.save("", "handle")
    assert await store.load("") is None
    await store.save("device-1", "handle")
    redis.values[store._resumption_key("device-1")] = b"bytes-handle"
    assert await store.load("device-1") == "bytes-handle"
    assert await store.device_usage_sec_async("") == 0.0
    assert await store.household_usage_sec_async("") == 0.0
    assert await store.household_usage_sec_async("missing-house") == 0.0
    assert await store.reconnect_count_async("", window_sec=60) == 0
    await store.record_reconnect_async("")

    redis.values[store._budget_key("device", "device-1")] = b"42.5"
    assert await store.device_usage_sec_async("device-1") == 42.5
    assert store._day_key() == "2026-06-20"
    assert store._reconnect_key("device-1") == "tbot:live:test:reconnect:device-1"
    await store.close()
    assert redis.closed

    async def close_async():
        return "closed"

    redis_async_close = _Redis(close_result=close_async(), use_close=True)
    await RedisLiveStateStore(redis_async_close).close()
    assert redis_async_close.closed


@pytest.mark.asyncio
async def test_redis_live_state_store_defaults_malformed_usage_values():
    redis = _Redis()
    store = RedisLiveStateStore(redis, namespace="test", day_key="2026-06-20")
    redis.values[store._budget_key("device", "device-1")] = "bad"
    redis.values[store._budget_key("household", "house-1")] = b"bad"

    assert await store.device_usage_sec_async("device-1") == 0.0
    assert await store.household_usage_sec_async("house-1") == 0.0


def test_create_live_state_store_defaults_and_redis_path(monkeypatch):
    assert isinstance(create_live_state_store({}), InMemoryLiveAdmissionStore)
    assert isinstance(create_live_state_store({"live_state": "bad"}), InMemoryLiveAdmissionStore)

    created = []

    class _RedisFactory:
        @staticmethod
        def from_url(url, decode_responses=True):
            created.append((url, decode_responses))
            return _Redis()

    monkeypatch.setitem(
        __import__("sys").modules,
        "redis.asyncio",
        types.SimpleNamespace(Redis=_RedisFactory),
    )
    store = create_live_state_store(
        {
            "live_state": {
                "redis_url": "redis://localhost:6379/0",
                "namespace": "ns",
                "resumption_ttl_sec": 1,
                "budget_ttl_sec": 2,
                "reconnect_ttl_sec": 3,
            }
        }
    )

    assert isinstance(store, RedisLiveStateStore)
    assert created == [("redis://localhost:6379/0", True)]
    assert store.namespace == "ns"
    assert store.resumption_ttl_sec == 1
    assert store.budget_ttl_sec == 2
    assert store.reconnect_ttl_sec == 3


def test_create_live_state_store_defaults_invalid_redis_ttls(monkeypatch):
    class _RedisFactory:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return _Redis()

    monkeypatch.setitem(
        __import__("sys").modules,
        "redis.asyncio",
        types.SimpleNamespace(Redis=_RedisFactory),
    )
    store = create_live_state_store(
        {
            "live_state": {
                "redis_url": "redis://localhost:6379/0",
                "resumption_ttl_sec": "bad",
                "budget_ttl_sec": None,
                "reconnect_ttl_sec": 0,
            }
        }
    )

    assert isinstance(store, RedisLiveStateStore)
    assert store.resumption_ttl_sec == 86400
    assert store.budget_ttl_sec == 172800
    assert store.reconnect_ttl_sec == 300


def test_create_live_state_store_falls_back_when_redis_package_missing(monkeypatch):
    real_import = builtins.__import__

    def import_without_redis(name, *args, **kwargs):
        if name == "redis.asyncio" or name == "redis":
            raise ModuleNotFoundError("No module named 'redis'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_redis)

    store = create_live_state_store(
        {"live_state": {"redis_url": "redis://localhost:6379/0"}}
    )

    assert isinstance(store, InMemoryLiveAdmissionStore)


def test_gate_from_config_fallbacks_and_sync_noops():
    assert isinstance(LiveAdmissionGate.from_config({"live_admission": "bad"}), LiveAdmissionGate)
    assert isinstance(
        LiveAdmissionGate.from_config({"server": {"audio_admission": "bad"}}),
        LiveAdmissionGate,
    )
    gate = LiveAdmissionGate.from_config(
        {"server": {"audio_admission": {"daily_live_minutes": 1, "reconnect_limit": 0}}}
    )

    assert gate.daily_device_minutes == 1
    assert gate.reconnect_limit == 0
    assert gate.admit("", None) == AdmissionDecision.ALLOW_LIVE
    assert gate._over_device_budget("") is False
    assert gate._over_household_budget("") is False
    assert gate._over_reconnect_limit("device-1") is False
    gate.record_live_usage("device-1", "house-1", 10)
    gate.record_reconnect("device-1")


def test_gate_from_config_defaults_invalid_numeric_limits():
    gate = LiveAdmissionGate.from_config(
        {
            "live_admission": {
                "daily_device_minutes": "bad",
                "daily_household_minutes": "bad",
                "reconnect_window_sec": "bad",
                "reconnect_limit": "bad",
            }
        }
    )

    assert gate.daily_device_minutes is None
    assert gate.daily_household_minutes is None
    assert gate.reconnect_window_sec == 60
    assert gate.reconnect_limit == 5
    gate.record_reconnect("device-1", now=1)
    assert gate.admit("device-1", "house-1", now=2).decision == AdmissionDecision.ALLOW_LIVE


@pytest.mark.asyncio
async def test_gate_async_falls_back_to_sync_store_methods_and_budget_reasons():
    store = _SyncFallbackStore()
    gate = LiveAdmissionGate(store, daily_device_minutes=1, daily_household_minutes=1, reconnect_limit=2)

    await gate.record_live_usage_async("device-1", "house-1", 61)
    await gate.record_reconnect_async("device-1", now=1)
    await gate.record_reconnect_async("device-1", now=2)

    decision = await gate.admit_async("device-1", "house-1", now=3)
    assert decision.decision == AdmissionDecision.FRIENDLY_BREAK
    assert decision.reason == AdmissionReason.RECONNECT_STORM

    gate = LiveAdmissionGate(store, daily_device_minutes=1, daily_household_minutes=1, reconnect_limit=0)
    decision = await gate.admit_async("device-1", "house-1")
    assert decision.reason == AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED

    gate = LiveAdmissionGate(store, daily_device_minutes=10, daily_household_minutes=1, reconnect_limit=0)
    decision = await gate.admit_async("device-1", "house-1")
    assert decision.reason == AdmissionReason.HOUSEHOLD_DAILY_BUDGET_EXHAUSTED

    gate = LiveAdmissionGate(store, daily_device_minutes=None, daily_household_minutes=None, reconnect_limit=0)
    decision = await gate.admit_async("device-1", "house-1")
    assert decision.decision == AdmissionDecision.ALLOW_LIVE
    assert await gate._over_device_budget_async("") is False
    assert await gate._over_household_budget_async("") is False
    assert await gate._over_reconnect_limit_async("") is False

    async_store_gate = LiveAdmissionGate(InMemoryLiveAdmissionStore())
    await async_store_gate.record_reconnect_async("device-2", now=10)
    assert async_store_gate.store.reconnect_count("device-2", window_sec=60, now=10) == 1


@pytest.mark.asyncio
async def test_call_store_awaits_sync_method_returning_awaitable():
    store = _AwaitableSyncStore()
    store.usage = 12

    value = store.device_usage_sec("device-1")
    assert inspect.isawaitable(value)
    assert await value == 12
    assert await _call_store(store, "missing_async", "device_usage_sec", "device-1") == 12
