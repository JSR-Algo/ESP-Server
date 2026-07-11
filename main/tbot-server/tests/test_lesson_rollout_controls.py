import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from config.config_loader import _apply_lesson_env_overrides
from core.lesson.runtime import LessonRuntime


def test_rollout_flags_default_false_and_parse_allowlist(monkeypatch):
    for name in (
        "LESSON_MOTION_PRESETS_ENABLED",
        "LESSON_PLAYFUL_INTERACTIONS_ENABLED",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST",
    ):
        monkeypatch.delenv(name, raising=False)

    config = _apply_lesson_env_overrides({"lesson": {}})
    assert config["lesson"]["motion_presets_enabled"] is False
    assert config["lesson"]["playful_interactions_enabled"] is False
    assert config["lesson"]["rollout_device_allowlist"] == []

    monkeypatch.setenv("LESSON_MOTION_PRESETS_ENABLED", "true")
    monkeypatch.setenv("LESSON_PLAYFUL_INTERACTIONS_ENABLED", "1")
    monkeypatch.setenv("LESSON_ROLLOUT_DEVICE_ALLOWLIST", " robot-01,AA:BB:CC ,robot-01 ")
    config = _apply_lesson_env_overrides({"lesson": {}})
    assert config["lesson"]["motion_presets_enabled"] is True
    assert config["lesson"]["playful_interactions_enabled"] is True
    assert config["lesson"]["rollout_device_allowlist"] == ["aa:bb:cc", "robot-01"]


def test_invalid_rollout_boolean_is_rejected(monkeypatch):
    monkeypatch.setenv("LESSON_MOTION_PRESETS_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="LESSON_MOTION_PRESETS_ENABLED"):
        _apply_lesson_env_overrides({"lesson": {}})


@pytest.mark.parametrize("yaml_value", ['"false"', '"true"', "0", "1", "null", '"invalid"'])
@pytest.mark.parametrize("key", ["motion_presets_enabled", "playful_interactions_enabled"])
def test_yaml_rollout_flags_reject_non_boolean_scalars(monkeypatch, key, yaml_value):
    monkeypatch.delenv("LESSON_MOTION_PRESETS_ENABLED", raising=False)
    monkeypatch.delenv("LESSON_PLAYFUL_INTERACTIONS_ENABLED", raising=False)
    config = yaml.safe_load(f"lesson:\n  {key}: {yaml_value}\n")
    with pytest.raises(ValueError, match=key):
        _apply_lesson_env_overrides(config)


@pytest.mark.parametrize("yaml_value, expected", [("false", False), ("true", True)])
def test_yaml_rollout_flags_accept_actual_booleans(monkeypatch, yaml_value, expected):
    monkeypatch.delenv("LESSON_MOTION_PRESETS_ENABLED", raising=False)
    monkeypatch.delenv("LESSON_PLAYFUL_INTERACTIONS_ENABLED", raising=False)
    config = yaml.safe_load(
        "lesson:\n"
        f"  motion_presets_enabled: {yaml_value}\n"
        f"  playful_interactions_enabled: {yaml_value}\n"
        "  rollout_device_allowlist: []\n"
    )
    result = _apply_lesson_env_overrides(config)
    assert result["lesson"]["motion_presets_enabled"] is expected
    assert result["lesson"]["playful_interactions_enabled"] is expected


def test_empty_allowlist_does_not_block_an_explicitly_enabled_control():
    runtime = _runtime(
        device_id="any-robot",
        lesson={"motion_presets_enabled": True, "rollout_device_allowlist": []},
    )
    assert runtime._lesson_rollout_control_enabled("motion_presets_enabled") is True


def test_compose_variants_forward_documented_lesson_rollout_environment():
    root = Path(__file__).resolve().parents[1]
    required = {
        "LESSON_RUNTIME_ENABLED",
        "LESSON_ASSET_DELIVERY_MODE",
        "LESSON_ASSET_PACK_MOUNT_ROOT",
        "LESSON_MOTION_PRESETS_ENABLED",
        "LESSON_PLAYFUL_INTERACTIONS_ENABLED",
        "LESSON_ROLLOUT_DEVICE_ALLOWLIST",
    }
    for name in ("docker-compose.yml", "docker-compose_all.yml"):
        compose = yaml.safe_load((root / name).read_text(encoding="utf-8"))
        environment = compose["services"]["tbot-esp32-server"]["environment"]
        forwarded = {entry.split("=", 1)[0] for entry in environment}
        assert required <= forwarded, f"{name} missing {sorted(required - forwarded)}"


def _runtime(*, device_id="robot-01", lesson=None):
    runtime = object.__new__(LessonRuntime)
    runtime.conn = SimpleNamespace(
        device_id=device_id,
        config={"lesson": lesson or {}},
        logger=SimpleNamespace(bind=lambda **_: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None)),
    )
    runtime._step = {
        "interaction": {"template": "safeSpeaking"},
        "motion": {"correct": "celebrate"},
    }
    runtime._motion_generation = 0
    runtime._motion_task = None
    runtime._closed = False
    runtime._step_id = "s1"
    runtime._step_seq = 3
    return runtime


def test_playful_runtime_gate_preserves_legacy_interaction_path():
    disabled = _runtime(lesson={"playful_interactions_enabled": False})
    assert disabled._uses_safe_speaking() is False

    enabled = _runtime(lesson={"playful_interactions_enabled": True})
    assert enabled._uses_safe_speaking() is True

    not_allowlisted = _runtime(
        device_id="robot-02",
        lesson={
            "playful_interactions_enabled": True,
            "rollout_device_allowlist": ["robot-01"],
        },
    )
    assert not_allowlisted._uses_safe_speaking() is False


def test_prepare_control_manifest_and_motion_disabled_metric(monkeypatch):
    runtime = _runtime(
        lesson={
            "motion_presets_enabled": True,
            "playful_interactions_enabled": True,
            "rollout_device_allowlist": ["robot-01"],
        }
    )
    runtime.assignment_version = 1
    runtime.profile = "espTft"
    runtime.lesson_id = "lesson-1"
    runtime.lesson_version = 2
    runtime.manifest_checksum = "abc"
    runtime.asset_cache = SimpleNamespace(preload_timeout_sec=30)
    runtime.manifest = {"assets": []}
    runtime._use_sd_asset_pack = lambda: False
    body = runtime._prepare_body()
    assert body["runtimeControls"] == {
        "motionPresetsEnabled": True,
        "playfulInteractionsEnabled": True,
    }

    logs = []
    runtime.conn.config["lesson"]["motion_presets_enabled"] = False
    runtime._log = lambda level, message: logs.append((level, message))
    runtime._dispatch_step_motion("correct")
    assert runtime._motion_task is None
    assert logs == [("info", "lesson_motion_dispatch outcome=disabled preset=celebrate")]


@pytest.mark.asyncio
async def test_motion_dispatch_false_is_counted_as_failed(monkeypatch):
    runtime = _runtime(lesson={"motion_presets_enabled": True})
    logs = []
    runtime._log = lambda level, message: logs.append((level, message))

    async def failed(*_args):
        return False

    monkeypatch.setattr("core.lesson.runtime.dispatch_motion_preset", failed)
    runtime._dispatch_step_motion("correct")
    await runtime._motion_task
    assert ("warning", "lesson_motion_dispatch outcome=failed preset=celebrate") in logs
