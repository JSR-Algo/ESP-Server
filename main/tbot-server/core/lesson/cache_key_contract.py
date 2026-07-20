"""Canonical validation contract for one firmware lesson cache key."""

import re
from typing import Any

CACHE_KEY_RE = re.compile(
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"v(?P<version>[1-9][0-9]*)-(?P<checksum>[0-9a-f]{64})"
)
CACHE_KEY_MAX_BYTES = 205
CACHE_KEY_SLUG_MAX_BYTES = 128
CACHE_KEY_VERSION_MAX_DIGITS = 10


class CacheEvictionRefused(RuntimeError):  # noqa: N818 - shared firmware contract name
    def __init__(self, code: str):
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
