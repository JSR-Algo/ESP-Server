#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TAG=""
SERVER_IMAGE="local/tbot-server"
WEB_IMAGE="local/tbot-server-web"
SERVER_BASE_IMAGE="local/tbot-server-base"
REMOTE_SERVER_BASE_REF="dinhmanh11/tbot-server-base:vps-20260525144756"
SERVER_REQUIREMENTS_FILE="main/tbot-server/requirements.txt"
NO_LATEST=0
PLATFORM=""
BUILD_BASE=0
FAST_GOOGLE_LIVE=0
FAST_WEB=1
ONLY="all"
VUE_APP_NEST_AUTH_DISABLED="${VUE_APP_NEST_AUTH_DISABLED:-false}"

usage() {
  cat <<'USAGE'
Usage: build-local.sh --tag <tag> [options]

Build TBOT Docker images locally. Nothing is pushed.

Options:
  --tag <tag>              Required image tag.
  --server-image <name>    Server image repo (default: local/tbot-server).
  --server-base-image <n>  Server base image repo (default: local/tbot-server-base).
  --server-base-ref <ref>  Remote base image when --build-base is not used.
  --server-requirements-file <path>
                           Requirements file copied into the server base image.
  --web-image <name>       Web/admin image repo (default: local/tbot-server-web).
  --platform <platform>    Optional Docker platform, for example linux/amd64.
  --build-base             Build the server base image locally first.
  --fast-google-live       Skip heavy optional local ASR/model deps in the base image.
  --docker-web-build       Build web/admin fully inside Docker instead of fast prebuilt artifacts.
  --only <all|server|web>  Build only one side (default: all).
  --no-latest              Do not tag latest-local convenience aliases.
  -h, --help               Show help.

Environment:
  VUE_APP_NEST_AUTH_DISABLED
                           Set to true only for the Cloudflare Access-protected
                           admin release (default: false).
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

run_manager_web_build() {
  printf 'Building manager web assets locally\n'
  if command -v npm >/dev/null 2>&1; then
    (cd "${PROJECT_DIR}/main/manager-web" && npm ci && VUE_APP_NEST_AUTH_DISABLED="${VUE_APP_NEST_AUTH_DISABLED}" npm run build)
  else
    docker run --rm \
      -e "VUE_APP_NEST_AUTH_DISABLED=${VUE_APP_NEST_AUTH_DISABLED}" \
      -v "${PROJECT_DIR}/main/manager-web:/app" \
      -v tbot-manager-web-npm-cache:/root/.npm \
      -w /app \
      node:18 \
      sh -lc 'npm ci && npm run build'
  fi
}

run_manager_api_build() {
  printf 'Building manager API jar locally\n'
  if [[ "${TBOT_USE_HOST_MVN:-0}" == "1" ]] && command -v mvn >/dev/null 2>&1; then
    mvn -f "${PROJECT_DIR}/main/manager-api/pom.xml" -Dmaven.test.skip=true package
  else
    M2_CACHE_DIR="${TBOT_M2_CACHE_DIR:-${HOME}/.m2}"
    mkdir -p "${M2_CACHE_DIR}"
    docker run --rm \
      -v "${PROJECT_DIR}/main/manager-api:/app" \
      -v "${M2_CACHE_DIR}:/root/.m2" \
      -w /app \
      maven:3.9.4-eclipse-temurin-21 \
      mvn -Dmaven.test.skip=true package
  fi
}

git_sha() {
  if git -C "${PROJECT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${PROJECT_DIR}" rev-parse --short=12 HEAD
  else
    printf 'unknown'
  fi
}

git_state() {
  if git -C "${PROJECT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [[ -n "$(git -C "${PROJECT_DIR}" status --porcelain)" ]]; then
      printf 'dirty'
    else
      printf 'clean'
    fi
  else
    printf 'not-a-git-worktree'
  fi
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
    --server-base-image)
      [[ $# -ge 2 ]] || die "--server-base-image requires a value"
      SERVER_BASE_IMAGE="$2"
      shift 2
      ;;
    --server-base-ref)
      [[ $# -ge 2 ]] || die "--server-base-ref requires a value"
      REMOTE_SERVER_BASE_REF="$2"
      shift 2
      ;;
    --server-requirements-file)
      [[ $# -ge 2 ]] || die "--server-requirements-file requires a value"
      SERVER_REQUIREMENTS_FILE="$2"
      shift 2
      ;;
    --web-image)
      [[ $# -ge 2 ]] || die "--web-image requires a value"
      WEB_IMAGE="$2"
      shift 2
      ;;
    --platform)
      [[ $# -ge 2 ]] || die "--platform requires a value"
      PLATFORM="$2"
      shift 2
      ;;
    --build-base)
      BUILD_BASE=1
      shift
      ;;
    --fast-google-live)
      FAST_GOOGLE_LIVE=1
      shift
      ;;
    --docker-web-build)
      FAST_WEB=0
      shift
      ;;
    --only)
      [[ $# -ge 2 ]] || die "--only requires a value"
      ONLY="$2"
      [[ "${ONLY}" == "all" || "${ONLY}" == "server" || "${ONLY}" == "web" ]] || die "--only must be all, server, or web"
      shift 2
      ;;
    --no-latest)
      NO_LATEST=1
      shift
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
[[ "${VUE_APP_NEST_AUTH_DISABLED}" == "true" || "${VUE_APP_NEST_AUTH_DISABLED}" == "false" ]] \
  || die "VUE_APP_NEST_AUTH_DISABLED must be true or false"
need_cmd docker
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"

SHA="$(git_sha)"
STATE="$(git_state)"
printf 'Building local TBOT images\n'
printf 'Project: %s\n' "${PROJECT_DIR}"
printf 'Git SHA: %s\n' "${SHA}"
printf 'Git state: %s\n' "${STATE}"
printf 'Tag: %s\n' "${TAG}"
printf 'Nest auth disabled in manager web: %s\n' "${VUE_APP_NEST_AUTH_DISABLED}"

BUILD_ARGS=()
if [[ -n "${PLATFORM}" ]]; then
  BUILD_ARGS+=(--platform "${PLATFORM}")
  printf 'Platform: %s\n' "${PLATFORM}"
fi

docker_build() {
  if [[ "${#BUILD_ARGS[@]}" -gt 0 ]]; then
    docker build "${BUILD_ARGS[@]}" "$@"
  else
    docker build "$@"
  fi
}

if [[ "${ONLY}" == "all" || "${ONLY}" == "server" ]]; then
  if [[ "${BUILD_BASE}" -eq 1 ]]; then
    docker_build \
      --pull \
      -f "${PROJECT_DIR}/Dockerfile-server-base" \
      --build-arg "REQUIREMENTS_FILE=${SERVER_REQUIREMENTS_FILE}" \
      --build-arg "TBOT_FAST_GOOGLE_LIVE=${FAST_GOOGLE_LIVE}" \
      -t "${SERVER_BASE_IMAGE}:${TAG}" \
      "${PROJECT_DIR}"
    SERVER_BASE_REF="${SERVER_BASE_IMAGE}:${TAG}"
  else
    SERVER_BASE_REF="${REMOTE_SERVER_BASE_REF}"
  fi

  docker_build \
    -f "${PROJECT_DIR}/Dockerfile-server" \
    --build-arg "TBOT_SERVER_BASE_IMAGE=${SERVER_BASE_REF}" \
    -t "${SERVER_IMAGE}:${TAG}" \
    -t "${SERVER_IMAGE}:${SHA}" \
    "${PROJECT_DIR}"
fi

if [[ "${ONLY}" == "all" || "${ONLY}" == "web" ]]; then
if [[ "${FAST_WEB}" -eq 1 ]]; then
  run_manager_web_build
  run_manager_api_build
  [[ -d "${PROJECT_DIR}/main/manager-web/dist" ]] || die "missing web dist"
  [[ -f "${PROJECT_DIR}/main/manager-api/target/tbot-esp32-api.jar" ]] || die "missing manager API jar"
  docker_build \
    --pull \
    -f "${PROJECT_DIR}/Dockerfile-web-runtime" \
    -t "${WEB_IMAGE}:${TAG}" \
    -t "${WEB_IMAGE}:${SHA}" \
    "${PROJECT_DIR}"
else
  docker_build \
    --pull \
    -f "${PROJECT_DIR}/Dockerfile-web" \
    --build-arg "VUE_APP_NEST_AUTH_DISABLED=${VUE_APP_NEST_AUTH_DISABLED}" \
    -t "${WEB_IMAGE}:${TAG}" \
    -t "${WEB_IMAGE}:${SHA}" \
    "${PROJECT_DIR}"
fi
fi

if [[ "${NO_LATEST}" -eq 0 ]]; then
  if [[ "${ONLY}" == "all" || "${ONLY}" == "server" ]]; then
    docker tag "${SERVER_IMAGE}:${TAG}" "${SERVER_IMAGE}:latest-local"
  fi
  if [[ "${ONLY}" == "all" || "${ONLY}" == "web" ]]; then
    docker tag "${WEB_IMAGE}:${TAG}" "${WEB_IMAGE}:latest-local"
  fi
fi

printf 'Built:\n'
if [[ "${ONLY}" == "all" || "${ONLY}" == "server" ]]; then
  printf '  %s:%s\n' "${SERVER_IMAGE}" "${TAG}"
fi
if [[ "${ONLY}" == "all" || "${ONLY}" == "web" ]]; then
  printf '  %s:%s\n' "${WEB_IMAGE}" "${TAG}"
fi
