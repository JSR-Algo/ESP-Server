#!/usr/bin/env bash
set -euo pipefail

REMOTE_ROOT=""
RELEASE_DIR=""
TAG=""
CANDIDATE_ENV=""
MIN_FREE_BYTES="2147483648"
MIN_FREE_PERCENT="5"
SERVER_SERVICE="tbot-esp32-server"
DB_SERVICE="tbot-esp32-server-db"
WEB_SERVICE="tbot-esp32-server-web"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --remote-root) REMOTE_ROOT="$2"; shift 2 ;;
    --release-dir) RELEASE_DIR="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --candidate-env) CANDIDATE_ENV="$2"; shift 2 ;;
    --min-free-bytes) MIN_FREE_BYTES="$2"; shift 2 ;;
    --min-free-percent) MIN_FREE_PERCENT="$2"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${REMOTE_ROOT}" ]] || die "--remote-root is required"
[[ -n "${RELEASE_DIR}" ]] || die "--release-dir is required"
[[ -n "${TAG}" ]] || die "--tag is required"
[[ "${MIN_FREE_BYTES}" =~ ^[0-9]+$ ]] || die "--min-free-bytes must be an integer"
[[ "${MIN_FREE_PERCENT}" =~ ^[0-9]+$ ]] || die "--min-free-percent must be an integer"

ENV_PATH="${REMOTE_ROOT}/.env"
VALIDATOR="${RELEASE_DIR}/validate-env.py"
BACKUP_COMMAND="${TBOT_BACKUP_COMMAND:-${RELEASE_DIR}/backup-db.sh}"
SERVER_ARCHIVE="${RELEASE_DIR}/tbot-server-${TAG}.tar.gz"
COMPOSE_FILE="${RELEASE_DIR}/docker-compose.prod.yml"
SERVER_IMAGE_REF_FILE="${RELEASE_DIR}/server-image.ref"

[[ -f "${VALIDATOR}" ]] || die "release is missing validate-env.py"
[[ -f "${BACKUP_COMMAND}" ]] || die "release is missing backup-db.sh"
[[ -f "${SERVER_ARCHIVE}" ]] || die "release is missing server image archive"
[[ -f "${COMPOSE_FILE}" ]] || die "release is missing docker-compose.prod.yml"
[[ -f "${SERVER_IMAGE_REF_FILE}" ]] || die "release is missing server-image.ref"
SERVER_IMAGE_REF="$(tr -d '\r\n' <"${SERVER_IMAGE_REF_FILE}")"
[[ "${SERVER_IMAGE_REF}" == *:"${TAG}" && "${SERVER_IMAGE_REF}" != *[[:space:]]* ]] || die "server-image.ref does not match the release tag"
SERVER_IMAGE_REPO="${SERVER_IMAGE_REF%:*}"

[[ -f "${ENV_PATH}" ]] || die "remote env file is missing"
python3 "${VALIDATOR}" "${ENV_PATH}"
if [[ -n "${CANDIDATE_ENV}" ]]; then
  python3 "${VALIDATOR}" "${CANDIDATE_ENV}"
fi
DEPLOY_ENV="${CANDIDATE_ENV:-${ENV_PATH}}"
python3 "${VALIDATOR}" --expect TBOT_SERVER_IMAGE "${SERVER_IMAGE_REF}" "${DEPLOY_ENV}"

(
  cd "${RELEASE_DIR}"
  sha256sum -c checksums.sha256
)

CURRENT_COMPOSE_FILE="${REMOTE_ROOT}/current/docker-compose.prod.yml"
[[ -f "${CURRENT_COMPOSE_FILE}" ]] || die "current compose file is missing"
server_container_ids="$(docker compose --env-file "${ENV_PATH}" -f "${CURRENT_COMPOSE_FILE}" ps -q "${SERVER_SERVICE}")"
[[ -n "${server_container_ids}" ]] || die "cannot resolve active server containers"
active_image_ids=""
for container_id in ${server_container_ids}; do
  image_id="$(docker inspect --format '{{.Image}}' "${container_id}")"
  [[ -n "${image_id}" ]] || die "cannot resolve an active server image"
  case " ${active_image_ids} " in
    *" ${image_id} "*) ;;
    *) active_image_ids="${active_image_ids:+${active_image_ids} }${image_id}" ;;
  esac
done

is_active_image() {
  local candidate active
  candidate="$1"
  for active in ${active_image_ids}; do
    [[ "${candidate}" == "${active}" ]] && return 0
  done
  return 1
}

