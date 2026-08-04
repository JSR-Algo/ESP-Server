import copy

import pytest

from core.lesson.flattened_cinematic_contract import trgb_container_bytes
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


def _trgb_cue_pack() -> tuple[dict, dict]:
    render_pack = _pack("flattenedCinematic.barn-listen")
    asset = render_pack["assets"][0]
    asset.update({
        "path": "lessons/derivatives/" + "d" * 64 + "/barn-listen.trgb",
        "url": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/barn-listen.trgb",
        "onlineUrl": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/barn-listen.trgb",
        "mediaType": "application/vnd.tbot.rgb565-indexed",
        "derivativeId": "d" * 64,
        "cueId": "barn-listen",
        "effect": "listen",
        "stepKey": "barn",
        "playbackMode": "loop",
        "size": trgb_container_bytes(13),
        "compatibilityMetadata": {
            "codec": "rgb565le", "containerVersion": 1,
            "width": 480, "height": 320, "storedWidth": 320, "storedHeight": 480,
            "orientation": "panelNativeClockwise", "fps": 10,
            "durationMs": 1300, "frameCount": 13, "frameBytes": 307200,
            "hasAudio": False,
        },
    })
    return render_pack, asset


def test_builds_deep_copied_physical_mcp_pack_without_mutating_render_pack():
    render_pack = _pack()
    render_pack["assets"][0]["sourceUrl"] = "https://assets.example/poster.png"
    before = copy.deepcopy(render_pack)

    mcp_pack = build_firmware_sync_pack(render_pack)

    assert render_pack == before
    assert mcp_pack is not render_pack
    assert mcp_pack["assets"] is not render_pack["assets"]
    assert mcp_pack["localRoot"] == f"{MOUNT_ROOT}/{CACHE_KEY}"
    assert mcp_pack["assets"][0]["sdPath"] == (
        f"{MOUNT_ROOT}/{CACHE_KEY}/backgroundScene.poster"
    )
    assert mcp_pack["assets"][0]["onlineUrl"] == "https://assets.example/poster.png"
    assert "localPath" not in mcp_pack["assets"][0]
    assert "url" not in mcp_pack["assets"][0]
    assert "sourceUrl" not in mcp_pack["assets"][0]
    assert render_pack == before
    assert render_pack["localRoot"].startswith("sd://")
    assert render_pack["assets"][0]["localPath"].startswith("sd://")


def test_percent_encodes_asset_key_for_one_direct_literal_basename():
    render_pack = _pack("folder/poster one.png")

    mcp_pack = build_firmware_sync_pack(render_pack)

    assert mcp_pack["assets"][0]["sdPath"].endswith(
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
    assert valid["assets"][0]["sdPath"].endswith("/poster")


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
    assert "url" not in sent
    assert "localPath" not in sent
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
    assert "url" not in sent
    assert "localPath" not in sent
    assert render_pack == before


def test_static_cached_asset_keeps_local_fallback_url_and_omits_distinct_source_url():
    render_pack = _pack()
    asset = render_pack["assets"][0]
    local_cache_url = "https://esp.example/tbot/lesson-assets/cache/backgroundScene.poster"
    source_url = "https://cdn.example/source/poster.png?sig=private-token"
    asset["url"] = local_cache_url
    asset["onlineUrl"] = local_cache_url
    asset["sourceUrl"] = source_url

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert sent["onlineUrl"] == local_cache_url
    assert "sourceUrl" not in sent
    assert asset["sourceUrl"] == source_url


def test_renderer_v3_mp4_preserves_playback_metadata_and_physical_sd_path_without_credentials():
    render_pack = _pack("scene.opening@v3")
    asset = render_pack["assets"][0]
    asset.update({
        "path": "visuals/scene.opening/v3.mp4",
        "url": "https://assets.example/visuals/scene.opening/v3.mp4",
        "onlineUrl": "https://assets.example/visuals/scene.opening/v3.mp4",
        "mediaType": "video/mp4",
        "sharedAssetKey": "scene.opening",
        "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [
            {"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"},
            {"stepKey": "s1", "phase": "greet", "slot": "backgroundScene.greet"},
        ],
    })
    asset["sdPath"] = f"sd://tbot/lesson-assets/{CACHE_KEY}/scene.opening%40v3"
    asset["localPath"] = asset["sdPath"]

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert sent["mediaType"] == "video/mp4"
    assert sent["sharedAssetKey"] == "scene.opening"
    assert sent["sharedAssetVersion"] == 3
    assert sent["compatibilityMetadata"] == asset["compatibilityMetadata"]
    assert sent["visualRefs"] == asset["visualRefs"]
    assert sent["sdPath"] == f"{MOUNT_ROOT}/{CACHE_KEY}/scene.opening%40v3"
    assert "localPath" not in sent
    assert "authorization" not in {key.lower() for key in sent}
    assert "cookie" not in {key.lower() for key in sent}


