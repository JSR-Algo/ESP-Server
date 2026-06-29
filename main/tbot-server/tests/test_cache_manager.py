import importlib
import sys

from core.utils.cache.config import CacheConfig, CacheType
from core.utils.cache.manager import GlobalCacheManager
from core.utils.cache.strategies import CacheStrategy


class _Logger:
    def __init__(self):
        self.debugs = []

    def debug(self, message):
        self.debugs.append(message)


def _patch_setup_logging(monkeypatch, setup_logging):
    logger_module = sys.modules.get("config.logger")
    if logger_module is None:
        logger_module = importlib.import_module("config.logger")
    monkeypatch.setattr(logger_module, "setup_logging", setup_logging)


def test_cache_manager_lazy_logger_initializes_once(monkeypatch):
    logger = _Logger()
    calls = []

    def setup_logging():
        calls.append(True)
        return logger

    _patch_setup_logging(monkeypatch, setup_logging)
    manager = GlobalCacheManager()

    assert manager.logger is logger
    assert manager.logger is logger
    assert calls == [True]


def test_cache_manager_lru_set_get_miss_expire_and_evict(monkeypatch):
    manager = GlobalCacheManager()
    logger = _Logger()
    manager._logger = logger
    _patch_setup_logging(monkeypatch, lambda: logger)
    now = {"value": 100.0}
    monkeypatch.setattr("core.utils.cache.manager.time.time", lambda: now["value"])
    monkeypatch.setattr("core.utils.cache.strategies.time.time", lambda: now["value"])
    cache_name = manager._get_cache_name(CacheType.INTENT, "voice")
    manager._last_cleanup = now["value"]
    manager._configs[cache_name] = CacheConfig(
        strategy=CacheStrategy.TTL_LRU,
        ttl=1,
        max_size=2,
        cleanup_interval=1,
    )

    assert manager._get_cache_name(CacheType.INTENT) == "intent"
    assert manager._get_cache_name(CacheType.INTENT, "voice") == "intent:voice"
    assert manager.get(CacheType.INTENT, "missing", namespace="voice") is None

    manager.set(CacheType.INTENT, "a", 1, namespace="voice")
    manager.set(CacheType.INTENT, "a", 10, namespace="voice")
    manager.set(CacheType.INTENT, "b", 2, namespace="voice")
    assert manager.get(CacheType.INTENT, "a", namespace="voice") == 10
    manager.set(CacheType.INTENT, "c", 3, namespace="voice")
    assert manager.get(CacheType.INTENT, "b", namespace="voice") is None
    assert manager._stats["evictions"] == 1

    now["value"] = 103.0
    assert manager.get(CacheType.INTENT, "a", namespace="voice") is None
    manager.set(CacheType.INTENT, "expired", 4, ttl=0.1, namespace="voice")
    now["value"] = 105.0
    manager.set(CacheType.INTENT, "fresh", 5, namespace="voice")
    assert manager._stats["cleanups"] >= 1
    assert logger.debugs


def test_cache_manager_fixed_size_delete_clear_invalidate_and_cleanup_edges():
    manager = GlobalCacheManager()
    cache_name = manager._get_cache_name(CacheType.CONFIG)
    manager._configs[cache_name] = CacheConfig(
        strategy=CacheStrategy.FIXED_SIZE,
        ttl=None,
        max_size=1,
    )

    manager.set(CacheType.CONFIG, "one", 1)
    manager.set(CacheType.CONFIG, "two", 2)
    assert manager.get(CacheType.CONFIG, "one") is None
    assert manager.get(CacheType.CONFIG, "two") == 2
    assert manager.delete(CacheType.WEATHER, "missing") is False
    assert manager.delete(CacheType.CONFIG, "missing") is False
    assert manager.delete(CacheType.CONFIG, "two") is True
    assert manager.get(CacheType.CONFIG, "two") is None

    manager.set(CacheType.CONFIG, "prefix:a", 1)
    manager.set(CacheType.CONFIG, "prefix:b", 2)
    assert manager.invalidate_pattern(CacheType.WEATHER, "prefix") == 0
    assert manager.invalidate_pattern(CacheType.CONFIG, "prefix") == 1
    manager.clear(CacheType.WEATHER)
    manager.clear(CacheType.CONFIG)
    assert manager.get(CacheType.CONFIG, "prefix:b") is None
    assert manager._cleanup_expired("missing") == 0
    manager._maybe_cleanup("missing")
