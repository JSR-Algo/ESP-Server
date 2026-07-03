from core.providers.tts.base import TTSProviderBase, _is_normal_audio_send_close
from core.providers.tts.dto.dto import SentenceType


class _SegmentingTts(TTSProviderBase):
    async def text_to_speak(self, text, output_file):
        return b""

class _FailingPrimaryTts(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.calls = 0

    async def text_to_speak(self, text, output_file):
        self.calls += 1
        raise RuntimeError("primary down")

class _QuotaFailingPrimaryTts(_FailingPrimaryTts):
    async def text_to_speak(self, text, output_file):
        self.calls += 1
        raise RuntimeError("Gemini TTS request failed: 429 - quota exceeded")

class _FallbackTts(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.audio_file_type = "mp3"

    async def text_to_speak(self, text, output_file):
        return b"fallback-audio"


def test_segment_text_includes_closing_quote_after_sentence_punctuation():
    provider = _SegmentingTts({"output_dir": "tmp/"}, True)
    provider.is_first_sentence = False
    provider.tts_text_buff = [' Wave and say "Hello, TeeBot!"']

    segment = provider._get_segment_text()

    assert segment == "Wave and say Hello, TeeBot"
    assert provider.processed_chars == len(' Wave and say "Hello, TeeBot!"')


def test_segment_text_includes_multiple_closing_marks_after_punctuation():
    provider = _SegmentingTts({"output_dir": "tmp/"}, True)
    provider.is_first_sentence = False
    provider.tts_text_buff = ["Try barn!) Next"]

    segment = provider._get_segment_text()

    assert segment == "Try barn"
    assert provider.processed_chars == len("Try barn!)")


def test_segment_text_combines_too_short_sentence_with_next_sentence():
    provider = _SegmentingTts({"output_dir": "tmp/"}, True)
    provider.is_first_sentence = False
    provider.tts_text_buff = ["Hey there! TeeBot is ready."]

    segment = provider._get_segment_text()

    assert segment == "Hey there! TeeBot is ready"
    assert provider.processed_chars == len("Hey there! TeeBot is ready.")

def test_tts_stream_uses_edge_fallback_after_primary_failures(monkeypatch):
    provider = _FailingPrimaryTts(
        {
            "output_dir": "tmp/",
            "fallback_tts": {"type": "edge", "voice": "en-US-JennyNeural"},
        },
        True,
    )
    provider.conn = type("Conn", (), {"sample_rate": 24000})()
    provider.opus_encoder = object()
    provider.current_sentence_id = "s1"
    provider._create_fallback_tts_provider = lambda: _FallbackTts({"output_dir": "tmp/"}, True)
    converted = []

    def fake_audio_bytes_to_data_stream(audio_bytes, **kwargs):
        converted.append((audio_bytes, kwargs["file_type"]))
        kwargs["callback"](b"opus-fallback")

    monkeypatch.setattr(
        "core.providers.tts.base.audio_bytes_to_data_stream",
        fake_audio_bytes_to_data_stream,
    )

    provider.to_tts_stream("Say barn.", opus_handler=provider.handle_opus)

    assert converted == [(b"fallback-audio", "mp3")]
    assert provider.tts_audio_queue.get_nowait() == (
        SentenceType.FIRST,
        None,
        "Say barn.",
        "s1",
    )
    assert provider.tts_audio_queue.get_nowait() == (
        SentenceType.MIDDLE,
        b"opus-fallback",
        None,
        "s1",
    )

def test_tts_stream_fast_fallbacks_and_caches_after_quota_error(monkeypatch):
    provider = _QuotaFailingPrimaryTts(
        {
            "output_dir": "tmp/",
            "fallback_tts": {"type": "edge", "voice": "en-US-JennyNeural"},
            "fallback_after_primary_error_cooldown_sec": 60,
        },
        True,
    )
    provider.conn = type("Conn", (), {"sample_rate": 24000})()
    provider.opus_encoder = object()
    provider.current_sentence_id = "s1"
    provider._create_fallback_tts_provider = lambda: _FallbackTts({"output_dir": "tmp/"}, True)
    converted = []

    def fake_audio_bytes_to_data_stream(audio_bytes, **kwargs):
        converted.append((audio_bytes, kwargs["file_type"]))
        kwargs["callback"](b"opus-fallback")

    monkeypatch.setattr(
        "core.providers.tts.base.audio_bytes_to_data_stream",
        fake_audio_bytes_to_data_stream,
    )

    provider.to_tts_stream("Robot chưa nghe rõ.", opus_handler=provider.handle_opus)
    provider.to_tts_stream("Con nói lại nhé.", opus_handler=provider.handle_opus)

    assert provider.calls == 1
    assert converted == [(b"fallback-audio", "mp3"), (b"fallback-audio", "mp3")]


def test_audio_thread_classifies_normal_websocket_close_as_non_error():
    class _Close(Exception):
        def __str__(self):
            return "received 1000 (OK); then sent 1000 (OK)"

    assert _is_normal_audio_send_close(_Close()) is True
    assert _is_normal_audio_send_close(RuntimeError("received 1011 internal error")) is False
