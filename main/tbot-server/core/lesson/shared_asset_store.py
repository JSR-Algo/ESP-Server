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
    ) -> None:
        self.root = Path(root).resolve()
        self.shared_root = self.root / "shared-assets" / "sha256"
        self.pack_root = Path(pack_root).resolve() if pack_root else self.root / "lesson-assets"
        self._failure_hook = failure_hook
        # A process restart must never inherit an interrupted write as valid state.
        self.cleanup_parts()

    def asset_path(self, digest: str) -> Path:
        digest = self._validate_digest(digest)
        return self.shared_root / digest[:2] / digest

    def put_bytes(self, content: bytes, digest: str) -> Path:
        digest = self._validate_digest(digest)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("asset checksum mismatch")
        target = self.asset_path(digest)
        if self.attest(digest):
            return target
        if target.exists():
            target.unlink()
        self._atomic_write(target, content)
        if not self.attest(digest):
            self._safe_unlink(target)
            raise ValueError("asset checksum mismatch after commit")
        return target

    def put_file(self, source: Any, digest: str) -> Path:
        source_path = Path(source)
        digest = self._validate_digest(digest)
        if self._hash_file(source_path) != digest:
            raise ValueError("asset checksum mismatch")
        target = self.asset_path(digest)
        if self.attest(digest):
            return target
        if target.exists():
            target.unlink()
        self._atomic_copy(target, source_path)
        if not self.attest(digest):
            self._safe_unlink(target)
            raise ValueError("asset checksum mismatch after commit")
        return target

    def attest(self, digest: str) -> bool:
        try:
            path = self.asset_path(digest)
            return path.is_file() and self._hash_file(path) == digest
        except (OSError, ValueError):
            return False

    def commit_pack(self, cache_key: str, assets: Mapping[str, str]) -> Path:
        pack_dir = self._pack_dir(cache_key)
        with self._pack_lock(cache_key):
            staging = pack_dir.with_name(
                ".{}.{}.staging".format(pack_dir.name, uuid.uuid4().hex)
            )
            backup = pack_dir.with_name(
                ".{}.{}.backup".format(pack_dir.name, uuid.uuid4().hex)
            )
            staging.mkdir(parents=True, exist_ok=False)
            try:
                normalized: Dict[str, str] = {}
                for key, digest in assets.items():
                    digest = self._validate_digest(digest)
                    if not self.attest(digest):
                        raise ValueError("cannot commit pack with unattested asset")
                    name = self._pack_asset_name(key)
                    self._atomic_link(staging / name, self.asset_path(digest))
                    normalized[str(key)] = digest

                manifest = {"cacheKey": cache_key, "assets": normalized}
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
                    os.replace(str(pack_dir), str(backup))
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

    def materialize_pack_asset(self, cache_key: str, key: str, digest: str) -> Path:
        """Expose partial first-generation assets without mutating a valid pack."""
        digest = self._validate_digest(digest)
        if not self.attest(digest):
            raise ValueError("cannot materialize unattested asset")
        target = self._pack_dir(cache_key) / self._pack_asset_name(key)
        with self._pack_lock(cache_key):
            if not self.is_pack_ready(cache_key):
                self._atomic_link(target, self.asset_path(digest))
        return target

    def is_pack_ready(self, cache_key: str) -> bool:
        try:
            pack_dir = self._pack_dir(cache_key)
            manifest_bytes = (pack_dir / "pack.json").read_bytes()
            ready = json.loads((pack_dir / "READY").read_text(encoding="utf-8"))
            if ready.get("packSha256") != hashlib.sha256(manifest_bytes).hexdigest():
                return False
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if manifest.get("cacheKey") != cache_key:
                return False
            assets = manifest.get("assets")
            if not isinstance(assets, dict) or ready.get("assetCount") != len(assets):
                return False
            for key, digest in assets.items():
                if not self.attest(digest):
                    return False
                pack_asset = pack_dir / self._pack_asset_name(key)
                if not pack_asset.is_file() or self._hash_file(pack_asset) != digest:
                    return False
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def cleanup_parts(self) -> int:
        removed = 0
        if not self.root.exists():
            return removed
        for path in self.root.rglob("*.part"):
            resolved = str(path.resolve())
            with self._active_parts_lock:
                if resolved in self._active_parts:
                    continue
                try:
                    with path.open("rb") as handle:
                        try:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def _atomic_write(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = self._temp_path(target)
        self._claim_part(temp)
        try:
            with temp.open("xb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                self._notify("before_replace", target)
                os.replace(str(temp), str(target))
                self._fsync_dir(target.parent)
                self._notify("after_replace", target)
        except Exception:
            # Preserve interrupted temp files for deterministic boot cleanup.
            raise
        finally:
            self._release_part(temp)

    def _atomic_copy(self, target: Path, source: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = self._temp_path(target)
        self._claim_part(temp)
        try:
            with source.open("rb") as src, temp.open("xb") as dst:
                fcntl.flock(dst.fileno(), fcntl.LOCK_EX)
                for block in iter(lambda: src.read(64 * 1024), b""):
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
                self._notify("before_replace", target)
                os.replace(str(temp), str(target))
                self._fsync_dir(target.parent)
                self._notify("after_replace", target)
        except Exception:
            raise
        finally:
            self._release_part(temp)

    def _atomic_link(self, target: Path, source: Path) -> None:
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
            with temp.open("rb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._notify("before_replace", target)
                os.replace(str(temp), str(target))
                self._fsync_dir(target.parent)
                self._notify("after_replace", target)
        except Exception:
            raise
        finally:
            self._release_part(temp)

    def _pack_dir(self, cache_key: str) -> Path:
        candidate = (self.pack_root / cache_key).resolve()
        root = self.pack_root.resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("asset pack path escapes pack root")
        return candidate

    @contextmanager
    def _pack_lock(self, cache_key: str):
        lock_root = self.pack_root / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_name = hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + ".lock"
        with (lock_root / lock_name).open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _pack_asset_name(key: str) -> str:
        if not key:
            raise ValueError("asset key must not be empty")
        return quote(str(key), safe="")

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
