from collections.abc import Mapping
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import warnings

warnings.filterwarnings(
    "ignore",
    message=r"'audioop' is deprecated and slated for removal in Python 3\.13",
    category=DeprecationWarning,
)

import audioop
import json
import time
import uuid

from core.utils import textUtils
from core.voice.child_safety import SAFE_DEFLECTION_LINE, screen_model_output


EMOTION_EMOJI = {
    "happy": "🙂",
    "laughing": "😆",
    "funny": "😂",
    "sad": "😔",
    "crying": "😭",
    "angry": "😠",
    "loving": "😍",
    "surprised": "😲",
    "shocked": "😱",
    "thinking": "🤔",
    "relaxed": "😌",
    "sleepy": "😴",
    "silly": "😜",
    "confused": "🙄",
    "neutral": "😶",
    "embarrassed": "😳",
    "winking": "😉",
    "cool": "😎",
    "delicious": "🤤",
    "kissy": "😘",
    "confident": "😏",
}


EMOTION_KEYWORDS = (
    ("sad", ("sorry", "unfortunately", "sad", "buồn", "tiếc", "xin lỗi")),
    ("thinking", ("think", "maybe", "let me", "hmm", "xem", "nghĩ", "có thể")),
    ("surprised", ("wow", "amazing", "really", "thật à", "bất ngờ")),
    ("happy", ("great", "good", "thanks", "nice", "tốt", "vui", "được")),
)


FALLBACK_EMOTION_CYCLE = ("happy", "thinking", "confident", "winking", "surprised")

