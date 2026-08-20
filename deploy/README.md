# Local-Build VPS Deploy

Deploy TBOT by building images locally, uploading a release bundle, then running only light Docker commands on the VPS.

## Current TBOT Production Notes

Current VPS deploys use DockerHub images instead of image tar upload when possible:

- Server image: `dinhmanh11/tbot-server:<tag>`
- Web/admin image: `dinhmanh11/tbot-server-web:<tag>`
- Known-good tag from the latest VPS rollout: `vps-20260525144756`
- Convenience tags pushed from the same build: `latest-vps`

Production device endpoints must use stable public domains. Temporary
`trycloudflare.com` quick tunnels are rejected by preflight and smoke checks.

- Admin: `https://admin.tjbot.vn`
- OTA: `https://esp.tjbot.vn/tbot/ota/`
- WebSocket: `wss://esp.tjbot.vn/tbot/v1/`

For Google Live mode, the Google API key must be saved in the Admin role config page, not in server env:

```text
https://admin.tjbot.vn/#/role-config?agentId=dd81bae707804544ac7404d4e389d280
```

The device MAC currently bound to that agent is:

```text
3c:0f:02:de:c2:e0 -> dd81bae707804544ac7404d4e389d280
```

Keep `GOOGLE_API_KEY` empty in `.env` for this manager-driven setup. The Python server reads the per-agent Google Live key from manager API private config.

## Flow

1. Build locally:
   ```sh
   esp32-server/deploy/build-local.sh --tag <tag>
   ```
2. Package locally:
   ```sh
   esp32-server/deploy/package-release.sh --tag <tag>
   ```
3. Prepare the VPS once:
   ```sh
   sudo mkdir -p /opt/tbot/{releases,data,uploadfile,mysql/data,redis/data,models/SenseVoiceSmall}
   sudo install -m 600 .env.example /opt/tbot/.env
   sudo editor /opt/tbot/.env
   ```
   Run from the uploaded release dir, or copy `.env.example` there first. Put local ASR model files under `/opt/tbot/models` when using FunASR/local ASR.
4. Deploy:
   ```sh
   esp32-server/deploy/deploy-vps.sh --host <ip> --user <ssh-user> --tag <tag>
   ```
   Deploy and rollback expect `/opt/tbot/current` to be a symlink. If an older
   VPS has a real `current` directory, the scripts refuse to replace it; migrate
   that directory separately before running them.
5. Smoke check:
   ```sh
   esp32-server/deploy/smoke-vps.sh --host <ip>
   curl http://<ip>:8003/tbot/ota/
   curl http://<ip>:8002/
   ```
   After DNS and Nginx are live:
   ```sh
   esp32-server/deploy/smoke-vps.sh \
     --admin-url https://admin.tjbot.vn/ \
     --ota-url https://esp.tjbot.vn/tbot/ota/ \
     --expected-ws-host esp.tjbot.vn
   ```
6. Roll back without rebuilding:
   ```sh
   esp32-server/deploy/rollback-vps.sh --host <ip> --user <ssh-user> --tag <previous-tag>
   ```

## Server-Only Safety Path

Use this path when only the Python ESP server image changes. It packages the reviewed database backup helper and deploy-safety scripts with the release, validates `/opt/tbot/.env` without sourcing or printing values, and recreates only `tbot-esp32-server` with Compose `--no-deps`.

```sh
TAG=<reviewed-tag>

deploy/package-release.sh \
  --tag "$TAG" \
  --server-only

deploy/deploy-vps.sh \
  --host <ip> \
  --user <ssh-user> \
  --tag "$TAG" \
  --server-only \
  --env-file deploy/production.env \
  --dry-run

deploy/rollback-vps.sh \
  --host <ip> \
  --user <ssh-user> \
  --tag <previous-reviewed-tag> \
  --server-only \
  --env-file deploy/production.env \
  --dry-run
```

