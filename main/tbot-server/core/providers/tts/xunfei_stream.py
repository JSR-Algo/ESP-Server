import os
import time
import uuid
import json
import hmac
import queue
import base64
import hashlib
import asyncio
import traceback
import websockets

from asyncio import Task
from typing import Callable, Any
from config.logger import setup_logging
from core.utils.tts import MarkdownCleaner
from urllib.parse import urlencode, urlparse
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType

TAG = __name__
logger = setup_logging()


class XunfeiWSAuth:
    @staticmethod
    def create_auth_url(api_key, api_secret, api_url):
        """Generate iFlytek WebSocket auth URL"""
        parsed_url = urlparse(api_url)
        host = parsed_url.netloc
        path = parsed_url.path

        # GetUTCTime, iFlytek requires useRFC1123Format
        now = time.gmtime()
        date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', now)

        # Build signature string
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"

        # Calculate Signature
        signature_sha = hmac.new(
            api_secret.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding='utf-8')

        # Constructauthorization
        authorization_origin = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')

        # Construct finalWebSocket URL
        v = {
            "authorization": authorization,
            "date": date,
            "host": host
        }
        url = api_url + '?' + urlencode(v)
        return url


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speed", 0, 100, 50, int),
        ("ttsPitch", "pitch", 0, 100, 50, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)

        # Set to streaming API type
        self.interface_type = InterfaceType.DUAL_STREAM

        # Basic Config
        self.app_id = config.get("app_id")
        self.api_key = config.get("api_key")
        self.api_secret = config.get("api_secret")
        self.report_on_last = True

        # API address
        self.api_url = config.get("api_url", "wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/mcd9m97e6")

        # Voice Config
        self.voice = config.get("voice", "x5_lingxiaoxuan_flow")
        if config.get("private_voice"):
            self.voice = config.get("private_voice")

        # Audio parameter config
        speed = config.get("speed", "50")
        self.speed = int(speed) if speed else 50

        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50

        pitch = config.get("pitch", "50")
        self.pitch = int(pitch) if pitch else 50

        # Apply percentage adjustment (if exists), otherwise use public config
        self._apply_percentage_params(config)

        # Audio encoding config
        self.format = config.get("format", "raw")

        # Colloquial config
        self.oral_level = config.get("oral_level", "mid")

        spark_assist = config.get("spark_assist", "1")
        self.spark_assist = int(spark_assist) if spark_assist else 1

        stop_split = config.get("stop_split", "0")
        self.stop_split = int(stop_split) if stop_split else 0
    
        remain = config.get("remain", "0")
        self.remain = int(remain) if remain else 0

        # WebSocket config
        self.ws = None
        self._monitor_task = None
        self.activate_session = False

        # Sequence number management
        self.text_seq = 0

        # Validate required parameters
        if not all([self.app_id, self.api_key, self.api_secret]):
            raise ValueError("iFlytek TTS requires app_id, api_key, and api_secret")

    async def _ensure_connection(self):
        """Ensure WebSocket connection available"""
        try:
            logger.bind(tag=TAG).debug("Starting new connection...")

            # Generate auth URL
            auth_url = XunfeiWSAuth.create_auth_url(
                self.api_key, self.api_secret, self.api_url
            )

            self.ws = await websockets.connect(
                auth_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10,
            )
            logger.bind(tag=TAG).debug("WebSocket connection established")
            return self.ws
        except Exception as e:
            logger.bind(tag=TAG).error(f"Connection failed: {str(e)}")
            self.ws = None
            raise

    def tts_text_priority_thread(self):
        """Streaming text processing thread"""
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)

                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("Interrupt message received, stop TTS text processing thread")
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
                    # Reset sequence number
                    self.text_seq = 0
                # Increase sequence number
                self.text_seq += 1

                if message.sentence_type == SentenceType.FIRST:
                    # Initialization parameters
                    try:
                        if not getattr(self.conn, "sentence_id", None):
                            self.conn.sentence_id = uuid.uuid4().hex
                            logger.bind(tag=TAG).debug(f"Auto-generate new session ID: {self.conn.sentence_id}")

                        logger.bind(tag=TAG).debug("Starting TTS session...")
                        future = asyncio.run_coroutine_threadsafe(
                            self.start_session(self.conn.sentence_id),
                            loop=self.conn.loop,
                        )
                        future.result(timeout=self.tts_timeout)
                        self.before_stop_play_files.clear()
                        logger.bind(tag=TAG).debug("TTS session started successfully")

                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Failed to start TTS session: {str(e)}")
                        continue

                # Process TextContent
                if ContentType.TEXT == message.content_type:
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
                            # Do not usecontinue, ensure subsequent processing not interrupted

                # Process FileContent
                if ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(
                        f"Add audio file to pending playback list: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        # Process file audio data first
                        self._process_audio_file_stream(message.content_file, callback=lambda audio_data: self.handle_audio_file(audio_data, message.content_detail))

                # Handle session end
                if message.sentence_type == SentenceType.LAST:
                    try:
                        logger.bind(tag=TAG).debug("Ending TTS session...")
                        asyncio.run_coroutine_threadsafe(
                            self.finish_session(self.conn.sentence_id),
                            loop=self.conn.loop,
                        )
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
        """Send text to TTS service for synthesis"""
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
                        # Send text synthesis request
                        run_request = self._build_base_request(status=1, text=txt)
                        await self.ws.send(json.dumps(run_request))
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

    async def start_session(self, session_id):
        logger.bind(tag=TAG).debug(f"Start session~~{session_id}")
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

            # Send session start request
            start_request = self._build_base_request(status=0)

            await self.ws.send(json.dumps(start_request))
            logger.bind(tag=TAG).debug("Session start request sent")
        except Exception as e:
            logger.bind(tag=TAG).error(f"Start session failed: {str(e)}")
            # EnsureClean resources
            await self.close()
            raise

    async def finish_session(self, session_id):
        logger.bind(tag=TAG).debug(f"Close session~~{session_id}")
        try:
            if self.ws:
                # Send session end request
                stop_request = self._build_base_request(status=2)
                await self.ws.send(json.dumps(stop_request))
                logger.bind(tag=TAG).debug("Session end request sent")

                if self._monitor_task:
                    try:
                        await self._monitor_task
                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Error waiting for listening task to finish: {str(e)}")
                    finally:
                        self._monitor_task = None
        except Exception as e:
            logger.bind(tag=TAG).error(f"Close session failed: {str(e)}")
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

    async def _start_monitor_tts_response(self):
        """Listen for TTS response"""
        try:
            while not self.conn.stop_event.is_set():
                try:
                    msg = await self.ws.recv()

                    # Check whether client aborted
                    if self.conn.client_abort:
                        logger.bind(tag=TAG).info("Received interrupt info, stopping TTS response listening")
                        break

                    try:
                        data = json.loads(msg)
                        header = data.get("header", {})
                        code = header.get("code")

                        if code == 0:
                            payload = data.get("payload", {})
                            audio_payload = payload.get("audio", {})

                            if audio_payload:
                                status = audio_payload.get("status", 0)
                                audio_data = audio_payload.get("audio", "")
                                if status == 0:
                                    logger.bind(tag=TAG).debug("TTS synthesis started")
                                    self.tts_audio_queue.put(
                                        (SentenceType.FIRST, [], None)
                                    )
                                elif status == 2:
                                    logger.bind(tag=TAG).debug("Received audio data with end status, TTS synthesis complete")
                                    self.activate_session = False
                                    self._process_before_stop_play_files()
                                    break
                                else:
                                    tts_text = self.get_tts_text(self.conn.sentence_id)
                                    if tts_text:
                                        logger.bind(tag=TAG).info(
                                            f"Sentence speech generated successfully: {tts_text}"
                                        )
                                        self.tts_audio_queue.put(
                                            (SentenceType.FIRST, [], tts_text)
                                        )
                                        self.clear_tts_text(self.conn.sentence_id)
                                    try:
                                        audio_bytes = base64.b64decode(audio_data)
                                        self.opus_encoder.encode_pcm_to_opus_stream(
                                            audio_bytes, False, self.handle_opus
                                        )

                                    except Exception as e:
                                        logger.bind(tag=TAG).error(f"Process audio data failed: {e}")

                        else:
                            message = header.get("message", "Unknown error")
                            logger.bind(tag=TAG).error(f"TTS synthesis error: {code} - {message}")
                            break

                    except json.JSONDecodeError:
                        logger.bind(tag=TAG).warning("Received invalid JSON message")

                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).warning("WebSocket connection closed")
                    break

                except Exception as e:
                    logger.bind(tag=TAG).error(
                        f"Error handling TTS response: {e}\n{traceback.format_exc()}"
                    )
                    break

            # Link not reusable
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

    def to_tts(self, text: str) -> list:
        """Non-streaming TTS processing, for testing and saving audio files"""
        try:
            # Create NewEventLoop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Store audio data
            audio_data = []

            async def _generate_audio():
                # Generate auth URL
                auth_url = XunfeiWSAuth.create_auth_url(
                    self.api_key, self.api_secret, self.api_url
                )

                # EstablishWebSocketConnect
                ws = await websockets.connect(
                    auth_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                )

                try:
                    filtered_text = MarkdownCleaner.clean_markdown(text)
                    if self._correct_words_pattern:
                        filtered_text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], filtered_text)

                    text_request = self._build_base_request(status=2,text=filtered_text)

                    await ws.send(json.dumps(text_request))

                    task_finished = False
                    while not task_finished:
                        msg = await ws.recv()

                        data = json.loads(msg)
                        header = data.get("header", {})
                        code = header.get("code")

                        if code == 0:
                            payload = data.get("payload", {})
                            audio_payload = payload.get("audio", {})
                            if audio_payload:
                                status = audio_payload.get("status", 0)
                                audio_base64 = audio_payload.get("audio", "")
                                if status == 1:
                                    try:
                                        audio_bytes = base64.b64decode(audio_base64)
                                        self.opus_encoder.encode_pcm_to_opus_stream(
                                            audio_bytes,
                                            end_of_stream=False,
                                            callback=lambda opus: audio_data.append(opus)
                                        )
                                    except Exception as e:
                                        logger.bind(tag=TAG).error(f"Process audio data failed: {e}")
                                elif status == 2:
                                    task_finished = True
                                    logger.bind(tag=TAG).debug("TTS task complete")

                        else:
                            message = header.get("message", "Unknown error")
                            raise Exception(f"Synthesis failed: {code} - {message}")

                finally:
                    # Clean resources
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

    def _build_base_request(self, status, text=" "):
        """Build base request structure"""
        return {
            "header": {
                "app_id": self.app_id,
                "status": status,
            },
            "parameter": {
                "oral": {
                    "oral_level": self.oral_level,
                    "spark_assist": self.spark_assist,
                    "stop_split": self.stop_split,
                    "remain": self.remain
                },
                "tts": {
                    "vcn": self.voice,
                    "speed": self.speed,
                    "volume": self.volume,
                    "pitch": self.pitch,
                    "bgs": 0,
                    "reg": 0,
                    "rdn": 0,
                    "rhy": 0,
                    "audio": {
                        "encoding": self.format,
                        "sample_rate": self.conn.sample_rate,
                        "channels": 1,
                        "bit_depth": 16,
                        "frame_size": 0
                    }
                }
            },
            "payload": {
                "text": {
                    "encoding": "utf8",
                    "compress": "raw",
                    "format": "plain",
                    "status": status,
                    "seq": self.text_seq,
                    "text": base64.b64encode(text.encode('utf-8')).decode('utf-8')
                }
            }
        }
