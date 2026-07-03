from core.handle.intentHandler import speak_txt
from core.voice.session_provider.base import VoiceSessionProvider


class ClassicPipelineProvider(VoiceSessionProvider):
    """Provider wrapper for legacy voice pipeline flow."""

    def __init__(self, conn):
        self.conn = conn

    async def start_session(self):
        await self.conn._start_classic_pipeline_session()

    async def handle_text_message(self, message):
        return False

    async def handle_audio_bytes(self, audio_bytes):
        return False

    async def speak_child_notice(self, text):
        speak_txt(self.conn, text)
        return True

    async def interrupt(self):
        self.conn.client_abort = True

    async def close(self):
        return None
