from __future__ import annotations

import time
import os
import inspect
from datetime import datetime, timezone
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4


class AdmissionDecision(Enum):
    ALLOW_LIVE = "allow_live"
    DEGRADE_TTS_ONLY = "degrade_tts_only"
    FRIENDLY_BREAK = "friendly_break"


class AdmissionReason(Enum):
    OK = "ok"
    DEVICE_DAILY_BUDGET_EXHAUSTED = "device_daily_budget_exhausted"
    HOUSEHOLD_DAILY_BUDGET_EXHAUSTED = "household_daily_budget_exhausted"
    RECONNECT_STORM = "reconnect_storm"


@dataclass(frozen=True)
class LiveAdmissionResult:
    decision: AdmissionDecision
    reason: AdmissionReason = AdmissionReason.OK

    def __eq__(self, other):
        if isinstance(other, AdmissionDecision):
            return self.decision == other
        return super().__eq__(other)


class InMemoryResumptionStore:
    """Local stand-in for the Redis resumption-handle store required at scale."""

    def __init__(self):
        self.saved = []
        self._handles = {}

    async def save(self, device_id, handle):
        if not handle:
            return
        self.saved.append((device_id, handle))
        self._handles[device_id] = handle

    async def load(self, device_id):
        return self._handles.get(device_id)


class InMemoryLiveAdmissionStore:
    """In-process budget store; Redis implementation should preserve this contract."""

    def __init__(self):
        self.saved = []
        self._handles = {}
        self._device_usage_sec = defaultdict(float)
        self._household_usage_sec = defaultdict(float)
        self._reconnects = defaultdict(deque)

    async def save(self, device_id, handle):
        if not handle:
            return
        self.saved.append((device_id, handle))
        self._handles[device_id] = handle

    async def load(self, device_id):
        return self._handles.get(device_id)

    def add_usage(self, device_id, household_id, seconds):
        seconds = max(0.0, float(seconds or 0.0))
        if device_id:
            self._device_usage_sec[device_id] += seconds
        if household_id:
            self._household_usage_sec[household_id] += seconds

    async def add_usage_async(self, device_id, household_id, seconds):
        self.add_usage(device_id, household_id, seconds)

    def device_usage_sec(self, device_id):
        return self._device_usage_sec.get(device_id, 0.0)

    async def device_usage_sec_async(self, device_id):
        return self.device_usage_sec(device_id)

    def household_usage_sec(self, household_id):
        return self._household_usage_sec.get(household_id, 0.0)

    async def household_usage_sec_async(self, household_id):
        return self.household_usage_sec(household_id)

    def record_reconnect(self, device_id, now=None):
        if not device_id:
            return
        self._reconnects[device_id].append(float(time.monotonic() if now is None else now))

    async def record_reconnect_async(self, device_id, now=None):
        self.record_reconnect(device_id, now=now)

    def reconnect_count(self, device_id, *, window_sec, now=None):
        if not device_id:
            return 0
        now = float(time.monotonic() if now is None else now)
        cutoff = now - max(0.0, float(window_sec or 0.0))
        reconnects = self._reconnects[device_id]
        while reconnects and reconnects[0] < cutoff:
            reconnects.popleft()
        return len(reconnects)

    async def reconnect_count_async(self, device_id, *, window_sec, now=None):
        return self.reconnect_count(device_id, window_sec=window_sec, now=now)


