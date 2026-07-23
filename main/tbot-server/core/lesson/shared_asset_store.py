"""Content-addressed SD storage and crash-safe lesson pack commits."""

from __future__ import annotations

import hashlib
import errno
import fcntl
import json
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Set
from urllib.parse import quote


FailureHook = Callable[[str, Path], None]


class SharedAssetStore:
    """Store verified bytes once and expose versioned lesson pack entries.

    ``root`` is the ``tbot`` directory (normally ``/sdcard/tbot``). Pack entries
    are hard links, so legacy lesson-pack paths remain valid without duplicating
    the underlying asset bytes.
    """

    _active_parts: Set[str] = set()
    _active_parts_lock = threading.RLock()

    def __init__(
        self,
        root: Any,
        *,
        pack_root: Any = None,
        failure_hook: Optional[FailureHook] = None,
        cleanup_on_init: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.shared_root = self.root / "shared-assets" / "sha256"
        self.pack_root = Path(pack_root).resolve() if pack_root else self.root / "lesson-assets"
        self._failure_hook = failure_hook
        # A process restart must never inherit an interrupted write as valid state.
        if cleanup_on_init:
            self.cleanup_parts()
        self._recover_pack_backups()

    def asset_path(self, digest: str) -> Path:
        digest = self._validate_digest(digest)
        return self.shared_root / digest[:2] / digest

    def put_bytes(self, content: bytes, digest: str) -> Path:
        with self._gc_lock(exclusive=False):
            return self._put_bytes_locked(content, digest)

    def _put_bytes_locked(self, content: bytes, digest: str) -> Path:
        digest = self._validate_digest(digest)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("asset checksum mismatch")
        target = self.asset_path(digest)
        if self._attest_unlocked(digest):
            return target
        if target.exists():
            target.unlink()
        self._atomic_write(target, content)
        if not self._attest_unlocked(digest):
            self._safe_unlink(target)
            raise ValueError("asset checksum mismatch after commit")
        return target

    def put_file(self, source: Any, digest: str) -> Path:
        with self._gc_lock(exclusive=False):
            return self._put_file_locked(source, digest)

    def _put_file_locked(self, source: Any, digest: str) -> Path:
        source_path = Path(source)
        digest = self._validate_digest(digest)
        if self._hash_file(source_path) != digest:
            raise ValueError("asset checksum mismatch")
        target = self.asset_path(digest)
        if self._attest_unlocked(digest):
            return target
        if target.exists():
            target.unlink()
        self._atomic_copy(target, source_path)
        if not self._attest_unlocked(digest):
            self._safe_unlink(target)
            raise ValueError("asset checksum mismatch after commit")
        return target

    def attest(self, digest: str) -> bool:
        with self._gc_lock(exclusive=False):
            return self._attest_unlocked(digest)

    def _attest_unlocked(self, digest: str) -> bool:
        try:
            path = self.asset_path(digest)
            return path.is_file() and self._hash_file(path) == digest
        except (OSError, ValueError):
            return False

    def commit_pack(
        self,
        cache_key: str,
        assets: Mapping[str, str],
        *,
        manifest: Optional[Mapping[str, Any]] = None,
    ) -> Path:
        with self._gc_lock(exclusive=False):
            return self._commit_pack_locked(cache_key, assets, manifest=manifest)

    def _commit_pack_locked(
        self,
        cache_key: str,
        assets: Mapping[str, str],
        *,
        manifest: Optional[Mapping[str, Any]] = None,
    ) -> Path:
        pack_dir = self._pack_dir(cache_key)
        with self._pack_lock(cache_key, exclusive=True):
            self._recover_pack_unlocked(cache_key)
            staging = pack_dir.with_name(".{}.staging".format(pack_dir.name))
            backup = pack_dir.with_name(".{}.backup".format(pack_dir.name))
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=False)
            try:
                normalized: Dict[str, str] = {}
                for key, digest in assets.items():
                    digest = self._validate_digest(digest)
                    if not self._attest_unlocked(digest):
                        raise ValueError("cannot commit pack with unattested asset")
                    name = self._pack_asset_name(key)
                    self._atomic_link(staging / name, self.asset_path(digest))
                    normalized[str(key)] = digest

                manifest = self._pack_manifest(cache_key, normalized, manifest)
                manifest_bytes = json.dumps(
                    manifest, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                self._atomic_write(staging / "pack.json", manifest_bytes)
                ready = {
                    "packSha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "assetCount": len(normalized),
                }
                self._atomic_write(
                    staging / "READY",
                    json.dumps(ready, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                )
                if pack_dir.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                    os.replace(str(pack_dir), str(backup))
                    self._fsync_dir(pack_dir.parent)
                    self._notify("after_backup", pack_dir)
                try:
                    os.replace(str(staging), str(pack_dir))
                    self._fsync_dir(pack_dir.parent)
                except Exception:
                    if backup.exists() and not pack_dir.exists():
                        os.replace(str(backup), str(pack_dir))
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return pack_dir

    def delete_pack(
        self,
        cache_key: str,
        *,
        sweep: bool = False,
        protected_cache_keys: Set[str] = frozenset(),
    ) -> list[str]:
        """Delete one pack under cross-process GC + per-pack exclusion."""
        with self._gc_lock(exclusive=True):
            with self._pack_lock(cache_key, exclusive=True):
                pack_dir = self._pack_dir(cache_key)
                if not pack_dir.exists():
                    return []
                tombstone = pack_dir.with_name(
                    ".{}.{}.gc".format(pack_dir.name, uuid.uuid4().hex)
                )
                os.replace(str(pack_dir), str(tombstone))
                self._fsync_dir(pack_dir.parent)
                shutil.rmtree(tombstone)
            if sweep:
                return self._sweep_unreferenced_cas_unlocked(set(protected_cache_keys))
        return []

    def sweep_unreferenced_cas(self, protected_cache_keys: Set[str] = frozenset()) -> list[str]:
        with self._gc_lock(exclusive=True):
            return self._sweep_unreferenced_cas_unlocked(set(protected_cache_keys))

    def _sweep_unreferenced_cas_unlocked(self, protected: Set[str]) -> list[str]:
        reachable = set()
        if self.pack_root.exists():
            for manifest_path in self.pack_root.rglob("pack.json"):
                pack = manifest_path.parent
                if any(part.startswith(".") for part in pack.relative_to(self.pack_root).parts):
                    continue
                cache_key = pack.relative_to(self.pack_root).as_posix()
                if cache_key not in protected and not self._is_pack_dir_ready(pack, cache_key):
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    reachable.update(self._manifest_digests(manifest))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
        deleted = []
        if not self.shared_root.exists():
            return deleted
        for path in self.shared_root.rglob("*"):
            if not path.is_file() or path.name in reachable:
                continue
            try:
                if path.stat().st_nlink > 1:
                    continue
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
        return sorted(deleted)

    def materialize_pack_asset(self, cache_key: str, key: str, digest: str) -> Path:
        """Expose partial first-generation assets without mutating a valid pack."""
        with self._gc_lock(exclusive=False):
            return self._materialize_pack_asset_locked(cache_key, key, digest)

    def put_file_and_materialize(
        self, source: Any, digest: str, cache_key: str, key: str
    ) -> Path:
        """Hold the GC lease continuously from CAS write through pack hardlink."""
        with self._gc_lock(exclusive=False):
            self._put_file_locked(source, digest)
            return self._materialize_pack_asset_locked(cache_key, key, digest)

    def put_files_and_commit_pack(
        self,
        cache_key: str,
        assets: Mapping[str, tuple[Any, str]],
        *,
        manifest: Optional[Mapping[str, Any]] = None,
    ) -> Path:
        """Atomically lease all CAS inputs until the READY pack is committed."""
        with self._gc_lock(exclusive=False):
            digests: Dict[str, str] = {}
            for key, (source, digest) in assets.items():
                self._put_file_locked(source, digest)
                digests[key] = digest
            return self._commit_pack_locked(cache_key, digests, manifest=manifest)

    def _materialize_pack_asset_locked(self, cache_key: str, key: str, digest: str) -> Path:
        digest = self._validate_digest(digest)
        if not self._attest_unlocked(digest):
            raise ValueError("cannot materialize unattested asset")
        target = self._pack_dir(cache_key) / self._pack_asset_name(key)
        with self._pack_lock(cache_key, exclusive=True):
            self._recover_pack_unlocked(cache_key)
            if not self._is_pack_ready_unlocked(cache_key):
                self._atomic_link(target, self.asset_path(digest))
        return target

    def is_pack_ready(self, cache_key: str) -> bool:
        with self._gc_lock(exclusive=False):
            return self._is_pack_ready_locked(cache_key)

    def _is_pack_ready_locked(self, cache_key: str) -> bool:
        with self._pack_lock(cache_key, exclusive=False):
            if self._is_pack_ready_unlocked(cache_key):
                return True
        with self._pack_lock(cache_key, exclusive=True):
            self._recover_pack_unlocked(cache_key)
            return self._is_pack_ready_unlocked(cache_key)

    def _is_pack_ready_unlocked(self, cache_key: str) -> bool:
        return self._is_pack_dir_ready(self._pack_dir(cache_key), cache_key)

    def _is_pack_dir_ready(self, pack_dir: Path, cache_key: str) -> bool:
        try:
            manifest_bytes = (pack_dir / "pack.json").read_bytes()
            ready = json.loads((pack_dir / "READY").read_text(encoding="utf-8"))
            if ready.get("packSha256") != hashlib.sha256(manifest_bytes).hexdigest():
                return False
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if manifest.get("cacheKey") != cache_key:
                return False
            assets = self._manifest_asset_records(manifest)
            if assets is None or ready.get("assetCount") != len(assets):
                return False
            for key, record in assets.items():
                digest = record["sha256"]
                if not self._attest_unlocked(digest):
                    return False
                pack_asset = pack_dir / self._pack_asset_name(key)
                if not pack_asset.is_file() or self._hash_file(pack_asset) != digest:
                    return False
                size = record.get("size")
                if size is not None and pack_asset.stat().st_size != size:
                    return False
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def cleanup_parts(self) -> int:
        removed = 0
        with self._parts_lock(exclusive=True, blocking=False) as acquired:
            if not acquired:
                return removed
            parts = (
                Path(dirpath) / name
                for dirpath, _dirnames, filenames in os.walk(self.root)
                for name in filenames
                if name.endswith(".part")
            )
            for path in parts:
                resolved = str(path.resolve())
                with self._active_parts_lock:
                    if resolved in self._active_parts:
                        continue
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        continue
        return removed

    def _atomic_write(self, target: Path, content: bytes) -> None:
        with self._parts_lock(exclusive=False, blocking=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = self._temp_path(target)
            self._claim_part(temp)
            try:
                with temp.open("xb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                    self._notify("after_temp_create", target)
                    self._notify("before_replace", target)
                    os.replace(str(temp), str(target))
                    self._fsync_dir(target.parent)
                    self._notify("after_replace", target)
            finally:
                self._release_part(temp)

    def _atomic_copy(self, target: Path, source: Path) -> None:
        with self._parts_lock(exclusive=False, blocking=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = self._temp_path(target)
            self._claim_part(temp)
            try:
                with source.open("rb") as src, temp.open("xb") as dst:
                    for block in iter(lambda: src.read(64 * 1024), b""):
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                    self._notify("after_temp_create", target)
                    self._notify("before_replace", target)
                    os.replace(str(temp), str(target))
                    self._fsync_dir(target.parent)
                    self._notify("after_replace", target)
            finally:
                self._release_part(temp)

    def _atomic_link(self, target: Path, source: Path) -> None:
        with self._parts_lock(exclusive=False, blocking=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = self._temp_path(target)
            self._claim_part(temp)
            try:
                try:
                    os.link(str(source), str(temp))
                except OSError as exc:
                    if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP):
                        raise
                    self._release_part(temp)
                    self._atomic_copy(target, source)
                    return
                self._notify("after_temp_create", target)
                self._notify("before_replace", target)
                os.replace(str(temp), str(target))
                self._fsync_dir(target.parent)
                self._notify("after_replace", target)
            finally:
                self._release_part(temp)

    def _pack_dir(self, cache_key: str) -> Path:
        candidate = (self.pack_root / cache_key).resolve()
        root = self.pack_root.resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("asset pack path escapes pack root")
        return candidate

    def _recover_pack_backups(self) -> None:
        if not self.pack_root.exists():
            return
        for backup in self.pack_root.rglob(".*.backup"):
            if not backup.is_dir():
                continue
            pack_name = backup.name[1:-len(".backup")]
            pack_dir = backup.with_name(pack_name)
            try:
                cache_key = pack_dir.relative_to(self.pack_root).as_posix()
                with self._pack_lock(cache_key, exclusive=True):
                    self._recover_pack_unlocked(cache_key)
            except (OSError, ValueError):
                continue

    def _recover_pack_unlocked(self, cache_key: str) -> None:
        pack_dir = self._pack_dir(cache_key)
        backup = pack_dir.with_name(".{}.backup".format(pack_dir.name))
        staging = pack_dir.with_name(".{}.staging".format(pack_dir.name))
        pack_valid = self._is_pack_dir_ready(pack_dir, cache_key)
        backup_valid = self._is_pack_dir_ready(backup, cache_key)
        if pack_valid:
            shutil.rmtree(backup, ignore_errors=True)
        elif backup_valid:
            shutil.rmtree(pack_dir, ignore_errors=True)
            os.replace(str(backup), str(pack_dir))
            self._fsync_dir(pack_dir.parent)
        shutil.rmtree(staging, ignore_errors=True)

    @contextmanager
    def _pack_lock(self, cache_key: str, *, exclusive: bool):
        lock_root = self.pack_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + ".lock"
        with (lock_root / lock_name).open("a+b") as handle:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _parts_lock(self, *, exclusive: bool, blocking: bool):
        self.root.mkdir(parents=True, exist_ok=True)
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            mode |= fcntl.LOCK_NB
        with (self.root / ".parts.lock").open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), mode)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _gc_lock(self, *, exclusive: bool):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".gc.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _pack_asset_name(key: str) -> str:
        if not key:
            raise ValueError("asset key must not be empty")
        return quote(str(key), safe="")

    def _pack_manifest(
        self,
        cache_key: str,
        assets: Mapping[str, str],
        manifest: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if manifest is None:
            return {"cacheKey": cache_key, "assets": dict(assets)}
        if not isinstance(manifest, Mapping):
            raise ValueError("invalid pack manifest")
        if manifest.get("cacheKey") != cache_key:
            raise ValueError("manifest cacheKey mismatch")
        raw_assets = manifest.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("manifest assets must be a list")
        seen: Set[str] = set()
        for item in raw_assets:
            if not isinstance(item, Mapping):
                raise ValueError("invalid manifest asset")
            key = item.get("key")
            digest = item.get("sha256")
            size = item.get("size")
            if not isinstance(key, str) or key not in assets:
                raise ValueError("manifest asset key mismatch")
            if key in seen:
                raise ValueError("duplicate manifest asset key")
            seen.add(key)
            if self._validate_digest(str(digest or "")) != assets[key]:
                raise ValueError("manifest asset digest mismatch")
            path = self.asset_path(assets[key])
            try:
                actual_size = path.stat().st_size
            except OSError as exc:
                raise ValueError("manifest asset missing from CAS") from exc
            if type(size) is not int or size != actual_size:
                raise ValueError("manifest asset size mismatch")
        if seen != set(assets.keys()):
            raise ValueError("manifest does not cover staged assets")
        return dict(manifest)

    @staticmethod
    def _manifest_assets(manifest: Mapping[str, Any]) -> Optional[Dict[str, str]]:
        records = SharedAssetStore._manifest_asset_records(manifest)
        if records is None:
            return None
        return {key: record["sha256"] for key, record in records.items()}

    @staticmethod
    def _manifest_asset_records(
        manifest: Mapping[str, Any],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        assets = manifest.get("assets")
        if isinstance(assets, dict):
            normalized = {}
            for key, digest in assets.items():
                if not isinstance(key, str) or not isinstance(digest, str):
                    return None
                normalized[key] = {"sha256": digest}
            return normalized
        if isinstance(assets, list):
            normalized = {}
            for item in assets:
                if not isinstance(item, dict):
                    return None
                key = item.get("key")
                digest = item.get("sha256")
                size = item.get("size")
                if (
                    not isinstance(key, str)
                    or not isinstance(digest, str)
                    or type(size) is not int
                    or size < 0
                    or key in normalized
                ):
                    return None
                normalized[key] = {"sha256": digest, "size": size}
            return normalized
        return None

    @classmethod
    def _manifest_digests(cls, manifest: Mapping[str, Any]) -> Set[str]:
        assets = cls._manifest_assets(manifest)
        if not assets:
            return set()
        return {digest for digest in assets.values() if isinstance(digest, str)}

    @staticmethod
    def _validate_digest(digest: str) -> str:
        value = str(digest or "").lower()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("invalid sha256 digest")
        return value

    @staticmethod
    def _hash_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(64 * 1024), b""):
                hasher.update(block)
        return hasher.hexdigest()

    @staticmethod
    def _temp_path(target: Path) -> Path:
        return target.with_name(
            "{}.{}.{}.part".format(target.name, os.getpid(), uuid.uuid4().hex)
        )

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(str(path), flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _notify(self, stage: str, path: Path) -> None:
        if self._failure_hook is not None:
            self._failure_hook(stage, path)

    @classmethod
    def _claim_part(cls, path: Path) -> None:
        with cls._active_parts_lock:
            cls._active_parts.add(str(path.resolve()))

    @classmethod
    def _release_part(cls, path: Path) -> None:
        with cls._active_parts_lock:
            cls._active_parts.discard(str(path.resolve()))
