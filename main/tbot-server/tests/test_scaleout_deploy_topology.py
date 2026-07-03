from pathlib import Path
import subprocess

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "main" / "tbot-server" / "config.yaml"


def _assert_affinity_uses_normalized_device_key(haproxy: str):
    assert "http-request set-header x-tbot-affinity-key %[hdr(x-device-id),lower] if { hdr(x-device-id) -m found }" in haproxy
    assert "http-request set-header x-tbot-affinity-key %[hdr(device-id),lower] if { hdr(device-id) -m found }" in haproxy
    assert "http-request set-header x-tbot-affinity-key %[url_param(device-id),url_dec,lower] if { url_param(device-id) -m found }" in haproxy
    assert "balance hdr(x-tbot-affinity-key)" in haproxy
    assert "balance hdr(device-id)" not in haproxy


def test_prod_compose_exposes_redis_to_python_ws_replicas():
    compose = (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "REDIS_URL:" in compose
    assert "redis://" in compose
    assert "tbot-esp32-server-redis" in compose


def test_prod_compose_fronts_ws_with_hash_affinity_lb_and_multiple_replicas():
    compose = (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    assert "tbot-wss-lb:" in compose
    assert "TBOT_SERVER_REPLICAS" in compose
    assert "replicas:" in compose
    assert "balance hdr(x-tbot-affinity-key)" in haproxy
    assert "hash-type consistent" in haproxy
    assert "server-template tbot" in haproxy

def test_vps_ws_backend_health_checks_http_readiness_before_routing():
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    assert "backend tbot_ws_backend\n    balance hdr(x-tbot-affinity-key)\n    hash-type consistent\n    option httpchk GET /tbot/ota/" in haproxy
    assert "server-template tbot 10 tbot-esp32-server:8000 check port 8003 resolvers docker resolve-prefer ipv4 init-addr libc,none" in haproxy

def test_vps_internal_http_routes_share_device_affinity_with_ws_routes():
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    _assert_affinity_uses_normalized_device_key(haproxy)
    assert "backend tbot_ws_backend\n    balance hdr(x-tbot-affinity-key)\n    hash-type consistent" in haproxy
    assert "backend tbot_http_backend\n    balance hdr(x-tbot-affinity-key)\n    hash-type consistent" in haproxy
    assert "balance roundrobin" not in haproxy


def test_vps_public_ws_query_device_id_is_normalized_before_backend_selection():
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    _assert_affinity_uses_normalized_device_key(haproxy)


def test_public_ws_frontend_routes_ota_and_internal_http_paths_to_http_backend():
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    assert "acl is_tbot_http_path path_beg /tbot/ota/ /internal/ /mcp/vision/" in haproxy
    assert "use_backend tbot_http_backend if is_tbot_http_path" in haproxy


def test_prod_compose_forwards_production_boot_guard_env_to_server():
    compose = (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "NODE_ENV: ${NODE_ENV:-production}" in compose
    assert "TBOT_SERVER_AUTH_KEY: ${TBOT_SERVER_AUTH_KEY:?set TBOT_SERVER_AUTH_KEY}" in compose
    assert "TBOT_REQUIRE_DEVICE_TOKEN: ${TBOT_REQUIRE_DEVICE_TOKEN:-true}" in compose
    assert "TBOT_DEVICE_MINT_SECRET: ${TBOT_DEVICE_MINT_SECRET:?set TBOT_DEVICE_MINT_SECRET}" in compose
    assert "JWT_PUBLIC_KEY: ${JWT_PUBLIC_KEY:?set JWT_PUBLIC_KEY}" in compose
    assert "LESSON_ASSET_ORIGIN_BASE: ${LESSON_ASSET_ORIGIN_BASE:?set LESSON_ASSET_ORIGIN_BASE}" in compose
    assert "LESSON_VOICE_RT_P95_DISABLE_MS: ${LESSON_VOICE_RT_P95_DISABLE_MS:-}" in compose
    assert "NODE_ENV=production" in env_example
    assert "TBOT_SERVER_AUTH_KEY=REPLACE_WITH_SHARED_WS_HMAC_SECRET" in env_example
    assert "TBOT_REQUIRE_DEVICE_TOKEN=true" in env_example
    assert "TBOT_DEVICE_MINT_SECRET=REPLACE_WITH_SHARED_DEVICE_MINT_SECRET" in env_example
    assert "JWT_PUBLIC_KEY=REPLACE_WITH_BACKEND_JWT_PUBLIC_KEY" in env_example
    assert "LESSON_VOICE_RT_P95_DISABLE_MS=1500" in env_example

def test_managed_render_blueprint_declares_lesson_runtime_production_posture():
    blueprint_path = REPO_ROOT / "render.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    services = {service["name"]: service for service in blueprint["services"]}
    edge = services["tbot-esp32-edge"]
    server = services["tbot-esp32-server"]
    edge_env = {item["key"]: item for item in edge["envVars"]}
    env = {item["key"]: item for item in server["envVars"]}
    rendered = blueprint_path.read_text(encoding="utf-8")

    assert edge["type"] == "web"
    assert edge["runtime"] == "docker"
    assert edge["dockerfilePath"] == "./Dockerfile-render-edge"
    assert edge_env["TBOT_SERVER_HOST"]["fromService"]["name"] == "tbot-esp32-server"
    assert edge_env["TBOT_SERVER_HOST"]["fromService"]["property"] == "host"
    assert server["type"] == "pserv"
    assert server["runtime"] == "docker"
    assert server["dockerfilePath"] == "./Dockerfile-server"
    assert server["dockerContext"] == "."
    assert env["NODE_ENV"]["value"] == "production"
    assert env["LESSON_RUNTIME_ENABLED"]["value"] == "true"
    assert env["TBOT_REQUIRE_DEVICE_TOKEN"]["value"] == "true"
    assert env["TBOT_SERVER_AUTH_ENABLED"]["value"] == "true"
    assert env["LESSON_VOICE_RT_P95_DISABLE_MS"]["value"] == "1500"
    for key in (
        "COURSE_BACKEND_URL",
        "JWT_PUBLIC_KEY",
        "TBOT_DEVICE_MINT_SECRET",
        "LESSON_ASSET_ORIGIN_BASE",
        "TBOT_PUBLIC_WEBSOCKET_URL",
    ):
        assert env[key]["sync"] is False
    assert env["REDIS_URL"]["fromService"]["name"] == "tbot-esp32-server-redis"
    assert "trycloudflare.com" not in rendered

def test_committed_endpoint_seed_artifacts_do_not_pin_quick_tunnels():
    for relative in (
        "deploy/CURRENT_ENDPOINTS.md",
        "deploy/current-quick-tunnel.env",
        "deploy/current-quick-tunnel-sys-params.sql",
        "deploy/tjbot-prod-sys-params.sql",
    ):
        contents = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "trycloudflare.com" not in contents, relative
        assert "ngrok" not in contents.lower(), relative
        assert "loca.lt" not in contents.lower(), relative
        assert "serveo.net" not in contents.lower(), relative

def test_render_edge_haproxy_routes_public_single_port_to_ws_and_http_backends():
    dockerfile = (REPO_ROOT / "Dockerfile-render-edge").read_text(encoding="utf-8")
    script = (REPO_ROOT / "deploy" / "start-render-haproxy.sh").read_text(encoding="utf-8")
    haproxy = (REPO_ROOT / "deploy" / "render-haproxy.cfg").read_text(encoding="utf-8")

    assert "haproxy:3.0-alpine" in dockerfile
    assert "start-render-haproxy.sh" in dockerfile
    assert "${PORT:-10000}" in script
    assert "${TBOT_SERVER_HOST:?set TBOT_SERVER_HOST}" in script
    assert "bind *:__PORT__" in haproxy
    assert "acl is_tbot_http_path path_beg /tbot/ota/ /internal/ /mcp/vision/" in haproxy
    assert "default_backend tbot_ws_backend" in haproxy
    assert "server tbot __TBOT_SERVER_HOST__:8000" in haproxy
    assert "server tbot __TBOT_SERVER_HOST__:8003" in haproxy

def test_render_edge_haproxy_keeps_internal_device_routes_on_same_backend_as_ws():
    haproxy = (REPO_ROOT / "deploy" / "render-haproxy.cfg").read_text(encoding="utf-8")

    _assert_affinity_uses_normalized_device_key(haproxy)
    assert "backend tbot_ws_backend\n    balance hdr(x-tbot-affinity-key)\n    hash-type consistent" in haproxy
    assert "backend tbot_http_backend\n    balance hdr(x-tbot-affinity-key)\n    hash-type consistent" in haproxy


def test_shipped_config_requires_auth_keepalive_and_gemini_reconnect():
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert config["server"]["auth"]["enabled"] is True
    assert config["server"]["auth"]["allowed_devices"] == []
    assert config["enable_websocket_ping"] is True
    reconnect = config["google_live"]["reconnect"]
    assert reconnect["enabled"] is True
    assert reconnect["max_retries"] > 0


def test_deploy_vps_preflight_rejects_missing_production_boot_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TBOT_REMOTE_ROOT=/opt/tbot",
                "TBOT_PUBLIC_WEBSOCKET_URL=wss://esp.tjbot.vn/tbot/v1/",
                "TBOT_BACKEND_API_URL=https://tbot-backend-8wmh.onrender.com/v1",
                "TBOT_REQUIRE_DEVICE_TOKEN=true",
                "TBOT_DEVICE_MINT_SECRET=secret",
                "LESSON_ASSET_ORIGIN_BASE=https://assets.example.com",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "deploy" / "deploy-vps.sh"),
            "--host",
            "127.0.0.1",
            "--user",
            "root",
            "--tag",
            "test",
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert "JWT_PUBLIC_KEY" in result.stderr
