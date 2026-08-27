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
: "${COURSE_MODE_ASSET_ORIGIN_BASE:?export the robot-reachable local asset origin ending in /}"
: "${TBOT_DEVICE_MINT_SECRET:?export the shared local device mint secret}"
[[ -f "${BACKEND_ROOT}/Dockerfile" ]] || fail "backend Dockerfile is missing"
[[ -f "${BACKEND_ROOT}/keys/dev-public.pem" ]] || fail "backend local public key is missing"
[[ -f "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem" ]] || fail "backend local private key is missing"
KEY_CHECK_DIR="$(mktemp -d)"
trap 'rm -rf -- "${KEY_CHECK_DIR}"' EXIT
openssl pkey -in "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem" -pubout -outform DER \
  -out "${KEY_CHECK_DIR}/private-public.der" 2>/dev/null && \
openssl pkey -pubin -in "${BACKEND_ROOT}/keys/dev-public.pem" -outform DER \
  -out "${KEY_CHECK_DIR}/public.der" 2>/dev/null && \
  cmp -s "${KEY_CHECK_DIR}/private-public.der" "${KEY_CHECK_DIR}/public.der" || \
  fail "backend local JWT public/private key pair is invalid or mismatched"
[[ -f "${BACKEND_ROOT}/src/lessons/course-mode/course-mode-v5-identity-materializer.ts" ]] || \
  fail "backend canonical Course Mode v5 materializer source is missing"
TBOT_ESP_REPOSITORY_ROOT="$(cd -- "${HERE}/../../.." && pwd -P)"
export TBOT_ESP_REPOSITORY_ROOT

BACKEND_IMAGE="local/tbot-backend:course-mode-physical-tft-${ACTUAL_SHA}"
MATERIALIZER_IMAGE="local/tbot-course-mode-v5-materializer:${ACTUAL_SHA}"
COMPOSE_PROJECT="tbot-course-mode-physical-tft"
export TBOT_BACKEND_WORKTREE="${BACKEND_ROOT}"
export TBOT_LESSON_STUDIO_BACKEND_IMAGE="${BACKEND_IMAGE}"
export TBOT_COURSE_MODE_V5_MATERIALIZER_IMAGE="${MATERIALIZER_IMAGE}"
export JWT_PUBLIC_KEY="$(cat "${BACKEND_ROOT}/keys/dev-public.pem")"
export JWT_PRIVATE_KEY="$(cat "${BACKEND_ROOT}/keys/dev-private-pkcs8.pem")"
export LESSON_ASSET_ORIGIN_BASE="${COURSE_MODE_ASSET_ORIGIN_BASE}"
export LESSON_ASSET_PUBLIC_BASE_URL="http://192.168.100.183:8003/"
export LESSON_RENDERER_V3_ENABLED="true"
export ROBOT_ESP_BASE_URL="http://192.168.100.183:8003"
export TBOT_ESP_SERVER_URL="${ROBOT_ESP_BASE_URL}"
export TASK07_DEVICE_MAC="14:c1:9f:d1:ac:20"
unset COMPOSE_PROJECT_NAME COMPOSE_PROFILES LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME
export LESSON_STUDIO_E2E_RESOURCE_PREFIX="${COMPOSE_PROJECT}"

echo "[course-mode-physical-tft] compiling backend ${ACTUAL_SHA} from ${BACKEND_ROOT}"
(cd -- "${BACKEND_ROOT}" && npm run build)
[[ -z "$(git -C "${BACKEND_ROOT}" status --porcelain --untracked-files=all)" ]] || \
  fail "backend build changed the reviewed worktree; refusing to build an image with uncommitted source"

echo "[course-mode-physical-tft] building ${BACKEND_IMAGE} from the reviewed backend worktree"
docker build --pull=false \
  --label "com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/course-mode-v5-identity-materializer.js" \
  --label "org.opencontainers.image.revision=${ACTUAL_SHA}" \
  --label "com.tbot.course-mode.build-source=reviewed-clean-git-worktree" \
  -f "${BACKEND_ROOT}/Dockerfile" \
  -t "${BACKEND_IMAGE}" "${BACKEND_ROOT}"

