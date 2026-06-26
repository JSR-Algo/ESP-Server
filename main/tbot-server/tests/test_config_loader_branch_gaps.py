"""Close the remaining *branch* coverage gaps in ``config.config_loader``.

``test_config_loader_edges.py`` + ``test_config_loader_lesson_coverage_gaps.py``
already give 100% *statement* coverage, but ``--cov-branch`` still flags nine
partial branches whose false-edge is never taken:

  * 113->115 -- normalize_voice_config: voice_mode != google_live AND google_live
                IS already a Mapping (so the ``config["google_live"] = {}`` repair
                is skipped, leaving the caller-supplied mapping intact)
  * 185->187 -- _apply_lesson_env_overrides: ``asset_origin`` falsy while some other
                lesson env IS set (so the function does not early-return but skips
                the asset_origin_base assignment)
  * 208->210 -- _apply_lesson_env_overrides: COURSE_BACKEND_URL present AND
                lesson.api_base ALREADY set (so the api_base fallback is skipped)
  * 329->331 -- _apply_server_endpoint_env_overrides: api_url env set but websocket
                env absent (websocket assignment skipped)
  * 331->347 -- _apply_server_endpoint_env_overrides: websocket env set but api_url
                env absent (api_url block skipped, early return)
  * 443->453 -- get_config_from_api_async: input config has no ``server`` key (the
                local-source server copy is skipped)
  * 455->457 -- get_config_from_api_async: config_data already carries a
                prompt_template (the local-prompt fallback is skipped)
  * 502->498 -- ensure_directories: an ASR/TTS provider Mapping with no output_dir
                (loop continues without adding a dir)
  * 517->507 -- ensure_directories: a selected_module provider Mapping with no
                output_dir (loop continues without adding a dir)

Harness mirrors ``test_config_loader_lesson_coverage_gaps.py``: the REAL module is
importlib-loaded so the conftest ``config.manage_api_client`` stub does not shadow
the dependency, and coverage attributes the lines to the shared source file.
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
            "config._config_loader_branch_gaps",
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


def _clear_lesson_env(monkeypatch):
    for name in (
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
        "TBOT_PUBLIC_WEBSOCKET_URL",
        "TBOT_BACKEND_API_URL",
        "TBOT_CLOSE_CONNECTION_NO_VOICE_TIME",
    ):
        monkeypatch.delenv(name, raising=False)


# ── 113->115: non-google_live voice_mode with an existing google_live Mapping ─────


def test_normalize_keeps_existing_google_live_mapping_for_classic_mode():
    """voice_mode is classic_pipeline AND google_live is already a Mapping -> the
    elif's ``config["google_live"] = {}`` repair (line 114) is SKIPPED, preserving
    the caller's mapping (branch 113->115). Mutation guard: if the source dropped
    the ``not isinstance`` check and always reset google_live, this asserted dict
    would be wiped to {}."""
    config = {
        "voice_mode": {"type": "classic_pipeline"},
        "google_live": {"language_code": "en-US", "barge_in": True},
    }

    result = config_loader.normalize_voice_config(config)

    # Mapping preserved untouched (NOT reset to {}), and NOT merged with the
    # google_live defaults (that only happens on the google_live branch).
    assert result["google_live"] == {"language_code": "en-US", "barge_in": True}


# ── 185->187: asset_origin falsy while other lesson env is set ─────────────────────


def test_lesson_env_skips_asset_origin_when_only_public_base_set(monkeypatch):
    """LESSON_ASSET_PUBLIC_BASE_URL set but LESSON_ASSET_ORIGIN_BASE absent -> the
    function does NOT early-return (public base keeps it going) yet the
    ``if asset_origin:`` block is skipped (branch 185->187). Proves asset_public_base
    is applied independently of asset_origin."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.setenv("LESSON_ASSET_PUBLIC_BASE_URL", "https://assets.public/")
    config = {"lesson": {}, "server": {}}

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"]["asset_public_base_url"] == "https://assets.public"
    assert "asset_origin_base" not in result["lesson"]


# ── 208->210: COURSE_BACKEND_URL present AND lesson.api_base already set ───────────


def test_course_backend_url_does_not_clobber_existing_api_base(monkeypatch):
    """COURSE_BACKEND_URL set but the author already pinned lesson.api_base -> the
    ``if not lesson_cfg.get("api_base"):`` guard (line 208) is False, so server.api_url
    is updated but lesson.api_base is preserved (branch 208->210). Mutation guard:
    dropping the ``not`` guard would overwrite the author's api_base."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://course.test/v1/")
    config = {
        "lesson": {"runtime_enabled": True, "api_base": "https://author.pinned/v1"},
        "server": {},
    }

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["server"]["api_url"] == "https://course.test/v1"
    assert result["lesson"]["api_base"] == "https://author.pinned/v1"


# ── 329->331: api_url env set but websocket env absent ─────────────────────────────


def test_server_endpoint_overrides_api_url_without_websocket(monkeypatch):
    """TBOT_BACKEND_API_URL set, TBOT_PUBLIC_WEBSOCKET_URL absent -> the
    ``if websocket_url:`` block (line 329) is skipped, only api_url is applied
    (branch 329->331)."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.setenv("TBOT_BACKEND_API_URL", "https://backend.test/v1/")
    config = {"server": {"websocket": "wss://kept/ws"}}

    result = config_loader._apply_server_endpoint_env_overrides(config)

    assert result["server"]["api_url"] == "https://backend.test/v1"
    # websocket untouched because no websocket env was provided.
    assert result["server"]["websocket"] == "wss://kept/ws"


