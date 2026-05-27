#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TAG=""
SERVER_IMAGE="local/tbot-server"
WEB_IMAGE="local/tbot-server-web"
OUT_ROOT="${PROJECT_DIR}/dist/deploy"

usage() {
  cat <<'USAGE'
Usage: package-release.sh --tag <tag> [options]

Save local Docker images and release metadata to esp32-server/dist/deploy/<tag>/.

Options:
  --tag <tag>              Required image tag.
  --server-image <name>    Server image repo (default: local/tbot-server).
  --web-image <name>       Web/admin image repo (default: local/tbot-server-web).
  --out-dir <dir>          Release output root (default: esp32-server/dist/deploy).
  -h, --help               Show help.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

git_sha() {
  if git -C "${PROJECT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${PROJECT_DIR}" rev-parse HEAD
  else
    printf 'unknown'
  fi
}

checksum_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_compose() {
  local path="$1"
  cat >"${path}" <<'YAML'
services:
  tbot-esp32-server:
    image: ${TBOT_SERVER_IMAGE}
    container_name: tbot-esp32-server
    depends_on:
      - tbot-esp32-server-db
      - tbot-esp32-server-redis
    restart: always
    ports:
      - "8000:8000"
      - "8003:8003"
    security_opt:
      - seccomp:unconfined
    environment:
      TZ: ${TZ:-Asia/Ho_Chi_Minh}
    volumes:
      - /opt/tbot/data:/opt/tbot-esp32-server/data
      - /opt/tbot/models/SenseVoiceSmall/model.pt:/opt/tbot-esp32-server/models/SenseVoiceSmall/model.pt

  tbot-esp32-server-web:
    image: ${TBOT_WEB_IMAGE}
    container_name: tbot-esp32-server-web
    restart: always
    depends_on:
      tbot-esp32-server-db:
        condition: service_healthy
      tbot-esp32-server-redis:
        condition: service_healthy
    ports:
      - "8002:8002"
    environment:
      TZ: ${TZ:-Asia/Ho_Chi_Minh}
      SPRING_DATASOURCE_DRUID_URL: jdbc:mysql://tbot-esp32-server-db:3306/tbot_esp32_server?useUnicode=true&characterEncoding=UTF-8&serverTimezone=Asia/Ho_Chi_Minh&nullCatalogMeansCurrent=true&connectTimeout=30000&socketTimeout=30000&autoReconnect=true&failOverReadOnly=false&maxReconnects=10
      SPRING_DATASOURCE_DRUID_USERNAME: ${MYSQL_USER:-root}
      SPRING_DATASOURCE_DRUID_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      SPRING_DATA_REDIS_HOST: tbot-esp32-server-redis
      SPRING_DATA_REDIS_PASSWORD: ${REDIS_PASSWORD:-}
      SPRING_DATA_REDIS_PORT: 6379
    volumes:
      - /opt/tbot/uploadfile:/uploadfile

  tbot-esp32-server-db:
    image: mysql:8
    container_name: tbot-esp32-server-db
    restart: always
    expose:
      - "3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-p${MYSQL_ROOT_PASSWORD}"]
      timeout: 45s
      interval: 10s
      retries: 10
    environment:
      TZ: ${TZ:-Asia/Ho_Chi_Minh}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DATABASE:-tbot_esp32_server}
      MYSQL_INITDB_ARGS: "--character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci"
    volumes:
      - /opt/tbot/mysql/data:/var/lib/mysql

  tbot-esp32-server-redis:
    image: redis:8.0
    container_name: tbot-esp32-server-redis
    restart: always
    expose:
      - "6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
YAML
}

