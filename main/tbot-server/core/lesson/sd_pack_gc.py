"""Quota-aware garbage collection and two-phase SD pack activation."""

from __future__ import annotations

import json
import os
import shutil
import fcntl
import uuid
from contextlib import contextmanager
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
        self.gc_free_percent = float(gc_free_percent)
        self.preload_min_free_percent = float(preload_min_free_percent)
        if not 0 <= self.gc_free_percent <= 100:
            raise ValueError("sd_gc_free_percent must be between 0 and 100")
        if not 0 <= self.preload_min_free_percent <= 100:
            raise ValueError("sd_preload_min_free_percent must be between 0 and 100")
        if self.preload_min_free_percent > self.gc_free_percent:
            raise ValueError("sd_preload_min_free_percent must not exceed sd_gc_free_percent")
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
    ) -> Dict[str, Any]:
        if self._voice_busy():
            return {"skipped": "voice_busy"}
        if self._render_busy():
            return {"skipped": "lesson_render_busy"}

        usage = self.physical_usage_bytes()
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
        try:
            if self.shared_store is not None:
                deleted_cas = self.shared_store.delete_pack(
                    cache_key,
                    sweep=True,
                    protected_cache_keys=protected,
                )
            else:
                tombstone = path.with_name(".{}.gc".format(path.name))
                os.replace(str(path), str(tombstone))
                shutil.rmtree(tombstone)
                deleted_cas = []
        except OSError:
            return {"skipped": "delete_failed"}
        return {
            "deleted": cache_key,
            "deletedCas": deleted_cas,
            "reason": "low_free_space" if low_free else "quota",
        }

    def _free_percent(self) -> float:
        usage = self._disk_usage(self.pack_root)
        total = int(getattr(usage, "total", 0) or 0)
        free = int(getattr(usage, "free", 0) or 0)
        return 100.0 if total <= 0 else (free * 100.0 / total)

    def physical_usage_bytes(self) -> int:
        """Count physical inodes once so lesson-pack hardlinks do not inflate quota."""
        total = 0
        seen = set()
        roots = [self.pack_root]
        if self.shared_store is not None:
            roots.append(self.shared_store.shared_root)
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                try:
                    if path.is_file():
                        stat = path.stat()
                        inode = (stat.st_dev, stat.st_ino)
                        if inode not in seen:
                            seen.add(inode)
                            total += stat.st_size
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
    """Atomically persist exact current/candidate/previous pack identities."""

    def __init__(
        self,
        store: SharedAssetStore,
        *,
        state_path: Any = None,
        current: Any = None,
        current_cache_key: Optional[str] = None,
    ) -> None:
        self.store = store
        self.state_path = Path(state_path or (store.root / "lesson-pack-activation.json"))
        with self._state_lock():
            loaded = self._load()
            self.current = self._identity(current) or self._identity(loaded.get("current"))
            if self.current is None and current_cache_key:
                self.current = self._identity(current_cache_key)
            self.previous_known_good = self._identity(loaded.get("previousKnownGood"))
            self.candidate = self._identity(loaded.get("candidate"))
            if current is not None or current_cache_key is not None:
                self._persist_unlocked()

    @property
    def current_cache_key(self) -> Optional[str]:
        return self.current.get("cacheKey") if self.current else None

    @property
    def previous_known_good_cache_key(self) -> Optional[str]:
        return self.previous_known_good.get("cacheKey") if self.previous_known_good else None

    @property
    def candidate_cache_key(self) -> Optional[str]:
        return self.candidate.get("cacheKey") if self.candidate else None

    def begin_candidate(self, identity: Any) -> None:
        with self._state_lock():
            self._reload_unlocked()
            self.candidate = self._identity(identity)
            self._persist_unlocked()

    def set_current_if_empty(self, identity: Any) -> None:
        with self._state_lock():
            self._reload_unlocked()
            if self.current is None:
                self.current = self._identity(identity)
                self._persist_unlocked()

    def verify_for_activation(self, identity: Any = None) -> bool:
        with self._state_lock():
            self._reload_unlocked()
            expected = self._identity(identity) if identity is not None else self.candidate
            return bool(
                expected
                and expected == self.candidate
                and self.store.is_pack_ready(expected["cacheKey"])
            )

    def activate_candidate(self, identity: Any = None) -> bool:
        with self._state_lock():
            self._reload_unlocked()
            expected = self._identity(identity) if identity is not None else self.candidate
            if not expected or expected != self.candidate or not self.store.is_pack_ready(expected["cacheKey"]):
                return False
            old = self.current
            self.current = expected
            self.candidate = None
            if old and old != expected:
                self.previous_known_good = old
            self._persist_unlocked()
            return True

    def abort_candidate(self) -> None:
        with self._state_lock():
            self._reload_unlocked()
            self.candidate = None
            self._persist_unlocked()

    def rollback(self, exact_identity: Any) -> bool:
        with self._state_lock():
            self._reload_unlocked()
            expected = self._identity(exact_identity)
            if not expected or expected != self.previous_known_good:
                return False
            if not self.store.is_pack_ready(expected["cacheKey"]):
                return False
            old = self.current
            self.current = expected
            self.previous_known_good = old
            self.candidate = None
            self._persist_unlocked()
            return True

    @staticmethod
    def _identity(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, str) and value:
            tail = value.rsplit("/", 1)[-1]
            version = 0
            checksum = ""
            if tail.startswith("v") and "-" in tail:
                raw_version, checksum = tail[1:].split("-", 1)
                try:
                    version = int(raw_version)
                except ValueError:
                    version = 0
            return {"cacheKey": value, "lessonVersion": version, "manifestChecksum": checksum}
        if not isinstance(value, dict):
            return None
        cache_key = value.get("cacheKey")
        checksum = value.get("manifestChecksum")
        version = value.get("lessonVersion")
        if not isinstance(cache_key, str) or not cache_key:
            return None
        if not isinstance(checksum, str) or not isinstance(version, int):
            return None
        tail = cache_key.rsplit("/", 1)[-1]
        if tail.startswith("v") and "-" in tail:
            raw_version, key_checksum = tail[1:].split("-", 1)
            try:
                if int(raw_version) != version or key_checksum != checksum:
                    return None
            except ValueError:
                return None
        return {"cacheKey": cache_key, "lessonVersion": version, "manifestChecksum": checksum}

    def _load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _reload_unlocked(self) -> None:
        loaded = self._load()
        self.current = self._identity(loaded.get("current"))
        self.previous_known_good = self._identity(loaded.get("previousKnownGood"))
        self.candidate = self._identity(loaded.get("candidate"))

    def _persist_unlocked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_name(
            "{}.{}.{}.part".format(self.state_path.name, os.getpid(), uuid.uuid4().hex)
        )
        content = json.dumps(
            {
                "current": self.current,
                "previousKnownGood": self.previous_known_good,
                "candidate": self.candidate,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with temp.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(self.state_path))
        self.store._fsync_dir(self.state_path.parent)

    @contextmanager
    def _state_lock(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
