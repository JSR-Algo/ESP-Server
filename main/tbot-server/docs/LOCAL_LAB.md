# Local lab (Mac Docker + physical robot)

## Server (already automated)

```bash
cd main/tbot-server
./scripts/local_lab_apply.sh
```

This sets:

| Item | Value |
|------|--------|
| OTA | `http://<LAN_IP>:8003/tbot/ota/` |
| Websocket | `ws://<LAN_IP>:8000/tbot/v1/` |
| Manager | `http://<LAN_IP>:8002` |
| Gemini key | Agent TBOT → Role → Google Live API Key only |

Whitelist MAC `28:84:85:85:1a:80` so WS token is not required.

## Why Hi-word is silent

Serial from the plugged robot showed:

```text
Connecting to websocket server: wss://esp.tjbot.vn/tbot/v1/
```

So the **firmware still uses production OTA/WS**, not Docker. Local server receives **zero** OTA/hello traffic.

Wake word on this build is **`Hi, Tâm`** (not always “Hi ESP”).

## Bind robot to local (required once)

1. Power robot on, same Wi‑Fi as the Mac.
2. Enter **Wi‑Fi / network setup** (AP mode / advanced settings on the LCD board).
3. Set **OTA URL** exactly to the line printed by `local_lab_apply.sh`, e.g.

   ```text
   http://192.168.100.230:8003/tbot/ota/
   ```

   (IP changes when Mac gets a new DHCP address — re-run the script.)

4. Save → reboot robot.
5. In a second terminal:

   ```bash
   ./scripts/local_lab_wait_device.sh
   ```

   You should see `OTARequest` + websocket connect.

6. Say **`Hi, Tâm`**. Expect: *“Dạ, mình nghe đây ạ.”*

## Verify cloud is not used

After rebind, serial should show something like:

```text
Connecting to websocket server: ws://192.168.100.230:8000/tbot/v1/
```

**Not** `wss://esp.tjbot.vn/...`.

## Gemini key

Only edit: **Manager → Agent TBOT → Role Config → Google Live API Key**.  
Runtime copies that key to all Gemini ASR/LLM/TTS modules.
