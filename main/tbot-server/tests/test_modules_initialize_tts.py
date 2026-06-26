from core.utils.modules_initialize import _selected_module


def test_selected_tts_falls_back_to_edge_when_selected_module_missing():
    config = {"selected_module": {}, "TTS": {"EdgeTTS": {"type": "edge"}}}

    assert _selected_module(config, "TTS") == "EdgeTTS"


def test_selected_tts_falls_back_to_only_configured_provider():
    config = {"selected_module": {}, "TTS": {"OnlyTTS": {"type": "custom"}}}

    assert _selected_module(config, "TTS") == "OnlyTTS"
