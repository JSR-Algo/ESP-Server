"""Google Gemini TTS provider.

Uses the Gemini generateContent REST endpoint with
``responseModalities=["AUDIO"]`` so a Gemini API key (the same one used
for LLM calls) is sufficient — no GCP service account required.

The model returns raw PCM 16-bit mono at 24000 Hz; this provider wraps
it into a WAV container so downstream audio handling (PCM/Opus
conversion in :class:`TTSProviderBase`) sees a normal audio file.

Supported models include ``gemini-3.1-flash-tts-preview``,
``gemini-2.5-flash-preview-tts``, and ``gemini-2.5-pro-preview-tts``.
"""

import base64
import io
import os
import uuid
import wave
from datetime import datetime
from typing import Optional

import requests

from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()

GEMINI_TTS_SAMPLE_RATE = 24000
GEMINI_TTS_CHANNELS = 1
GEMINI_TTS_SAMPLE_WIDTH = 2  # 16-bit PCM

DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Wrap raw 16-bit mono 24kHz PCM in a WAV header."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(GEMINI_TTS_CHANNELS)
        wf.setsampwidth(GEMINI_TTS_SAMPLE_WIDTH)
        wf.setframerate(GEMINI_TTS_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key")
        self.model = config.get("model_name", "gemini-3.1-flash-tts-preview")
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "Kore")
        # Optional natural-language style steering, e.g. "Read in a warm, calm tone".
        self.style_instructions: Optional[str] = config.get("style_instructions")
        self.api_base = config.get("api_url", DEFAULT_API_BASE).rstrip("/")
        self.audio_file_type = "wav"
        self.timeout = int(config.get("timeout", 60))

        proxies = {}
        if config.get("http_proxy"):
            proxies["http"] = config["http_proxy"]
        if config.get("https_proxy"):
            proxies["https"] = config["https_proxy"]
        self.proxies = proxies or None

        msg = check_model_key("TTS", self.api_key)
        if msg:
            logger.bind(tag=TAG).error(msg)

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def text_to_speak(self, text, output_file):
        if self.style_instructions:
            prompt_text = f"{self.style_instructions}: {text}"
        else:
            prompt_text = text

        body = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.voice}
                    }
                },
            },
        }
        url = f"{self.api_base}/models/{self.model}:generateContent?key={self.api_key}"

        resp = requests.post(
            url,
            json=body,
            timeout=self.timeout,
            proxies=self.proxies,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise Exception(
                f"Gemini TTS request failed: {resp.status_code} - {resp.text}"
            )

        data = resp.json()
        try:
            inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm_b64 = inline["data"]
        except (KeyError, IndexError) as e:
            raise Exception(
                f"Gemini TTS response missing audio inlineData: {data}"
            ) from e

        pcm_bytes = base64.b64decode(pcm_b64)
        wav_bytes = _pcm_to_wav(pcm_bytes)

        if output_file:
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "wb") as f:
                f.write(wav_bytes)
            return None
        return wav_bytes
