#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 - <<'PY'
from core.lesson.runtime import _manifest_asset_cache_inputs


def layer(slot: str, asset_id: str, media_type: str, sha: str) -> dict:
    metadata = {
        "mediaKind": "video" if slot == "robotOverlay" else "image",
        "mediaType": media_type,
        "width": 240 if slot != "backgroundScene" else 480,
        "height": 240 if slot != "backgroundScene" else 320,
        "rect": {"x": 0, "y": 0, "width": 220, "height": 220},
    }
    if slot == "backgroundScene":
        metadata.update({"fit": "cover"})
    elif slot == "teachingObject":
        metadata.update({"fit": "contain"})
    else:
        metadata.update({
            "codec": "mjpeg", "hasAudio": False, "fps": 10,
            "durationMs": 1000, "frameCount": 10,
            "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
        })
    return {
        "layer": "background" if slot == "backgroundScene" else slot,
        "slot": slot,
        "assetVersionId": asset_id,
        "assetKey": asset_id.removesuffix("@v1"),
        "version": 1,
        "sha256": sha * 64,
        "bytes": 1000,
        "metadata": metadata,
    }


effects = ("flyIn", "walk", "teach", "listen", "thinking", "celebrate", "exit")
phases = []
for index, effect in enumerate(effects, start=1):
    phases.append({
        "templateId": "layeredCinematic",
        "templateVersion": 1,
        "phaseId": effect,
        "timing": {"durationMs": 1000},
        "playbackMode": "once",
        "layers": [
            layer("backgroundScene", "background.classroom@v1", "image/jpeg", "a"),
            layer("teachingObject", "object.happy@v1", "image/png", "b"),
            layer("robotOverlay", f"robot.{effect}@v1", "video/mp4", f"{index:x}"),
        ],
    })

generic = [{
    "id": item["assetVersionId"],
    "path": f"https://assets.test/{item['assetVersionId']}",
    "url": f"https://assets.test/{item['assetVersionId']}",
    "sha256": item["sha256"],
    "bytes": item["bytes"],
    "critical": True,
    "layer": item["slot"],
    "role": "pose",
    "mediaType": item["metadata"]["mediaType"],
} for item in {entry["assetVersionId"]: entry for phase in phases for entry in phase["layers"]}.values()]
generic.extend({
    "id": f"robotOverlay.{pose}",
    "path": f"https://assets.test/robotOverlay.{pose}",
    "url": f"https://assets.test/robotOverlay.{pose}",
    "sha256": "f" * 64,
    "bytes": 1000,
    "critical": False,
    "layer": "robotOverlay",
    "role": "pose",
    "mediaType": "image/png",
} for pose in ("teach", "listening", "thinking", "celebrate"))

projected = _manifest_asset_cache_inputs({
    "manifestVersion": "teebot-lesson-renderer.v5",
    "assets": generic,
    "cinematicPhases": phases,
})
keys = [asset["key"] for asset in projected]
assert len(keys) == 9, f"renderer-v5 retained non-phase assets: {keys}"
assert not any(key.startswith("robotOverlay.") for key in keys), keys
print("T54 renderer-v5 phase membership: PASS")
PY
