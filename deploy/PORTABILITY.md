# esp-server portability runbook

Goal: changing the **DB / Redis / NestJS course backend / VPS** should be a
**config change + restart**, never a code edit or image rebuild.

Two ways the web container runs in practice:
- **Live prod (current):** a manual `docker run --env-file /opt/tbot/web-runtime.env`
  on the `tbot` docker network. Change config = edit `/opt/tbot/web-runtime.env`
  then `docker restart tbot-esp32-server-web`.
- **Canonical / fresh VPS:** `docker compose -f deploy/docker-compose.prod.yml`
  with `/opt/tbot/.env` (see `deploy/.env.example`). Change config = edit
  `/opt/tbot/.env` then `docker compose up -d`.

The built SPA has **zero hardcoded backend URLs** — every call is relative
(`/tbot`, `/nestjs`) and resolved by nginx, so the frontend never needs a rebuild
when infra moves. nginx renders `/nestjs` from env at container start (`start.sh`).

---

## A — Change MySQL (name / password / host / port / user)

| Knob | env var | default |
|---|---|---|
| db name | `MYSQL_DATABASE` | `tbot_esp32_server` |
| password | `MYSQL_ROOT_PASSWORD` (or `MYSQL_PASSWORD` for a non-root user) | — |
| host | `MYSQL_HOST` | `tbot-esp32-server-db` (in-stack) |
| port | `MYSQL_PORT` | `3306` |
| user | `MYSQL_USER` | `root` |

- **compose:** set the vars in `/opt/tbot/.env`, `docker compose up -d`.
- **live manual-run:** the env-file carries the assembled
  `SPRING_DATASOURCE_DRUID_URL` / `_USERNAME` / `_PASSWORD` — edit those directly
  in `/opt/tbot/web-runtime.env`, then restart the web container.
- Frontend + Python tbot-server need **no change** (neither touches MySQL).
- **Back up first:** `deploy/backup-db.sh` (now dumps `$MYSQL_DATABASE`, not the
  old wrong `tbot`).

## B — Change the NestJS course backend (the `/nestjs` upstream)

| Knob | env var | default |
|---|---|---|
| backend host | `NESTJS_UPSTREAM_HOST` | `tbot-backend-8wmh.onrender.com` |
| shared token | `NESTJS_TOKEN` (optional; per-user "Author sign-in" overrides) | empty |

- Set in `/opt/tbot/web-runtime.env` (live) or `/opt/tbot/.env` (compose) →
  restart. `start.sh` re-renders nginx; **no rebuild**.
- The backend's own Postgres is managed where that NestJS is deployed (e.g.
  Render `tbot-db`); changing it is a backend-side concern, transparent here.
- **Server-side gap (TODO):** tbot-server's `server.api_url`
  (`core/lesson/runtime.py`) is not yet env-driven — see "Open hardening" below.

## C — Move to a new VPS

1. Install Docker + compose; create `/opt/tbot/{data,uploadfile,mysql/data,redis/data,models}`.
2. Copy `deploy/.env.example` → `/opt/tbot/.env`, fill secrets.
3. **Restore DB:** `gunzip < backup.sql.gz | docker exec -i tbot-esp32-server-db mysql -u<user> -p<pw> <db>`.
4. Deploy: `deploy/deploy-vps.sh --host <ip> --user <user> --tag <tag>` (host/user
   are args — no IP baked in), or load image tarballs + `docker compose up -d`.
5. **Tunnels:** if the public ingress (Cloudflare tunnel / domain) changes, update
   the server-advertised URLs in the Admin console `sys_params`
   (`server.websocket` / `server.ota` / `server.fronted_url`) — these are DB rows,
   not env.
6. ⚠ **Flashed devices:** firmware bakes the OTA-bootstrap + provisioning URLs into
   the `.bin`. A device whose NVS is cleared falls back to the compiled URL, so the
   bootstrap host must be a **stable** domain (not a rotating tunnel) — see below.

## D — Change public ingress URLs (Cloudflare tunnels / domain)

- SPA: **no change** (relative + `window.location.origin`).
- Server-advertised WS/OTA/front URLs: Admin console `sys_params` (DB), not env.
- WS tunnel: device resolves it at runtime via OTA — **no reflash** needed.
- ⚠ OTA + provisioning bootstrap URLs are compile-time in firmware — see below.

## E — Change Redis (password / host / port / image)

| Knob | env var | default |
|---|---|---|
| password | `REDIS_PASSWORD` | empty |
| host | `REDIS_HOST` | `tbot-esp32-server-redis` |
| port | `REDIS_PORT` | `6379` |
| image | `REDIS_IMAGE` | `redis:8.0` |

Only the Java manager-api uses Redis; frontend + Python need no change.

---

## Open hardening (not yet done — larger / riskier)

- **Firmware-baked URLs (highest risk):** the OTA-bootstrap host
  (`main/Kconfig.projbuild`) and `PROVISIONING_STATUS_URL` are in the flashed
  binary. Make the defaults a **stable** domain and add an NVS-first override so a
  VPS/tunnel move doesn't strand cleared-NVS devices. Requires a firmware rebuild +
  reflash for existing units.
- **tbot-server (Python) env overlays:** make `manager-api.url/secret`,
  `server.api_url` (course backend), and the public WS/OTA URLs read from env
  (`MANAGER_API_URL`, `COURSE_BACKEND_URL`, `PUBLIC_WS_URL`, `PUBLIC_OTA_URL`) in
  `config_loader`, so changing them is env, not editing `data/.config.yaml`.
- **Converge live prod onto compose** (kill the manual-run vs compose drift) so a
  redeploy is reproducible from one source of truth.
- **Rotate committed secrets:** dev MySQL `123456` + knife4j password in
  `application-dev.yml`, the qweather api_key in `tbot-server config.yaml`.
- **Spring profile:** set `SPRING_PROFILES_ACTIVE=prod` and add an
  `application-prod.yml` (no localhost fallback) so the dev profile can't silently
  mask a misconfigured prod datasource.