Remove `--dry-run` only after the lane has passed its review/release gate and the production owner authorizes deployment or rollback. Server-only rollback validates the saved env before installing it, recreates only `tbot-esp32-server` with `--no-deps`, and verifies the database and web container IDs remain unchanged. The default remote free-space gate requires both 2 GiB and 5% free. Override them only with reviewed values using `--min-free-bytes` and `--min-free-percent`.

If the gate is missed, cleanup considers only images in the configured server image repository. It resolves every active scaled-server container through Compose, preserves every active image ID plus the newest distinct rollback image, and skips any image used by a container. The transaction fails before backup, image load, symlink switch, or Compose recreation if the threshold remains unmet.

The remote transaction snapshots the database and web container IDs before mutation and compares them after server health recovery. Any ID change fails the deployment. The only recreate command is:

```sh
docker compose --env-file /opt/tbot/.env \
  -f /opt/tbot/current/docker-compose.prod.yml \
  up -d --no-deps tbot-esp32-server
```

Dotenv files may contain blank lines, comments, `NAME=value`, and single- or double-quoted values, including quoted multiline public keys. Invalid identifiers, duplicate assignments, command substitution, shell operators, unquoted whitespace, and unterminated quotes fail closed. Diagnostics contain line/key metadata only, never values.

## DockerHub Fast Path

Use this path when DockerHub credentials are available locally and the VPS can pull images. It avoids uploading tarballs and avoids builds on the VPS.

Build locally for a normal Google Live deployment:

```sh
TAG="vps-$(date +%Y%m%d%H%M%S)"
./deploy/build-local.sh \
  --tag "$TAG" \
  --platform linux/amd64 \
  --server-image dinhmanh11/tbot-server \
  --web-image dinhmanh11/tbot-server-web \
  --server-base-image dinhmanh11/tbot-server-base \
  --build-base \
  --fast-google-live \
  --server-requirements-file main/tbot-server/requirements-google-live.txt
```

Push:

```sh
docker push "dinhmanh11/tbot-server:$TAG"
docker push "dinhmanh11/tbot-server-web:$TAG"
docker tag "dinhmanh11/tbot-server:$TAG" dinhmanh11/tbot-server:latest-vps
docker tag "dinhmanh11/tbot-server-web:$TAG" dinhmanh11/tbot-server-web:latest-vps
docker push dinhmanh11/tbot-server:latest-vps
docker push dinhmanh11/tbot-server-web:latest-vps
```

On the VPS, set `/opt/tbot/.env`:

```sh
TBOT_SERVER_IMAGE=dinhmanh11/tbot-server:<tag>
TBOT_WEB_IMAGE=dinhmanh11/tbot-server-web:<tag>
TBOT_REMOTE_ROOT=/opt/tbot
TBOT_PUBLIC_WEBSOCKET_URL=wss://esp.tjbot.vn/tbot/v1/
TBOT_BACKEND_API_URL=https://tbot-backend-8wmh.onrender.com/v1
TBOT_DEVICE_MINT_SECRET=<shared-device-mint-secret>
TBOT_SERVER_AUTH_KEY=<shared-ws-hmac-secret>
TZ=Asia/Ho_Chi_Minh
MYSQL_DATABASE=tbot_esp32_server
MYSQL_ROOT_PASSWORD=<existing-db-password>
MYSQL_SERVER_TIMEZONE=Asia/Ho_Chi_Minh
REDIS_PASSWORD=
TBOT_WS_PORT=8000
TBOT_HTTP_PORT=8003
TBOT_ADMIN_PORT=8002
GOOGLE_API_KEY=
```

## Host Nginx for tjbot.vn

The Docker stack keeps the same local/public ports from `docker-compose.prod.yml`:

```text
admin web/API: 127.0.0.1:8002
OTA/HTTP:      127.0.0.1:8003
WebSocket:     127.0.0.1:8000
```

### Current production ingress: Cloudflare Tunnel

