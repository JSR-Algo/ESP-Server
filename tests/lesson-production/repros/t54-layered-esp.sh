#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

test -f main/tbot-server/core/lesson/layered_cinematic_contract.py
rg -q '_complete_passive_step' main/tbot-server/core/lesson/runtime.py
test -f main/manager-web/public/tvideo-demo/assets/t54-layered/robot-teach.mp4
cd main/tbot-server
${TBOT_REPRO_PYTHON:-python3} \
  -m pytest tests/test_layered_cinematic_contract.py -q