# ── 331->347: websocket env set but api_url env absent ─────────────────────────────


def test_server_endpoint_overrides_websocket_without_api_url(monkeypatch):
    """TBOT_PUBLIC_WEBSOCKET_URL set, TBOT_BACKEND_API_URL absent -> the
    ``if api_url:`` block (line 331) is skipped and the function returns at 347
    (branch 331->347). server.api_url and lesson.api_base must stay untouched."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.setenv("TBOT_PUBLIC_WEBSOCKET_URL", "wss://public.test/ws")
    config = {
        "server": {"api_url": "https://kept.test/v1"},
        "lesson": {"api_base": "https://kept.test/v1"},
    }

    result = config_loader._apply_server_endpoint_env_overrides(config)

    assert result["server"]["websocket"] == "wss://public.test/ws"
    assert result["server"]["api_url"] == "https://kept.test/v1"
    assert result["lesson"]["api_base"] == "https://kept.test/v1"


# ── 443->453: get_config_from_api_async with no server key in input config ─────────


@pytest.mark.asyncio
async def test_api_config_without_local_server_skips_server_copy(monkeypatch):
    """Input config carries no ``server`` key -> the ``if config.get("server"):``
    block (line 443) is skipped; the server still gets the auth subkey from line 453
    (branch 443->453)."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.setattr(config_loader, "init_service", lambda config: None)

    async def server_config():
        return {"server": {"auth": {"enabled": True}}, "prompt_template": None}

    monkeypatch.setattr(config_loader, "get_server_config", server_config)

    result = await config_loader.get_config_from_api_async(
        {"manager-api": {"url": "http://m", "secret": "s"}}
    )

    # No local-source server overlay (ip/port/etc. absent) but auth subkey set.
    assert result["server"]["auth"] == {"enabled": True}
    assert "ip" not in result["server"]


# ── 455->457: config_data already carries a prompt_template ────────────────────────


@pytest.mark.asyncio
async def test_api_config_keeps_remote_prompt_template(monkeypatch):
    """get_server_config returns a config_data that ALREADY has prompt_template ->
    the ``if not config_data.get("prompt_template"):`` fallback (line 455) is skipped,
    the remote template wins over the local one (branch 455->457). Mutation guard:
    dropping the ``not`` guard would clobber the remote template with the local."""
    _clear_lesson_env(monkeypatch)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.setattr(config_loader, "init_service", lambda config: None)

    async def server_config():
        return {
            "server": {"auth": {"enabled": False}},
            "prompt_template": {"default": "remote"},
        }

    monkeypatch.setattr(config_loader, "get_server_config", server_config)

    result = await config_loader.get_config_from_api_async(
        {
            "manager-api": {"url": "http://m", "secret": "s"},
            "prompt_template": {"default": "local-should-not-win"},
        }
    )

    assert result["prompt_template"] == {"default": "remote"}


# ── 502->498: ASR/TTS provider Mapping with no output_dir ──────────────────────────


def test_ensure_directories_asr_provider_without_output_dir(tmp_path, monkeypatch):
    """An ASR provider that IS a Mapping but has no output_dir -> the inner
    ``if output_dir:`` (line 502) is False, the loop continues (branch 502->498)
    without adding any ASR dir. Only the log dir ends up created."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: str(project) + "/")
    made = []
    monkeypatch.setattr(
        config_loader.os, "makedirs", lambda path, exist_ok=False: made.append(path)
    )

    config_loader.ensure_directories(
        {
            "log": {"log_dir": "logs"},
            "ASR": {"local": {"model": "tiny"}},  # Mapping, but no output_dir
        }
    )

    assert made == [str(project / "logs")]


# ── 517->507: selected_module provider Mapping with no output_dir ──────────────────


def test_ensure_directories_selected_module_without_output_dir(tmp_path, monkeypatch):
    """A selected LLM provider whose config is a Mapping but lacks output_dir -> the
    ``if output_dir:`` (line 517) is False, the loop continues (branch 517->507).
    Only the log dir is created."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: str(project) + "/")
    made = []
    monkeypatch.setattr(
        config_loader.os, "makedirs", lambda path, exist_ok=False: made.append(path)
    )

    config_loader.ensure_directories(
        {
            "log": {"log_dir": "logs"},
            "selected_module": {"LLM": "llm"},
            "LLM": {"llm": {"model": "gpt"}},  # Mapping, but no output_dir
        }
    )

    assert made == [str(project / "logs")]


# Reference the asyncio import so linters don't flag it (used by event-loop guard
# tests elsewhere; kept here for harness parity with the sibling gap suite).
assert asyncio is not None
