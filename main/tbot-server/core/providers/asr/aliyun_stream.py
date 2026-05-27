import json
import time
import uuid
import hmac
import base64
import hashlib
import asyncio
import requests
import websockets
import opuslib_next
from urllib import parse
from datetime import datetime
from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class AccessToken:
    @staticmethod
    def _encode_text(text):
        encoded_text = parse.quote_plus(text)
        return encoded_text.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    @staticmethod
    def _encode_dict(dic):
        keys = dic.keys()
        dic_sorted = [(key, dic[key]) for key in sorted(keys)]
        encoded_text = parse.urlencode(dic_sorted)
        return encoded_text.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    @staticmethod
    def create_token(access_key_id, access_key_secret):
        parameters = {
            "AccessKeyId": access_key_id,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": "cn-shanghai",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid1()),
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Version": "2019-02-28",
        }
        query_string = AccessToken._encode_dict(parameters)
        string_to_sign = (
            "GET" + "&" + AccessToken._encode_text("/") + "&" + AccessToken._encode_text(query_string)
        )
        secreted_string = hmac.new(
            bytes(access_key_secret + "&", encoding="utf-8"),
            bytes(string_to_sign, encoding="utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(secreted_string)
        signature = AccessToken._encode_text(signature)
        full_url = "http://nls-meta.cn-shanghai.aliyuncs.com/?Signature=%s&%s" % (signature, query_string)
        response = requests.get(full_url)
        if response.ok:
            root_obj = response.json()
            if "Token" in root_obj:
                return root_obj["Token"]["Id"], root_obj["Token"]["ExpireTime"]
        return None, None


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
        self.server_ready = False  # Server readyStatus

        # Basic Config
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.appkey = config.get("appkey")
        self.token = config.get("token")
        self.host = config.get("host", "nls-gateway-cn-shanghai.aliyuncs.com")
        # If configured as intranet address (contains-internal.aliyuncs.com), then usewsProtocol, default iswssProtocol
        if "-internal." in self.host:
            self.ws_url = f"ws://{self.host}/ws/v1"
        else:
            # Default UsewssProtocol
            self.ws_url = f"wss://{self.host}/ws/v1"

        self.max_sentence_silence = config.get("max_sentence_silence")
        self.output_dir = config.get("output_dir", "./audio_output")
        self.delete_audio_file = delete_audio_file
        self.expire_time = None

        self.task_id = uuid.uuid4().hex

        # TokenManage
        if self.access_key_id and self.access_key_secret:
            self._refresh_token()
        elif not self.token:
            raise ValueError("Must provide access_key_id+access_key_secret or provide token directly")

    def _refresh_token(self):
        """Refresh Token"""
        self.token, expire_time_str = AccessToken.create_token(self.access_key_id, self.access_key_secret)
        if not self.token:
            raise ValueError("Cannot get valid access Token")
        
        try:
            expire_str = str(expire_time_str).strip()
            if expire_str.isdigit():
                expire_time = datetime.fromtimestamp(int(expire_str))
            else:
                expire_time = datetime.strptime(expire_str, "%Y-%m-%dT%H:%M:%SZ")
            self.expire_time = expire_time.timestamp() - 60
        except:
            self.expire_time = None

    def _is_token_expired(self):
        """Check whether Token expired"""
        return self.expire_time and time.time() > self.expire_time

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn, audio, audio_have_voice):
        # Call parent class method first for base logic
        await super().receive_audio(conn, audio, audio_have_voice)

        # Only when hasVoiceEstablish connection when no connection exists (exclude stopping state)
        if audio_have_voice and not self.is_processing and not self.asr_ws:
            try:
                await self._start_recognition(conn)
            except Exception as e:
                logger.bind(tag=TAG).error(f"Start recognition failed: {str(e)}")
                await self._cleanup()
                return

        if self.asr_ws and self.is_processing and self.server_ready:
            try:
                pcm_frame = self.decoder.decode(audio, 960)
                await self.asr_ws.send(pcm_frame)
            except Exception as e:
                logger.bind(tag=TAG).warning(f"Send audio failed: {str(e)}")
                await self._cleanup()

    async def _start_recognition(self, conn: "ConnectionHandler"):
        """Start recognition session"""
        if self._is_token_expired():
            self._refresh_token()
        
        # Establish Connection
        headers = {"X-NLS-Token": self.token}
        self.asr_ws = await websockets.connect(
            self.ws_url,
            additional_headers=headers,
            max_size=1000000000,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        )

        self.task_id = uuid.uuid4().hex

        logger.bind(tag=TAG).debug(f"WebSocket connection established, task_id: {self.task_id}")

        self.is_processing = True
        self.server_ready = False  # Reset server prepareStatus
        self.forward_task = asyncio.create_task(self._forward_results(conn))

        # Send start request
        start_request = {
            "header": {
                "namespace": "SpeechTranscriber",
                "name": "StartTranscription",
                "message_id": uuid.uuid4().hex,
                "task_id": self.task_id,
                "appkey": self.appkey
            },
            "payload": {
                "format": "pcm",
                "sample_rate": 16000,
                "enable_intermediate_result": True,
                "enable_punctuation_prediction": True,
                "enable_inverse_text_normalization": True,
                "max_sentence_silence": self.max_sentence_silence,
                "enable_voice_detection": False,
            }
        }
        await self.asr_ws.send(json.dumps(start_request, ensure_ascii=False))
        logger.bind(tag=TAG).debug("Start request sent, waiting for server to be ready...")

    async def _forward_results(self, conn: "ConnectionHandler"):
        """Forward recognition result"""
        try:
            while not conn.stop_event.is_set():
                # Get audio data of current connection
                audio_data = conn.asr_audio
                try:
                    response = await self.asr_ws.recv()
                    result = json.loads(response)

                    header = result.get("header", {})
                    payload = result.get("payload", {})
                    message_name = header.get("name", "")
                    status = header.get("status", 0)

                    if status != 20000000:
                        if status == 40010004:
                            logger.bind(tag=TAG).warning(f"Please close connection after server response completes, status code: {status}")
                            break
                        if status in [40000004, 40010003]:  # Connection timeout or client disconnected
                            logger.bind(tag=TAG).warning(f"Connection issue, status code: {status}")
                            break
                        elif status in [40270002, 40270003]:  # Audio Issue
                            logger.bind(tag=TAG).warning(f"Audio processing issue, status code: {status}")
                            continue
                        else:
                            logger.bind(tag=TAG).error(f"RecognizeError,Status code: {status}, Message: {header.get('status_text', '')}")
                            continue

                    # Receiving TranscriptionStarted means server is ready to receive audio data
                    if message_name == "TranscriptionStarted":
                        self.server_ready = True
                        logger.bind(tag=TAG).debug("Server ready, start sending cached audio...")

                        # Send cached audio
                        if conn.asr_audio:
                            for cached_audio in conn.asr_audio[-10:]:
                                try:
                                    pcm_frame = self.decoder.decode(cached_audio, 960)
                                    await self.asr_ws.send(pcm_frame)
                                except Exception as e:
                                    logger.bind(tag=TAG).warning(f"Send cached audio failed: {e}")
                                    break
                        continue
                    elif message_name == "SentenceEnd":
                        # Sentence end (triggered for each sentence)
                        text = payload.get("result", "")
                        if text:
                            logger.bind(tag=TAG).info(f"Recognized text: {text}")

                            # Accumulate recognition results in manual mode
                            if conn.client_listen_mode == "manual":
                                if self.text:
                                    self.text += text
                                else:
                                    self.text = text

                                # In manual mode, only after receivingstopTrigger processing only after signal (process once only)
                                if conn.client_voice_stop:
                                    logger.bind(tag=TAG).debug("Received final recognition result, trigger processing")
                                    await self.handle_voice_stop(conn, audio_data)
                                    break
                            else:
                                # Direct overwrite in auto mode
                                self.text = text
                                await self.handle_voice_stop(conn, audio_data)
                                break

                except asyncio.TimeoutError:
                    logger.bind(tag=TAG).error("Receive result timeout")
                    break
                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("ASR service connection closed")
                    self.is_processing = False
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"Process result failed: {str(e)}")
                    break

        except Exception as e:
            logger.bind(tag=TAG).error(f"Result forwarding failed: {str(e)}")
        finally:
            # Clean connection audio cache
            await self._cleanup()
            conn.reset_audio_states()

    async def _send_stop_request(self):
        """Send stop recognition request (do not close connection)"""
        if self.asr_ws:
            try:
                # Stop audio sending first
                self.is_processing = False

                stop_msg = {
                    "header": {
                        "namespace": "SpeechTranscriber",
                        "name": "StopTranscription",
                        "message_id": uuid.uuid4().hex,
                        "task_id": self.task_id,
                        "appkey": self.appkey
                    }
                }
                logger.bind(tag=TAG).debug("Stop recognition request sent")
                await self.asr_ws.send(json.dumps(stop_msg, ensure_ascii=False))
            except Exception as e:
                logger.bind(tag=TAG).error(f"Send stop recognitionRequest failed: {e}")

    async def _cleanup(self):
        """Clean up resources (close connection)"""
        logger.bind(tag=TAG).debug(f"Start ASR session cleanup | current state: processing={self.is_processing}, server_ready={self.server_ready}")

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
        """Close resources"""
        await self._cleanup()
        if hasattr(self, 'decoder') and self.decoder is not None:
            try:
                del self.decoder
                self.decoder = None
                logger.bind(tag=TAG).debug("Aliyun decoder resources released")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"Error releasing Aliyun decoder resources: {e}")
