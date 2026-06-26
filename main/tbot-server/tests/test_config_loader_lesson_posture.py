"""CR-TRIG-05 (P0, robot-server) — pin the lesson runtime_enabled *posture*.

Catalog gap: ``docs/qa/course-robot-test-catalog.md`` row CR-TRIG-05 asks us to
pin that the lesson gate is **env-derived, not file-derived**, because
``config_loader._apply_lesson_env_overrides`` auto-enables the runtime when the
production lesson prerequisites (course backend URL + ``TBOT_DEVICE_MINT_SECRET``
+ ``LESSON_ASSET_ORIGIN_BASE``) are present, and ``_assert_lesson_runtime_boot_safe``
is the boot guard that must reject a file-derived ``runtime_enabled: true`` when
those prerequisites are absent.

These tests drive the REAL functions against the REAL shipped ``config.yaml``
(loaded via ``config_loader.read_config`` / ``get_project_dir`` — the exact path
``load_config`` uses), so they pin the bytes that actually ship, not a synthetic
dict. Pure-function level: no network, no manage-api, no event loop.

Reconciliation note (live code vs. the gap's framing): the gap text says
"config.yaml ships runtime_enabled:true". The CURRENT shipped file ships
``runtime_enabled: false`` (dark default). These tests assert the TRUE current
posture: the file is dark and the effective gate is derived from env.

Env hygiene: ``_apply_lesson_env_overrides`` and ``_assert_lesson_runtime_boot_safe``
read ``os.environ`` directly, so the autouse fixture below clears every relevant
variable. Without that, an ambient ``LESSON_*`` / ``TBOT_DEVICE_MINT_SECRET`` in
the developer/CI shell could make a "stays dark" assertion falsely pass or make a
boot-safe assertion falsely skip.
"""

import copy

import pytest

from config.config_loader import (
    _apply_lesson_env_overrides,
    _assert_lesson_runtime_boot_safe,
    get_project_dir,
    read_config,
)

# Every env var that participates in the lesson posture / boot-safety logic.
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
    """Pin a known-empty posture env so ambient shell/CI vars cannot leak in."""
    for name in _LESSON_POSTURE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def _shipped_config():
    """The exact dict ``load_config`` reads from the real shipped ``config.yaml``."""
    return read_config(get_project_dir() + "config.yaml")


# ── 1. file-derived posture: the shipped file is DARK ────────────────────────────


def test_shipped_config_yaml_ships_lesson_runtime_disabled():
    """The committed config.yaml must ship the lesson runtime OFF (dark default).

    This is the file-derived half of the contract: shipping ``true`` here would
    auto-arm the runtime on every deploy regardless of env, which is the exact
    drift CR-TRIG-05 guards against.
    """
    lesson = _shipped_config()["lesson"]

    assert lesson["runtime_enabled"] is False
    # Sanity: the file ships an api_base (used as the env-less fallback below) but
    # does NOT pre-ship the asset prerequisites, so the file alone cannot satisfy
    # the auto-enable predicate.
    assert lesson.get("api_base")
    assert "asset_origin_base" not in lesson


# ── 2. effective gate is ENV-derived ────────────────────────────────────────────


def test_shipped_dark_config_auto_enables_when_env_prereqs_present(monkeypatch):
    """Starting from the shipped DARK file, full env prereqs flip the gate ON.

    Proves the gate is env-derived: the file says ``false`` but the effective
    posture after ``_apply_lesson_env_overrides`` is ``true``. Note no
    ``COURSE_BACKEND_URL`` is set — the auto-enable predicate uses the shipped
    ``lesson.api_base`` as the backend fallback, so mint-secret + asset origin are
    sufficient. That api_base fallback is itself a drift surface worth pinning.
    """
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")

    config = _apply_lesson_env_overrides(copy.deepcopy(_shipped_config()))

    assert config["lesson"]["runtime_enabled"] is True
    assert config["lesson"]["asset_origin_base"] == "https://assets.origin"
    # And the resulting armed posture is boot-safe (env prereqs are present).
    assert _assert_lesson_runtime_boot_safe(config) is None


def test_shipped_dark_config_stays_dark_without_env_prereqs():
    """Shipped DARK file + NO env prereqs => the gate stays OFF.

    The complementary env-derived proof: absent the env, nothing arms the runtime,
    and the dark posture is trivially boot-safe (the guard is a no-op when off).
    """
    config = _apply_lesson_env_overrides(copy.deepcopy(_shipped_config()))

    assert config["lesson"]["runtime_enabled"] is False
    assert _assert_lesson_runtime_boot_safe(config) is None


def test_explicit_env_false_overrides_present_prereqs(monkeypatch):
    """``LESSON_RUNTIME_ENABLED=false`` wins even when auto-enable prereqs are set.

    Pins that the explicit operator kill-switch is honored over auto-enable, so a
    deploy can force the runtime dark without editing the config volume.
    """
    monkeypatch.setenv("LESSON_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")

    config = _apply_lesson_env_overrides(copy.deepcopy(_shipped_config()))

    assert config["lesson"]["runtime_enabled"] is False


# ── 3. boot-safe assertion rejects unsafe file-derived posture ───────────────────


def test_boot_assert_rejects_file_derived_true_without_env_prereqs():
    """A file-derived ``runtime_enabled: true`` with NO env prereqs must HARD-FAIL.

    This is the boot-safe guard that turns the dangerous drift posture (someone
    flips the committed file to true on a host that lacks the mint secret / asset
    origin) from a silent half-armed runtime into a loud boot crash. The error
    must name the exact missing prerequisites.
    """
    config = copy.deepcopy(_shipped_config())
    config["lesson"]["runtime_enabled"] = True

    with pytest.raises(RuntimeError) as excinfo:
        _assert_lesson_runtime_boot_safe(config)

    message = str(excinfo.value)
    assert "runtime_enabled=true requires boot env prerequisites" in message
    assert "TBOT_DEVICE_MINT_SECRET" in message
    assert "LESSON_ASSET_ORIGIN_BASE" in message


def test_boot_assert_rejects_sd_pack_without_mount_root(monkeypatch):
    """Armed runtime in sd_pack mode without a mount root must HARD-FAIL.

    Even with the base env prereqs satisfied, sd_pack delivery requires
    ``LESSON_ASSET_PACK_MOUNT_ROOT`` so verified bytes are materialized before
    firmware reads ``sd://`` paths. Pins that the guard catches this second
    posture hole.
    """
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    config = copy.deepcopy(_shipped_config())
    config["lesson"]["runtime_enabled"] = True
    config["lesson"]["asset_delivery_mode"] = "sd_pack"
    config["lesson"].pop("asset_pack_mount_root", None)

    with pytest.raises(RuntimeError, match="sd_pack requires LESSON_ASSET_PACK_MOUNT_ROOT"):
        _assert_lesson_runtime_boot_safe(config)

    # Supplying the mount root clears the guard.
    config["lesson"]["asset_pack_mount_root"] = "/mnt/sd/tbot/lesson-assets"
    assert _assert_lesson_runtime_boot_safe(config) is None
