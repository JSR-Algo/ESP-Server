import hashlib
import json
import os
from pathlib import Path

import pytest

from core.lesson.shared_asset_store import SharedAssetStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_equal_sha_is_stored_once_and_reused_across_lesson_packs(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"shared lesson asset"
    digest = _sha(content)

    first = store.put_bytes(content, digest)
    second = store.put_bytes(content, digest)
    pack_a = store.commit_pack("lesson-a/v1-one", {"poster": digest})
    pack_b = store.commit_pack("lesson-b/v2-two", {"poster": digest})

    assert first == second == tmp_path / "tbot/shared-assets/sha256" / digest[:2] / digest
    assert first.read_bytes() == content
    assert os.stat(pack_a / "poster").st_ino == os.stat(first).st_ino
    assert os.stat(pack_b / "poster").st_ino == os.stat(first).st_ino
    assert store.is_pack_ready("lesson-a/v1-one")
    assert store.is_pack_ready("lesson-b/v2-two")


def test_interrupted_atomic_write_leaves_no_visible_asset_and_cleanup_is_safe(tmp_path):
    def fail_before_replace(stage, _path):
        if stage == "before_replace":
            raise RuntimeError("power loss")

    store = SharedAssetStore(tmp_path / "tbot", failure_hook=fail_before_replace)
    content = b"partial"
    digest = _sha(content)

    with pytest.raises(RuntimeError, match="power loss"):
        store.put_bytes(content, digest)

    assert not store.asset_path(digest).exists()
    assert list((tmp_path / "tbot").rglob("*.part"))
    assert store.cleanup_parts() == 1
    assert store.cleanup_parts() == 0


def test_checksum_mismatch_never_commits_asset(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    expected = _sha(b"expected")

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.put_bytes(b"corrupt", expected)

    assert not store.asset_path(expected).exists()
    assert not list((tmp_path / "tbot").rglob("*.part"))


def test_missing_or_corrupt_ready_marker_makes_pack_nonready(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"asset"
    digest = _sha(content)
    store.put_bytes(content, digest)
    pack = store.commit_pack("lesson/v1-sum", {"asset": digest})

    (pack / "READY").unlink()
    assert not store.is_pack_ready("lesson/v1-sum")
    (pack / "READY").write_text("not-json", encoding="utf-8")
    assert not store.is_pack_ready("lesson/v1-sum")


def test_restart_reattests_cas_and_rejects_corrupt_bytes(tmp_path):
    root = tmp_path / "tbot"
    content = b"asset"
    digest = _sha(content)
    store = SharedAssetStore(root)
    asset = store.put_bytes(content, digest)
    store.commit_pack("lesson/v1-sum", {"asset": digest})
    assert store.is_pack_ready("lesson/v1-sum")

    asset.write_bytes(b"tampered")
    restarted = SharedAssetStore(root)

    assert not restarted.is_pack_ready("lesson/v1-sum")
    assert not restarted.attest(digest)


def test_pack_json_then_ready_are_committed_last_atomically(tmp_path):
    events = []

    def record(stage, path):
        events.append((stage, Path(path).name))

    store = SharedAssetStore(tmp_path / "tbot", failure_hook=record)
    data = b"asset"
    digest = _sha(data)
    store.put_bytes(data, digest)
    pack = store.commit_pack("lesson/v1-sum", {"asset": digest})

    replaced = [name for stage, name in events if stage == "after_replace"]
    assert replaced[-2:] == ["pack.json", "READY"]
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert manifest["assets"] == {"asset": digest}
