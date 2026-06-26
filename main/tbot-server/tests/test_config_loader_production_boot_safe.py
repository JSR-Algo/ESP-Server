"""Coverage for the production / lesson boot-safety guards in ``config_loader``.

Two uncovered regions are exercised here, both pure-function level (no network,
no event loop, no manage-api):

1. ``_assert_lesson_runtime_boot_safe`` early return when the config is not a
   ``Mapping`` (config_loader.py:252). A non-Mapping config (e.g. ``None`` or a
   list) must be tolerated silently rather than raising — this is the defensive
   guard that lets the boot path call the assertion before the config shape is
   fully validated.

2. ``_assert_production_boot_safe`` (config_loader.py:278-308). This guard only
   engages when ``NODE_ENV == "production"`` (trivially set in-process via
   monkeypatch — it is NOT a production-network dependency), then enforces:
     * ``server.auth.enabled is True``;
     * the env prerequisites ``TBOT_REQUIRE_DEVICE_TOKEN`` / ``JWT_PUBLIC_KEY`` /
       ``TBOT_DEVICE_MINT_SECRET`` / ``LESSON_ASSET_ORIGIN_BASE`` are all present;
     * ``TBOT_REQUIRE_DEVICE_TOKEN`` parses truthy;
     * ``ADMIN_AUTH_DISABLED`` is not truthy.
   The happy path uses a non-``google_live`` voice_mode so the AEC sub-assertion
   (``_assert_production_google_live_aec_ready``) is reached and returns early —
   keeping this file off any audio/AEC dependency.

Env hygiene: every variable the guards read is cleared by the autouse fixture so
ambient shell/CI values cannot leak in and falsely pass/skip an assertion.
"""

import pytest

from config.config_loader import (
    _assert_lesson_runtime_boot_safe,
    _assert_production_boot_safe,
)

# Every env var the production / lesson boot guards consult.
_PROD_BOOT_ENV = (
    "NODE_ENV",
    "TBOT_REQUIRE_DEVICE_TOKEN",
    "JWT_PUBLIC_KEY",
    "TBOT_DEVICE_MINT_SECRET",
    "LESSON_ASSET_ORIGIN_BASE",
    "ADMIN_AUTH_DISABLED",
)


@pytest.fixture(autouse=True)
def _clear_prod_boot_env(monkeypatch):
    for name in _PROD_BOOT_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def _full_prod_env(monkeypatch):
    """Set the complete set of satisfied production env prerequisites."""
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("TBOT_REQUIRE_DEVICE_TOKEN", "true")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "-----BEGIN PUBLIC KEY-----xyz")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")


def _auth_on_config():
    """A config whose server.auth.enabled is True (the only non-env precondition)."""
    return {"server": {"auth": {"enabled": True}}, "voice_mode": {"type": "edge"}}


# ── 1. lesson boot-safe: non-Mapping early return (line 252) ──────────────────────


@pytest.mark.parametrize("bad_config", [None, ["not", "a", "mapping"], "string", 42])
def test_lesson_boot_safe_tolerates_non_mapping(bad_config):
    """A non-Mapping config must short-circuit to ``None`` without raising.

    Drives config_loader.py:252 — the defensive guard that lets the boot path
    call the assertion before the config shape is known-good.
    """
    assert _assert_lesson_runtime_boot_safe(bad_config) is None


# ── 2. production boot-safe gate (lines 278-308) ──────────────────────────────────


def test_prod_boot_safe_noop_when_not_production():
    """Outside production the guard is a no-op (line 278-279).

    No env is set (autouse fixture cleared NODE_ENV), so the guard returns before
    touching any precondition — even a config that would otherwise fail.
    """
    assert _assert_production_boot_safe({"server": {}}) is None


def test_prod_boot_safe_rejects_non_mapping_config(monkeypatch):
    """In production a non-Mapping config is normalized to ``{}`` then fails on auth.

    Drives lines 280-287: the ``config = {}`` reassignment plus the
    ``auth_enabled is not True`` rejection (auth is absent in the empty dict).
    """
    monkeypatch.setenv("NODE_ENV", "production")
    with pytest.raises(RuntimeError, match="server.auth.enabled=true"):
        _assert_production_boot_safe(None)


def test_prod_boot_safe_rejects_auth_disabled(monkeypatch):
    """server.auth.enabled not True must hard-fail (lines 283-287)."""
    monkeypatch.setenv("NODE_ENV", "production")
    config = {"server": {"auth": {"enabled": False}}}
    with pytest.raises(RuntimeError, match="server.auth.enabled=true"):
        _assert_production_boot_safe(config)


def test_prod_boot_safe_rejects_missing_env_prereqs(monkeypatch):
    """auth on, but env prerequisites missing => names the missing vars (289-303)."""
    monkeypatch.setenv("NODE_ENV", "production")
    # Auth satisfied, but none of the four env prereqs are set.
    config = _auth_on_config()
    with pytest.raises(RuntimeError) as excinfo:
        _assert_production_boot_safe(config)
    message = str(excinfo.value)
    assert "TBOT_REQUIRE_DEVICE_TOKEN" in message
    assert "JWT_PUBLIC_KEY" in message
    assert "TBOT_DEVICE_MINT_SECRET" in message
    assert "LESSON_ASSET_ORIGIN_BASE" in message


def test_prod_boot_safe_rejects_non_truthy_require_device_token(monkeypatch):
    """All env vars PRESENT but TBOT_REQUIRE_DEVICE_TOKEN parses falsy (304-305).

    The ``missing`` list is empty (the var is non-empty -> present), so execution
    reaches the explicit truthiness re-check that rejects a present-but-falsy
    value like ``"false"``.
    """
    _full_prod_env(monkeypatch)
    monkeypatch.setenv("TBOT_REQUIRE_DEVICE_TOKEN", "false")
    config = _auth_on_config()
    with pytest.raises(RuntimeError, match="TBOT_REQUIRE_DEVICE_TOKEN=true"):
        _assert_production_boot_safe(config)


def test_prod_boot_safe_rejects_admin_auth_disabled(monkeypatch):
    """ADMIN_AUTH_DISABLED truthy must hard-fail (lines 306-307)."""
    _full_prod_env(monkeypatch)
    monkeypatch.setenv("ADMIN_AUTH_DISABLED", "true")
    config = _auth_on_config()
    with pytest.raises(RuntimeError, match="ADMIN_AUTH_DISABLED must not be true"):
        _assert_production_boot_safe(config)


def test_prod_boot_safe_passes_with_all_prereqs(monkeypatch):
    """Fully satisfied production posture passes through to line 308 and returns.

    voice_mode.type != "google_live" so the AEC sub-assertion is reached and
    returns early — covering the call site (308) without any audio dependency.
    """
    _full_prod_env(monkeypatch)
    config = _auth_on_config()  # voice_mode.type == "edge" -> AEC check no-ops
    assert _assert_production_boot_safe(config) is None
