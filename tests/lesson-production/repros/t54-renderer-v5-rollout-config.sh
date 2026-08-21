#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

rg -q 'renderer_v5_flag = _parse_strict_bool_env\("LESSON_RENDERER_V5_ENABLED"\)' \
  main/tbot-server/config/config_loader.py
rg -q 'lesson_cfg\.setdefault\("renderer_v5_enabled", False\)' \
  main/tbot-server/config/config_loader.py
rg -q '"renderer_v5_enabled",' main/tbot-server/config/config_loader.py
rg -q 'LESSON_RENDERER_V5_ENABLED: \$\{LESSON_RENDERER_V5_ENABLED:-false\}' \
  deploy/docker-compose.prod.yml
rg -q 'LESSON_RENDERER_V5_ENABLED=false' deploy/.env.example
rg -q 'LESSON_RENDERER_V5_ENABLED' deploy/deploy-vps.sh

python_bin="${TBOT_REPRO_PYTHON:-python3}"
"${python_bin}" -m pytest -q \
  main/tbot-server/tests/test_lesson_rollout_controls.py \
  main/tbot-server/tests/test_config_loader_lesson_env_overrides.py \
  main/tbot-server/tests/test_config_loader_lesson_sd_gc.py \
  main/tbot-server/tests/test_scaleout_deploy_topology.py
