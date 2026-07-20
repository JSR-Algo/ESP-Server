import copy

import pytest

from core.lesson.sd_pack_mcp_payload import (
    FIRMWARE_LESSON_ASSET_ROOT,
    FirmwareSyncPackError,
    build_firmware_sync_pack,
)

CHECKSUM = "0123456789abcdef" * 4
CACHE_KEY = f"pip-farm-3m/v1-{CHECKSUM}"
MOUNT_ROOT = "/sdcard/tbot/lesson-assets"


def _pack(key="backgroundScene.poster"):
    encoded = key.replace("/", "%2F").replace(" ", "%20")
    return {
        "assignmentVersion": 7,
        "lessonId": "pip-farm-3m",
        "lessonVersion": 1,
        "manifestChecksum": CHECKSUM,
        "cacheKey": CACHE_KEY,
        "localRoot": f"sd://tbot/lesson-assets/{CACHE_KEY}",
        "ready": True,
        "assets": [
            {
                "key": key,
                "path": "assets/poster.png",
                "url": "https://assets.example/poster.png",
                "sha256": "a" * 64,
                "size": 100,
                "critical": True,
                "state": "READY",
                "checksumOk": True,
                "localPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/{encoded}",
            }
        ],
    }


def test_builds_deep_copied_physical_mcp_pack_without_mutating_render_pack():
    render_pack = _pack()
    before = copy.deepcopy(render_pack)

    mcp_pack = build_firmware_sync_pack(render_pack)

    assert render_pack == before
    assert mcp_pack is not render_pack
    assert mcp_pack["assets"] is not render_pack["assets"]
    assert mcp_pack["localRoot"] == f"{MOUNT_ROOT}/{CACHE_KEY}"
    assert mcp_pack["assets"][0]["localPath"] == (
        f"{MOUNT_ROOT}/{CACHE_KEY}/backgroundScene.poster"
    )
    assert render_pack["localRoot"].startswith("sd://")
    assert render_pack["assets"][0]["localPath"].startswith("sd://")


def test_percent_encodes_asset_key_for_one_direct_literal_basename():
    render_pack = _pack("folder/poster one.png")

    mcp_pack = build_firmware_sync_pack(render_pack)

    assert mcp_pack["assets"][0]["localPath"].endswith(
        "/folder%2Fposter%20one.png"
    )


@pytest.mark.parametrize(
    "pack",
    [
        {**_pack(), "cacheKey": "pip-farm-3m/v1-short"},
        {**_pack(), "assets": []},
        {**_pack(), "assets": _pack()["assets"] * 65},
    ],
)
def test_rejects_noncanonical_or_unbounded_mcp_transform(pack):
    with pytest.raises(FirmwareSyncPackError):
        build_firmware_sync_pack(pack)


def test_rejects_unencodable_or_firmware_reserved_asset_keys_stably():
    for key in (
        "\ud800",
        ".",
        "..",
        "CURRENT.JSON",
        "poster.jpg.TMP",
        "poster.backup",
        "poster.BACKUP",
        "poster.",
        "poster..",
    ):
        with pytest.raises(FirmwareSyncPackError):
            build_firmware_sync_pack(_pack(key))


@pytest.mark.parametrize(
    "keys",
    [
        ("poster", "poster.backup"),
        ("poster.backup", "poster"),
    ],
)
def test_rejects_backup_namespace_pair_in_both_orders_without_poisoning_next_transform(keys):
    pack = _pack(keys[0])
    second = copy.deepcopy(pack["assets"][0])
    second["key"] = keys[1]
    pack["assets"].append(second)

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(pack)

    valid = build_firmware_sync_pack(_pack("poster"))
    assert valid["assets"][0]["localPath"].endswith("/poster")


def test_rejects_fat_trailing_dot_alias_pair():
    pack = _pack("poster")
    alias = copy.deepcopy(pack["assets"][0])
    alias["key"] = "poster."
    pack["assets"].append(alias)

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(pack)


def test_robot_target_root_is_fixed_and_not_a_server_materialization_input():
    mcp_pack = build_firmware_sync_pack(_pack())

    assert mcp_pack["localRoot"].startswith(FIRMWARE_LESSON_ASSET_ROOT + "/")
    with pytest.raises(TypeError):
        build_firmware_sync_pack(_pack(), "/opt/tbot-esp32-server/data/lesson-packs")
