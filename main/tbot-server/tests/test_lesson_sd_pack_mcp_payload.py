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
                "onlineUrl": "https://assets.example/poster.png",
                "sha256": "a" * 64,
                "size": 100,
                "mediaType": "image/png",
                "critical": True,
                "state": "READY",
                "checksumOk": True,
                "sdPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/{encoded}",
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
    assert mcp_pack["assets"][0]["sdPath"] == mcp_pack["assets"][0]["localPath"]
    assert mcp_pack["assets"][0]["onlineUrl"] == "https://assets.example/poster.png"
    assert mcp_pack["assets"][0]["url"] == "https://assets.example/poster.png"
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

def test_normalizes_new_only_aliases_and_preserves_signed_url_exactly():
    render_pack = _pack()
    asset = render_pack["assets"][0]
    asset["onlineUrl"] = "https://assets.example/poster.png?sig=secret&expires=1"
    asset["sdPath"] = asset.pop("localPath")
    asset.pop("url")

    mcp_pack = build_firmware_sync_pack(render_pack)

    sent = mcp_pack["assets"][0]
    assert sent["onlineUrl"] == "https://assets.example/poster.png?sig=secret&expires=1"
    assert sent["url"] == sent["onlineUrl"]
    assert sent["sdPath"] == sent["localPath"]
    assert render_pack["assets"][0]["onlineUrl"].endswith("expires=1")
    assert "url" not in render_pack["assets"][0]

def test_normalizes_legacy_only_aliases_without_mutating_input():
    render_pack = _pack()
    render_pack["assets"][0].pop("onlineUrl")
    render_pack["assets"][0].pop("sdPath")
    before = copy.deepcopy(render_pack)

    mcp_pack = build_firmware_sync_pack(render_pack)

    sent = mcp_pack["assets"][0]
    assert sent["onlineUrl"] == before["assets"][0]["url"]
    assert sent["url"] == before["assets"][0]["url"]
    assert sent["sdPath"] == sent["localPath"]
    assert render_pack == before

@pytest.mark.parametrize(
    ("mutate", "secret"),
    [
        (lambda asset: asset.update({"sdPath": asset["localPath"] + ".changed"}), False),
        (lambda asset: asset.update({"onlineUrl": asset["url"] + "?sig=private-token"}), True),
        (lambda asset: (asset.pop("sdPath"), asset.pop("localPath")), False),
        (lambda asset: (asset.pop("onlineUrl"), asset.pop("url")), False),
        (lambda asset: asset.update({"sdPath": "sd://tbot/lesson-assets/bad"}), False),
        (lambda asset: asset.update({"onlineUrl": "ftp://assets.example/poster.png"}), False),
        (lambda asset: asset.update({"sha256": "a" * 63}), False),
        (lambda asset: asset.update({"size": -1}), False),
        (lambda asset: asset.update({"critical": "true"}), False),
        (lambda asset: asset.update({"mediaType": ""}), False),
    ],
)
def test_rejects_invalid_asset_metadata_before_mcp_copy(mutate, secret):
    render_pack = _pack()
    mutate(render_pack["assets"][0])

    with pytest.raises(FirmwareSyncPackError) as exc_info:
        build_firmware_sync_pack(render_pack)

    assert str(exc_info.value) == "firmware sync pack invalid"
    if secret:
        assert "private-token" not in str(exc_info.value)
