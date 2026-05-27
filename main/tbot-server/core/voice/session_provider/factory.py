from collections.abc import Mapping

from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider
from core.voice.session_provider.google_live import GoogleLiveProvider


def create_voice_session_provider(conn):
    voice_mode_config = conn.config.get("voice_mode", {})
    if not isinstance(voice_mode_config, Mapping):
        voice_mode_config = {}
    voice_mode = voice_mode_config.get("type", "classic_pipeline")
    if voice_mode == "google_live":
        return GoogleLiveProvider(conn)
    return ClassicPipelineProvider(conn)
