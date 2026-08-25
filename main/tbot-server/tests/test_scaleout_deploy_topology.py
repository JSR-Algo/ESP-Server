from pathlib import Path
import subprocess

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "main" / "tbot-server" / "config.yaml"


def _assert_affinity_uses_normalized_device_key(haproxy: str):
    assert "http-request set-header x-tbot-affinity-key %[hdr(x-device-id),lower] if { hdr(x-device-id) -m found }" in haproxy
    assert "http-request set-header x-tbot-affinity-key %[hdr(device-id),lower] if { hdr(device-id) -m found }" in haproxy
    assert "http-request set-header x-tbot-affinity-key %[url_param(device-id),url_dec,lower] if { url_param(device-id) -m found }" in haproxy
    assert "balance hdr(x-tbot-affinity-key)" in haproxy
    assert "balance hdr(device-id)" not in haproxy


def _haproxy_backend(haproxy: str, name: str) -> list[str]:
    """Directives inside `backend <name>`, up to the next top-level section.

    These were asserted as one exact contiguous string, which broke the moment
    an unrelated directive was inserted into the block — `timeout check 10s`
    landed between `backend tbot_ws_backend` and `balance …`, and
    `inter 5s fall 5 rise 1` inside the server-template line. Every property the
    tests care about was still present; only the adjacency had changed. Match on
    the block's CONTENTS so ordering and additions do not produce false failures.
    """
    lines = haproxy.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.strip() == f"backend {name}"
    )
    directives = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        if line.strip():
            directives.append(line.strip())
    return directives


def _assert_device_affinity_backend(haproxy: str, name: str) -> list[str]:
    directives = _haproxy_backend(haproxy, name)
    assert "balance hdr(x-tbot-affinity-key)" in directives, name
    assert "hash-type consistent" in directives, name
    return directives


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

    directives = _assert_device_affinity_backend(haproxy, "tbot_ws_backend")
    assert "option httpchk GET /tbot/ota/" in directives
    # The WS backend must health-check the HTTP port, not the WS port: a replica
    # that accepts sockets before its HTTP surface is ready would otherwise take
    # device traffic. Match the directive's PARTS, so adding tuning knobs
    # (`inter 5s fall 5 rise 1`) does not fail the test.
    template = next(d for d in directives if d.startswith("server-template tbot "))
    for fragment in (
        "tbot-esp32-server:8000",
        "check port 8003",
        "resolvers docker",
        "resolve-prefer ipv4",
        "init-addr libc,none",
    ):
        assert fragment in template, fragment

