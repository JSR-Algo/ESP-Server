#!/usr/bin/env bash
# T5.3 — bring up the E2E simulated stack (backend + ESP server, no hardware).
#
# Reproducible bring-up: builds the ESP server image from THIS checkout (never a
# stale local tag), starts the full compose project, renders the ESP config with the
# manager-api secret that the freshly seeded database actually generated, and
# provisions the simulated device.
#
# Usage:  ./up.sh [--rebuild]
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${HERE}/.." && pwd)"
ESP_REPO="$(cd -- "${DOCKER_DIR}/../.." && pwd)"
# Walk up to the TBOT umbrella rather than counting '..' segments: this checkout may
# be the canonical robot/esp32-server OR a git worktree two levels deeper, and a fixed
# relative hop silently resolves to the wrong directory in one of the two layouts.
TBOT_ROOT="${ESP_REPO}"
while [[ "${TBOT_ROOT}" != "/" && ! ( -d "${TBOT_ROOT}/tbot-backend" && -d "${TBOT_ROOT}/robot" ) ]]; do
  TBOT_ROOT="$(dirname "${TBOT_ROOT}")"
done
if [[ "${TBOT_ROOT}" == "/" ]]; then
  echo "[up] FATAL: could not locate the TBOT root (expected a dir holding tbot-backend/ and robot/)" >&2
  exit 1
fi

SIM_DEVICE_ID="${LESSON_SIM_DEVICE_ID:-14:c1:9f:d1:a8:48}"
SIM_IMAGE="${TBOT_LESSON_SIM_ESP_IMAGE:-local/tbot-server:lesson-e2e-sim}"
BACKEND_IMAGE="${TBOT_LESSON_STUDIO_BACKEND_IMAGE:-local/tbot-backend:lesson-studio-e2e}"
WEB_IMAGE="${TBOT_LESSON_STUDIO_WEB_IMAGE:-local/tbot-server-web:lesson-studio-e2e}"
BASE_IMAGE_OVERRIDE="${TBOT_SERVER_BASE_IMAGE:-}"
LOCAL_BASE_IMAGE="local/tbot-server-base:lesson-e2e-sim-$(git -C "${ESP_REPO}" rev-parse --short HEAD)"
BASE_IMAGE="${BASE_IMAGE_OVERRIDE:-${LOCAL_BASE_IMAGE}}"

export JWT_PUBLIC_KEY="${JWT_PUBLIC_KEY:-$(cat "${TBOT_ROOT}/tbot-backend/keys/dev-public.pem")}"
export TBOT_DEVICE_MINT_SECRET="${TBOT_DEVICE_MINT_SECRET:-lab-mint-58b6712d872ccec8}"
# The /tvideo-demo prefix is mandatory: a bare origin serves the SPA index.html and
# the canonical spec then sha256-hashes HTML instead of media.
export LESSON_ASSET_ORIGIN_BASE="${LESSON_ASSET_ORIGIN_BASE:-http://127.0.0.1:8102/tvideo-demo}"
export ROBOT_ESP_BASE_URL="${ROBOT_ESP_BASE_URL:-http://host.docker.internal:8013}"
# Absolute: compose resolves relative defaults against the FIRST -f file's directory,
# which silently points two levels too high when running from a git worktree.
export TBOT_BACKEND_WORKTREE="${TBOT_BACKEND_WORKTREE:-${TBOT_ROOT}/tbot-backend}"
export TBOT_FIRMWARE_WORKTREE="${TBOT_FIRMWARE_WORKTREE:-${TBOT_ROOT}/robot/TBOT-Firmware}"
export TBOT_LESSON_SIM_ESP_IMAGE="${SIM_IMAGE}"
export TBOT_LESSON_STUDIO_BACKEND_IMAGE="${BACKEND_IMAGE}"
export TBOT_LESSON_STUDIO_WEB_IMAGE="${WEB_IMAGE}"
export LESSON_SIM_DEVICE_ID="${SIM_DEVICE_ID}"

COMPOSE=(docker compose
  -f "${DOCKER_DIR}/docker-compose.lesson-studio-e2e.yml"
  -f "${DOCKER_DIR}/docker-compose.lesson-e2e-sim.yml")
RESOURCE_PREFIX="${LESSON_STUDIO_E2E_RESOURCE_PREFIX:-${COMPOSE_PROJECT_NAME:-${LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME:-tbot-ls-e2e}}}"

docker_build() {
  local attempt
  for attempt in 1 2 3; do
    if docker build "$@"; then
      return 0
    fi
    [[ "${attempt}" -lt 3 ]] || return 1
    echo "[up] Docker build failed; retrying (${attempt}/3)" >&2
    sleep 3
  done
}

