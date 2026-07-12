"""Per-env LESSON_* / COURSE_BACKEND_URL override branches in
``config_loader._apply_lesson_env_overrides``.

Covers the still-uncovered override arms (round-1 coverage push):

* ``LESSON_ASSET_PUBLIC_BASE_URL``   -> lesson.asset_public_base_url   (line 204)
* ``LESSON_ASSET_DELIVERY_MODE``     -> lesson.asset_delivery_mode     (line 206)
* ``LESSON_ASSET_PACK_LOCAL_ROOT``   -> lesson.asset_pack_local_root   (line 208)
* ``LESSON_ASSET_PACK_MOUNT_ROOT``   -> lesson.asset_pack_mount_root   (line 210)
* ``LESSON_STEP_TIMEOUT_FLOOR_SEC``  float-parse success + ValueError  (212-215)
* ``LESSON_MAX_ASSET_BYTES``         -> lesson.max_asset_bytes         (line 217)
* ``LESSON_MAX_TOTAL_ASSET_BYTES``   -> lesson.max_total_asset_bytes   (line 219)
* ``COURSE_BACKEND_URL`` block: normalize, create missing server cfg,
  set api_url, and authoritatively set lesson.api_base                 (222-229)
* auto-derive runtime_enabled only from explicit backend URL prereqs    (line 234)

Pure-function level: no network, no event loop. The function reads
``os.environ`` directly, so the autouse fixture clears every relevant
variable so an ambient shell/CI value cannot leak into an assertion.
"""

from pathlib import Path

import pytest

from config.config_loader import (
    _apply_lesson_env_overrides,
    _parse_bool_env,
    get_project_dir,
    read_config,
)

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
    "LESSON_SAMPLE_ENABLED",
    "LESSON_SAMPLE_ASSET_BASE",
    "LESSON_SAMPLE_MODE",
    "TBOT_DEVICE_MINT_SECRET",
)


@pytest.fixture(autouse=True)
def _clear_lesson_env(monkeypatch):
    for name in _LESSON_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


# ── per-env asset override arms (204, 206, 208, 210) ─────────────────────────────


def test_asset_public_base_url_override_strips_trailing_slash(monkeypatch):
    """LESSON_ASSET_PUBLIC_BASE_URL -> lesson.asset_public_base_url, rstrip'd."""
    monkeypatch.setenv("LESSON_ASSET_PUBLIC_BASE_URL", "https://cdn.example/assets/")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["asset_public_base_url"] == "https://cdn.example/assets"


def test_asset_delivery_mode_override(monkeypatch):
    """LESSON_ASSET_DELIVERY_MODE -> lesson.asset_delivery_mode (verbatim)."""
    monkeypatch.setenv("LESSON_ASSET_DELIVERY_MODE", "sd_pack")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["asset_delivery_mode"] == "sd_pack"


def test_asset_pack_local_root_override_strips_trailing_slash(monkeypatch):
    """LESSON_ASSET_PACK_LOCAL_ROOT -> lesson.asset_pack_local_root, rstrip'd."""
    monkeypatch.setenv("LESSON_ASSET_PACK_LOCAL_ROOT", "/var/lib/tbot/packs/")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["asset_pack_local_root"] == "/var/lib/tbot/packs"


def test_asset_pack_mount_root_override_strips_trailing_slash(monkeypatch):
    """LESSON_ASSET_PACK_MOUNT_ROOT -> lesson.asset_pack_mount_root, rstrip'd."""
    monkeypatch.setenv("LESSON_ASSET_PACK_MOUNT_ROOT", "/mnt/sd/tbot/lesson-assets/")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["asset_pack_mount_root"] == "/mnt/sd/tbot/lesson-assets"


# ── built-in sample-lesson demo flags ────────────────────────────────────────────


