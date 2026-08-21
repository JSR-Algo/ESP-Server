#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 -m pytest -q \
  tests/test_tvideo_farm_cross_repo_fixture.py \
  tests/test_nginx_generation_cache_runtime.py::test_public_generation_reads_are_uncached_bounded_and_origin_isolated
