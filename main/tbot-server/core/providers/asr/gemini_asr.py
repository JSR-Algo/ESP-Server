import base64
import asyncio
import os
import time
from typing import List, Optional, Tuple

import requests

from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__
logger = setup_logging()

DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

LANGUAGE_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        self.interface_type = InterfaceType.NON_STREAM
        self.api_key = config.get("api_key")
        self.model = config.get("model_name", "gemini-2.5-flash")
        self.api_base = config.get("api_url", DEFAULT_API_BASE).rstrip("/")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file
        self.timeout = int(config.get("timeout", 60))
        self.language = config.get("language", "vi")
        self.prompt = config.get("prompt") or self._default_prompt()

        proxies = {}
        if config.get("http_proxy"):
            proxies["http"] = config["http_proxy"]
        if config.get("https_proxy"):
            proxies["https"] = config["https_proxy"]
        self.proxies = proxies or None

        os.makedirs(self.output_dir, exist_ok=True)

    def requires_file(self) -> bool:
        return True

    def _default_prompt(self) -> str:
        language_name = LANGUAGE_NAMES.get(self.language, self.language)
        return (
            f"Transcribe this audio as {language_name}. "
            "Return only the spoken text. Do not translate, explain, add punctuation-only text, "
            "or include language labels."
        )

    async def speech_to_text(
        self,
        opus_data: List[bytes],
        session_id: str,
        audio_format="opus",
        artifacts=None,
    ) -> Tuple[Optional[str], Optional[str]]:
        if artifacts is None or not artifacts.file_path:
            return "", None

        file_path = artifacts.file_path
        try:
            with open(file_path, "rb") as audio_file:
                audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")

            body = {
                "contents": [
                    {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/wav",
                                    "data": audio_b64,
                                }
                            },
                            {"text": self.prompt},
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "text/plain",
                },
            }

            url = f"{self.api_base}/models/{self.model}:generateContent?key={self.api_key}"
            start_time = time.time()
            response = await asyncio.to_thread(
                requests.post,
                url,
                json=body,
                timeout=self.timeout,
                proxies=self.proxies,
                headers={"Content-Type": "application/json"},
            )
            logger.bind(tag=TAG).debug(
                f"Gemini ASR took: {time.time() - start_time:.3f}s | status: {response.status_code}"
            )

            if response.status_code != 200:
                raise Exception(
                    f"Gemini ASR request failed: {response.status_code} - {response.text}"
                )

            data = response.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            return text, file_path
        except Exception as e:
            logger.bind(tag=TAG).error(f"Gemini ASR recognition failed: {e}")
            return "", None
