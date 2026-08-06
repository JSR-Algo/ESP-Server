"""T5.1 — robot half of the byte-for-byte golden-vector parity suite.

The backend half is `tbot-backend/src/lessons/lesson-contract-vectors.spec.ts`
and consumes the SAME file. Both run in their repo's default suite (`npm test`
/ `python -m pytest`), so a divergence is blocking in BOTH CIs.

Sealed container: nothing here re-implements the backend's composition rules in
Python for the sake of the test. Every assertion drives the SHIPPING validators
in `core.lesson.cache_key_contract` against expectations produced by an
independent oracle in the backend's generator script.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

import pytest

from core.lesson.cache_key_contract import (
    CACHE_KEY_MAX_BYTES,
    CACHE_KEY_SLUG_MAX_BYTES,
    CACHE_KEY_VERSION_MAX_DIGITS,
    MAX_ENCODED_BASENAME_BYTES,
    AssetBasenameRefused,
    CacheEvictionRefused,
    compose_asset_sd_path,
    compose_cache_key,
    encode_asset_basename,
    validate_cache_key,
)
from core.lesson.contract_vectors import (
    LESSON_CONTRACT_VECTORS_PATH,
    LESSON_CONTRACT_VECTORS_SHA256,
    ContractVectorsDrift,
    load_lesson_contract_vectors,
)

VECTORS = load_lesson_contract_vectors()

def _find_backend_copy() -> str | None:
    """Locate the backend's own copy in whatever checkout layout we are in.

    Plain clone (TBOT/robot/esp32-server/...) and git-worktree layouts
    (TBOT/robot/esp32-server/.worktrees/<task>/...) put the sibling backend at
    different depths, so walk ancestors instead of counting dirnames.
    """
    override = os.environ.get("TBOT_BACKEND_CONTRACTS_DIR")
    if override:
        candidate = os.path.join(override, "lesson-cache-key.vectors.json")
        return candidate if os.path.exists(candidate) else None
    current = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(
            current, "tbot-backend", "contracts", "lesson-cache-key.vectors.json"
        )
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


BACKEND_COPY = _find_backend_copy()


def _b64(value: str) -> bytes:
    return base64.b64decode(value)


def _ids(vectors):
    return [vector["id"] for vector in vectors]


# ── tamper anchor ────────────────────────────────────────────────────────────


def test_vendored_vectors_hash_to_the_frozen_sha256():
    with open(LESSON_CONTRACT_VECTORS_PATH, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    assert digest == LESSON_CONTRACT_VECTORS_SHA256


def test_loader_refuses_drifted_bytes(tmp_path, monkeypatch):
    tampered = tmp_path / "lesson-cache-key.vectors.json"
    tampered.write_bytes(b"{}\n")
    monkeypatch.setattr(
        "core.lesson.contract_vectors.LESSON_CONTRACT_VECTORS_PATH", str(tampered)
    )
    with pytest.raises(ContractVectorsDrift):
        load_lesson_contract_vectors()


def test_backend_copy_is_byte_identical_when_the_sibling_repo_is_checked_out():
    if BACKEND_COPY is None:
        # Standalone ESP CI cannot see the backend repo; the frozen sha256 above
        # is what makes THAT pipeline blocking. This leg adds the direct
        # byte-comparison wherever both repos are present (dev, gate, T5.3 e2e).
        pytest.skip("backend checkout not present next to this repo")
    with open(BACKEND_COPY, "rb") as handle:
        backend_bytes = handle.read()
    with open(LESSON_CONTRACT_VECTORS_PATH, "rb") as handle:
        vendored_bytes = handle.read()
    assert backend_bytes == vendored_bytes


def test_declared_limits_match_the_shipping_contract():
    limits = VECTORS["limits"]
    assert limits["slugMaxBytes"] == CACHE_KEY_SLUG_MAX_BYTES
    assert limits["cacheKeyMaxBytes"] == CACHE_KEY_MAX_BYTES
    assert limits["versionMaxDigits"] == CACHE_KEY_VERSION_MAX_DIGITS
    assert limits["assetBasenameMaxBytes"] == MAX_ENCODED_BASENAME_BYTES
    assert limits["sdRoot"] == "/sdcard/tbot/lesson-assets"


# ── sha256 digests ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vector", VECTORS["digestVectors"], ids=_ids(VECTORS["digestVectors"])
)
def test_digest_vectors(vector):
    raw = _b64(vector["input"]["bytes_b64"])
    assert len(raw) == vector["expected"]["byte_length"]
    assert hashlib.sha256(raw).hexdigest() == vector["expected"]["sha256"]


# ── cache-key composition ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vector", VECTORS["cacheKeyVectors"], ids=_ids(VECTORS["cacheKeyVectors"])
)
def test_cache_key_vectors(vector):
    manifest_bytes = _b64(vector["input"]["manifest_bytes_b64"])
    checksum = hashlib.sha256(manifest_bytes).hexdigest()
    assert checksum == vector["expected"]["sha256"]

    expected_key = vector["expected"]["cache_key"]
    if expected_key is None:
        with pytest.raises(CacheEvictionRefused):
            compose_cache_key(vector["input"]["slug"], vector["input"]["version"], checksum)
        return
    assert compose_cache_key(
        vector["input"]["slug"], vector["input"]["version"], checksum
    ) == expected_key
    assert validate_cache_key(expected_key) == expected_key
    assert len(expected_key.encode("ascii")) <= CACHE_KEY_MAX_BYTES


@pytest.mark.parametrize(
    "vector", VECTORS["rawKeyVectors"], ids=_ids(VECTORS["rawKeyVectors"])
)
def test_raw_cache_key_vectors(vector):
    raw = vector["input"]["cache_key"]
    if vector["expected"]["error_code"] is None:
        assert validate_cache_key(raw) == raw
    else:
        with pytest.raises(CacheEvictionRefused):
            validate_cache_key(raw)


# ── asset basename / SD path ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vector", VECTORS["assetPathVectors"], ids=_ids(VECTORS["assetPathVectors"])
)
def test_asset_path_vectors(vector):
    asset_key = _b64(vector["input"]["asset_key_b64"]).decode("utf-8")
    if vector["expected"]["error_code"] is not None:
        with pytest.raises(AssetBasenameRefused):
            encode_asset_basename(asset_key)
        return
    assert encode_asset_basename(asset_key) == vector["expected"]["encoded_basename"]
    assert (
        compose_asset_sd_path(vector["input"]["cache_key"], asset_key)
        == vector["expected"]["sd_path"]
    )


def test_asset_vectors_cover_every_media_type_the_pack_builder_emits():
    covered = {vector["input"]["media_type"] for vector in VECTORS["assetPathVectors"]}
    for media_type in (
        "image/png",
        "image/jpeg",
        "video/mp4",
        "application/vnd.tbot.rgb565",
    ):
        assert media_type in covered


# ── profile ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vector", VECTORS["profileVectors"], ids=_ids(VECTORS["profileVectors"])
)
def test_profile_vectors(vector):
    from core.lesson import sd_pack_materializer as materializer

    manifest = {
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 1,
        "profile": vector["input"]["profile"],
        "manifestChecksum": "a" * 64,
        "cacheKey": "w01-d01-barn-say-it/v1-{}".format("a" * 64),
        "assets": [
            {
                "key": "teachingObject.barn",
                "sha256": "b" * 64,
                "size": 1,
                "mediaType": "image/png",
                "critical": True,
                "onlineUrl": "https://cdn.example.com/assets/objects/barn.png",
                "sdPath": "/sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v1-{}/teachingObject.barn".format("a" * 64),
            }
        ],
    }
    config = {
        "lesson": {
            "asset_pack_mount_root": "/tmp/t51-packs",
            "asset_allowed_origins": "https://cdn.example.com",
            "max_file_bytes": 50 * 1024 * 1024,
            "max_pack_bytes": 200 * 1024 * 1024,
        }
    }
    if vector["expected"]["error_code"] is None:
        assert materializer._validate_manifest(manifest, config)["profile"] == "espTft"
    else:
        with pytest.raises(materializer.MaterializationError) as excinfo:
            materializer._validate_manifest(manifest, config)
        assert excinfo.value.code == vector["expected"]["error_code"]


# ── error namespace ──────────────────────────────────────────────────────────


def test_error_envelope_is_the_canonical_code_message_retryable():
    from core.lesson.sd_pack_materializer import MaterializationError

    assert list(VECTORS["envelope"]["fields"]) == ["code", "message", "retryable"]
    response = MaterializationError("INVALID_CACHE_KEY", 400, False, "nope").to_response()
    for field in VECTORS["envelope"]["fields"]:
        assert field in response
    assert response["code"] == "INVALID_CACHE_KEY"
    # Historical key kept so any already-deployed reader keeps working.
    assert response["error"] == response["code"]
    assert response["retryable"] is False


@pytest.mark.parametrize(
    "vector",
    VECTORS["errorNamespaceVectors"],
    ids=[vector["code"] for vector in VECTORS["errorNamespaceVectors"]],
)
def test_error_codes_survive_the_backend_ingress_grammar(vector):
    assert vector["backend_ingress_code"] == vector["code"].lower()
    # Mirrors ERROR_CODE_PATTERN in the backend's lesson-asset-sync controller:
    # a code that fails it is dropped and the terminal failure degrades to a
    # generic retryable one.
    assert re.fullmatch(
        VECTORS["envelope"]["errorCodePattern"], vector["backend_ingress_code"]
    )


def test_every_declared_error_code_is_actually_raisable_by_this_module():
    """The namespace must not advertise codes the materializer cannot emit."""
    import pathlib

    source = pathlib.Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "core", "lesson", "sd_pack_materializer.py")
    ).read_text(encoding="utf-8")
    handler = pathlib.Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "core", "api", "lesson_sd_materialize_handler.py")
    ).read_text(encoding="utf-8")
    for vector in VECTORS["errorNamespaceVectors"]:
        code = vector["code"]
        assert f'"{code}"' in source or f'"{code}"' in handler, code


# ── property fuzz (shared seed) ──────────────────────────────────────────────


def _fuzz_corpus():
    """Regenerates the SAME corpus the backend spec builds from the same LCG."""
    alphabet = _b64(VECTORS["fuzz"]["alphabet_b64"]).decode("utf-8")
    state = 0x5EED1234

    def nxt():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        return state

    corpus = []
    for _ in range(VECTORS["fuzz"]["count"]):
        length = nxt() % 12
        corpus.append("".join(alphabet[nxt() % len(alphabet)] for _ in range(length)))
    return corpus


def test_fuzz_corpus_matches_the_frozen_shared_corpus():
    """Proves both languages fuzz the SAME inputs, not merely the same count."""
    corpus = _fuzz_corpus()
    separator = _b64(VECTORS["fuzz"]["separator_b64"]).decode("utf-8")
    assert len(corpus) == VECTORS["fuzz"]["count"]
    digest = hashlib.sha256(separator.join(corpus).encode("utf-8")).hexdigest()
    assert digest == VECTORS["fuzz"]["corpusSha256"]


def test_fuzz_outcomes_agree_with_typescript():
    corpus = _fuzz_corpus()
    separator = _b64(VECTORS["fuzz"]["separator_b64"]).decode("utf-8")
    outcomes = []
    for value in corpus:
        try:
            outcomes.append("+" + encode_asset_basename(value))
        except AssetBasenameRefused:
            outcomes.append("-")
    digest = hashlib.sha256(separator.join(outcomes).encode("utf-8")).hexdigest()
    assert digest == VECTORS["fuzz"]["outcomeSha256"]