write_env_example() {
  local path="$1"
  local source_env="${SCRIPT_DIR}/.env.example"
  if [[ -f "${source_env}" ]]; then
    sed \
      -e "s#^TBOT_SERVER_IMAGE=.*#TBOT_SERVER_IMAGE=${SERVER_IMAGE}:${TAG}#" \
      -e "s#^TBOT_WEB_IMAGE=.*#TBOT_WEB_IMAGE=${WEB_IMAGE}:${TAG}#" \
      -e "s#REPLACE_WITH_RELEASE_TAG#${TAG}#g" \
      "${source_env}" >"${path}"
    return 0
  fi
  cat >"${path}" <<EOF
TBOT_SERVER_IMAGE=${SERVER_IMAGE}:${TAG}
TBOT_WEB_IMAGE=${WEB_IMAGE}:${TAG}
TZ=Asia/Ho_Chi_Minh
MYSQL_ROOT_PASSWORD=change-me
MYSQL_USER=root
MYSQL_DATABASE=tbot_esp32_server
REDIS_PASSWORD=
EOF
}

while (($#)); do
  case "$1" in
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      TAG="$2"
      shift 2
      ;;
    --server-image)
      [[ $# -ge 2 ]] || die "--server-image requires a value"
      SERVER_IMAGE="$2"
      shift 2
      ;;
    --web-image)
      [[ $# -ge 2 ]] || die "--web-image requires a value"
      WEB_IMAGE="$2"
      shift 2
      ;;
    --out-dir)
      [[ $# -ge 2 ]] || die "--out-dir requires a value"
      OUT_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "${TAG}" ]] || die "--tag is required"
need_cmd docker
need_cmd gzip
docker image inspect "${SERVER_IMAGE}:${TAG}" >/dev/null 2>&1 || die "missing image: ${SERVER_IMAGE}:${TAG}"
docker image inspect "${WEB_IMAGE}:${TAG}" >/dev/null 2>&1 || die "missing image: ${WEB_IMAGE}:${TAG}"

RELEASE_DIR="${OUT_ROOT}/${TAG}"
mkdir -p "${RELEASE_DIR}"

SERVER_TAR="tbot-server-${TAG}.tar.gz"
WEB_TAR="tbot-server-web-${TAG}.tar.gz"

printf 'Saving images to %s\n' "${RELEASE_DIR}"
docker save "${SERVER_IMAGE}:${TAG}" | gzip -c >"${RELEASE_DIR}/${SERVER_TAR}"
docker save "${WEB_IMAGE}:${TAG}" | gzip -c >"${RELEASE_DIR}/${WEB_TAR}"

if [[ -f "${SCRIPT_DIR}/docker-compose.prod.yml" ]]; then
  cp "${SCRIPT_DIR}/docker-compose.prod.yml" "${RELEASE_DIR}/docker-compose.prod.yml"
else
  write_compose "${RELEASE_DIR}/docker-compose.prod.yml"
fi
write_env_example "${RELEASE_DIR}/.env.example"

SERVER_SHA="$(checksum_file "${RELEASE_DIR}/${SERVER_TAR}")"
WEB_SHA="$(checksum_file "${RELEASE_DIR}/${WEB_TAR}")"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_SHA="$(git_sha)"

cat >"${RELEASE_DIR}/checksums.sha256" <<EOF
${SERVER_SHA}  ${SERVER_TAR}
${WEB_SHA}  ${WEB_TAR}
EOF

cat >"${RELEASE_DIR}/release.json" <<EOF
{
  "tag": "$(json_escape "${TAG}")",
  "gitSha": "$(json_escape "${GIT_SHA}")",
  "builtAt": "$(json_escape "${BUILD_TIME}")",
  "images": {
    "server": "$(json_escape "${SERVER_IMAGE}:${TAG}")",
    "web": "$(json_escape "${WEB_IMAGE}:${TAG}")"
  },
  "artifacts": {
    "server": {
      "file": "$(json_escape "${SERVER_TAR}")",
      "sha256": "$(json_escape "${SERVER_SHA}")"
    },
    "web": {
      "file": "$(json_escape "${WEB_TAR}")",
      "sha256": "$(json_escape "${WEB_SHA}")"
    }
  }
}
EOF

printf 'Packaged release: %s\n' "${RELEASE_DIR}"