def _positive_int_or_default(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def _non_negative_int_or_default(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed >= 0 else int(default)


def _positive_float_or_none(value):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float_or_default(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed > 0 else float(default)


class RedisLiveStateStore:
    """Redis-backed MP-11 live state contract for resumption + budget state."""

    def __init__(
        self,
        redis,
        *,
        namespace="prod",
        day_key=None,
        resumption_ttl_sec=86400,
        budget_ttl_sec=172800,
        reconnect_ttl_sec=300,
    ):
        self.redis = redis
        self.namespace = str(namespace or "prod")
        self.day_key = day_key
        self.resumption_ttl_sec = _positive_int_or_default(resumption_ttl_sec, 86400)
        self.budget_ttl_sec = _positive_int_or_default(budget_ttl_sec, 172800)
        self.reconnect_ttl_sec = _positive_int_or_default(reconnect_ttl_sec, 300)

    async def save(self, device_id, handle):
        if not device_id or not handle:
            return
        await self.redis.set(
            self._resumption_key(device_id),
            str(handle),
            ex=self.resumption_ttl_sec,
        )

    async def load(self, device_id):
        if not device_id:
            return None
        value = await self.redis.get(self._resumption_key(device_id))
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def add_usage_async(self, device_id, household_id, seconds):
        seconds = max(0.0, float(seconds or 0.0))
        if device_id:
            key = self._budget_key("device", device_id)
            await self.redis.incrbyfloat(key, seconds)
            await self.redis.expire(key, self.budget_ttl_sec)
        if household_id:
            key = self._budget_key("household", household_id)
            await self.redis.incrbyfloat(key, seconds)
            await self.redis.expire(key, self.budget_ttl_sec)

    async def device_usage_sec_async(self, device_id):
        return await self._usage_sec("device", device_id)

    async def household_usage_sec_async(self, household_id):
        return await self._usage_sec("household", household_id)

    async def record_reconnect_async(self, device_id, now=None):
        if not device_id:
            return
        now = float(time.monotonic() if now is None else now)
        key = self._reconnect_key(device_id)
        await self.redis.zadd(key, {f"{now:.6f}:{uuid4().hex}": now})
        await self.redis.expire(key, self.reconnect_ttl_sec)

    async def reconnect_count_async(self, device_id, *, window_sec, now=None):
        if not device_id:
            return 0
        now = float(time.monotonic() if now is None else now)
        cutoff = now - max(0.0, float(window_sec or 0.0))
        key = self._reconnect_key(device_id)
        await self.redis.zremrangebyscore(key, "-inf", cutoff)
        return int(await self.redis.zcard(key))

    async def close(self):
        close = getattr(self.redis, "aclose", None) or getattr(self.redis, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _usage_sec(self, scope, identifier):
        if not identifier:
            return 0.0
        value = await self.redis.get(self._budget_key(scope, identifier))
        if value is None:
            return 0.0
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return float(value)

    def _resumption_key(self, device_id):
        return f"tbot:live:{self.namespace}:session:{device_id}:resumption"

    def _budget_key(self, scope, identifier):
        return f"tbot:live:{self.namespace}:budget:{scope}:{self._day_key()}:{identifier}"

    def _reconnect_key(self, device_id):
        return f"tbot:live:{self.namespace}:reconnect:{device_id}"

    def _day_key(self):
        return self.day_key or datetime.now(timezone.utc).strftime("%Y-%m-%d")

def create_live_state_store(config):
    cfg = config.get("live_state", {}) if isinstance(config, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    redis_url = cfg.get("redis_url") or os.environ.get("TBOT_LIVE_REDIS_URL") or os.environ.get("REDIS_URL")
    if not redis_url:
        return InMemoryLiveAdmissionStore()
    try:
        from redis.asyncio import Redis
    except ImportError:
        return InMemoryLiveAdmissionStore()

    redis = Redis.from_url(redis_url, decode_responses=True)
    return RedisLiveStateStore(
        redis,
        namespace=cfg.get("namespace", os.environ.get("TBOT_LIVE_REDIS_NAMESPACE", "prod")),
        resumption_ttl_sec=cfg.get("resumption_ttl_sec", 86400),
        budget_ttl_sec=cfg.get("budget_ttl_sec", 172800),
        reconnect_ttl_sec=cfg.get("reconnect_ttl_sec", 300),
    )


class LiveAdmissionGate:
    def __init__(
        self,
        store=None,
        *,
        daily_device_minutes=None,
        daily_household_minutes=None,
        reconnect_window_sec=60,
        reconnect_limit=5,
    ):
        self.store = store or InMemoryLiveAdmissionStore()
        self.daily_device_minutes = _positive_float_or_none(daily_device_minutes)
        self.daily_household_minutes = _positive_float_or_none(daily_household_minutes)
        self.reconnect_window_sec = _positive_float_or_default(reconnect_window_sec, 60)
        self.reconnect_limit = _non_negative_int_or_default(reconnect_limit, 5)

    @classmethod
    def from_config(cls, config, store=None):
        cfg = config.get("live_admission", {}) if isinstance(config, dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        if not cfg and isinstance(config, dict):
            server_cfg = config.get("server", {})
            if isinstance(server_cfg, dict):
                cfg = server_cfg.get("audio_admission", {}) or {}
            if not isinstance(cfg, dict):
                cfg = {}
        device_minutes = cfg.get("daily_device_minutes")
        if device_minutes is None:
            device_minutes = cfg.get("daily_live_minutes")
        return cls(
            store,
            daily_device_minutes=device_minutes,
            daily_household_minutes=cfg.get("daily_household_minutes"),
            reconnect_window_sec=cfg.get("reconnect_window_sec", 60),
            reconnect_limit=cfg.get("reconnect_limit", 5),
        )

    def admit(self, device_id, household_id=None, *, now=None):
        if self._over_reconnect_limit(device_id, now=now):
            return LiveAdmissionResult(
                AdmissionDecision.FRIENDLY_BREAK,
                AdmissionReason.RECONNECT_STORM,
            )
        if self._over_device_budget(device_id):
            return LiveAdmissionResult(
                AdmissionDecision.DEGRADE_TTS_ONLY,
                AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            )
        if self._over_household_budget(household_id):
            return LiveAdmissionResult(
                AdmissionDecision.DEGRADE_TTS_ONLY,
                AdmissionReason.HOUSEHOLD_DAILY_BUDGET_EXHAUSTED,
            )
        return LiveAdmissionResult(AdmissionDecision.ALLOW_LIVE)

    async def admit_async(self, device_id, household_id=None, *, now=None):
        if await self._over_reconnect_limit_async(device_id, now=now):
            return LiveAdmissionResult(
                AdmissionDecision.FRIENDLY_BREAK,
                AdmissionReason.RECONNECT_STORM,
            )
        if await self._over_device_budget_async(device_id):
            return LiveAdmissionResult(
                AdmissionDecision.DEGRADE_TTS_ONLY,
                AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            )
        if await self._over_household_budget_async(household_id):
            return LiveAdmissionResult(
                AdmissionDecision.DEGRADE_TTS_ONLY,
                AdmissionReason.HOUSEHOLD_DAILY_BUDGET_EXHAUSTED,
            )
        return LiveAdmissionResult(AdmissionDecision.ALLOW_LIVE)

    def record_live_usage(self, device_id, household_id, seconds):
        self.store.add_usage(device_id, household_id, seconds)

    async def record_live_usage_async(self, device_id, household_id, seconds):
        add_usage = getattr(self.store, "add_usage_async", None)
        if callable(add_usage):
            await add_usage(device_id, household_id, seconds)
            return
        self.record_live_usage(device_id, household_id, seconds)

    def record_reconnect(self, device_id, *, now=None):
        self.store.record_reconnect(device_id, now=now)

    async def record_reconnect_async(self, device_id, *, now=None):
        record_reconnect = getattr(self.store, "record_reconnect_async", None)
        if callable(record_reconnect):
            await record_reconnect(device_id, now=now)
            return
        self.record_reconnect(device_id, now=now)

    def _over_device_budget(self, device_id):
        if not self.daily_device_minutes or not device_id:
            return False
        return self.store.device_usage_sec(device_id) >= float(self.daily_device_minutes) * 60.0

    async def _over_device_budget_async(self, device_id):
        if not self.daily_device_minutes or not device_id:
            return False
        usage = await _call_store(self.store, "device_usage_sec_async", "device_usage_sec", device_id)
        return usage >= float(self.daily_device_minutes) * 60.0

    def _over_household_budget(self, household_id):
        if not self.daily_household_minutes or not household_id:
            return False
        return self.store.household_usage_sec(household_id) >= float(self.daily_household_minutes) * 60.0

    async def _over_household_budget_async(self, household_id):
        if not self.daily_household_minutes or not household_id:
            return False
        usage = await _call_store(self.store, "household_usage_sec_async", "household_usage_sec", household_id)
        return usage >= float(self.daily_household_minutes) * 60.0

    def _over_reconnect_limit(self, device_id, *, now=None):
        if not self.reconnect_limit or not device_id:
            return False
        return self.store.reconnect_count(
            device_id,
            window_sec=self.reconnect_window_sec,
            now=now,
        ) >= int(self.reconnect_limit)

    async def _over_reconnect_limit_async(self, device_id, *, now=None):
        if not self.reconnect_limit or not device_id:
            return False
        count = await _call_store(
            self.store,
            "reconnect_count_async",
            "reconnect_count",
            device_id,
            window_sec=self.reconnect_window_sec,
            now=now,
        )
        return count >= int(self.reconnect_limit)

async def _call_store(store, async_name, sync_name, *args, **kwargs):
    method = getattr(store, async_name, None)
    if callable(method):
        return await method(*args, **kwargs)
    method = getattr(store, sync_name)
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
