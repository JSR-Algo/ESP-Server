# TJBot Production Domain Design

## Goal

Expose the current VPS stack through stable `tjbot.vn` subdomains and make the deploy source of truth match those production endpoints.

## Endpoints

- `https://admin.tjbot.vn` routes to the admin web/API container on local port `8002`.
- `https://esp.tjbot.vn/tbot/ota/` routes to the robot HTTP/OTA service on local port `8003`.
- `wss://esp.tjbot.vn/tbot/v1/` routes to the robot WebSocket service on local port `8000`.

The `/tbot/ota/` and `/tbot/v1/` paths stay unchanged because firmware, OTA payload validation, HAProxy, and manager params already depend on them.

## Deployment Shape

DNS uses two `A` records pointing at the VPS public IP:

- `admin.tjbot.vn`
- `esp.tjbot.vn`

Nginx runs on the VPS host and terminates HTTPS. Docker continues publishing the existing local ports from `docker-compose.prod.yml`, so no app container change is required.

## Source Changes

- Add a reusable Nginx vhost config for `admin.tjbot.vn` and `esp.tjbot.vn`.
- Add a production SQL params file for `server.fronted_url`, `server.websocket`, and `server.ota`.
- Update deploy env examples and README commands to use `tjbot.vn` production URLs.
- Keep quick-tunnel files as lab-only references instead of replacing them.

## Verification

- Static shell checks for deploy scripts.
- Nginx config syntax can be tested on the VPS with `sudo nginx -t`.
- HTTP checks:
  - `curl -I https://admin.tjbot.vn`
  - `curl -I https://esp.tjbot.vn/tbot/ota/`
- OTA payload check should confirm `websocket.url` host is `esp.tjbot.vn` and path is `/tbot/v1/`.