The current VPS provider blocks public `80/443` before traffic reaches host
Nginx, so production uses Cloudflare Tunnel instead of direct public Nginx TLS.

Cloudflare DNS for `tjbot.vn` is delegated to:

```text
johnny.ns.cloudflare.com
reza.ns.cloudflare.com
```

The service reads `/etc/cloudflared/config.yml` on the VPS. The repo-owned,
placeholder-only ingress template and safe validation/apply/rollback procedure
are in [`cloudflared/README.md`](cloudflared/README.md). Keep the exact public
lesson generation routes ahead of hostname catch-alls so they reach host Nginx.

Cloudflare security must not serve a browser challenge to robots or WebSocket
clients. Keep an active custom security rule:

```text
name: bypass challenge for tjbot public subdomains
expression: (http.host eq "esp.tjbot.vn") or (http.host eq "admin.tjbot.vn")
action: Skip
skip components: remaining custom rules, managed rules, Super Bot Fight Mode,
Browser Integrity Check, Security Level
```

Verify the tunnel and public endpoints:

```sh
ssh -i ~/.ssh/tbot_vps_ed25519 -p 22701 root@160.187.240.56 \
  'systemctl is-enabled cloudflared && systemctl is-active cloudflared && cloudflared tunnel info tjbot-prod'

curl -I https://admin.tjbot.vn/
curl -sS https://esp.tjbot.vn/tbot/ota/
curl --http1.1 -I https://esp.tjbot.vn/tbot/v1/ \
  -H 'Connection: Upgrade' \
  -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Sec-WebSocket-Version: 13'
```

### Direct Nginx TLS fallback

Use this only if the VPS provider opens public `80/443`. Point DNS `A` records
for `admin.tjbot.vn` and `esp.tjbot.vn` to the VPS public IP, then install the
checked-in Nginx vhost:

```sh
sudo install -m 644 /opt/tbot/current/nginx/tjbot.vn.conf /etc/nginx/conf.d/tjbot.vn.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d admin.tjbot.vn -d esp.tjbot.vn
```

Apply production manager params:

```sh
docker exec -i tbot-esp32-server-db sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" tbot_esp32_server' \
  < /opt/tbot/current/tjbot-prod-sys-params.sql
```

Verify:

```sh
curl -I https://admin.tjbot.vn
curl -I https://esp.tjbot.vn/tbot/ota/
deploy/smoke-vps.sh \
  --admin-url https://admin.tjbot.vn/ \
  --ota-url https://esp.tjbot.vn/tbot/ota/ \
  --expected-ws-host esp.tjbot.vn
```

Then pull and recreate with Docker Compose v2:

```sh
cd /opt/tbot/current
docker compose --env-file /opt/tbot/.env -f docker-compose.prod.yml pull tbot-esp32-server tbot-esp32-server-web
docker compose --env-file /opt/tbot/.env -f docker-compose.prod.yml up -d tbot-esp32-server tbot-esp32-server-web
```

If the VPS only has `docker-compose` v1 and it fails with `KeyError: 'ContainerConfig'`, do not keep retrying Compose. Use the manual fallback below.

## Source of truth: `docker-compose.prod.yml` is canonical

`deploy/docker-compose.prod.yml` is the **single source of truth** for how every
container runs (names, network `tbot`, ports, volumes, env wiring, healthchecks,
`depends_on` ordering, and security options). Anything below is either driven by it
(`docker compose ... up -d`) or a **scoped fallback** that must reproduce the same
env knobs so it cannot drift from compose.

The drift this section exists to prevent: the historical live-prod manual run set
Redis up **without** `--requirepass` while compose sets it, and omitted the
`NESTJS_*` and external-MySQL knobs. The fallback and helper below pass every knob
from one env file so the manual path stays byte-identical to compose.

Knobs that MUST be passed by any manual run so it matches compose:

