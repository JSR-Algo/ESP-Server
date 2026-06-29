# Current Lab Endpoints

These are the quick-tunnel endpoints currently wired for local robot testing.
They are not production-stable; if `cloudflared` or the laptop restarts, create
new tunnels and update this file plus `current-quick-tunnel.env`.

| Role | URL |
| --- | --- |
| Admin UI | `https://warranty-thunder-independence-related.trycloudflare.com` |
| OTA base | `https://carefully-freelance-improving-numerical.trycloudflare.com` |
| OTA endpoint | `https://carefully-freelance-improving-numerical.trycloudflare.com/tbot/ota/` |
| WebSocket | `wss://freebsd-concern-noon-cement.trycloudflare.com/tbot/v1/` |

## Reapply Manager Parameters

```sh
docker exec -i tbot-runtime-db sh -lc 'mysql -uroot -p123456 tbot_esp32_server' \
  < robot/esp32-server/deploy/current-quick-tunnel-sys-params.sql
```

## Reapply Robot OTA Override

For the USB-connected robot, `wifi/ota_url` should be:

```text
https://carefully-freelance-improving-numerical.trycloudflare.com/tbot/ota/
```

The current unit was verified with:

```text
ssid: SUMI_LAU1
ip: 192.168.0.111
ota host: carefully-freelance-improving-numerical.trycloudflare.com:443
```