start_backend_tier() {
  local redis_logs status
  set +e
  "${COMPOSE[@]}" up -d postgres redis mysql backend seed-postgres web seed-mysql
  status=$?
  set -e
  if [[ "${status}" -eq 0 ]]; then
    return 0
  fi

  redis_logs="$(docker logs "${RESOURCE_PREFIX}-redis" 2>&1 || true)"
  if [[ "${redis_logs}" != *"Bad file format reading the append only file"* ]]; then
    return "${status}"
  fi

  echo "[up] corrupted simulation Redis AOF detected; recreating only its local fixture volume" >&2
  "${COMPOSE[@]}" rm -sf redis
  docker volume rm "${RESOURCE_PREFIX}-redis-data"
  "${COMPOSE[@]}" up -d postgres redis mysql backend seed-postgres web seed-mysql
}

if [[ "${1:-}" == "--rebuild" ]] || ! docker image inspect "${BACKEND_IMAGE}" >/dev/null 2>&1; then
  echo "[up] building backend image ${BACKEND_IMAGE} from ${TBOT_BACKEND_WORKTREE}"
  docker_build -q -f "${TBOT_BACKEND_WORKTREE}/Dockerfile" \
    -t "${BACKEND_IMAGE}" "${TBOT_BACKEND_WORKTREE}" >/dev/null
fi

if [[ "${1:-}" == "--rebuild" ]] || ! docker image inspect "${WEB_IMAGE}" >/dev/null 2>&1; then
  echo "[up] building manager web/API image ${WEB_IMAGE} from ${ESP_REPO}"
  docker_build -q -f "${ESP_REPO}/Dockerfile-web" \
    --build-arg "WEB_NODE_IMAGE=node:20" \
    --build-arg "VUE_APP_NEST_AUTH_DISABLED=true" \
    -t "${WEB_IMAGE}" "${ESP_REPO}" >/dev/null
fi

if [[ "${1:-}" == "--rebuild" ]] || ! docker image inspect "${SIM_IMAGE}" >/dev/null 2>&1; then
  if [[ -z "${BASE_IMAGE_OVERRIDE}" ]]; then
    echo "[up] building checkout-local dependency image ${BASE_IMAGE}"
    docker_build -q -f "${ESP_REPO}/Dockerfile-server-base" \
      --build-arg "REQUIREMENTS_FILE=main/tbot-server/requirements.txt" \
      --build-arg "TBOT_FAST_GOOGLE_LIVE=1" \
      -t "${BASE_IMAGE}" "${ESP_REPO}" >/dev/null
  fi
  echo "[up] building ${SIM_IMAGE} from $(git -C "${ESP_REPO}" rev-parse --short HEAD)"
  docker_build -q -f "${ESP_REPO}/Dockerfile-server" \
    --build-arg "TBOT_SERVER_BASE_IMAGE=${BASE_IMAGE}" \
    -t "${SIM_IMAGE}" "${ESP_REPO}" >/dev/null
fi

echo "[up] starting backend tier"
start_backend_tier

echo "[up] waiting for manager-api"
until docker exec tbot-ls-e2e-mysql sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server -N -e "select 1"' >/dev/null 2>&1; do
  sleep 2
done

# server.secret is regenerated on every clean manager-api deploy, so it is read from
# the live database rather than pinned in a committed file.
SECRET="$(docker exec tbot-ls-e2e-mysql sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server -N -e "select param_value from sys_params where param_code=\"server.secret\";"' \
  | tr -d '\r')"
if [[ -z "${SECRET}" ]]; then
  echo "[up] FATAL: could not read sys_params server.secret" >&2
  exit 1
fi

echo "[up] rendering ESP config (device=${SIM_DEVICE_ID})"
mkdir -p "${HERE}/esp-data/lesson_asset_packs" "${HERE}/esp-data/lesson_assets"
sed -e "s|__MANAGER_SECRET__|${SECRET}|" -e "s|__SIM_DEVICE_ID__|${SIM_DEVICE_ID}|" \
  "${HERE}/config.template.yaml" > "${HERE}/esp-data/.config.yaml"
cp "${ESP_REPO}/main/tbot-server/agent-base-prompt.txt" "${HERE}/esp-data/" 2>/dev/null || true

