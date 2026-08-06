"""Canonical validation contract for one firmware lesson cache key.

THE Python half of the backend <-> ESP lesson cache-key contract. The
TypeScript half is ``src/lessons/lesson-cache-key.contract.ts`` in tbot-backend.
Both halves are pinned to the SAME golden vectors
(``contracts/lesson-cache-key.vectors.json``, vendored byte-for-byte from the
backend repo) and each repo's CI asserts that file's sha256 against a frozen
constant, so a one-sided edit is RED before it can ship (T5.1).

Sealed container: the backend is the only *producer* of a cache key / SD path.
Everything here parses, validates, or re-derives-for-comparison; nothing in the
runtime request path may invent a key of its own.
"""

import re
from typing import Any
from urllib.parse import quote

CACHE_KEY_RE = re.compile(
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"v(?P<version>[1-9][0-9]*)-(?P<checksum>[0-9a-f]{64})"
)
CACHE_KEY_MAX_BYTES = 205
CACHE_KEY_SLUG_MAX_BYTES = 128
CACHE_KEY_VERSION_MAX_DIGITS = 10

FIRMWARE_LESSON_ASSET_ROOT = "/sdcard/tbot/lesson-assets"

# Mirrors LESSON_ASSET_BASENAME_MAX_BYTES in lesson-cache-key.contract.ts.
# NOT 255: SharedAssetStore appends a ".<pid>.<uuid>.part" atomic temp suffix to
# the encoded name, which has to stay inside the FAT 255-byte component limit.
MAX_ENCODED_BASENAME_BYTES = 200

# Names the pack directory already owns. "pack.json" and "READY" are written by
# SharedAssetStore.commit_pack AFTER the asset hard links, so an asset encoding
# to either name is silently overwritten by the store's own control file and the
# pack can then never verify READY.
RESERVED_ASSET_BASENAMES = frozenset(
    {
        ".",
        "..",
        "pack.json",
        "ready",
        "current.json",
        "pvg.json",
        "activation.json",
        "lesson-pack-activation.json",
    }
)
RESERVED_ASSET_SUFFIXES = (".tmp", ".download", ".part", ".backup")
FAT_FORBIDDEN_RE = re.compile("[<>:\"/\\\\|?*\x00-\x1f]")
FAT_RESERVED_DEVICE_BASENAME_RE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)"
)
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


class CacheEvictionRefused(RuntimeError):  # noqa: N818 - shared firmware contract name
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AssetBasenameRefused(RuntimeError):  # noqa: N818 - shared firmware contract name
    """An asset key that cannot become one canonical FAT-safe pack basename."""

    def __init__(self, code: str = "invalid_asset_key"):
        super().__init__(code)
        self.code = code


def validate_cache_key(value: Any) -> str:
    if not isinstance(value, str):
        raise CacheEvictionRefused("invalid_cache_key")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise CacheEvictionRefused("invalid_cache_key") from None
    if not encoded or len(encoded) > CACHE_KEY_MAX_BYTES:
        raise CacheEvictionRefused("invalid_cache_key")
    match = CACHE_KEY_RE.fullmatch(value)
    if match is None:
        raise CacheEvictionRefused("invalid_cache_key")
    if (
        len(match.group("slug")) > CACHE_KEY_SLUG_MAX_BYTES
        or len(match.group("version")) > CACHE_KEY_VERSION_MAX_DIGITS
    ):
        raise CacheEvictionRefused("invalid_cache_key")
    reconstructed = "{}/v{}-{}".format(
        match.group("slug"),
        match.group("version"),
        match.group("checksum"),
    )
    if reconstructed != value:
        raise CacheEvictionRefused("invalid_cache_key")
    return reconstructed


def compose_cache_key(lesson_key: Any, lesson_version: Any, manifest_checksum: Any) -> str:
    """Re-derive the canonical key FOR COMPARISON with a backend-supplied one.

    This is the single Python composition formula (matches
    ``composeLessonCacheKey`` in TS). It is deliberately validating, never
    sanitizing: a component that cannot form a canonical key raises rather than
    being coerced into one.
    """
    if not isinstance(lesson_key, str):
        raise CacheEvictionRefused("invalid_cache_key")
    if type(lesson_version) is not int or lesson_version <= 0:
        raise CacheEvictionRefused("invalid_cache_key")
    if not isinstance(manifest_checksum, str) or not _HEX64_RE.fullmatch(manifest_checksum):
        raise CacheEvictionRefused("invalid_cache_key")
    return validate_cache_key(
        "{}/v{}-{}".format(lesson_key, lesson_version, manifest_checksum)
    )


def encode_asset_basename(asset_key: Any) -> str:
    """THE canonical pack basename for one asset key.

    Equivalent to ``encodeLessonAssetBasename`` in lesson-cache-key.contract.ts:
    RFC 3986 strict percent-encoding (``quote(value, safe="")``) plus the
    FAT/reserved-name rules the SD card and the pack store impose. Note the TS
    side must NOT use bare ``encodeURIComponent`` -- it leaves ``!'()*`` literal
    while this leaves none of them, and the pack file is written under THIS
    name (SharedAssetStore._pack_asset_name).
    """
    if not isinstance(asset_key, str) or not asset_key:
        raise AssetBasenameRefused()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in asset_key):
        raise AssetBasenameRefused()
    try:
        encoded = quote(asset_key, safe="")
        encoded_bytes = encoded.encode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError):
        raise AssetBasenameRefused() from None
    if not encoded_bytes or len(encoded_bytes) > MAX_ENCODED_BASENAME_BYTES:
        raise AssetBasenameRefused()
    if FAT_FORBIDDEN_RE.search(encoded) or encoded.endswith("."):
        raise AssetBasenameRefused()
    lowered = encoded.lower()
    if (
        lowered in RESERVED_ASSET_BASENAMES
        or FAT_RESERVED_DEVICE_BASENAME_RE.match(lowered)
        or lowered.endswith(RESERVED_ASSET_SUFFIXES)
    ):
        raise AssetBasenameRefused()
    return encoded


def compose_asset_sd_path(cache_key: str, asset_key: Any) -> str:
    """Re-derive the firmware SD path FOR COMPARISON with the backend's."""
    return "{}/{}/{}".format(
        FIRMWARE_LESSON_ASSET_ROOT,
        validate_cache_key(cache_key),
        encode_asset_basename(asset_key),
    )
