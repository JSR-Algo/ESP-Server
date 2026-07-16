"""Build the physical-path asset pack used only for firmware MCP sync."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote

from core.lesson.sd_pack_evict import CacheEvictionRefused, validate_cache_key

FIRMWARE_LESSON_ASSET_ROOT = "/sdcard/tbot/lesson-assets"
MAX_SYNC_ASSETS = 64
MAX_FAT_BASENAME_BYTES = 255
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_RESERVED_BASENAMES = frozenset(
    {
        ".",
        "..",
        "current.json",
        "pvg.json",
        "activation.json",
        "lesson-pack-activation.json",
    }
)
_RESERVED_SUFFIXES = (".tmp", ".download", ".part", ".backup")


class FirmwareSyncPackError(ValueError):
    pass


def _refuse() -> None:
    raise FirmwareSyncPackError("firmware sync pack invalid")


def _canonical_cache_key(value: Any) -> str:
    try:
        return validate_cache_key(value)
    except CacheEvictionRefused:
        _refuse()


def _encoded_basename(asset_key: Any) -> str:
    if not isinstance(asset_key, str) or not asset_key:
        _refuse()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in asset_key):
        _refuse()
    try:
        encoded = quote(asset_key, safe="")
        encoded_bytes = encoded.encode("ascii")
    except UnicodeEncodeError:
        _refuse()
    lowered = encoded.lower()
    if (
        not encoded_bytes
        or len(encoded_bytes) > MAX_FAT_BASENAME_BYTES
        or encoded.endswith(".")
        or lowered in _RESERVED_BASENAMES
        or lowered.endswith(_RESERVED_SUFFIXES)
    ):
        _refuse()
    return encoded


def build_firmware_sync_pack(pack: Any) -> dict[str, Any]:
    """Return a deep MCP-only copy; never rewrite the render/prepare pack."""
    if not isinstance(pack, dict):
        _refuse()
    cache_key = _canonical_cache_key(pack.get("cacheKey"))
    manifest_checksum = pack.get("manifestChecksum")
    if (
        not isinstance(manifest_checksum, str)
        or _LOWER_SHA256_RE.fullmatch(manifest_checksum) is None
        or not cache_key.endswith("-" + manifest_checksum)
    ):
        _refuse()
    assets = pack.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_SYNC_ASSETS:
        _refuse()
    destinations = set()
    basenames = []
    for asset in assets:
        if not isinstance(asset, dict):
            _refuse()
        basename = _encoded_basename(asset.get("key"))
        collision_key = basename.lower()
        if collision_key in destinations:
            _refuse()
        destinations.add(collision_key)
        basenames.append(basename)

    result = copy.deepcopy(pack)
    physical_root = f"{FIRMWARE_LESSON_ASSET_ROOT}/{cache_key}"
    result["localRoot"] = physical_root
    for index, basename in enumerate(basenames):
        result["assets"][index]["localPath"] = f"{physical_root}/{basename}"
    return result
