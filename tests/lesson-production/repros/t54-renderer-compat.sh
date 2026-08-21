#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

PYTHON=${TBOT_REPRO_PYTHON:-python3}
"$PYTHON" -m pytest \
  main/tbot-server/tests/test_lesson_runtime_branch_gaps.py::LayeredCinematicRuntimeTest::test_requests_only_enabled_exact_advertised_renderer_lanes \
  main/tbot-server/tests/test_lesson_runtime.py::LessonPullOnConnectCapabilityTest::test_v4_rollout_keeps_v2_and_v1_fallbacks_for_assigned_v2_manifest \
  -q
