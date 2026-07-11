"""Quota-aware garbage collection and two-phase SD pack activation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple

from core.lesson.shared_asset_store import SharedAssetStore


DEFAULT_GC_FREE_PERCENT = 20.0
DEFAULT_PRELOAD_MIN_FREE_PERCENT = 5.0


class SdPackGarbageCollector:
    """Delete at most one unattached READY pack without touching shared CAS bytes."""

    def __init__(
        self,
        pack_root: Any,
        *,
        shared_asset_store: Optional[SharedAssetStore] = None,
        shared_store: Optional[SharedAssetStore] = None,
        quota_bytes: int = 0,
        gc_free_percent: float = DEFAULT_GC_FREE_PERCENT,
        preload_min_free_percent: float = DEFAULT_PRELOAD_MIN_FREE_PERCENT,
        disk_usage: Callable[[Any], Any] = shutil.disk_usage,
        voice_busy: Optional[Callable[[], bool]] = None,
        render_busy: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.pack_root = Path(pack_root).resolve()
        self.shared_store = shared_asset_store or shared_store
        self.quota_bytes = max(0, int(quota_bytes or 0))
        self.gc_free_percent = max(0.0, float(gc_free_percent))
        self.preload_min_free_percent = max(0.0, float(preload_min_free_percent))
        self._disk_usage = disk_usage
        self._voice_busy = voice_busy or (lambda: False)
        self._render_busy = render_busy or (lambda: False)

    def can_preload(self) -> bool:
        return self._free_percent() >= self.preload_min_free_percent

    def boot_cleanup(self) -> Dict[str, Any]:
        removed_parts = self.shared_store.cleanup_parts() if self.shared_store else 0
        ready = []
        ignored = []
        for cache_key, _path in self._pack_directories():
            if self._is_ready(cache_key):
                ready.append(cache_key)
            else:
                ignored.append(cache_key)
        return {
            "removedParts": removed_parts,
            "ready": sorted(ready),
            "ignored": sorted(ignored),
        }

    def collect_one(
        self,
        *,
        active_cache_key: Optional[str] = None,
        preloading_cache_key: Optional[str] = None,
        current_cache_key: Optional[str] = None,
        previous_known_good_cache_key: Optional[str] = None,
        protected_cache_keys: Iterable[str] = (),
    ) -> Dict[str, str]:
        if self._voice_busy():
            return {"skipped": "voice_busy"}
        if self._render_busy():
            return {"skipped": "lesson_render_busy"}

        usage = self._pack_usage_bytes()
        low_free = self._free_percent() < self.gc_free_percent
        above_quota = self.quota_bytes > 0 and usage > self.quota_bytes
        if not low_free and not above_quota:
            return {"skipped": "threshold_not_met"}

        protected: Set[str] = {
            key
            for key in (
                active_cache_key,
                preloading_cache_key,
                current_cache_key,
                previous_known_good_cache_key,
                *protected_cache_keys,
            )
            if isinstance(key, str) and key
        }
        candidates = []
        for cache_key, path in self._pack_directories():
            if cache_key in protected or not self._is_ready(cache_key):
                continue
            try:
                candidates.append((path.stat().st_mtime, cache_key, path))
            except OSError:
                continue
        if not candidates:
            return {"skipped": "no_evictable_pack"}

        _mtime, cache_key, path = min(candidates, key=lambda item: (item[0], item[1]))
        tombstone = path.with_name(".{}.gc".format(path.name))
        try:
            os.replace(str(path), str(tombstone))
            shutil.rmtree(tombstone)
        except OSError:
            return {"skipped": "delete_failed"}
        return {
            "deleted": cache_key,
            "reason": "low_free_space" if low_free else "quota",
        }

    def _free_percent(self) -> float:
        usage = self._disk_usage(self.pack_root)
        total = int(getattr(usage, "total", 0) or 0)
        free = int(getattr(usage, "free", 0) or 0)
        return 100.0 if total <= 0 else (free * 100.0 / total)

    def _pack_usage_bytes(self) -> int:
        total = 0
        for _cache_key, pack in self._pack_directories():
            for path in pack.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _pack_directories(self) -> Iterable[Tuple[str, Path]]:
        if not self.pack_root.is_dir():
            return []
        packs = []
        for ready in self.pack_root.rglob("READY"):
            pack = ready.parent
            if any(part.startswith(".") for part in pack.relative_to(self.pack_root).parts):
                continue
            packs.append((pack.relative_to(self.pack_root).as_posix(), pack))
        # Non-READY directories are included for boot reporting, but never GC'd.
        for manifest in self.pack_root.rglob("pack.json"):
            pack = manifest.parent
            item = (pack.relative_to(self.pack_root).as_posix(), pack)
            if item not in packs and not any(
                part.startswith(".") for part in pack.relative_to(self.pack_root).parts
            ):
                packs.append(item)
        return packs

    def _is_ready(self, cache_key: str) -> bool:
        if self.shared_store is not None:
            return self.shared_store.is_pack_ready(cache_key)
        return (self.pack_root / cache_key / "READY").is_file()


class SdPackActivationState:
    """Keep the old exact cache identity until a verified candidate is activated."""

    def __init__(self, store: SharedAssetStore, *, current_cache_key: Optional[str] = None) -> None:
        self.store = store
        self.current_cache_key = current_cache_key
        self.previous_known_good_cache_key: Optional[str] = None
        self.candidate_cache_key: Optional[str] = None

    def begin_candidate(self, cache_key: str) -> None:
        self.candidate_cache_key = cache_key

    def activate_candidate(self) -> bool:
        candidate = self.candidate_cache_key
        if not candidate or not self.store.is_pack_ready(candidate):
            return False
        old = self.current_cache_key
        self.current_cache_key = candidate
        self.candidate_cache_key = None
        if old and old != candidate:
            self.previous_known_good_cache_key = old
        return True

    def rollback(self, exact_cache_key: str) -> bool:
        if exact_cache_key != self.previous_known_good_cache_key:
            return False
        if not self.store.is_pack_ready(exact_cache_key):
            return False
        old = self.current_cache_key
        self.current_cache_key = exact_cache_key
        self.previous_known_good_cache_key = old
        self.candidate_cache_key = None
        return True
