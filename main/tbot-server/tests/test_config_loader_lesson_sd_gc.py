from config.config_loader import _apply_lesson_env_overrides, _merge_local_lesson_asset_pack_settings


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
