#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T5.1 (ESP half) — the robot had FOUR mutually inconsistent copies of the
# backend<->ESP cache-key / asset-basename contract. This repro asserts they are
# ONE, using only the shipping modules; it imports nothing the fix introduced,
# so it tests the BUG, not the patch.
#
#   1. global_generation_poller encoded asset keys with
#      quote(key, safe="-_.!~*'()") — JavaScript encodeURIComponent semantics —
#      while sd_pack_materializer, sd_pack_sync and
#      SharedAssetStore._pack_asset_name all use quote(key, safe=""). The store
#      writes the file under the STRICT name, so the two ESP ingress paths
#      disagreed on every key containing ! ' ( ) *.
#   2. global_generation_poller capped the cache key at 200 bytes while the
#      canonical cap is 205 — a maximal-but-legal key was rejected there as
#      cms_cache_key_too_long and accepted by the materializer.
#   3. sd_pack_evict carried a byte-for-byte COPY of validate_cache_key with its
#      OWN CacheEvictionRefused class, so `except CacheEvictionRefused` in
#      sd_pack_mcp_payload (which imports the evict copy) could not catch a
#      raise from the cache_key_contract copy.
#   4. sd_pack_materializer accepted asset keys that encode to "pack.json" or
#      "READY" — the two control files SharedAssetStore.commit_pack writes AFTER
#      hard-linking assets, so such an asset is silently overwritten and the
#      pack can never verify READY.
set -euo pipefail

REPO="$TBOT_REPRO_REPO_ROOT"
cd "$(pwd)/main/tbot-server"

cat > tests/__t51_repro.py <<'PY'
"""T5.1 repro — one cache-key/basename contract across every ESP ingress path."""

import importlib
from urllib.parse import quote

import pytest

CONTRACT = importlib.import_module("core.lesson.cache_key_contract")
EVICT = importlib.import_module("core.lesson.sd_pack_evict")
POLLER = importlib.import_module("core.lesson.global_generation_poller")
STORE = importlib.import_module("core.lesson.shared_asset_store")
MATERIALIZER = importlib.import_module("core.lesson.sd_pack_materializer")

HEX = "a" * 64
CACHE_KEY = "w01-d01-barn-say-it/v1-{}".format(HEX)
CONFIG = {
    "lesson": {
        "asset_pack_mount_root": "/tmp/t51-repro-packs",
        "asset_allowed_origins": "https://cdn.example.com",
        "max_file_bytes": 50 * 1024 * 1024,
        "max_pack_bytes": 200 * 1024 * 1024,
    }
}


def manifest(asset_key, sd_path=None):
    encoded = quote(asset_key, safe="") if sd_path is None else sd_path
    return {
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 1,
        "profile": "espTft",
        "manifestChecksum": HEX,
        "cacheKey": CACHE_KEY,
        "assets": [
            {
                "key": asset_key,
                "sha256": "b" * 64,
                "size": 1024,
                "mediaType": "image/png",
                "critical": True,
                "onlineUrl": "https://cdn.example.com/assets/objects/barn.png",
                "sdPath": "/sdcard/tbot/lesson-assets/{}/{}".format(CACHE_KEY, encoded),
            }
        ],
    }


@pytest.mark.parametrize("asset_key", ["robot'pose", "robot(1)", "robot!bang", "robot*star"])
def test_every_ingress_path_uses_the_same_strict_encoding(asset_key):
    """The pack store's on-disk name is quote(key, safe='') — everything must match it."""
    on_disk = STORE.SharedAssetStore._pack_asset_name(asset_key)
    assert on_disk == quote(asset_key, safe="")

    # The CMS poller must expect the SAME basename the store will write.
    poller_encoded = quote(asset_key, safe="-_.!~*'()")
    assert poller_encoded == on_disk or _poller_rejects_the_loose_form(asset_key), (
        "global_generation_poller still encodes with encodeURIComponent semantics: "
        "{!r} vs on-disk {!r}".format(poller_encoded, on_disk)
    )

    # The materializer must accept the strict form the store writes.
    normalized = MATERIALIZER._validate_manifest(manifest(asset_key), CONFIG)
    assert normalized["assets"][0]["sdPath"].endswith("/" + on_disk)


def _poller_rejects_the_loose_form(asset_key):
    """True once the poller no longer keeps ! ' ( ) * literal."""
    encoder = getattr(POLLER, "encode_asset_basename", None)
    if encoder is None:
        return False
    return encoder(asset_key) == quote(asset_key, safe="")


def test_cache_key_byte_cap_is_the_same_everywhere():
    assert POLLER.MAX_CACHE_KEY_BYTES == CONTRACT.CACHE_KEY_MAX_BYTES == 205
    assert EVICT.CACHE_KEY_MAX_BYTES == CONTRACT.CACHE_KEY_MAX_BYTES
    maximal = "{}/v1234567890-{}".format("a" * 128, HEX)
    assert len(maximal.encode("ascii")) == 205
    assert CONTRACT.validate_cache_key(maximal) == maximal
    assert len(maximal.encode("ascii")) <= POLLER.MAX_CACHE_KEY_BYTES


def test_encoded_basename_cap_is_the_same_everywhere():
    assert POLLER.MAX_ENCODED_BASENAME_BYTES == CONTRACT.MAX_ENCODED_BASENAME_BYTES == 200
    # ...and the materializer APPLIES that cap, not a local 255.
    accepted = MATERIALIZER._validate_manifest(manifest("a" * 200), CONFIG)
    assert accepted["assets"][0]["key"] == "a" * 200


def test_one_cache_eviction_refused_class():
    assert EVICT.CacheEvictionRefused is CONTRACT.CacheEvictionRefused
    assert EVICT.validate_cache_key is CONTRACT.validate_cache_key


@pytest.mark.parametrize("asset_key", ["pack.json", "READY", "current.json", "a" * 201])
def test_materializer_refuses_names_the_pack_store_owns(asset_key):
    with pytest.raises(MATERIALIZER.MaterializationError) as excinfo:
        MATERIALIZER._validate_manifest(manifest(asset_key), CONFIG)
    assert excinfo.value.code == "INVALID_ASSET_KEY"


def test_error_envelope_carries_the_canonical_code_field():
    body = MATERIALIZER.MaterializationError("INVALID_CACHE_KEY", 400, False, "nope").to_response()
    assert body["code"] == "INVALID_CACHE_KEY"
    assert body["retryable"] is False
    assert body["message"] == "nope"
PY

trap 'rm -f tests/__t51_repro.py' EXIT
python3 -m pytest -q --no-header -p no:cacheprovider tests/__t51_repro.py

echo "REPRO PASS: T5.1 ESP cache-key/basename contract is single-sourced and consistent."
