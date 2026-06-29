import os
import re
import uuid
import queue
import asyncio
import importlib
import threading
import traceback
import concurrent.futures
import time

from core.utils import p3
from datetime import datetime
from core.utils import textUtils
from typing import Callable, Any
from collections.abc import Mapping
from abc import ABC, abstractmethod
from config.logger import setup_logging
from core.utils import opus_encoder_utils
from core.utils.tts import MarkdownCleaner, convert_percentage_to_range
from core.utils.output_counter import add_device_output
try:
    from core.handle.reportHandle import enqueue_tts_report
except (ImportError, AttributeError):  # pragma: no cover - test stubs / optional reporting
    def enqueue_tts_report(*_args, **_kwargs):
        return None
from core.handle.sendAudioHandle import sendAudioMessage
from core.utils.util import audio_bytes_to_data_stream, audio_to_data_stream
from core.providers.tts.dto.dto import (
    TTSMessageDTO,
    SentenceType,
    ContentType,
    InterfaceType,
)

TAG = __name__
logger = setup_logging()

_TRAILING_SEGMENT_CLOSERS = set('"\')]}》】」』）')
_TTS_SEGMENT_EDGE_CHARS = " \t\r\n\"'）)]}》】」』.!?;:,"
_MIN_TTS_SEGMENT_CHARS = 18


def _is_normal_audio_send_close(exc) -> bool:
    text = str(exc or "")
    return "received 1000" in text and "sent 1000" in text