- `TZ`, `GOOGLE_API_KEY`
- `SPRING_DATASOURCE_DRUID_URL` / `_USERNAME` / `_PASSWORD`
  (built from `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_DATABASE`/`MYSQL_USER`/`MYSQL_PASSWORD`)
- `SPRING_DATA_REDIS_HOST` / `SPRING_DATA_REDIS_PORT` / **`SPRING_DATA_REDIS_PASSWORD`**
  (must equal `REDIS_PASSWORD`; Redis must run `--requirepass "$REDIS_PASSWORD"`)
- `NESTJS_UPSTREAM_HOST` / `NESTJS_UPSTREAM_SCHEME` /
  `NESTJS_ADMIN_PROXY_KEY` / `NESTJS_TOKEN` (the `/nestjs` course-CMS proxy;
  the admin proxy key is server-only and must match NestJS
  `TBOT_ADMIN_PROXY_KEY`, while the token is only for a legacy per-user-login
  rollback)
- `LESSON_RUNTIME_ENABLED`, `LESSON_SAMPLE_ENABLED`, `LESSON_RENDERER_V2_ENABLED`,
  `LESSON_ASSET_DELIVERY_MODE`, `LESSON_MOTION_PRESETS_ENABLED`,
  `LESSON_PLAYFUL_INTERACTIONS_ENABLED`, and `LESSON_ROLLOUT_DEVICE_ALLOWLIST`.
  Production defaults are dark; the initial
  enabled rollout requires `sd_pack` and exactly one device.

> Prefer compose. Reach for the manual fallback only when Compose v1 cannot
> recreate containers cleanly. For a **web-only** image bump, prefer
> `deploy/redeploy-web.sh` (below) — it swaps just the web container with the
> same `--env-file` and includes a healthcheck + automatic rollback.

## Compose v1 Manual Fallback

Use this only when Docker Compose v1 cannot recreate containers cleanly. It keeps
MySQL data and reuses the existing Docker network `tbot`.

This fallback reads **one** env file (`/opt/tbot/.env`, the same file compose uses)
so the manual run can never silently diverge from compose. Set every knob there
(see `deploy/.env.example`), including `REDIS_PASSWORD`, `NESTJS_UPSTREAM_HOST`,
`NESTJS_UPSTREAM_SCHEME`, `NESTJS_ADMIN_PROXY_KEY`, `NESTJS_TOKEN`, and any
external-MySQL overrides. Keep the admin proxy key server-only and equal to the
NestJS `TBOT_ADMIN_PROXY_KEY`; keep the legacy token empty unless intentionally
rolling back to per-user author login.

