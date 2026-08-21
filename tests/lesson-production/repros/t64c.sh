#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# F-T64-05 + F-T64-08 — deploy exposure, and four deploy-contract tests that had
# drifted from the configs they guard.
#
#   F-T64-05: deploy/docker-compose.prod.yml published 8000/8003/8002 with no
#     127.0.0.1 prefix, i.e. on 0.0.0.0. On a VPS without a host firewall that
#     puts the whole ESP HTTP surface — including /internal/*, which T6.4 had
#     just finished authenticating — directly on the internet, around both
#     cloudflared and Nginx. Nothing needs the wildcard bind: cloudflared is a
#     host systemd service reaching 127.0.0.1:8003/:8000/:8002.
#
#   F-T64-08: four tests asserted config text that had since MOVED, so they were
#     red on main and had stopped guarding anything:
#       * two in test_http_server.py pinned the pre-d6536973 public-index
#         topology (`:3003` + proxy_cache), but that commit re-pointed the route
#         to the local CMS on :8002 and deliberately dropped the cache.
#       * three in test_scaleout_deploy_topology.py matched an exact contiguous
#         haproxy block that `timeout check 10s` / `inter 5s fall 5 rise 1` had
#         been inserted into — every property still held, only adjacency changed.
#
# Asserts the fixed state through the shipping tests + the config itself.
set -euo pipefail

cd "$(pwd)/main/tbot-server"

cat > tests/__t64c_repro.py <<'PY'
"""T6.4 follow-up repro — loopback-only publishes, and deploy contracts that hold."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_prod_compose_publishes_nothing_on_all_interfaces():
    compose = yaml.safe_load(
        (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    published = [
        (name, str(entry))
        for name, service in compose["services"].items()
        for entry in (service.get("ports") or [])
    ]
    assert published, "expected published ports to guard"
    assert [f"{n}: {e}" for n, e in published if not e.startswith("127.0.0.1:")] == []


def test_the_esp_http_port_is_still_published_somewhere():
    # Guard against "fixing" the exposure by deleting the publish outright:
    # cloudflared and Nginx both dial 127.0.0.1:8003 on the host.
    compose = yaml.safe_load(
        (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    entries = [
        str(entry)
        for service in compose["services"].values()
        for entry in (service.get("ports") or [])
    ]
    assert any(e.endswith(":8003") for e in entries)
    assert any(e.endswith(":8000") for e in entries)


def test_cloudflared_ingress_still_targets_loopback():
    ingress = (REPO_ROOT / "deploy" / "cloudflared" / "config.yml.example").read_text(
        encoding="utf-8"
    )
    # If the tunnel ever pointed somewhere other than loopback, the loopback bind
    # above would silently break the tunnel instead of only closing the hole.
    assert "service: http://127.0.0.1:8003" in ingress
    assert "service: http://127.0.0.1:8000" in ingress


def test_public_index_route_matches_the_shipped_upstream():
    nginx = (REPO_ROOT / "deploy" / "nginx" / "tjbot.vn.conf").read_text(encoding="utf-8")
    assert nginx.count("proxy_pass http://127.0.0.1:3003") == 0
    assert nginx.count("proxy_pass http://127.0.0.1:8002") == 3
    # The cache was removed on purpose; keep the host layer storage-free.
    assert "proxy_cache" not in nginx


@pytest.mark.parametrize(
    "test_id",
    [
        "tests/test_http_server.py::test_nginx_public_generation_locations_are_read_only_redacted_proxies",
        "tests/test_http_server.py::test_nginx_public_generation_reads_use_bounded_uri_egress_and_cache_only_latest",
        "tests/test_scaleout_deploy_topology.py::test_vps_ws_backend_health_checks_http_readiness_before_routing",
        "tests/test_scaleout_deploy_topology.py::test_vps_internal_http_routes_share_device_affinity_with_ws_routes",
        "tests/test_scaleout_deploy_topology.py::test_render_edge_haproxy_keeps_internal_device_routes_on_same_backend_as_ws",
    ],
)
def test_deploy_contract_tests_pass(test_id):
    """These five were RED on main; run them for real rather than restating them."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT / "main" / "tbot-server",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-2000:]
PY

trap 'rm -f tests/__t64c_repro.py' EXIT
python3 -m pytest tests/__t64c_repro.py -q
