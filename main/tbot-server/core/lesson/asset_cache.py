"""S7 — ESP asset preload cache (D-PRELOAD-OWNER OWNER, ADR 0013 §C).

The ESP Server is the SOLE downloader + sha256 verifier. It:

* fetches each manifest-declared asset over a DEDICATED ``httpx`` client that
  carries NO ``Authorization: Bearer`` (the manager-api secret must never leak to
  the asset origin) and is bound to one ``assetOriginBase`` (D-ASSET-HOST waiver),
* streams ``hashlib.sha256`` over the response so hashing never blocks the WS loop,
* reports READY **only** when every ``critical`` asset exists locally AND every
  sha256 passes (binary rule); a critical mismatch raises ``ASSET_CHECKSUM_MISMATCH``
  and blocks READY permanently (no auto-retry), and
* PAUSES all downloads during any realtime voice turn (the realtime path always
  wins) — resuming on the next IDLE boundary, with ``PRELOAD_TIMEOUT`` bounding
  starvation.

Firmware never downloads or checksums anything; the ESP synthesizes
``lesson_preload_status`` from this cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

try:  # httpx is a hard runtime dep (==0.28.1); guarded so unit tests can inject a fake.
    import httpx
except Exception:  # pragma: no cover - exercised only where httpx is absent
    httpx = None  # type: ignore

from core.lesson.errors import (
    AssetChecksumMismatch,
    AssetProfileUnavailable,
    PreloadTimeout,
)

TAG = "LessonAssetCache"

# Per-asset lifecycle states (wire enum: PENDING|DOWNLOADING|READY|FAILED|EVICTED).
PENDING = "PENDING"
DOWNLOADING = "DOWNLOADING"
READY = "READY"
FAILED = "FAILED"
EVICTED = "EVICTED"

_DEFAULT_CHUNK = 64 * 1024
_DEFAULT_POLL_INTERVAL = 0.2


def _sha_prefix(value: Optional[str]) -> str:
    return (value or "")[:8]


class AssetState:
    """Mutable per-asset record. ``critical`` gates READY; optional assets do not."""

    __slots__ = ("key", "path", "sha256", "critical", "layer", "role", "media_type", "url", "state", "checksum_ok", "reason")

    def __init__(self, asset: Dict[str, Any]) -> None:
        # ``key`` is projected upstream as ``a.get("id") or a.get("assetId")``
        # (runtime.py _critical_assets_payload), so a manifest asset that declares
        # neither id nor assetId arrives here with key=None. A falsy key is unusable:
        # _final_path would AttributeError on ``None.replace`` and None keys collide
        # in _by_key — both crashes. Callers must filter these out BEFORE constructing
        # an AssetState (see AssetCache.__init__); this guard is the last line of
        # defense so a slipped-through bad asset fails loudly at construction, not
        # silently mid-preload.
        key = asset.get("key")
        if not key:
            raise ValueError(
                "AssetState requires a non-empty 'key' (manifest asset is missing "
                "both 'id' and 'assetId')"
            )
        self.key: str = key
        self.path: str = asset.get("path") or ""
        self.sha256: str = (asset.get("sha256") or "").lower()
        self.critical: bool = bool(asset.get("critical"))
        self.layer: Optional[str] = asset.get("layer")
        self.role: Optional[str] = asset.get("role")
        self.media_type: Optional[str] = asset.get("mediaType") or asset.get("media_type")
        # Absolute url is optional; when assetOriginBase is set we join path to it.
        self.url: Optional[str] = asset.get("url")
        self.state: str = PENDING
        self.checksum_ok: bool = False
        self.reason: Optional[str] = None

    def as_status(self) -> Dict[str, Any]:
        # The lesson_preload_status per-asset wire shape (plan §5.5): {key,state,checksumOk}.
        return {"key": self.key, "state": self.state, "checksumOk": self.checksum_ok}


class AssetCache:
    """Owns download + verification + the realtime guard for one lesson's assets.

    Constructor deps are injectable so the §10.2 pytest can drive it without a real
    network, a real connection, or wall-clock sleeps:

    * ``client``      — an ``httpx.AsyncClient``-like object exposing ``.stream(...)``.
    * ``busy_check``  — ``() -> bool``; True pauses downloads (defaults to
                        ``conn.is_realtime_busy``). Exhaustive-by-default: any
                        non-IDLE realtime state pauses.
    * ``clock``/``sleep`` — time + await hooks for deterministic timeout tests.
    """

    def __init__(
        self,
        *,
        assets: List[Dict[str, Any]],
        profile: str,
        asset_origin_base: Optional[str] = None,
        cache_root: str = "data/lesson_assets",
        lesson_key: str = "lesson",
        lesson_version: int = 0,
        manifest_checksum: str = "",
        preload_timeout_sec: float = 90.0,
        concurrency: int = 2,
        client: Any = None,
        busy_check: Optional[Callable[[], bool]] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        logger: Any = None,
        chunk_size: int = _DEFAULT_CHUNK,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self.profile = profile
        self.asset_origin_base = (asset_origin_base or "").rstrip("/")
        # P5 version-aware cache key: (lessonId, lessonVersion, manifest_checksum).
        # Two authored versions of the same lesson — or the same version republished
        # with different bytes (new checksum) — land in DISJOINT directories so their
        # assets can never collide / be re-attested across versions. ``cache_key`` is
        # the stable identity republish-on-connect compares to decide a re-pull.
        self.lesson_key = lesson_key
        self.lesson_version = int(lesson_version or 0)
        self.manifest_checksum = manifest_checksum or ""
        self.cache_key = self._compose_cache_key(
            lesson_key, self.lesson_version, self.manifest_checksum
        )
        self.cache_dir = os.path.join(cache_root, self.cache_key)
        self.preload_timeout_sec = float(preload_timeout_sec)
        self.concurrency = max(1, int(concurrency))
        self._client = client
        self._owns_client = False
        self._busy_check = busy_check or (lambda: False)
        self._now = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._logger = logger
        self._chunk = chunk_size
        self._poll_interval = poll_interval
        self._deadline: Optional[float] = None

        # Skip manifest assets with a falsy key (no id/assetId): they would crash
        # _final_path (None.replace) and collide in _by_key (every None overwrites
        # the prior). We drop + log them rather than abort the whole preload — one
        # malformed asset must not block an otherwise-renderable lesson. A DROPPED
        # *critical* asset cannot reach READY, so is_ready() naturally stays false
        # via criticalReady < criticalTotal (no silent ready:true).
        self.assets: List[AssetState] = []
        for a in assets:
            if not a.get("key"):
                self._log(
                    "warning",
                    "skipping manifest asset with empty key "
                    f"(missing id/assetId): {self._describe_keyless(a)}",
                )
                continue
            self.assets.append(AssetState(a))
        self._by_key: Dict[str, AssetState] = {a.key: a for a in self.assets}

    @staticmethod
    def _describe_keyless(asset: Dict[str, Any]) -> str:
        """Best-effort identifier for an asset we cannot key on (for the skip log)."""
        return str(asset.get("path") or asset.get("layer") or asset.get("role") or "?")

    @staticmethod
    def _compose_cache_key(lesson_key: str, lesson_version: int, manifest_checksum: str) -> str:
        """Filesystem-safe ``<lesson>/v<version>-<checksum8>`` cache-dir suffix.

        ``lesson_version``/``manifest_checksum`` are appended only when present, so a
        slice caller that constructs an AssetCache with neither keeps the legacy
        ``<lesson_key>`` directory verbatim (back-compat, no migration needed)."""
        safe = (lesson_key or "lesson").replace("/", "_").replace("..", "_")
        parts = [safe]
        suffix = ""
        if lesson_version:
            suffix += f"v{lesson_version}"
        if manifest_checksum:
            suffix += ("-" if suffix else "") + manifest_checksum[:8]
        if suffix:
            parts.append(suffix)
        return os.path.join(*parts)

    # ── public API ───────────────────────────────────────────────────────────

    @property
    def critical_assets(self) -> List[AssetState]:
        return [a for a in self.assets if a.critical]

    def is_ready(self) -> bool:
        """Binary READY rule (plan §5.5/§6.2.3): every critical asset present AND
        every sha256 verified. ANY critical not-READY / checksum-unverified -> False."""
        criticals = self.critical_assets
        if not criticals:
            return False
        return all(a.state == READY and a.checksum_ok for a in criticals)

    def synthesize_preload_status(self, assignment_version: int) -> Dict[str, Any]:
        """ESP-SYNTHESIZED ``lesson_preload_status.body`` (D-PRELOAD-OWNER) — firmware
        never emits this. ``ready`` is THE start gate."""
        criticals = self.critical_assets
        critical_ready = sum(1 for a in criticals if a.state == READY and a.checksum_ok)
        return {
            "assignmentVersion": assignment_version,
            "ready": self.is_ready(),
            "criticalTotal": len(criticals),
            "criticalReady": critical_ready,
            "assets": [a.as_status() for a in self.assets],
        }

    def assert_profile_renderable(self) -> None:
        """Profile reject (plan §5.4/§6.2.4): on espTft, NEVER enter PRELOADING for a
        manifest whose critical backgroundScene asset is full video."""
        if self.profile != "espTft":
            return
        for a in self.critical_assets:
            is_video = a.role == "video" or (a.media_type or "").lower().startswith("video/")
            if a.layer == "backgroundScene" and is_video:
                raise AssetProfileUnavailable(
                    "espTft cannot render a critical full-video backgroundScene",
                    context={"assetKey": a.key, "mediaType": a.media_type, "role": a.role},
                )

    async def preload(self) -> bool:
        """Download + verify all assets. Returns the binary READY result.

        Raises ``ASSET_PROFILE_UNAVAILABLE`` before any byte is fetched for a
        forced-video espTft manifest, ``ASSET_CHECKSUM_MISMATCH`` on a critical
        sha mismatch, or ``PRELOAD_TIMEOUT`` if READY is not reached in time.
        """
        self.assert_profile_renderable()
        os.makedirs(self.cache_dir, exist_ok=True)
        await self._ensure_client()
        self._deadline = self._now() + self.preload_timeout_sec

        # Restart re-attest (plan §6.3.5 / ADR 0013 §C): re-hash any surviving cached
        # critical bytes BEFORE fetching — never presence-trust, never re-download
        # already-verified bytes. On a cold cache this is a cheap no-op.
        await self.reattest()

        sem = asyncio.Semaphore(self.concurrency)

        async def _run(asset: AssetState) -> None:
            async with sem:
                await self._download_one(asset)

        # Only fetch what re-attest did not already verify. Criticals first so READY
        # (and the start gate) opens ASAP; optional assets continue best-effort even
        # after ready:true (plan §5.5).
        pending = [a for a in self.assets if a.state != READY]
        ordered = sorted(pending, key=lambda a: 0 if a.critical else 1)
        # Wrap each per-asset coroutine as a Task so we can CANCEL still-running
        # siblings when one fails. A bare ``gather(*coros)`` propagates the first
        # exception but lets the other coroutines keep running detached on the shared
        # client — orphan downloads that keep streaming + leave ``.part`` files behind
        # (the finally _maybe_close_client(force=False) is a no-op, so they never even
        # get their socket reclaimed). On any raise (critical AssetChecksumMismatch,
        # PreloadTimeout, or wall-clock TimeoutError) we cancel the rest, await their
        # unwind, and sweep orphan ``.part`` files before re-raising.
        tasks = [asyncio.ensure_future(_run(a)) for a in ordered]
        try:
            # A single wall-clock bound (PRELOAD_TIMEOUT) over the whole fetch —
            # covers busy-starvation AND a pre-first-byte handshake hang.
            await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=self.preload_timeout_sec,
            )
        except asyncio.TimeoutError:
            await self._cancel_pending(tasks, ordered)
            raise PreloadTimeout()
        except BaseException:
            # AssetChecksumMismatch on a critical asset (or any other failure) —
            # tear down siblings so no download outlives this preload.
            await self._cancel_pending(tasks, ordered)
            raise
        finally:
            await self._maybe_close_client()
        return self.is_ready()

    async def _cancel_pending(
        self, tasks: List["asyncio.Future"], assets: List[AssetState]
    ) -> None:
        """Cancel still-running download tasks and reap orphan ``.part`` files.

        Called when ``gather`` raises (a critical checksum mismatch, a timeout) so
        that no sibling download keeps streaming on the shared client after preload
        has decided the lesson is not READY. Cancellation is best-effort and must not
        mask the original error: we swallow CancelledError + cleanup faults here."""
        for t in tasks:
            if not t.done():
                t.cancel()
        # Await every task so cancellation actually propagates into the coroutine
        # (closing the in-flight ``stream(...)`` context) before we return and close
        # the client. return_exceptions=True keeps a sibling fault from shadowing the
        # original raise the caller is propagating.
        await asyncio.gather(*tasks, return_exceptions=True)
        # Sweep any ``.part`` files left by a cancelled mid-stream write. A cancelled
        # _download_one is interrupted at an ``await`` (it never reaches its own
        # except/finally cleanup), so its tmp file is orphaned — remove it here.
        for asset in assets:
            try:
                if asset.state == READY:
                    continue
                self._safe_remove(self._tmp_path(asset))
            except Exception:  # pragma: no cover - cleanup is best-effort
                pass

    async def reattest(self) -> bool:
        """Restart re-attest (plan §6.3.5 / ADR 0013 §C): re-run sha256 over cached
        critical assets BEFORE re-reporting READY. Presence on disk is never trusted."""
        for asset in self.critical_assets:
            final_path = self._final_path(asset)
            if not os.path.exists(final_path):
                asset.state = PENDING
                asset.checksum_ok = False
                continue
            digest = await asyncio.to_thread(self._hash_file, final_path)
            if digest == asset.sha256:
                asset.state = READY
                asset.checksum_ok = True
            else:
                # Cached bytes are stale/corrupt — discard, do NOT serve unverified.
                asset.state = FAILED
                asset.checksum_ok = False
                asset.reason = "checksum_mismatch"
                self._safe_remove(final_path)
        return self.is_ready()

    async def aclose(self) -> None:
        await self._maybe_close_client(force=True)

    async def evict(self) -> None:
        """P5 eviction (republish-on-connect): tear down THIS version's cache so a
        newer ``lessonVersion`` / ``manifestChecksum`` re-pulls fresh bytes instead
        of re-attesting stale ones. Reuses the existing client teardown (``aclose``),
        then removes the version-scoped cache dir and flips every asset to EVICTED.

        Disjoint cache dirs mean evicting one version NEVER touches another version's
        bytes. Best-effort + idempotent: a missing dir or a closed client is a no-op,
        so an in-flight teardown can call it without guarding."""
        await self.aclose()
        for asset in self.assets:
            asset.state = EVICTED
            asset.checksum_ok = False
            asset.reason = "evicted"
        try:
            if os.path.isdir(self.cache_dir):
                shutil.rmtree(self.cache_dir, ignore_errors=True)
        except OSError:  # pragma: no cover - best-effort teardown
            pass

    # ── internals ────────────────────────────────────────────────────────────

    async def _download_one(self, asset: AssetState) -> None:
        asset.state = DOWNLOADING
        asset.checksum_ok = False
        # Realtime guard also gates request INITIATION: do not even open the TCP/TLS
        # connection during a voice turn (the voice path always wins). May raise
        # PRELOAD_TIMEOUT if the turn never yields within the deadline.
        await self._wait_while_busy()
        url = self._resolve_url(asset)
        tmp_path = self._tmp_path(asset)
        hasher = hashlib.sha256()
        try:
            async with self._client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes(self._chunk):
                        # Realtime guard: the voice path always wins. Pause (do not
                        # throttle) before consuming each chunk while busy.
                        await self._wait_while_busy()
                        if not chunk:
                            continue
                        hasher.update(chunk)
                        fh.write(chunk)
        except PreloadTimeout:
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.reason = "preload_timeout"
            raise
        except Exception as exc:  # network / HTTP failure (NOT a checksum fault)
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.checksum_ok = False
            asset.reason = "network_error"
            self._log("warning", f"asset {asset.key} download failed: {type(exc).__name__}")
            # A critical network failure leaves READY false (PRELOAD_TIMEOUT bounds it);
            # we do NOT raise a checksum mismatch for a transport error.
            return

        digest = hasher.hexdigest()
        if digest != asset.sha256:
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.checksum_ok = False
            asset.reason = "checksum_mismatch"
            self._log(
                "error",
                f"asset {asset.key} checksum mismatch want={_sha_prefix(asset.sha256)} got={_sha_prefix(digest)}",
            )
            if asset.critical:
                # Blocks READY permanently until re-publish; no best-effort render.
                raise AssetChecksumMismatch(asset.key)
            return

        os.replace(tmp_path, self._final_path(asset))
        asset.state = READY
        asset.checksum_ok = True
        self._log("info", f"asset {asset.key} READY sha={_sha_prefix(asset.sha256)}")

    async def _wait_while_busy(self) -> None:
        while self._busy_check():
            if self._deadline is not None and self._now() >= self._deadline:
                raise PreloadTimeout()
            await self._sleep(self._poll_interval)
        if self._deadline is not None and self._now() >= self._deadline:
            raise PreloadTimeout()

    def _resolve_url(self, asset: AssetState) -> str:
        # assetOriginBase (one config value, D-ASSET-HOST) joined to the relative
        # path; fall back to a manifest-provided absolute url when no base is set.
        if self.asset_origin_base and asset.path:
            return f"{self.asset_origin_base}/{asset.path.lstrip('/')}"
        if asset.url:
            return asset.url
        return asset.path

    def _tmp_path(self, asset: AssetState) -> str:
        # Per-INSTANCE (not per-call) temp path so concurrent robots running the same
        # lesson never share a ``.part`` file — each ConnectionHandler gets its own
        # AssetCache, so ``id(self)`` differs across connections -> disjoint temp files.
        # Uniqueness is deterministic (id(self) + PID never change for a live instance),
        # so a given instance always reproduces the same path: that's what lets the
        # _cancel_pending orphan sweep reconstruct + remove THIS instance's tmp file.
        # NOTE: a per-call uuid4 suffix would break that sweep (it could not rebuild the
        # path) and leak orphan .part files on timeout/checksum-cancel.
        return f"{self._final_path(asset)}.{os.getpid()}.{id(self):x}.part"

    def _final_path(self, asset: AssetState) -> str:
        # Content lives under data/<lesson>/<assetKey>; keys are filesystem-safe slugs.
        # Defensive: a falsy key would AttributeError on ``None.replace`` and is never
        # a valid on-disk slug. Construction already rejects keyless assets, so this
        # guards only a programmatic misuse path — fail loudly, never write to the
        # cache_dir root.
        if not asset.key:
            raise ValueError("cannot resolve a cache path for an asset with an empty key")
        safe = asset.key.replace("/", "_").replace("..", "_")
        return os.path.join(self.cache_dir, safe)

    def _hash_file(self, path: str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(self._chunk), b""):
                hasher.update(block)
        return hasher.hexdigest()

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        if httpx is None:  # pragma: no cover
            raise RuntimeError("httpx is required for asset preload but is not installed")
        # Dedicated client: NO Authorization header, keep-alive disabled to match the
        # process-wide connection-reuse hygiene of the manager-api client.
        self._client = httpx.AsyncClient(
            headers={"User-Agent": f"TbotLessonPreload/1.0 (PID:{os.getpid()})"},
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=0),
            follow_redirects=True,
        )
        self._owns_client = True

    async def _maybe_close_client(self, force: bool = False) -> None:
        if self._client is not None and self._owns_client and force:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            self._owns_client = False

    def _safe_remove(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.bind(tag=TAG).__getattribute__(level)(message)
        except Exception:
            pass