```sh
TAG=<tag>
TBOT_REMOTE_ROOT="${TBOT_REMOTE_ROOT:-/opt/tbot}"
ENV_FILE="$TBOT_REMOTE_ROOT/.env"
python3 "$TBOT_REMOTE_ROOT/current/validate-env.py" "$ENV_FILE"
set -a; . "$ENV_FILE"; set +a            # load .env so the knobs below are populated

: "${TBOT_SERVER_IMAGE:?set TBOT_SERVER_IMAGE in $ENV_FILE}"
: "${TBOT_WEB_IMAGE:?set TBOT_WEB_IMAGE in $ENV_FILE}"
: "${MYSQL_ROOT_PASSWORD:?set MYSQL_ROOT_PASSWORD in $ENV_FILE}"
: "${TBOT_PUBLIC_WEBSOCKET_URL:?set TBOT_PUBLIC_WEBSOCKET_URL in $ENV_FILE}"
: "${TBOT_BACKEND_API_URL:?set TBOT_BACKEND_API_URL in $ENV_FILE}"
: "${TBOT_DEVICE_MINT_SECRET:?set TBOT_DEVICE_MINT_SECRET in $ENV_FILE}"
: "${TBOT_SERVER_AUTH_KEY:?set TBOT_SERVER_AUTH_KEY in $ENV_FILE}"
: "${JWT_PUBLIC_KEY:?set JWT_PUBLIC_KEY in $ENV_FILE}"
: "${LESSON_ASSET_ORIGIN_BASE:?set LESSON_ASSET_ORIGIN_BASE in $ENV_FILE}"

# Resolve the same defaults compose uses so the manual run matches it exactly.
NODE_ENV="${NODE_ENV:-production}"
TBOT_REQUIRE_DEVICE_TOKEN="${TBOT_REQUIRE_DEVICE_TOKEN:-true}"
TZ="${TZ:-Asia/Ho_Chi_Minh}"
MYSQL_HOST="${MYSQL_HOST:-tbot-esp32-server-db}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="${MYSQL_DATABASE:-tbot_esp32_server}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$MYSQL_ROOT_PASSWORD}"
MYSQL_SERVER_TIMEZONE="${MYSQL_SERVER_TIMEZONE:-Asia/Ho_Chi_Minh}"
REDIS_HOST="${REDIS_HOST:-tbot-esp32-server-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
NESTJS_UPSTREAM_HOST="${NESTJS_UPSTREAM_HOST:-tbot-backend-8wmh.onrender.com}"
NESTJS_UPSTREAM_SCHEME="${NESTJS_UPSTREAM_SCHEME:-https}"
NESTJS_TOKEN="${NESTJS_TOKEN:-}"
NESTJS_ADMIN_PROXY_KEY="${NESTJS_ADMIN_PROXY_KEY:-}"
REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/0"
LESSON_RUNTIME_ENABLED="${LESSON_RUNTIME_ENABLED:-false}"
LESSON_SAMPLE_ENABLED="${LESSON_SAMPLE_ENABLED:-false}"
LESSON_RENDERER_V2_ENABLED="${LESSON_RENDERER_V2_ENABLED:-false}"
LESSON_ASSET_DELIVERY_MODE="${LESSON_ASSET_DELIVERY_MODE:-}"
LESSON_MOTION_PRESETS_ENABLED="${LESSON_MOTION_PRESETS_ENABLED:-false}"
LESSON_PLAYFUL_INTERACTIONS_ENABLED="${LESSON_PLAYFUL_INTERACTIONS_ENABLED:-false}"
LESSON_ROLLOUT_DEVICE_ALLOWLIST="${LESSON_ROLLOUT_DEVICE_ALLOWLIST:-}"
LESSON_ASSET_PACK_LOCAL_ROOT="${LESSON_ASSET_PACK_LOCAL_ROOT:-sd://tbot/lesson-assets}"
LESSON_ASSET_PACK_MOUNT_ROOT="${LESSON_ASSET_PACK_MOUNT_ROOT:-/opt/tbot-esp32-server/data/lesson-packs}"

[ "$LESSON_SAMPLE_ENABLED" = false ] || { echo "LESSON_SAMPLE_ENABLED must be false in production" >&2; exit 1; }
if [ "$LESSON_RENDERER_V2_ENABLED" = true ] && [ "$LESSON_RUNTIME_ENABLED" != true ]; then
  echo "renderer v2 requires LESSON_RUNTIME_ENABLED=true" >&2
  exit 1
fi
if [ "$LESSON_RUNTIME_ENABLED" = true ]; then
  [ "$LESSON_ASSET_DELIVERY_MODE" = sd_pack ] || { echo "enabled lessons require sd_pack" >&2; exit 1; }
  [ "$(printf '%s' "$LESSON_ROLLOUT_DEVICE_ALLOWLIST" | awk -F, '{c=0; for(i=1;i<=NF;i++) if($i~/[^[:space:]]/) c++; print c}')" -eq 1 ] || { echo "enabled lessons require exactly one rollout device" >&2; exit 1; }
fi

mkdir -p "$TBOT_REMOTE_ROOT"/{data,models,uploadfile,mysql/data,redis/data} "$TBOT_REMOTE_ROOT/data/lesson-packs"
# MySQL data remains at "$TBOT_REMOTE_ROOT/mysql/data" when the DB container is reused.
docker network inspect tbot >/dev/null 2>&1 || docker network create tbot

# Redis — match compose: enable --requirepass whenever REDIS_PASSWORD is set.
docker rm -f tbot-esp32-server-redis 2>/dev/null || true
if [ -n "$REDIS_PASSWORD" ]; then REDIS_AUTH="--requirepass \"$REDIS_PASSWORD\""; else REDIS_AUTH=""; fi
docker run -d --name tbot-esp32-server-redis --restart unless-stopped \
  --network tbot -v "$TBOT_REMOTE_ROOT/redis/data":/data \
  "${REDIS_IMAGE:-redis:8.0}" sh -c "exec redis-server --appendonly yes $REDIS_AUTH"

docker rm -f tbot-esp32-server 2>/dev/null || true
docker run -d --name tbot-esp32-server --restart unless-stopped \
  --network tbot --security-opt seccomp:unconfined \
  -e "TZ=$TZ" -e "NODE_ENV=$NODE_ENV" -e "GOOGLE_API_KEY=${GOOGLE_API_KEY:-}" \
  -e "TBOT_PUBLIC_WEBSOCKET_URL=$TBOT_PUBLIC_WEBSOCKET_URL" \
  -e "TBOT_BACKEND_API_URL=$TBOT_BACKEND_API_URL" \
  -e "TBOT_REQUIRE_DEVICE_TOKEN=$TBOT_REQUIRE_DEVICE_TOKEN" \
  -e "JWT_PUBLIC_KEY=$JWT_PUBLIC_KEY" \
  -e "TBOT_DEVICE_MINT_SECRET=$TBOT_DEVICE_MINT_SECRET" \
  -e "TBOT_SERVER_AUTH_KEY=$TBOT_SERVER_AUTH_KEY" \
  -e "LESSON_ASSET_ORIGIN_BASE=$LESSON_ASSET_ORIGIN_BASE" \
  -e "LESSON_RUNTIME_ENABLED=$LESSON_RUNTIME_ENABLED" \
  -e "LESSON_SAMPLE_ENABLED=$LESSON_SAMPLE_ENABLED" \
  -e "LESSON_RENDERER_V2_ENABLED=$LESSON_RENDERER_V2_ENABLED" \
  -e "LESSON_ASSET_DELIVERY_MODE=$LESSON_ASSET_DELIVERY_MODE" \
  -e "LESSON_MOTION_PRESETS_ENABLED=$LESSON_MOTION_PRESETS_ENABLED" \
  -e "LESSON_PLAYFUL_INTERACTIONS_ENABLED=$LESSON_PLAYFUL_INTERACTIONS_ENABLED" \
  -e "LESSON_ROLLOUT_DEVICE_ALLOWLIST=$LESSON_ROLLOUT_DEVICE_ALLOWLIST" \
  -e "LESSON_ASSET_PACK_LOCAL_ROOT=$LESSON_ASSET_PACK_LOCAL_ROOT" \
  -e "LESSON_ASSET_PACK_MOUNT_ROOT=$LESSON_ASSET_PACK_MOUNT_ROOT" \
  -e "REDIS_URL=$REDIS_URL" \
  --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/tbot/ota/', timeout=3).read(1)\"" \
  --health-interval 15s --health-timeout 5s --health-retries 6 --health-start-period 30s \
  -p "${TBOT_WS_PORT:-8000}:8000" -p "${TBOT_HTTP_PORT:-8003}:8003" \
  -v "$TBOT_REMOTE_ROOT/data":/opt/tbot-esp32-server/data \
  -v "$TBOT_REMOTE_ROOT/data/lesson-packs:$LESSON_ASSET_PACK_MOUNT_ROOT" \
  -v "$TBOT_REMOTE_ROOT/models":/opt/tbot-esp32-server/models \
  "$TBOT_SERVER_IMAGE"

docker rm -f tbot-esp32-server-web 2>/dev/null || true
docker run -d --name tbot-esp32-server-web --restart unless-stopped \
  --network tbot \
  -e "TZ=$TZ" \
  -e "SPRING_DATASOURCE_DRUID_URL=jdbc:mysql://${MYSQL_HOST}:${MYSQL_PORT}/${MYSQL_DATABASE}?useUnicode=true&characterEncoding=UTF-8&serverTimezone=${MYSQL_SERVER_TIMEZONE}&nullCatalogMeansCurrent=true&connectTimeout=30000&socketTimeout=30000&autoReconnect=true&failOverReadOnly=false&maxReconnects=10" \
  -e "SPRING_DATASOURCE_DRUID_USERNAME=$MYSQL_USER" \
  -e "SPRING_DATASOURCE_DRUID_PASSWORD=$MYSQL_PASSWORD" \
  -e "SPRING_DATA_REDIS_HOST=$REDIS_HOST" \
  -e "SPRING_DATA_REDIS_PASSWORD=$REDIS_PASSWORD" \
  -e "SPRING_DATA_REDIS_PORT=$REDIS_PORT" \
  -e "NESTJS_UPSTREAM_HOST=$NESTJS_UPSTREAM_HOST" \
  -e "NESTJS_UPSTREAM_SCHEME=$NESTJS_UPSTREAM_SCHEME" \
  -e "NESTJS_TOKEN=$NESTJS_TOKEN" \
  -e "NESTJS_ADMIN_PROXY_KEY=$NESTJS_ADMIN_PROXY_KEY" \
  -p "${TBOT_ADMIN_PORT:-8002}:8002" \
  -v "$TBOT_REMOTE_ROOT/uploadfile":/uploadfile \
  "$TBOT_WEB_IMAGE"
```

