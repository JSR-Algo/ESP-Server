from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docs/docker/docker-compose.course-mode-physical-tft.yml"


class ComposeLoader(yaml.SafeLoader):
    pass


ComposeLoader.add_constructor(
    "!override",
    lambda loader, node: loader.construct_sequence(node),
)


def test_physical_tft_override_is_loopback_only_and_one_device_scoped():
    raw = COMPOSE.read_text(encoding="utf-8")
    compose = yaml.load(raw, Loader=ComposeLoader)

    assert set(compose) == {"services"}
    assert set(compose["services"]) == {"backend"}

    backend = compose["services"]["backend"]
    assert backend["ports"] == ["127.0.0.1:3000:3000"]
    assert backend["environment"] == {
        "COURSE_MODE_V2_PUBLISH_ENABLED": "true",
        "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED": "true",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "14:c1:9f:d1:ac:20",
        "TBOT_DEVICE_MINT_SECRET": (
            "${TBOT_DEVICE_MINT_SECRET:?export the shared local device mint secret}"
        ),
    }

    lowered = raw.lower()
    assert "production" not in lowered
    assert "https://" not in lowered
    assert "volumes:" not in lowered
