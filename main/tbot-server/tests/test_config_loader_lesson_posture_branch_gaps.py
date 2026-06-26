"""Round-2 branch gaps for ``config_loader`` lesson posture.

``test_config_loader_lesson_posture.py`` pins the headline CR-TRIG-05 contract
(file-dark + env-derived gate + boot-safe guard) but only exercises a handful of
the env-override legs inside ``_apply_lesson_env_overrides``. Coverage showed the
individual asset/timeout/byte override branches (and the COURSE_BACKEND_URL
server.api_url + api_base fallback) were never driven. These tests drive each
override leg against a synthetic-but-real config dict so the assignment is
actually executed, closing the remaining lines on the lesson-posture surface.

Pure-function level: no network, no manage-api, no event loop. The autouse
fixture clears every lesson env var so ambient shell/CI values cannot leak in.
"""

import pytest

from config.config_loader import (
    _apply_lesson_env_overrides,
    _assert_lesson_runtime_boot_safe,
    _parse_positive_int_env,
)

_LESSON_POSTURE_ENV = (
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
    "TBOT_DEVICE_MINT_SECRET",
)


@pytest.fixture(autouse=True)
def _clear_lesson_env(monkeypatch):
    for name in _LESSON_POSTURE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def test_all_asset_overrides_strip_trailing_slash_and_create_lesson_block(monkeypatch):
    """Each *_BASE / *_ROOT override is applied and right-stripped of trailing /.

    The input config has NO ``lesson`` key, so this also drives the
    ``lesson_cfg = {}`` creation branch.
    """
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example/origin/")
    monkeypatch.setenv("LESSON_ASSET_PUBLIC_BASE_URL", "https://cdn.example/pub/")
    monkeypatch.setenv("LESSON_ASSET_DELIVERY_MODE", "http_pull")
    monkeypatch.setenv("LESSON_ASSET_PACK_LOCAL_ROOT", "/srv/packs/")
    monkeypatch.setenv("LESSON_ASSET_PACK_MOUNT_ROOT", "/mnt/sd/")

    config = {}
    out = _apply_lesson_env_overrides(config)

    lesson = out["lesson"]
    assert lesson["asset_origin_base"] == "https://cdn.example/origin"
    assert lesson["asset_public_base_url"] == "https://cdn.example/pub"
    assert lesson["asset_delivery_mode"] == "http_pull"
    assert lesson["asset_pack_local_root"] == "/srv/packs"
    assert lesson["asset_pack_mount_root"] == "/mnt/sd"


def test_step_timeout_floor_parses_and_clamps_to_zero(monkeypatch):
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "-3.5")
    out = _apply_lesson_env_overrides({"lesson": {}})
    assert out["lesson"]["step_timeout_floor_sec"] == 0.0


def test_step_timeout_floor_invalid_value_is_swallowed(monkeypatch):
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "not-a-number")
    out = _apply_lesson_env_overrides({"lesson": {}})
    assert "step_timeout_floor_sec" not in out["lesson"]


def test_max_byte_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("LESSON_MAX_ASSET_BYTES", "1024")
    monkeypatch.setenv("LESSON_MAX_TOTAL_ASSET_BYTES", "4096")
    out = _apply_lesson_env_overrides({"lesson": {}})
    assert out["lesson"]["max_asset_bytes"] == 1024
    assert out["lesson"]["max_total_asset_bytes"] == 4096


def test_course_url_sets_server_api_url_and_lesson_api_base_fallback(monkeypatch):
    """COURSE_BACKEND_URL writes server.api_url and fills lesson.api_base when absent.

    Drives the ``server`` block creation branch and the
    ``not lesson_cfg.get('api_base')`` fallback assignment.
    """
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.example/api/")
    out = _apply_lesson_env_overrides({})
    assert out["server"]["api_url"] == "https://courses.example/api"
    assert out["lesson"]["api_base"] == "https://courses.example/api"


def test_course_url_does_not_overwrite_author_provided_api_base(monkeypatch):
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.example/api/")
    out = _apply_lesson_env_overrides(
        {"lesson": {"api_base": "https://author.example/keep"}}
    )
    assert out["server"]["api_url"] == "https://courses.example/api"
    assert out["lesson"]["api_base"] == "https://author.example/keep"


def test_auto_enable_predicate_arms_runtime_when_full_prereqs_present(monkeypatch):
    """Flag absent + mint secret + asset origin + api_base -> runtime auto-armed."""
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "s3cr3t")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example/origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.example/api")
    out = _apply_lesson_env_overrides({})
    assert out["lesson"]["runtime_enabled"] is True


def test_auto_enable_uses_server_api_url_when_lesson_api_base_missing(monkeypatch):
    """The auto-enable api_base resolution falls back to server.api_url."""
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "s3cr3t")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example/origin")
    out = _apply_lesson_env_overrides(
        {"server": {"api_url": "https://server.example/api"}, "lesson": {}}
    )
    assert out["lesson"]["runtime_enabled"] is True


def test_auto_enable_stays_dark_without_mint_secret(monkeypatch):
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example/origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.example/api")
    out = _apply_lesson_env_overrides({})
    assert out["lesson"].get("runtime_enabled") is not True


def test_lesson_max_bytes_non_integer_env_is_dropped(monkeypatch):
    """LESSON_MAX_ASSET_BYTES that won't parse to int is swallowed (returns None).

    Drives the ValueError leg of ``_parse_positive_int_env`` (used by the lesson
    byte-limit overrides) so a malformed operator value cannot crash boot.
    """
    monkeypatch.setenv("LESSON_MAX_ASSET_BYTES", "not-an-int")
    out = _apply_lesson_env_overrides({"lesson": {}})
    assert "max_asset_bytes" not in out["lesson"]
    assert _parse_positive_int_env("LESSON_MAX_ASSET_BYTES") is None


def test_apply_overrides_returns_non_mapping_config_untouched():
    """``_apply_lesson_env_overrides`` short-circuits on a non-Mapping config."""
    sentinel = ["not", "a", "mapping"]
    assert _apply_lesson_env_overrides(sentinel) is sentinel


def test_boot_safe_guard_is_noop_for_non_mapping_config():
    """The lesson boot-safe assert returns early on a non-Mapping config."""
    assert _assert_lesson_runtime_boot_safe(["not", "a", "mapping"]) is None


def test_explicit_flag_false_blocks_auto_enable(monkeypatch):
    """Explicit LESSON_RUNTIME_ENABLED=false wins even with full prereqs present."""
    monkeypatch.setenv("LESSON_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "s3cr3t")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://cdn.example/origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.example/api")
    out = _apply_lesson_env_overrides({})
    assert out["lesson"]["runtime_enabled"] is False
