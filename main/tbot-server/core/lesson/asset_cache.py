"""S7 — ESP asset preload cache (D-PRELOAD-OWNER OWNER, ADR 0013 §C).

The ESP Server is the authoritative sha256 verifier and READY gate. It:

* fetches each manifest-declared asset over a DEDICATED ``httpx`` client that
  carries NO ``Authorization: Bearer`` (the manager-api secret must never leak to
  the asset origin) and is bound to one ``assetOriginBase`` (D-ASSET-HOST waiver),
* streams ``hashlib.sha256`` over the response so hashing never blocks the WS loop,
* reports READY **only** when every manifest-declared asset exists locally AND every
  sha256 passes (binary rule); a checksum mismatch blocks READY permanently (no
  auto-retry), and
* PAUSES all ESP-side asset downloads during actual realtime voice activity (the
  voice path always wins) while allowing bounded progress if a live provider leaves
  its interaction state busy after transient speech has ended, and
* materializes a verified SD asset pack when configured, so firmware can read local
  ``sd://`` bytes during render without owning checksum verification.

Firmware may fetch/read the ESP-verified URLs or SD pack paths at render time, but
does not compute sha256 or emit ``lesson_preload_status``. The ESP synthesizes that
status from this cache and owns the start READY gate.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import shutil
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote

try:  # httpx is a hard runtime dep (==0.28.1); guarded so unit tests can inject a fake.
    import httpx
except Exception:  # pragma: no cover - exercised only where httpx is absent
    httpx = None  # type: ignore

from core.lesson.errors import (
    AssetChecksumMismatch,
    AssetProfileUnavailable,
    PreloadTimeout,
)
from core.lesson.flattened_cinematic_contract import (
    FlattenedCinematicContractError,
    validate_flattened_cinematic_cache_asset,
    validate_pathless_flattened_cinematic_cache_asset,
)
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    validate_renderer_v3_shared_mp4,
    validate_renderer_v4_flattened_mp4,
)
from core.lesson.shared_asset_store import SharedAssetStore

TAG = "LessonAssetCache"

# Per-asset lifecycle states (wire enum: PENDING|DOWNLOADING|READY|FAILED|EVICTED).
PENDING = "PENDING"
DOWNLOADING = "DOWNLOADING"
READY = "READY"
FAILED = "FAILED"
EVICTED = "EVICTED"

_DEFAULT_CHUNK = 64 * 1024
_DEFAULT_POLL_INTERVAL = 0.2
_SOFT_BUSY_PROGRESS_GRACE_SEC = 0.1
_DEFAULT_MAX_ASSET_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_TOTAL_ASSET_BYTES = 64 * 1024 * 1024
_DEFAULT_SD_MAX_ASSET_BYTES = 32 * 1024 * 1024
_DEFAULT_SD_MAX_TOTAL_ASSET_BYTES = 128 * 1024 * 1024
_MAX_SD_CONFIG_BYTES = 2 * 1024 * 1024 * 1024
_ESPTFT_BACKGROUND_MAX_SIZE = (320, 240)


def _sha_prefix(value: Optional[str]) -> str:
    return (value or "")[:8]


def _sd_env_int_or_default(name: str, default: int) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > _MAX_SD_CONFIG_BYTES:
        return None
    return value

def _normalize_esptft_background_jpeg(content: bytes) -> bytes:
    """Return a small baseline JPEG that firmware can decode inside its SRAM budget."""
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - environment/dependency failure
        raise RuntimeError("Pillow is required to normalize espTft lesson posters") from exc

    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGB")
        image.thumbnail(_ESPTFT_BACKGROUND_MAX_SIZE)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=82, optimize=False, progressive=False)
        return out.getvalue()


class AssetState:
    """Mutable per-asset record. All manifest assets gate READY for SD-safe lessons."""

    __slots__ = (
        "key", "path", "sha256", "size", "critical", "layer", "role", "media_type", "url",
        "shared_asset_key", "shared_asset_version", "compatibility_metadata", "visual_refs",
        "derivative_id", "phase_id", "cue_id", "effect", "step_key", "playback_mode",
        "renderer_v3_mp4", "renderer_v4_mp4",
        "state", "checksum_ok", "reason",
    )

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
        raw_sha256 = asset.get("sha256")
        self.sha256: str = raw_sha256.lower() if isinstance(raw_sha256, str) else ""
        self.size: Optional[int] = asset.get("size") if type(asset.get("size")) is int else None
        self.critical: bool = bool(asset.get("critical"))
        self.layer: Optional[str] = asset.get("layer")
        self.role: Optional[str] = asset.get("role")
        self.media_type: Optional[str] = asset.get("mediaType") or asset.get("media_type")
        # Absolute url is optional; when assetOriginBase is set we join path to it.
        self.url: Optional[str] = asset.get("onlineUrl") or asset.get("url")
        self.shared_asset_key: Optional[str] = asset.get("sharedAssetKey")
        self.shared_asset_version: Optional[int] = asset.get("sharedAssetVersion")
        self.compatibility_metadata: Any = asset.get("compatibilityMetadata")
        self.visual_refs: Any = asset.get("visualRefs")
        self.derivative_id: Optional[str] = asset.get("derivativeId")
        self.phase_id: Optional[str] = asset.get("phaseId")
        self.cue_id: str | None = asset.get("cueId")
        self.effect: str | None = asset.get("effect")
        self.step_key: str | None = asset.get("stepKey")
        self.playback_mode: str | None = asset.get("playbackMode")
        try:
            validate_renderer_v3_shared_mp4(asset)
            self.renderer_v3_mp4 = True
        except FirmwareSyncPackError:
            self.renderer_v3_mp4 = False
        try:
            validate_renderer_v4_flattened_mp4(asset)
            self.renderer_v4_mp4 = True
        except FirmwareSyncPackError:
            try:
                validate_flattened_cinematic_cache_asset(asset)
                self.renderer_v4_mp4 = True
            except FlattenedCinematicContractError:
                try:
                    validate_pathless_flattened_cinematic_cache_asset(asset)
                    self.renderer_v4_mp4 = True
                except FlattenedCinematicContractError:
                    self.renderer_v4_mp4 = False
        self.state: str = PENDING
        self.checksum_ok: bool = False
        self.reason: Optional[str] = None

    def as_status(self) -> Dict[str, Any]:
        # Runtime forwards READY/FAILED critical records to the backend from this
        # synthesized status, so preserve both identity and criticality.
        return {
            "key": self.key,
            "assetId": self.key,
            "state": self.state,
            "checksumOk": self.checksum_ok,
            "critical": self.critical,
        }


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
        public_base_url: Optional[str] = None,
        asset_pack_local_root: Optional[str] = None,
        asset_pack_mount_root: Optional[str] = None,
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
        image_normalizer: Optional[Callable[[bytes], bytes]] = None,
        chunk_size: int = _DEFAULT_CHUNK,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES,
        max_total_asset_bytes: int = _DEFAULT_MAX_TOTAL_ASSET_BYTES,
        shared_asset_store: Optional[SharedAssetStore] = None,
    ) -> None:
        self.profile = profile
        self.asset_origin_base = (asset_origin_base or "").rstrip("/")
        self.public_base_url = (public_base_url or "").rstrip("/")
        self.asset_pack_local_root = (asset_pack_local_root or "sd://tbot/lesson-assets").rstrip("/")
        self.asset_pack_mount_root = os.path.realpath(asset_pack_mount_root) if asset_pack_mount_root else ""
        self._shared_asset_store = shared_asset_store
        if self.asset_pack_mount_root and self._shared_asset_store is None:
            self._shared_asset_store = SharedAssetStore(
                os.path.dirname(self.asset_pack_mount_root),
                pack_root=self.asset_pack_mount_root,
            )
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
        self._image_normalizer = image_normalizer or _normalize_esptft_background_jpeg
        self._chunk = chunk_size
        self._poll_interval = poll_interval
        self._max_asset_bytes = int(max_asset_bytes or _DEFAULT_MAX_ASSET_BYTES)
        self._max_total_asset_bytes = int(max_total_asset_bytes or _DEFAULT_MAX_TOTAL_ASSET_BYTES)
        self._sd_max_asset_bytes = _sd_env_int_or_default(
            "LESSON_SD_MAX_FILE_BYTES", _DEFAULT_SD_MAX_ASSET_BYTES
        )
        self._sd_max_total_asset_bytes = _sd_env_int_or_default(
            "LESSON_SD_MAX_PACK_BYTES", _DEFAULT_SD_MAX_TOTAL_ASSET_BYTES
        )
        self._downloaded_total_bytes = 0
        self._deadline: Optional[float] = None
        self._soft_busy_since: Optional[float] = None
        self._pack_only_assets: Dict[str, tuple[str, int]] = {}
        self._hydrated_ready_shared_pack = False

        # Skip manifest assets with a falsy key (no id/assetId): they would crash
        # _final_path (None.replace) and collide in _by_key (every None overwrites
        # the prior). Track every dropped asset so malformed manifests can never
        # report READY after the keyed subset downloads successfully.
        self.assets: List[AssetState] = []
        self._dropped_asset_count = 0
        for a in assets:
            if not a.get("key"):
                self._dropped_asset_count += 1
                self._log(
                    "warning",
                    "skipping manifest asset with empty key "
                    f"(missing id/assetId): {self._describe_keyless(a)}",
                )
                continue
            self.assets.append(AssetState(a))
        self._by_key: Dict[str, AssetState] = {a.key: a for a in self.assets}
        self._by_source: Dict[str, AssetState] = {}
        for asset in self.assets:
            for source in self._source_aliases(asset):
                self._by_source.setdefault(source, asset)

    @staticmethod
    def _describe_keyless(asset: Dict[str, Any]) -> str:
        """Best-effort identifier for an asset we cannot key on (for the skip log)."""
        return str(asset.get("path") or asset.get("layer") or asset.get("role") or "?")

    @staticmethod
    def _compose_cache_key(lesson_key: str, lesson_version: int, manifest_checksum: str) -> str:
        """Filesystem-safe ``<lesson>/v<version>-<manifest-checksum>`` cache-dir suffix.

        ``lesson_version``/``manifest_checksum`` are appended only when present, so a
        slice caller that constructs an AssetCache with neither keeps the legacy
        ``<lesson_key>`` directory verbatim (back-compat, no migration needed)."""
        safe = (lesson_key or "lesson").replace("/", "_").replace("..", "_")
        parts = [safe]
        suffix = ""
        if lesson_version:
            suffix += f"v{lesson_version}"
        if manifest_checksum:
            safe_checksum = "".join(
                ch if ch.isalnum() or ch in "._-" else "_"
                for ch in manifest_checksum.strip()
            ).replace("..", "_")
            suffix += ("-" if suffix else "") + safe_checksum
        if suffix:
            parts.append(suffix)
        return os.path.join(*parts)

    # ── public API ───────────────────────────────────────────────────────────

    @property
    def critical_assets(self) -> List[AssetState]:
        return [a for a in self.assets if a.critical]

    @property
    def required_assets(self) -> List[AssetState]:
        return self.assets

    def is_ready(self) -> bool:
        """Binary READY rule: every manifest asset is present and sha256 verified.

        Backend and firmware now require all three visual layer image sources for a
        lesson_step, so SD/cache READY cannot ignore a non-critical robotOverlay pose.
        """
        if self._dropped_asset_count > 0:
            return False
        required = self.required_assets
        if not required:
            return False
        return all(
            a.state == READY
            and a.checksum_ok
            and (
                self._asset_cache_materialized(a)
                or self._pack_only_asset_ready(a)
            )
            and self._asset_pack_materialized(a)
            for a in required
        ) and (
            self._materialized_total_bytes(required) <= self._ready_total_asset_limit()
            and self._asset_pack_ready()
        )

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

    def public_url_for_source(self, source: str) -> Optional[str]:
        if not self.public_base_url or not source:
            return None
        asset = self._asset_for_source(source)
        if asset is None or asset.state != READY or not asset.checksum_ok:
            return None
        if not self._asset_cache_materialized(asset):
            return None
        token = base64.urlsafe_b64encode(self.cache_key.encode("utf-8")).decode("ascii")
        token = token.rstrip("=")
        return f"{self.public_base_url}/tbot/lesson-assets/{token}/{quote(asset.key, safe='')}"

    def local_pack_url_for_source(self, source: str) -> Optional[str]:
        if not source:
            return None
        asset = self._asset_for_source(source)
        if asset is None or asset.state != READY or not asset.checksum_ok:
            return None
        if not (
            self._asset_cache_materialized(asset)
            or self._pack_only_asset_ready(asset)
        ):
            return None
        if not self._asset_pack_materialized(asset):
            return None
        return self._local_pack_path(asset)

    def asset_pack_manifest(
        self,
        *,
        assignment_version: int,
        lesson_id: str,
        lesson_version: int,
        manifest_checksum: str,
    ) -> Dict[str, Any]:
        return {
            "assignmentVersion": assignment_version,
            "lessonId": lesson_id,
            "lessonVersion": lesson_version,
            "manifestChecksum": manifest_checksum,
            "cacheKey": self.cache_key,
            "localRoot": f"{self.asset_pack_local_root}/{self.cache_key}",
            "ready": self.is_ready(),
            "assets": [
                self._asset_pack_record(asset)
                for asset in self.assets
                if asset.state == READY
                and asset.checksum_ok
                and (
                    self._asset_cache_materialized(asset)
                    or self._pack_only_asset_ready(asset)
                )
                and self._asset_pack_materialized(asset)
            ],
        }

    def _asset_pack_record(self, asset: AssetState) -> Dict[str, Any]:
        pack_path = self._asset_pack_path(asset)
        materialized_path = pack_path if pack_path and os.path.exists(pack_path) else self._asset_pack_source_path(asset)
        size = os.path.getsize(materialized_path) if os.path.exists(materialized_path) else None
        sha256 = self._hash_file(materialized_path) if os.path.exists(materialized_path) else asset.sha256
        download_url = self._resolve_url(asset)
        if self.public_base_url and self._asset_cache_materialized(asset):
            token = base64.urlsafe_b64encode(self.cache_key.encode("utf-8")).decode("ascii").rstrip("=")
            # Keep '@' literal in the HTTP path. Some firmware HTTP stacks escape
            # an existing "%40" again, while '@' is valid inside a path segment.
            #
            # DO NOT "unify" this with cache_key_contract.encode_asset_basename (F-T51-02).
            # That helper is the shared BACKEND<->ESP wire contract for the pack BASENAME —
            # the name the file is written under on the SD card — and it uses safe="".
            # This line is a different thing: the ESP's own HTTP fetch URL, served locally by
            # core/http_server.py's /tbot/lesson-assets/{cacheToken}/{assetKey} route. It is
            # ESP-local, not a wire contract, and the deliberate safe='@' divergence exists
            # for the firmware constraint above. Shared-visual keys are exactly
            # "<sharedKey>@v<N>", so this is the key shape where it actually matters.
            # Pinned by test_lesson_asset_cache.py's ".../object.robot@v1" expectation —
            # if you change this, that test fails, and that failure is correct.
            download_url = f"{self.public_base_url}/tbot/lesson-assets/{token}/{quote(asset.key, safe='@')}"
        record = {
            "key": asset.key,
            "path": asset.path,
            "url": download_url,
            "sourceUrl": asset.url,
            "sha256": sha256,
            "sourceSha256": asset.sha256 if sha256 != asset.sha256 else None,
            "size": size,
            "mediaType": asset.media_type,
            "critical": asset.critical,
            "layer": asset.layer,
            "role": asset.role,
            "state": asset.state,
            "checksumOk": asset.checksum_ok,
            "localPath": self._local_pack_path(asset),
            "sharedAssetKey": asset.shared_asset_key,
            "sharedAssetVersion": asset.shared_asset_version,
            "compatibilityMetadata": asset.compatibility_metadata,
            "visualRefs": asset.visual_refs,
            "derivativeId": asset.derivative_id,
            "phaseId": asset.phase_id,
            "cueId": asset.cue_id,
            "effect": asset.effect,
            "stepKey": asset.step_key,
            "playbackMode": asset.playback_mode,
        }
        return {k: v for k, v in record.items() if v is not None}

    def _local_pack_path(self, asset: AssetState) -> str:
        return f"{self.asset_pack_local_root}/{self.cache_key}/{quote(asset.key, safe='')}"

    def _asset_cache_materialized(self, asset: AssetState) -> bool:
        try:
            path = self._final_path(asset)
            if not os.path.exists(path) or self._hash_file(path) != asset.sha256:
                return False
            if self._requires_render_safe_derivative(asset):
                render_path = self._render_safe_path(asset)
                return os.path.exists(render_path) and os.path.getsize(render_path) > 0
            return True
        except (OSError, ValueError):
            return False

    def _asset_pack_materialized(self, asset: AssetState) -> bool:
        if not self.asset_pack_mount_root:
            return True
        if self._pack_only_asset_ready(asset):
            return True
        try:
            path = self._asset_pack_path(asset)
            source = self._asset_pack_source_path(asset)
            materialized = (
                os.path.exists(path)
                and os.path.exists(source)
                and self._hash_file(path) == self._hash_file(source)
            )
            return materialized
        except (OSError, ValueError):
            return False

    def _pack_only_asset_ready(self, asset: AssetState) -> bool:
        attestation = self._pack_only_assets.get(asset.key)
        if attestation is None:
            return False
        digest, size = attestation
        try:
            path = self._asset_pack_path(asset)
            return (
                os.path.isfile(path)
                and os.path.getsize(path) == size
                and self._hash_file(path) == digest
            )
        except (OSError, ValueError):
            return False

    def _asset_pack_ready(self) -> bool:
        if not self.asset_pack_mount_root or self._shared_asset_store is None:
            return True
        return self._shared_asset_store.is_pack_ready(self.cache_key)

    def _materialized_total_bytes(self, assets: List[AssetState]) -> int:
        total = 0
        for asset in assets:
            try:
                path = self._final_path(asset)
                if os.path.exists(path):
                    total += os.path.getsize(path)
                elif self._pack_only_asset_ready(asset):
                    total += os.path.getsize(self._asset_pack_path(asset))
            except (OSError, ValueError):
                return self._ready_total_asset_limit() + 1
        return total

    def _ready_total_asset_limit(self) -> int:
        if (
            self._hydrated_ready_shared_pack
            and self.asset_pack_mount_root
            and self._sd_max_total_asset_bytes is not None
        ):
            return self._sd_max_total_asset_bytes
        return self._max_total_asset_bytes

    def _asset_for_source(self, source: str) -> Optional[AssetState]:
        if source in self._by_source:
            return self._by_source[source]
        clean = source.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if clean in self._by_source:
            return self._by_source[clean]
        for asset in self.assets:
            if asset.path and clean.endswith("/" + asset.path.lstrip("/")):
                return asset
        return None

    def _source_aliases(self, asset: AssetState) -> List[str]:
        aliases = []
        if asset.path:
            aliases.append(asset.path)
            aliases.append(os.path.basename(asset.path))
            if self.asset_origin_base:
                aliases.append(f"{self.asset_origin_base}/{asset.path.lstrip('/')}")
        if asset.url:
            aliases.append(asset.url)
        return [a for a in aliases if a]

    def assert_profile_renderable(self) -> None:
        """Profile reject (plan §5.4/§6.2.4): on espTft, NEVER enter PRELOADING for a
        manifest whose critical backgroundScene asset is full video."""
        if self.profile != "espTft":
            return
        for a in self.critical_assets:
            is_video = a.role == "video" or (a.media_type or "").lower().startswith("video/")
            if is_video and not (a.renderer_v3_mp4 or a.renderer_v4_mp4):
                raise AssetProfileUnavailable(
                    "espTft only accepts validated lesson renderer MP4 assets",
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

        # Only fetch what re-attest did not already verify. Criticals first to keep
        # first useful pixels fast, but READY waits for every manifest asset.
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
            self._commit_asset_pack_if_complete()
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
        assets BEFORE re-reporting READY. Presence on disk is never trusted."""
        await asyncio.to_thread(self._hydrate_from_ready_shared_pack)
        total_bytes = 0
        for asset in self.required_assets:
            final_path = self._final_path(asset)
            if not os.path.exists(final_path):
                if self._pack_only_asset_ready(asset):
                    asset.state = READY
                    asset.checksum_ok = True
                    total_bytes += os.path.getsize(self._asset_pack_path(asset))
                else:
                    asset.state = PENDING
                    asset.checksum_ok = False
                continue
            digest = await asyncio.to_thread(self._hash_file, final_path)
            if digest == asset.sha256:
                if self._ensure_render_safe_derivative(asset):
                    asset.state = READY
                    asset.checksum_ok = True
                    self._materialize_asset_pack_file(asset)
                    total_bytes += os.path.getsize(final_path)
                else:
                    asset.state = FAILED
                    asset.checksum_ok = False
                    asset.reason = "render_derivative_failed"
            else:
                # Cached bytes are stale/corrupt — discard, do NOT serve unverified.
                self._safe_remove(final_path)
                if self._pack_only_asset_ready(asset):
                    asset.state = READY
                    asset.checksum_ok = True
                    total_bytes += os.path.getsize(self._asset_pack_path(asset))
                else:
                    asset.state = FAILED
                    asset.checksum_ok = False
                    asset.reason = "checksum_mismatch"
        if total_bytes > self._ready_total_asset_limit():
            for asset in self.required_assets:
                if asset.state == READY:
                    asset.state = FAILED
                    asset.checksum_ok = False
                    asset.reason = "lesson_assets_too_large"
        self._commit_asset_pack_if_complete()
        return self.is_ready()

    def _hydrate_from_ready_shared_pack(self) -> bool:
        """Restore an exact rich READY pack into the separate server cache."""
        self._pack_only_assets = {}
        self._hydrated_ready_shared_pack = False
        if self._shared_asset_store is None or not self.asset_pack_mount_root:
            return False
        if self._sd_max_asset_bytes is None or self._sd_max_total_asset_bytes is None:
            return False
        try:
            if not self._shared_asset_store.is_pack_ready(self.cache_key):
                return False
            pack_dir = self._asset_pack_dir()
            with open(os.path.join(pack_dir, "pack.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            checksum = manifest.get("manifestChecksum")
            if (
                manifest.get("cacheKey") != self.cache_key
                or manifest.get("lessonId") != self.lesson_key
                or manifest.get("profile") != self.profile
                or type(manifest.get("lessonVersion")) is not int
                or manifest.get("lessonVersion") != self.lesson_version
                or checksum != self.manifest_checksum
                or not isinstance(checksum, str)
                or len(checksum) != 64
                or any(ch not in "0123456789abcdef" for ch in checksum)
            ):
                return False
            records = manifest.get("assets")
            if not isinstance(records, list):
                return False
            by_key = {}
            for record in records:
                if not isinstance(record, dict):
                    return False
                key = record.get("key")
                if not isinstance(key, str) or not key or key in by_key:
                    return False
                by_key[key] = record
            if set(by_key) != {asset.key for asset in self.required_assets}:
                return False

            sources = {}
            pack_only_assets = {}
            total_bytes = 0
            for asset in self.required_assets:
                record = by_key[asset.key]
                digest = record.get("sha256")
                source_digest = record.get("sourceSha256")
                size = record.get("size")
                source = self._asset_pack_path(asset)
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)
                    or (
                        source_digest is not None
                        and (
                            not isinstance(source_digest, str)
                            or len(source_digest) != 64
                            or any(
                                ch not in "0123456789abcdef"
                                for ch in source_digest
                            )
                            or source_digest != asset.sha256
                        )
                    )
                    or type(size) is not int
                    or size < 0
                    or size > self._sd_max_asset_bytes
                    or not os.path.isfile(source)
                    or os.path.getsize(source) != size
                    or self._hash_file(source) != digest
                ):
                    return False
                total_bytes += size
                if total_bytes > self._sd_max_total_asset_bytes:
                    return False
                if digest == asset.sha256:
                    sources[asset.key] = source
                elif (
                    (
                        self._requires_render_safe_derivative(asset)
                        or asset.renderer_v4_mp4
                    )
                    and source_digest == asset.sha256
                ):
                    pack_only_assets[asset.key] = (digest, size)
                else:
                    return False

            os.makedirs(self.cache_dir, exist_ok=True)
            for key, source in sources.items():
                asset = self._by_key[key]
                final_path = self._final_path(asset)
                tmp_path = f"{final_path}.{os.getpid()}.{id(self):x}.hydrate.part"
                self._safe_remove(tmp_path)
                try:
                    with open(source, "rb") as source_file, open(tmp_path, "xb") as target:
                        shutil.copyfileobj(source_file, target)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(tmp_path, final_path)
                finally:
                    self._safe_remove(tmp_path)
            self._pack_only_assets = pack_only_assets
            self._hydrated_ready_shared_pack = True
            return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

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
            pack_dir = self._asset_pack_dir()
            if pack_dir and os.path.isdir(pack_dir):
                shutil.rmtree(pack_dir, ignore_errors=True)
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
        downloaded = 0
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
                        downloaded += len(chunk)
                        if downloaded > self._max_asset_bytes:
                            self._safe_remove(tmp_path)
                            asset.state = FAILED
                            asset.checksum_ok = False
                            asset.reason = "asset_too_large"
                            self._log(
                                "warning",
                                f"asset {asset.key} exceeds max size {self._max_asset_bytes} bytes",
                            )
                            return
                        if self._downloaded_total_bytes + len(chunk) > self._max_total_asset_bytes:
                            self._safe_remove(tmp_path)
                            asset.state = FAILED
                            asset.checksum_ok = False
                            asset.reason = "lesson_assets_too_large"
                            self._log(
                                "warning",
                                f"lesson assets exceed max total size {self._max_total_asset_bytes} bytes",
                            )
                            return
                        self._downloaded_total_bytes += len(chunk)
                        hasher.update(chunk)
                        fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
        except PreloadTimeout:
            self._downloaded_total_bytes = max(0, self._downloaded_total_bytes - downloaded)
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.reason = "preload_timeout"
            raise
        except Exception as exc:  # network / HTTP failure (NOT a checksum fault)
            self._downloaded_total_bytes = max(0, self._downloaded_total_bytes - downloaded)
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.checksum_ok = False
            asset.reason = "network_error"
            self._log("warning", f"asset {asset.key} download failed: {type(exc).__name__}")
            # Any network failure leaves READY false (PRELOAD_TIMEOUT bounds it);
            # we do NOT raise a checksum mismatch for a transport error.
            return

        if asset.size is not None and downloaded != asset.size:
            self._downloaded_total_bytes = max(0, self._downloaded_total_bytes - downloaded)
            self._safe_remove(tmp_path)
            asset.state = FAILED
            asset.checksum_ok = False
            asset.reason = "declared_size_mismatch"
            self._log("warning", f"asset {asset.key} size mismatch want={asset.size} got={downloaded}")
            return

        digest = hasher.hexdigest()
        if digest != asset.sha256:
            self._downloaded_total_bytes = max(0, self._downloaded_total_bytes - downloaded)
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

        final_path = self._final_path(asset)
        os.replace(tmp_path, final_path)
        if not self._ensure_render_safe_derivative(asset):
            asset.state = FAILED
            asset.checksum_ok = False
            asset.reason = "render_derivative_failed"
            if asset.critical:
                raise AssetProfileUnavailable(
                    "espTft background poster could not be normalized for firmware decode",
                    context={"assetKey": asset.key},
                )
            return
        asset.state = READY
        asset.checksum_ok = True
        self._materialize_asset_pack_file(asset)
        self._log("info", f"asset {asset.key} READY sha={_sha_prefix(asset.sha256)}")

    async def _wait_while_busy(self) -> None:
        while self._busy_check():
            now = self._now()
            if self._deadline is not None and now >= self._deadline:
                raise PreloadTimeout()
            voice_activity = self._user_streaming_voice_activity()
            if voice_activity is not None and not voice_activity[0]:
                last_audio_at = voice_activity[1]
                if self._soft_busy_since is None:
                    self._soft_busy_since = max(now, last_audio_at)
                else:
                    self._soft_busy_since = max(self._soft_busy_since, last_audio_at)
                if now - self._soft_busy_since >= _SOFT_BUSY_PROGRESS_GRACE_SEC:
                    return
            else:
                # Actual child/robot speech always wins and restarts the quiet grace.
                self._soft_busy_since = None
            await self._sleep(self._poll_interval)
        self._soft_busy_since = None
        if self._deadline is not None and self._now() >= self._deadline:
            raise PreloadTimeout()

    def _user_streaming_voice_activity(self) -> Optional[tuple[bool, float]]:
        """Read voice state and last ingress time for a stuck USER_STREAMING owner."""
        owner = getattr(self._busy_check, "__self__", None)
        if owner is None:
            return None
        signal_names = (
            "client_is_speaking",
            "client_have_voice",
            "_lesson_asset_audio_inflight",
            "_lesson_asset_last_audio_at",
        )
        try:
            state_reader = getattr(owner, "_realtime_interaction_state", None)
            if not callable(state_reader):
                return None
            state = state_reader()
            state = getattr(state, "value", state)
            if state != "USER_STREAMING" or not all(
                hasattr(owner, name) for name in signal_names
            ):
                return None
            active = any(bool(getattr(owner, name, False)) for name in signal_names[:3])
            last_audio_at = max(0.0, float(owner._lesson_asset_last_audio_at))
            return active, last_audio_at
        except Exception:
            return None

    def _resolve_url(self, asset: AssetState) -> str:
        # assetOriginBase (one config value, D-ASSET-HOST) joined to the relative
        # path; fall back to a manifest-provided absolute url when no base is set.
        if (asset.renderer_v3_mp4 or asset.renderer_v4_mp4) and asset.url:
            return asset.url
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

    def _render_safe_path(self, asset: AssetState) -> str:
        return f"{self._final_path(asset)}.render.jpg"

    def _requires_render_safe_derivative(self, asset: AssetState) -> bool:
        media_type = (asset.media_type or "").lower()
        return (
            self.profile == "espTft"
            and asset.layer == "backgroundScene"
            and asset.role == "poster"
            and (media_type == "image/jpeg" or media_type == "image/jpg")
        )

    def _ensure_render_safe_derivative(self, asset: AssetState) -> bool:
        if not self._requires_render_safe_derivative(asset):
            return True
        try:
            final_path = self._final_path(asset)
            with open(final_path, "rb") as fh:
                original = fh.read()
            normalized = self._image_normalizer(original)
            if not isinstance(normalized, (bytes, bytearray)) or not normalized:
                raise ValueError("empty normalized image")
            tmp_path = f"{self._render_safe_path(asset)}.{os.getpid()}.{id(self):x}.part"
            with open(tmp_path, "wb") as fh:
                fh.write(bytes(normalized))
            os.replace(tmp_path, self._render_safe_path(asset))
            return True
        except Exception as exc:
            self._safe_remove(self._render_safe_path(asset))
            self._log("warning", f"asset {asset.key} render-safe derivative failed: {type(exc).__name__}")
            return False

    def _asset_pack_dir(self) -> str:
        if not self.asset_pack_mount_root:
            return ""
        pack_dir = os.path.realpath(os.path.join(self.asset_pack_mount_root, self.cache_key))
        if pack_dir != self.asset_pack_mount_root and not pack_dir.startswith(self.asset_pack_mount_root + os.sep):
            raise ValueError("asset pack path escapes mount root")
        return pack_dir

    def _asset_pack_path(self, asset: AssetState) -> str:
        pack_dir = self._asset_pack_dir()
        if not pack_dir:
            return ""
        if not asset.key:
            raise ValueError("cannot resolve an asset pack path for an empty key")
        safe = quote(asset.key, safe="")
        path = os.path.realpath(os.path.join(pack_dir, safe))
        if path == pack_dir or not path.startswith(pack_dir + os.sep):
            raise ValueError("asset pack file path escapes pack dir")
        return path

    def _materialize_asset_pack_file(self, asset: AssetState) -> None:
        dest = self._asset_pack_path(asset)
        if not dest:
            return
        src = self._asset_pack_source_path(asset)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.realpath(src) == os.path.realpath(dest):
            return
        if self._shared_asset_store is not None:
            digest = self._hash_file(src)
            self._shared_asset_store.put_file_and_materialize(
                src, digest, self.cache_key, asset.key
            )
            return
        tmp = f"{dest}.{os.getpid()}.{id(self):x}.part"
        with open(src, "rb") as source, open(tmp, "xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, dest)

    def _commit_asset_pack_if_complete(self) -> None:
        if self._shared_asset_store is None or not self.asset_pack_mount_root:
            return
        assets = {}
        for asset in self.required_assets:
            if asset.state != READY or not asset.checksum_ok:
                return
            source = self._asset_pack_source_path(asset)
            if not os.path.exists(source):
                return
            digest = self._hash_file(source)
            assets[asset.key] = (source, digest)
        if assets:
            self._shared_asset_store.put_files_and_commit_pack(self.cache_key, assets)

    def _asset_pack_source_path(self, asset: AssetState) -> str:
        if self._requires_render_safe_derivative(asset):
            render_path = self._render_safe_path(asset)
            if os.path.exists(render_path):
                return render_path
        return self._final_path(asset)

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
