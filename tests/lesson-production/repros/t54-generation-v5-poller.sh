#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

rg -q 'validate_layered_cinematic_generation_asset' \
  main/tbot-server/core/lesson/global_generation_poller.py
rg -q 'cms_invalid_renderer_v5_asset' \
  main/tbot-server/core/lesson/global_generation_poller.py

python_bin="${TBOT_REPRO_PYTHON:-python3}"
"${python_bin}" -m pytest -q \
  main/tbot-server/tests/test_global_generation_poller.py \
  -k renderer_v5
