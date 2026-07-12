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
    assert "LESSON_SHARED_VISUAL_AUTHORING_ENABLED: \"true\"" in compose
    assert "LESSON_EXACT_ESPTFT_PREVIEW_ENABLED: \"true\"" in compose
    assert 'TBOT_E2E_CAPTCHA_ENABLED: "true"' in compose
    assert "TBOT_E2E_CAPTCHA_CODE: E2E42" in compose
    assert "condition: service_healthy" in compose
    assert "condition: service_completed_successfully" in compose


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