# Device provisioning is split across TWO databases and BOTH are required. The
# manager-api (MySQL) row is what lets the ESP server accept the websocket; the
# backend (Postgres) `devices` row is what lets it mint a device token. Seeding
# only the first gets you a robot the ESP accepts and the backend disowns:
# POST /v1/internal/devices/mint-token answers 404 DEVICE_NOT_LINKED and the
# lesson dies at "backend identity unavailable" with no assignment ever pulled.
# That was F-T53-01, and it cost a whole session to find — do not drop either.
echo "[up] provisioning simulated device (manager-api / MySQL)"
# The device seed rewrites google_live_config_json wholesale, which silently WIPES a
# previously seeded API key on every re-run. Symptom: the stack comes up looking
# identical and the robot simply stops speaking. Carry the existing key across.
EXISTING_KEY="$(docker exec tbot-ls-e2e-mysql sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server -N -e "select coalesce(json_unquote(json_extract(google_live_config_json, \"$.api_key\")), \"\") from ai_agent where id=\"agent_e2e_sim_0001\";"' \
  2>/dev/null | tr -d '\r' || true)"
docker cp "${HERE}/seed-sim-device.sql" tbot-ls-e2e-mysql:/tmp/seed-sim-device.sql
docker exec tbot-ls-e2e-mysql sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server < /tmp/seed-sim-device.sql'
LESSON_SIM_GEMINI_API_KEY="${LESSON_SIM_GEMINI_API_KEY:-${EXISTING_KEY}}"

# Google Live credential. The agent's key lives in manager-api
# (ai_agent.google_live_config_json.api_key) -- that is where a real claimed robot
# reads it from, NOT an env var on the server. Seeding it is what lets the sim
# exercise the Live path for real: without it the provider initialises and then
# fails `Google Live unavailable type=auth`, so the robot never SPEAKS, and every
# checkpoint that needs audible output (the spoken start acknowledgement, the
# per-step prompt handoff) is unprovable in simulation.
#
# Never commit the key. Export it for the run:
#   export LESSON_SIM_GEMINI_API_KEY=...   # then ./up.sh
if [[ -n "${LESSON_SIM_GEMINI_API_KEY:-}" ]]; then
  echo "[up] seeding Google Live API key for the simulated agent"
  docker exec -i tbot-ls-e2e-mysql sh -lc \
    "MYSQL_PWD=123456 mysql -u root tbot_esp32_server -e \
     \"update ai_agent set google_live_config_json = json_set(google_live_config_json, '\\\$.api_key', '${LESSON_SIM_GEMINI_API_KEY}') where id='agent_e2e_sim_0001';\""
else
  echo "[up] WARNING: LESSON_SIM_GEMINI_API_KEY unset — Google Live will fail auth."
  echo "[up]          The lesson still runs, but the robot never speaks, so the"
  echo "[up]          audible-acknowledgement and step-prompt checkpoints cannot pass."
fi

echo "[up] provisioning simulated device (backend / Postgres)"
# Role is `tbot`, not `postgres` — the image is created with POSTGRES_USER=tbot.
docker exec -i tbot-ls-e2e-pg psql -U tbot -d tbot -v ON_ERROR_STOP=1 \
  < "${HERE}/seed-sim-backend.sql"

# manager-api caches the differentiated config it serves. Clear that cache after
# seeding and before ESP boot, because the ESP process snapshots the manager's base
# config at startup; restarting web afterwards leaves ESP holding stale handshake data.
docker restart tbot-ls-e2e-web >/dev/null

echo "[up] starting ESP lesson server"
"${COMPOSE[@]}" up -d esp-server

# Wait for the boot banner FROM THIS START, not "some time in the last 5 minutes". The
# banner is printed once at boot, so on a container that was already running (the
# common case when re-seeding config) `--since 5m` never matches and this loop spins
# forever. Anchor on the restart and give up loudly instead of hanging.
docker restart tbot-ls-e2e-esp >/dev/null
ESP_SINCE="$(date +%s)"
for _ in $(seq 1 60); do
  if docker logs --since "${ESP_SINCE}" tbot-ls-e2e-esp 2>&1 | grep -qE "WebsocketAddress|Traceback"; then
    break
  fi
  sleep 2
done
if ! docker logs --since "${ESP_SINCE}" tbot-ls-e2e-esp 2>&1 | grep -qE "WebsocketAddress|Traceback"; then
  echo "[up] FATAL: ESP server did not report a websocket address within 120s" >&2
  docker logs --tail 30 tbot-ls-e2e-esp >&2
  exit 1
fi

echo "[up] ready"
echo "  backend   http://127.0.0.1:3100/v1/health"
echo "  admin web http://127.0.0.1:8102/"
echo "  esp ws    ws://127.0.0.1:8010/tbot/v1/"
echo "  device    ${SIM_DEVICE_ID}"
