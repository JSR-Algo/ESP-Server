#!/usr/bin/env bash
# Lesson-lane coverage LOCK.
#
# Additive gate: pins the lesson runtime surface at 100% line AND branch coverage.
# This does NOT touch global pytest config (pyproject [tool.pytest.ini_options]),
# so the normal full-suite run is unaffected. Run this script (e.g. in CI or
# pre-merge) to fail the build if any actionable lesson line OR branch regresses
# below 100% (--cov-branch + --cov-fail-under=100).
#
# Scoped modules (all currently 100%):
#   core/lesson/**                         (runtime, asset_cache, forwarder,
#                                            preload_voice_alarm, errors, fsm)
#   plugins_func/functions/start_lesson.py (start_lesson tool-admission gate)
#   core/providers/tools/product_toolset.py
#   config/manage_api_client.py            (lesson legs + shared client edges)
#
# config_loader lesson posture and google_live lesson narration/AEC/safety are
# exercised by their dedicated tests but are NOT whole-file gated here: those
# files carry large non-lesson / prod-network surfaces that are out of this
# lane's actionable scope.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv311/bin/python}"

TESTS=$(ls \
  tests/test_lesson_*.py \
  tests/test_start_lesson_tool.py \
  tests/test_product_toolset*.py \
  tests/test_manage_api_client*.py \
  tests/test_config_loader_lesson_posture.py \
  tests/test_google_live*.py \
  tests/test_aec_required_raises.py \
  tests/test_connection_voice_provider_routing.py \
  2>/dev/null)

exec "$PY" -m pytest $TESTS \
  --cov=core.lesson \
  --cov=plugins_func.functions.start_lesson \
  --cov=core.providers.tools.product_toolset \
  --cov=config.manage_api_client \
  --cov-branch \
  --cov-report=term-missing \
  --cov-fail-under=100 \
  -q
