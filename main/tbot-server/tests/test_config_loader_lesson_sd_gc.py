from config.config_loader import _apply_lesson_env_overrides, _merge_local_lesson_asset_pack_settings
import pytest
import yaml
from pathlib import Path
from core.lesson.sd_pack_gc import SdPackGarbageCollector


def test_sd_gc_env_overrides_follow_lesson_config_pattern(monkeypatch):
    monkeypatch.setenv("LESSON_SD_CACHE_QUOTA_BYTES", "734003200")
    monkeypatch.setenv("LESSON_SD_GC_FREE_PERCENT", "20")
    monkeypatch.setenv("LESSON_SD_PRELOAD_MIN_FREE_PERCENT", "5")
    config = {"lesson": {}}

    _apply_lesson_env_overrides(config)

    assert config["lesson"]["sd_cache_quota_bytes"] == 734003200
    assert config["lesson"]["sd_gc_free_percent"] == 20
    assert config["lesson"]["sd_preload_min_free_percent"] == 5


def test_sd_gc_settings_survive_manager_api_config_merge():
    api = {"lesson": {}}
    local = {
        "lesson": {
            "sd_cache_quota_bytes": 123,
            "sd_gc_free_percent": 20,
            "sd_preload_min_free_percent": 5,
        }
    }

    _merge_local_lesson_asset_pack_settings(api, local)

    assert api["lesson"] == local["lesson"]


def test_sd_gc_percent_boundaries_and_relationship(monkeypatch, tmp_path):
    monkeypatch.setenv("LESSON_SD_GC_FREE_PERCENT", "100")
    monkeypatch.setenv("LESSON_SD_PRELOAD_MIN_FREE_PERCENT", "0")
    config = {"lesson": {}}
    _apply_lesson_env_overrides(config)
    assert config["lesson"]["sd_gc_free_percent"] == 100
    assert config["lesson"]["sd_preload_min_free_percent"] == 0
    SdPackGarbageCollector(tmp_path, gc_free_percent=100, preload_min_free_percent=0)

    with pytest.raises(ValueError, match="between 0 and 100"):
        SdPackGarbageCollector(tmp_path, gc_free_percent=101)
    with pytest.raises(ValueError, match="must not exceed"):
        SdPackGarbageCollector(tmp_path, gc_free_percent=20, preload_min_free_percent=21)


def test_invalid_env_relationship_is_not_applied_and_yaml_defaults_are_exact(monkeypatch):
    monkeypatch.setenv("LESSON_SD_GC_FREE_PERCENT", "10")
    monkeypatch.setenv("LESSON_SD_PRELOAD_MIN_FREE_PERCENT", "20")
    config = {"lesson": {}}
    _apply_lesson_env_overrides(config)
    assert "sd_gc_free_percent" not in config["lesson"]
    assert "sd_preload_min_free_percent" not in config["lesson"]

    yaml_path = Path(__file__).parents[1] / "config.yaml"
    shipped = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["lesson"]
    assert shipped["sd_gc_free_percent"] == 20
    assert shipped["sd_preload_min_free_percent"] == 5
