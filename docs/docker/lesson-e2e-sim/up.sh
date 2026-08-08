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
# Dependency layer only; application code is overlaid from this checkout below.
BASE_IMAGE="${TBOT_SERVER_BASE_IMAGE:-local/tbot-server:main-dd48f39d-local-20260805}"

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
export LESSON_SIM_DEVICE_ID="${SIM_DEVICE_ID}"

COMPOSE=(docker compose
  -f "${DOCKER_DIR}/docker-compose.lesson-studio-e2e.yml"
  -f "${DOCKER_DIR}/docker-compose.lesson-e2e-sim.yml")

if [[ "${1:-}" == "--rebuild" ]] || ! docker image inspect "${SIM_IMAGE}" >/dev/null 2>&1; then
  echo "[up] building ${SIM_IMAGE} from $(git -C "${ESP_REPO}" rev-parse --short HEAD)"
  docker build -q -f "${ESP_REPO}/Dockerfile-server" \
    --build-arg "TBOT_SERVER_BASE_IMAGE=${BASE_IMAGE}" \
    -t "${SIM_IMAGE}" "${ESP_REPO}" >/dev/null
fi

echo "[up] starting backend tier"
"${COMPOSE[@]}" up -d postgres redis mysql backend seed-postgres web seed-mysql

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
docker cp "${HERE}/seed-sim-device.sql" tbot-ls-e2e-mysql:/tmp/seed-sim-device.sql
docker exec tbot-ls-e2e-mysql sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server < /tmp/seed-sim-device.sql'

echo "[up] provisioning simulated device (backend / Postgres)"
# Role is `tbot`, not `postgres` — the image is created with POSTGRES_USER=tbot.
docker exec -i tbot-ls-e2e-pg psql -U tbot -d tbot -v ON_ERROR_STOP=1 \
  < "${HERE}/seed-sim-backend.sql"

echo "[up] starting ESP lesson server"
"${COMPOSE[@]}" up -d esp-server

until docker logs --since 5m tbot-ls-e2e-esp 2>&1 | grep -qE "WebsocketAddress|Traceback"; do
  sleep 2
done

echo "[up] ready"
echo "  backend   http://127.0.0.1:3100/v1/health"
echo "  admin web http://127.0.0.1:8102/"
echo "  esp ws    ws://127.0.0.1:8010/tbot/v1/"
echo "  device    ${SIM_DEVICE_ID}"
