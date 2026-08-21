#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 - <<'PY'
from core.lesson.asset_cache import AssetState
from core.lesson.runtime import _manifest_asset_cache_inputs

phase = {
    "templateId": "layeredCinematic",
    "templateVersion": 1,
    "phaseId": "flyIn",
    "timing": {"durationMs": 1000},
    "playbackMode": "once",
    "layers": [{
        "layer": "robotOverlay",
        "slot": "robotOverlay",
        "assetVersionId": "robot.t54.flyIn@v1",
        "assetKey": "robot.t54.flyIn",
        "version": 1,
        "sha256": "c" * 64,
        "bytes": 1000,
        "metadata": {
            "mediaKind": "video", "mediaType": "video/mp4", "codec": "mjpeg",
            "hasAudio": False, "width": 200, "height": 200, "fps": 10,
            "durationMs": 1000, "frameCount": 10,
            "rect": {"x": 20, "y": 100, "width": 200, "height": 200},
            "chromaKey": {"keyColor": "#00ff00", "tolerance": 24, "featherPx": 1},
        },
    }],
}
assets = _manifest_asset_cache_inputs({
    "manifestVersion": "teebot-lesson-renderer.v5",
    "assets": [],
    "cinematicPhases": [phase],
})
assert len(assets) == 1
assert "visualRefs" not in assets[0]
assert AssetState(assets[0]).renderer_v5_media is True
PY
