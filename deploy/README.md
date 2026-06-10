# Local-Build VPS Deploy

Deploy TJBot by building images locally, uploading a release bundle, then running only light Docker commands on the VPS.

## Current TJBot Production Notes

Current VPS deploys use DockerHub images instead of image tar upload when possible:

- Server image: `dinhmanh11/TJBot-server:<tag>`
- Web/admin image: `dinhmanh11/TJBot-server-web:<tag>`
- Known-good tag from the latest VPS rollout: `vps-20260525144756`
- Convenience tags pushed from the same build: `latest-vps`

The active public tunnel URLs for the firmware and OTA config are:

- Admin: `https://animation-shareholders-country-these.trycloudflare.com`
- OTA: `https://luggage-spears-louisville-psychology.trycloudflare.com/TJBot/ota/`
- WebSocket: `wss://perform-elvis-specifically-nominated.trycloudflare.com/TJBot/v1/`

For Google Live mode, the Google API key must be saved in the Admin role config page, not in server env:

```text
https://animation-shareholders-country-these.trycloudflare.com/#/role-config?agentId=dd81bae707804544ac7404d4e389d280
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
   sudo mkdir -p /opt/TJBot/{releases,data,uploadfile,mysql/data,redis/data,models/SenseVoiceSmall}
   sudo install -m 600 .env.example /opt/TJBot/.env
   sudo editor /opt/TJBot/.env
   ```
   Run from the uploaded release dir, or copy `.env.example` there first. Put local ASR model files under `/opt/TJBot/models` when using FunASR/local ASR.
4. Deploy:
   ```sh
   esp32-server/deploy/deploy-vps.sh --host <ip> --user <ssh-user> --tag <tag>
   ```
5. Smoke check:
   ```sh
   esp32-server/deploy/smoke-vps.sh --host <ip>
   curl http://<ip>:8003/TJBot/ota/
   curl http://<ip>:8002/
   ```
6. Roll back without rebuilding:
   ```sh
   esp32-server/deploy/rollback-vps.sh --host <ip> --user <ssh-user> --tag <previous-tag>
   ```

## DockerHub Fast Path

Use this path when DockerHub credentials are available locally and the VPS can pull images. It avoids uploading tarballs and avoids builds on the VPS.

Build locally for a normal Google Live deployment:

```sh
TAG="vps-$(date +%Y%m%d%H%M%S)"
./deploy/build-local.sh \
  --tag "$TAG" \
  --platform linux/amd64 \
  --server-image dinhmanh11/TJBot-server \
  --web-image dinhmanh11/TJBot-server-web \
  --server-base-image dinhmanh11/TJBot-server-base \
  --build-base \
  --fast-google-live \
  --server-requirements-file main/TJBot-server/requirements-google-live.txt
```

Push:

```sh
docker push "dinhmanh11/TJBot-server:$TAG"
docker push "dinhmanh11/TJBot-server-web:$TAG"
docker tag "dinhmanh11/TJBot-server:$TAG" dinhmanh11/TJBot-server:latest-vps
docker tag "dinhmanh11/TJBot-server-web:$TAG" dinhmanh11/TJBot-server-web:latest-vps
docker push dinhmanh11/TJBot-server:latest-vps
docker push dinhmanh11/TJBot-server-web:latest-vps
```

On the VPS, set `/opt/TJBot/current/.env`:

```sh
TJBot_SERVER_IMAGE=dinhmanh11/TJBot-server:<tag>
TJBot_WEB_IMAGE=dinhmanh11/TJBot-server-web:<tag>
TJBot_REMOTE_ROOT=/opt/TJBot
TZ=Asia/Ho_Chi_Minh
MYSQL_DATABASE=TJBot_esp32_server
MYSQL_ROOT_PASSWORD=<existing-db-password>
MYSQL_SERVER_TIMEZONE=Asia/Ho_Chi_Minh
REDIS_PASSWORD=
TJBot_WS_PORT=8000
TJBot_HTTP_PORT=8003
TJBot_ADMIN_PORT=8002
GOOGLE_API_KEY=
```

Then pull and recreate with Docker Compose v2:

```sh
cd /opt/TJBot/current
docker compose --env-file .env -f docker-compose.prod.yml pull TJBot-esp32-server TJBot-esp32-server-web
docker compose --env-file .env -f docker-compose.prod.yml up -d TJBot-esp32-server TJBot-esp32-server-web
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
- `NESTJS_UPSTREAM_HOST` / `NESTJS_TOKEN` (the `/nestjs` course-CMS proxy upstream)

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
and any external-MySQL overrides.

```sh
TAG=<tag>
ENV_FILE=/opt/tbot/.env
set -a; . "$ENV_FILE"; set +a            # load .env so the knobs below are populated

: "${TBOT_SERVER_IMAGE:?set TBOT_SERVER_IMAGE in $ENV_FILE}"
: "${TBOT_WEB_IMAGE:?set TBOT_WEB_IMAGE in $ENV_FILE}"
: "${MYSQL_ROOT_PASSWORD:?set MYSQL_ROOT_PASSWORD in $ENV_FILE}"