def test_default_config_starts_interactive_sample_lesson():
    """Default robot config maps spoken start_lesson to the interactive sample demo."""
    config = read_config(f"{get_project_dir()}config.yaml")

    assert config["lesson"]["sample_lesson"] is True
    assert config["lesson"]["sample_mode"] == "interactive"
    assert config["lesson"]["asset_delivery_mode"] == "sd_pack"
    assert config["lesson"]["asset_pack_local_root"] == "sd://tbot/lesson-assets"
    assert config["lesson"]["asset_pack_mount_root"] == "/sdcard/tbot/lesson-assets"


def test_empty_boolean_env_is_absent_not_false(monkeypatch):
    """Compose forwards unset env as empty strings; empty must not override config."""
    monkeypatch.setenv("LESSON_SAMPLE_ENABLED", "")

    assert _parse_bool_env("LESSON_SAMPLE_ENABLED") is None


def test_production_compose_disables_sample_while_local_compose_preserves_demo():
    """Production must not expose the assignment-bypassing sample lesson by default."""
    project_dir = Path(get_project_dir())
    local_compose = (project_dir / "docker-compose.yml").read_text()
    prod_compose = (project_dir.parents[1] / "deploy" / "docker-compose.prod.yml").read_text()

    assert "LESSON_SAMPLE_ENABLED=${LESSON_SAMPLE_ENABLED:-false}" not in local_compose
    assert "LESSON_SAMPLE_MODE=${LESSON_SAMPLE_MODE:-passive}" not in local_compose
    assert "LESSON_SAMPLE_ENABLED: ${LESSON_SAMPLE_ENABLED:-false}" in prod_compose
    assert "LESSON_SAMPLE_MODE: ${LESSON_SAMPLE_MODE:-passive}" not in prod_compose


def test_prod_env_example_declares_shared_lesson_runtime_secrets():
    project_dir = Path(get_project_dir())
    env_example = (project_dir.parents[1] / "deploy" / ".env.example").read_text()

    for key in (
        "LESSON_RUNTIME_ENABLED",
        "LESSON_ASSET_PACK_LOCAL_ROOT",
        "LESSON_ASSET_PACK_MOUNT_ROOT",
        "COURSE_BACKEND_URL",
        "TBOT_DEVICE_MINT_SECRET",
        "TBOT_REQUIRE_DEVICE_TOKEN",
        "JWT_PUBLIC_KEY",
        "LESSON_ASSET_ORIGIN_BASE",
    ):
        assert f"{key}=" in env_example


def test_sample_enabled_override_sets_sample_lesson_bool(monkeypatch):
    """LESSON_SAMPLE_ENABLED -> lesson.sample_lesson (bool); NOT coupled to runtime_enabled."""
    monkeypatch.setenv("LESSON_SAMPLE_ENABLED", "true")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["sample_lesson"] is True
    # The sample flag must NEVER auto-enable the production runtime.
    assert config["lesson"].get("runtime_enabled") in (None, False)


def test_sample_asset_base_override_strips_trailing_slash(monkeypatch):
    """LESSON_SAMPLE_ASSET_BASE -> lesson.sample_asset_base_url, rstrip'd."""
    monkeypatch.setenv("LESSON_SAMPLE_ASSET_BASE", "https://cdn.example/sample/")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["sample_asset_base_url"] == "https://cdn.example/sample"


def test_sample_mode_override_works_without_other_lesson_env(monkeypatch):
    """LESSON_SAMPLE_MODE alone selects the sample lesson interaction mode."""
    monkeypatch.setenv("LESSON_SAMPLE_MODE", "passive")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["sample_mode"] == "passive"

def test_voice_rt_p95_disable_ms_override_sets_lesson_threshold(monkeypatch):
    monkeypatch.setenv("LESSON_VOICE_RT_P95_DISABLE_MS", "1750")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["voice_rt_p95_disable_ms"] == 1750.0


def test_sample_flags_absent_is_a_noop(monkeypatch):
    """With only the sample flags absent (and nothing else set), the function early-returns
    the config untouched — no spurious lesson.sample_lesson key."""
    config = _apply_lesson_env_overrides({"lesson": {}})

    assert "sample_lesson" not in config["lesson"]
    assert "sample_asset_base_url" not in config["lesson"]