def test_vps_internal_http_routes_share_device_affinity_with_ws_routes():
    haproxy = (REPO_ROOT / "deploy" / "haproxy.cfg").read_text(encoding="utf-8")

    _assert_affinity_uses_normalized_device_key(haproxy)
    _assert_device_affinity_backend(haproxy, "tbot_ws_backend")
    _assert_device_affinity_backend(haproxy, "tbot_http_backend")
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
    local_compose = (REPO_ROOT / "main" / "tbot-server" / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    deploy_script = (REPO_ROOT / "deploy" / "deploy-vps.sh").read_text(encoding="utf-8")

    assert "NODE_ENV: ${NODE_ENV:-production}" in compose
    assert "TBOT_SERVER_AUTH_KEY: ${TBOT_SERVER_AUTH_KEY:?set TBOT_SERVER_AUTH_KEY}" in compose
    assert "TBOT_REQUIRE_DEVICE_TOKEN: ${TBOT_REQUIRE_DEVICE_TOKEN:-true}" in compose
    assert "TBOT_DEVICE_MINT_SECRET: ${TBOT_DEVICE_MINT_SECRET:?set TBOT_DEVICE_MINT_SECRET}" in compose
    assert "JWT_PUBLIC_KEY: ${JWT_PUBLIC_KEY:?set JWT_PUBLIC_KEY}" in compose
    assert "LESSON_ASSET_ORIGIN_BASE: ${LESSON_ASSET_ORIGIN_BASE:?set LESSON_ASSET_ORIGIN_BASE}" in compose
    assert "LESSON_ASSET_ALLOWED_ORIGINS: ${LESSON_ASSET_ALLOWED_ORIGINS:?set LESSON_ASSET_ALLOWED_ORIGINS}" in compose
    assert "LESSON_SD_MAX_FILE_BYTES: ${LESSON_SD_MAX_FILE_BYTES:?set LESSON_SD_MAX_FILE_BYTES}" in compose
    assert "LESSON_SD_MAX_PACK_BYTES: ${LESSON_SD_MAX_PACK_BYTES:?set LESSON_SD_MAX_PACK_BYTES}" in compose
    assert "LESSON_ASSET_ALLOWED_ORIGINS=${LESSON_ASSET_ALLOWED_ORIGINS:-}" in local_compose
    assert "LESSON_SD_MAX_FILE_BYTES=${LESSON_SD_MAX_FILE_BYTES:-}" in local_compose
    assert "LESSON_SD_MAX_PACK_BYTES=${LESSON_SD_MAX_PACK_BYTES:-}" in local_compose
    assert "LESSON_VOICE_RT_P95_DISABLE_MS: ${LESSON_VOICE_RT_P95_DISABLE_MS:-}" in compose
    assert "NODE_ENV=production" in env_example
    assert "TBOT_SERVER_AUTH_KEY=REPLACE_WITH_SHARED_WS_HMAC_SECRET" in env_example
    assert "TBOT_REQUIRE_DEVICE_TOKEN=true" in env_example
    assert "TBOT_DEVICE_MINT_SECRET=REPLACE_WITH_SHARED_DEVICE_MINT_SECRET" in env_example
    assert "JWT_PUBLIC_KEY=REPLACE_WITH_BACKEND_JWT_PUBLIC_KEY" in env_example
    assert "LESSON_ASSET_ALLOWED_ORIGINS=https://res.cloudinary.com" in env_example
    assert "LESSON_SD_MAX_FILE_BYTES=33554432" in env_example
    assert "LESSON_SD_MAX_PACK_BYTES=134217728" in env_example
    assert "TBOT_SERVER_AUTH_KEY LESSON_ASSET_ORIGIN_BASE LESSON_ASSET_ALLOWED_ORIGINS" in deploy_script
    assert "LESSON_VOICE_RT_P95_DISABLE_MS=1500" in env_example
    assert "LESSON_ASSET_PACK_LOCAL_ROOT=sd://tbot/lesson-assets" in env_example
    assert "LESSON_ASSET_PACK_MOUNT_ROOT=/opt/tbot-esp32-server/data/lesson-packs" in env_example


def test_prod_deploy_wires_renderer_flags_defaulting_disabled():
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    env_example = (REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    script = (REPO_ROOT / "deploy" / "deploy-vps.sh").read_text(encoding="utf-8")

    environment = compose["services"]["tbot-esp32-server"]["environment"]

    assert environment["LESSON_RENDERER_V2_ENABLED"] == "${LESSON_RENDERER_V2_ENABLED:-false}"
    assert environment["LESSON_RENDERER_V4_ENABLED"] == "${LESSON_RENDERER_V4_ENABLED:-false}"
    assert environment["LESSON_RENDERER_V3_ENABLED"] == "${LESSON_RENDERER_V3_ENABLED:-false}"
    assert environment["LESSON_RENDERER_V5_ENABLED"] == "${LESSON_RENDERER_V5_ENABLED:-false}"
    assert "LESSON_RENDERER_V2_ENABLED=false" in env_example
    assert "LESSON_RENDERER_V4_ENABLED=false" in env_example
    assert "LESSON_RENDERER_V3_ENABLED=false" in env_example
    assert "LESSON_RENDERER_V5_ENABLED=false" in env_example
    assert "LESSON_RENDERER_V2_ENABLED" in script
    assert "LESSON_RENDERER_V4_ENABLED" in script
    assert "LESSON_RENDERER_V3_ENABLED" in script
    assert "LESSON_RENDERER_V5_ENABLED" in script


def test_prod_compose_uses_voice_safe_lesson_preload_timeout_default():
    compose = yaml.safe_load(
        (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )

    environment = compose["services"]["tbot-esp32-server"]["environment"]

    assert environment["LESSON_PRELOAD_TIMEOUT_SEC"] == "${LESSON_PRELOAD_TIMEOUT_SEC:-240}"


def test_server_healthcheck_proves_both_http_and_websocket_listeners():
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text())
    command = compose["services"]["tbot-esp32-server"]["healthcheck"]["test"][-1]
    assert "create_connection(('127.0.0.1', 8000)" in command
    assert "http://127.0.0.1:8003/tbot/ota/" in command


def test_deploy_waits_for_every_server_replica_to_be_healthy():
    script = (REPO_ROOT / "deploy" / "deploy-vps.sh").read_text(encoding="utf-8")
    assert "wait_for_remote_stack" in script
    assert "TBOT_SERVER_REPLICAS" in script
    assert ".State.Health.Status" in script
    assert "healthy server replicas" in script


def test_deploy_and_rollback_refuse_real_current_directory_before_atomic_switch():
    for script_name in ("deploy-vps.sh", "rollback-vps.sh"):
        script = (REPO_ROOT / "deploy" / script_name).read_text(encoding="utf-8")
        assert "ln -sfn ${REMOTE_RELEASE_Q} ${REMOTE_Q}/current" not in script
        assert "refusing to replace non-symlink current directory" in script
        assert "ln -s ${REMOTE_RELEASE_Q}" in script
        assert "mv -Tf" in script


def test_deploy_snapshots_env_before_upload_and_records_rollback_marker():
    script = (REPO_ROOT / "deploy" / "deploy-vps.sh").read_text(encoding="utf-8")

    backup = script.index(".env.rollback-")
    marker = script.index("env-backup-path")
    upload = script.index('run_scp "${ENV_FILE}"')

    assert backup < marker < upload
    assert "date -u +%Y%m%dT%H%M%SZ" in script
    assert "install -m 600" in script


def test_rollback_restores_recorded_env_before_switching_release():
    script = (REPO_ROOT / "deploy" / "rollback-vps.sh").read_text(encoding="utf-8")

    marker = script.index("env-backup-path")
    validation = script.index("invalid or unavailable env rollback backup")
    restore = script.index("install -m 600")
    switch = script.index("ln -s ${REMOTE_RELEASE_Q}")
    compose = script.index("docker compose --env-file")

    assert marker < validation < restore < switch < compose
    assert ".env.rollback-" in script


def test_release_package_includes_haproxy_config_required_by_compose():
    script = (REPO_ROOT / "deploy" / "package-release.sh").read_text(encoding="utf-8")

    assert 'cp "${SCRIPT_DIR}/haproxy.cfg" "${RELEASE_DIR}/haproxy.cfg"' in script


def test_release_package_fallback_preserves_asset_materialization_limits():
    script = (REPO_ROOT / "deploy" / "package-release.sh").read_text(encoding="utf-8")

    for expected in (
        "LESSON_ASSET_ALLOWED_ORIGINS: ${LESSON_ASSET_ALLOWED_ORIGINS:?set LESSON_ASSET_ALLOWED_ORIGINS}",
        "LESSON_SD_MAX_FILE_BYTES: ${LESSON_SD_MAX_FILE_BYTES:?set LESSON_SD_MAX_FILE_BYTES}",
        "LESSON_SD_MAX_PACK_BYTES: ${LESSON_SD_MAX_PACK_BYTES:?set LESSON_SD_MAX_PACK_BYTES}",
        "LESSON_ASSET_ALLOWED_ORIGINS=https://res.cloudinary.com",
        "LESSON_SD_MAX_FILE_BYTES=33554432",
        "LESSON_SD_MAX_PACK_BYTES=134217728",
    ):
        assert expected in script


def test_profile_sync_public_key_is_present_in_checked_in_and_generated_env_examples():
    env_example = (REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    package_script = (REPO_ROOT / "deploy" / "package-release.sh").read_text(
        encoding="utf-8"
    )
    deploy_script = (REPO_ROOT / "deploy" / "deploy-vps.sh").read_text(
        encoding="utf-8"
    )

    expected = "TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY=REPLACE_WITH_PROFILE_SYNC_JWT_PUBLIC_KEY"
    assert expected in env_example
    assert expected in package_script
    assert "JWT_PUBLIC_KEY TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY TBOT_DEVICE_MINT_SECRET" in deploy_script


def test_manual_fallback_preserves_redis_and_sd_pack_runtime_contract():
    readme = (REPO_ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert '-e "REDIS_URL=$REDIS_URL"' in readme
    assert '-e "LESSON_RENDERER_V2_ENABLED=$LESSON_RENDERER_V2_ENABLED"' in readme
    assert '-e "LESSON_ASSET_PACK_LOCAL_ROOT=$LESSON_ASSET_PACK_LOCAL_ROOT"' in readme
    assert '-e "LESSON_ASSET_PACK_MOUNT_ROOT=$LESSON_ASSET_PACK_MOUNT_ROOT"' in readme
    assert '-v "$TBOT_REMOTE_ROOT/data/lesson-packs:$LESSON_ASSET_PACK_MOUNT_ROOT"' in readme

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
        "LESSON_ASSET_ALLOWED_ORIGINS",
        "LESSON_SD_MAX_FILE_BYTES",
        "LESSON_SD_MAX_PACK_BYTES",
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
    _assert_device_affinity_backend(haproxy, "tbot_ws_backend")
    _assert_device_affinity_backend(haproxy, "tbot_http_backend")


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


@pytest.mark.parametrize(
    "limit_key, limit_value, expected_error",
    [
        (
            "LESSON_SD_MAX_FILE_BYTES",
            "29186047",
            "LESSON_SD_MAX_FILE_BYTES must be at least 29186048",
        ),
        (
            "LESSON_SD_MAX_PACK_BYTES",
            "116139165",
            "LESSON_SD_MAX_PACK_BYTES must be at least 116139166",
        ),
    ],
)
def test_deploy_vps_preflight_rejects_sd_limits_below_farm_v7_minimum(
    tmp_path, limit_key, limit_value, expected_error
):
    values = {
        "TBOT_REMOTE_ROOT": "/opt/tbot",
        "TBOT_PUBLIC_WEBSOCKET_URL": "wss://esp.tjbot.vn/tbot/v1/",
        "TBOT_BACKEND_API_URL": "https://backend.example.com/v1",
        "NODE_ENV": "production",
        "TBOT_REQUIRE_DEVICE_TOKEN": "true",
        "JWT_PUBLIC_KEY": "public-key",
        "TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY": "profile-public-key",
        "TBOT_DEVICE_MINT_SECRET": "mint-secret",
        "TBOT_SERVER_AUTH_KEY": "server-secret",
        "LESSON_ASSET_ORIGIN_BASE": "https://assets.example.com",
        "LESSON_ASSET_ALLOWED_ORIGINS": "https://assets.example.com",
        "LESSON_SD_MAX_FILE_BYTES": "29186048",
        "LESSON_SD_MAX_PACK_BYTES": "116139166",
    }
    values[limit_key] = limit_value
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")

    result = subprocess.run(
        [
            "bash", str(REPO_ROOT / "deploy" / "deploy-vps.sh"),
            "--host", "127.0.0.1", "--user", "root", "--tag", "test",
            "--env-file", str(env_file), "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_deploy_vps_preflight_accepts_farm_v7_minimum_env_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TBOT_REMOTE_ROOT=/opt/tbot",
                "TBOT_PUBLIC_WEBSOCKET_URL=wss://esp.tjbot.vn/tbot/v1/",
                "TBOT_BACKEND_API_URL=https://backend.example.com/v1",
                "NODE_ENV=production",
                "TBOT_REQUIRE_DEVICE_TOKEN=true",
                "JWT_PUBLIC_KEY=public-key",
                "TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY=profile-public-key",
                "TBOT_DEVICE_MINT_SECRET=mint-secret",
                "TBOT_SERVER_AUTH_KEY=server-secret",
                "LESSON_ASSET_ORIGIN_BASE=https://assets.example.com",
                "LESSON_ASSET_ALLOWED_ORIGINS=https://assets.example.com",
                "LESSON_SD_MAX_FILE_BYTES=29186048",
                "LESSON_SD_MAX_PACK_BYTES=116139166",
                "",
            ]
        ),
        encoding="utf-8",
    )
    release_dir = tmp_path / "releases" / "test"
    release_dir.mkdir(parents=True)
    (release_dir / "release.json").write_text("{}\n", encoding="utf-8")
    (release_dir / "checksums.sha256").write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            "bash", str(REPO_ROOT / "deploy" / "deploy-vps.sh"),
            "--host", "127.0.0.1", "--user", "root", "--tag", "test",
            "--env-file", str(env_file), "--release-root", str(tmp_path / "releases"),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_deploy_vps_preflight_accepts_multiline_single_quoted_public_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TBOT_REMOTE_ROOT=/opt/tbot",
                "TBOT_PUBLIC_WEBSOCKET_URL=wss://esp.tjbot.vn/tbot/v1/",
                "TBOT_BACKEND_API_URL=https://backend.example.com/v1",
                "NODE_ENV=production",
                "TBOT_REQUIRE_DEVICE_TOKEN=true",
                "JWT_PUBLIC_KEY=public-key",
                "TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY='",
                "-----BEGIN PUBLIC KEY-----",
                "c2FuaXRpemVkLXRlc3Qta2V5",
                "-----END PUBLIC KEY-----",
                "'",
                "TBOT_DEVICE_MINT_SECRET=mint-secret",
                "TBOT_SERVER_AUTH_KEY=server-secret",
                "LESSON_ASSET_ORIGIN_BASE=https://assets.example.com",
                "LESSON_ASSET_ALLOWED_ORIGINS=https://assets.example.com",
                "LESSON_SD_MAX_FILE_BYTES=29186048",
                "LESSON_SD_MAX_PACK_BYTES=116139166",
                "",
            ]
        ),
        encoding="utf-8",
    )
    release_dir = tmp_path / "releases" / "test"
    release_dir.mkdir(parents=True)
    (release_dir / "release.json").write_text("{}\n", encoding="utf-8")
    (release_dir / "checksums.sha256").write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            "bash", str(REPO_ROOT / "deploy" / "deploy-vps.sh"),
            "--host", "127.0.0.1", "--user", "root", "--tag", "test",
            "--env-file", str(env_file), "--release-root", str(tmp_path / "releases"),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "overrides, expected_error",
    [
        ({"LESSON_SAMPLE_ENABLED": "true"}, "LESSON_SAMPLE_ENABLED must be false"),
        ({"LESSON_RUNTIME_ENABLED": "true"}, "LESSON_ROLLOUT_DEVICE_ALLOWLIST must contain exactly one"),
        (
            {
                "LESSON_RUNTIME_ENABLED": "true",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01,robot-02",
            },
            "LESSON_ROLLOUT_DEVICE_ALLOWLIST must contain exactly one",
        ),
        (
            {
                "LESSON_RUNTIME_ENABLED": "false",
                "LESSON_RENDERER_V2_ENABLED": "true",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01",
            },
            "renderer v2 cannot be true while LESSON_RUNTIME_ENABLED is false",
        ),
        (
            {
                "LESSON_RUNTIME_ENABLED": "false",
                "LESSON_RENDERER_V4_ENABLED": "true",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01",
            },
            "renderer v4 cannot be true while LESSON_RUNTIME_ENABLED is false",
        ),
        (
            {
                "LESSON_RUNTIME_ENABLED": "true",
                "LESSON_RENDERER_V2_ENABLED": "true",
                "LESSON_ASSET_DELIVERY_MODE": "sd_pack",
                "LESSON_ASSET_PACK_LOCAL_ROOT": "sd://tbot/lesson-assets",
                "LESSON_ASSET_PACK_MOUNT_ROOT": "/opt/tbot-esp32-server/data/lesson-packs",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01,robot-02",
            },
            "LESSON_ROLLOUT_DEVICE_ALLOWLIST must contain exactly one",
        ),
        (
            {
                "LESSON_RENDERER_V2_ENABLED": "sometimes",
            },
            "LESSON_RENDERER_V2_ENABLED must be exactly true or false",
        ),
        (
            {
                "LESSON_RUNTIME_ENABLED": "false",
                "LESSON_MOTION_PRESETS_ENABLED": "true",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01",
            },
            "cannot be true while LESSON_RUNTIME_ENABLED is false",
        ),
        (
            {
                "LESSON_RUNTIME_ENABLED": "true",
                "LESSON_ASSET_DELIVERY_MODE": "sd_pack",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "robot-01",
            },
            "LESSON_ASSET_PACK_LOCAL_ROOT is required",
        ),
    ],
)
def test_deploy_vps_preflight_rejects_unsafe_lesson_rollout(tmp_path, overrides, expected_error):
    values = {
        "TBOT_REMOTE_ROOT": "/opt/tbot",
        "TBOT_PUBLIC_WEBSOCKET_URL": "wss://esp.tjbot.vn/tbot/v1/",
        "TBOT_BACKEND_API_URL": "https://backend.example.com/v1",
        "NODE_ENV": "production",
        "TBOT_REQUIRE_DEVICE_TOKEN": "true",
        "JWT_PUBLIC_KEY": "public-key",
        "TBOT_PROFILE_SYNC_JWT_PUBLIC_KEY": "profile-public-key",
        "TBOT_DEVICE_MINT_SECRET": "mint-secret",
        "TBOT_SERVER_AUTH_KEY": "server-secret",
        "LESSON_ASSET_ORIGIN_BASE": "https://assets.example.com",
        "LESSON_SAMPLE_ENABLED": "false",
        "LESSON_RUNTIME_ENABLED": "false",
        "LESSON_RENDERER_V2_ENABLED": "false",
        "LESSON_RENDERER_V4_ENABLED": "false",
        "LESSON_RENDERER_V5_ENABLED": "false",
        "LESSON_MOTION_PRESETS_ENABLED": "false",
        "LESSON_PLAYFUL_INTERACTIONS_ENABLED": "false",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "",
        "LESSON_ASSET_DELIVERY_MODE": "",
        "LESSON_ASSET_PACK_LOCAL_ROOT": "",
        "LESSON_ASSET_PACK_MOUNT_ROOT": "",
    }
    values.update(overrides)
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")

    result = subprocess.run(
        [
            "bash", str(REPO_ROOT / "deploy" / "deploy-vps.sh"),
            "--host", "127.0.0.1", "--user", "root", "--tag", "test",
            "--env-file", str(env_file), "--dry-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_prod_compose_publishes_every_port_on_loopback_only():
    """F-T64-05 — a bare "8003:8003" publishes on 0.0.0.0.

    On a VPS without a host firewall that makes the entire ESP HTTP surface —
    including /internal/*, which the backend drives — reachable directly, around
    both cloudflared and Nginx. Nothing legitimate needs the wildcard bind:
    cloudflared runs as a host systemd service and reaches these on 127.0.0.1
    (deploy/cloudflared/config.yml.example), as does deploy/nginx/tjbot.vn.conf.
    """
    compose = yaml.safe_load(
        (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )

    published = [
        (name, entry)
        for name, service in compose["services"].items()
        for entry in (service.get("ports") or [])
    ]
    assert published, "expected at least one published port to guard"

    offenders = [
        f"{name}: {entry}"
        for name, entry in published
        if not str(entry).startswith("127.0.0.1:")
    ]
    assert offenders == [], (
        "these ports publish on 0.0.0.0 and bypass cloudflared/Nginx: " f"{offenders}"
    )
