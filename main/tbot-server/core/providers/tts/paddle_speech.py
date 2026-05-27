import io
import wave
import json
import base64
import asyncio
import websockets
import numpy as np
from datetime import datetime
from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase



TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 3, 1.0, lambda v: round(float(v), 1)),
        ("ttsRate", "speed", 0, 3, 1.0, lambda v: round(float(v), 1)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.url = config.get("url", "ws://192.168.1.10:8092/paddlespeech/tts/streaming")
        self.protocol = config.get("protocol", "websocket")
        
        if config.get("private_voice"):
            self.spk_id = int(config.get("private_voice"))
        else:
            self.spk_id = int(config.get("spk_id", "0"))

        speed = config.get("speed", 1.0)
        self.speed = float(speed) if speed else 1.0
        
        volume = config.get("volume", 1.0)
        self.volume = float(volume) if volume else 1.0
        
        self.delete_audio_file = config.get("delete_audio", True)

        # Apply percentage adjustment (if exists), otherwise use public config
        self._apply_percentage_params(config)

        if not self.delete_audio_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = config.get("save_path")
            if save_path:
                if not save_path.endswith('.wav'):
                    save_path = f"{save_path}_{timestamp}.wav"
                else:
                    other_path = save_path[:-4]
                    save_path = f"{other_path}_{timestamp}.wav"
                self.save_path = save_path
            else:
                self.save_path = f"./streaming_tts_{timestamp}.wav"
        else:
            self.save_path = None

    async def pcm_to_wav(self, pcm_data: bytes, sample_rate: int = 24000, num_channels: int = 1,
                         bits_per_sample: int = 16) -> bytes:
        """
        will PCM Convert data to WAV File and return byte data
        :param pcm_data: PCM Data (raw byte stream)
        :param sample_rate: Audio sample rate, default24000
        :param num_channels: Channel count, default mono
        :param bits_per_sample: bits per sample, default16
        :return: WAV Format byte data
        """
        byte_data = np.frombuffer(pcm_data, dtype=np.int16)  # 16positionPCM
        wav_io = io.BytesIO()

        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(num_channels)
            wav_file.setsampwidth(bits_per_sample // 8)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(byte_data.tobytes())

        return wav_io.getvalue()

    async def text_to_speak(self, text, output_file):
        if self.protocol == "websocket":
            return await self.text_streaming(text, output_file)
        else:
            raise ValueError("Unsupported protocol. Please use 'websocket' or 'http'.")

    async def text_streaming(self, text, output_file):
        try:
            # Use websockets Async connect to WebSocket Server
            async with websockets.connect(self.url) as ws:
                # Send start request
                start_request = {
                    "task": "tts",
                    "signal": "start"
                }
                await ws.send(json.dumps(start_request))

                # Receive StartResponseAnd extract session_id
                start_response = await ws.recv()
                start_response = json.loads(start_response)  # Parse JSON Response
                if start_response.get("status") != 0:
                    raise Exception(f"Connection Failed: {start_response.get('signal')}")

                session_id = start_response.get("session")

                # Send text data to synthesize
                data_request = {
                    "text": text,
                    "spk_id": self.spk_id,
                }
                await ws.send(json.dumps(data_request))

                audio_chunks = b""
                timeout_seconds = 60  # Set timeout
                try:
                    while True:
                        response = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
                        response = json.loads(response)  # Parse JSON Response
                        status = response.get("status")

                        if status == 2:  # Last packet
                            break
                        else:
                            # Concatenate audio data (base64 Encoded PCM Data)
                            audio_chunks += base64.b64decode(response.get("audio"))
                except asyncio.TimeoutError:
                    raise Exception(f"WebSocket Timeout: waiting for audio data exceeds {timeout_seconds} seconds")

                # After concatenation PCM Convert data to WAV Format
                wav_data = await self.pcm_to_wav(audio_chunks)

                # End Request
                end_request = {
                    "task": "tts",
                    "signal": "end",
                    "session": session_id  # Session ID Must match start request
                }
                await ws.send(json.dumps(end_request))

                # Receive EndResponseAvoid service throwingException
                await ws.recv()

                # Decide whether based on configSaveFile
                if not self.delete_audio_file and self.save_path:
                    with open(self.save_path, "wb") as f:
                        f.write(wav_data)
                    logger.bind(tag=TAG).info(f"Audio filealreadySaveto: {self.save_path}")
                
                # Return orSaveAudio Data
                if output_file:
                    with open(output_file, "wb") as file_to_save:
                        file_to_save.write(wav_data)
                else:
                    return wav_data

        except Exception as e:
            raise Exception(f"Error during TTS WebSocket request: {e} while processing text: {text}")