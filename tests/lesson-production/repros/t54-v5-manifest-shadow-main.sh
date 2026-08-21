#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 - <<'PY'
from copy import deepcopy

from core.lesson.asset_cache import AssetCache, AssetState
from core.lesson.runtime import _manifest_asset_cache_inputs
from core.lesson.sd_pack_mcp_payload import build_firmware_sync_pack


def phase(effect: str, index: int) -> dict:
    return {
        "templateId": "layeredCinematic",
        "templateVersion": 1,
        "phaseId": effect,
        "timing": {"durationMs": 1000},
        "playbackMode": "once",
        "layers": [
            {
                "layer": "background",
                "slot": "backgroundScene",
                "assetVersionId": "background.classroom@v1",
                "assetKey": "background.classroom",
                "version": 1,
                "sha256": "a" * 64,
                "bytes": 1000,
                "metadata": {
                    "mediaKind": "image",
                    "mediaType": "image/jpeg",
                    "width": 480,
                    "height": 320,
                    "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
                    "fit": "cover",
                },
            },
            {
                "layer": "teachingObject",
                "slot": "teachingObject",
                "assetVersionId": "object.happy@v1",
                "assetKey": "object.happy",
                "version": 1,
                "sha256": "b" * 64,
                "bytes": 2000,
                "metadata": {
                    "mediaKind": "image",
                    "mediaType": "image/png",
                    "width": 240,
                    "height": 240,
                    "rect": {"x": 130, "y": 72, "width": 200, "height": 200},
                    "fit": "contain",
                },
            },
            {
                "layer": "robotOverlay",
                "slot": "robotOverlay",
                "assetVersionId": f"robot.{effect}@v1",
                "assetKey": f"robot.{effect}",
                "version": 1,
                "sha256": f"{index:x}" * 64,
                "bytes": 3000,
                "metadata": {
                    "mediaKind": "video",
                    "mediaType": "video/mp4",
                    "codec": "mjpeg",
                    "hasAudio": False,
                    "width": 240,
                    "height": 240,
                    "fps": 10,
                    "durationMs": 1000,
                    "frameCount": 10,
                    "rect": {"x": 160, "y": 64, "width": 220, "height": 220},
                    "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
                },
            },
        ],
    }


effects = ("flyIn", "walk", "teach", "listen", "thinking", "celebrate", "exit")
phases = [phase(effect, index) for index, effect in enumerate(effects, start=1)]
unique_layers = {
    layer["assetVersionId"]: layer
    for item in phases
    for layer in item["layers"]
}
generic_assets = [
    {
        "id": layer["assetVersionId"],
        "path": f"https://assets.test/{layer['assetVersionId']}",
        "url": f"https://assets.test/{layer['assetVersionId']}",
        "sha256": layer["sha256"],
        "bytes": layer["bytes"],
        "critical": True,
        "layer": layer["slot"],
        "role": "pose",
        "mediaType": layer["metadata"]["mediaType"],
    }
    for layer in unique_layers.values()
]
projected = _manifest_asset_cache_inputs({
    "manifestVersion": "teebot-lesson-renderer.v5",
    "assets": generic_assets,
    "cinematicPhases": phases,
})
assert len(projected) == 9, f"renderer-v5 projection retained asset shadows: {len(projected)}"
assert all(AssetState(asset).renderer_v5_media for asset in projected)
AssetCache(assets=projected, profile="espTft").assert_profile_renderable()

cache_key = "w02-feelings/v6-" + "f" * 64
robot = deepcopy(next(asset for asset in projected if asset["layer"] == "robotOverlay"))
robot.update({
    "state": "READY",
    "checksumOk": True,
    "onlineUrl": robot["url"],
    "sdPath": f"sd://tbot/lesson-assets/{cache_key}/robot.flyIn%40v1",
    "localPath": f"sd://tbot/lesson-assets/{cache_key}/robot.flyIn%40v1",
})
pack = {
    "lessonId": "w02-feelings",
    "lessonVersion": 6,
    "profile": "espTft",
    "manifestChecksum": "f" * 64,
    "cacheKey": cache_key,
    "ready": True,
    "localRoot": f"sd://tbot/lesson-assets/{cache_key}",
    "assets": [robot],
}
sent = build_firmware_sync_pack(pack)
assert len(sent["assets"]) == 1
assert sent["assets"][0]["mediaType"] == "video/mp4"
print("T54 renderer-v5 manifest shadow: PASS")
PY
