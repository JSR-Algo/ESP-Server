"""Close the remaining config_loader coverage gaps on the lesson-touching boot paths.

The sibling ``test_config_loader_edges.py`` already drives most of ``config.config_loader``
through its real (importlib-loaded) module, but eleven statements stay uncovered because
no existing test hits these specific branches:

  * 214  -- lesson auto-enable refuses implicit ``server.api_url`` fallback unless an
            explicit production backend URL env is present
  * 280  -- production boot ``RuntimeError`` for missing env prerequisites
  * 285  -- production boot ``TBOT_REQUIRE_DEVICE_TOKEN`` not truthy-bool
  * 287  -- production boot ``ADMIN_AUTH_DISABLED`` must not be true
  * 294  -- AEC readiness early-return when ``voice_mode`` is not ``google_live``
  * 307-308 -- production boot requires *active* AEC; the REAL ``AecProcessor`` is
            bypassed when ``aec_enabled`` is false, so the runtime error fires
  * 366, 368 -- ``load_config`` repairing non-Mapping default / custom config
  * 376-379 -- ``load_config`` refusing to fetch manager-api config from a running loop

These exercise the same boot-safety contract the lesson runtime depends on
(``lesson.runtime_enabled`` requires a coherent ``api_base`` + mint secret + asset origin).
The harness mirrors ``test_config_loader_edges.py``: load the REAL module via importlib so
the conftest ``config.manage_api_client`` stub does not shadow the dependency.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

config_loader = None


def _load_real_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_real_config_loader():
    root = Path(__file__).resolve().parents[1]
    previous_manage_api = sys.modules.get("config.manage_api_client")
    sentinel = object()
    if previous_manage_api is None:
        previous_manage_api = sentinel
    real_manage_api = _load_real_module(
        "config.manage_api_client",
        root / "config" / "manage_api_client.py",
    )
    sys.modules["config.manage_api_client"] = real_manage_api
    try:
        return _load_real_module(
            "config._config_loader_lesson_gaps",
            root / "config" / "config_loader.py",
        )
    finally:
        if previous_manage_api is sentinel:
            sys.modules.pop("config.manage_api_client", None)
        else:
            sys.modules["config.manage_api_client"] = previous_manage_api


def _clear_config_cache():
    from core.utils.cache.config import CacheType
    from core.utils.cache.manager import cache_manager

    cache_manager.clear(CacheType.CONFIG)


@pytest.fixture(autouse=True)
def clean_config_cache():
    global config_loader
    config_loader = _load_real_config_loader()
    _clear_config_cache()
    yield
    _clear_config_cache()


def _clear_production_env(monkeypatch):
    for name in (
        "NODE_ENV",
        "TBOT_REQUIRE_DEVICE_TOKEN",
        "JWT_PUBLIC_KEY",
        "TBOT_DEVICE_MINT_SECRET",
        "LESSON_ASSET_ORIGIN_BASE",
        "LESSON_MAX_ASSET_BYTES",
        "LESSON_MAX_TOTAL_ASSET_BYTES",
        "ADMIN_AUTH_DISABLED",
        "LESSON_RUNTIME_ENABLED",
        "COURSE_BACKEND_URL",
    ):
        monkeypatch.delenv(name, raising=False)


# ── line 214: lesson auto-enable falls back to server.api_url for api_base ────────


def test_lesson_auto_enable_refuses_server_api_url_without_explicit_backend_env(monkeypatch):
    """flag absent + runtime off + no explicit backend URL env keeps runtime dark.

    A shipped or config-file ``server.api_url`` is not enough to arm production
    lessons; COURSE_BACKEND_URL/TBOT_BACKEND_API_URL must be explicit.
    """
    _clear_production_env(monkeypatch)
    monkeypatch.delenv("LESSON_RUNTIME_ENABLED", raising=False)
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin/")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    config = {
        "lesson": {"runtime_enabled": False},
        "server": {"api_url": "https://server.only/v1"},
    }

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"].get("runtime_enabled") is False


def test_lesson_auto_enable_skipped_when_no_endpoint_anywhere(monkeypatch):
    """Mutation guard for line 214/215: with api_base falling back to a *missing*
    server.api_url, the gate stays closed (api_base is None) and runtime is NOT
    auto-enabled -- proves 214 actually feeds the 215 condition rather than being
    dead code."""
    _clear_production_env(monkeypatch)
    monkeypatch.delenv("LESSON_RUNTIME_ENABLED", raising=False)
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin/")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    config = {"lesson": {"runtime_enabled": False}, "server": {}}

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"].get("runtime_enabled") is False


# ── lines 280-287: production boot env prerequisites ──────────────────────────────


def test_production_boot_raises_on_missing_env_prereqs(monkeypatch):
    """auth.enabled True but the four required env vars absent -> line 280 RuntimeError."""
    _clear_production_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "production")
    config = {"server": {"auth": {"enabled": True}, "auth_key": "auth-key"}}

    with pytest.raises(RuntimeError, match="missing: TBOT_REQUIRE_DEVICE_TOKEN"):
        config_loader._assert_production_boot_safe(config)


def test_production_boot_requires_device_token_truthy_bool(monkeypatch):
    """All env present (so the missing-list is empty) but TBOT_REQUIRE_DEVICE_TOKEN
    parses to False -> line 285 RuntimeError. _clean_env sees it as set (non-empty),
    so only the _parse_bool_env check rejects it."""
    _clear_production_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("TBOT_REQUIRE_DEVICE_TOKEN", "false")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "key")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.test")
    config = {"server": {"auth": {"enabled": True}, "auth_key": "auth-key"}}

    with pytest.raises(RuntimeError, match="TBOT_REQUIRE_DEVICE_TOKEN=true"):
        config_loader._assert_production_boot_safe(config)


def test_production_boot_rejects_admin_auth_disabled(monkeypatch):
    """Device token truthy but ADMIN_AUTH_DISABLED=true -> line 287 RuntimeError."""
    _clear_production_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("TBOT_REQUIRE_DEVICE_TOKEN", "true")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "key")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.test")
    monkeypatch.setenv("ADMIN_AUTH_DISABLED", "true")
    config = {"server": {"auth": {"enabled": True}, "auth_key": "auth-key"}}

    with pytest.raises(RuntimeError, match="ADMIN_AUTH_DISABLED must not be true"):
        config_loader._assert_production_boot_safe(config)


# ── AEC readiness early-return for non-google_live voice_mode ─────────────────────


def test_aec_ready_check_noop_for_non_google_live(monkeypatch):
    """voice_mode != google_live -> _assert_production_google_live_aec_ready returns
    early without constructing an AecProcessor."""
    _clear_production_env(monkeypatch)
    config = {
        "server": {"auth": {"enabled": True}, "auth_key": "auth-key"},
        "voice_mode": {"type": "classic_pipeline"},
    }

    # No raise: this helper's own non-google_live branch skips the AEC gate.
    assert config_loader._assert_production_google_live_aec_ready(config) is None


# ── lines 307-308: production boot requires active AEC (real bypassed processor) ───


def test_production_boot_raises_when_real_aec_bypassed(monkeypatch):
    """google_live voice mode with aec_enabled false -> the REAL AecProcessor reports
    bypassed=True -> lines 307-308 RuntimeError. No monkeypatch of AecProcessor: the
    genuine pure-Python DSP gate is exercised."""
    _clear_production_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("TBOT_REQUIRE_DEVICE_TOKEN", "true")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "key")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.test")
    config = {
        "server": {"auth": {"enabled": True}, "auth_key": "auth-key"},
        "voice_mode": {"type": "google_live"},
        "google_live": {"aec_enabled": False},
    }

    with pytest.raises(RuntimeError, match="production boot requires active AEC; bypassed="):
        config_loader._assert_production_boot_safe(config)


# ── lines 366, 368: load_config repairs non-Mapping default / custom config ───────


def test_load_config_repairs_non_mapping_configs(monkeypatch):
    """read_config returning non-Mapping values for both default and custom config
    -> lines 366 and 368 coerce them to {} before merge. No manager-api url, so the
    plain merge_configs path runs (line 382) and boot-safety passes (NODE_ENV unset)."""
    _clear_production_env(monkeypatch)
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: "/project/")
    monkeypatch.setattr(config_loader, "read_config", lambda _path: ["not", "a", "mapping"])
    monkeypatch.setattr(config_loader, "ensure_directories", lambda config: None)

    result = config_loader.load_config()

    # Both configs coerced to {} -> normalize_voice_config supplies the default mode.
    assert result["voice_mode"]["type"] == "classic_pipeline"


# ── lines 376-379: load_config refuses manager-api fetch inside a running loop ────


def test_load_config_rejects_manager_api_fetch_in_running_loop(monkeypatch):
    """When a manager-api url is present AND an event loop is already running,
    load_config must raise (lines 376-379) telling the caller to use
    load_config_async() instead -- the cross-process/event-loop boundary guard."""
    _clear_production_env(monkeypatch)
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: "/project/")
    monkeypatch.setattr(
        config_loader,
        "read_config",
        lambda path: {"manager-api": {"url": "http://manager", "secret": "s"}}
        if path.endswith("data/.config.yaml")
        else {},
    )
    monkeypatch.setattr(config_loader, "ensure_directories", lambda config: None)

    async def _invoke_inside_running_loop():
        # We ARE inside a running loop here; load_config must refuse.
        return config_loader.load_config()

    with pytest.raises(RuntimeError, match="cannot fetch manager-api config from a running event loop"):
        asyncio.run(_invoke_inside_running_loop())