# ── step-timeout floor float-parse try/except (212-215) ──────────────────────────


def test_step_timeout_floor_parses_numeric(monkeypatch):
    """A numeric LESSON_STEP_TIMEOUT_FLOOR_SEC is parsed and clamped at >= 0.0."""
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "2.5")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["step_timeout_floor_sec"] == 2.5


def test_step_timeout_floor_clamps_negative_to_zero(monkeypatch):
    """A negative floor is clamped to 0.0 (max(0.0, float(...)) arm)."""
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "-3")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["step_timeout_floor_sec"] == 0.0


def test_step_timeout_floor_non_numeric_is_ignored(monkeypatch):
    """A non-numeric floor hits the ValueError->pass arm and sets nothing."""
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "not-a-float")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert "step_timeout_floor_sec" not in config["lesson"]


# ── byte-budget override arms (217, 219) ─────────────────────────────────────────


def test_max_asset_bytes_override(monkeypatch):
    """LESSON_MAX_ASSET_BYTES (positive int) -> lesson.max_asset_bytes."""
    monkeypatch.setenv("LESSON_MAX_ASSET_BYTES", "1048576")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["max_asset_bytes"] == 1048576


def test_max_total_asset_bytes_override(monkeypatch):
    """LESSON_MAX_TOTAL_ASSET_BYTES (positive int) -> lesson.max_total_asset_bytes."""
    monkeypatch.setenv("LESSON_MAX_TOTAL_ASSET_BYTES", "20971520")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"]["max_total_asset_bytes"] == 20971520


# ── COURSE_BACKEND_URL block (222-229) ───────────────────────────────────────────


def test_course_backend_url_creates_missing_server_cfg_and_sets_both(monkeypatch):
    """COURSE_BACKEND_URL with NO preexisting server cfg.

    Drives the create-server-cfg arm (224-226): server is created, api_url set
    to the normalized (trailing-slash-stripped) URL, and lesson.api_base falls
    back to the same value because the lesson cfg has no api_base yet.
    """
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.tbot.dev/api/")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["server"]["api_url"] == "https://courses.tbot.dev/api"
    assert config["lesson"]["api_base"] == "https://courses.tbot.dev/api"


def test_course_backend_url_with_existing_server_cfg_overrides_lesson_api_base(monkeypatch):
    """COURSE_BACKEND_URL with a preexisting server cfg AND author lesson.api_base.

    server.api_url and lesson.api_base are both overwritten so runtime pulls from
    the explicit production backend URL, not a shipped/stale default.
    """
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://courses.tbot.dev/api")

    config = _apply_lesson_env_overrides(
        {
            "lesson": {"api_base": "https://author.example/lesson"},
            "server": {"api_url": "https://old.example", "other": "keep"},
        }
    )

    assert config["server"]["api_url"] == "https://courses.tbot.dev/api"
    assert config["server"]["other"] == "keep"
    assert config["lesson"]["api_base"] == "https://courses.tbot.dev/api"


# ── auto-derive runtime_enabled requires explicit backend URL (line 234) ───


def test_auto_enable_requires_explicit_backend_url_when_lesson_api_base_absent(monkeypatch):
    """No explicit flag, no COURSE_BACKEND_URL, no lesson.api_base.

    Mint secret + asset origin + server.api_url are not enough; production
    lessons must be armed by an explicit backend URL env override.
    """
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")

    config = _apply_lesson_env_overrides(
        {
            "lesson": {},  # no api_base -> forces server.api_url fallback
            "server": {"api_url": "https://server.example/api"},
        }
    )

    assert config["lesson"].get("runtime_enabled") in (None, False)


def test_no_auto_enable_when_no_api_base_anywhere(monkeypatch):
    """Mint secret + asset origin but NO api_base in lesson or server.

    The line-234 fallback finds nothing, so api_base stays falsy and the runtime
    must NOT auto-enable (predicate's ``and api_base`` is False).
    """
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")

    config = _apply_lesson_env_overrides({"lesson": {}})

    assert config["lesson"].get("runtime_enabled") in (None, False)
