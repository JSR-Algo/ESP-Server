#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 - <<'PY'
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from core.lesson.asset_cache import AssetState
from core.lesson.sd_pack_materializer import _validate_asset
from core.lesson.sd_pack_mcp_payload import build_firmware_sync_pack
from core.lesson.sd_pack_sync import cached_asset_packs
from core.lesson.shared_asset_store import SharedAssetStore

checksum = "7" * 64
cache_key = f"w02-feelings/v2-{checksum}"

def sd_path(key):
    return f"/sdcard/tbot/lesson-assets/{cache_key}/{quote(key, safe='')}"

def asset(key, content, media_type, slot, metadata):
    url = f"https://assets.example/{quote(key, safe='')}"
    return {
        "key": key,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "mediaType": media_type,
        "critical": True,
        "onlineUrl": url,
        "url": url,
        "sdPath": sd_path(key),
        "localPath": sd_path(key),
        "sharedAssetKey": key.removesuffix("@v1"),
        "sharedAssetVersion": 1,
        "compatibilityMetadata": metadata,
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": slot}],
    }

background = asset(
    "scene.t54.farm@v1", b"background", "image/jpeg", "backgroundScene",
    {"mediaKind": "image", "mediaType": "image/jpeg", "width": 480, "height": 320,
     "rect": {"x": 0, "y": 0, "width": 480, "height": 320}, "fit": "cover"},
)
robot = asset(
    "robot.t54.flyIn@v1", b"robot", "video/mp4", "robotOverlay",
    {"mediaKind": "video", "mediaType": "video/mp4", "codec": "mjpeg",
     "hasAudio": False, "width": 200, "height": 200, "fps": 10,
     "durationMs": 1000, "frameCount": 10,
     "rect": {"x": 20, "y": 100, "width": 200, "height": 200},
     "chromaKey": {"keyColor": "#00ff00", "tolerance": 24, "featherPx": 1}},
)

background = _validate_asset(
    background, cache_key, 1024, {"https://assets.example"}, False
)
robot = _validate_asset(
    robot, cache_key, 1024, {"https://assets.example"}, False
)
assert background["compatibilityMetadata"]["mediaKind"] == "image"

pack = {
    "lessonId": "w02-feelings", "lessonVersion": 2, "profile": "espTft",
    "manifestChecksum": checksum, "cacheKey": cache_key, "ready": True,
    "localRoot": f"sd://tbot/lesson-assets/{cache_key}", "assets": [background, robot],
}
sent = build_firmware_sync_pack(pack)
assert len(sent["assets"]) == 2
assert all("compatibilityMetadata" not in item for item in sent["assets"])

state_input = dict(robot)
state_input.update({"path": "wrong-origin-relative.mp4", "layer": "robotOverlay", "role": "flyIn"})
assert AssetState(state_input).renderer_v5_media is True

with TemporaryDirectory(prefix="t54-v5-materializer-gate-") as root:
    mount = Path(root) / "lesson-assets"
    store = SharedAssetStore(Path(root), pack_root=mount)
    digests = {}
    for item, content in ((background, b"background"), (robot, b"robot")):
        store.put_bytes(content, item["sha256"])
        digests[item["key"]] = item["sha256"]
    store.commit_pack(cache_key, digests, manifest=pack)
    cached = list(cached_asset_packs({"lesson": {"asset_pack_mount_root": str(mount)}}))
    assert len(cached) == 1
    assert {item["compatibilityMetadata"]["mediaKind"] for item in cached[0]["assets"]} == {
        "image", "video",
    }
PY
