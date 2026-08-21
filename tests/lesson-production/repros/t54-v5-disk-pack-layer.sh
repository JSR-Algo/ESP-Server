#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

probe="$(mktemp "${TMPDIR:-/tmp}/t54-v5-disk-pack-layer.XXXXXX.py")"
trap 'rm -f "$probe"' EXIT

cat >"$probe" <<'PY'
from core.lesson.layered_cinematic_contract import (
    validate_layered_cinematic_generation_asset,
)


def asset(slot: str, media_type: str, metadata: dict) -> dict:
    return {
        "key": f"asset.{slot}@v1",
        "sharedAssetKey": f"asset.{slot}",
        "sharedAssetVersion": 1,
        "mediaType": media_type,
        "compatibilityMetadata": metadata,
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": slot}],
    }


cases = (
    (
        "backgroundScene",
        "image/jpeg",
        "background",
        {
            "mediaKind": "image",
            "mediaType": "image/jpeg",
            "width": 480,
            "height": 320,
            "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "fit": "cover",
        },
    ),
    (
        "teachingObject",
        "image/png",
        "teachingObject",
        {
            "mediaKind": "image",
            "mediaType": "image/png",
            "width": 192,
            "height": 192,
            "rect": {"x": 130, "y": 72, "width": 200, "height": 200},
            "fit": "contain",
        },
    ),
    (
        "robotOverlay",
        "video/mp4",
        "robotOverlay",
        {
            "mediaKind": "video",
            "mediaType": "video/mp4",
            "codec": "mjpeg",
            "hasAudio": False,
            "width": 240,
            "height": 240,
            "fps": 10,
            "durationMs": 3000,
            "frameCount": 30,
            "rect": {"x": 240, "y": 80, "width": 220, "height": 220},
            "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
        },
    ),
)

for slot, media_type, expected_layer, metadata in cases:
    normalized = validate_layered_cinematic_generation_asset(
        asset(slot, media_type, metadata)
    )
    assert normalized.get("layer") == expected_layer, (
        f"disk-reloaded renderer-v5 asset lost layer: slot={slot} "
        f"actual={normalized.get('layer')!r} expected={expected_layer!r}"
    )

print("T54 renderer-v5 disk-pack layer recovery: PASS")
PY

if [ -n "${TBOT_GATE_PYTHON:-}" ]; then
  python_bin="$TBOT_GATE_PYTHON"
else
  python_bin="${TBOT_REPRO_PYTHON:-python3}"
  if [ ! -x "$python_bin" ]; then
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$common_dir" ]; then
      python_bin="$(dirname "$common_dir")/main/tbot-server/.venv311/bin/python"
    fi
  fi
  if [ ! -x "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
fi

if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found for the T54 v5 layer probe" >&2
  exit 2
fi

PYTHONPATH="$PWD/main/tbot-server" "$python_bin" "$probe"
