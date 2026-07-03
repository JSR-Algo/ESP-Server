"""Boot-safety guard for the lesson runtime: ``_assert_lesson_runtime_boot_safe``.

This guard is WHY the lesson runtime must be enabled via ENV, not by flipping the
committed ``config.yaml`` default. When ``lesson.runtime_enabled`` is truthy the
guard HARD-RAISES at boot unless the deploy provides ``TBOT_DEVICE_MINT_SECRET`` and
``LESSON_ASSET_ORIGIN_BASE`` (and, in sd_pack asset mode, ``asset_pack_mount_root``).
A flipped static default would therefore crash boot in every environment that lacks
those secrets (local, CI, any deploy without them) — so the default stays ``false``
and operators enable per-env. See docs/deploy/lesson-runtime-enablement.md.

Pure-function level: no network, no event loop. The function reads ``os.environ``
directly, so the autouse fixture clears every relevant variable.
"""

import pytest

from config.config_loader import _assert_lesson_runtime_boot_safe

_GUARD_ENV = (
    "TBOT_DEVICE_MINT_SECRET",
    "LESSON_ASSET_ORIGIN_BASE",
    "LESSON_ASSET_PACK_MOUNT_ROOT",
    # runtime_enabled=true also requires a backend URL to arm lessons (added by the
    # lesson-runtime-enablement hardening). Clear it so negative tests stay negative.
    "COURSE_BACKEND_URL",
    "TBOT_BACKEND_API_URL",
)


@pytest.fixture(autouse=True)
def _clear_guard_env(monkeypatch):
    for name in _GUARD_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def _enabled(**lesson):
    cfg = {"lesson": {"runtime_enabled": True}}
    cfg["lesson"].update(lesson)
    return cfg


# ── the dark default is ALWAYS boot-safe (the whole point of the gate) ───────────


def test_runtime_disabled_is_boot_safe_with_no_env():
    # The committed default (runtime_enabled false) must NEVER crash boot, even with
    # zero lesson env present. This is what makes the static default safe to ship.
    assert _assert_lesson_runtime_boot_safe({"lesson": {"runtime_enabled": False}}) is None


def test_missing_lesson_block_is_boot_safe():
    assert _assert_lesson_runtime_boot_safe({}) is None
    assert _assert_lesson_runtime_boot_safe(None) is None


# ── runtime_enabled=true REQUIRES the mint secret + asset origin in env ──────────


def test_enabled_with_both_prerequisites_present_passes(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://backend.example/v1")
    # no raise — this is the supported "live" config
    assert _assert_lesson_runtime_boot_safe(_enabled()) is None


def test_enabled_missing_backend_url_raises(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    with pytest.raises(RuntimeError, match="COURSE_BACKEND_URL"):
        _assert_lesson_runtime_boot_safe(_enabled())


def test_enabled_missing_mint_secret_raises(monkeypatch):
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    with pytest.raises(RuntimeError, match="TBOT_DEVICE_MINT_SECRET"):
        _assert_lesson_runtime_boot_safe(_enabled())


def test_enabled_missing_asset_origin_raises(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    with pytest.raises(RuntimeError, match="LESSON_ASSET_ORIGIN_BASE"):
        _assert_lesson_runtime_boot_safe(_enabled())


def test_enabled_missing_both_lists_both(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _assert_lesson_runtime_boot_safe(_enabled())
    message = str(exc.value)
    assert "TBOT_DEVICE_MINT_SECRET" in message
    assert "LESSON_ASSET_ORIGIN_BASE" in message


# ── sd_pack asset mode additionally requires the mount root ──────────────────────


def test_enabled_sd_pack_without_mount_root_raises(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://backend.example/v1")
    with pytest.raises(RuntimeError, match="LESSON_ASSET_PACK_MOUNT_ROOT"):
        _assert_lesson_runtime_boot_safe(_enabled(asset_delivery_mode="sd_pack"))


def test_enabled_sd_pack_with_mount_root_passes(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://backend.example/v1")
    assert (
        _assert_lesson_runtime_boot_safe(
            _enabled(asset_delivery_mode="sd_pack", asset_pack_mount_root="/mnt/lesson")
        )
        is None
    )