After fallback, verify the running images:

```sh
docker inspect tbot-esp32-server tbot-esp32-server-web \
  --format '{{.Name}} {{.Config.Image}} {{.State.Status}} restart={{.State.Restarting}}'
```

## Web-only redeploy (`redeploy-web.sh`)

For the common case — a new web/admin image with the rest of the stack unchanged —
use the helper instead of the full fallback. It loads `/opt/tbot/.env`, saves the
current web image as a rollback point, stops + renames the old container, runs the
new one with the **same env knobs as compose**, healthchecks `:8002`, and rolls
back automatically if the healthcheck fails.

```sh
# pull-based image (DockerHub):
deploy/redeploy-web.sh --tag <new-tag>

# tarball-based image (offline / package-release):
deploy/redeploy-web.sh --image-tar /opt/tbot/current/tbot-server-web.tar.gz

# repoint the env file or admin port if they differ from the defaults:
deploy/redeploy-web.sh --tag <new-tag> --env-file /opt/tbot/.env --admin-port 8002
```

It only touches `tbot-esp32-server-web`; MySQL, Redis, and the Python server are
left running. Because it reads the same `/opt/tbot/.env` as compose, the new web
container picks up `SPRING_*` (incl. the Redis password), `NESTJS_UPSTREAM_HOST`,
and any external-MySQL knobs without drift.

