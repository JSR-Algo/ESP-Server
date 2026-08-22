import json
import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
BASE_COMPOSE = ROOT / "docs/docker/docker-compose.lesson-studio-e2e.yml"
OVERLAY_COMPOSE = ROOT / "docs/docker/docker-compose.course-mode-physical-tft.yml"


class ComposeLoader(yaml.SafeLoader):
    pass


ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: loader.construct_sequence(node),
)


def test_physical_tft_override_is_loopback_only_and_one_device_scoped():
    raw = OVERLAY_COMPOSE.read_text(encoding="utf-8")
    overlay = yaml.load(raw, Loader=ComposeLoader)

    assert set(overlay) == {"name", "services"}
    assert overlay["name"] == "tbot-course-mode-physical-tft"
    assert set(overlay["services"]) == {"backend"}
    assert "volumes" not in overlay
    assert "volumes" not in overlay["services"]["backend"]
    assert overlay["services"]["backend"]["environment"][
        "TBOT_DEVICE_MINT_SECRET"
    ] == "${TBOT_DEVICE_MINT_SECRET:?export the shared local device mint secret}"

    env = os.environ.copy()
    for variable in (
        "COMPOSE_PROJECT_NAME",
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
    ):
        env.pop(variable, None)
    env.update(
        {
            "JWT_PUBLIC_KEY": "dummy-local-public-key",
            "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "http://127.0.0.1:8102/tvideo-demo",
            "ROBOT_ESP_BASE_URL": "http://host.docker.internal:8080",
        }
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OVERLAY_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        text=True,
    )
    compose = json.loads(result.stdout)
    backend = compose["services"]["backend"]

    assert compose["name"] == "tbot-course-mode-physical-tft"
    assert backend["container_name"] == "tbot-course-mode-physical-tft-backend"
    assert compose["networks"]["lesson-studio-e2e"]["name"] == (
        "tbot-course-mode-physical-tft"
    )
    assert {
        volume["name"] for volume in compose["volumes"].values()
    } == {
        "tbot-course-mode-physical-tft-pg-data",
        "tbot-course-mode-physical-tft-redis-data",
        "tbot-course-mode-physical-tft-mysql-data",
    }
    assert "tbot-ls-e2e" not in result.stdout
    assert backend["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 3000,
            "published": "3000",
            "protocol": "tcp",
        }
    ]
    required_environment = (
        "COURSE_MODE_V2_PUBLISH_ENABLED",
        "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST",
        "TBOT_DEVICE_MINT_SECRET",
    )
    assert {
        key: backend["environment"][key] for key in required_environment
    } == {
        "COURSE_MODE_V2_PUBLISH_ENABLED": "true",
        "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED": "true",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "14:c1:9f:d1:ac:20",
        "TBOT_DEVICE_MINT_SECRET": "dummy-local-mint-secret",
    }

    lowered = raw.lower()
    assert "production" not in lowered
    assert "prod." not in lowered
    assert "password:" not in lowered
    assert "token:" not in lowered
    assert "https://" not in lowered
    assert "http://" not in lowered
