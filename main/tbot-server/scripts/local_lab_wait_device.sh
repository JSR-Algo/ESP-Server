#!/usr/bin/env bash
# Wait until the robot hits local OTA/WS (proves OTA URL was changed off cloud).
set -euo pipefail
echo "Watching tbot-esp32-server logs for device OTA/WS (Ctrl+C to stop)..."
echo "Expected after OTA rebind: OTARequestDevice ID: 28:84:85:85:1a:80"
docker logs -f tbot-esp32-server 2>&1 | grep --line-buffered -iE \
  'OTARequest|Device ID|28:84|websocket|hello|wake_greeting|Google Live connect|auth'