## Runtime

`docker-compose.prod.yml` expects prebuilt images from `$TBOT_REMOTE_ROOT/.env`:

- `TBOT_SERVER_IMAGE` for the Python voice/WebSocket/OTA server.
- `TBOT_WEB_IMAGE` for the web/admin image.

The production stack exposes:

- `8000` WebSocket device traffic.
- `8003` Python HTTP/OTA/vision service.
- `8002` web/admin.

Persistent VPS paths:

- `$TBOT_REMOTE_ROOT/data`
- `$TBOT_REMOTE_ROOT/uploadfile`
- `$TBOT_REMOTE_ROOT/models`
- `$TBOT_REMOTE_ROOT/mysql/data`
- `$TBOT_REMOTE_ROOT/redis/data`

## VPS Notes

Minimum practical sizing:

- API-only or light testing: 2 vCPU / 4 GB RAM.
- Full stack with local ASR/FunASR workloads: 4 vCPU / 8 GB RAM or more.

Keep free disk for the current release, previous release, Docker layers, MySQL data, Redis AOF, logs, and model files. Image tarballs can be large; prune old releases only after a known-good rollback point exists.

## Google Live Fast Profile

When Google Live is the primary voice path, prefer `requirements-google-live.txt` with `--fast-google-live`. This skips heavy local ASR/model packages such as torch, FunASR, sherpa/modelscope, and avoids CUDA requirements.

