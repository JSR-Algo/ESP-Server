"""Round-1 branch coverage for ``config.config_loader._apply_lesson_env_overrides``.

Drives:
  - line 169: early return when ``config`` is not a Mapping (config=None);
  - lines 197-198: ``lesson`` key missing / non-Mapping -> create empty dict and
    attach it to the config before applying overrides.

``_apply_lesson_env_overrides`` reads ``os.environ`` directly, so the fixture
clears every lesson env var first, then sets only what each test needs.
"""

import pytest

from config.config_loader import _apply_lesson_env_overrides

_LESSON_ENV = (
    "LESSON_RUNTIME_ENABLED",
    "COURSE_BACKEND_URL",
    "LESSON_ASSET_ORIGIN_BASE",
    "LESSON_ASSET_PUBLIC_BASE_URL",
    "LESSON_ASSET_DELIVERY_MODE",
    "LESSON_ASSET_PACK_LOCAL_ROOT",
    "LESSON_ASSET_PACK_MOUNT_ROOT",
    "LESSON_STEP_TIMEOUT_FLOOR_SEC",
    "LESSON_MAX_ASSET_BYTES",
    "LESSON_MAX_TOTAL_ASSET_BYTES",
)


@pytest.fixture(autouse=True)
def _clear_lesson_env(monkeypatch):
    for name in _LESSON_ENV:
        monkeypatch.delenv(name, raising=False)


def test_non_mapping_config_returns_unchanged():
    """Line 169: a non-Mapping config (None) short-circuits and is returned as-is,
    even though an override env var is set."""
    import os

    os.environ["LESSON_RUNTIME_ENABLED"] = "true"
    try:
        assert _apply_lesson_env_overrides(None) is None
    finally:
        del os.environ["LESSON_RUNTIME_ENABLED"]


def test_missing_lesson_key_creates_and_attaches_empty_dict(monkeypatch):
    """Lines 197-198: config has no ``lesson`` key, so the function creates an
    empty dict, attaches it to ``config['lesson']``, then writes the override."""
    monkeypatch.setenv("LESSON_RUNTIME_ENABLED", "true")
    config = {}  # no 'lesson' key at all
    result = _apply_lesson_env_overrides(config)
    assert result is config
    assert isinstance(config["lesson"], dict)
    assert config["lesson"]["runtime_enabled"] is True


def test_non_mapping_lesson_value_is_replaced_with_dict(monkeypatch):
    """Lines 196-198 (sibling): ``lesson`` exists but is a non-Mapping (a list),
    so it is replaced with a fresh empty dict before the override is applied."""
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example.com/")
    config = {"lesson": ["not", "a", "mapping"]}
    result = _apply_lesson_env_overrides(config)
    assert result is config
    assert isinstance(config["lesson"], dict)
    # rstrip("/") applied by the override path.
    assert config["lesson"]["asset_origin_base"] == "https://cdn.example.com"
