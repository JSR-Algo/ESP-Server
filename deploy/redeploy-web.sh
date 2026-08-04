#!/usr/bin/env bash
set -euo pipefail

# Swap ONLY the web/admin container (tbot-esp32-server-web) to a new image,
# leaving MySQL, Redis, and the Python server running. Reads the SAME env file
# that docker-compose.prod.yml uses (/opt/tbot/.env) so the manual web run cannot
# drift from compose: SPRING_* (incl. the Redis password), NESTJS_* upstream, and
# any external-MySQL knobs all come from one source of truth.
#
# Pattern: save current image (rollback point) -> load/pull new image ->
# stop + rename old container -> run new -> healthcheck :8002 -> rollback on fail.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../docs/docker/cms-authority.sh"

ENV_FILE="/opt/tbot/.env"
TAG=""
IMAGE=""
IMAGE_TAR=""
ADMIN_PORT=""
HEALTH_RETRIES="30"
HEALTH_INTERVAL="2"
CONTAINER="tbot-esp32-server-web"
NETWORK="tbot"

usage() {
  cat <<'USAGE'
Usage: redeploy-web.sh [--tag <tag> | --image <ref> | --image-tar <file>] [options]

Swap only the web/admin container to a new image, with healthcheck + rollback.
The rest of the stack (db, redis, python server) is left untouched.

Image selection (pick one; defaults to TBOT_WEB_IMAGE from the env file):
  --tag <tag>           Use <repo>:<tag>, where <repo> is TBOT_WEB_IMAGE's repo.
  --image <ref>         Use an explicit image reference (repo:tag).
  --image-tar <file>    docker load a tarball, then use TBOT_WEB_IMAGE from env.

Options:
  --env-file <file>     Env file shared with compose (default: /opt/tbot/.env).
  --admin-port <port>   Host admin port for the healthcheck (default: TBOT_ADMIN_PORT or 8002).
  --health-retries <n>  Healthcheck attempts before rollback (default: 30).
  --health-interval <s> Seconds between healthcheck attempts (default: 2).
  -h, --help            Show help.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

env_value() {
  # Read KEY from ENV_FILE (strips surrounding quotes); empty if unset/missing.
  local key="$1"
  [[ -r "${ENV_FILE}" ]] || return 0
  local value
  value="$(awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "${ENV_FILE}")"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value}"
}

image_repo() {
  # Strip the :tag (if any) from an image ref, preserving a registry host:port.
  local ref="$1"
  local last="${ref##*/}"
  if [[ "${last}" == *:* ]]; then
    printf '%s' "${ref%:*}"
  else
    printf '%s' "${ref}"
  fi
}

