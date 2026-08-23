#!/usr/bin/env bash
# Build the reviewed backend worktree into an immutable local image before
# rendering or starting the Course Mode physical-TFT Compose project.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${HERE}/.." && pwd)"
BASE_COMPOSE="${DOCKER_DIR}/docker-compose.lesson-studio-e2e.yml"
OVERLAY_COMPOSE="${DOCKER_DIR}/docker-compose.course-mode-physical-tft.yml"

fail() {
  echo "[course-mode-physical-tft] FATAL: $*" >&2
  exit 1
}

[[ "${1:-}" == "" || "${1:-}" == "--config-only" ]] || \
  fail "usage: $0 [--config-only]"

: "${TBOT_BACKEND_WORKTREE:?export the reviewed task-owned backend worktree}"
: "${TBOT_BACKEND_GIT_SHA:?export the reviewed full backend git SHA}"

[[ "${TBOT_BACKEND_GIT_SHA}" =~ ^[0-9a-f]{40}$ ]] || \
  fail "TBOT_BACKEND_GIT_SHA must be a lowercase full 40-character git SHA"
[[ -d "${TBOT_BACKEND_WORKTREE}" ]] || \
  fail "TBOT_BACKEND_WORKTREE is not a directory: ${TBOT_BACKEND_WORKTREE}"

BACKEND_ROOT="$(git -C "${TBOT_BACKEND_WORKTREE}" rev-parse --show-toplevel 2>/dev/null)" || \
  fail "TBOT_BACKEND_WORKTREE is not a git worktree"
BACKEND_ROOT="$(cd -- "${BACKEND_ROOT}" && pwd -P)"
REQUESTED_ROOT="$(cd -- "${TBOT_BACKEND_WORKTREE}" && pwd -P)"
[[ "${BACKEND_ROOT}" == "${REQUESTED_ROOT}" ]] || \
  fail "TBOT_BACKEND_WORKTREE must name the backend worktree root: ${BACKEND_ROOT}"

ACTUAL_SHA="$(git -C "${BACKEND_ROOT}" rev-parse HEAD)"
[[ "${ACTUAL_SHA}" == "${TBOT_BACKEND_GIT_SHA}" ]] || \
  fail "backend HEAD ${ACTUAL_SHA} does not match TBOT_BACKEND_GIT_SHA ${TBOT_BACKEND_GIT_SHA}"
[[ -z "$(git -C "${BACKEND_ROOT}" status --porcelain --untracked-files=all)" ]] || \
  fail "backend worktree must be clean so the SHA-tagged image has exact source provenance"
[[ -f "${BACKEND_ROOT}/Dockerfile" ]] || fail "backend Dockerfile is missing"
[[ -f "${BACKEND_ROOT}/keys/dev-public.pem" ]] || fail "backend local public key is missing"
[[ -f "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem" ]] || fail "backend local private key is missing"
openssl pkey -in "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem" -pubout 2>/dev/null | \
  cmp -s - "${BACKEND_ROOT}/keys/dev-public.pem" || \
  fail "backend local JWT public/private key pair is invalid or mismatched"
[[ -f "${BACKEND_ROOT}/src/lessons/course-mode/course-mode-local-materializer.ts" ]] || \
  fail "backend Course Mode local materializer source is missing"

BACKEND_IMAGE="local/tbot-backend:course-mode-physical-tft-${ACTUAL_SHA}"
COMPOSE_PROJECT="tbot-course-mode-physical-tft"
export TBOT_BACKEND_WORKTREE="${BACKEND_ROOT}"
export TBOT_LESSON_STUDIO_BACKEND_IMAGE="${BACKEND_IMAGE}"
export JWT_PUBLIC_KEY="$(cat "${BACKEND_ROOT}/keys/dev-public.pem")"
export JWT_PRIVATE_KEY="$(cat "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem")"
unset COMPOSE_PROJECT_NAME COMPOSE_PROFILES LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME
export LESSON_STUDIO_E2E_RESOURCE_PREFIX="${COMPOSE_PROJECT}"

echo "[course-mode-physical-tft] compiling backend ${ACTUAL_SHA} from ${BACKEND_ROOT}"
(cd -- "${BACKEND_ROOT}" && npm run build)
[[ -z "$(git -C "${BACKEND_ROOT}" status --porcelain --untracked-files=all)" ]] || \
  fail "backend build changed the reviewed worktree; refusing to build an image with uncommitted source"

echo "[course-mode-physical-tft] building ${BACKEND_IMAGE} from the reviewed backend worktree"
docker build --pull=false \
  --label "com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/course-mode-local-materializer.js" \
  --label "org.opencontainers.image.revision=${ACTUAL_SHA}" \
  --label "com.tbot.course-mode.build-source=reviewed-clean-git-worktree" \
  -f "${BACKEND_ROOT}/Dockerfile" \
  -t "${BACKEND_IMAGE}" "${BACKEND_ROOT}"

echo "[course-mode-physical-tft] verifying compiled materializer in ${BACKEND_IMAGE}"
docker run --rm --entrypoint /nodejs/bin/node "${BACKEND_IMAGE}" \
  -e "require('node:fs').accessSync('/app/dist/lessons/course-mode/course-mode-local-materializer.js')"

COMPOSE=(docker compose --project-name "${COMPOSE_PROJECT}" -f "${BASE_COMPOSE}" -f "${OVERLAY_COMPOSE}")
echo "[course-mode-physical-tft] validating Compose with ${BACKEND_IMAGE}"
"${COMPOSE[@]}" config --quiet

if [[ "${1:-}" == "--config-only" ]]; then
  echo "[course-mode-physical-tft] config-only preflight complete; stack not started"
  exit 0
fi

echo "[course-mode-physical-tft] starting the local physical-TFT Compose project"
"${COMPOSE[@]}" up -d
