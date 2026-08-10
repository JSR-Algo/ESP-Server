from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_lesson_e2e_sim_sets_sd_materialize_limit():
    compose_path = REPO_ROOT / "docs/docker/docker-compose.lesson-e2e-sim.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    environment = compose["services"]["esp-server"]["environment"]

    assert str(environment["LESSON_SD_MAX_FILE_BYTES"]) == "33554432"
