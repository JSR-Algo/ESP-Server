from abc import ABC, abstractmethod


class VoiceSessionProvider(ABC):
    @abstractmethod
    async def start_session(self):
        """Start voice session lifecycle."""

    @abstractmethod
    async def handle_text_message(self, message):
        """Handle inbound text message."""

    @abstractmethod
    async def handle_audio_bytes(self, audio_bytes):
        """Handle inbound audio payload."""

    @abstractmethod
    async def interrupt(self):
        """Interrupt active session work."""

    @abstractmethod
    async def close(self):
        """Close provider resources."""

