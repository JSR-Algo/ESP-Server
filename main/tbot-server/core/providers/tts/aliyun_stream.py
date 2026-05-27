import os
import uuid
import json
import hmac
import hashlib
import base64
import time
import queue
import asyncio
import traceback
import websockets

from asyncio import Task
from urllib import parse
from datetime import datetime
from typing import Callable, Any
from config.logger import setup_logging
from core.utils.tts import MarkdownCleaner
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType


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
            "RegionId": "cn-shanghai",  # Use Shanghai region forTokenGet
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid1()),
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Version": "2019-02-28",
        }

        query_string = AccessToken._encode_dict(parameters)
        string_to_sign = (
            "GET"
            + "&"
            + AccessToken._encode_text("/")
            + "&"
            + AccessToken._encode_text(query_string)
        )

        secreted_string = hmac.new(
            bytes(access_key_secret + "&", encoding="utf-8"),
            bytes(string_to_sign, encoding="utf-8"),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(secreted_string)
        signature = AccessToken._encode_text(signature)

        full_url = "http://nls-meta.cn-shanghai.aliyuncs.com/?Signature=%s&%s" % (
            signature,
            query_string,
        )

        import requests

        response = requests.get(full_url)
        if response.ok:
            root_obj = response.json()
            key = "Token"
            if key in root_obj:
                token = root_obj[key]["Id"]
                expire_time = root_obj[key]["ExpireTime"]
                return token, expire_time
        return None, None


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speech_rate", -500, 500, 0, int),
        ("ttsPitch", "pitch_rate", -500, 500, 0, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)

        # Set to streaming API type
        self.interface_type = InterfaceType.DUAL_STREAM

        # Basic Config
        self.access_key_id = config.get("access_key_id")
        self.access_key_secret = config.get("access_key_secret")
        self.appkey = config.get("appkey")
        self.format = config.get("format", "pcm")
        self.report_on_last = True

        # Voice Config - CosyVoiceLarge model voice
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "longxiaochun")  # CosyVoiceDefault Voice

        # Audio parameter config
        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50

        speech_rate = config.get("speech_rate", "0")
        self.speech_rate = int(speech_rate) if speech_rate else 0

        pitch_rate = config.get("pitch_rate", "0")
        self.pitch_rate = int(pitch_rate) if pitch_rate else 0

        # Apply percentage adjustment (if exists), otherwise use public config
        self._apply_percentage_params(config)

        # WebSocket config
        self.host = config.get("host", "nls-gateway-cn-beijing.aliyuncs.com")
        # If configured as intranet address (contains-internal.aliyuncs.com), then usewsProtocol, default iswssProtocol
        if "-internal." in self.host:
            self.ws_url = f"ws://{self.host}/ws/v1"
        else:
            # Default UsewssProtocol
            self.ws_url = f"wss://{self.host}/ws/v1"
        self.ws = None
        self._monitor_task = None
        self.activate_session = False
        self.last_active_time = None

        # DedicatedttsSet
        self.task_id = uuid.uuid4().hex

        # TokenManage
        if self.access_key_id and self.access_key_secret:
            self._refresh_token()
        else:
            self.token = config.get("token")
            self.expire_time = None

    def _refresh_token(self):
        """Refresh Token and record expiration time"""
        if self.access_key_id and self.access_key_secret:
            self.token, expire_time_str = AccessToken.create_token(
                self.access_key_id, self.access_key_secret
            )
            if not expire_time_str:
                raise ValueError("Cannot get valid Token expiration time")

            expire_str = str(expire_time_str).strip()

            try:
                if expire_str.isdigit():
                    expire_time = datetime.fromtimestamp(int(expire_str))
                else:
                    expire_time = datetime.strptime(expire_str, "%Y-%m-%dT%H:%M:%SZ")
                self.expire_time = expire_time.timestamp() - 60
            except Exception as e:
                raise ValueError(f"Invalid expiration time format: {expire_str}") from e
        else:
            self.expire_time = None

        if not self.token:
            raise ValueError("Cannot get valid access Token")

    def _is_token_expired(self):
        """Check whether Token expired"""
        if not self.expire_time:
            return False
        return time.time() > self.expire_time

    async def _ensure_connection(self):
        """Ensure WebSocket connection available"""
        try:
            if self._is_token_expired():
                logger.bind(tag=TAG).warning("Token expired, auto-refreshing...")
                self._refresh_token()
            current_time = time.time()
            if self.ws and current_time - self.last_active_time < 10:
                # 10Can reuse link for continuous conversation only within seconds
                self.task_id = uuid.uuid4().hex
                logger.bind(tag=TAG).debug(f"Using existing connection..., task_id: {self.task_id}")
                return self.ws
            logger.bind(tag=TAG).debug("Starting new connection...")

            self.ws = await websockets.connect(
                self.ws_url,
                additional_headers={"X-NLS-Token": self.token},
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10,
            )
            self.task_id = uuid.uuid4().hex
            logger.bind(tag=TAG).debug(f"WebSocket connection established, task_id: {self.task_id}")
            self.last_active_time = time.time()
            return self.ws
        except Exception as e:
            logger.bind(tag=TAG).error(f"Connection failed: {str(e)}")
            self.ws = None
            self.last_active_time = None
            raise

    def tts_text_priority_thread(self):
        """Streaming text processing thread"""
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)

                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("Interrupt message received, stop TTS text processing thread")
                    asyncio.run_coroutine_threadsafe(
                        self.finish_session(self.conn.sentence_id),
                        loop=self.conn.loop,
                    )
                    continue

                # Filter oldMessage: checksentence_idMatches
                if message.sentence_id != self.conn.sentence_id:
                    continue

                logger.bind(tag=TAG).debug(
                    f"ReceiveTTSTask |{message.sentence_type.name} | {message.content_type.name} | Session ID: {message.sentence_id}"
                )

                if message.sentence_type == SentenceType.FIRST:
                    # Reset streaming processingStatus
                    self.reset_stream_state()
                    # Initialization parameters
                    try:
                        logger.bind(tag=TAG).debug("Starting TTS session...")
                        future = asyncio.run_coroutine_threadsafe(
                            self.start_session(self.task_id),
                            loop=self.conn.loop,
                        )
                        future.result(timeout=self.tts_timeout)
                        self.before_stop_play_files.clear()
                        logger.bind(tag=TAG).debug("TTS session started successfully")

                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Failed to start TTS session: {str(e)}")
                        continue

                elif ContentType.TEXT == message.content_type:
                    if message.content_detail:
                        try:
                            logger.bind(tag=TAG).debug(
                                f"Start sending TTS text: {message.content_detail}"
                            )
                            future = asyncio.run_coroutine_threadsafe(
                                self.text_to_speak(message.content_detail, None),
                                loop=self.conn.loop,
                            )
                            future.result(timeout=self.tts_timeout)
                        except Exception as e:
                            logger.bind(tag=TAG).error(f"Failed to send TTS text: {str(e)}")
                            continue

                elif ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(
                        f"Add audio file to pending playback list: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        # Process file audio data first
                        self._process_audio_file_stream(message.content_file, callback=lambda audio_data: self.handle_audio_file(audio_data, message.content_detail))
                if message.sentence_type == SentenceType.LAST:
                    try:
                        logger.bind(tag=TAG).debug("Ending TTS session...")
                        future = asyncio.run_coroutine_threadsafe(
                            self.finish_session(self.task_id),
                            loop=self.conn.loop,
                        )
                        future.result(timeout=self.tts_timeout)
                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Failed to end TTS session: {str(e)}")
                        continue

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"Failed to process TTS text: {str(e)}, type: {type(e).__name__}, stack: {traceback.format_exc()}"
                )

    async def text_to_speak(self, text, _):
        try:
            if self.ws is None:
                logger.bind(tag=TAG).warning(f"WebSocket connection does not exist, stop sending text")
                return
            filtered_text = MarkdownCleaner.clean_markdown(text)

            if filtered_text:
                # Use sliding window matching to handle cross-fragmentReplacement word
                confirmed_texts, self._pending_prefix = self._match_stream_text(filtered_text)

                # Send each confirmed text segment
                for txt in confirmed_texts:
                    if txt and self.ws:
                        run_request = {
                            "header": {
                                "message_id": uuid.uuid4().hex,
                                "task_id": self.task_id,
                                "namespace": "FlowingSpeechSynthesizer",
                                "name": "RunSynthesis",
                                "appkey": self.appkey,
                            },
                            "payload": {"text": txt},
                        }
                        await self.ws.send(json.dumps(run_request))
                        self.last_active_time = time.time()
            return

        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to send TTS text: {str(e)}")
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
            raise

    async def start_session(self, task_id):
        logger.bind(tag=TAG).debug("Session started~~")
        try:
            # Previous session is activeStatusClose previous connection and create new link when
            if self.activate_session:
                await self.close()

            # Set session active flag
            self.activate_session = True

            # Establish new connection
            await self._ensure_connection()

            # Start listener task
            if self._monitor_task is None or self._monitor_task.done():
                logger.bind(tag=TAG).debug("Start listening task...")
                self._monitor_task = asyncio.create_task(self._start_monitor_tts_response())

            start_request = {
                "header": {
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "namespace": "FlowingSpeechSynthesizer",
                    "name": "StartSynthesis",
                    "appkey": self.appkey,
                },
                "payload": {
                    "voice": self.voice,
                    "format": self.format,
                    "sample_rate": self.conn.sample_rate,
                    "volume": self.volume,
                    "speech_rate": self.speech_rate,
                    "pitch_rate": self.pitch_rate,
                    "enable_subtitle": True,
                },
            }
            await self.ws.send(json.dumps(start_request))
            self.last_active_time = time.time()
            logger.bind(tag=TAG).debug("Session start request sent")
        except Exception as e:
            logger.bind(tag=TAG).error(f"Start session failed: {str(e)}")
            # EnsureClean resources
            await self.close()
            raise

    async def finish_session(self, task_id):
        logger.bind(tag=TAG).debug(f"Close session~~{task_id}")
        try:
            if self.ws:
                stop_request = {
                    "header": {
                        "message_id": uuid.uuid4().hex,
                        "task_id": self.task_id,
                        "namespace": "FlowingSpeechSynthesizer",
                        "name": "StopSynthesis",
                        "appkey": self.appkey,
                    }
                }
                await self.ws.send(json.dumps(stop_request))
                logger.bind(tag=TAG).debug("Session end request sent")
                self.last_active_time = time.time()

        except Exception as e:
            logger.bind(tag=TAG).error(f"Close session failed: {str(e)}")
            # EnsureClean resources
            await self.close()
            raise

    async def close(self):
        """Resource cleanup"""
        await super().close()
        self.activate_session = False
        if self._monitor_task:
            try:
                self._monitor_task.cancel()
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.bind(tag=TAG).warning(f"Error canceling listen task on close: {e}")
            self._monitor_task = None

        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
            self.ws = None
            self.last_active_time = None

    async def _start_monitor_tts_response(self):
        """Listen to TTS response - long running"""
        try:
            while not self.conn.stop_event.is_set():
                try:
                    msg = await self.ws.recv()
                    self.last_active_time = time.time()

                    if isinstance(msg, str):  # Text ControlMessage
                        try:
                            data = json.loads(msg)
                            header = data.get("header", {})
                            event_name = header.get("name")
                            task_id = header.get("task_id")

                            # Only process current active session'sResponse
                            if task_id and self.task_id != task_id:
                                if event_name in ["SynthesisCompleted", "TaskFailed"]:
                                    logger.bind(tag=TAG).debug(f"Received residual downstream end response, reset session state~~")
                                    self.activate_session = False
                                continue

                            if event_name == "SynthesisStarted":
                                logger.bind(tag=TAG).debug("TTS synthesis started")
                                self.tts_audio_queue.put(
                                    (SentenceType.FIRST, [], None)
                                )
                            elif event_name == "SentenceEnd":
                                # Send cached data
                                tts_text = self.get_tts_text(self.conn.sentence_id)
                                if tts_text:
                                    logger.bind(tag=TAG).info(
                                        f"Sentence speech generated successfully: {tts_text}"
                                    )
                                    self.tts_audio_queue.put(
                                        (SentenceType.FIRST, [], tts_text)
                                    )
                                    self.clear_tts_text(self.conn.sentence_id)
                            elif event_name == "SynthesisCompleted":
                                logger.bind(tag=TAG).debug(f"Session ended~~")
                                self.activate_session = False
                                self._process_before_stop_play_files()
                        except json.JSONDecodeError:
                            logger.bind(tag=TAG).warning("Received invalid JSON message")
                    # BinaryMessage(audio data)
                    elif isinstance(msg, (bytes, bytearray)):
                        self.opus_encoder.encode_pcm_to_opus_stream(msg, False, self.handle_opus)
                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).warning("WebSocket connection closed")
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(
                        f"Error handling TTS response: {e}\n{traceback.format_exc()}"
                    )
                    break
            # ConnectExceptionClose whenWebSocket
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
        # Clean references when listening task exits
        finally:
            self.activate_session = False
            self._monitor_task = None

    def audio_to_opus_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """Override parent class method: use independent temporary encoder to process audio files, avoid concurrency conflicts with TTS streaming encoder.
        In dual-streaming TTS, monitor task receives TTS audio in event loop thread and uses self.opus_encoder to encode,
        while tts_text_priority_thread processes music files and also uses self.opus_encoder.
        Shared encoder.buffer is not thread-safe. Concurrent access can cause SILK resampler assertion failure.
        """
        from core.utils.util import audio_to_data_stream

        return audio_to_data_stream(
            audio_file_path,
            is_opus=True,
            callback=callback,
            sample_rate=self.conn.sample_rate,
            opus_encoder=None,
        )

    def to_tts(self, text: str) -> list:
        """Non-streaming TTS processing, for testing and saving audio files"""
        try:
            # Create NewEventLoop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Store audio data
            audio_data = []

            async def _generate_audio():
                # Refresh Token(if needed)
                if self._is_token_expired():
                    self._refresh_token()

                # EstablishWebSocketConnect
                ws = await websockets.connect(
                    self.ws_url,
                    additional_headers={"X-NLS-Token": self.token},
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                )
                try:
                    # SendStartSynthesisRequest
                    start_request = {
                        "header": {
                            "message_id": uuid.uuid4().hex,
                            "task_id": self.task_id,
                            "namespace": "FlowingSpeechSynthesizer",
                            "name": "StartSynthesis",
                            "appkey": self.appkey,
                        },
                        "payload": {
                            "voice": self.voice,
                            "format": self.format,
                            "sample_rate": self.conn.sample_rate,
                            "volume": self.volume,
                            "speech_rate": self.speech_rate,
                            "pitch_rate": self.pitch_rate,
                            "enable_subtitle": True,
                        },
                    }
                    await ws.send(json.dumps(start_request))

                    # WaitSynthesisStartedResponse
                    synthesis_started = False
                    while not synthesis_started:
                        msg = await ws.recv()
                        if isinstance(msg, str):
                            data = json.loads(msg)
                            header = data.get("header", {})
                            if header.get("name") == "SynthesisStarted":
                                synthesis_started = True
                                logger.bind(tag=TAG).debug("TTS synthesis started")
                            elif header.get("name") == "TaskFailed":
                                error_info = data.get("payload", {}).get(
                                    "error_info", {}
                                )
                                error_code = error_info.get("error_code")
                                error_message = error_info.get(
                                    "error_message", "Unknown error"
                                )
                                raise Exception(
                                    f"Failed to start synthesis: {error_code} - {error_message}"
                                )

                    # Send text synthesis request
                    filtered_text = MarkdownCleaner.clean_markdown(text)
                    if self._correct_words_pattern:
                        filtered_text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], filtered_text)
                    run_request = {
                        "header": {
                            "message_id": uuid.uuid4().hex,
                            "task_id": self.task_id,
                            "namespace": "FlowingSpeechSynthesizer",
                            "name": "RunSynthesis",
                            "appkey": self.appkey,
                        },
                        "payload": {"text": filtered_text},
                    }
                    await ws.send(json.dumps(run_request))

                    # Send stop synthesis request
                    stop_request = {
                        "header": {
                            "message_id": uuid.uuid4().hex,
                            "task_id": self.task_id,
                            "namespace": "FlowingSpeechSynthesizer",
                            "name": "StopSynthesis",
                            "appkey": self.appkey,
                        }
                    }
                    await ws.send(json.dumps(stop_request))

                    # Receive audio data
                    synthesis_completed = False
                    while not synthesis_completed:
                        msg = await ws.recv()
                        if isinstance(msg, (bytes, bytearray)):
                            self.opus_encoder.encode_pcm_to_opus_stream(
                                msg,
                                end_of_stream=False,
                                callback=lambda opus: audio_data.append(opus)
                            )
                        elif isinstance(msg, str):
                            data = json.loads(msg)
                            header = data.get("header", {})
                            event_name = header.get("name")
                            if event_name == "SynthesisCompleted":
                                synthesis_completed = True
                                logger.bind(tag=TAG).debug("TTS synthesis complete")
                            elif event_name == "TaskFailed":
                                error_info = data.get("payload", {}).get(
                                    "error_info", {}
                                )
                                error_code = error_info.get("error_code")
                                error_message = error_info.get(
                                    "error_message", "Unknown error"
                                )
                                raise Exception(
                                    f"Synthesis failed: {error_code} - {error_message}"
                                )
                finally:
                    try:
                        await ws.close()
                    except:
                        pass

            loop.run_until_complete(_generate_audio())
            loop.close()

            return audio_data
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to generate audio data: {str(e)}")
            return []