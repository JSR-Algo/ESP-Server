from types import SimpleNamespace

import pytest

from core.providers.asr import openai as openai_asr


@pytest.mark.asyncio
async def test_openai_asr_records_last_error_on_http_failure(tmp_path, monkeypatch):
    class _Response:
        status_code = 401
        text = "unauthorized"

    def _post(*_args, **_kwargs):
        return _Response()

    monkeypatch.setattr(openai_asr.requests, "post", _post)
    provider = openai_asr.ASRProvider(
        {
            "api_key": "bad-key",
            "base_url": "https://example.test/asr",
            "model_name": "whisper",
            "output_dir": str(tmp_path),
        },
        delete_audio_file=True,
    )
    audio_file = tmp_path / "input.wav"
    audio_file.write_bytes(b"fake")

    text, file_path = await provider.speech_to_text(
        [b"opus"],
        "session-1",
        "opus",
        SimpleNamespace(file_path=str(audio_file)),
    )

    assert text == ""
    assert file_path is None
    assert "401" in provider.last_error
