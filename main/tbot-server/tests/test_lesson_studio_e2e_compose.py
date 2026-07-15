from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docs/docker/docker-compose.lesson-studio-e2e.yml"


def test_lesson_studio_compose_is_test_owned_and_complete():
    compose = COMPOSE.read_text()

    assert "name: tbot-ls-e2e" in compose
    for service in ("postgres:", "redis:", "mysql:", "backend:", "web:", "seed-postgres:", "seed-mysql:"):
        assert service in compose

    assert '"3100:3000"' in compose
    assert '"8102:8002"' in compose
    assert "local/tbot-backend:lesson-studio-e2e" in compose
    assert "local/tbot-server-web:lesson-studio-e2e" in compose
    assert "/src/lessons/fixtures/tvideo-raw-code/assets:/usr/share/nginx/html/tvideo-demo:ro" not in compose
    assert "/src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json:/usr/share/nginx/html/tvideo-demo/asset-manifest.json:ro" in compose
    assert "/src/lessons/fixtures/tvideo-raw-code/assets/admin:/usr/share/nginx/html/tvideo-demo/admin:ro" in compose
    assert "/src/lessons/fixtures/tvideo-raw-code/assets/esp-tft:/usr/share/nginx/html/tvideo-demo/esp-tft:ro" in compose
    assert "/lesson/assets:/usr/share/nginx/html/tvideo-demo/assets:ro" in compose
    assert "LESSON_ASSET_ORIGIN_BASE: ${LESSON_ASSET_ORIGIN_BASE:?export a browser-and-robot reachable lesson asset origin}" in compose
    assert "TBOT_DEVICE_MINT_SECRET: ${TBOT_DEVICE_MINT_SECRET:?export the shared local device mint secret}" in compose
    assert "ROBOT_ESP_BASE_URL: ${ROBOT_ESP_BASE_URL:?export the live robot ESP fan-out URL}" in compose
    assert "TBOT_ESP_SERVER_URL: ${TBOT_ESP_SERVER_URL:-}" in compose
    assert "LESSON_SHARED_VISUAL_AUTHORING_ENABLED: \"true\"" in compose
    assert "LESSON_EXACT_ESPTFT_PREVIEW_ENABLED: \"true\"" in compose
    assert 'TBOT_E2E_CAPTCHA_ENABLED: "true"' in compose
    assert "TBOT_E2E_CAPTCHA_CODE: E2E42" in compose
    assert "condition: service_healthy" in compose
    assert "/tbot/user/captcha" not in compose
    assert "http://127.0.0.1:8002/login" in compose


def test_lesson_studio_seed_includes_real_response_visual_sources():
    seed = (ROOT / "docs/docker/lesson-studio-e2e/seed-postgres.sql").read_text()
    for asset_key in (
        "feedback.correct.star",
        "feedback.near-miss.spark",
        "feedback.incorrect.try-again",
        "ending.farm.parade",
    ):
        assert asset_key in seed
    assert "4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4" in seed


def test_nestjs_proxy_recovers_quickly_from_docker_dns_startup_races():
    nginx = (ROOT / "docs/docker/nginx.conf").read_text()
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in nginx
    assert "8.8.8.8" not in nginx
    assert "1.1.1.1" not in nginx
    assert "resolver_timeout 2s;" in nginx


def test_lesson_studio_seed_assets_are_idempotent_and_use_fixed_accounts():
    postgres = (ROOT / "docs/docker/lesson-studio-e2e/seed-postgres.sql").read_text()
    mysql = (ROOT / "docs/docker/lesson-studio-e2e/seed-mysql.sql").read_text()

    assert "lesson-author-e2e@local.invalid" in postgres
    assert "11111111-1111-4111-8111-111111111111" in postgres
    assert "ON CONFLICT" in postgres
    assert "admin_role_assignments" in postgres
    assert "lesson_admin_e2e" in mysql
    assert "9000001" in mysql
    assert "ON DUPLICATE KEY UPDATE" in mysql


def test_seed_services_remain_healthy_for_compose_wait():
    compose = COMPOSE.read_text()

    assert compose.count("condition: service_healthy") >= 6
    assert compose.count("test: [\"CMD\", \"test\", \"-f\", \"/tmp/seed-complete\"]") == 2
    assert compose.count("touch /tmp/seed-complete && exec sleep infinity") == 2
    assert "condition: service_completed_successfully" not in compose