while (($#)); do
  case "$1" in
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      TAG="$2"; shift 2 ;;
    --image)
      [[ $# -ge 2 ]] || die "--image requires a value"
      IMAGE="$2"; shift 2 ;;
    --image-tar)
      [[ $# -ge 2 ]] || die "--image-tar requires a value"
      IMAGE_TAR="$2"; shift 2 ;;
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file requires a value"
      ENV_FILE="$2"; shift 2 ;;
    --admin-port)
      [[ $# -ge 2 ]] || die "--admin-port requires a value"
      ADMIN_PORT="$2"; shift 2 ;;
    --health-retries)
      [[ $# -ge 2 ]] || die "--health-retries requires a value"
      HEALTH_RETRIES="$2"; shift 2 ;;
    --health-interval)
      [[ $# -ge 2 ]] || die "--health-interval requires a value"
      HEALTH_INTERVAL="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      die "unknown argument: $1" ;;
  esac
done

need_cmd docker
need_cmd curl
[[ -r "${ENV_FILE}" ]] || die "cannot read env file: ${ENV_FILE} (set --env-file)"

# --- Resolve config from the shared env file (compose's source of truth) -------
ENV_WEB_IMAGE="$(env_value TBOT_WEB_IMAGE)"
TZ_VALUE="$(env_value TZ)"; TZ_VALUE="${TZ_VALUE:-Asia/Ho_Chi_Minh}"

MYSQL_ROOT_PASSWORD="$(env_value MYSQL_ROOT_PASSWORD)"
MYSQL_HOST="$(env_value MYSQL_HOST)"; MYSQL_HOST="${MYSQL_HOST:-tbot-esp32-server-db}"
MYSQL_PORT="$(env_value MYSQL_PORT)"; MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="$(env_value MYSQL_DATABASE)"; MYSQL_DATABASE="${MYSQL_DATABASE:-tbot_esp32_server}"
MYSQL_USER="$(env_value MYSQL_USER)"; MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="$(env_value MYSQL_PASSWORD)"; MYSQL_PASSWORD="${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD}}"
MYSQL_SERVER_TIMEZONE="$(env_value MYSQL_SERVER_TIMEZONE)"; MYSQL_SERVER_TIMEZONE="${MYSQL_SERVER_TIMEZONE:-Asia/Ho_Chi_Minh}"

REDIS_HOST="$(env_value REDIS_HOST)"; REDIS_HOST="${REDIS_HOST:-tbot-esp32-server-redis}"
REDIS_PORT="$(env_value REDIS_PORT)"; REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="$(env_value REDIS_PASSWORD)"

NESTJS_UPSTREAM_HOST="$(env_value NESTJS_UPSTREAM_HOST)"; NESTJS_UPSTREAM_HOST="${NESTJS_UPSTREAM_HOST:-tbot-backend-8wmh.onrender.com}"
NESTJS_UPSTREAM_SCHEME="$(env_value NESTJS_UPSTREAM_SCHEME)"; NESTJS_UPSTREAM_SCHEME="${NESTJS_UPSTREAM_SCHEME:-https}"
NESTJS_TOKEN="$(env_value NESTJS_TOKEN)"
NESTJS_ADMIN_PROXY_KEY="$(env_value NESTJS_ADMIN_PROXY_KEY)"
PUBLIC_CMS_UPSTREAM_HOST="$(env_value PUBLIC_CMS_UPSTREAM_HOST)"
PUBLIC_CMS_UPSTREAM_SCHEME="$(env_value PUBLIC_CMS_UPSTREAM_SCHEME)"
TBOT_ALLOW_SPLIT_CMS_AUTHORITY="$(env_value TBOT_ALLOW_SPLIT_CMS_AUTHORITY)"
configure_cms_authority || die "CMS authority configuration is not production-safe"

REMOTE_ROOT="$(env_value TBOT_REMOTE_ROOT)"; REMOTE_ROOT="${REMOTE_ROOT:-/opt/tbot}"

if [[ -z "${ADMIN_PORT}" ]]; then
  ADMIN_PORT="$(env_value TBOT_ADMIN_PORT)"; ADMIN_PORT="${ADMIN_PORT:-8002}"
fi

[[ -n "${MYSQL_PASSWORD}" ]] || die "no MySQL password: set MYSQL_ROOT_PASSWORD (or MYSQL_PASSWORD) in ${ENV_FILE}"

# --- Determine the NEW web image ----------------------------------------------
if [[ -n "${IMAGE}" ]]; then
  NEW_IMAGE="${IMAGE}"
elif [[ -n "${TAG}" ]]; then
  [[ -n "${ENV_WEB_IMAGE}" ]] || die "cannot derive repo for --tag: TBOT_WEB_IMAGE unset in ${ENV_FILE}"
  NEW_IMAGE="$(image_repo "${ENV_WEB_IMAGE}"):${TAG}"
elif [[ -n "${IMAGE_TAR}" ]]; then
  [[ -r "${IMAGE_TAR}" ]] || die "cannot read image tar: ${IMAGE_TAR}"
  printf 'Loading web image from %s\n' "${IMAGE_TAR}"
  case "${IMAGE_TAR}" in
    *.gz) gunzip -c "${IMAGE_TAR}" | docker load ;;
    *)    docker load -i "${IMAGE_TAR}" ;;
  esac
  [[ -n "${ENV_WEB_IMAGE}" ]] || die "TBOT_WEB_IMAGE unset in ${ENV_FILE}; cannot pick loaded image"
  NEW_IMAGE="${ENV_WEB_IMAGE}"
else
  [[ -n "${ENV_WEB_IMAGE}" ]] || die "no image selected and TBOT_WEB_IMAGE unset in ${ENV_FILE}"
  NEW_IMAGE="${ENV_WEB_IMAGE}"
fi

printf 'Redeploying %s -> %s\n' "${CONTAINER}" "${NEW_IMAGE}"

# --- Save current image as a rollback point -----------------------------------
OLD_IMAGE=""
OLD_BACKUP=""
if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
  OLD_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${CONTAINER}")"
  OLD_BACKUP="${CONTAINER}-prev-$(date +%Y%m%d%H%M%S)"
  printf 'Current image: %s (renaming old container -> %s as rollback point)\n' "${OLD_IMAGE}" "${OLD_BACKUP}"
fi

# --- Pull the new image when it is not already present locally -----------------
if ! docker image inspect "${NEW_IMAGE}" >/dev/null 2>&1; then
  printf 'Pulling %s\n' "${NEW_IMAGE}"
  docker pull "${NEW_IMAGE}"
fi

# --- Stop + rename the old container (keep it for rollback) --------------------
if [[ -n "${OLD_BACKUP}" ]]; then
  docker rm -f "${OLD_BACKUP}" 2>/dev/null || true
  docker stop "${CONTAINER}" >/dev/null
  docker rename "${CONTAINER}" "${OLD_BACKUP}"
fi

mkdir -p "${REMOTE_ROOT}/uploadfile"

run_web() {
  # $1 = container name, $2 = image ref. Same env wiring as docker-compose.prod.yml.
  docker run -d --name "$1" --restart unless-stopped \
    --network "${NETWORK}" \
    -e "TZ=${TZ_VALUE}" \
    -e "SPRING_DATASOURCE_DRUID_URL=jdbc:mysql://${MYSQL_HOST}:${MYSQL_PORT}/${MYSQL_DATABASE}?useUnicode=true&characterEncoding=UTF-8&serverTimezone=${MYSQL_SERVER_TIMEZONE}&nullCatalogMeansCurrent=true&connectTimeout=30000&socketTimeout=30000&autoReconnect=true&failOverReadOnly=false&maxReconnects=10" \
    -e "SPRING_DATASOURCE_DRUID_USERNAME=${MYSQL_USER}" \
    -e "SPRING_DATASOURCE_DRUID_PASSWORD=${MYSQL_PASSWORD}" \
    -e "SPRING_DATA_REDIS_HOST=${REDIS_HOST}" \
    -e "SPRING_DATA_REDIS_PASSWORD=${REDIS_PASSWORD}" \
    -e "SPRING_DATA_REDIS_PORT=${REDIS_PORT}" \
    -e "NESTJS_UPSTREAM_HOST=${NESTJS_UPSTREAM_HOST}" \
    -e "NESTJS_UPSTREAM_SCHEME=${NESTJS_UPSTREAM_SCHEME}" \
    -e "NESTJS_TOKEN=${NESTJS_TOKEN}" \
    -e "NESTJS_ADMIN_PROXY_KEY=${NESTJS_ADMIN_PROXY_KEY}" \
    -e "PUBLIC_CMS_UPSTREAM_HOST=${PUBLIC_CMS_UPSTREAM_HOST}" \
    -e "PUBLIC_CMS_UPSTREAM_SCHEME=${PUBLIC_CMS_UPSTREAM_SCHEME}" \
    -e "TBOT_ALLOW_SPLIT_CMS_AUTHORITY=${TBOT_ALLOW_SPLIT_CMS_AUTHORITY}" \
    -p "${ADMIN_PORT}:8002" \
    -v "${REMOTE_ROOT}/uploadfile":/uploadfile \
    "$2" >/dev/null
}

healthcheck() {
  # Admin app answers on :8002. Any HTTP response (incl. 401) means it is up.
  local i
  for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl --silent --show-error --output /dev/null --max-time 5 \
        "http://127.0.0.1:${ADMIN_PORT}/"; then
      return 0
    fi
    sleep "${HEALTH_INTERVAL}"
  done
  return 1
}

rollback() {
  printf 'Healthcheck failed; rolling back to previous web container\n' >&2
  docker rm -f "${CONTAINER}" 2>/dev/null || true
  if [[ -n "${OLD_BACKUP}" ]]; then
    docker rename "${OLD_BACKUP}" "${CONTAINER}"
    docker start "${CONTAINER}" >/dev/null
  fi
}

# --- Run new -> healthcheck -> rollback on failure ----------------------------
docker rm -f "${CONTAINER}" 2>/dev/null || true
run_web "${CONTAINER}" "${NEW_IMAGE}"

if healthcheck; then
  printf 'Healthcheck OK on :%s\n' "${ADMIN_PORT}"
  if [[ -n "${OLD_BACKUP}" ]]; then
    docker rm -f "${OLD_BACKUP}" >/dev/null 2>&1 || true
    printf 'Removed rollback container %s\n' "${OLD_BACKUP}"
  fi
  docker inspect "${CONTAINER}" \
    --format '{{.Name}} {{.Config.Image}} {{.State.Status}} restart={{.State.Restarting}}'
  printf 'Web redeploy complete: %s\n' "${NEW_IMAGE}"
else
  rollback
  die "web redeploy rolled back; old container ${OLD_BACKUP:-<none>} restored. New image was ${NEW_IMAGE}"
fi
