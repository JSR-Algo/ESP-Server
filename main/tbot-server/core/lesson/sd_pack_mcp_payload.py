"""Build the physical-path asset pack used only for firmware MCP sync."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote, urlsplit

from core.lesson.sd_pack_evict import CacheEvictionRefused, validate_cache_key

FIRMWARE_LESSON_ASSET_ROOT = "/sdcard/tbot/lesson-assets"
MAX_SYNC_ASSETS = 64
MAX_FAT_BASENAME_BYTES = 255
MAX_SAFE_SIZE = 2 * 1024 * 1024 * 1024
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

def _matching_alias(asset: dict[str, Any], primary: str, legacy: str) -> str:
    primary_present = primary in asset
    legacy_present = legacy in asset
    if primary_present and legacy_present and asset[primary] != asset[legacy]:
        _refuse()
    if primary_present:
        value = asset[primary]
    elif legacy_present:
        value = asset[legacy]
    else:
        _refuse()
    if not isinstance(value, str) or not value:
        _refuse()
    return value

def _validate_source_local_path(value: str, cache_key: str, basename: str) -> None:
    allowed = {
        f"sd://tbot/lesson-assets/{cache_key}/{basename}",
        f"{FIRMWARE_LESSON_ASSET_ROOT}/{cache_key}/{basename}",
    }
    if value not in allowed:
        _refuse()

def _validate_online_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        _refuse()

def _validate_asset_metadata(asset: dict[str, Any], cache_key: str, basename: str) -> tuple[str, str]:
    if _LOWER_SHA256_RE.fullmatch(asset.get("sha256") or "") is None:
        _refuse()
    size = asset.get("size")
    if type(size) is not int or size < 0 or size > MAX_SAFE_SIZE:
        _refuse()
    media_type = asset.get("mediaType")
    if not isinstance(media_type, str) or not media_type.strip() or any(ord(char) < 0x20 for char in media_type):
        _refuse()
    if type(asset.get("critical")) is not bool:
        _refuse()
    local_path = _matching_alias(asset, "sdPath", "localPath")
    online_url = _matching_alias(asset, "onlineUrl", "url")
    _validate_source_local_path(local_path, cache_key, basename)
    _validate_online_url(online_url)
    return local_path, online_url


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
    normalized_assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            _refuse()
        basename = _encoded_basename(asset.get("key"))
        collision_key = basename.lower()
        if collision_key in destinations:
            _refuse()
        destinations.add(collision_key)
        basenames.append(basename)
        normalized_assets.append(_validate_asset_metadata(asset, cache_key, basename))

    result = copy.deepcopy(pack)
    physical_root = f"{FIRMWARE_LESSON_ASSET_ROOT}/{cache_key}"
    result["localRoot"] = physical_root
    for index, basename in enumerate(basenames):
        _source_local_path, online_url = normalized_assets[index]
        physical_path = f"{physical_root}/{basename}"
        result["assets"][index]["localPath"] = physical_path
        result["assets"][index]["sdPath"] = physical_path
        result["assets"][index]["onlineUrl"] = online_url
        result["assets"][index]["url"] = online_url
    return result
