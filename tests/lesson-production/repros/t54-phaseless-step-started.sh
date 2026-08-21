#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PROBE="$(mktemp main/tbot-server/tests/test_t54_phaseless_step_started_gate_XXXXXX.py)"
cleanup() { rm -f "$PROBE"; }
trap cleanup EXIT

cp "$ROOT/t54-phaseless-step-started/probe.py" "$PROBE"
python3 -m pytest -q "$PROBE"
