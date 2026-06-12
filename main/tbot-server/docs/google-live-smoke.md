# Google Live Smoke

Use this only when you have a real `GOOGLE_API_KEY`.

## Quick connect/close smoke

```bash
cd esp32-server/main/tbot-server
GOOGLE_API_KEY=... ./.venv311/bin/python scripts/google_live_smoke.py
```

Optional overrides:

```bash
GOOGLE_API_KEY=... \
GOOGLE_LIVE_MODEL=gemini-3.1-flash-live-preview \
GOOGLE_LIVE_VOICE_NAME=Kore \
./.venv311/bin/python scripts/google_live_smoke.py
```

Expected output:

```text
SMOKE_CONNECT_OK
SMOKE_CLOSE_OK
```

## Manager-backed smoke

Use this when the real Google Live key lives in manager API private config. The
script loads the agent config in memory and does not print secrets.

```bash
cd esp32-server/main/tbot-server
./.venv311/bin/python scripts/google_live_smoke.py \
  --manager-device-id 3c:0f:02:de:c2:e0 \
  --manager-client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc
```

## Device network preflight

Run this before the 10-cycle physical robot soak. Packet loss must be zero; high
average latency means Wi-Fi/router placement must be fixed before judging audio
or barge-in behavior.

```bash
cd esp32-server/main/tbot-server
./.venv311/bin/python scripts/voice_mode_preflight.py \
  --device-ip 192.168.0.<robot-ip> \
  --max-loss-pct 0 \
  --max-avg-ms 1000 \
  --max-max-ms 1500 \
  --max-jitter-ms 500 \
  --max-duplicates 0
```

## Websocket barge-in soak

This exercises the real server websocket, manager private config, Google Live
session, text interrupt path, `tts stop`, new `tts start`, and binary audio
return. It does not replace speaking into the physical robot microphone.

```bash
cd esp32-server/main/tbot-server
./.venv311/bin/python scripts/voice_mode_websocket_soak.py \
  --device-id 3c:0f:02:de:c2:e0 \
  --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \
  --cycles 10
```

## Physical Vietnamese interrupt smoke

This is the production gate for robot voice mode. It must be run on the real
robot after OTA/flash, not only against the websocket soak client.

1. Connect the real robot to `ws://192.168.0.114:8000/tbot/v1/`.
2. Speak Vietnamese for 8-15 seconds.
3. Confirm transcript stays close to the spoken intent.
4. Let AI start speaking.
5. Interrupt mid-sentence with a new Vietnamese request.
6. Confirm old audio stops immediately.
7. Confirm the new request is answered.
8. Repeat 10 cycles.
9. Disconnect/reconnect the robot.
10. Check logs for no fatal error, duplicate session, stale audio, or self-interrupt loop.

Audit server logs after the run:

```bash
cd esp32-server/main/tbot-server
tmux capture-pane -t tbot_server -p -S -24000 > /tmp/tbot_physical_audit.log
./.venv311/bin/python scripts/physical_smoke_audit.py /tmp/tbot_physical_audit.log \
  --device-id 3c:0f:02:de:c2:e0 \
  --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \
  --server-ip 192.168.0.114 \
  --min-interrupts 10
```

Optional proxy check before the physical run:

```bash
cd esp32-server/main/tbot-server
./.venv311/bin/python scripts/voice_mode_websocket_audio_bargein.py \
  --websocket-url ws://192.168.0.114:8000/tbot/v1/ \
  --device-id 3c:0f:02:de:c2:e0 \
  --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc
```

This verifies Opus input can trigger local barge-in over the real websocket. It
does not replace the physical Vietnamese microphone smoke.

## Hardware unblock runbook

Use this when the physical audit reports `physical_ws_connected=false`.

1. Confirm the server is reachable from the robot network:

```bash
cd esp32-server/main/tbot-server
lsof -nP -iTCP:8000 -sTCP:LISTEN
curl -sS -X POST http://127.0.0.1:8002/tbot/ota/ \
  -H 'Device-Id: 3c:0f:02:de:c2:e0' \
  -H 'Client-Id: d16afa54-eb44-4fcb-8cac-cdefdf05f6fc' \
  -H 'Content-Type: application/json' \
  --data '{"application":{"name":"xiaozhi","version":"2.2.6"},"board":{"type":"freenove-esp32s3-display-2.8-lcd","ssid":"local","rssi":-45,"channel":6,"ip":"192.168.0.120","mac":"3c:0f:02:de:c2:e0"},"mac_address":"3c:0f:02:de:c2:e0","uuid":"d16afa54-eb44-4fcb-8cac-cdefdf05f6fc"}'
```

2. Confirm the robot is on LAN. The target MAC must appear before judging the
physical voice path:

```bash
fping -a -g 192.168.0.1 192.168.0.254 -r1 -t300 2>/dev/null | sort -V | while read ip; do arp -n "$ip"; done
```

3. If OTA does not start, connect an ESP32-S3 bootloader/data USB path and
verify serial access before flashing:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
source /Users/manhhodinh/esp/esp-idf/export.sh
python /Users/manhhodinh/esp/esp-idf/components/esptool_py/esptool/esptool.py \
  --chip esp32s3 -p /dev/cu.<esp32-port> -b 115200 --connect-attempts 2 chip_id
```

4. Only after `chip_id` succeeds, flash the built firmware and rerun the physical
Vietnamese interrupt smoke:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
source /Users/manhhodinh/esp/esp-idf/export.sh
idf.py -p /dev/cu.<esp32-port> flash monitor
```

## Optional unittest smoke

```bash
cd esp32-server/main/tbot-server
RUN_GOOGLE_LIVE_SMOKE=1 GOOGLE_API_KEY=... \
./.venv311/bin/python -m unittest tests.test_google_live_live_smoke -v
```

If `RUN_GOOGLE_LIVE_SMOKE` or `GOOGLE_API_KEY` is missing, the smoke test is skipped by design.
