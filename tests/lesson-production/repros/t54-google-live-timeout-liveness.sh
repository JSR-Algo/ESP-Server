#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PROBE_DIR="$(mktemp -d main/tbot-server/tests/t54_google_live_timeout_liveness_XXXXXX)"
PROBE="$PROBE_DIR/test_t54_google_live_timeout_liveness.py"
cleanup() { find "$PROBE_DIR" -depth -delete; }
trap cleanup EXIT

cp "$ROOT/t54-google-live-timeout-liveness/probe.py" "$PROBE"
python3 -m pytest -q "$PROBE"