If local ASR/FunASR is needed later, rebuild the server base without `--fast-google-live` and mount the required model files under `$TBOT_REMOTE_ROOT/models`.

## Required Smoke Checks

Run these before claiming a VPS deploy is complete:

### Backend Render Endpoint Handoff

The backend `/v1/device/bootstrap` and `/v1/device/config` responses are the
production source of truth that mobile provisioning and firmware fallback read.
After the ESP VPS has a stable public host, update the backend Render service
with these env values before asking a robot to use the production URL:

```text
TBOT_ESP_SERVER_URL=https://esp.tjbot.vn
TBOT_OTA_URL=https://esp.tjbot.vn/tbot/ota/
TBOT_WS_URL=wss://esp.tjbot.vn/tbot/v1/
```

Do not use a `trycloudflare.com` quick tunnel for any of those values. The
backend resolver fails fast in production when quick-tunnel endpoints are present,
but an already-deployed older backend or stale Render env can still advertise an
unsafe endpoint until the service is redeployed with the corrected env.

From the local TBOT workspace root, verify the public backend after deploy:

```sh
python3 robot/scripts/tbot_connect_live_probe.py \
  --backend-url https://tbot-backend-8wmh.onrender.com \
  --expected-ws-host esp.tjbot.vn \
  --timeout 20
```

The probe must return `"ok": true`. If it reports `bootstrap uses quick tunnel
device endpoint`, the production backend is not ready for robot lesson traffic.

### VPS Runtime Smoke

From the local TBOT workspace root:

```sh
python3 robot/scripts/tbot_connect_deploy_preflight.py \
  --env-file robot/esp32-server/deploy/.env.example \
  --expected-remote-root /opt/tbot
```

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep tbot-esp32-server
curl -k -sS 'https://esp.tjbot.vn/tbot/ota/'
curl -sS -i http://127.0.0.1:8002/ | head
docker exec tbot-esp32-server sh -lc 'cd /opt/tbot-esp32-server && python scripts/google_live_smoke.py --manager-device-id 3c:0f:02:de:c2:e0 --manager-client-id 2e820403-2eb5-45d9-9694-c9d6635af87e'
docker logs --since=3m tbot-esp32-server 2>&1 | grep -Ei 'Google Live API key is missing|manager-api config error|Device not found|Traceback|Exception|ERROR' || true
```

Expected results:

- OTA response includes `wss://esp.tjbot.vn/tbot/v1/`.
- Admin health returns HTTP 200 with `401 Unauthorized` for unauthenticated root path.
- Google Live smoke prints `SMOKE_CONNECT_OK` and `SMOKE_CLOSE_OK`.
- Error grep has no active Google key, manager API, device binding, or traceback errors.

## Security

- Do not commit `/opt/tbot/.env` or real secrets.
- Prefer SSH keys over password login.
- Keep public inbound traffic on `80` and `443`; restrict direct access to `8000`, `8002`, and `8003` to trusted operators while Nginx fronts production traffic.
- Keep MySQL `3306` and Redis `6379` unexposed.
- Put TLS and admin access controls in front of public admin deployments.
- Rotate `MYSQL_ROOT_PASSWORD` and `REDIS_PASSWORD` before first public use.
