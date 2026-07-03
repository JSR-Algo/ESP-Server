# Current Endpoints

## Production

These are the stable `tjbot.vn` endpoints for the VPS deployment.

| Role | URL |
| --- | --- |
| Admin UI | `https://admin.tjbot.vn` |
| OTA base | `https://esp.tjbot.vn` |
| OTA endpoint | `https://esp.tjbot.vn/tbot/ota/` |
| WebSocket | `wss://esp.tjbot.vn/tbot/v1/` |

Current production ingress uses Cloudflare Tunnel because the VPS provider blocks
public `80/443` before traffic reaches host Nginx.

| Item | Value |
| --- | --- |
| Cloudflare zone nameservers | `johnny.ns.cloudflare.com`, `reza.ns.cloudflare.com` |
| Tunnel name | `tjbot-prod` |
| Tunnel ID | `389630b4-fc56-4d7a-97e2-cd5430641b89` |
| Tunnel config | `/etc/cloudflared/config.yml` on the VPS |
| Admin origin | `http://127.0.0.1:8002` |
| OTA/HTTP origin | `http://127.0.0.1:8003` |
| WebSocket origin | `http://127.0.0.1:8000` |

Cloudflare has an active custom security rule named
`bypass challenge for tjbot public subdomains` for:

```text
(http.host eq "esp.tjbot.vn") or (http.host eq "admin.tjbot.vn")
```

The rule action is `Skip` for Cloudflare challenge-producing components so
robot OTA and WebSocket clients are not served the browser challenge page.

Apply production manager parameters with:

```sh
docker exec -i tbot-esp32-server-db sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" tbot_esp32_server' \
  < robot/esp32-server/deploy/tjbot-prod-sys-params.sql
```

## Local Lab Endpoints

Do not commit ephemeral lab tunnel hostnames. For laptop-only testing, keep those
values in an untracked local env file and leave committed endpoint seeds on the
stable production host.

| Role | URL |
| --- | --- |
| Admin UI | `https://admin.tjbot.vn` |
| OTA base | `https://esp.tjbot.vn` |
| OTA endpoint | `https://esp.tjbot.vn/tbot/ota/` |
| WebSocket | `wss://esp.tjbot.vn/tbot/v1/` |

## Reapply Manager Parameters

```sh
docker exec -i tbot-runtime-db sh -lc 'mysql -uroot -p123456 tbot_esp32_server' \
  < robot/esp32-server/deploy/current-quick-tunnel-sys-params.sql
```

## Reapply Robot OTA Override

For the USB-connected robot, `wifi/ota_url` should be:

```text
https://esp.tjbot.vn/tbot/ota/
```

The current unit was verified with:

```text
ssid: SUMI_LAU1
ip: 192.168.0.111
ota host: esp.tjbot.vn:443
```