echo "[course-mode-physical-tft] building canonical v5 materializer ${MATERIALIZER_IMAGE}"
docker build --pull=false \
  --label "com.tbot.course-mode.materializer-path=/app/dist/lessons/course-mode/course-mode-v5-identity-materializer.js" \
  --label "org.opencontainers.image.revision=${ACTUAL_SHA}" \
  -f "${BACKEND_ROOT}/Dockerfile.course-mode-v5-identity" \
  -t "${MATERIALIZER_IMAGE}" "${BACKEND_ROOT}"

echo "[course-mode-physical-tft] verifying compiled materializer in ${BACKEND_IMAGE}"
docker run --rm --entrypoint node "${MATERIALIZER_IMAGE}" \
  -e "require('node:fs').accessSync('/app/dist/lessons/course-mode/course-mode-v5-identity-materializer.js')"

COMPOSE=(docker compose --project-name "${COMPOSE_PROJECT}" -f "${BASE_COMPOSE}" -f "${OVERLAY_COMPOSE}")
echo "[course-mode-physical-tft] validating Compose with ${BACKEND_IMAGE}"
"${COMPOSE[@]}" config --quiet

if [[ "${1:-}" == "--config-only" ]]; then
  echo "[course-mode-physical-tft] config-only preflight complete; stack not started"
  exit 0
fi

echo "[course-mode-physical-tft] starting the local physical-TFT Compose project"
: "${TBOT_AUTHORIZED_FIRMWARE_READBACK:?export the authorized AC:20 app partition readback path}"
[[ -f "${TBOT_AUTHORIZED_FIRMWARE_READBACK}" ]] || \
  fail "authorized firmware readback is missing: ${TBOT_AUTHORIZED_FIRMWARE_READBACK}"
python3 "${HERE}/verify_firmware_endpoints.py" \
  "${TBOT_AUTHORIZED_FIRMWARE_READBACK}" \
  "http://192.168.100.183:8003/tbot/ota/" \
  "ws://192.168.100.183:8000/tbot/v1/" || \
  fail "authorized firmware endpoints do not target the isolated local stack; do not reboot or capture"
ESP_COMPOSE_FILES="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' tbot-esp32-server 2>/dev/null)" || \
  fail "tbot-esp32-server must be running so its local Compose project can be recreated"
[[ -n "${ESP_COMPOSE_FILES}" ]] || fail "tbot-esp32-server Compose provenance is unavailable"
ESP_COMPOSE=(docker compose --project-name tbot-server)
IFS=',' read -r -a ESP_COMPOSE_PATHS <<< "${ESP_COMPOSE_FILES}"
for compose_path in "${ESP_COMPOSE_PATHS[@]}"; do
  [[ -f "${compose_path}" ]] || fail "tbot-esp32-server Compose file is missing: ${compose_path}"
  ESP_COMPOSE+=(-f "${compose_path}")
done
echo "[course-mode-physical-tft] recreating the ESP bridge with the shared local mint secret"
"${ESP_COMPOSE[@]}" up -d --force-recreate tbot-esp32-server
"${COMPOSE[@]}" up -d --wait postgres redis mysql backend
IDENTITY_STATE="$("${COMPOSE[@]}" exec -T postgres psql -U tbot -d tbot -Atqc \
  "SELECT CASE WHEN COUNT(*)=0 THEN 'missing' WHEN NOT BOOL_AND(d.current_household_id IS NOT NULL AND d.assigned_child_profile_id IS NOT NULL AND h.owner_id IS NOT NULL) THEN 'partial' WHEN NOT BOOL_OR(EXISTS (SELECT 1 FROM lesson_assignments la JOIN lessons l ON l.id=la.lesson_id AND l.lesson_version=la.lesson_version WHERE la.device_id=d.id AND la.state IN ('ASSIGNED','PRELOADING','READY','RUNNING','PAUSED') AND l.status='published' AND l.manifest_version='teebot-lesson-renderer.v5')) THEN 'assignment-missing' ELSE 'ready' END FROM devices d LEFT JOIN households h ON h.id=d.current_household_id WHERE lower(d.mac_address)=lower('14:c1:9f:d1:ac:20')")"
if [[ "${IDENTITY_STATE}" == "missing" ]]; then
  fail "task-owned AC:20 identity is missing; refusing automatic bootstrap"
elif [[ "${IDENTITY_STATE}" != "ready" ]]; then
  fail "task-owned AC:20 identity or active renderer-v5 assignment is incomplete; refusing automatic repair"
fi
"${COMPOSE[@]}" up -d
