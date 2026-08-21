#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# Cross-repo contract drift — the ESP v2 TRGB asset projection vs its own test.
#
# `test_flattened_cinematic_contract.py` still expected the PRE-v2 asset shape:
# a nested `compatibilityMetadata` plus `width`/`height`. The implementation
# emits the flat container fields, and the implementation is the one that is
# right — TBOT-Firmware validates this object with ExactObjectKeys against
# kV2TrgbAssetKeys (main/lesson_handler.cc:2204), so the nested shape the test
# demanded would be REJECTED by the device outright.
#
# RED@base is the repo's own suite failing on main; GREEN@tip is the corrected
# expectation plus a parity guard that reads the key set out of the firmware
# source, so this cannot silently drift back.
set -euo pipefail

SERVER_DIR="main/tbot-server"
[ -d "$SERVER_DIR" ] || { echo "FATAL: run from an esp32-server worktree root"; exit 2; }

cd "$SERVER_DIR"
python3 -m pytest -q -p no:cacheprovider --tb=short tests/test_flattened_cinematic_contract.py

echo "REPRO PASS: ESP v2 TRGB asset projection matches the firmware's ExactObjectKeys set."
