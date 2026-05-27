import json
import hmac
import base64
import hashlib
import asyncio
import websockets
import opuslib_next
import gc
from time import mktime
from datetime import datetime
from urllib.parse import urlencode
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from config.logger import setup_logging
from wsgiref.handlers import format_date_time
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType

TAG = __name__
logger = setup_logging()

# frameStatusConstant
STATUS_FIRST_FRAME = 0  # First frame identifier
STATUS_CONTINUE_FRAME = 1  # Middle frame flag
STATUS_LAST_FRAME = 2  # Last frame identifier


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.text = ""
        self.decoder = opuslib_next.Decoder(16000, 1)
        self.asr_ws = None
        self.forward_task = None
        self.is_processing = False
        self.server_ready = False

        # iFlytek Config
        self.app_id = config.get("app_id")
        self.api_key = config.get("api_key")
        self.api_secret = config.get("api_secret")

        if not all([self.app_id, self.api_key, self.api_secret]):
            raise ValueError("Must provide app_id, api_key, and api_secret")

        # Recognition Parameters
        self.iat_params = {
            "domain": config.get("domain", "slm"),
            "language": config.get("language", "zh_cn"),
            "accent": config.get("accent", "mandarin"),
            "result": {"encoding": "utf8", "compress": "raw", "format": "plain"},
        }

        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

    def create_url(self) -> str:
        """Generate auth URL"""
        url = "ws://iat.cn-huabei-1.xf-yun.com/v1"
        # GenerateRFC1123Format ofTimestamp
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # Concatenate string
        signature_origin = "host: " + "iat.cn-huabei-1.xf-yun.com" + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + "/v1 " + "HTTP/1.1"

        # Performhmac-sha256Encrypt
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding="utf-8")

        authorization_origin = (
            'api_key="%s", algorithm="%s", headers="%s", signature="%s"'
            % (self.api_key, "hmac-sha256", "host date request-line", signature_sha)
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(
            encoding="utf-8"
        )

        # Combine request auth params into dictionary
        v = {
            "authorization": authorization,
            "date": date,
            "host": "iat.cn-huabei-1.xf-yun.com",
        }

        # Concatenate auth parameters, generateurl
        url = url + "?" + urlencode(v)
        return url

    async def open_audio_channels(self, conn: "ConnectionHandler"):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        # Call parent class method first for base logic
        await super().receive_audio(conn, audio, audio_have_voice)

        # If this time hasVoiceand connection not established before
        if audio_have_voice and self.asr_ws is None and not self.is_processing:
            try:
                await self._start_recognition(conn)
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to establish ASR connection: {str(e)}")
                await self._cleanup()
                return

        # Send current audio data
        if self.asr_ws and self.is_processing and self.server_ready:
            try:
                pcm_frame = self.decoder.decode(audio, 960)
                await self._send_audio_frame(pcm_frame, STATUS_CONTINUE_FRAME)
            except Exception as e:
                logger.bind(tag=TAG).warning(f"Error occurred while sending audio data: {e}")
                await self._cleanup()

    async def _start_recognition(self, conn: "ConnectionHandler"):
        """Start recognition session"""
        try:
            self.is_processing = True
            # EstablishWebSocketConnect
            ws_url = self.create_url()
            logger.bind(tag=TAG).info(f"Connecting to ASR service: {ws_url[:50]}...")

            # If manual mode,Set timeout duration to one minute
            if conn.client_listen_mode == "manual":
                self.iat_params["eos"] = 60000

            self.asr_ws = await websockets.connect(
                ws_url,
                max_size=1000000000,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
            )

            logger.bind(tag=TAG).info("ASR WebSocket connection established")
            self.server_ready = False
            self.forward_task = asyncio.create_task(self._forward_results(conn))

            # Send first audio frame
            if conn.asr_audio and len(conn.asr_audio) > 0:
                first_audio = conn.asr_audio[-1] if conn.asr_audio else b""
                pcm_frame = (
                    self.decoder.decode(first_audio, 960) if first_audio else b""
                )
                await self._send_audio_frame(pcm_frame, STATUS_FIRST_FRAME)
                self.server_ready = True
                logger.bind(tag=TAG).info("First frame sent, recognition started")

                # Send cached audio data
                for cached_audio in conn.asr_audio[-10:]:
                    try:
                        pcm_frame = self.decoder.decode(cached_audio, 960)
                        await self._send_audio_frame(pcm_frame, STATUS_CONTINUE_FRAME)
                    except Exception as e:
                        logger.bind(tag=TAG).info(f"Error sending cached audio data: {e}")
                        break

        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to establish ASR connection: {str(e)}")
            if hasattr(e, "__cause__") and e.__cause__:
                logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
            if self.asr_ws:
                await self.asr_ws.close()
                self.asr_ws = None
            self.is_processing = False
            raise

    async def _send_audio_frame(self, audio_data: bytes, status: int):
        """Send audio frame"""
        if not self.asr_ws:
            return

        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        frame_data = {
            "header": {"status": status, "app_id": self.app_id},
            "parameter": {"iat": self.iat_params},
            "payload": {
                "audio": {"audio": audio_b64, "sample_rate": 16000, "encoding": "raw"}
            },
        }

        await self.asr_ws.send(json.dumps(frame_data, ensure_ascii=False))

    async def _forward_results(self, conn: "ConnectionHandler"):
        """Forward recognition result"""
        try:
            while not conn.stop_event.is_set():
                try:
                    response = await asyncio.wait_for(self.asr_ws.recv(), timeout=60)
                    result = json.loads(response)
                    logger.bind(tag=TAG).debug(f"Received ASR result: {result}")

                    header = result.get("header", {})
                    payload = result.get("payload", {})
                    code = header.get("code", 0)
                    status = header.get("status", 0)

                    if code != 0:
                        logger.bind(tag=TAG).error(
                            f"RecognizeError,Errorcode: {code}, Message: {header.get('message', '')}"
                        )
                        if code in [10114, 10160]:  # Connection Issue
                            break
                        continue

                    # Handle recognition result
                    if payload and "result" in payload:
                        text_data = payload["result"]["text"]
                        if text_data:
                            # Decodebase64Text
                            decoded_text = base64.b64decode(text_data).decode("utf-8")
                            text_json = json.loads(decoded_text)
                            # Extract TextContent
                            text_ws = text_json.get("ws", [])
                            for i in text_ws:
                                for j in i.get("cw", []):
                                    w = j.get("w", "")
                                    self.text += w

                    if status == 2:
                        logger.bind(tag=TAG).debug("Received final recognition result, trigger processing")
                        await self.handle_voice_stop(conn, conn.asr_audio)
                        break

                except asyncio.TimeoutError:
                    logger.bind(tag=TAG).error("Receive result timeout")
                    break
                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("ASR service connection closed")
                    self.is_processing = False
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"ProcessASRError occurred when resultError: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
                    self.is_processing = False
                    break

        except Exception as e:
            logger.bind(tag=TAG).error(f"ASRResult forwarding task occurredError: {str(e)}")
            if hasattr(e, "__cause__") and e.__cause__:
                logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
        finally:
            # Clean connection resources
            await self._cleanup()
            conn.reset_audio_states()

    async def handle_voice_stop(
        self, conn: "ConnectionHandler", asr_audio_task: List[bytes]
    ):
        """Handle voice stop, send last frame and process recognition result"""
        try:
            # Send last frame first to indicate audio end
            if self.asr_ws and self.is_processing:
                try:
                    await self._send_audio_frame(b"", STATUS_LAST_FRAME)
                    logger.bind(tag=TAG).debug(f"Stop request sent")

                    await asyncio.sleep(0.25)
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Send stop request failed: {e}")

            await super().handle_voice_stop(conn, asr_audio_task)
        except Exception as e:
            logger.bind(tag=TAG).error(f"Handle voice stop failed: {e}")
            import traceback

            logger.bind(tag=TAG).debug(f"ExceptionDetails: {traceback.format_exc()}")

    def stop_ws_connection(self):
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False

    async def _send_stop_request(self):
        """Send stop recognition request (do not close connection)"""
        if self.asr_ws:
            try:
                # Stop audio sending first
                self.is_processing = False
                await self._send_audio_frame(b"", STATUS_LAST_FRAME)
                logger.bind(tag=TAG).debug("Stop request sent")
            except Exception as e:
                logger.bind(tag=TAG).error(f"Send stop request failed: {e}")

    async def _cleanup(self):
        """Clean up resources (close connection)"""
        logger.bind(tag=TAG).debug(
            f"Start ASR session cleanup | current state: processing={self.is_processing}, server_ready={self.server_ready}"
        )

        # StatusReset
        self.is_processing = False
        self.server_ready = False
        logger.bind(tag=TAG).debug("ASR status reset")

        # Close Connection
        if self.asr_ws:
            try:
                logger.bind(tag=TAG).debug("Closing WebSocket connection")
                await asyncio.wait_for(self.asr_ws.close(), timeout=2.0)
                logger.bind(tag=TAG).debug("WebSocket connection closed")
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to close WebSocket connection: {e}")
            finally:
                self.asr_ws = None

        # Clean task reference
        self.forward_task = None

        logger.bind(tag=TAG).debug("ASR session cleanup complete")

    async def speech_to_text(self, opus_data, session_id, audio_format, artifacts=None):
        """Get recognition result"""
        result = self.text
        self.text = ""
        return result, None

    async def close(self):
        """Resource cleanup method"""
        if self.asr_ws:
            await self.asr_ws.close()
            self.asr_ws = None
        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None
        self.is_processing = False

        # Explicit ReleasedecoderResource
        if hasattr(self, "decoder") and self.decoder is not None:
            try:
                del self.decoder
                self.decoder = None
                logger.bind(tag=TAG).debug("Xunfei decoder resources released")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"ReleaseXunfei decoderError with resource: {e}")

