#!/usr/bin/env bash
# Apply TBOT local-lab server endpoints to the current Mac LAN IP and restart Docker.
# Usage: ./scripts/local_lab_apply.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LAN_IP="${LAN_IP:-$(ipconfig getifaddr en0 2>/dev/null || true)}"
if [[ -z "${LAN_IP}" ]]; then
  LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi
if [[ -z "${LAN_IP}" ]]; then
  echo "ERROR: cannot detect LAN IP (en0/en1). Set LAN_IP=x.x.x.x" >&2
  exit 1
fi

WS_URL="ws://${LAN_IP}:8000/tbot/v1/"
OTA_URL="http://${LAN_IP}:8003/tbot/ota/"
HTTP_UI="http://${LAN_IP}:8002"
VISION="http://${LAN_IP}:8003/mcp/vision/explain"
ASSETS="http://${LAN_IP}:8003"

CFG="${ROOT}/data/.config.yaml"
if [[ ! -f "$CFG" ]]; then
  echo "ERROR: missing $CFG" >&2
  exit 1
fi

python3 - "$CFG" "$WS_URL" "$VISION" "$ASSETS" <<'PY'
import re, sys
from pathlib import Path
path, ws, vision, assets = sys.argv[1:5]
text = Path(path).read_text(encoding="utf-8")
text2 = re.sub(r"(?m)^(\s*websocket:\s*).*$", rf"\1{ws}", text, count=1)
text2 = re.sub(r"(?m)^(\s*vision_explain:\s*).*$", rf"\1{vision}", text2, count=1)
text2 = re.sub(r"(?m)^(\s*asset_public_base_url:\s*).*$", rf"\1{assets}", text2, count=1)
# Ensure local lab whitelist for known robots if missing.
if "28:84:85:85:1a:80" not in text2:
    text2 = text2.replace(
        "allowed_devices: []",
        'allowed_devices:\n      - "28:84:85:85:1a:80"\n      - "3c:0f:02:de:c2:e0"',
        1,
    )
Path(path).write_text(text2, encoding="utf-8")
print(f"updated {path}")
PY

echo "Updating manager sys_params ..."
docker exec tbot-esp32-server-db mysql -uroot -p123456 tbot_esp32_server -e "
UPDATE sys_params SET param_value='${WS_URL}' WHERE param_code='server.websocket';
UPDATE sys_params SET param_value='${OTA_URL}' WHERE param_code='server.ota';
UPDATE sys_params SET param_value='${HTTP_UI}' WHERE param_code='server.fronted_url';
SELECT param_code, param_value FROM sys_params
 WHERE param_code IN ('server.websocket','server.ota','server.fronted_url');
" 2>/dev/null

echo "Recreating tbot-esp32-server ..."
docker compose -f docker-compose_all.yml up -d --force-recreate tbot-esp32-server

sleep 4
echo "=== OTA health ==="
curl -sS "${OTA_URL}" || true
echo
echo "=== POST OTA sample (device mac) ==="
curl -sS -X POST "${OTA_URL}" \
  -H "Device-Id: 28:84:85:85:1a:80" \
  -H "Client-Id: local-lab" \
  -H "Content-Type: application/json" \
  -d '{"application":{"version":"2.2.34"},"board":{"type":"lcdwiki-es3c35p"}}' \
  | python3 -m json.tool 2>/dev/null || true

cat <<EOF

============================================================
LOCAL LAB READY on ${LAN_IP}
  OTA URL (put this on the robot):  ${OTA_URL}
  Websocket (advertised by OTA):    ${WS_URL}
  Manager UI:                       ${HTTP_UI}

ROBOT MUST use that OTA URL (not esp.tjbot.vn).
Serial previously showed:
  Connecting to websocket: wss://esp.tjbot.vn/tbot/v1/
Until OTA is changed, Hi-word will hit cloud, not this Docker.

After changing OTA on device + reboot, wake word is currently:
  "Hi, Tâm"  (firmware log-only / application path)
============================================================
EOF