# Resolve the same defaults compose uses so the manual run matches it exactly.
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
NESTJS_TOKEN="${NESTJS_TOKEN:-}"
REMOTE_ROOT="${TBOT_REMOTE_ROOT:-/opt/tbot}"

mkdir -p "$REMOTE_ROOT"/{data,models,uploadfile,redis/data}
docker network inspect tbot >/dev/null 2>&1 || docker network create tbot

# Redis — match compose: enable --requirepass whenever REDIS_PASSWORD is set.
docker rm -f tbot-esp32-server-redis 2>/dev/null || true
if [ -n "$REDIS_PASSWORD" ]; then REDIS_AUTH="--requirepass \"$REDIS_PASSWORD\""; else REDIS_AUTH=""; fi
docker run -d --name tbot-esp32-server-redis --restart unless-stopped \
  --network tbot -v "$REMOTE_ROOT/redis/data":/data \
  "${REDIS_IMAGE:-redis:8.0}" sh -c "exec redis-server --appendonly yes $REDIS_AUTH"

docker rm -f tbot-esp32-server 2>/dev/null || true
docker run -d --name tbot-esp32-server --restart unless-stopped \
  --network tbot --security-opt seccomp:unconfined \
  -e "TZ=$TZ" -e "GOOGLE_API_KEY=${GOOGLE_API_KEY:-}" \
  -p "${TBOT_WS_PORT:-8000}:8000" -p "${TBOT_HTTP_PORT:-8003}:8003" \
  -v "$REMOTE_ROOT/data":/opt/tbot-esp32-server/data \
  -v "$REMOTE_ROOT/models":/opt/tbot-esp32-server/models \
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
  -e "NESTJS_TOKEN=$NESTJS_TOKEN" \
  -p "${TBOT_ADMIN_PORT:-8002}:8002" \
  -v "$REMOTE_ROOT/uploadfile":/uploadfile \
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

`docker-compose.prod.yml` expects prebuilt images from `/opt/TJBot/.env`:

- `TJBot_SERVER_IMAGE` for the Python voice/WebSocket/OTA server.
- `TJBot_WEB_IMAGE` for the web/admin image.

The production stack exposes:

- `8000` WebSocket device traffic.
- `8003` Python HTTP/OTA/vision service.
- `8002` web/admin.

Persistent VPS paths:

- `/opt/TJBot/data`
- `/opt/TJBot/uploadfile`
- `/opt/TJBot/models`
- `/opt/TJBot/mysql/data`
- `/opt/TJBot/redis/data`

## VPS Notes

Minimum practical sizing:

- API-only or light testing: 2 vCPU / 4 GB RAM.
- Full stack with local ASR/FunASR workloads: 4 vCPU / 8 GB RAM or more.

Keep free disk for the current release, previous release, Docker layers, MySQL data, Redis AOF, logs, and model files. Image tarballs can be large; prune old releases only after a known-good rollback point exists.

## Google Live Fast Profile

When Google Live is the primary voice path, prefer `requirements-google-live.txt` with `--fast-google-live`. This skips heavy local ASR/model packages such as torch, FunASR, sherpa/modelscope, and avoids CUDA requirements.

If local ASR/FunASR is needed later, rebuild the server base without `--fast-google-live` and mount the required model files under `/opt/TJBot/models`.

## Required Smoke Checks

Run these before claiming a VPS deploy is complete:

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep TJBot-esp32-server
curl -k -sS 'https://luggage-spears-louisville-psychology.trycloudflare.com/TJBot/ota/'
curl -sS -i http://127.0.0.1:8002/TJBot/ | head
docker exec TJBot-esp32-server sh -lc 'cd /opt/TJBot-esp32-server && python scripts/google_live_smoke.py --manager-device-id 3c:0f:02:de:c2:e0 --manager-client-id 2e820403-2eb5-45d9-9694-c9d6635af87e'
docker logs --since=3m TJBot-esp32-server 2>&1 | grep -Ei 'Google Live API key is missing|manager-api config error|Device not found|Traceback|Exception|ERROR' || true
```

Expected results:

- OTA response includes `wss://perform-elvis-specifically-nominated.trycloudflare.com/TJBot/v1/`.
- Admin health returns HTTP 200 with `401 Unauthorized` for unauthenticated root path.
- Google Live smoke prints `SMOKE_CONNECT_OK` and `SMOKE_CLOSE_OK`.
- Error grep has no active Google key, manager API, device binding, or traceback errors.

## Security

- Do not commit `/opt/TJBot/.env` or real secrets.
- Prefer SSH keys over password login.
- Restrict inbound firewall access to `8000`, `8002`, and `8003`; keep MySQL `3306` and Redis `6379` unexposed.
- Put TLS and admin access controls in front of public `8002` deployments.
- Rotate `MYSQL_ROOT_PASSWORD` and `REDIS_PASSWORD` before first public use.