class GoogleLiveAudioBridge:
    """Bridge between websocket audio frames and live transport events."""

    def __init__(
        self,
        conn,
        client,
        logger,
        response_id_getter=None,
        response_cancelled_checker=None,
        user_transcript_handler=None,
        user_transcript_barge_in_handler=None,
        tool_call_handler=None,
        tool_call_cancellation_handler=None,
        model_output_unblocked_handler=None,
    ):
        self.conn = conn
        self.client = client
        self.logger = logger
        self._response_id_getter = response_id_getter or (lambda: None)
        self._response_cancelled_checker = response_cancelled_checker or (
            lambda response_id: False
        )
        self._user_transcript_handler = user_transcript_handler
        self._user_transcript_barge_in_handler = user_transcript_barge_in_handler
        self._tool_call_handler = tool_call_handler
        self._tool_call_cancellation_handler = tool_call_cancellation_handler
        self._model_output_unblocked_handler = model_output_unblocked_handler
        self._aec_processor = self._build_aec_processor()
        self._aec_reference_resampler_rates = None
        self._aec_reference_resampler_state = None
        self._active_response_id = None
        self._locally_cancelled_response_ids = set()
        self._input_decoder = None
        self._output_encoder = None
        self._first_audio_out_logged = False
        self._output_chunk_count = 0
        self._output_byte_count = 0
        self._input_chunk_count = 0
        self._suppress_audio_until = 0
        self._block_model_output_until_user_ack = False
        self._input_resampler_state = None
        self._output_resampler_state = None
        self._input_resampler_rates = None
        self._output_resampler_rates = None
        self._input_live_chunk_buffer = bytearray()
        self._unblock_timer_task = None
        self._accepted_user_turn_after_block = False
        self._waiting_for_interrupted_audio_end = False
        self._moderation_block_active = False
        self._fallback_emotion_index = 0
        self._last_emotion_sent = None
        self._audio_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"google-live-audio-{id(self):x}",
        )
        self._audio_executor_closed = False
        # Rolling buffer of recently-transcribed model speech, used to detect
        # speaker echo that Gemini STT mis-attributes to the user and would
        # otherwise fire a transcript_barge_in mid-sentence.
        # Entries: (monotonic_ts, normalized_text)
        self._recent_model_transcript_segments = []

    async def forward_input_audio(self, audio_bytes):
        await self.forward_decoded_input_audio(
            await self.decode_input_audio_async(audio_bytes)
        )

    async def forward_decoded_input_audio(self, pcm_bytes):
        if not self._is_valid_pcm16(pcm_bytes):
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live dropped invalid input audio bytes={}",
                len(pcm_bytes or b""),
            )
            return None
        self._log_input_audio_diagnostics(pcm_bytes)
        self._input_live_chunk_buffer.extend(pcm_bytes)
        chunk_bytes = self._get_live_input_chunk_bytes()
        while len(self._input_live_chunk_buffer) >= chunk_bytes:
            chunk = bytes(self._input_live_chunk_buffer[:chunk_bytes])
            del self._input_live_chunk_buffer[:chunk_bytes]
            await self.client.send_audio(chunk)

    async def flush_pending_input_audio(self):
        if not self._input_live_chunk_buffer:
            return 0
        chunk = bytes(self._input_live_chunk_buffer)
        self._input_live_chunk_buffer.clear()
        await self.client.send_audio(chunk)
        return len(chunk)

    def decode_input_audio(self, audio_bytes):
        return self._decode_input_audio(audio_bytes)

    async def decode_input_audio_async(self, audio_bytes):
        return await self._run_audio_cpu(self._decode_input_audio, audio_bytes)

    def input_rms(self, pcm_bytes):
        if not pcm_bytes:
            return 0
        return audioop.rms(pcm_bytes, 2)

    async def handle_event(self, event):
        if not isinstance(event, Mapping):
            return False

        event_type = event.get("type")
        if event_type == "transcript":
            transcript_text = event.get("text")
            if transcript_text is None:
                return False
            self.logger.bind(tag="GoogleLive").info(
                "Google Live transcript source={} chars={}",
                event.get("source") or "unknown",
                len(transcript_text),
            )
            if event.get("source") == "user":
                if await self._maybe_handle_user_transcript_intent(transcript_text):
                    return True
                await self._maybe_trigger_transcript_barge_in(transcript_text)
            if event.get("source") == "model":
                self._record_model_transcript(transcript_text)
                if self._is_unsafe_model_output(transcript_text):
                    await self._block_unsafe_model_output(transcript_text)
                    return True
                if (
                    self._should_drop_blocked_model_event(event_type)
                    or self._is_stale_response_event()
                ):
                    return True
            if self._should_send_llm_state(event):
                await self._send_llm_message(transcript_text)
                return True
            if event.get("source") == "model":
                await self._send_emotion_message(transcript_text)
            await self._send_display_message(transcript_text)
            return True

        if event_type == "audio_start":
            if self._moderation_block_active:
                self._active_response_id = self._response_id_getter()
                self._mark_active_response_cancelled()
                return True
            if self._should_drop_blocked_model_event(event_type):
                return True
            if self._block_model_output_until_user_ack:
                self._active_response_id = self._response_id_getter()
                self._mark_active_response_cancelled()
                return True
            if time.monotonic() < self._suppress_audio_until:
                self._active_response_id = self._response_id_getter()
                self._mark_active_response_cancelled()
                return True
            self._active_response_id = self._response_id_getter()
            self._locally_cancelled_response_ids.discard(self._active_response_id)
            if len(self._locally_cancelled_response_ids) > 20:
                self._locally_cancelled_response_ids = set(
                    sorted(self._locally_cancelled_response_ids)[-10:]
                )
            # Anti-mix gate: if music has ALREADY been streaming chunks
            # (frame_index > 0), pause it before sending TTS so the device
            # doesn't mix robot voice with music. The frame_index>0 check
            # avoids breaking the initial "Đã phát nhạc" reply — at that
            # moment the music_session exists but has not sent any frame yet.
            session = getattr(self.conn, "_music_session", None)
            if session is not None:
                streaming_started = getattr(session, "frame_index", 0) > 0
                already_paused = (
                    hasattr(session, "is_paused") and session.is_paused()
                )
                if streaming_started and not already_paused:
                    try:
                        if hasattr(session, "pause"):
                            session.pause()
                            self.logger.bind(tag="GoogleLive").info(
                                "music_auto_paused trigger=audio_start_overlap "
                                "frame_index={}",
                                session.frame_index,
                            )
                    except Exception as exc:
                        self.logger.bind(tag="GoogleLive").warning(
                            "music_auto_pause_at_audio_start failed: {}", exc
                        )
            self.conn.google_live_audio_out_started_at = time.monotonic()
            self._output_chunk_count = 0
            self._output_byte_count = 0
            self.logger.bind(tag="GoogleLive").info("Google Live audio_start")
            await self._send_tts_message("start")
            return True

        if event_type in {"audio", "audio_chunk"}:
            audio_bytes = event.get("audio")
            if audio_bytes is None:
                return False
            if self._moderation_block_active:
                return True
            if self._should_drop_blocked_model_event(event_type):
                return True
            if self._is_stale_response_event():
                self.logger.bind(tag="GoogleLive").info(
                    "model_output_chunk_dropped reason=stale_response_id old={} current={}",
                    self._active_response_id,
                    self._response_id_getter(),
                )
                return True
            self._output_chunk_count += 1
            self._output_byte_count += len(audio_bytes)
            self._log_first_audio_out_latency()
            self._record_turn_first_audio_latency()
            await self._send_binary_audio_message(
                audio_bytes,
                audio_format=event.get("audio_format"),
                mime_type=event.get("mime_type"),
            )
            return True

        if event_type == "audio_end":
            if self._moderation_block_active:
                self._moderation_block_active = False
                self._reset_output_encoder()
                self._active_response_id = None
                return True
            if self._block_model_output_until_user_ack:
                self._reset_output_encoder()
                self._active_response_id = None
                self._waiting_for_interrupted_audio_end = False
                self._log_stale_model_event_drop(event_type, "blocked_until_user_turn")
                if self._maybe_unblock_after_interrupted_turn_drained():
                    self.logger.bind(tag="GoogleLive").info(
                        "model_output_unblock_trigger source=turn_drained"
                    )
                    await self._notify_model_output_unblocked()
                return True
            if self._is_stale_response_event():
                self._reset_output_encoder()
                self._active_response_id = None
                return True
            flushed_packets = await self._flush_output_audio()
            self.logger.bind(tag="GoogleLive").info(
                "Google Live audio_end chunks={} bytes={} flushed_packets={}",
                self._output_chunk_count,
                self._output_byte_count,
                flushed_packets,
            )
            self._output_chunk_count = 0
            self._output_byte_count = 0
            self.conn.google_live_audio_out_started_at = None
            self._active_response_id = None
            await self._send_tts_message("stop")
            return True

        if event_type == "tool_call":
            if self._should_drop_blocked_model_event(event_type):
                return True
            if self._tool_call_handler is not None:
                try:
                    await self._tool_call_handler(event)
                except Exception as exc:
                    self.logger.bind(tag="GoogleLive").warning(
                        "Google Live tool_call handler failed: {}", exc
                    )
            else:
                self.logger.bind(tag="GoogleLive").warning(
                    "Google Live tool_call dropped (no handler)"
                )
            return True

        if event_type == "tool_call_cancellation":
            if self._should_drop_blocked_model_event(event_type):
                return True
            if self._tool_call_cancellation_handler is not None:
                try:
                    await self._tool_call_cancellation_handler(event)
                except Exception as exc:
                    self.logger.bind(tag="GoogleLive").warning(
                        "Google Live tool_call_cancellation handler failed: {}", exc
                    )
            return True

        if event_type == "interruption":
            if self._server_side_interruptions_disabled():
                self.logger.bind(tag="GoogleLive").info(
                    "Google Live server interruption ignored by config"
                )
                return True
            output_age = self._current_output_age_sec()
            min_age = self._get_interruption_min_output_age_sec()
            if output_age is not None and output_age < min_age:
                self.logger.bind(tag="GoogleLive").info(
                    "Google Live interruption suppressed_for_age "
                    "output_age_ms={:.0f} threshold_ms={:.0f}",
                    output_age * 1000,
                    min_age * 1000,
                )
                return True
            self.conn.client_abort = True
            self.conn.google_live_audio_out_started_at = None
            self._mark_active_response_cancelled()
            self._reset_output_encoder()
            self._clear_conn_queues()
            self.logger.bind(tag="GoogleLive").info(
                "Google Live interruption output_age_ms={}",
                round(output_age * 1000, 1) if output_age is not None else "n/a",
            )
            await self._send_tts_stop_now()
            return True

        return False

    async def stop_output(self):
        self.conn.google_live_audio_out_started_at = None
        self._mark_active_response_cancelled()
        self._waiting_for_interrupted_audio_end = (
            self._active_response_id is not None
            or self._output_chunk_count > 0
            or self._output_byte_count > 0
        )
        self._accepted_user_turn_after_block = False
        self._suppress_audio_until = max(
            self._suppress_audio_until,
            time.monotonic() + self._get_interrupt_suppress_audio_sec(),
        )
        self._block_model_output_until_user_ack = True
        self._output_chunk_count = 0
        self._output_byte_count = 0
        self._reset_output_encoder()
        self._clear_conn_queues()
        self._schedule_unblock_timeout()
        await self._send_tts_stop_now()

    def current_response_id(self):
        return self._active_response_id

    def allow_model_output(self):
        if not self._block_model_output_until_user_ack:
            self._cancel_unblock_timer()
            return
        self._accepted_user_turn_after_block = True
        if not self._waiting_for_interrupted_audio_end:
            if self._unblock_model_output():
                self.logger.bind(tag="GoogleLive").info(
                    "model_output_unblock_trigger source=user_ack"
                )
                self._schedule_model_output_unblocked_notification()

    def is_model_output_blocked(self):
        return self._block_model_output_until_user_ack

    async def close(self):
        self._cancel_unblock_timer()
        if not self._audio_executor_closed:
            self._audio_executor_closed = True
            self._audio_executor.shutdown(wait=False, cancel_futures=True)

    async def _run_audio_cpu(self, func, *args, **kwargs):
        if self._audio_executor_closed:
            return func(*args, **kwargs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._audio_executor,
            partial(func, *args, **kwargs),
        )

    def _schedule_unblock_timeout(self):
        timeout = self._get_unblock_timeout_sec()
        if timeout <= 0:
            return
        self._cancel_unblock_timer()
        self._unblock_timer_task = asyncio.create_task(self._unblock_after(timeout))

    async def _unblock_after(self, seconds):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if self._block_model_output_until_user_ack:
            try:
                self.logger.bind(tag="GoogleLive").info(
                    "Google Live model_output_still_blocked_waiting_user_turn after {:.0f} ms accepted_user_turn={} waiting_audio_end={}",
                    seconds * 1000,
                    self._accepted_user_turn_after_block,
                    self._waiting_for_interrupted_audio_end,
                )
            except Exception:
                pass

    def _cancel_unblock_timer(self):
        task = self._unblock_timer_task
        self._unblock_timer_task = None
        if task is not None and not task.done():
            task.cancel()

    def _get_unblock_timeout_sec(self):
        config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        try:
            value = float(config.get("model_output_unblock_timeout_sec", 1.5))
        except (TypeError, ValueError):
            value = 1.5
        return max(0.0, value)

    def _unblock_model_output(self):
        if not self._block_model_output_until_user_ack:
            return False
        self._block_model_output_until_user_ack = False
        self._accepted_user_turn_after_block = False
        self._waiting_for_interrupted_audio_end = False
        self._cancel_unblock_timer()
        return True

    def _maybe_unblock_after_interrupted_turn_drained(self):
        if (
            self._block_model_output_until_user_ack
            and self._accepted_user_turn_after_block
            and not self._waiting_for_interrupted_audio_end
        ):
            return self._unblock_model_output()
        return False

    def _schedule_model_output_unblocked_notification(self):
        if self._model_output_unblocked_handler is None:
            return
        self.logger.bind(tag="GoogleLive").info(
            "model_output_unblock_trigger source=unblock_scheduled"
        )
        asyncio.create_task(self._notify_model_output_unblocked())

    async def _notify_model_output_unblocked(self):
        if self._model_output_unblocked_handler is None:
            return
        try:
            result = self._model_output_unblocked_handler()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live model_output_unblocked handler failed: {}", exc
            )

    def _should_drop_blocked_model_event(self, event_type):
        if not self._block_model_output_until_user_ack:
            return False
        self._log_stale_model_event_drop(event_type, "blocked_until_user_turn")
        return True

    def _log_stale_model_event_drop(self, event_type, reason):
        try:
            self.logger.bind(tag="GoogleLive").info(
                "Google Live stale_model_event_dropped type={} reason={} response_id={} current_response_id={}",
                event_type,
                reason,
                self._active_response_id,
                self._response_id_getter(),
            )
        except Exception:
            pass

    async def _maybe_handle_user_transcript_intent(self, transcript_text):
        if self._user_transcript_handler is None:
            return False
        try:
            return bool(await self._user_transcript_handler(transcript_text))
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live user_transcript handler failed: {}",
                exc,
            )
            return False

    async def _maybe_trigger_transcript_barge_in(self, transcript_text):
        """Fire a barge-in when Live API recognises real user words during model output.

        This sidesteps the echo-driven false positives that plagued raw VAD: the
        Live STT only emits a user transcript when it transcribes recognisable
        speech, which the test data showed reliably filters out speaker echo.
        Cost is a higher latency (~1-2s, the time STT needs to accumulate audio)
        compared to direct VAD.
        """
        if self._user_transcript_barge_in_handler is None:
            return
        config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        if not bool(config.get("barge_in_via_transcript", False)):
            return
        if (
            getattr(self.conn, "google_live_audio_out_started_at", None) is None
            and not self._has_music_session()
        ):
            return  # no model/music output to interrupt
        try:
            min_chars = int(config.get("barge_in_transcript_min_chars", 3))
        except (TypeError, ValueError):
            min_chars = 3
        if len(transcript_text or "") < max(1, min_chars):
            return
        try:
            min_output_age = float(
                config.get("barge_in_transcript_min_output_age_sec", 2.0)
            )
        except (TypeError, ValueError):
            min_output_age = 2.0
        output_age = self._current_output_age_sec()
        if (
            min_output_age > 0
            and output_age is not None
            and output_age < min_output_age
        ):
            self.logger.bind(tag="GoogleLive").info(
                "Google Live transcript_barge_in suppressed_for_age "
                "output_age_ms={:.0f} threshold_ms={:.0f}",
                output_age * 1000,
                min_output_age * 1000,
            )
            return
        if self._looks_like_model_echo(transcript_text):
            self.logger.bind(tag="GoogleLive").info(
                "Google Live transcript_barge_in suppressed_as_model_echo "
                "chars={} text_preview={!r}",
                len(transcript_text),
                transcript_text[:40],
            )
            return
        self.logger.bind(tag="GoogleLive").info(
            "Google Live transcript_barge_in chars={} text_preview={!r}",
            len(transcript_text),
            transcript_text[:40],
        )
        try:
            await self._user_transcript_barge_in_handler(transcript_text)
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live transcript_barge_in handler failed: {}",
                exc,
            )

    def _has_music_session(self):
        session = getattr(self.conn, "_music_session", None)
        if session is None:
            return False
        stop_event = getattr(session, "stop_event", None)
        if stop_event is not None and hasattr(stop_event, "is_set"):
            try:
                return not stop_event.is_set()
            except Exception:
                return True
        return True

    async def _send_display_message(self, text):
        if self.conn.websocket is None:
            return
        from core.handle.sendAudioHandle import send_display_message

        await send_display_message(self.conn, text)

    async def _send_llm_message(self, text):
        if self.conn.websocket is None:
            return
        import json

        emotion = self._derive_emotion(text)

        await self.conn.websocket.send(
            json.dumps(
                {
                    "type": "llm",
                    "text": text,
                    "emotion": emotion,
                    "session_id": self.conn.session_id,
                }
            )
        )
        self._last_emotion_sent = emotion

    async def _send_emotion_message(self, text):
        if self.conn.websocket is None:
            return
        emotion = self._derive_emotion(text)
        if emotion == self._last_emotion_sent:
            return
        self._last_emotion_sent = emotion
        await self.conn.websocket.send(
            json.dumps(
                {
                    "type": "llm",
                    "text": EMOTION_EMOJI.get(emotion, "🙂"),
                    "emotion": emotion,
                    "session_id": self.conn.session_id,
                }
            )
        )

    def _derive_emotion(self, text):
        text = text or ""
        for char in text:
            if char in textUtils.EMOJI_MAP:
                return textUtils.EMOJI_MAP[char]
        lowered = text.lower()
        for emotion, keywords in EMOTION_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                return emotion
        emotion = FALLBACK_EMOTION_CYCLE[
            self._fallback_emotion_index % len(FALLBACK_EMOTION_CYCLE)
        ]
        self._fallback_emotion_index += 1
        return emotion

    async def _send_tts_message(self, state):
        if self.conn.websocket is None:
            return
        from core.handle.sendAudioHandle import send_tts_message

        extra_fields = None
        if state == "stop":
            extra_fields = {
                "continue_listening": True,
                "listen_mode": "realtime",
            }
        await send_tts_message(self.conn, state, extra_fields=extra_fields)
        if state == "start":
            self.conn.client_is_speaking = True
        elif state == "stop":
            self.conn.client_is_speaking = False
            self._mark_echo_tail_suppression("tts_stop")

    async def _send_tts_stop_now(self):
        if self.conn.websocket is None:
            return
        if hasattr(self.conn, "clearSpeakStatus"):
            self.conn.clearSpeakStatus()
        self.conn.client_is_speaking = False
        self._mark_echo_tail_suppression("tts_stop_now")
        await self.conn.websocket.send(
            json.dumps(
                {
                    "type": "tts",
                    "state": "stop",
                    # Patch 3.4: this sender is interrupt-only (barge-in). Tag the
                    # stop so the device cuts playback immediately (ResetDecoder)
                    # instead of draining the queue like a normal end-of-turn stop.
                    "reason": "interrupt",
                    "session_id": self.conn.session_id,
                }
            )
        )

    def _is_unsafe_model_output(self, text):
        return bool(screen_model_output(text).get("blocked"))

    async def _block_unsafe_model_output(self, text):
        self._moderation_block_active = True
        self._active_response_id = self._response_id_getter()
        self._mark_active_response_cancelled()
        self.conn.client_abort = True
        self.conn.google_live_audio_out_started_at = None
        self._block_model_output_until_user_ack = True
        self._waiting_for_interrupted_audio_end = True
        self._accepted_user_turn_after_block = False
        self._output_chunk_count = 0
        self._output_byte_count = 0
        self._reset_output_encoder()
        self._clear_conn_queues()
        self._schedule_unblock_timeout()
        self.logger.bind(tag="GoogleLive").warning(
            "Google Live output_moderation_blocked source=model_output"
        )
        await self._send_tts_stop_now()
        self.conn.client_abort = False
        await self._send_safe_deflection()
        await self._enqueue_safety_block(text)

    async def _send_safe_deflection(self):
        if self.conn.websocket is None:
            return
        from core.handle.sendAudioHandle import send_tts_message

        await send_tts_message(
            self.conn,
            "sentence_start",
            SAFE_DEFLECTION_LINE,
        )
        self._queue_safe_deflection_tts()

    def _queue_safe_deflection_tts(self):
        tts = getattr(self.conn, "tts", None)
        queue_obj = getattr(tts, "tts_text_queue", None)
        if queue_obj is None:
            return
        try:
            from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

            sentence_id = str(uuid.uuid4().hex)
            self.conn.sentence_id = sentence_id
            queue_obj.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
            queue_obj.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=ContentType.TEXT,
                    content_detail=SAFE_DEFLECTION_LINE,
                )
            )
            queue_obj.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            if hasattr(tts, "store_tts_text"):
                tts.store_tts_text(sentence_id, SAFE_DEFLECTION_LINE)
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live safe_deflection_tts_queue failed: {}",
                exc,
            )

    async def _enqueue_safety_block(self, text):
        forwarder = await self._resolve_safety_event_forwarder()
        if forwarder is None:
            return
        forwarder.enqueue(
            {
                "eventType": "safety_block",
                "detail": {
                    "source": "model_output",
                    "text": self._redact_safety_text(text),
                },
            }
        )

    async def _resolve_safety_event_forwarder(self):
        for candidate in (
            getattr(self.conn, "safety_event_forwarder", None),
            getattr(getattr(self.conn, "lesson_runtime", None), "forwarder", None),
        ):
            if candidate is not None and hasattr(candidate, "enqueue"):
                return candidate
        return await self._create_connection_safety_forwarder()

    async def _create_connection_safety_forwarder(self):
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = config.get("lesson", {}) or {}
        server_cfg = config.get("server", {}) or {}
        base_url = lesson_cfg.get("api_base") or server_cfg.get("api_url")
        device_id = getattr(self.conn, "device_id", None)
        if not base_url or not device_id:
            return None
        try:
            import httpx
            from config.device_token_client import resolve_device_identity
            from core.lesson.forwarder import LessonEventForwarder
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live safety forwarder unavailable: {}",
                exc,
            )
            return None

        token = lesson_cfg.get("device_token")
        backend_device_id = device_id
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(max_keepalive_connections=0),
                follow_redirects=True,
            ) as client:
                minted_uuid, minted_token = await resolve_device_identity(
                    client, base_url, device_id, logger=self.logger
                )
            if minted_uuid and minted_token:
                backend_device_id = minted_uuid
                token = minted_token
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live safety forwarder token mint failed: {}",
                exc,
            )

        if not token:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live safety_block event dropped: no device token"
            )
            return None
        forwarder = LessonEventForwarder(
            device_id=backend_device_id,
            base_url=base_url,
            token=token,
            logger=self.logger,
        )
        setattr(self.conn, "safety_event_forwarder", forwarder)
        return forwarder

    def _redact_safety_text(self, text):
        redacted = str(text or "")
        for phrase in (
            "home address",
            "phone number",
            "so we can meet",
            "tell me where you live",
        ):
            redacted = redacted.replace(phrase, "[redacted]")
            redacted = redacted.replace(phrase.title(), "[redacted]")
        return redacted

    def _decode_input_audio(self, audio_bytes):
        if not audio_bytes:
            return audio_bytes
        input_rate = int(getattr(self.conn, "sample_rate", 24000))
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        target_rate = int(client_config.get("input_sample_rate", 16000))
        self._last_input_source_rate = input_rate
        self._last_input_target_rate = target_rate
        self._last_input_encoded_bytes = len(audio_bytes)
        try:
            decoder = self._get_input_decoder()
            pcm_bytes = decoder.decode(audio_bytes, self._get_input_frame_size())
            resampled = self._resample_pcm16(pcm_bytes, input_rate, target_rate)
            return self._apply_aec(resampled, target_rate)
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live dropped corrupt input opus encoded_bytes={} source_rate={} target_rate={} error_type={}",
                len(audio_bytes),
                input_rate,
                target_rate,
                type(exc).__name__,
            )
            return b""

    async def _send_binary_audio_message(
        self,
        audio_bytes,
        audio_format=None,
        mime_type=None,
    ):
        if audio_bytes is None or self.conn.websocket is None:
            return
        from core.handle.sendAudioHandle import sendAudio

        if audio_format == "pcm16" or (mime_type and "audio/pcm" in mime_type):
            packets = await self._run_audio_cpu(
                self._encode_output_audio, audio_bytes, mime_type
            )
            if packets:
                await sendAudio(self.conn, packets)
            return
        if audio_bytes:
            await sendAudio(self.conn, audio_bytes)

    def _encode_output_audio(self, audio_bytes, mime_type=None):
        source_rate = self._extract_sample_rate_from_mime(mime_type)
        target_rate = int(getattr(self.conn, "sample_rate", 24000))
        # Push the reference signal into the AEC stage BEFORE encoding so
        # the mic-side echo cancellation has up-to-date far-end audio. We
        # resample the model output once more to the AEC sample rate
        # because mic processing happens after the input-side resample.
        self._push_aec_reference(audio_bytes, source_rate)
        pcm_bytes = self._resample_pcm16(
            audio_bytes, source_rate, target_rate, direction="output"
        )
        packets = []
        self._get_output_encoder(target_rate).encode_pcm_to_opus_stream(
            pcm_bytes,
            end_of_stream=False,
            callback=packets.append,
        )
        return packets

    async def _flush_output_audio(self):
        if self.conn.websocket is None or self._output_encoder is None:
            return 0
        from core.handle.sendAudioHandle import sendAudio

        packets = await self._run_audio_cpu(self._flush_output_audio_sync)
        if packets:
            await sendAudio(self.conn, packets)
        return len(packets)

    def _flush_output_audio_sync(self):
        packets = []
        self._output_encoder.encode_pcm_to_opus_stream(
            b"",
            end_of_stream=True,
            callback=packets.append,
        )
        return packets

    def _extract_sample_rate_from_mime(self, mime_type):
        if mime_type and "rate=" in mime_type:
            try:
                return int(str(mime_type).split("rate=", 1)[1].split(";", 1)[0])
            except (TypeError, ValueError):
                pass
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        return int(client_config.get("output_sample_rate", 24000))

    def _log_first_audio_out_latency(self):
        if self._first_audio_out_logged:
            return
        session_started_at = getattr(self.conn, "google_live_session_started_at", None)
        if session_started_at is None:
            return
        self._first_audio_out_logged = True
        self.logger.bind(tag="GoogleLive").info(
            "Google Live first_audio_out_latency_ms={:.1f}",
            (time.monotonic() - session_started_at) * 1000,
        )

    def _record_turn_first_audio_latency(self):
        turn_started_at = getattr(self.conn, "google_live_turn_started_at", None)
        if turn_started_at is None:
            return
        try:
            latency_ms = max(0.0, (time.monotonic() - float(turn_started_at)) * 1000)
        except (TypeError, ValueError):
            self.conn.google_live_turn_started_at = None
            return
        self.conn.google_live_turn_started_at = None
        self.logger.bind(tag="GoogleLive").info(
            "Google Live turn_latency_ms={:.1f} phase=first_audio_out",
            latency_ms,
        )
        self._note_voice_round_trip(latency_ms)
        self._record_turn_latency_metric(latency_ms)

    def _note_voice_round_trip(self, latency_ms):
        note_voice_round_trip = getattr(self.conn, "note_voice_round_trip", None)
        if not callable(note_voice_round_trip):
            return
        try:
            note_voice_round_trip(latency_ms)
        except Exception:
            pass

    def _record_turn_latency_metric(self, latency_ms):
        record_voice_metric = getattr(self.conn, "record_voice_metric", None)
        if not callable(record_voice_metric):
            return
        try:
            record_voice_metric(
                "turn_latency_ms",
                latency_ms,
                {"source": "google_live", "phase": "first_audio_out"},
            )
        except Exception:
            pass

    def _should_send_llm_state(self, event):
        if event.get("source") != "model":
            return False
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        return bool(client_config.get("send_llm_state_events", False))

    def _current_output_age_sec(self):
        started_at = getattr(self.conn, "google_live_audio_out_started_at", None)
        if started_at is None:
            return None
        return max(0.0, time.monotonic() - started_at)

    def _get_interruption_min_output_age_sec(self):
        config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        try:
            # GLIVE-2: default lowered 0.5 -> 0.2 so an early barge-in in the first
            # half-second of output is not swallowed. Still overridable via config.
            value = float(config.get("interruption_min_output_age_sec", 0.2))
        except (TypeError, ValueError):
            value = 0.2
        return max(0.0, value)

    def _server_side_interruptions_disabled(self):
        config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        # Explicit operator override: ignore_server_interruptions=True forces live
        # interruptions to be HONORED (subject only to the output-age guard), so it can
        # never silently block a real barge-in regardless of the disable default.
        if config.get("ignore_server_interruptions"):
            return False
        return bool(config.get("disable_server_side_interruptions", True))

    def _get_transcript_echo_window_sec(self):
        config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        try:
            value = float(config.get("transcript_echo_window_sec", 15.0))
        except (TypeError, ValueError):
            value = 15.0
        return max(0.0, value)

    @staticmethod
    def _normalize_transcript_for_echo(text):
        if not text:
            return ""
        return "".join(ch for ch in text.lower() if ch.isalnum())

    def _record_model_transcript(self, text):
        norm = self._normalize_transcript_for_echo(text)
        if not norm:
            return
        window = self._get_transcript_echo_window_sec()
        if window <= 0:
            return
        now = time.monotonic()
        cutoff = now - window
        segments = [
            (t, s) for (t, s) in self._recent_model_transcript_segments if t >= cutoff
        ]
        segments.append((now, norm))
        self._recent_model_transcript_segments = segments

    def _looks_like_model_echo(self, user_text):
        norm_user = self._normalize_transcript_for_echo(user_text)
        if len(norm_user) < 3:
            return False
        window = self._get_transcript_echo_window_sec()
        if window <= 0:
            return False
        cutoff = time.monotonic() - window
        for ts, norm_model in self._recent_model_transcript_segments:
            if ts < cutoff:
                continue
            if norm_user in norm_model:
                return True
        return False

    def _resample_pcm16(self, pcm_bytes, source_rate, target_rate, direction="input"):
        if source_rate == target_rate:
            return pcm_bytes
        rates = (source_rate, target_rate)
        if direction == "output":
            if self._output_resampler_rates != rates:
                self._output_resampler_state = None
                self._output_resampler_rates = rates
            state = self._output_resampler_state
        else:
            if self._input_resampler_rates != rates:
                self._input_resampler_state = None
                self._input_resampler_rates = rates
            state = self._input_resampler_state
        converted, new_state = audioop.ratecv(
            pcm_bytes, 2, 1, source_rate, target_rate, state
        )
        if direction == "output":
            self._output_resampler_state = new_state
        else:
            self._input_resampler_state = new_state
        return converted

    def _is_valid_pcm16(self, pcm_bytes):
        return bool(pcm_bytes) and len(pcm_bytes) % 2 == 0

    def _log_input_audio_diagnostics(self, pcm_bytes):
        self._input_chunk_count += 1
        if self._input_chunk_count != 1 and self._input_chunk_count % 50 != 0:
            return
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        if not bool(client_config.get("log_audio_diagnostics", True)):
            return
        source_rate = getattr(
            self,
            "_last_input_source_rate",
            int(getattr(self.conn, "sample_rate", 24000)),
        )
        target_rate = getattr(
            self,
            "_last_input_target_rate",
            int(client_config.get("input_sample_rate", 16000)),
        )
        encoded_bytes = getattr(self, "_last_input_encoded_bytes", None)
        self.logger.bind(tag="GoogleLive").info(
            "Google Live input_audio_diag encoded_bytes={} decoded_bytes={} rms={} source_rate={} target_rate={} sample_width=2",
            encoded_bytes if encoded_bytes is not None else "unknown",
            len(pcm_bytes),
            self.input_rms(pcm_bytes),
            source_rate,
            target_rate,
        )

    def _get_input_decoder(self):
        if self._input_decoder is None:
            import opuslib_next

            sample_rate = int(getattr(self.conn, "sample_rate", 24000))
            self._input_decoder = opuslib_next.Decoder(sample_rate, 1)
        return self._input_decoder

    def _get_input_frame_size(self):
        sample_rate = int(getattr(self.conn, "sample_rate", 24000))
        return int(sample_rate * 60 / 1000)

    def _get_live_input_chunk_bytes(self):
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live",
            {},
        )
        try:
            chunk_ms = int(client_config.get("input_live_chunk_ms", 20))
        except (TypeError, ValueError):
            chunk_ms = 20
        chunk_ms = min(40, max(20, chunk_ms))
        try:
            sample_rate = int(client_config.get("input_sample_rate", 16000))
        except (TypeError, ValueError):
            sample_rate = 16000
        return max(2, int(sample_rate * chunk_ms / 1000) * 2)

    def _get_output_encoder(self, sample_rate):
        from core.utils.opus_encoder_utils import OpusEncoderUtils

        if self._output_encoder is None or self._output_encoder.sample_rate != sample_rate:
            self._output_encoder = OpusEncoderUtils(
                sample_rate=sample_rate,
                channels=1,
                frame_size_ms=60,
            )
        return self._output_encoder

    def _is_stale_response_event(self):
        response_id = self._active_response_id
        if response_id is None:
            return False
        if self._response_cancelled_checker(response_id):
            return True
        if response_id in self._locally_cancelled_response_ids:
            return True
        current_response_id = self._response_id_getter()
        return current_response_id is not None and response_id != current_response_id

    def _clear_conn_queues(self):
        tts_text_queue, tts_audio_queue, report_queue, rate_queue = (
            self._current_output_queue_lengths()
        )
        if hasattr(self.conn, "clear_queues"):
            self.conn.clear_queues()
        self.logger.bind(tag="GoogleLive").info(
            "output_queue_cleared reason=interrupt response_id={} "
            "tts_text_queue={} tts_audio_queue={} report_queue={} rate_queue={}",
            self._active_response_id,
            tts_text_queue,
            tts_audio_queue,
            report_queue,
            rate_queue,
        )
        rate_controller = getattr(self.conn, "audio_rate_controller", None)
        if rate_controller is not None and hasattr(rate_controller, "reset"):
            try:
                rate_controller.reset()
            except Exception as exc:
                self.logger.bind(tag="GoogleLive").warning(
                    "Google Live audio_rate_controller reset failed: {}", exc
                )

    def _current_output_queue_lengths(self):
        tts = getattr(self.conn, "tts", None)
        tts_text_queue = self._safe_queue_length(getattr(tts, "tts_text_queue", None))
        tts_audio_queue = self._safe_queue_length(getattr(tts, "tts_audio_queue", None))
        report_queue = self._safe_queue_length(getattr(self.conn, "report_queue", None))
        rate_controller = getattr(self.conn, "audio_rate_controller", None)
        rate_queue = self._safe_queue_length(getattr(rate_controller, "queue", None))
        return tts_text_queue, tts_audio_queue, report_queue, rate_queue

    @staticmethod
    def _safe_queue_length(queue_obj):
        if queue_obj is None:
            return 0
        if hasattr(queue_obj, "qsize"):
            try:
                return int(queue_obj.qsize())
            except Exception:
                return 0
        try:
            return len(queue_obj)
        except Exception:
            return 0

    def _reset_output_encoder(self):
        self._output_encoder = None
        self._output_chunk_count = 0
        self._output_byte_count = 0
        self._output_resampler_state = None
        self._output_resampler_rates = None

    def _mark_active_response_cancelled(self):
        if self._active_response_id is not None:
            self._locally_cancelled_response_ids.add(self._active_response_id)

    def _get_interrupt_suppress_audio_sec(self):
        config = self.conn.config.get("google_live", {})
        try:
            suppress_sec = float(config.get("interrupt_suppress_audio_sec", 0.25))
        except (TypeError, ValueError):
            suppress_sec = 0.25
        return max(0, suppress_sec)

    def _mark_echo_tail_suppression(self, reason):
        config = self.conn.config.get("google_live", {})
        try:
            tail_ms = float(config.get("echo_tail_suppression_ms", 400))
        except (TypeError, ValueError):
            tail_ms = 400.0
        if tail_ms <= 0:
            return
        until = time.monotonic() + tail_ms / 1000.0
        current_until = getattr(self.conn, "google_live_echo_suppress_until", 0.0)
        self.conn.google_live_echo_suppress_until = max(current_until, until)
        self.logger.bind(tag="GoogleLive").info(
            "echo_tail_suppression_started reason={} duration_ms={:.0f}",
            reason,
            tail_ms,
        )

    # ------------------------------------------------------------------
    # AEC integration
    # ------------------------------------------------------------------

    def _build_aec_processor(self):
        client_config = getattr(self.client, "config", None) or self.conn.config.get(
            "google_live", {}
        )
        if not isinstance(client_config, Mapping):
            client_config = {}
        enabled = bool(client_config.get("aec_enabled", False))
        if not enabled:
            return None
        try:
            from core.voice.aec import AecProcessor
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live AEC import failed, running without AEC: {}", exc
            )
            return None
        target_rate = int(client_config.get("input_sample_rate", 16000))
        try:
            filter_ms = int(client_config.get("aec_filter_length_ms", 200))
        except (TypeError, ValueError):
            filter_ms = 200
        try:
            frame_ms = int(client_config.get("aec_frame_ms", 10))
        except (TypeError, ValueError):
            frame_ms = 10
        processor = AecProcessor(
            sample_rate=target_rate,
            frame_ms=frame_ms,
            filter_ms=filter_ms,
            enabled=True,
        )
        self.logger.bind(tag="GoogleLive").info(
            "Google Live AEC initialised sample_rate={} filter_ms={} frame_ms={} bypassed={} reason={}",
            target_rate,
            filter_ms,
            frame_ms,
            processor.bypassed,
            processor.reason,
        )
        return processor

    def _apply_aec(self, pcm_bytes, rate):
        if self._aec_processor is None or self._aec_processor.bypassed:
            return pcm_bytes
        if rate != self._aec_processor.sample_rate:
            # Defensive: should not happen because we only invoke this after
            # resampling to input_sample_rate which matches the AEC rate.
            return pcm_bytes
        try:
            return self._aec_processor.process_mic(pcm_bytes)
        except Exception as exc:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live AEC process_mic failed, dropping AEC for this chunk: {}",
                exc,
            )
            return pcm_bytes

    def _push_aec_reference(self, pcm_bytes, source_rate):
        if (
            self._aec_processor is None
            or self._aec_processor.bypassed
            or not pcm_bytes
        ):
            return
        target_rate = self._aec_processor.sample_rate
        if source_rate == target_rate:
            resampled = pcm_bytes
        else:
            try:
                rates = (source_rate, target_rate)
                if self._aec_reference_resampler_rates != rates:
                    self._aec_reference_resampler_state = None
                    self._aec_reference_resampler_rates = rates
                resampled, self._aec_reference_resampler_state = audioop.ratecv(
                    pcm_bytes,
                    2,
                    1,
                    source_rate,
                    target_rate,
                    self._aec_reference_resampler_state,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.bind(tag="GoogleLive").warning(
                    "Google Live AEC reference resample failed: {}", exc
                )
                return
        try:
            self._aec_processor.push_reference(resampled)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live AEC push_reference failed: {}", exc
            )