read_disk_space() {
  local disk_line free_kb used_percent
  disk_line="$(df -Pk "${REMOTE_ROOT}" | awk 'END { print $4, $5 }')"
  free_kb="${disk_line%% *}"
  used_percent="${disk_line##* }"
  used_percent="${used_percent%%%}"
  [[ "${free_kb}" =~ ^[0-9]+$ && "${used_percent}" =~ ^[0-9]+$ ]] || die "cannot parse root filesystem free space"
  free_bytes=$((free_kb * 1024))
  free_percent=$((100 - used_percent))
}

read_disk_space
if (( free_bytes < MIN_FREE_BYTES || free_percent < MIN_FREE_PERCENT )); then
  rollback_image=""
  image_ids="$(docker image ls --no-trunc --format '{{.ID}} {{.CreatedAt}}' "${SERVER_IMAGE_REPO}" | awk '!seen[$1]++ { print $1 }')"
  while IFS= read -r image_id; do
    [[ -n "${image_id}" ]] || continue
    if ! is_active_image "${image_id}" && [[ -z "${rollback_image}" ]]; then
      rollback_image="${image_id}"
      continue
    fi
    if is_active_image "${image_id}" || [[ "${image_id}" == "${rollback_image}" ]]; then
      continue
    fi
    if [[ -n "$(docker ps -aq --filter "ancestor=${image_id}")" ]]; then
      printf 'Preserving image used by a container: %s\n' "${image_id}"
      continue
    fi
    docker image rm "${image_id}"
  done <<EOF
${image_ids}
EOF
  read_disk_space
fi
if (( free_bytes < MIN_FREE_BYTES || free_percent < MIN_FREE_PERCENT )); then
  die "free-space gate failed (free_bytes=${free_bytes}, free_percent=${free_percent})"
fi
printf 'Free-space gate passed: %s bytes, %s%% free\n' "${free_bytes}" "${free_percent}"

db_before="$(docker inspect --format '{{.Id}}' "${DB_SERVICE}")"
web_before="$(docker inspect --format '{{.Id}}' "${WEB_SERVICE}")"
[[ -n "${db_before}" && -n "${web_before}" ]] || die "cannot snapshot protected container IDs"

verify_protected_ids() {
  local db_after web_after
  db_after="$(docker inspect --format '{{.Id}}' "${DB_SERVICE}")"
  web_after="$(docker inspect --format '{{.Id}}' "${WEB_SERVICE}")"
  [[ "${db_after}" == "${db_before}" ]] || die "database container ID changed"
  [[ "${web_after}" == "${web_before}" ]] || die "web container ID changed"
}

mkdir -p "${REMOTE_ROOT}/backups"
TBOT_BACKUP_DIR="${REMOTE_ROOT}/backups" "${BACKUP_COMMAND}"

if [[ -n "${CANDIDATE_ENV}" ]]; then
  env_backup="${REMOTE_ROOT}/.env.rollback-$(date -u +%Y%m%dT%H%M%SZ)-${TAG}"
  install -m 600 "${ENV_PATH}" "${env_backup}"
  printf '%s\n' "${env_backup}" >"${RELEASE_DIR}/env-backup-path"
  install -m 600 "${CANDIDATE_ENV}" "${ENV_PATH}"
fi

gunzip -c "${SERVER_ARCHIVE}" | docker load
if [[ -e "${REMOTE_ROOT}/current" && ! -L "${REMOTE_ROOT}/current" ]]; then
  die "refusing to replace non-symlink current directory"
fi
ln -sfn "${RELEASE_DIR}" "${REMOTE_ROOT}/current"

if ! docker compose --env-file "${ENV_PATH}" -f "${REMOTE_ROOT}/current/docker-compose.prod.yml" up -d --no-deps "${SERVER_SERVICE}"; then
  verify_protected_ids
  die "server-only compose recreate failed"
fi
if [[ "${TBOT_DEPLOY_SKIP_HEALTH_WAIT:-0}" != "1" ]]; then
  attempt=0
  while (( attempt < 90 )); do
    ids="$(docker compose --env-file "${ENV_PATH}" -f "${REMOTE_ROOT}/current/docker-compose.prod.yml" ps -q "${SERVER_SERVICE}")"
    healthy=0
    count=0
    for id in ${ids}; do
      count=$((count + 1))
      [[ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${id}")" == healthy ]] && healthy=$((healthy + 1))
    done
    if (( count > 0 && count == healthy )); then break; fi
    attempt=$((attempt + 1))
    sleep 2
  done
  if (( count == 0 || count != healthy )); then
    verify_protected_ids
    die "server health wait failed"
  fi
fi

verify_protected_ids
printf 'Server-only deploy complete; protected container IDs unchanged\n'