class TTSProviderBase(ABC):
    def __init__(self, config, delete_audio_file):
        self.interface_type = InterfaceType.NON_STREAM
        self.conn = None
        self.delete_audio_file = delete_audio_file
        self.audio_file_type = "wav"
        self._fallback_tts_config = config.get("fallback_tts") if isinstance(config, Mapping) else None
        self._fallback_after_primary_error_until = 0.0
        self._fallback_after_primary_error_cooldown_sec = self._config_float(
            config,
            "fallback_after_primary_error_cooldown_sec",
            300.0,
        )
        self.output_file = config.get("output_dir", "tmp/")
        self.tts_timeout = int(config.get("tts_timeout", 15))
        self.tts_text_queue = queue.Queue()
        self.tts_audio_queue = queue.Queue()
        self.tts_audio_first_sentence = True
        self.before_stop_play_files = []
        self.report_on_last = False
        # sentence_id mapping to text, for streamingTTSGet correct subtitle text
        self._sentence_text_map = {}
        # LoadReplacement wordused for one-time regex replacement
        raw_words = config.get("correct_words", [])
        self.correct_words = {}
        for item in raw_words:
            parts = item.split("|", 1)
            if len(parts) == 2:
                self.correct_words[parts[0]] = parts[1]
        # Build regex, use longest match first (Sortthen escape concatenate)
        if self.correct_words:
            # bykeySort by length descending. Match longer first to avoid short word partial interference
            sorted_keys = sorted(self.correct_words.keys(), key=len, reverse=True)
            pattern_str = "|".join(re.escape(k) for k in sorted_keys)
            self._correct_words_pattern = re.compile(pattern_str)
            # Build reverse replacement regex, used toTTSRestore service-returned replaced text to original text (subtitle display)
            reverse_map = {v: k for k, v in self.correct_words.items()}
            sorted_reverse_keys = sorted(reverse_map.keys(), key=len, reverse=True)
            reverse_pattern_str = "|".join(re.escape(k) for k in sorted_reverse_keys)
            self._reverse_words_pattern = re.compile(reverse_pattern_str)
            self._reverse_words_map = reverse_map
            # Streaming sliding window: grouped by first charReplacement wordDictionary for quick lookup
            self._words_by_first_char = {}
            for key in sorted_keys:  # Use already length-desc sortedkeysEnsure long words match first
                first_char = key[0] if key else ""
                if first_char not in self._words_by_first_char:
                    self._words_by_first_char[first_char] = []
                self._words_by_first_char[first_char].append(key)
        else:
            self._correct_words_pattern = None
            self._reverse_words_pattern = None
            self._reverse_words_map = None

        # Streaming sliding window: cached text to match
        self._pending_prefix = ""
        self.tts_text_buff = []
        self.punctuations = (
            ".",
            "?",
            "?",
            "!",
            "!",
            ";",
            ";",
            ":",
        )
        self.first_sentence_punctuations = (
            ",",
            "~",
            ",",
            ",",
            ".",
            "?",
            "?",
            "!",
            "!",
            ";",
            ";",
            ":",
        )
        self.tts_stop_request = False
        self.processed_chars = 0
        self.is_first_sentence = True

    def _config_float(self, config, key, fallback):
        try:
            return max(0.0, float(config.get(key, fallback)))
        except (TypeError, ValueError, AttributeError):
            return fallback

    def _primary_tts_fast_fallback_active(self):
        return time.monotonic() < self._fallback_after_primary_error_until

    def _is_fast_fallback_tts_error(self, exc):
        message = str(exc or "").casefold()
        return any(
            marker in message
            for marker in (
                "429",
                "quota",
                "rate limit",
                "rate-limit",
                "resource exhausted",
                "too many requests",
                "generate_requests_per_model_per_day",
            )
        )

    def _activate_primary_tts_fast_fallback(self, original_text, exc):
        if self._fallback_after_primary_error_cooldown_sec <= 0:
            return
        self._fallback_after_primary_error_until = (
            time.monotonic() + self._fallback_after_primary_error_cooldown_sec
        )
        logger.bind(tag=TAG).warning(
            f"Primary TTS quota/rate failure; using fallback TTS for cooldown: {original_text}, error: {exc}"
        )

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    def handle_opus(self, opus_data: bytes):
        logger.bind(tag=TAG).debug(f"Push data to queue, frame count~~ {len(opus_data)}")
        self.tts_audio_queue.put((SentenceType.MIDDLE, opus_data, None, getattr(self, 'current_sentence_id', None)))

    def handle_audio_file(self, file_audio: bytes, text):
        self.before_stop_play_files.append((file_audio, text))

    def to_tts_stream(self, text, opus_handler: Callable[[bytes], None] = None) -> None:
        # Keep original text for display/Report
        original_text = text
        text = MarkdownCleaner.clean_markdown(text)
        # Use regex one-shot replacement to avoid repeated traversal and partial match issues
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], text)
        max_repeat_time = 5
        if self.delete_audio_file:
            # NeedDeleteFile directly converted to audio data
            if self._primary_tts_fast_fallback_active():
                max_repeat_time = 0
            while max_repeat_time > 0:
                try:
                    audio_bytes = asyncio.run(self.text_to_speak(text, None))
                    if audio_bytes:
                        # Use original text for display/Report
                        self.tts_audio_queue.put((SentenceType.FIRST, None, original_text, getattr(self, 'current_sentence_id', None)))
                        audio_bytes_to_data_stream(
                            audio_bytes,
                            file_type=self.audio_file_type,
                            is_opus=True,
                            callback=opus_handler,
                            sample_rate=self.conn.sample_rate,
                            opus_encoder=self.opus_encoder,
                        )
                        break
                    else:
                        max_repeat_time -= 1
                except Exception as e:
                    logger.bind(tag=TAG).warning(
                        f"Speech generation failed {5 - max_repeat_time + 1} times: {original_text}, error: {e}"
                    )
                    if self._is_fast_fallback_tts_error(e):
                        self._activate_primary_tts_fast_fallback(original_text, e)
                        max_repeat_time = 0
                        break
                    max_repeat_time -= 1
            if max_repeat_time > 0:
                logger.bind(tag=TAG).info(
                    f"Speech generation succeeded: {original_text}, retried {5 - max_repeat_time} times"
                )
            else:
                if self._stream_fallback_audio(text, original_text, opus_handler=opus_handler):
                    return None
                logger.bind(tag=TAG).error(
                    f"Voice generation failed: {original_text}, check network or service status"
                )
            return None
        else:
            tmp_file = self.generate_filename()
            try:
                if self._primary_tts_fast_fallback_active():
                    max_repeat_time = 0
                while not os.path.exists(tmp_file) and max_repeat_time > 0:
                    try:
                        asyncio.run(self.text_to_speak(text, tmp_file))
                    except Exception as e:
                        logger.bind(tag=TAG).warning(
                            f"Speech generation failed {5 - max_repeat_time + 1} times: {original_text}, error: {e}"
                        )
                        # Not executed successfully,DeleteFile
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)
                        if self._is_fast_fallback_tts_error(e):
                            self._activate_primary_tts_fast_fallback(original_text, e)
                            max_repeat_time = 0
                            break
                        max_repeat_time -= 1

                if max_repeat_time > 0:
                    logger.bind(tag=TAG).info(
                        f"Speech generated successfully: {original_text}:{tmp_file}, retried {5 - max_repeat_time} times"
                    )
                else:
                    if self._stream_fallback_audio(text, original_text, opus_handler=opus_handler):
                        return None
                    logger.bind(tag=TAG).error(
                        f"Voice generation failed: {original_text}, check network or service status"
                    )
                self.tts_audio_queue.put((SentenceType.FIRST, None, original_text, getattr(self, 'current_sentence_id', None)))
                self._process_audio_file_stream(tmp_file, callback=opus_handler)
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
                return None
    
    def to_tts(self, text):
        # Keep original text for logs/Show
        original_text = text
        text = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], text)
        max_repeat_time = 5
        if self.delete_audio_file:
            # NeedDeleteFile directly converted to audio data
            if self._primary_tts_fast_fallback_active():
                max_repeat_time = 0
            while max_repeat_time > 0:
                try:
                    audio_bytes = asyncio.run(self.text_to_speak(text, None))
                    if audio_bytes:
                        audio_datas = []
                        audio_bytes_to_data_stream(
                            audio_bytes,
                            file_type=self.audio_file_type,
                            is_opus=True,
                            callback=lambda data: audio_datas.append(data),
                            sample_rate=self.conn.sample_rate,
                        )
                        return audio_datas
                    else:
                        max_repeat_time -= 1
                except Exception as e:
                    logger.bind(tag=TAG).warning(
                        f"Speech generation failed {5 - max_repeat_time + 1} times: {original_text}, error: {e}"
                    )
                    if self._is_fast_fallback_tts_error(e):
                        self._activate_primary_tts_fast_fallback(original_text, e)
                        max_repeat_time = 0
                        break
                    max_repeat_time -= 1
            if max_repeat_time > 0:
                logger.bind(tag=TAG).info(
                    f"Speech generation succeeded: {original_text}, retried {5 - max_repeat_time} times"
                )
            else:
                fallback = asyncio.run(self._fallback_text_to_speak(text, original_text))
                if fallback:
                    audio_bytes, file_type = fallback
                    audio_datas = []
                    audio_bytes_to_data_stream(
                        audio_bytes,
                        file_type=file_type,
                        is_opus=True,
                        callback=lambda data: audio_datas.append(data),
                        sample_rate=self.conn.sample_rate,
                    )
                    return audio_datas
                logger.bind(tag=TAG).error(
                    f"Voice generation failed: {original_text}, check network or service status"
                )
            return None
        else:
            tmp_file = self.generate_filename()
            try:
                if self._primary_tts_fast_fallback_active():
                    max_repeat_time = 0
                while not os.path.exists(tmp_file) and max_repeat_time > 0:
                    try:
                        asyncio.run(self.text_to_speak(text, tmp_file))
                    except Exception as e:
                        logger.bind(tag=TAG).warning(
                            f"Speech generation failed {5 - max_repeat_time + 1} times: {original_text}, error: {e}"
                        )
                        # Not executed successfully,DeleteFile
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)
                        if self._is_fast_fallback_tts_error(e):
                            self._activate_primary_tts_fast_fallback(original_text, e)
                            max_repeat_time = 0
                            break
                        max_repeat_time -= 1

                if max_repeat_time > 0:
                    logger.bind(tag=TAG).info(
                        f"Speech generated successfully: {original_text}:{tmp_file}, retried {5 - max_repeat_time} times"
                    )
                else:
                    logger.bind(tag=TAG).error(
                        f"Voice generation failed: {original_text}, check network or service status"
                    )

                return tmp_file
            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
                return None

    @abstractmethod
    async def text_to_speak(self, text, output_file):
        pass

    def _create_fallback_tts_provider(self):
        config = self._fallback_tts_config
        if not isinstance(config, Mapping):
            return None
        provider_type = str(config.get("type") or "").strip()
        if not provider_type:
            return None
        module = importlib.import_module(f"core.providers.tts.{provider_type}")
        provider = module.TTSProvider(dict(config), True)
        provider.conn = self.conn
        if hasattr(self, "opus_encoder"):
            provider.opus_encoder = self.opus_encoder
        return provider

    async def _fallback_text_to_speak(self, text, original_text):
        provider = self._create_fallback_tts_provider()
        if provider is None:
            return None
        fallback_type = getattr(provider, "audio_file_type", "mp3")
        try:
            audio_bytes = await provider.text_to_speak(text, None)
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"Fallback TTS failed: {original_text}, error: {exc}"
            )
            return None
        if not audio_bytes:
            logger.bind(tag=TAG).error(
                f"Fallback TTS returned empty audio: {original_text}"
            )
            return None
        logger.bind(tag=TAG).warning(
            f"Primary TTS exhausted; using fallback TTS: {original_text}"
        )
        return audio_bytes, fallback_type

    def _stream_fallback_audio(self, text, original_text, opus_handler=None):
        fallback = asyncio.run(self._fallback_text_to_speak(text, original_text))
        if not fallback:
            return False
        audio_bytes, file_type = fallback
        self.tts_audio_queue.put((SentenceType.FIRST, None, original_text, getattr(self, 'current_sentence_id', None)))
        audio_bytes_to_data_stream(
            audio_bytes,
            file_type=file_type,
            is_opus=True,
            callback=opus_handler,
            sample_rate=self.conn.sample_rate,
            opus_encoder=self.opus_encoder,
        )
        return True

    def audio_to_pcm_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """Convert audio file to PCM encoding"""
        return audio_to_data_stream(audio_file_path, is_opus=False, callback=callback, sample_rate=self.conn.sample_rate, opus_encoder=None)

    def audio_to_opus_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """Audio file converted to Opus encoding"""
        return audio_to_data_stream(audio_file_path, is_opus=True, callback=callback, sample_rate=self.conn.sample_rate, opus_encoder=self.opus_encoder)

    def tts_one_sentence(
        self,
        conn,
        content_type,
        content_detail=None,
        content_file=None,
        sentence_id=None,
    ):
        """Send sentence"""
        if not sentence_id:
            if conn.sentence_id:
                sentence_id = conn.sentence_id
            else:
                sentence_id = str(uuid.uuid4().hex)
                conn.sentence_id = sentence_id
        # For single-sentence text, perform segmentation
        segments = re.split(r"([.!?!?;;\n])", content_detail)
        for seg in segments:
            self.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=content_type,
                    content_detail=seg,
                    content_file=content_file,
                )
            )

    async def open_audio_channels(self, conn):
        self.conn = conn

        # Based onconnofsample_rateCreate encoder, do not override if subclass already created (IndexTTSAPI return is24kHZ-Pending resampling)
        if not hasattr(self, 'opus_encoder') or self.opus_encoder is None:
            self.opus_encoder = opus_encoder_utils.OpusEncoderUtils(
                sample_rate=conn.sample_rate, channels=1, frame_size_ms=60
            )

        # tts Digest Thread
        self.tts_priority_thread = threading.Thread(
            target=self.tts_text_priority_thread, daemon=True
        )
        self.tts_priority_thread.start()

        # Audio playback digestion thread
        self.audio_play_priority_thread = threading.Thread(
            target=self._audio_play_priority_thread, daemon=True
        )
        self.audio_play_priority_thread.start()

    def store_tts_text(self, sentence_id, text):
        """Store text for specified sentence_id, used by streaming TTS to get correct subtitle text

        Args:
            sentence_id: session ID
            text: text to store
        """
        if sentence_id and text:
            self._sentence_text_map[sentence_id] = text
            # Keep only recent 5 items, prevent memory leak
            if len(self._sentence_text_map) > 5:
                oldest = next(iter(self._sentence_text_map))
                del self._sentence_text_map[oldest]

    def get_tts_text(self, sentence_id):
        """Get text for specified sentence_id

        Args:
            sentence_id: session ID

        Returns:
            str: corresponding text, returns None if not found
        """
        return self._sentence_text_map.get(sentence_id)

    def clear_tts_text(self, sentence_id):
        """Clear text for specified sentence_id

        Args:
            sentence_id: session ID
        """
        if sentence_id in self._sentence_text_map:
            del self._sentence_text_map[sentence_id]

    def _restore_original_text(self, text):
        if not self._reverse_words_pattern or not text:
            return text
        return self._reverse_words_pattern.sub(
            lambda m: self._reverse_words_map[m.group(0)], text
        )

    # Default here is non-streaming handling
    # Override in subclass for streaming handling
    def tts_text_priority_thread(self):
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("Interrupt message received, stop TTS text processing thread")
                    continue
                # Filter oldMessage: checksentence_idMatches
                if message.sentence_id != self.conn.sentence_id:
                    continue
                if message.sentence_type == SentenceType.FIRST:
                    self.current_sentence_id = message.sentence_id
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.is_first_sentence = True
                    self.tts_audio_first_sentence = True
                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self.to_tts_stream(segment_text, opus_handler=self.handle_opus)
                elif ContentType.FILE == message.content_type:
                    self._process_remaining_text_stream(opus_handler=self.handle_opus)
                    tts_file = message.content_file
                    if tts_file and os.path.exists(tts_file):
                        self._process_audio_file_stream(
                            tts_file, callback=self.handle_opus
                        )
                if message.sentence_type == SentenceType.LAST:
                    self._process_remaining_text_stream(opus_handler=self.handle_opus)
                    self.tts_audio_queue.put(
                        (message.sentence_type, [], message.content_detail, message.sentence_id)
                    )

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"Failed to process TTS text: {str(e)}, type: {type(e).__name__}, stack: {traceback.format_exc()}"
                )
                continue

    def _audio_play_priority_thread(self):
        # Text and audio list needing report
        enqueue_text = None
        enqueue_audio = []
        while not self.conn.stop_event.is_set():
            text = None
            try:
                try:
                    item = self.tts_audio_queue.get(timeout=0.1)
                    if len(item) == 4:
                        sentence_type, audio_datas, text, sentence_id = item
                    else:
                        sentence_type, audio_datas, text = item
                        sentence_id = None
                except queue.Empty:
                    if self.conn.stop_event.is_set():
                        break
                    continue

                if self.conn.client_abort:
                    logger.bind(tag=TAG).debug("Received interrupt signal, skip current audio data")
                    enqueue_text, enqueue_audio = None, []
                    continue

                # Report when next text starts or session ends
                if sentence_type is not SentenceType.MIDDLE:
                    if self.report_on_last:
                        # Accumulation mode: suitable for only one voice stream throughoutTTS(such asseed-tts-2.0)
                        # FIRSTWhen onlyRecord textaudio keeps accumulating, only whenLASTReport uniformly when
                        if text:
                            enqueue_text = text
                        if sentence_type == SentenceType.LAST:
                            enqueue_tts_report(self.conn, enqueue_text, enqueue_audio)
                            enqueue_audio = []
                            enqueue_text = None
                    else:
                        # Non-cumulative mode: report each sentence separately
                        if enqueue_text is not None:
                            enqueue_tts_report(self.conn, enqueue_text, enqueue_audio)
                        enqueue_audio = []
                        enqueue_text = text

                # Collect reported audio data
                if isinstance(audio_datas, bytes):
                    enqueue_audio.append(audio_datas)

                # Send Audio
                future = asyncio.run_coroutine_threadsafe(
                    sendAudioMessage(self.conn, sentence_type, audio_datas, text, sentence_id),
                    self.conn.loop,
                )
                future.result()

                # Record output and report
                if self.conn.max_output_size > 0 and text:
                    add_device_output(self.conn.headers.get("device-id"), len(text))

            except Exception as e:
                if _is_normal_audio_send_close(e):
                    logger.bind(tag=TAG).debug(f"audio_play_priority_thread closed normally: {text} {e}")
                else:
                    logger.bind(tag=TAG).error(f"audio_play_priority_thread: {text} {e}")

    async def start_session(self, session_id):
        pass

    async def finish_session(self, session_id):
        pass

    async def close(self):
        """Resource cleanup method"""
        self._sentence_text_map.clear()
        if hasattr(self, "ws") and self.ws:
            await self.ws.close()

    def _get_segment_text(self):
        # Merge all current text and process unsplit part
        full_text = "".join(self.tts_text_buff)
        current_text = full_text[self.processed_chars :]  # Start from unprocessed position

        # Choose different punctuation set based on whether first sentence
        punctuations_to_use = (
            self.first_sentence_punctuations
            if self.is_first_sentence
            else self.punctuations
        )

        punct_positions = []
        for punct in punctuations_to_use:
            punct_positions.extend(match.start() for match in re.finditer(re.escape(punct), current_text))
        punct_positions = sorted(set(punct_positions))

        if punct_positions:
            last_punct_pos = self._choose_segment_punctuation(current_text, punct_positions)
            segment_end_pos = self._extend_segment_end_after_punctuation(
                current_text, last_punct_pos
            )
            segment_text_raw = current_text[:segment_end_pos]
            segment_text = textUtils.get_string_no_punctuation_or_emoji(
                segment_text_raw
            )
            segment_text = self._clean_segment_text_for_tts(segment_text)
            self.processed_chars += len(segment_text_raw)  # Update processed character position

            # If first sentence, after finding first comma, set flag toFalse
            if self.is_first_sentence:
                self.is_first_sentence = False

            return segment_text
        elif self.tts_stop_request and current_text:
            segment_text = current_text
            segment_text = self._clean_segment_text_for_tts(segment_text)
            self.is_first_sentence = True  # Reset Flag
            return segment_text
        else:
            return None

    def _choose_segment_punctuation(self, current_text, punct_positions):
        for index, punct_pos in enumerate(punct_positions):
            segment_end_pos = self._extend_segment_end_after_punctuation(current_text, punct_pos)
            segment_text = textUtils.get_string_no_punctuation_or_emoji(
                current_text[:segment_end_pos]
            )
            segment_text = self._clean_segment_text_for_tts(segment_text)
            if len(segment_text) >= _MIN_TTS_SEGMENT_CHARS or index == len(punct_positions) - 1:
                return punct_pos
        return punct_positions[0]

    def _clean_segment_text_for_tts(self, segment_text):
        if not segment_text:
            return segment_text
        segment_text = segment_text.strip(_TTS_SEGMENT_EDGE_CHARS)
        segment_text = segment_text.replace('"', "").replace("“", "").replace("”", "")
        return segment_text.strip()

    def _extend_segment_end_after_punctuation(self, current_text, punct_pos):
        end_pos = punct_pos + 1
        while end_pos < len(current_text) and current_text[end_pos] in _TRAILING_SEGMENT_CLOSERS:
            end_pos += 1
        return end_pos

    def _process_audio_file_stream(
        self, tts_file, callback: Callable[[Any], Any]
    ) -> None:
        """Process audio file and convert to specified format

        Args:
            tts_file: audio file path
            callback: file processing function
        """
        if tts_file.endswith(".p3"):
            p3.decode_opus_from_file_stream(tts_file, callback=callback)
        elif self.conn.audio_format == "pcm":
            self.audio_to_pcm_data_stream(tts_file, callback=callback)
        else:
            self.audio_to_opus_data_stream(tts_file, callback=callback)

        if (
            self.delete_audio_file
            and tts_file is not None
            and os.path.exists(tts_file)
            and tts_file.startswith(self.output_file)
        ):
            os.remove(tts_file)

    def _process_before_stop_play_files(self):
        for audio_datas, text in self.before_stop_play_files:
            self.tts_audio_queue.put((SentenceType.MIDDLE, audio_datas, text, getattr(self, 'current_sentence_id', None)))
        self.before_stop_play_files.clear()
        self.tts_audio_queue.put((SentenceType.LAST, [], None, getattr(self, 'current_sentence_id', None)))

    def _process_remaining_text_stream(
        self, opus_handler: Callable[[bytes], None] = None
    ):
        """Process remaining text and generate speech

        Returns:
            bool: whether text processed successfully
        """
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars :]
        if remaining_text:
            segment_text = textUtils.get_string_no_punctuation_or_emoji(remaining_text)
            segment_text = self._clean_segment_text_for_tts(segment_text)
            if segment_text:
                self.to_tts_stream(segment_text, opus_handler=opus_handler)
                self.processed_chars += len(full_text)
                return True
        return False

    def _apply_percentage_params(self, config):
        """Batch apply percentage parameters based on TTS_PARAM_CONFIG defined by subclass"""
        for config_key, attr_name, min_val, max_val, base_val, transform in self.TTS_PARAM_CONFIG:
            if config_key in config:
                val = convert_percentage_to_range(config[config_key], min_val, max_val, base_val)
                setattr(self, attr_name, transform(val) if transform else val)

    def _match_stream_text(self, text):
        """Streaming text sliding-window match, used to handle replacement words across chunks

        Args:
            text: input text chunk

        Returns:
            tuple: (confirmed text list, remaining prefix to match)
        """
        if not self.correct_words or not text:
            return [text] if text else [], ""

        result = []
        pending = self._pending_prefix
        i = 0

        while i < len(text):
            char = text[i]

            # Try:pending + Whether current character can matchReplacement word
            test_text = pending + char

            matched = False
            # Traverse possible matchingReplacement word
            candidates = self._words_by_first_char.get(pending[0], []) if pending else self._words_by_first_char.get(char, [])
            for key in candidates:
                if test_text == key:
                    # Full match, replace then send
                    result.append(self.correct_words[key])
                    pending = ""
                    matched = True
                    break
                elif key.startswith(test_text):
                    # isReplacement wordprefix, continue waiting
                    pending = test_text
                    matched = True
                    break

            if matched:
                i += 1
                continue

            # No longer word matched,pending ofContentConfirm can send
            if pending:
                result.append(pending)
                pending = ""

            # Check whether current character is one ofReplacement wordStart of
            if char in self._words_by_first_char:
                pending = char
            else:
                result.append(char)

            i += 1

        return result, pending

    def reset_stream_state(self):
        """Reset streaming processing state, used to clear residual state at session start"""
        self._pending_prefix = ""