@pytest.mark.parametrize("suffix", ["", "?variant=robot&expires=2000000000#opening"])
def test_renderer_v3_mp4_preserves_exact_public_url_with_optional_query_and_fragment(suffix):
    render_pack = _pack("scene.opening@v3")
    asset = render_pack["assets"][0]
    url = "https://assets.example/visuals/scene.opening/v3.mp4" + suffix
    asset.update({
        "url": url,
        "onlineUrl": url,
        "mediaType": "video/mp4",
        "sharedAssetKey": "scene.opening",
        "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
        "sdPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/scene.opening%40v3",
        "localPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/scene.opening%40v3",
    })

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert sent["onlineUrl"] == url
    assert "url" not in sent


def test_rejects_arbitrary_video_that_lacks_validated_renderer_v3_shared_identity():
    render_pack = _pack("authored-video")
    render_pack["assets"][0]["mediaType"] = "video/mp4"

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(render_pack)


def test_renderer_v4_flattened_mp4_preserves_exact_identity_and_physical_path():
    render_pack = _pack("flattenedCinematic.opening")
    asset = render_pack["assets"][0]
    asset.update({
        "url": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
        "onlineUrl": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
        "mediaType": "video/mp4", "derivativeId": "d" * 64, "phaseId": "opening",
        "compatibilityMetadata": {
            "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
            "durationMs": 9000, "frameCount": 90, "hasAudio": False,
        },
    })

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert sent["derivativeId"] == "d" * 64
    assert sent["phaseId"] == "opening"
    assert sent["compatibilityMetadata"]["frameCount"] == 90
    assert sent["sdPath"] == f"{MOUNT_ROOT}/{CACHE_KEY}/flattenedCinematic.opening"
    assert "localPath" not in sent
    assert "url" not in sent


def test_renderer_v4_v2_cue_omits_trgb_compatibility_metadata_from_mcp_only():
    render_pack, asset = _trgb_cue_pack()
    compatibility_metadata = copy.deepcopy(asset["compatibilityMetadata"])

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert {key: sent[key] for key in ("cueId", "effect", "stepKey", "playbackMode")} == {
        "cueId": "barn-listen", "effect": "listen", "stepKey": "barn", "playbackMode": "loop",
    }
    assert sent["mediaType"] == "application/vnd.tbot.rgb565-indexed"
    assert "compatibilityMetadata" not in sent
    assert asset["compatibilityMetadata"] == compatibility_metadata
    assert "localPath" not in sent
    assert "url" not in sent


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effect", []), ("effect", {}), ("effect", None),
        ("cueId", []), ("stepKey", {}), ("playbackMode", []),
    ],
)
def test_renderer_v4_v2_malformed_identity_raises_stable_pack_error(field, value):
    render_pack, asset = _trgb_cue_pack()
    asset[field] = value

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(render_pack)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("containerVersion", 2), ("width", 480.0), ("height", True),
        ("storedWidth", 480), ("storedHeight", 320),
        ("orientation", "landscape"), ("fps", 10.0),
        ("durationMs", 1300.0), ("frameCount", True), ("frameBytes", 153600),
    ],
)
def test_renderer_v4_v2_metadata_requires_exact_integers(field, value):
    render_pack, asset = _trgb_cue_pack()
    asset["compatibilityMetadata"][field] = value

    with pytest.raises(FirmwareSyncPackError):
        build_firmware_sync_pack(render_pack)


@pytest.mark.parametrize("value", [123, [], None, True])
def test_renderer_v4_v2_sha256_requires_an_actual_string(value):
    render_pack, asset = _trgb_cue_pack()
    asset["sha256"] = value

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(render_pack)


def test_renderer_v4_trgb_rejects_mp4_path_with_rgb_media_type():
    render_pack, asset = _trgb_cue_pack()
    asset["path"] = asset["path"].replace(".trgb", ".mp4")
    asset["url"] = asset["url"].replace(".trgb", ".mp4")
    asset["onlineUrl"] = asset["onlineUrl"].replace(".trgb", ".mp4")

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(render_pack)


def test_renderer_v4_trgb_copies_signed_source_url_to_online_url_without_forwarding_source_field():
    render_pack, asset = _trgb_cue_pack()
    signed_source = asset["url"] + "?sig=fixture-token&expires=2000000000"
    asset["sourceUrl"] = signed_source
    asset["url"] = "https://esp.example/tbot/lesson-assets/cache/flattenedCinematic.barn-listen"
    asset["onlineUrl"] = asset["url"]

    sent = build_firmware_sync_pack(render_pack)["assets"][0]

    assert sent["onlineUrl"] == signed_source
    assert "sourceUrl" not in sent
    assert "url" not in sent
    assert asset["sourceUrl"] == signed_source


def test_renderer_v4_trgb_rejects_forwarded_source_url_with_wrong_derivative_path():
    render_pack, asset = _trgb_cue_pack()
    asset["sourceUrl"] = "https://evil.example/other.trgb?sig=private-token"
    asset["url"] = "https://esp.example/tbot/lesson-assets/cache/flattenedCinematic.barn-listen"
    asset["onlineUrl"] = asset["url"]

    with pytest.raises(FirmwareSyncPackError, match="^firmware sync pack invalid$"):
        build_firmware_sync_pack(render_pack)


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
