import asyncio
import hashlib
import json
import re
import time
import unicodedata
from collections import deque
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from core.activity_lease import ActivityOperation
from core.voice.google_live import GoogleLiveAudioBridge, GoogleLiveClientFactory
from core.voice.output_safety_judge import judge_output_unsafe
from core.voice.google_live.interaction_controller import (
    GoogleLiveInteractionController,
    InteractionState,
)
from core.voice.session_provider.base import VoiceSessionProvider
from core.providers.tools.product_toolset import LESSON_CONVERSATION_TOOLS, product_tool_names
from core.voice.live_admission import AdmissionDecision, AdmissionReason, LiveAdmissionGate
from core.voice.session_orchestrator import SessionMode, normalize_session_mode
from plugins_func.register import Action
from core.lesson.log_context import with_lesson_log_context


LESSON_LIVE_TEXT_INSTRUCTION = (
    "Đọc nguyên văn câu sau bằng giọng Google Live đã cấu hình. "
    "Giữ đúng ngôn ngữ từng phần: tiếng Việt đọc tiếng Việt, từ hoặc cụm tiếng Anh "
    "đọc tiếng Anh. Không phản hồi ngữ cảnh trước, không nhắc lại câu trẻ vừa nói, "
    "không dịch, không thêm nội dung, không bỏ sót, không rút gọn: "
)
LESSON_CONVERSATION_SYSTEM_INSTRUCTION = (
    "\n\nDuring an active lesson, coach briefly and naturally. Never say that the child "
    "is wrong. Allow at most two contextual turns, then guide back to the English "
    "target. Bridge Vietnamese meaning to English. Use only supplied pronunciation "
    "outcomes. The lesson tools own progress; never claim mastery or choose the next step."
)
LIVE_WAKE_WORD_ALIASES = {
    "hi esp",
    "hai esp",
    "hey esp",
    "hi spy",
    "hai spy",
    "hey spy",
    "i spy",
    "high spy",
    "high speed",
    "hi speed",
    "hi tam",
    "hai tam",
    "hey tam",
}


@contextmanager
def _voice_activity_lease(conn, operation):
    helper = getattr(conn, "voice_activity_lease", None)
    if callable(helper):
        with helper(operation) as allowed:
            yield allowed
        return
    coordinator = getattr(conn, "activity_leases", None)
    if coordinator is None:
        yield True
        return
    lease = coordinator.try_acquire_voice(operation)
    if lease is None:
        yield False
        return
    try:
        yield True
    finally:
        lease.release()


class GoogleLiveProvider(VoiceSessionProvider):
    """Google Live session provider for production robot speech."""

    # Only hard-reopen Live after several silent timeouts (2 was too aggressive mid-chat).
    _SILENT_LIVE_REOPEN_TIMEOUTS = 5
    _SILENT_LIVE_REOPEN_COOLDOWN_SEC = 120.0
    # Live often re-emits "bắt đầu bài học" 2–5s later; 2s was too short and
    # restarted the sample mid-greeting (silent / cut introduction).
    _START_LESSON_DUPLICATE_TOOL_WINDOW_SEC = 12.0
    # Residual noise while waiting for model: 2.8s felt laggy; 1.6s still
    # covers first audio arrival without long deaf windows.
    _WAITING_MODEL_RETRY_AUDIO_GRACE_SEC = 1.6
    # Short: Live STT often re-emits the wake alias after firmware already handled Hi ESP.
    _WAKE_TRANSCRIPT_TAIL_SUPPRESS_SEC = 0.15
    _WAKE_GREETING_LIVE_INSTRUCTION = (
        "Bạn là robot TBOT nói với trẻ. Chỉ nói đúng một câu chào ngắn bằng tiếng Việt, "
        "ấm áp, không hỏi thêm, không gọi tool, không giải thích. Câu: "
    )

    @staticmethod
    def _as_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _conn_float(self, name, default=0.0):
        return self._as_float(getattr(self.conn, name, default), default)

    def _mark_lesson_asset_audio_activity(self):
        self.conn._lesson_asset_last_audio_at = time.monotonic()

    def __init__(
        self,
        conn,
        client_factory=None,
        classic_provider_factory=None,
    ):
        self.conn = conn
        self._client_factory = client_factory or GoogleLiveClientFactory.create
        self._classic_provider_factory = classic_provider_factory
        self._client = None
        self._bridge = None
        self._receive_task = None
        self._input_flush_task = None
        self._forced_interrupt_flush_task = None
        self._waiting_model_timeout_task = None
        self._user_audio_window_task = None
        self._lesson_child_transcript_timeout_task = None
        self._start_lesson_asr_fallback_task = None
        self._func_handler_bootstrap_task = None
        self._live_prewarm_task = None
        self._wake_greeting_task = None
        self._wake_greeting_sent_until = 0.0
        # While True until this timestamp: drop mic + skip interrupt so the first
        # spoken wake greeting is not cancelled by firmware listen:start / residual.
        self._wake_greeting_protect_until = 0.0
        # After model TTS ends, hold residual/echo so it does not open a fake user turn
        # (that left the robot deaf while WAITING_MODEL on garbage).
        self._post_reply_hold_until = 0.0
        self._fallback_provider = None
        self._fallback_activating = False
        self._reconnect_attempts = 0
        self._reconnecting = False
        self._closing = False
        self._lifecycle_lock = None
        self._lifecycle_generation = 0
        self._live_open_lock = None
        self._session_generation = 0
        self._response_generation = 0
        self._cancelled_response_ids = set()
        self._input_flush_generation = 0
        self._forced_interrupt_flush_generation = 0
        self._user_audio_window_generation = 0
        self._loud_input_duration_sec = 0.0
        self._last_loud_input_at = None
        self._pending_reconnect_audio = deque(maxlen=self._get_reconnect_buffer_capacity())
        self._pending_interrupt_audio = deque(
            maxlen=self._get_interrupt_replay_buffer_capacity()
        )
        self._start_lesson_asr_fallback_audio = deque(maxlen=96)
        self._start_lesson_asr_fallback_generation = 0
        self._start_lesson_asr_fallback_disabled = False
        self._last_expired_audio_window_drop_log_at = 0.0
        self._pending_interrupt_audio_response_id = None
        self._interrupt_capture_response_id = None
        self._interrupt_capture_reason = None
        self._interrupt_capture_started_at = None
        self._interrupt_capture_last_speech_at = None
        self._interrupt_capture_frames = 0
        self._interrupt_capture_bytes = 0
        self._interrupt_capture_peak_rms = 0
        # Per-turn idempotency flags scoped to each interrupt turn. They guard
        # _replay_pending_interrupt_audio against double-replay if
        # model_output_unblocked fires twice and prevent forced-flush from
        # re-finalising an already-finalised input stream. Both reset on a new
        # candidate (see _start_interrupt_capture_turn).
        self._interrupt_replayed_once = False
        self._interrupt_forwarded_once = False
        self._proactive_reconnect_task = None
        self._last_interrupt_at = 0.0
        # Per-reason throttle map for echo_suppressed log to prevent 16/sec
        # log spam during music playback (each mic frame is ~60ms).
        self._last_echo_suppressed_log_at = {}
        self._pending_tool_calls = set()
        self._cancelled_tool_call_ids = set()
        self._user_audio_allowed_until = 0.0
        self._wake_audio_window_until = 0.0
        self._wake_transcript_tail_suppress_until = 0.0
        # Lesson child-response window bookkeeping. _last_lesson_prompt_len sizes
        # the pre-listen guard delay; the _user_stream_* fields mark an in-flight
        # user audio stream so a WAITING_MODEL frame is forwarded (not dropped)
        # while the lesson listening window is open.
        self._last_lesson_prompt_len = 0
        self._last_lesson_prompt_text = ""
        self._lesson_prompt_resend_count = 0
        self._lesson_prompt_reopen_fast = False
        self._lesson_prompt_output_last_activity_at = None
        self._lesson_child_audio_pending_transcript = False
        self._lesson_child_speech_start_frames = []
        self._user_stream_response_id = None
        self._user_stream_started_at = None
        self._user_stream_last_speech_at = None
        self._user_stream_frames = 0
        # Conversation-turn finalization / waiting-model reopen bookkeeping.
        self._waiting_model_since = None
        self._consecutive_waiting_model_timeouts = 0
        self._last_silent_live_reopen_at = 0.0
        self._last_waiting_model_retry_prompt_at = 0.0
        self._echo_bypass_pending_interrupt = False
        self._last_clean_user_turn_response_id = None
        self._suppress_start_lesson_tool_call_until = 0.0
        self._skip_next_session_resumption_restore = False
        self._interaction = GoogleLiveInteractionController(conn)
        self._idle_close_task = None
        self._voice_consent_denied = False
        self._lesson_instruction_generation = None
        self._lesson_context_signature = None

    async def start_session(self):
        async with self._get_lifecycle_lock():
            self._lifecycle_generation += 1
            self._closing = False
            if self._client is not None and self._bridge is not None:
                self.conn.voice_provider = self
                return
            if self._has_session_orchestrator():
                self.conn.voice_provider = self
                self._voice_consent_denied = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live provider initialized dormant"
                )
                # Cold first-wake hang fix: open Live in the background right after
                # the robot websocket is up so "Hi ESP" does not wait ~1s on
                # Google connect before the first spoken turn.
                if self._prewarm_live_on_connect_enabled():
                    # delay 0: first Hi ESP after boot should not wait for cold connect.
                    self._schedule_live_prewarm(
                        "connect",
                        delay_sec=self._prewarm_live_on_connect_delay_sec(),
                    )
                    # Bootstrap tools in parallel so first Live turn is not blocked.
                    self._schedule_func_handler_bootstrap("connect_prewarm")
                return
            try:
                await self._ensure_func_handler()
                if await self._open_live_session() is False:
                    return False
                self.conn.voice_provider = self
                self._voice_consent_denied = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live provider initialized"
                )
            except Exception as exc:
                await self._close_live_resources()
                handled = await self._activate_classic_fallback(exc)
                if not handled and self._should_raise_without_fallback(exc):
                    raise

    async def _ensure_func_handler(self):
        """Bootstrap UnifiedToolHandler for live mode (classic path normally does this)."""
        if getattr(self.conn, "func_handler", None) is None:
            try:
                from core.providers.tools.unified_tool_handler import UnifiedToolHandler

                self.conn.func_handler = UnifiedToolHandler(self.conn)
                await self.conn.func_handler._initialize()
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live func_handler bootstrap failed: {}",
                    self._safe_error_message(exc),
                )
                self.conn.func_handler = None
                return

        # Augment the selected Intent's functions list so server-plugin
        # executor recognises live-only tools (e.g. change_volume).
        # Without this, Gemini sees the tool in connect config but
        # tool_manager returns NOTFOUND when the model calls it.
        self._inject_live_extra_functions_into_intent()
        self._log_tool_handler_inventory("ready")

    def _inject_live_extra_functions_into_intent(self):
        extras = self._extra_function_names_for_live()
        if not extras:
            return
        config = self.conn.config
        if not isinstance(config, Mapping):
            return
        intent_root = config.get("Intent")
        if not isinstance(intent_root, dict):
            return
        selected_module = config.get("selected_module") or {}
        if not isinstance(selected_module, dict):
            return
        selected_intent = selected_module.get("Intent")
        if not selected_intent:
            return
        intent_profile = intent_root.get(selected_intent)
        if not isinstance(intent_profile, dict):
            return
        current = intent_profile.get("functions") or []
        try:
            existing = list(current)
        except TypeError:
            existing = []
        existing_set = {str(item) for item in existing if item}
        merged = list(existing)
        added = []
        for name in extras:
            if name in existing_set:
                continue
            merged.append(name)
            existing_set.add(name)
            added.append(name)
        if not added:
            return
        intent_profile["functions"] = merged
        try:
            self.conn.func_handler.tool_manager.refresh_tools()
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live tool refresh after injection failed: {}",
                self._safe_error_message(exc),
            )
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live injected extra tools into Intent {} added=[{}]",
            selected_intent,
            ",".join(added),
        )

    def _log_tool_handler_inventory(self, reason):
        try:
            func_handler = getattr(self.conn, "func_handler", None)
            tools = func_handler.get_functions() if func_handler is not None else []
            names = []
            for tool in tools or []:
                if isinstance(tool, Mapping):
                    name = tool.get("function", {}).get("name")
                    if name:
                        names.append(str(name))
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live func_handler {} tool_count={} names=[{}]",
                reason,
                len(names),
                ",".join(names),
            )
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live func_handler inventory log failed: {}",
                self._safe_error_message(exc),
            )

    async def handle_text_message(self, message):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_SEND_TEXT
        ) as allowed:
            if not allowed:
                return False
            return await self._handle_text_message(message)

    async def _handle_text_message(self, message):
        if self._fallback_provider is not None:
            return await self._fallback_provider.handle_text_message(message)
        listen_state, listen_text = self._extract_listen_control(message)
        if listen_state == "start":
            self._touch_live_activity()
            if time.monotonic() >= self._wake_audio_window_until:
                await self._reset_conversation_live_context("listen_start")
            else:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live conversation_context_reset_skipped reason=listen_start wake_window_active=true"
                )
            await self._open_user_audio_window("listen_start")
            return True
        if listen_state == "stop":
            self._touch_live_activity()
            self._cancel_user_audio_window_task()
            self._wake_audio_window_until = 0.0
            self._wake_transcript_tail_suppress_until = 0.0
            await self._finalize_user_audio_input("listen_stop")
            return True
        text = self._extract_user_text_message(message)
        if text is None and listen_state == "detect":
            self._touch_live_activity()
            return True
        if text is None:
            return False
        try:
            self._touch_live_activity()
            if await self._dispatch_lesson_child_response(text):
                return True
            if self._is_local_stop_word(text):
                await self._handle_local_stop_word(text)
                return True
            if listen_state == "detect" and self._is_wake_word_only(text):
                await self._reset_conversation_live_context("wake_word")
                # Clear stale turn timer so first_audio latency is measured from this wake.
                self.conn.google_live_turn_started_at = None
                await self._send_wake_listening_feedback(listen_text or text)
                await self._open_user_audio_window("wake_word")
                # Open Live immediately + speak a short first line so the first
                # post-connect Hi ESP is not silent for several seconds.
                self._schedule_live_prewarm("wake_word", delay_sec=0.0)
                self._schedule_wake_greeting("wake_detect")
                return True
            if await self._dispatch_lesson_start_intent(text):
                return True
            if await self._dispatch_music_control_intent(text):
                return True
            if self._has_active_output():
                await self._begin_user_interrupt("text_input")
            self.conn.client_abort = False
            if self._client is None or not hasattr(self._client, "send_text"):
                if not await self._ensure_live_open_for_lesson_text():
                    self.conn.logger.bind(tag="GoogleLive").warning(
                        "Google Live text input consumed without live client"
                    )
                    return True
            try:
                await self._client.send_text(text)
                self._mark_complete_text_user_turn("text_input")
                return True
            except Exception as exc:
                await self._handle_runtime_failure(exc)
                return True
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True

    async def handle_audio_bytes(self, audio_bytes):
        if self._fallback_provider is not None:
            return await self._fallback_provider.handle_audio_bytes(audio_bytes)
        if audio_bytes:
            self._touch_live_activity()
        if self._reconnecting:
            if audio_bytes:
                self._pending_reconnect_audio.append(
                    (self._response_generation, audio_bytes)
                )
            return True

        opened_live_for_audio = False
        if self._has_session_orchestrator() and self._bridge is None:
            # Prefer awaiting an in-flight wake/connect prewarm so the first
            # spoken words after "Hi ESP" do not start a second connect race.
            if not await self._await_live_prewarm_if_running():
                if not await self._ensure_live_open_for_audio():
                    return True
            if self._bridge is None:
                if not await self._ensure_live_open_for_audio():
                    return True
            opened_live_for_audio = self._bridge is not None
        if (
            opened_live_for_audio
            and time.monotonic() >= self._user_audio_allowed_until
        ):
            await self._open_user_audio_window("inbound_audio")
        if self._bridge is None:
            return True
        if await self._forward_lesson_child_audio(audio_bytes):
            return True
        if self._should_hold_lesson_prompt_pending_audio():
            self._cancel_input_flush_task()
            self._cancel_waiting_model_timeout_task()
            self._waiting_model_since = None
            if self._interaction.state == InteractionState.WAITING_MODEL:
                self._interaction.transition(InteractionState.LISTENING)
            self._clear_user_stream()
            self.conn.client_abort = False
            return True
        self._cancel_user_audio_window_task()
        try:
            decoded_audio = None
            if hasattr(self._bridge, "decode_input_audio_async"):
                decoded_audio = await self._bridge.decode_input_audio_async(audio_bytes)
            elif hasattr(self._bridge, "decode_input_audio"):
                decoded_audio = self._bridge.decode_input_audio(audio_bytes)
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True
        if time.monotonic() < self._wake_transcript_tail_suppress_until:
            self._log_audio_decision(
                "drop_input",
                "wake_transcript_tail",
                decoded_audio,
            )
            self.conn.client_abort = False
            return True
        # Protect first spoken wake greeting: firmware often opens listen:start and
        # streams residual frames that interrupt the greeting before it plays.
        # After a short hard-protect, allow strong deliberate speech so "bắt đầu
        # bài học" right after Hi ESP is not swallowed for 2–3s.
        if self._is_wake_greeting_protected():
            protect_remaining = float(self._wake_greeting_protect_until or 0.0) - time.monotonic()
            hard_protect = protect_remaining > max(0.0, self._wake_greeting_protect_sec() - 0.55)
            if hard_protect or not self._is_strong_user_speech(decoded_audio, multiplier=2.2):
                self._log_audio_decision(
                    "drop_input",
                    "wake_greeting_protect",
                    decoded_audio,
                )
                self.conn.client_abort = False
                return True
            self._wake_greeting_protect_until = 0.0
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live wake_greeting_protect_released reason=strong_user_speech"
            )
        # Right after robot TTS: residual/echo must not open a fake user turn.
        if self._is_post_reply_hold_active() and not self._is_strong_user_speech(
            decoded_audio, multiplier=2.2, floor=1800
        ):
            self._log_audio_decision(
                "drop_input",
                "post_reply_hold",
                decoded_audio,
            )
            self.conn.client_abort = False
            return True
        # Conversation-side WAITING_MODEL handling. While waiting for the model to
        # respond, mic frames are normally dropped (the model is "speaking"). But
        # if the model returns nothing within waiting_model_timeout_sec, reopen the
        # mic so the child can retry, and (after a longer grace) nudge them once.
        if self._interaction.state == InteractionState.WAITING_MODEL:
            if self._waiting_model_release_due():
                await self._maybe_queue_waiting_model_retry_prompt()
                self._cancel_waiting_model_timeout_task()
                self._interaction.transition(InteractionState.USER_STREAMING)
                self._waiting_model_since = None
            elif self._waiting_model_retry_audio_can_resume(decoded_audio):
                self._cancel_waiting_model_timeout_task()
                self._interaction.transition(InteractionState.USER_STREAMING)
                self._waiting_model_since = None
                self._consecutive_waiting_model_timeouts = 0
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live waiting_model_retry_audio_resumed"
                )
            else:
                self.conn.client_abort = False
                return True
        try:
            if self._should_drop_expired_conversation_audio_window(decoded_audio):
                self._user_audio_allowed_until = 0.0
                if self._interaction.state == InteractionState.LISTENING:
                    self._interaction.transition(InteractionState.IDLE)
                self.conn.client_abort = False
                self._log_expired_audio_window_drop()
                return True
            # Drop mic frames during the post-audio_start window so Live
            # VAD cannot fire on the model's own speaker echo before AEC
            # has converged. Real user barge-in resumes once the window
            # expires (config: mute_input_after_audio_start_sec).
            if self._should_drop_input_post_audio_start():
                self.conn.client_abort = False
                return True
            should_suppress_echo = self._should_suppress_robot_output_echo(decoded_audio)
            buffered_current_frame = False
            if self._echo_bypass_pending_interrupt:
                self._echo_bypass_pending_interrupt = False
                await self._begin_user_interrupt("loud_input")
                self._buffer_pending_interrupt_audio(decoded_audio)
                self.conn.client_abort = False
                return True
            elif should_suppress_echo:
                self._log_audio_decision(
                    "suppress_echo",
                    self._current_audio_suppression_reason(),
                    decoded_audio,
                )
                self.conn.client_abort = False
                return True
            if self._should_hold_interrupt_audio(decoded_audio):
                self._log_audio_decision("hold_interrupt_audio", "blocked_output", decoded_audio)
                self.conn.client_abort = False
                return True
            aec_live_vad_only = (
                decoded_audio is not None
                and self._has_active_output()
                and self._can_forward_aec_audio_for_live_vad(self._get_live_config())
            )
            interrupted = False
            if self._is_wake_greeting_protected():
                self._log_audio_decision(
                    "drop_input", "wake_greeting_protect", decoded_audio
                )
                self.conn.client_abort = False
                return True
            if self._should_interrupt_for_input(decoded_audio):
                await self._begin_user_interrupt("audio_input")
                interrupted = True
                if not (
                    self._client is not None
                    and getattr(self._client, "connected", False)
                ):
                    self.conn.client_abort = False
                    return True
            elif self._should_drop_input_during_output():
                self._log_audio_decision("drop_input", "output_active", decoded_audio)
                self.conn.client_abort = False
                return True
            if self._should_drop_conversation_start_noise(decoded_audio):
                self._log_audio_decision("drop_input", "below_speech_threshold", decoded_audio)
                self.conn.client_abort = False
                return True
            self.conn.client_abort = False
            if decoded_audio is not None and hasattr(
                self._bridge, "forward_decoded_input_audio"
            ):
                await self._bridge.forward_decoded_input_audio(decoded_audio)
            else:
                await self._bridge.forward_input_audio(audio_bytes)
            self._mark_lesson_asset_audio_activity()
            self._log_audio_decision("forward_input", "accepted", decoded_audio)
            if aec_live_vad_only and not interrupted:
                return True
            if self._interrupt_capture_response_id == self._response_generation:
                self._interrupt_forwarded_once = True
            if not buffered_current_frame:
                self._record_interrupt_capture_audio(decoded_audio)
            if not buffered_current_frame:
                self._buffer_pending_interrupt_audio_while_blocked(decoded_audio)
            self._record_start_lesson_asr_fallback_audio(audio_bytes, decoded_audio)
            self._mark_clean_user_turn_opened("audio_input")
            self._record_user_stream_audio(decoded_audio)
            if self._user_turn_can_finalize():
                await self._finalize_user_turn_clean()
            else:
                self._schedule_input_flush()
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True
        return True

    async def _forward_lesson_child_audio(self, audio_bytes):
        """During an open lesson child-response window the child's mic frame must
        be forwarded even while the interaction sits in WAITING_MODEL from a
        just-finished prompt turn (the normal output-active drop would silence the
        child). Returns True when the frame was handled here."""
        if not (
            self._lesson_child_response_window_active()
            or self._lesson_child_response_window_active(
                require_audio_window=False,
                require_explicit_runtime_window=True,
            )
        ):
            return False
        semantic_tool_path = self._lesson_conversation_tool_path_active()
        if semantic_tool_path:
            self._lesson_child_audio_pending_transcript = False
            self._cancel_lesson_child_transcript_timeout_task()
        elif self._lesson_child_audio_pending_transcript:
            self.conn.client_abort = False
            return True
        self._force_lesson_session_mode("lesson_child_audio")
        bridge = self._bridge
        if bridge is None or not hasattr(bridge, "forward_decoded_input_audio"):
            return False
        if self._has_active_output():
            self.conn.client_abort = False
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_child_audio_deferred reason=robot_speaking",
                    self.conn,
                )
            )
            return True
        try:
            decoded = None
            decode_async = getattr(bridge, "decode_input_audio_async", None)
            if callable(decode_async):
                decoded = await decode_async(audio_bytes)
            elif hasattr(bridge, "decode_input_audio"):
                decoded = bridge.decode_input_audio(audio_bytes)
            if decoded is None:
                return False
            rms = self._input_rms(decoded)
            frames_to_forward = [decoded]
            if self._user_stream_started_at is None:
                frames_to_forward = self._lesson_child_speech_start_frames_to_forward(
                    decoded,
                    rms,
                )
                if frames_to_forward is None:
                    self.conn.client_abort = False
                    return True
            output_blocked = False
            blocked_check = getattr(bridge, "is_model_output_blocked", None)
            if callable(blocked_check):
                try:
                    output_blocked = bool(blocked_check())
                except Exception:
                    output_blocked = False
            if output_blocked:
                self.conn.client_abort = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_child_audio_deferred reason=output_blocked",
                        self.conn,
                    )
                )
                return True
            for frame in frames_to_forward:
                await bridge.forward_decoded_input_audio(frame)
                self._mark_lesson_asset_audio_activity()
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_child_audio_forwarded bytes={} rms={}",
                        self.conn,
                    ),
                    len(frame),
                    rms if frame is decoded and rms is not None else "n/a",
                )
            # The child's continued audio closes the WAITING_MODEL turn so the
            # next lesson step's audio is not dropped.
            if self._interaction.state == InteractionState.WAITING_MODEL:
                self._interaction.transition(InteractionState.USER_STREAMING)
            for frame in frames_to_forward:
                self._record_user_stream_audio(frame)
            if self._user_turn_can_finalize():
                await self._finalize_user_turn_clean()
                if not semantic_tool_path:
                    self._lesson_child_audio_pending_transcript = True
            else:
                self._schedule_input_flush()
            self.conn.client_abort = False
            return True
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True

    def discard_refused_voice_input(self):
        """Drop local input state without finalizing or replaying a refused stream."""
        self._cancel_user_audio_window_task()
        self._user_audio_allowed_until = 0.0
        self._wake_audio_window_until = 0.0
        self._wake_transcript_tail_suppress_until = 0.0
        self._clear_user_stream()
        self._pending_reconnect_audio.clear()
        self._pending_interrupt_audio.clear()
        self._start_lesson_asr_fallback_audio.clear()

    async def interrupt(self):
        if self._fallback_provider is not None:
            await self._fallback_provider.interrupt()
            return
        await self._begin_user_interrupt("explicit_interrupt")

    async def close(self):
        self._closing = True
        self._lifecycle_generation += 1
        async with self._get_lifecycle_lock():
            await self._close_live_resources()
            if self._fallback_provider is not None:
                await self._fallback_provider.close()

    async def prepare_for_sample_lesson(self):
        if self._fallback_provider is not None:
            return False
        try:
            await self._ensure_func_handler()
            self._cancel_start_lesson_asr_fallback_task()
            self._start_lesson_asr_fallback_audio.clear()
            self._cancel_waiting_model_timeout_task()
            self._waiting_model_since = None
            self._last_waiting_model_retry_prompt_at = 0.0
            self._lesson_child_audio_pending_transcript = False
            self._clear_lesson_child_speech_start_frames()
            self._cancel_lesson_child_transcript_timeout_task()
            # Mic is re-opened by child-response windows / lesson_start intent.
            # Keep 0 here so prepare stays deterministic for unit tests.
            self._user_audio_allowed_until = 0.0
            self._wake_audio_window_until = 0.0
            self._wake_transcript_tail_suppress_until = 0.0
            self._wake_greeting_protect_until = 0.0
            self._clear_user_stream()
            self._pending_reconnect_audio.clear()
            self._pending_interrupt_audio.clear()
            self.conn.client_abort = False
            self.conn.google_live_turn_started_at = None
            self.conn.google_live_audio_out_started_at = None
            self._suppress_start_lesson_tool_call_until = (
                time.monotonic() + self._START_LESSON_DUPLICATE_TOOL_WINDOW_SEC
            )
            if self._interaction.state in (
                InteractionState.WAITING_MODEL,
                InteractionState.INTERRUPTING,
                InteractionState.USER_STREAMING,
            ):
                self._interaction.transition(InteractionState.LISTENING)
            if self._bridge is not None and hasattr(self._bridge, "force_allow_model_output"):
                self._bridge.force_allow_model_output()
            client_connected = self._client is not None and (
                not hasattr(self._client, "connected") or self._client.connected
            )
            receive_task_healthy = self._receive_task is None or not self._receive_task.done()
            if client_connected and receive_task_healthy:
                self.conn.voice_provider = self
                self._voice_consent_denied = False
                return True
            async with self._get_live_open_lock():
                await self._close_live_resources()
                self.conn.google_live_session_resumption_handle = None
                await self._open_live_session_locked(restore_session_resumption=False)
            self.conn.voice_provider = self
            self._voice_consent_denied = False
            return True
        except Exception as exc:
            await self._close_live_resources()
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live sample_lesson_prepare failed: {}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )
            return False

    def _has_session_orchestrator(self):
        return hasattr(self.conn, "session_mode")

    def _is_live_client_ready(self):
        return (
            self._client is not None
            and self._bridge is not None
            and (
                not hasattr(self._client, "connected")
                or bool(getattr(self._client, "connected", False))
            )
        )

    async def ensure_live_ready(self, reason="ensure"):
        """Public hook for connection orchestrator to open Live without dormancy."""
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live ensure_live_ready reason={}",
            reason,
        )
        return await self._ensure_live_open_for_audio()

    async def _ensure_live_open_for_audio(self, *, preserve_live_prewarm=False):
        if normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) == SessionMode.LESSON:
            # LESSON: only interactive steps need Live; passive steps stay TTS-only.
            if not self._active_lesson_step_is_interactive():
                return False
        if self._is_live_client_ready():
            return True
        timeout = self._get_live_open_timeout_sec()
        try:
            if timeout is None:
                return await self._open_live_for_audio(
                    preserve_live_prewarm=preserve_live_prewarm
                )
            return await asyncio.wait_for(
                self._open_live_for_audio(
                    preserve_live_prewarm=preserve_live_prewarm
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._handle_live_open_timeout(timeout)
            return False

    def _prewarm_live_on_connect_enabled(self):
        return bool(self._get_live_config().get("prewarm_live_on_connect", True))

    def _prewarm_live_on_connect_delay_sec(self):
        delay = self._as_float(
            self._get_live_config().get("prewarm_live_on_connect_delay_sec", 0.0),
            0.0,
        )
        return max(0.0, min(delay, 5.0))

    def _wake_greeting_enabled(self):
        return bool(self._get_live_config().get("wake_greeting_enabled", True))

    def _wake_greeting_text(self):
        text = self._get_live_config().get("wake_greeting_text")
        if text is None or str(text).strip() == "":
            return "Dạ, mình nghe đây ạ."
        return str(text).strip()

    def _wake_greeting_protect_sec(self):
        """How long mic must not interrupt the spoken wake greeting."""
        try:
            value = float(
                self._get_live_config().get("wake_greeting_protect_sec", 1.2)
            )
        except (TypeError, ValueError):
            value = 1.2
        return max(0.4, min(value, 3.0))

    def _is_wake_greeting_protected(self):
        return time.monotonic() < float(self._wake_greeting_protect_until or 0.0)

    def _post_reply_hold_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("post_reply_hold_sec", 0.55))
        except (TypeError, ValueError):
            value = 0.55
        return max(0.2, min(value, 1.5))

    def _is_post_reply_hold_active(self):
        return time.monotonic() < float(self._post_reply_hold_until or 0.0)

    def _arm_post_reply_hold(self, reason="audio_end"):
        hold = self._post_reply_hold_sec()
        now = time.monotonic()
        self._post_reply_hold_until = now + hold
        # Also seed echo gate so suppress_robot_output_echo covers residual.
        echo_ms = 500.0
        try:
            echo_ms = float(
                self._get_live_config().get("echo_tail_suppression_ms", 500)
            )
        except (TypeError, ValueError):
            echo_ms = 500.0
        until = now + max(hold, max(0.3, echo_ms / 1000.0))
        current = float(getattr(self.conn, "google_live_echo_suppress_until", 0.0) or 0.0)
        if until > current:
            self.conn.google_live_echo_suppress_until = until
            self.conn.google_live_echo_suppress_started_at = now
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live post_reply_hold_armed reason={} hold_ms={:.0f}",
            reason,
            hold * 1000.0,
        )

    def _input_rms_value(self, decoded_audio):
        if decoded_audio is None or self._bridge is None:
            return None
        if not hasattr(self._bridge, "input_rms"):
            return None
        try:
            rms = self._bridge.input_rms(decoded_audio)
        except Exception:
            return None
        return rms if isinstance(rms, (int, float)) else None

    def _is_strong_user_speech(self, decoded_audio, *, multiplier=1.8, floor=1500):
        """True for deliberate speech (not residual speaker energy)."""
        rms = self._input_rms_value(decoded_audio)
        if rms is None:
            return False
        threshold = self._get_user_speech_rms_threshold() or 650
        return rms >= max(int(floor), int(threshold * multiplier))

    async def _send_wake_greeting(self, reason):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_WAKE_GREETING
        ) as allowed:
            if not allowed:
                return False
            return await self._send_wake_greeting_with_lease(reason)

    async def _send_wake_greeting_with_lease(self, reason):
        # Let firmware listen:start / tts:stop settle before speaking.
        await asyncio.sleep(0.25)
        await self._await_live_prewarm_if_running()
        if not self._is_live_client_ready():
            if not await self._ensure_live_open_for_audio():
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live wake_greeting_skipped reason=live_not_open trigger={}",
                    reason,
                )
                return False
        if time.monotonic() < float(self._wake_greeting_sent_until or 0.0):
            return False
        client = self._client
        if client is None or not hasattr(client, "send_text"):
            return False
        greeting = self._wake_greeting_text()
        try:
            if self._bridge is not None and hasattr(self._bridge, "allow_model_output"):
                self._bridge.allow_model_output()
            self.conn.client_abort = False
            protect_sec = self._wake_greeting_protect_sec()
            # Arm protect BEFORE send so concurrent mic frames cannot interrupt.
            now = time.monotonic()
            self._wake_greeting_protect_until = now + protect_sec
            self._wake_greeting_sent_until = now + max(4.0, protect_sec + 1.0)
            self.conn.google_live_turn_started_at = now
            await client.send_text(f"{self._WAKE_GREETING_LIVE_INSTRUCTION}{greeting}")
            self._touch_live_activity()
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live wake_greeting_sent reason={} chars={} protect_ms={:.0f}",
                reason,
                len(greeting),
                protect_sec * 1000,
            )
            return True
        except Exception as exc:
            self._wake_greeting_protect_until = 0.0
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live wake_greeting_failed reason={} error={}",
                reason,
                self._safe_error_message(exc),
            )
            return False

    def _schedule_wake_greeting(self, reason="wake"):
        """One-shot Live text greeting after Hi ESP so cold start is not silent."""
        if not self._wake_greeting_enabled() or self._closing:
            return
        if time.monotonic() < float(self._wake_greeting_sent_until or 0.0):
            return
        task = self._wake_greeting_task
        if task is not None and not task.done():
            return
        self._wake_greeting_task = asyncio.get_running_loop().create_task(
            self._send_wake_greeting(reason)
        )
        self._wake_greeting_task.add_done_callback(
            lambda done: self._log_background_task_failure(
                done, "wake_greeting", reason
            )
        )

    def _prewarm_live_on_wake_enabled(self):
        return bool(self._get_live_config().get("prewarm_live_on_wake", True))

    def _get_wake_transcript_tail_suppress_sec(self):
        value = self._as_float(
            self._get_live_config().get(
                "wake_transcript_tail_suppress_sec",
                self._WAKE_TRANSCRIPT_TAIL_SUPPRESS_SEC,
            ),
            self._WAKE_TRANSCRIPT_TAIL_SUPPRESS_SEC,
        )
        return max(0.0, min(value, 2.0))

    def _default_idle_timeout_sec(self):
        # Long multi-turn chats: keep Live hot through pauses (15 min).
        return 900.0 if self._prewarm_live_on_connect_enabled() else 120.0

    def _idle_timeout_sec(self):
        config = getattr(self.conn, "config", {}) or {}
        live_admission = (
            config.get("live_admission", {}) if isinstance(config, Mapping) else {}
        )
        timeout = (
            live_admission.get("idle_timeout_sec")
            if isinstance(live_admission, Mapping)
            else None
        )
        if timeout is None:
            google_live = (
                config.get("google_live", {}) if isinstance(config, Mapping) else {}
            )
            default_idle = self._default_idle_timeout_sec()
            timeout = (
                google_live.get("idle_timeout_sec", default_idle)
                if isinstance(google_live, Mapping)
                else default_idle
            )
        return max(0.0, self._as_float(timeout, self._default_idle_timeout_sec()))

    def _log_background_task_failure(self, done, task_name, reason):
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live {} task failed reason={} error={}",
                task_name,
                reason,
                self._safe_error_message(exc),
            )

    def _schedule_live_prewarm(self, reason, delay_sec=0.0):
        """Open Live in the background so first wake avoids cold-connect latency."""
        if reason == "wake_word" and not self._prewarm_live_on_wake_enabled():
            return
        if reason == "connect" and not self._prewarm_live_on_connect_enabled():
            return
        if self._closing or self._is_live_client_ready():
            return
        task = self._live_prewarm_task
        if task is not None and not task.done():
            return
        delay = max(0.0, self._as_float(delay_sec, 0.0))

        async def _run():
            with _voice_activity_lease(
                self.conn, ActivityOperation.GOOGLE_PREWARM
            ) as allowed:
                if not allowed:
                    return False
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._closing:
                    return False
                if (
                    normalize_session_mode(
                        getattr(self.conn, "session_mode", SessionMode.DORMANT)
                    )
                    == SessionMode.LESSON
                    and not self._active_lesson_step_is_interactive()
                ):
                    return False
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live live_prewarm_start reason={}",
                    reason,
                )
                try:
                    ok = await self._ensure_live_open_for_audio(
                        preserve_live_prewarm=True
                    )
                except Exception as exc:
                    self.conn.logger.bind(tag="GoogleLive").warning(
                        "Google Live live_prewarm_failed reason={} error={}",
                        reason,
                        self._safe_error_message(exc),
                    )
                    return False
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live live_prewarm_done reason={} ok={}",
                    reason,
                    bool(ok),
                )
                return bool(ok)

        self._live_prewarm_task = asyncio.get_running_loop().create_task(_run())
        self._live_prewarm_task.add_done_callback(
            lambda done: self._log_background_task_failure(
                done, "live_prewarm", reason
            )
        )

    async def _await_live_prewarm_if_running(self):
        """Return True when Live is ready after any in-flight prewarm finishes."""
        task = self._live_prewarm_task
        if task is None:
            return self._is_live_client_ready()
        try:
            ok = bool(task.result()) if task.done() else bool(await task)
        except Exception:
            ok = False
        return ok and self._bridge is not None

    async def _open_live_for_audio(self, *, preserve_live_prewarm=False):
        if self._lesson_runtime_active() or normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON:
            return await self._open_live_for_audio_with_lease(
                preserve_live_prewarm=preserve_live_prewarm
            )
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_OPEN
        ) as allowed:
            if not allowed:
                return False
            return await self._open_live_for_audio_with_lease(
                preserve_live_prewarm=preserve_live_prewarm
            )

    async def _open_live_for_audio_with_lease(
        self, *, preserve_live_prewarm=False
    ):
        decision = await self._admit_live_open()
        if decision.decision == AdmissionDecision.FRIENDLY_BREAK:
            await self._send_live_unavailable(decision.reason)
            return False
        if decision.decision == AdmissionDecision.DEGRADE_TTS_ONLY:
            await self._activate_budget_degrade(
                decision.reason,
                preserve_live_prewarm=preserve_live_prewarm,
            )
            return False
        try:
            if await self._open_live_session() is False:
                return False
            self._schedule_func_handler_bootstrap("audio_live_open")
            await asyncio.sleep(0)
            self.conn.voice_provider = self
            self._voice_consent_denied = False
            return True
        except Exception as exc:
            if self._lesson_conversation_tool_path_active():
                if await self._handle_lesson_live_interruption("transport"):
                    return False
            await self._close_live_resources(
                preserve_live_prewarm=preserve_live_prewarm
            )
            handled = await self._activate_classic_fallback(exc)
            if not handled and self._should_raise_without_fallback(exc):
                raise
            return False

    async def _handle_live_open_timeout(self, timeout):
        try:
            await self._close_live_resources()
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live live_open_timeout cleanup failed: {}",
                self._safe_error_message(exc),
            )
        self.conn.client_abort = False
        if (
            self._has_session_orchestrator()
            and normalize_session_mode(
                getattr(self.conn, "session_mode", SessionMode.DORMANT)
            ) != SessionMode.LESSON
        ):
            enter_dormant = getattr(self.conn, "enter_dormant_mode", None)
            if callable(enter_dormant):
                await enter_dormant(reason="live_open_timeout")
        self.conn.logger.bind(tag="GoogleLive").warning(
            "Google Live live_open_timeout timeout_sec={}",
            timeout,
        )

    def _get_live_open_timeout_sec(self):
        config = self._get_live_config()
        value = config.get("live_open_timeout_sec") if isinstance(config, Mapping) else None
        if value is None and isinstance(config, Mapping):
            connect_timeout = config.get("connect_timeout_sec")
            try:
                value = float(connect_timeout) + 2.0
            except (TypeError, ValueError):
                value = 12.0
        if value is None:
            value = 12.0
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return 12.0
        return timeout if timeout > 0 else None

    def _schedule_func_handler_bootstrap(self, reason):
        if getattr(self.conn, "func_handler", None) is not None:
            return
        task = self._func_handler_bootstrap_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._func_handler_bootstrap_task = loop.create_task(
            self._ensure_func_handler()
        )

        def _log_bootstrap_failure(done):
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live func_handler background bootstrap failed reason={} error={}",
                    reason,
                    self._safe_error_message(exc),
                )

        self._func_handler_bootstrap_task.add_done_callback(_log_bootstrap_failure)

    async def _admit_live_open(self):
        gate = getattr(self.conn, "live_admission_gate", None)
        if gate is None:
            gate = LiveAdmissionGate.from_config(getattr(self.conn, "config", {}) or {})
            self.conn.live_admission_gate = gate
        admit = getattr(gate, "admit_async", None)
        if callable(admit):
            return await admit(
                getattr(self.conn, "device_id", None),
                getattr(self.conn, "household_id", None),
            )
        return gate.admit(
            getattr(self.conn, "device_id", None),
            getattr(self.conn, "household_id", None),
        )

    async def _activate_budget_degrade(
        self,
        reason: AdmissionReason,
        *,
        preserve_live_prewarm=False,
    ):
        await self._close_live_resources(
            preserve_live_prewarm=preserve_live_prewarm
        )
        if self._has_session_orchestrator():
            self.conn._set_session_mode(SessionMode.CONVERSATION, reason=reason.value)
        await self._activate_classic_fallback(RuntimeError(reason.value))

    async def _send_live_unavailable(self, reason: AdmissionReason):
        payload = {
            "type": "alert",
            "status": "live_unavailable",
            "reason": reason.value,
            "session_id": getattr(self.conn, "session_id", None),
            "message": "Let's take a short break and try again soon.",
            "emotion": "neutral",
        }
        sent = getattr(self.conn, "sent", None)
        if isinstance(sent, list):
            sent.append(payload)
            return
        websocket = getattr(self.conn, "websocket", None)
        if websocket is None:
            return
        try:
            await websocket.send(json.dumps(payload))
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live unavailable prompt send failed: {}",
                self._safe_error_message(exc),
            )

    # Error classes the child should hear an explanation for. Benign normal closes
    # ("stream_closed"/"unknown") stay silent so a clean turn-end is never narrated.
    _FALLBACK_NOTICE_ERROR_CLASSES = frozenset(
        {"quota", "auth", "invalid_config", "network"}
    )
    _FALLBACK_NOTICE_MESSAGE = "Robot cần nghỉ một chút xíu thôi, con thử lại nhé!"

    async def _send_fallback_notice(self, exc):
        """Best-effort child-facing alert for the legacy fallback path.

        Production Google Live configs disable this path so AI speech does not
        switch voices. Never leak the raw exception, which can carry an API key.
        """
        try:
            error_class = self._classify_error(exc)
            if error_class not in self._FALLBACK_NOTICE_ERROR_CLASSES:
                return
            payload = {
                "type": "alert",
                "status": "live_unavailable",
                "reason": error_class,
                "session_id": getattr(self.conn, "session_id", None),
                "message": self._FALLBACK_NOTICE_MESSAGE,
                "emotion": "neutral",
            }
            sent = getattr(self.conn, "sent", None)
            if isinstance(sent, list):
                sent.append(payload)
                return
            websocket = getattr(self.conn, "websocket", None)
            if websocket is None:
                return
            await websocket.send(json.dumps(payload))
        except Exception as notice_exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live fallback notice send failed: {}",
                self._safe_error_message(notice_exc),
            )

    def _touch_live_activity(self):
        if self._has_session_orchestrator():
            self.conn.last_live_activity_at = time.monotonic()
        if hasattr(self.conn, "last_activity_time"):
            self.conn.last_activity_time = time.time() * 1000

    async def _record_live_session_usage(self):
        started_at = getattr(self.conn, "google_live_session_started_at", None)
        if started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - float(started_at))
        self.conn.google_live_session_started_at = None
        gate = getattr(self.conn, "live_admission_gate", None)
        if gate is not None:
            record_usage = getattr(gate, "record_live_usage_async", None)
            if callable(record_usage):
                await record_usage(
                    getattr(self.conn, "device_id", None),
                    getattr(self.conn, "household_id", None),
                    elapsed,
                )
            else:
                gate.record_live_usage(
                    getattr(self.conn, "device_id", None),
                    getattr(self.conn, "household_id", None),
                    elapsed,
                )
        store = getattr(self.conn, "live_resumption_store", None)
        save = getattr(store, "save", None)
        handle = getattr(self.conn, "google_live_session_resumption_handle", None)
        if self._is_session_resumption_enabled() and callable(save) and handle:
            await save(getattr(self.conn, "device_id", None), handle)

    def _is_session_resumption_enabled(self):
        return bool(self._get_live_config().get("session_resumption_enabled", True))

    def _schedule_idle_close_task(self):
        if not self._has_session_orchestrator() or self._idle_close_task is not None:
            return
        timeout = self._idle_timeout_sec()
        if timeout <= 0:
            return
        self._idle_close_task = asyncio.create_task(self._idle_close_loop(timeout))

    async def _idle_close_loop(self, timeout):
        try:
            while self._client is not None and not self._closing:
                await asyncio.sleep(timeout)
                if await self._close_if_idle_once(timeout):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live idle close loop failed: {}",
                self._safe_error_message(exc),
            )

    async def _close_if_idle_once(self, timeout):
        if self._client is None:
            return True
        if self._lesson_runtime_active():
            return False
        last_activity = getattr(self.conn, "last_live_activity_at", None)
        if last_activity is None:
            last_activity = getattr(self.conn, "google_live_session_started_at", None)
        if last_activity is None:
            return False
        if time.monotonic() - float(last_activity) < timeout:
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live idle_timeout closing_live timeout_sec={}",
            timeout,
        )
        await self._close_live_resources()
        if self._has_session_orchestrator():
            await self.conn.enter_dormant_mode(reason="idle_timeout")
        return True

    def _ensure_required_aec_ready(self):
        config = self._get_live_config()
        if not bool(config.get("aec_enabled", False)):
            return
        processor = getattr(self._bridge, "_aec_processor", None)
        client_config = getattr(self._client, "config", None)
        if (
            processor is None
            and self._client_factory is not GoogleLiveClientFactory.create
            and (
                not isinstance(client_config, Mapping)
                or "aec_enabled" not in client_config
            )
        ):
            return
        if processor is None or getattr(processor, "bypassed", False):
            reason = getattr(processor, "reason", None) or "processor_unavailable"
            raise RuntimeError(f"AEC required but bypassed ({reason})")

    async def _restore_session_resumption_handle(self):
        if not self._is_session_resumption_enabled():
            self.conn.google_live_session_resumption_handle = None
            return False
        if getattr(self.conn, "google_live_session_resumption_handle", None):
            return False
        store = getattr(self.conn, "live_resumption_store", None)
        load = getattr(store, "load", None)
        device_id = getattr(self.conn, "device_id", None)
        if not callable(load) or not device_id:
            return False
        handle = await load(device_id)
        if not handle:
            return False
        self.conn.google_live_session_resumption_handle = str(handle)
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live session_resumption_handle_restored has_handle=True"
        )
        return True

    async def _persist_session_resumption_handle(self, handle):
        if not self._is_session_resumption_enabled():
            return
        store = getattr(self.conn, "live_resumption_store", None)
        save = getattr(store, "save", None)
        device_id = getattr(self.conn, "device_id", None)
        if callable(save) and device_id and handle:
            await save(device_id, handle)

    def _schedule_session_resumption_persist(self, handle):
        if not self._is_session_resumption_enabled():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._persist_session_resumption_handle(handle))

        def _log_persist_failure(done):
            try:
                done.result()
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live session_resumption_handle_persist_failed: {}",
                    self._safe_error_message(exc),
                )

        task.add_done_callback(_log_persist_failure)

    async def _record_reconnect_attempt(self):
        gate = getattr(self.conn, "live_admission_gate", None)
        record_reconnect = getattr(gate, "record_reconnect_async", None)
        if callable(record_reconnect):
            try:
                await record_reconnect(getattr(self.conn, "device_id", None))
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live reconnect accounting failed: {}",
                    self._safe_error_message(exc),
                )
            return
        record_reconnect = getattr(gate, "record_reconnect", None)
        if not callable(record_reconnect):
            return
        try:
            record_reconnect(getattr(self.conn, "device_id", None))
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live reconnect accounting failed: {}",
                self._safe_error_message(exc),
            )

    def _suppress_user_transcript_as_model_echo(self, transcript_text):
        bridge = self._bridge
        if bridge is None or not hasattr(bridge, "looks_like_model_echo"):
            return False
        try:
            if not bridge.looks_like_model_echo(transcript_text):
                return False
        except Exception:
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_transcript_suppressed_as_model_echo chars={}",
            len(transcript_text or ""),
        )
        return True

    async def _on_user_transcript(self, transcript_text):
        self._cancel_start_lesson_asr_fallback_task()
        self._start_lesson_asr_fallback_audio.clear()
        if self._lesson_conversation_tool_path_active():
            self._lesson_child_audio_pending_transcript = False
            self._cancel_lesson_child_transcript_timeout_task()
            self._record_lesson_conversation_recognized_text(transcript_text)
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_conversation_transcript_ignored source=user chars={}",
                    self.conn,
                ),
                len(str(transcript_text or "")),
            )
            return True
        if self._suppress_user_transcript_as_model_echo(transcript_text):
            return True
        if await self._dispatch_lesson_child_response(transcript_text):
            return True
        if self._is_live_wake_transcript_only(transcript_text):
            # Firmware often already opened wake via listen:detect; ignore Live STT duplicate.
            if time.monotonic() < float(self._wake_audio_window_until or 0.0):
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live wake_transcript_ignored_duplicate "
                    "chars={} window_ms_left={:.0f}",
                    len(str(transcript_text or "")),
                    max(0.0, (self._wake_audio_window_until - time.monotonic()) * 1000),
                )
                return True
            if self._has_active_output() or self._has_music_session():
                await self._begin_user_interrupt("wake_word")
            suppress_sec = self._get_wake_transcript_tail_suppress_sec()
            if suppress_sec > 0:
                self._wake_transcript_tail_suppress_until = (
                    time.monotonic() + suppress_sec
                )
            await self._send_wake_listening_feedback(transcript_text)
            await self._open_user_audio_window("wake_word")
            if self._user_stream_started_at is None:
                await self._reset_conversation_live_context("wake_transcript")
            self._schedule_live_prewarm("wake_transcript", delay_sec=0.0)
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live wake_transcript_only chars={}",
                len(str(transcript_text or "")),
            )
            return True
        if await self._dispatch_lesson_start_intent(transcript_text):
            return True
        if await self._dispatch_music_control_intent(transcript_text):
            return True
        return False

    async def _dispatch_lesson_start_intent(self, transcript_text):
        payload = self._classify_lesson_start_intent(transcript_text)
        if payload is None:
            self._log_lesson_start_intent_miss(transcript_text)
            return False
        # Delayed / re-emitted STT after a successful start must not restart the lesson
        # (that cancelled s1 greeting and left the robot silent).
        if time.monotonic() < float(self._suppress_start_lesson_tool_call_until or 0.0):
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_start_intent_suppressed reason=duplicate_window "
                    "chars={}",
                    self.conn,
                ),
                len(str(transcript_text or "")),
            )
            return True
        if self._lesson_runtime_active():
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_start_intent_suppressed reason=lesson_already_active "
                    "chars={}",
                    self.conn,
                ),
                len(str(transcript_text or "")),
            )
            self._suppress_start_lesson_tool_call_until = (
                time.monotonic() + self._START_LESSON_DUPLICATE_TOOL_WINDOW_SEC
            )
            return True
        # User asked to start lesson: stop shielding residual mic from wake greeting.
        self._wake_greeting_protect_until = 0.0
        if not await self.transition_to_lesson_start():
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_start_intent retry reason=live-transition-timeout",
                    self.conn,
                )
            )
            return True
        # In dormant live mode the tool handler may not be bootstrapped yet. Only
        # bootstrap when the classified tool is actually admitted for this product
        # (product_tool_names), so a missing handler does not get spun up to run a
        # tool that is not part of the device's toolset.
        if getattr(self.conn, "func_handler", None) is None:
            try:
                admitted = payload.get("name") in product_tool_names(self.conn)
            except Exception:
                admitted = False
            if admitted:
                await self._ensure_func_handler()
        func_handler = getattr(self.conn, "func_handler", None)
        if func_handler is None:
            return False
        try:
            with self._lesson_start_tool_dispatch_scope():
                result = await func_handler.handle_llm_function_call(self.conn, payload)
            self._suppress_start_lesson_tool_call_until = (
                time.monotonic() + self._START_LESSON_DUPLICATE_TOOL_WINDOW_SEC
            )
            await self._send_lesson_start_ack(result)
            # The local tool dispatch finished: release the realtime busy/interrupt
            # latch set by _begin_user_interrupt so the controller does not stay
            # stuck in INTERRUPTING with client_abort latched.
            self._interaction.transition(InteractionState.LISTENING)
            self.conn.client_abort = False
            # Keep mic open so the child can answer after greeting without re-wake.
            try:
                await self._open_user_audio_window("lesson_start")
            except Exception:
                pass
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_start_intent tool={} chars={}",
                    self.conn,
                ),
                payload.get("name"),
                len(str(transcript_text or "")),
            )
            return True
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_start_intent failed tool={} error={}",
                    self.conn,
                ),
                payload.get("name"),
                self._safe_error_message(exc),
            )
            return False

    @contextmanager
    def _lesson_start_tool_dispatch_scope(self):
        marker = getattr(self.conn, "_lesson_start_tool_dispatch_context", None)
        if marker is None:
            marker = ContextVar(
                f"lesson_start_tool_dispatch_{id(self.conn):x}",
                default=None,
            )
            self.conn._lesson_start_tool_dispatch_context = marker
        interaction = self._interaction
        admission = {
            "providerId": id(self),
            "responseGeneration": int(self._response_generation),
            "responseId": int(interaction.response_id),
        }
        token = marker.set(admission)
        try:
            yield
        finally:
            marker.reset(token)

    def _log_lesson_start_intent_miss(self, transcript_text):
        text = self._normalize_intent_text(transcript_text)
        if not text:
            return
        markers = (
            "bai hoc", "hoc bai", "khoa hoc", "lesson", "class", "course",
            "start", "begin", "open", "speed", "esp",
        )
        if not any(marker in text for marker in markers):
            return
        try:
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_start_intent miss normalized_chars={} chars={}",
                    self.conn,
                ),
                len(text),
                len(str(transcript_text or "")),
            )
        except Exception:
            pass

    async def _send_lesson_start_ack(self, action_response):
        text = self._lesson_start_ack_text(action_response)
        if not text:
            return False
        if await self._send_live_text_ack(
            text,
            log_label="lesson_start_ack",
            allow_lesson_output=True,
        ):
            return True
        return False

    async def speak_lesson_step_prompt(self, text, *, continue_listening=False):
        text = str(text or "").strip()
        if not text:
            return False
        # Remember the prompt length so the subsequent child-response window can
        # wait for this narration's TTS to finish before opening the mic.
        self._last_lesson_prompt_len = len(text)
        self._last_lesson_prompt_text = text
        self._lesson_prompt_reopen_fast = bool(continue_listening)
        self._lesson_prompt_resend_count = 0
        if await self._send_live_text_ack(
            text,
            log_label="lesson_step_prompt",
            allow_lesson_output=True,
        ):
            return True
        return False

    async def wait_lesson_step_prompt_idle(self):
        config = self._get_live_config()
        estimate = self._lesson_prompt_duration_estimate_sec(config)
        # Cap wait to spoken length + small margin. A hard 30s hang after a Live
        # interrupt made start_lesson feel broken and skipped the greeting.
        adaptive_timeout = min(9.0, max(3.0, estimate + 1.0))
        for attempt in range(2):
            await self._wait_for_lesson_prompt_output_idle(
                config,
                output_timeout_override=adaptive_timeout,
                timeout_log_label="lesson_passive_prompt_output_guard_timeout",
            )
            needs_resend = bool(
                getattr(self.conn, "google_live_lesson_prompt_needs_resend", False)
            )
            if not needs_resend:
                return True
            if attempt >= 1:
                self.conn.google_live_lesson_prompt_needs_resend = False
                return True
            # Live stopped generation mid-prompt; re-speak introduction once.
            self.conn.google_live_lesson_prompt_needs_resend = False
            if not await self._resend_last_lesson_prompt(reason="interrupted"):
                return True
            self._lesson_prompt_resend_count = int(
                getattr(self, "_lesson_prompt_resend_count", 0) or 0
            ) + 1
        return True

    async def open_lesson_child_response_window(self):
        """Open the listening window during which the child's voice/text is
        routed to the active lesson runtime instead of the chat model.

        Waits a prompt-guard delay (so the robot's just-spoken narration TTS has
        finished playing) before advancing _user_audio_allowed_until, then sizes
        the window from lesson_child_response_window_sec.
        """
        runtime = getattr(self.conn, "lesson_runtime", None)
        guarded_step_id = getattr(runtime, "_step_id", None) if runtime is not None else None
        if runtime is not None and not self._lesson_runtime_accepts_voice_input():
            return False
        config = self._get_live_config()

        def _read_float(key, default):
            try:
                return float(config.get(key, default))
            except (TypeError, ValueError):
                return default

        open_delay = _read_float("lesson_child_response_open_delay_sec", 0.15)
        chars_per_sec = _read_float("lesson_prompt_tts_chars_per_sec", 12.0)
        max_open_delay = _read_float("lesson_child_response_max_open_delay_sec", 8.0)
        # Coaching / no-answer reopens should re-open the mic soon after TTS.
        fast_reopen_sec = _read_float("lesson_child_response_fast_reopen_sec", 1.0)
        # Cover patient final-question windows (sample s4 ~22s) with margin.
        window_sec = _read_float("lesson_child_response_window_sec", 30.0)

        prompt_estimate = 0.0
        if chars_per_sec > 0 and self._last_lesson_prompt_len > 0:
            prompt_estimate = self._last_lesson_prompt_len / chars_per_sec
        delay = open_delay + prompt_estimate
        if max_open_delay > 0:
            delay = min(delay, max_open_delay)
        fast_reopen_requested = self._lesson_prompt_reopen_fast and fast_reopen_sec >= 0
        if fast_reopen_requested:
            delay = min(delay, fast_reopen_sec)
        self._lesson_prompt_reopen_fast = False
        if delay > 0:
            # Call asyncio.sleep through the module-level asyncio so the test's
            # google_live_module.asyncio.sleep monkeypatch is honoured.
            await asyncio.sleep(delay)
        prompt_output_idle = await self._wait_for_lesson_prompt_output_idle(
            config,
            timeout_log_label="lesson_prompt_output_guard_timeout",
        )
        if not prompt_output_idle:
            return False
        if runtime is not None:
            current_runtime = getattr(self.conn, "lesson_runtime", None)
            if (
                current_runtime is not runtime
                or getattr(current_runtime, "_step_id", None) != guarded_step_id
                or not self._lesson_runtime_accepts_voice_input()
            ):
                return False

        self._lesson_child_audio_pending_transcript = False
        self._clear_lesson_child_speech_start_frames()
        self._cancel_lesson_child_transcript_timeout_task()
        self._user_audio_allowed_until = max(
            self._user_audio_allowed_until,
            time.monotonic() + max(0.1, window_sec),
        )
        self._force_lesson_session_mode("lesson_child_response_window")
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_child_response_window_open delay_sec={:.2f} window_sec={:.1f}",
                self.conn,
            ),
            delay,
            window_sec,
        )
        return True

    def close_lesson_child_response_window(self):
        self._lesson_child_audio_pending_transcript = False
        self._cancel_lesson_child_transcript_timeout_task()
        self._clear_lesson_child_speech_start_frames()
        self._user_audio_allowed_until = 0.0
        self._user_stream_response_id = None
        self._user_stream_started_at = None
        self._user_stream_last_speech_at = None
        self._user_stream_frames = 0
        if self._interaction.state == InteractionState.USER_STREAMING:
            self._interaction.transition(InteractionState.LISTENING)

    async def _wait_for_lesson_prompt_output_idle(
        self,
        config,
        *,
        output_timeout_override=None,
        timeout_log_label="lesson_prompt_output_guard_timeout",
    ):
        poll_sec = self._read_lesson_guard_float(
            config, "lesson_prompt_output_poll_sec", 0.1
        )
        poll_sec = max(0.01, poll_sec)
        output_timeout = self._read_lesson_guard_float(
            config, "lesson_prompt_output_guard_timeout_sec", 12.0
        )
        if output_timeout_override is not None:
            output_timeout = min(output_timeout, max(0.0, output_timeout_override))
        # Never wait longer than spoken estimate + small buffer for passive steps.
        estimate_cap = min(9.0, max(3.0, self._lesson_prompt_duration_estimate_sec(config) + 1.2))
        output_timeout = min(output_timeout, estimate_cap)
        playback_timeout = self._read_lesson_guard_float(
            config, "lesson_prompt_playback_guard_timeout_sec", 6.0
        )
        playback_tail = self._read_lesson_guard_float(
            config, "lesson_prompt_playback_tail_sec", 0.5
        )

        remaining = max(0.0, output_timeout)
        wait_started = time.monotonic()
        # If Live never starts audio (common when interruption races text prompts),
        # fail fast instead of waiting the full spoken estimate.
        no_audio_deadline = min(2.5, max(1.0, output_timeout * 0.35))
        while getattr(self.conn, "google_live_lesson_prompt_output_allowed", False):
            if getattr(self.conn, "google_live_lesson_prompt_needs_resend", False):
                # Interruption cut the prompt before/while speaking — exit so caller
                # can resend instead of burning the full timeout mute.
                self.conn.google_live_lesson_prompt_output_allowed = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_prompt_wait_interrupted_for_resend remaining_sec={:.1f}",
                        self.conn,
                    ),
                    remaining,
                )
                return False
            if self._lesson_prompt_output_inferred_idle(config):
                self.conn.google_live_lesson_prompt_output_allowed = False
                self.conn.google_live_lesson_prompt_output_inferred_idle = True
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_prompt_output_inferred_idle idle_sec={:.1f}",
                        self.conn,
                    ),
                    self._get_lesson_prompt_inferred_idle_sec(config),
                )
                break
            if (
                not self._lesson_prompt_heard_audio()
                and (time.monotonic() - wait_started) >= no_audio_deadline
            ):
                self.conn.google_live_lesson_prompt_output_allowed = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_prompt_no_audio_deadline_sec={:.1f}",
                        self.conn,
                    ),
                    no_audio_deadline,
                )
                return False
            if remaining <= 0:
                self.conn.google_live_lesson_prompt_output_allowed = False
                self.conn.google_live_lesson_prompt_output_inferred_idle = False
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live {} timeout_sec={:.1f}",
                    timeout_log_label,
                    output_timeout,
                )
                return False
            sleep_for = min(poll_sec, remaining)
            await asyncio.sleep(sleep_for)
            remaining -= sleep_for

        rate_controller = getattr(self.conn, "audio_rate_controller", None)
        wait_until_empty = getattr(rate_controller, "wait_until_empty", None)
        queue_obj = getattr(rate_controller, "queue", None)
        if wait_until_empty is None or queue_obj is None:
            return True
        try:
            queue_len = len(queue_obj)
        except Exception:
            queue_len = 0
        pending_task = getattr(rate_controller, "pending_send_task", None)
        if queue_len <= 0 and (pending_task is None or pending_task.done()):
            return True
        try:
            await asyncio.wait_for(
                wait_until_empty(),
                timeout=max(0.01, playback_timeout),
            )
            if playback_tail > 0:
                await asyncio.sleep(playback_tail)
        except asyncio.TimeoutError:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_prompt_playback_guard_timeout timeout_sec={:.1f} queue_len={}",
                    self.conn,
                ),
                playback_timeout,
                queue_len,
            )
        return True

    @staticmethod
    def _read_lesson_guard_float(config, key, default):
        try:
            return float(config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _lesson_prompt_output_inferred_idle(self, config):
        last_activity_at = self._lesson_prompt_output_last_activity_at
        if last_activity_at is None:
            return False
        idle_sec = self._get_lesson_prompt_inferred_idle_sec(config)
        if idle_sec <= 0:
            return False
        # ponytail: Live sometimes omits audio_end for text-prompt TTS; upgrade
        # to provider finish-reason handling if Gemini exposes a stable signal.
        return (time.monotonic() - last_activity_at) >= idle_sec

    def _get_lesson_prompt_inferred_idle_sec(self, config):
        return max(
            0.0,
            self._read_lesson_guard_float(
                config, "lesson_prompt_inferred_idle_sec", 1.6
            ),
        )

    def _lesson_child_response_window_active(
        self,
        *,
        require_audio_window=True,
        require_explicit_runtime_window=False,
    ):
        """True when a child-response window is open AND the active lesson
        runtime is interactive and running."""
        if require_audio_window and time.monotonic() >= self._user_audio_allowed_until:
            return False
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None:
            return False
        if not callable(getattr(runtime, "on_child_response", None)):
            return False
        if getattr(runtime, "_step_passive", False):
            return False
        if getattr(runtime, "_step_completed", False):
            return False
        runtime_window_open = getattr(runtime, "_child_response_window_open", None)
        if runtime_window_open is False:
            return False
        if require_explicit_runtime_window and runtime_window_open is not True:
            return False
        state = getattr(runtime, "state", None)
        if state is not None and state not in ("RUNNING",):
            return False
        return True

    def _lesson_child_response_route_active(self):
        if self._lesson_conversation_tool_path_active():
            return False
        if (
            self._lesson_child_response_window_active()
            or self._lesson_child_response_window_active(
                require_audio_window=False,
                require_explicit_runtime_window=True,
            )
        ):
            return True
        if not self._lesson_child_audio_pending_transcript:
            return False
        if not self._lesson_runtime_accepts_voice_input():
            return False
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None or not callable(getattr(runtime, "on_child_response", None)):
            return False
        if getattr(runtime, "_child_response_window_open", None) is not True:
            try:
                runtime._child_response_window_open = True
            except Exception:
                pass
        self._user_audio_allowed_until = max(
            self._user_audio_allowed_until,
            time.monotonic() + 0.1,
        )
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_child_response_pending_transcript_window_opened",
                self.conn,
            )
        )
        return True

    def _should_hold_lesson_prompt_pending_audio(self):
        if not getattr(self.conn, "google_live_lesson_prompt_output_allowed", False):
            return False
        if not self._lesson_runtime_accepts_voice_input():
            return False
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None or not callable(getattr(runtime, "on_child_response", None)):
            return False
        return getattr(runtime, "_child_response_window_open", None) is not True

    def _expired_conversation_audio_window(self):
        if not self._has_session_orchestrator():
            return False
        if self._lesson_runtime_accepts_voice_input():
            return False
        if self._interaction.state not in (
            InteractionState.IDLE,
            InteractionState.LISTENING,
        ):
            return False
        return time.monotonic() >= self._user_audio_allowed_until

    def _should_drop_expired_conversation_audio_window(self, decoded_audio):
        if not self._expired_conversation_audio_window():
            return False
        threshold = self._get_user_speech_rms_threshold()
        if decoded_audio is None or threshold is None:
            return True
        rms = self._input_rms(decoded_audio)
        return not (isinstance(rms, (int, float)) and rms >= threshold)

    def _log_expired_audio_window_drop(self):
        now = time.monotonic()
        if now - self._last_expired_audio_window_drop_log_at < 5.0:
            return
        self._last_expired_audio_window_drop_log_at = now
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live audio_dropped reason=expired_user_audio_window state={}",
            self._interaction.state.value,
        )

    def _force_lesson_session_mode(self, reason):
        """Keep Live model output muted while lesson runtime owns child input."""
        if not self._lesson_runtime_active():
            return
        current = normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        )
        if current == SessionMode.LESSON:
            return
        setter = getattr(self.conn, "_set_session_mode", None)
        if callable(setter):
            setter(SessionMode.LESSON, reason=reason)
        else:
            self.conn.session_mode = SessionMode.LESSON

    async def _route_lesson_child_response(self, transcript_text):
        """Route a child answer to the lesson runtime, blocking Live model
        output for the turn first.

        Returns:
            True  -> transcript consumed by the runtime (do not dispatch/forward)
            False -> blank text the runtime declined (not a command either)
            None  -> route does not apply / runtime declined non-blank text
                     (caller should fall through to command dispatch)
        """
        if not self._lesson_child_response_route_active():
            return None
        runtime = self.conn.lesson_runtime
        self._lesson_child_audio_pending_transcript = False
        self._clear_lesson_child_speech_start_frames()
        self._cancel_lesson_child_transcript_timeout_task()
        self._force_lesson_session_mode("lesson_child_response")
        # Cancel Live's own answer to the child's audio before the runtime emits
        # the controlled retry/success prompt for this lesson turn.
        await self._begin_user_interrupt("lesson_child_response")
        await self._hard_reconnect_after_interrupt(
            "lesson_child_response_prompt",
            restore_session_resumption=False,
        )
        self._interaction.transition(InteractionState.LISTENING)
        self.conn.client_abort = False
        handled = await runtime.on_child_response(
            transcript_text, source="voice_transcript"
        )
        if handled:
            return True
        if not str(transcript_text or "").strip():
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_child_response_consumed_unhandled chars={}",
                self.conn,
            ),
            len(str(transcript_text or "").strip()),
        )
        return True

    async def _send_wake_listening_feedback(self, text):
        """Echo a listening cue to the device when a wake word is detected: an STT
        frame carrying the recognized wake text, then a TTS stop so the device
        leaves the speaking state and opens its mic. Best-effort — a websocket
        failure must not break wake-word routing."""
        websocket = getattr(self.conn, "websocket", None)
        if websocket is None:
            return
        session_id = getattr(self.conn, "session_id", None)
        try:
            from core.utils import textUtils

            stt_text = textUtils.get_string_no_punctuation_or_emoji(str(text or ""))
        except Exception:
            stt_text = str(text or "")
        for payload in (
            {"type": "stt", "text": stt_text, "session_id": session_id},
            {
                "type": "tts",
                "state": "stop",
                "session_id": session_id,
                "continue_listening": True,
                "listen_mode": "realtime",
            },
        ):
            try:
                await websocket.send(json.dumps(payload))
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live wake_listening_feedback send failed: {}",
                    self._safe_error_message(exc),
                )
                return

    async def _dispatch_lesson_child_response(self, transcript_text):
        """Bool wrapper over _route_lesson_child_response for the text/transcript
        routing paths. Preserves the route's stop-output-before-advance ordering
        and swallows runtime errors. Returns True only when the runtime consumed
        the answer; False otherwise (declined / blank / no active window)."""
        try:
            routed = await self._route_lesson_child_response(transcript_text)
        except Exception:
            return False
        return routed is True

    async def _dispatch_lesson_child_response_failure(self, reason):
        """Route a finalized child-audio turn that produced no usable transcript."""
        if self._lesson_conversation_tool_path_active():
            self._lesson_child_audio_pending_transcript = False
            self._cancel_lesson_child_transcript_timeout_task()
            return False
        if not self._lesson_child_response_route_active():
            return False
        runtime = getattr(self.conn, "lesson_runtime", None)
        handler = getattr(runtime, "on_child_response_failure", None)
        if not callable(handler):
            return False
        self._lesson_child_audio_pending_transcript = False
        self._clear_lesson_child_speech_start_frames()
        self._cancel_lesson_child_transcript_timeout_task()
        self._force_lesson_session_mode("lesson_child_response_failure")
        try:
            await self._begin_user_interrupt("lesson_child_response_failure")
            await self._hard_reconnect_after_interrupt(
                "lesson_child_response_failure_prompt",
                restore_session_resumption=False,
            )
            self._interaction.transition(InteractionState.LISTENING)
            self.conn.client_abort = False
            return bool(await handler(str(reason or "stt_unavailable")))
        except Exception:
            return False

    def _lesson_start_ack_text(self, action_response):
        if action_response is None:
            return ""
        action = getattr(action_response, "action", None)
        result = str(getattr(action_response, "result", "") or "").lower()
        response = str(getattr(action_response, "response", "") or "").strip()
        if action == Action.ERROR:
            return response or "Xin lỗi, robot chưa bắt đầu bài học được."
        # Successful schedule: stay silent. Sample s1 greeting is the introduction;
        # speaking "Bắt đầu bài học nhé" here races Live and cuts the greeting.
        if action == Action.RECORD and "lesson start scheduled" in result:
            return ""
        if action == Action.RESPONSE and any(
            marker in result for marker in ("disabled", "busy", "failed", "error")
        ):
            return response or "Robot chưa bắt đầu bài học được."
        return ""

    def _lesson_prompt_duration_estimate_sec(self, config=None):
        config = config if isinstance(config, Mapping) else self._get_live_config()
        chars_per_sec = self._read_lesson_guard_float(
            config, "lesson_prompt_tts_chars_per_sec", 12.0
        )
        if chars_per_sec <= 0:
            chars_per_sec = 12.0
        prompt_len = max(1, int(self._last_lesson_prompt_len or 0))
        # Spoken Vietnamese + device drain headroom.
        return (prompt_len / chars_per_sec) + 1.5

    def _lesson_prompt_heard_audio(self):
        return self._lesson_prompt_output_last_activity_at is not None

    async def _resend_last_lesson_prompt(self, *, reason="interruption"):
        text = str(getattr(self, "_last_lesson_prompt_text", "") or "").strip()
        if not text:
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_prompt_resend reason={} chars={}",
                self.conn,
            ),
            reason,
            len(text),
        )
        return await self._send_live_text_ack(
            text,
            log_label="lesson_step_prompt_resend",
            allow_lesson_output=True,
        )

    async def _send_live_text_ack(
        self,
        text,
        *,
        log_label="lesson_start_ack",
        allow_lesson_output=False,
    ):
        if allow_lesson_output:
            return await self._send_live_text_ack_with_lease(
                text,
                log_label=log_label,
                allow_lesson_output=True,
            )
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_SEND_TEXT
        ) as allowed:
            if not allowed:
                return False
            return await self._send_live_text_ack_with_lease(
                text,
                log_label=log_label,
                allow_lesson_output=allow_lesson_output,
            )

    async def _send_live_text_ack_with_lease(
        self, text, *, log_label, allow_lesson_output
    ):
        if not await self._ensure_live_open_for_lesson_text():
            return False
        client = self._client
        if client is None or not hasattr(client, "send_text"):
            return False
        try:
            if allow_lesson_output:
                self.conn.google_live_lesson_prompt_output_allowed = True
                self.conn.google_live_lesson_prompt_output_inferred_idle = False
                self._lesson_prompt_output_last_activity_at = None
                self._last_lesson_prompt_text = str(text or "").strip()
                self._last_lesson_prompt_len = len(self._last_lesson_prompt_text)
                self.conn.google_live_lesson_prompt_needs_resend = False
            if self._bridge is not None:
                if allow_lesson_output and hasattr(self._bridge, "force_allow_model_output"):
                    self._bridge.force_allow_model_output()
                elif hasattr(self._bridge, "allow_model_output"):
                    self._bridge.allow_model_output()
            self.conn.google_live_turn_started_at = time.monotonic()
            await client.send_text(f"{LESSON_LIVE_TEXT_INSTRUCTION}{text}")
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live {} sent via live text chars={} sha256={}",
                log_label,
                len(text),
                text_hash,
            )
            return True
        except Exception as exc:
            if allow_lesson_output:
                self.conn.google_live_lesson_prompt_output_allowed = False
            await self._close_live_resources()
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live {} live text failed: {}",
                log_label,
                self._safe_error_message(exc),
            )
            return False

    async def _await_live_connect(self, client, timeout_sec: float = 3.0) -> bool:
        """Give an in-flight Live connect a moment to finish before giving up on it.

        Bounded on purpose: a lesson prompt that arrives late is still spoken, but one
        that waits indefinitely would stall the whole step.
        """
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while time.monotonic() < deadline:
            if getattr(client, "connected", False):
                return True
            await asyncio.sleep(0.05)
        return bool(getattr(client, "connected", False))

    async def _ensure_live_open_for_lesson_text(self):
        client = self._client
        if client is not None and hasattr(client, "send_text"):
            # Existing is not the same as CONNECTED. `start_lesson` schedules a Live
            # prewarm and then drives straight into the lesson, so when step 1's prompt
            # is sent the client object is already here while its session is still being
            # established. Treating that as ready made send_text raise "client not
            # connected" and the prompt was dropped (handoff=0) -- on a real robot the
            # child hears nothing at the start of the lesson, every time.
            if getattr(client, "connected", True):
                return True
            if await self._await_live_connect(client):
                return True
            # Still not up: fall through and open a session rather than drop the prompt.
        if not self._has_session_orchestrator():
            return False
        decision = await self._admit_live_open()
        if decision.decision == AdmissionDecision.FRIENDLY_BREAK:
            await self._send_live_unavailable(decision.reason)
            return False
        if decision.decision == AdmissionDecision.DEGRADE_TTS_ONLY:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_text_open degraded reason={}",
                    self.conn,
                ),
                decision.reason.value,
            )
            return False
        try:
            await self._ensure_func_handler()
            if await self._open_live_session() is False:
                return False
            self.conn.voice_provider = self
            self._voice_consent_denied = False
            return self._client is not None and hasattr(self._client, "send_text")
        except Exception as exc:
            if self._lesson_conversation_tool_path_active():
                # A curated fallback may already have been spoken through the
                # ladder above; never let the caller also send its original
                # text on the same turn, or the child hears two replies.
                if await self._handle_lesson_live_interruption("transport"):
                    return False
            await self._close_live_resources()
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_text_open failed: {}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )
            return False

    def _classify_lesson_start_intent(self, transcript_text):
        try:
            if "start_lesson" not in product_tool_names(self.conn):
                return None
        except Exception:
            return None
        text = self._normalize_intent_text(transcript_text)
        if not text:
            return None
        # Reject anything that is NOT a direct "start the lesson now" command, even when
        # a lesson marker is present: negations ("đừng/không/chưa/khoan mở bài học"),
        # questions ("khi nào/làm sao/tại sao/how/why/what time ... bắt đầu bài học"),
        # temporal deferrals ("lát nữa/ngày mai/later/after/không phải bây giờ"), and
        # reported speech ("nhắc con/remind me/tell me/robot nói/cô giáo nói ..."). Markers
        # are checked AFTER this, so a blocker hit short-circuits to None. (Normalized text:
        # accent-stripped, đ→d, lowercased, whitespace-collapsed.)
        blockers = (
            # explicit negation
            "khong ", "dung ", "chua ", "khoan ", "do not", "dont", "never ",
            "not ready", "not now", "no not", "no start", "cancel ", "stop lesson",
            # questions about the lesson, not a command to start it
            "khi nao", "bao gio", "lam sao", "tai sao", "cach ",
            "what time", "how do", "how to", "why ", "steps to",
            # temporal deferral
            "lat nua", "ngay mai", " later", "after ", "khong phai bay gio",
            # reported speech / quoting / repeating / meta, not a direct request
            "nhac con", "remind me", "tell me", "robot noi", "co giao",
            "teacher ", " say ", "please say", "doc ", "lap lai", "repeat",
            "the phrase", "means ", "don t",
        )
        if any(blocker in text for blocker in blockers):
            return None
        # Keep the high-speed workaround scoped to transcripts that still carry
        # explicit start/lesson context; plain "high speed" is a wake alias for
        # "Hi ESP" in production.
        exact_markers = {
            "high speed start",
            "hi speed start",
            "high speed lesson",
            "hi speed lesson",
        }
        if text in exact_markers:
            return {"name": "start_lesson", "arguments": {}}
        wake_prefixed_high_speed = (
            "alo high speed",
            "a lo high speed",
            "hello high speed",
            "hi high speed",
            "hey high speed",
            "alo hi speed",
            "a lo hi speed",
            "hello hi speed",
        )
        if text in wake_prefixed_high_speed:
            return {"name": "start_lesson", "arguments": {}}
        markers = (
            "bat dau bai hoc",
            "bat dau 1 bai hoc",
            "bat dau mot bai hoc",
            "bat dau tiet hoc",
            "bat dau khoa hoc",
            "bat dau hoc bai",
            "quay dau bai hoc",
            "dao ve hoc",
            "vao bai hoc",
            "vo bai hoc",
            "vao hoc bai",
            "vo hoc bai",
            "vao khoa hoc",
            "vo khoa hoc",
            "mo bai hoc",
            "mo bai hoc cua con",
            "mo khoa hoc",
            "mo khoa hoc cua con",
            "chuyen sang bai hoc",
            "hoc bai thoi",
            "hoc bai di",
            "con muon hoc bai",
            "hoc tiep bai",
            "tiep tuc bai hoc",
            "tiep tuc khoa hoc",
            "bai hoc cua con dau",
            "start lesson",
            "start the lesson",
            "begin lesson",
            "begin the class",
            "open my lesson",
            "switch to lesson",
            "continue the lesson",
            "resume lesson",
            "resume my class",
        )
        if not any(marker in text for marker in markers):
            return None
        return {"name": "start_lesson", "arguments": {}}

    def _is_local_stop_word(self, text):
        normalized = self._normalize_intent_text(text)
        if not normalized:
            return False
        return bool(
            re.search(r"(^|\s)dung\s+lai($|\s)", normalized)
            or re.search(r"(^|\s)stop($|\s)", normalized)
        )

    async def _handle_local_stop_word(self, text):
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live local_stop_word detected chars={}",
            len(str(text or "")),
        )
        await self._begin_user_interrupt("local_stop_word")


    def _get_lifecycle_lock(self):
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

    def _get_live_open_lock(self):
        if self._live_open_lock is None:
            self._live_open_lock = asyncio.Lock()
        return self._live_open_lock

    async def _receive_events_loop(self, generation):
        client = self._client
        if generation != self._session_generation or client is None:
            return
        try:
            async for event in client.receive_events():
                if generation != self._session_generation or client is not self._client:
                    return
                if (
                    isinstance(event, dict)
                    and event.get("type") == "session_expiring"
                ):
                    self.conn.logger.bind(tag="GoogleLive").warning(
                        "Google Live session_expiring time_left_ms={}",
                        event.get("time_left_ms"),
                    )
                    self._schedule_proactive_reconnect(event)
                    continue
                if (
                    isinstance(event, dict)
                    and event.get("type") == "session_resumption_update"
                ):
                    self._handle_session_resumption_update(event)
                    continue
                await self._handle_live_event(event)
                await self._bridge.handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation != self._session_generation or client is not self._client:
                return
            await self._handle_runtime_failure(exc)
        else:
            if (
                not self._closing
                and self._fallback_provider is None
                and generation == self._session_generation
                and client is self._client
            ):
                await self._handle_runtime_failure(
                    RuntimeError("Google Live receive loop ended")
                )

    def _schedule_proactive_reconnect(self, event):
        if self._closing or self._reconnecting or self._fallback_activating:
            return
        if self._proactive_reconnect_task is not None and not self._proactive_reconnect_task.done():
            return
        self._proactive_reconnect_task = asyncio.create_task(
            self._proactive_reconnect(event)
        )

    async def _proactive_reconnect(self, event):
        try:
            await self._handle_runtime_failure(
                RuntimeError(
                    "session_expiring time_left_ms={}".format(
                        event.get("time_left_ms")
                    )
                )
            )
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live proactive_reconnect failed: {}",
                self._safe_error_message(exc),
            )

    async def _activate_classic_fallback(self, exc):
        await self._stop_live_output_for_transport_change()
        self.conn.logger.bind(tag="GoogleLive").error(
            "Google Live unavailable type={}: {}",
            self._classify_error(exc),
            self._safe_error_message(exc),
        )
        self.conn.logger.bind(tag="GoogleLive").warning(
            "Google Live fallback_disabled reason={}",
            self._safe_error_message(exc),
        )
        return False

    async def _speak_fallback_notice_if_available(self, exc):
        try:
            if self._classify_error(exc) not in self._FALLBACK_NOTICE_ERROR_CLASSES:
                return
            speaker = getattr(self._fallback_provider, "speak_child_notice", None)
            if callable(speaker):
                await speaker(self._FALLBACK_NOTICE_MESSAGE)
        except Exception as notice_exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live fallback notice speech failed: {}",
                self._safe_error_message(notice_exc),
            )

    async def _handle_runtime_failure(self, exc):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_RECONNECT
        ) as allowed:
            if not allowed:
                return False
            await self._handle_runtime_failure_with_lease(exc)
            return True

    async def _handle_runtime_failure_with_lease(self, exc):
        await self._stop_live_output_for_transport_change()
        self.conn.logger.bind(tag="GoogleLive").warning(
            "Google Live runtime failure type={}: {}",
            self._classify_error(exc),
            self._safe_error_message(exc),
        )
        if (
            self._closing
            or self._fallback_provider is not None
            or self._fallback_activating
            or self._reconnecting
        ):
            return

        if self._lesson_conversation_tool_path_active():
            handled = await self._handle_lesson_live_interruption("transport")
            if handled:
                return

        reconnect_result = await self._try_reconnect_with_lease(exc)
        if reconnect_result is not False:
            return
        if self._closing:
            return

        self._fallback_activating = True
        try:
            await self._close_live_resources()
            await self._activate_classic_fallback(exc)
        finally:
            self._fallback_activating = False

    async def _close_live_resources(self, *, preserve_live_prewarm=False):
        current_task = asyncio.current_task()
        receive_task = self._receive_task
        flush_task = self._input_flush_task
        forced_interrupt_flush_task = self._forced_interrupt_flush_task
        waiting_model_timeout_task = self._waiting_model_timeout_task
        user_audio_window_task = self._user_audio_window_task
        lesson_child_transcript_timeout_task = self._lesson_child_transcript_timeout_task
        start_lesson_asr_fallback_task = self._start_lesson_asr_fallback_task
        proactive_task = self._proactive_reconnect_task
        idle_task = self._idle_close_task
        func_handler_bootstrap_task = self._func_handler_bootstrap_task
        live_prewarm_task = self._live_prewarm_task
        wake_greeting_task = self._wake_greeting_task
        self._receive_task = None
        self._input_flush_task = None
        self._forced_interrupt_flush_task = None
        self._waiting_model_timeout_task = None
        self._user_audio_window_task = None
        self._lesson_child_transcript_timeout_task = None
        self._start_lesson_asr_fallback_task = None
        self._proactive_reconnect_task = None
        self._idle_close_task = None
        self._func_handler_bootstrap_task = None
        # wait_for runs the Live open in a child task, so owned prewarm cleanup
        # must preserve the parent explicitly as well as by current-task identity.
        if not preserve_live_prewarm and live_prewarm_task is not current_task:
            self._live_prewarm_task = None
        if wake_greeting_task is not current_task:
            self._wake_greeting_task = None
        self._wake_audio_window_until = 0.0
        self._wake_transcript_tail_suppress_until = 0.0
        for background_task in (live_prewarm_task, wake_greeting_task):
            if (
                background_task is not None
                and background_task is not current_task
                and not (
                    preserve_live_prewarm and background_task is live_prewarm_task
                )
                and not background_task.done()
            ):
                background_task.cancel()
                try:
                    await background_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        if receive_task is not None and receive_task is not current_task:
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
        if flush_task is not None and flush_task is not current_task:
            flush_task.cancel()
            try:
                await flush_task
            except asyncio.CancelledError:
                pass
        if (
            forced_interrupt_flush_task is not None
            and forced_interrupt_flush_task is not current_task
        ):
            forced_interrupt_flush_task.cancel()
            try:
                await forced_interrupt_flush_task
            except asyncio.CancelledError:
                pass
        if (
            waiting_model_timeout_task is not None
            and waiting_model_timeout_task is not current_task
        ):
            waiting_model_timeout_task.cancel()
            try:
                await waiting_model_timeout_task
            except asyncio.CancelledError:
                pass
        if user_audio_window_task is not None and user_audio_window_task is not current_task:
            user_audio_window_task.cancel()
            try:
                await user_audio_window_task
            except asyncio.CancelledError:
                pass
        if (
            lesson_child_transcript_timeout_task is not None
            and lesson_child_transcript_timeout_task is not current_task
        ):
            lesson_child_transcript_timeout_task.cancel()
            try:
                await lesson_child_transcript_timeout_task
            except asyncio.CancelledError:
                pass
        if (
            start_lesson_asr_fallback_task is not None
            and start_lesson_asr_fallback_task is not current_task
        ):
            start_lesson_asr_fallback_task.cancel()
            try:
                await start_lesson_asr_fallback_task
            except asyncio.CancelledError:
                pass
        if proactive_task is not None and proactive_task is not current_task:
            proactive_task.cancel()
            try:
                await proactive_task
            except asyncio.CancelledError:
                pass
        if idle_task is not None and idle_task is not current_task:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass
        if (
            func_handler_bootstrap_task is not None
            and func_handler_bootstrap_task is not current_task
        ):
            func_handler_bootstrap_task.cancel()
            try:
                await func_handler_bootstrap_task
            except asyncio.CancelledError:
                pass

        if self._bridge is not None and hasattr(self._bridge, "close"):
            try:
                await self._bridge.close()
            except Exception:
                pass

        await self._record_live_session_usage()

        if self._client is not None:
            try:
                await self._client.close()
            except RuntimeError as exc:
                if "asynchronous generator is already running" not in str(exc):
                    raise
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live close skipped concurrent live_context exit: {}",
                    self._safe_error_message(exc),
                )
        self._client = None

        self._bridge = None
        self._start_lesson_asr_fallback_audio.clear()
        if self._has_session_orchestrator():
            self.conn.google_live_audio_out_started_at = None

    async def _close_stale_live_resources_before_open(self):
        receive_task = self._receive_task
        if (
            self._client is None
            and self._bridge is None
            and (receive_task is None or receive_task.done())
        ):
            if receive_task is not None and receive_task.done():
                self._receive_task = None
            return
        self.conn.logger.bind(tag="GoogleLive").warning(
            "Google Live stale_live_resources_before_open closing_existing_receive_task={}",
            bool(receive_task is not None and not receive_task.done()),
        )
        await self._close_live_resources()

    async def _open_live_session(self):
        if self._lesson_runtime_active() or normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON:
            await self._open_live_session_with_lease()
            return True
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_OPEN
        ) as allowed:
            if not allowed:
                return False
            await self._open_live_session_with_lease()
            return True

    async def _open_live_session_with_lease(self):
        async with self._get_live_open_lock():
            try:
                await self._open_live_session_locked()
            except Exception as exc:
                if not self._should_retry_without_session_resumption(exc):
                    raise
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live retrying_without_session_resumption reason={}",
                    self._safe_error_message(exc),
                )
                await self._close_live_resources()
                self.conn.google_live_session_resumption_handle = None
                await self._open_live_session_locked(restore_session_resumption=False)

    async def _open_live_session_locked(self, *, restore_session_resumption=True):
        await self._close_stale_live_resources_before_open()
        if self._skip_next_session_resumption_restore:
            restore_session_resumption = False
            self._skip_next_session_resumption_restore = False
        if restore_session_resumption:
            await self._restore_session_resumption_handle()
        self._session_generation += 1
        generation = self._session_generation
        self._interaction.start_live_connection(generation)
        self._cancelled_response_ids.clear()
        self._pending_tool_calls.clear()
        self._cancelled_tool_call_ids.clear()
        live_config = self._get_live_config_with_functions()
        lesson_instruction_in_config = (
            self._lesson_runtime_active()
            or normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) == SessionMode.LESSON
        )
        self._client = self._client_factory(live_config, self.conn.logger)
        generation_getter = getattr(self._client, "set_response_generation_getter", None)
        if callable(generation_getter):
            generation_getter(self.current_response_id)
        self._bridge = GoogleLiveAudioBridge(
            self.conn,
            self._client,
            self.conn.logger,
            response_id_getter=self.current_response_id,
            response_cancelled_checker=self.is_response_cancelled,
            user_transcript_handler=self._on_user_transcript,
            user_transcript_barge_in_handler=self._on_user_transcript_barge_in,
            tool_call_handler=self._handle_tool_call_event,
            tool_call_cancellation_handler=self._handle_tool_call_cancellation_event,
            model_output_unblocked_handler=self._on_model_output_unblocked,
            output_judge=self._build_output_judge(),
        )
        self._ensure_required_aec_ready()
        await self._client.connect()
        self._lesson_instruction_generation = generation if lesson_instruction_in_config else None
        self._lesson_context_signature = None
        await self._publish_current_lesson_context()
        self.conn.google_live_session_started_at = time.monotonic()
        self._touch_live_activity()
        if (
            self._has_session_orchestrator()
            and not self._lesson_runtime_active()
            and normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) != SessionMode.LESSON
        ):
            self.conn._set_session_mode(SessionMode.CONVERSATION, reason="live_open")
        elif self._has_session_orchestrator() and self._lesson_runtime_active():
            self.conn._set_session_mode(SessionMode.LESSON, reason="lesson_runtime_active")
        self._schedule_idle_close_task()
        self._receive_task = asyncio.create_task(
            self._receive_events_loop(generation)
        )

    async def _reset_conversation_live_context(self, reason):
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON or self._lesson_runtime_active():
            return False
        active_user_stream = (
            self._interaction.state == InteractionState.USER_STREAMING
            or self._user_stream_started_at is not None
        )
        active_output = self._has_active_output()
        if active_user_stream or active_output:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live conversation_context_reset_skipped reason={} active_user_stream={} active_output={}",
                reason,
                active_user_stream,
                active_output,
            )
            return False
        had_handle = bool(
            getattr(self.conn, "google_live_session_resumption_handle", None)
        )
        self.conn.google_live_session_resumption_handle = None
        self._skip_next_session_resumption_restore = True
        if had_handle and self._client is not None:
            await self._close_live_resources()
        if had_handle:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live conversation_context_reset reason={} resumption=cleared",
                reason,
            )
        return True

    def _should_retry_without_session_resumption(self, exc):
        if not self._is_session_resumption_enabled():
            return False
        if not getattr(self.conn, "google_live_session_resumption_handle", None):
            return False
        message = str(exc).lower()
        return (
            "session expired" in message
            or "session not found" in message
            or "resumption" in message
        )

    def _build_output_judge(self):
        """Build the optional async LLM-judge callable for model-output moderation.

        Returns None (judge disabled, regex-only) unless a usable LLM provider is
        on the connection. The judge runs the sync `response_no_stream` off the
        event loop via asyncio.to_thread so it never blocks the realtime path; the
        judge helper itself is timeout-bounded and fail-open."""
        llm = getattr(self.conn, "llm", None)
        if llm is None or not hasattr(llm, "response_no_stream"):
            return None

        async def _judge(text):
            async def _call(system, user):
                return await asyncio.to_thread(llm.response_no_stream, system, user)

            return await judge_output_unsafe(text, _call)

        return _judge

    def _augment_prompt_with_child_name(self, prompt):
        """Append a <child_profile> addressing block to the system prompt when a
        usable child name is configured. Mirrors core.handle.sendAudioHandle.
        _child_name_for_tts_state name resolution. Returns the prompt unchanged
        when no usable name is present so the augmentation is a pure no-op."""
        config = getattr(self.conn, "config", None)
        if not isinstance(config, Mapping):
            return prompt
        child_profile = config.get("child_profile") or {}
        if not isinstance(child_profile, Mapping):
            return prompt
        raw_name = child_profile.get("child_name") or child_profile.get("childName")
        if not isinstance(raw_name, str):
            return prompt
        child_name = raw_name.strip()
        if not child_name:
            return prompt
        block = (
            "\n\n<child_profile>\n"
            f"The child's name is {child_name}.\n"
            "Use the child's name naturally when greeting and encouraging them.\n"
            "</child_profile>"
        )
        return f"{prompt}{block}"

    def _get_live_config_with_functions(self):
        config = self._get_live_config()
        functions = self._resolve_functions_for_live()
        if functions:
            config["functions"] = functions
        if config.get("session_resumption_enabled", True):
            handle = getattr(self.conn, "google_live_session_resumption_handle", None)
            if handle:
                config["session_resumption_handle"] = handle
        # Pass agent's system prompt into Live so the model knows when to
        # call device-control tools (volume, brightness, theme). Without
        # system_instruction the model only chats verbally and ignores
        # Vietnamese intents like "tăng âm lượng".
        prompt = self.conn.config.get("prompt") if self.conn else None
        lesson_active = (
            self._lesson_runtime_active()
            or normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) == SessionMode.LESSON
        )
        if prompt:
            prompt = self._augment_prompt_with_child_name(prompt)
            if lesson_active:
                prompt += LESSON_CONVERSATION_SYSTEM_INSTRUCTION
            config["system_prompt"] = prompt
        elif lesson_active:
            config["system_prompt"] = LESSON_CONVERSATION_SYSTEM_INSTRUCTION.strip()
        return config

    async def publish_lesson_conversation_context(self, context):
        if not isinstance(context, Mapping) or self._client is None:
            return False
        sender = getattr(self._client, "send_text", None)
        if not callable(sender):
            return False
        serialized = json.dumps(dict(context), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signature = (self._session_generation, serialized)
        instruction_needed = self._lesson_instruction_generation != self._session_generation
        if self._lesson_context_signature == signature and not instruction_needed:
            return True
        prefix = "Internal lesson control update. Do not repeat or explain this control message. "
        if instruction_needed:
            prefix += LESSON_CONVERSATION_SYSTEM_INSTRUCTION.strip() + " "
        await sender(prefix + "Authoritative lesson context JSON: " + serialized)
        self._lesson_instruction_generation = self._session_generation
        self._lesson_context_signature = signature
        return True

    async def _publish_current_lesson_context(self):
        runtime = getattr(self.conn, "lesson_runtime", None)
        snapshot = getattr(runtime, "conversation_tool_context", None)
        if not callable(snapshot):
            return False
        try:
            context = snapshot()
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_context_snapshot_failed error={}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )
            return False
        return await self.publish_lesson_conversation_context(context)

    async def deactivate_lesson_conversation_context(self):
        sender = getattr(self._client, "send_text", None)
        try:
            if callable(sender) and self._lesson_instruction_generation is not None:
                await sender(
                    "Internal control update: the lesson has ended. Return to the base "
                    "general-chat system instruction. Ignore the prior lesson coaching "
                    "constraint and do not call lesson tools unless a new authoritative "
                    "lesson context is supplied. Do not repeat this message."
                )
        finally:
            self._lesson_instruction_generation = None
            self._lesson_context_signature = None

    def _handle_session_resumption_update(self, event):
        if not isinstance(event, Mapping):
            return False
        if not self._is_session_resumption_enabled():
            self.conn.google_live_session_resumption_handle = None
            return False
        handle = event.get("handle")
        if not event.get("resumable") or not handle:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live session_resumption_update ignored resumable={} has_handle={}",
                bool(event.get("resumable")),
                bool(handle),
            )
            return False
        self.conn.google_live_session_resumption_handle = str(handle)
        # Persist to the shared live-resumption store (best-effort, fire-and-forget) so a
        # DIFFERENT replica can restore this session after a mid-session failover
        # (test_scaleout_redis_integration). No-op when no store is configured.
        self._schedule_session_resumption_persist(str(handle))
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live session_resumption_handle_updated has_handle=True"
        )
        return True

    # Music tools temporarily removed per user request ("Bỏ function nghe nhạc
    # trước") — plus the robot arm/head motor controls. These were prod-only (lived
    # only in the deployed docker image) and the unify dropped them from git; recovered
    # here to match the proven production image, where they run live alongside voice, so
    # the earlier audio-mixing caution is resolved on-device.
    _LIVE_ALWAYS_INCLUDE = (
        "change_volume",
        "raise_left_arm",
        "raise_right_arm",
        "lower_left_arm",
        "lower_right_arm",
        "raise_both_arms",
        "lower_both_arms",
        "set_left_arm_percent",
        "set_right_arm_percent",
        "set_both_arms_percent",
        "turn_head_left",
        "turn_head_right",
        "center_head",
        "set_head_angle",
        "set_head_percent",
        "turn_head_left_then_right_max",
    )

    # Plugins that depend on classic-pipeline state (conn.tts, conn.sentence_id,
    # tts_text_queue) which Google Live does not initialise. Listing them as
    # Live tools makes the model attempt to call them and crash.
    # NOTE: play_music has been rewritten in plugins_func/functions/play_music_live.py
    # to stream audio directly via sendAudio(), so it's compatible with both modes.
    # Music tools (play/pause/resume/stop) added to this set to disable them
    # entirely from the Live API tool surface until mixing issues are fixed.
    _LIVE_INCOMPATIBLE_TOOLS = frozenset({
        "hass_play_music",
        "play_music",
        "play_music_live",
        "pause_music",
        "resume_music",
        "stop_music",
    })

    def _resolve_functions_for_live(self):
        try:
            names = product_tool_names(self.conn)
            live_names = [
                name for name in names if name not in self._LIVE_INCOMPATIBLE_TOOLS
            ]
            dropped = [name for name in names if name in self._LIVE_INCOMPATIBLE_TOOLS]
            if dropped:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live dropped incompatible tools for live mode: {}",
                    ",".join(dropped),
                )
            return self._build_descriptions_for(live_names) or None
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live failed to resolve tool functions: {}",
                self._safe_error_message(exc),
            )
            return None

    def _extra_function_names_for_live(self):
        return [
            name
            for name in product_tool_names(self.conn)
            if name not in self._LIVE_INCOMPATIBLE_TOOLS
        ]

    def _resolve_override_function_names(self):
        """Live mode tool list, resolved independently of selected_module.Intent.

        Order of precedence:
          1. google_live.functions (explicit list in config).
          2. Intent.function_call.functions (config.yaml default for function_call mode).
        Falls through to the func_handler's own resolution when neither is set.
        """
        live_cfg = self._get_live_config()
        live_funcs = live_cfg.get("functions") if isinstance(live_cfg, Mapping) else None
        if isinstance(live_funcs, list) and live_funcs:
            return [str(name) for name in live_funcs if name]

        intent_cfg = self.conn.config.get("Intent", {}) if isinstance(self.conn.config, Mapping) else {}
        function_call_cfg = intent_cfg.get("function_call", {}) if isinstance(intent_cfg, Mapping) else {}
        candidate = function_call_cfg.get("functions") if isinstance(function_call_cfg, Mapping) else None
        if isinstance(candidate, list) and candidate:
            return [str(name) for name in candidate if name]
        return None

    def _build_descriptions_for(self, names):
        try:
            import importlib
            import sys

            registry_module = importlib.import_module("plugins_func.register")
            if not hasattr(registry_module, "all_function_registry"):
                sys.modules.pop("plugins_func.register", None)
                registry_module = importlib.import_module("plugins_func.register")
            all_function_registry = registry_module.all_function_registry
        except Exception:
            return None
        # Only the exit intent is force-included: it is part of the curated child base
        # toolset, so Live's tool surface stays == the classic child toolset minus the
        # music tools (test_live_and_classic_share_child_product_toolset_modulo_music).
        # get_lunar is NOT a child product tool and must not be exposed to Live alone.
        necessary = {"handle_exit_intent"}
        wanted = []
        seen = set()
        for name in list(names) + list(necessary):
            if name in seen:
                continue
            seen.add(name)
            wanted.append(name)

        module_aliases = {"get_lunar": "get_time"}
        for name in wanted:
            if name in all_function_registry:
                continue
            module_name = module_aliases.get(name, name)
            try:
                module = importlib.import_module(f"plugins_func.functions.{module_name}")
                if name not in all_function_registry:
                    importlib.reload(module)
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live failed to import live tool {}: {}",
                    name,
                    self._safe_error_message(exc),
                )

        plugin_overrides = {}
        plugins_cfg = self.conn.config.get("plugins", {}) if isinstance(self.conn.config, Mapping) else {}
        if isinstance(plugins_cfg, Mapping):
            for plugin_name, plugin_value in plugins_cfg.items():
                description = None
                if isinstance(plugin_value, Mapping):
                    description = plugin_value.get("description")
                elif isinstance(plugin_value, str):
                    try:
                        parsed = json.loads(plugin_value)
                        if isinstance(parsed, dict):
                            description = parsed.get("description")
                    except Exception:
                        description = None
                if description:
                    plugin_overrides[plugin_name] = description

        descriptions = []
        missing = []
        for name in wanted:
            item = all_function_registry.get(name)
            if item is None:
                missing.append(name)
                continue
            description = self._clone_description(item.description)
            override = plugin_overrides.get(name)
            if override and isinstance(description, Mapping):
                function_def = description.get("function")
                if isinstance(function_def, dict):
                    function_def["description"] = override
            descriptions.append(description)
        if missing:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live skipped unregistered live tools: {}", ",".join(missing)
            )
        return descriptions

    @staticmethod
    def _clone_description(description):
        try:
            import copy

            return copy.deepcopy(description)
        except Exception:
            return description

    _NON_RETRIABLE_ERROR_CLASSES = frozenset(
        {"auth", "quota", "invalid_config"}
    )

    async def _try_reconnect(self, exc):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_RECONNECT
        ) as allowed:
            if not allowed:
                return None
            return await self._try_reconnect_with_lease(exc)

    async def _try_reconnect_with_lease(self, exc):
        reconnect_config = self._get_reconnect_config()
        if not reconnect_config.get("enabled"):
            return False
        if self._reconnect_attempts >= reconnect_config["max_retries"]:
            return False
        error_class = self._classify_error(exc)
        will_retry = error_class not in self._NON_RETRIABLE_ERROR_CLASSES
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live classify_error kind={} retry={}",
            error_class,
            "yes" if will_retry else "no",
        )
        if not will_retry:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live skipping reconnect for non-retriable error_class={}",
                error_class,
            )
            return False

        self._reconnecting = True
        self._interaction.transition(InteractionState.RECONNECTING)
        try:
            await self._close_live_resources()
            while self._reconnect_attempts < reconnect_config["max_retries"]:
                self._reconnect_attempts += 1
                attempt_number = self._reconnect_attempts
                backoff_ms = self._get_reconnect_delay_ms(attempt_number)
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live reconnect attempt {} after runtime failure: {}",
                    attempt_number,
                    self._safe_error_message(exc),
                )
                self.conn.logger.bind(tag="GoogleLive").info(
                    "reconnect_started reason={} attempt={} state={}",
                    error_class,
                    attempt_number,
                    self._interaction.state.value,
                )
                if backoff_ms > 0:
                    await asyncio.sleep(backoff_ms / 1000.0)
                if self._closing:
                    return False
                try:
                    # Account each reconnect against the live-admission gate BEFORE
                    # re-opening Live, so reconnect storms count toward the device budget.
                    await self._record_reconnect_attempt()
                    await self._open_live_session()
                    await self._forward_pending_reconnect_audio()
                    self._reconnect_attempts = 0
                    self.conn.voice_provider = self
                    self.conn.logger.bind(tag="GoogleLive").info(
                        "Google Live reconnect attempt {} succeeded",
                        attempt_number,
                    )
                    self.conn.logger.bind(tag="GoogleLive").info(
                        "reconnect_succeeded attempt={} live_connection_id={}",
                        attempt_number,
                        self._interaction.live_connection_id,
                    )
                    return True
                except Exception as reconnect_exc:
                    await self._close_live_resources()
                    reconnect_error_class = self._classify_error(reconnect_exc)
                    self.conn.logger.bind(tag="GoogleLive").warning(
                        "Google Live reconnect attempt {} failed: {}",
                        attempt_number,
                        self._safe_error_message(reconnect_exc),
                    )
                    self.conn.logger.bind(tag="GoogleLive").warning(
                        "reconnect_failed attempt={} error_class={}",
                        attempt_number,
                        reconnect_error_class,
                    )
            return False
        finally:
            self._reconnecting = False

    async def _forward_pending_reconnect_audio(self):
        if self._bridge is None or not self._pending_reconnect_audio:
            self._pending_reconnect_audio.clear()
            return
        replay_frames = 0
        replay_bytes = 0
        while self._pending_reconnect_audio:
            item = self._pending_reconnect_audio.popleft()
            if isinstance(item, tuple) and len(item) == 2:
                buffered_response_id, packet = item
                if buffered_response_id != self._response_generation:
                    self.conn.logger.bind(tag="GoogleLive").info(
                        "reconnect_replay_skipped reason=stale_turn buffered_response_id={} current_response_id={}",
                        buffered_response_id,
                        self._response_generation,
                    )
                    continue
            else:
                packet = item
            if not packet:
                continue
            replay_frames += 1
            replay_bytes += len(packet)
            decoded_audio = None
            if hasattr(self._bridge, "decode_input_audio_async"):
                decoded_audio = await self._bridge.decode_input_audio_async(packet)
            elif hasattr(self._bridge, "decode_input_audio"):
                decoded_audio = self._bridge.decode_input_audio(packet)
            if decoded_audio is not None and hasattr(
                self._bridge, "forward_decoded_input_audio"
            ):
                await self._bridge.forward_decoded_input_audio(decoded_audio)
                continue
            await self._bridge.forward_input_audio(packet)
        if replay_frames:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live replayed_buffered_audio frames={} bytes={}",
                replay_frames,
                replay_bytes,
            )
        self._schedule_input_flush()

    def _get_interrupt_replay_buffer_capacity(self):
        config = self._get_live_config()
        frame_ms = self._get_input_frame_duration_sec() * 1000
        try:
            budget_ms = float(config.get("interrupt_replay_buffer_ms", 900))
        except (TypeError, ValueError):
            budget_ms = 900.0
        if budget_ms <= 0 or frame_ms <= 0:
            return 1
        return max(1, int(budget_ms / frame_ms))

    def _buffer_pending_interrupt_audio(self, decoded_audio):
        if not decoded_audio:
            return False
        if self._pending_interrupt_audio_response_id != self._response_generation:
            self._pending_interrupt_audio.clear()
            self._pending_interrupt_audio_response_id = self._response_generation
        self._pending_interrupt_audio.append(decoded_audio)
        self.conn.logger.bind(tag="GoogleLive").info(
            "user_speech_pending_replay frames={} bytes={}",
            len(self._pending_interrupt_audio),
            sum(len(f) for f in self._pending_interrupt_audio),
        )
        self._record_interrupt_capture_audio(decoded_audio)
        return True

    def _buffer_pending_interrupt_audio_while_blocked(self, decoded_audio):
        if self._pending_interrupt_audio_response_id != self._response_generation:
            return False
        if self._bridge is None or not hasattr(self._bridge, "is_model_output_blocked"):
            return False
        try:
            if not self._bridge.is_model_output_blocked():
                return False
        except Exception:
            return False
        return self._buffer_pending_interrupt_audio(decoded_audio)

    def _should_hold_interrupt_audio(self, decoded_audio):
        if self._interrupt_capture_response_id != self._response_generation:
            return False
        if self._bridge is None or not hasattr(self._bridge, "is_model_output_blocked"):
            return False
        try:
            if not self._bridge.is_model_output_blocked():
                return False
        except Exception:
            return False
        self._buffer_pending_interrupt_audio(decoded_audio)
        return True

    async def _on_model_output_unblocked(self):
        if self._interrupt_capture_response_id == self._response_generation:
            return
        await self._replay_pending_interrupt_audio("model_output_unblocked")

    async def _replay_pending_interrupt_audio(self, reason):
        if self._bridge is None or not self._pending_interrupt_audio:
            if self._bridge is None:
                self.conn.logger.bind(tag="GoogleLive").info("replay_skipped reason=bridge_none")
            else:
                self.conn.logger.bind(tag="GoogleLive").info("replay_skipped reason=empty_queue")
            self._pending_interrupt_audio.clear()
            self._pending_interrupt_audio_response_id = None
            return
        if self._pending_interrupt_audio_response_id != self._response_generation:
            self.conn.logger.bind(tag="GoogleLive").info(
                "replay_skipped reason=response_id_drift expected={} actual={} frames={}",
                self._pending_interrupt_audio_response_id,
                self._response_generation,
                len(self._pending_interrupt_audio),
            )
            self._pending_interrupt_audio.clear()
            self._pending_interrupt_audio_response_id = None
            return
        if self._interrupt_replayed_once:
            self.conn.logger.bind(tag="GoogleLive").info("replay_skipped reason=already_replayed")
            self._pending_interrupt_audio.clear()
            self._pending_interrupt_audio_response_id = None
            return
        replay_frames = 0
        replay_bytes = 0
        while self._pending_interrupt_audio:
            pcm_audio = self._pending_interrupt_audio.popleft()
            if not pcm_audio:
                continue
            replay_frames += 1
            replay_bytes += len(pcm_audio)
            if hasattr(self._bridge, "forward_decoded_input_audio"):
                await self._bridge.forward_decoded_input_audio(pcm_audio)
        self._pending_interrupt_audio_response_id = None
        if replay_frames:
            self._interrupt_replayed_once = True
            self._interrupt_forwarded_once = True
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live replayed_interrupt_audio reason={} frames={} bytes={} response_id={}",
                reason,
                replay_frames,
                replay_bytes,
                self._response_generation,
            )
            self._schedule_input_flush()
            if reason != "interrupt_finalized":
                self._schedule_forced_interrupt_input_flush("interrupt_replay")

    def _start_interrupt_capture_turn(self, reason):
        now = time.monotonic()
        self._interrupt_capture_response_id = self._response_generation
        self._interrupt_capture_reason = reason
        self._interrupt_capture_started_at = now
        self._interrupt_capture_last_speech_at = now
        self._interrupt_capture_frames = 0
        self._interrupt_capture_bytes = 0
        self._interrupt_capture_peak_rms = 0
        self._interrupt_replayed_once = False
        self._interrupt_forwarded_once = False
        self._cancel_forced_interrupt_flush_task()

    def _record_interrupt_capture_audio(self, decoded_audio):
        if self._interrupt_capture_response_id != self._response_generation:
            return
        if not decoded_audio:
            return
        self._interrupt_capture_frames += 1
        self._interrupt_capture_bytes += len(decoded_audio)
        rms = self._input_rms(decoded_audio)
        if isinstance(rms, (int, float)):
            self._interrupt_capture_peak_rms = max(
                self._interrupt_capture_peak_rms,
                int(rms),
            )
            if rms >= self._get_interrupt_speech_rms_threshold():
                self._interrupt_capture_last_speech_at = time.monotonic()
        self._schedule_forced_interrupt_input_flush("speech_tail")

    def _input_rms(self, decoded_audio):
        if self._bridge is None or not hasattr(self._bridge, "input_rms"):
            return None
        try:
            return self._bridge.input_rms(decoded_audio)
        except Exception:
            return None

    def _get_reconnect_buffer_capacity(self):
        config = self._get_live_config()
        frame_ms = self._get_input_frame_duration_sec() * 1000
        try:
            budget_ms = float(config.get("reconnect_buffer_ms", 2000))
        except (TypeError, ValueError):
            budget_ms = 2000.0
        if budget_ms <= 0 or frame_ms <= 0:
            return 1
        return max(1, int(budget_ms / frame_ms))

    def _schedule_input_flush(self):
        flush_delay = self._get_input_flush_delay()
        if flush_delay is None:
            return

        self._cancel_input_flush_task()
        self._input_flush_generation += 1
        generation = self._input_flush_generation
        self._input_flush_task = asyncio.create_task(
            self._flush_input_after_idle(flush_delay, generation)
        )

    async def _finalize_user_audio_input(self, reason):
        self._cancel_input_flush_task()
        # Idempotency guard: a redundant finalize (late listen_stop / idle-flush
        # race) arriving while already WAITING_MODEL must not re-send
        # end_audio_stream nor re-stamp _waiting_model_since (which would extend
        # the dead-mic window). Skip before touching the client.
        if self._interaction.state == InteractionState.WAITING_MODEL:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live input_finalization_skipped reason={} already_waiting",
                reason,
            )
            return
        if self._client is None or not hasattr(self._client, "end_audio_stream"):
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live input_finalization_skipped reason={} client=missing",
                reason,
            )
            return
        if hasattr(self._client, "connected") and not self._client.connected:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live input_finalization_skipped reason={} client=disconnected",
                reason,
            )
            return
        if self._bridge is not None and hasattr(self._bridge, "flush_pending_input_audio"):
            await self._bridge.flush_pending_input_audio()
        await self._client.end_audio_stream()
        if self._complete_lesson_child_audio_finalization(reason):
            return
        if self._complete_lesson_audio_without_model_wait(reason):
            return
        self._interaction.transition(InteractionState.WAITING_MODEL)
        self._waiting_model_since = time.monotonic()
        self._schedule_waiting_model_timeout_task()
        self._schedule_start_lesson_asr_fallback_task()
        self._clear_user_stream()
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live input_finalized reason={} state={} response_id={}",
            reason,
            self._interaction.state.value,
            self._response_generation,
        )

    def _clear_user_stream(self):
        self._user_stream_started_at = None
        self._user_stream_response_id = None
        self._user_stream_last_speech_at = None
        self._user_stream_frames = 0
        self._last_clean_user_turn_response_id = None

    def _record_user_stream_audio(self, decoded_audio):
        now = time.monotonic()
        if self._user_stream_started_at is None:
            self._user_stream_started_at = now
            self._user_stream_last_speech_at = now
            self._user_stream_response_id = self._response_generation
            self._user_stream_frames = 0
        self._user_stream_frames += 1
        threshold = self._get_user_speech_rms_threshold()
        if threshold is not None and decoded_audio:
            rms = self._input_rms(decoded_audio)
            if isinstance(rms, (int, float)) and rms >= threshold:
                self._user_stream_last_speech_at = now

    def _clean_turn_finalizer_enabled(self):
        config = self._get_live_config()
        if not isinstance(config, Mapping):
            return False
        return any(
            config.get(key) is not None
            for key in (
                "input_min_capture_ms",
                "input_speech_tail_ms",
                "input_max_capture_ms",
            )
        )

    def _user_turn_can_finalize(self):
        if not self._clean_turn_finalizer_enabled():
            return False
        started_at = self._user_stream_started_at
        if started_at is None:
            return False
        last_speech_at = self._user_stream_last_speech_at or started_at
        now = time.monotonic()
        max_capture = self._get_user_max_capture_sec()
        if max_capture is not None and now - started_at >= max_capture:
            return True
        min_capture = self._get_user_min_capture_sec() or 0.0
        if now - started_at < min_capture:
            return False
        tail = self._get_user_speech_tail_sec()
        if tail is None:
            return False
        return now - last_speech_at >= tail

    async def _finalize_user_turn_clean(self):
        self._cancel_input_flush_task()
        if self._bridge is not None and hasattr(self._bridge, "flush_pending_input_audio"):
            await self._bridge.flush_pending_input_audio()
        if self._client is not None and hasattr(self._client, "end_audio_stream"):
            await self._client.end_audio_stream()
        if self._complete_lesson_child_audio_finalization("clean_turn"):
            return
        if self._complete_lesson_audio_without_model_wait("clean_turn"):
            return
        self._interaction.transition(InteractionState.WAITING_MODEL)
        self._waiting_model_since = time.monotonic()
        self._schedule_waiting_model_timeout_task()
        self._schedule_start_lesson_asr_fallback_task()
        self._clear_user_stream()
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_turn_finalized state={} response_id={}",
            self._interaction.state.value,
            self._response_generation,
        )

    def _get_user_min_capture_sec(self):
        config = self._get_live_config()
        value = config.get("input_min_capture_ms") if isinstance(config, Mapping) else None
        if value is None:
            return None
        try:
            return max(0.0, float(value) / 1000.0)
        except (TypeError, ValueError):
            return None

    def _get_user_speech_tail_sec(self):
        config = self._get_live_config()
        value = None
        if isinstance(config, Mapping):
            if self._uses_lesson_input_timing():
                # Short single-word answers should finalize faster than full phrases.
                if self._lesson_child_response_window_active(require_audio_window=False):
                    value = config.get(
                        "lesson_child_input_speech_tail_ms",
                        config.get("input_speech_tail_ms"),
                    )
                else:
                    value = config.get("input_speech_tail_ms")
            else:
                value = self._conversation_timing_value(
                    config,
                    "conversation_input_speech_tail_ms",
                    "input_speech_tail_ms",
                )
        if value is None:
            return None
        try:
            return max(0.0, float(value) / 1000.0)
        except (TypeError, ValueError):
            return None

    def _get_user_max_capture_sec(self):
        config = self._get_live_config()
        value = None
        if isinstance(config, Mapping):
            if self._uses_lesson_input_timing():
                if self._lesson_child_response_window_active(require_audio_window=False):
                    value = config.get(
                        "lesson_child_input_max_capture_ms",
                        config.get("input_max_capture_ms"),
                    )
                else:
                    value = config.get("input_max_capture_ms")
            else:
                value = self._conversation_timing_value(
                    config,
                    "conversation_input_max_capture_ms",
                    "input_max_capture_ms",
                )
        if value is None:
            return None
        try:
            return max(0.0, float(value) / 1000.0)
        except (TypeError, ValueError):
            return None

    def _get_user_speech_rms_threshold(self):
        config = self._get_live_config()
        value = (
            config.get("input_speech_rms_threshold") if isinstance(config, Mapping) else None
        )
        if value is None:
            return None
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return None

    def _get_lesson_child_speech_rms_threshold(self):
        config = self._get_live_config()
        if not isinstance(config, Mapping):
            return None
        value = config.get(
            "lesson_child_input_speech_rms_threshold",
            config.get("input_speech_rms_threshold"),
        )
        if value is None:
            return None
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return None

    def _lesson_child_audio_is_speech(self, rms):
        threshold = self._get_lesson_child_speech_rms_threshold()
        if threshold is None:
            return True
        return isinstance(rms, (int, float)) and rms >= threshold

    def _should_drop_conversation_start_noise(self, decoded_audio):
        if decoded_audio is None:
            return False
        if self._user_stream_started_at is not None:
            return False
        if self._interrupt_capture_response_id == self._response_generation:
            return False
        if self._uses_lesson_input_timing():
            return False
        if self._has_active_output():
            return False
        threshold = self._get_user_speech_rms_threshold()
        if threshold is None:
            return False
        rms = self._input_rms(decoded_audio)
        return isinstance(rms, (int, float)) and rms < threshold

    def _lesson_child_speech_start_frames_to_forward(self, decoded, rms):
        if not self._lesson_child_audio_is_speech(rms):
            self._clear_lesson_child_speech_start_frames()
            return None
        self._lesson_child_speech_start_frames.append(decoded)
        frames = list(self._lesson_child_speech_start_frames)
        self._clear_lesson_child_speech_start_frames()
        return frames

    def _clear_lesson_child_speech_start_frames(self):
        self._lesson_child_speech_start_frames.clear()

    def _get_waiting_model_timeout_sec(self):
        config = self._get_live_config()
        value = config.get("waiting_model_timeout_sec") if isinstance(config, Mapping) else None
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        return value

    def _get_waiting_model_retry_prompt_after_sec(self):
        config = self._get_live_config()
        value = (
            config.get("waiting_model_retry_prompt_after_sec")
            if isinstance(config, Mapping)
            else None
        )
        if value is None:
            return 0.0
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def _get_waiting_model_retry_prompt_cooldown_sec(self):
        config = self._get_live_config()
        value = (
            config.get("waiting_model_retry_prompt_cooldown_sec")
            if isinstance(config, Mapping)
            else None
        )
        if value is None:
            return 0.0
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def _waiting_model_release_due(self):
        if self._waiting_model_since is None:
            return False
        timeout = self._get_waiting_model_timeout_sec()
        if timeout is None:
            return False
        return (time.monotonic() - self._waiting_model_since) >= timeout

    def _waiting_model_retry_audio_can_resume(self, decoded_audio):
        if self._waiting_model_since is None:
            return False
        if time.monotonic() - self._waiting_model_since < self._WAITING_MODEL_RETRY_AUDIO_GRACE_SEC:
            return False
        if self._has_active_output() or self._has_music_session():
            return False
        if self._is_post_reply_hold_active():
            return False
        # Require stronger than ambient/residual so echo does not steal the turn.
        return self._is_strong_user_speech(decoded_audio, multiplier=1.8)

    def _record_start_lesson_asr_fallback_audio(self, audio_bytes, decoded_audio):
        if not self._start_lesson_asr_fallback_enabled():
            return
        threshold = self._get_user_speech_rms_threshold()
        if decoded_audio is not None and threshold is not None:
            rms = self._input_rms(decoded_audio)
            if not (isinstance(rms, (int, float)) and rms >= threshold):
                return
        if audio_bytes:
            self._start_lesson_asr_fallback_audio.append(audio_bytes)

    def _start_lesson_asr_fallback_enabled(self):
        if self._start_lesson_asr_fallback_disabled:
            return False
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON:
            return False
        try:
            return "start_lesson" in product_tool_names(self.conn)
        except Exception:
            return False

    def _get_start_lesson_asr_fallback_delay_sec(self):
        config = self._get_live_config()
        value = (
            config.get("lesson_start_asr_fallback_delay_sec")
            if isinstance(config, Mapping)
            else None
        )
        if value is None:
            return 0.8
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.8

    def _schedule_start_lesson_asr_fallback_task(self):
        if not self._start_lesson_asr_fallback_audio:
            return
        if not self._start_lesson_asr_fallback_enabled():
            self._start_lesson_asr_fallback_audio.clear()
            return
        self._cancel_start_lesson_asr_fallback_task()
        self._start_lesson_asr_fallback_generation += 1
        generation = self._start_lesson_asr_fallback_generation
        frames = list(self._start_lesson_asr_fallback_audio)
        delay = self._get_start_lesson_asr_fallback_delay_sec()
        self._start_lesson_asr_fallback_task = asyncio.create_task(
            self._run_start_lesson_asr_fallback(delay, generation, frames)
        )

    async def _run_start_lesson_asr_fallback(self, delay, generation, frames):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if generation != self._start_lesson_asr_fallback_generation:
                return
            if self._closing or self._has_active_output():
                return
            asr = self._get_start_lesson_asr_provider()
            if asr is None or not hasattr(asr, "speech_to_text_wrapper"):
                return
            text, _file_path = await asr.speech_to_text_wrapper(
                frames,
                getattr(self.conn, "session_id", ""),
                getattr(self.conn, "audio_format", "opus"),
            )
            if self._is_asr_auth_failure(getattr(asr, "last_error", "")):
                self._start_lesson_asr_fallback_disabled = True
                self._start_lesson_asr_fallback_audio.clear()
                self.conn.logger.bind(tag="GoogleLive").warning(
                    with_lesson_log_context(
                        "Google Live lesson_start_asr_fallback disabled reason=asr_auth_failure",
                        self.conn,
                    )
                )
                return
            transcript = self._extract_asr_transcript_text(text)
            if not transcript:
                return
            if self._classify_lesson_start_intent(transcript) is None:
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live lesson_start_asr_fallback miss chars={}",
                        self.conn,
                    ),
                    len(transcript),
                )
                return
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_start_asr_fallback transcript chars={}",
                    self.conn,
                ),
                len(transcript),
            )
            self._start_lesson_asr_fallback_audio.clear()
            await self._dispatch_lesson_start_intent(transcript)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._is_asr_auth_failure(exc):
                self._start_lesson_asr_fallback_disabled = True
                self._start_lesson_asr_fallback_audio.clear()
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_start_asr_fallback failed: {}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )

    def _is_asr_auth_failure(self, exc):
        message = str(exc).lower()
        return (
            "401" in message
            or "unauthorized" in message
            or "invalid api key" in message
            or "invalid_api_key" in message
        )

    def _get_start_lesson_asr_provider(self):
        asr = getattr(self.conn, "asr", None)
        if asr is not None:
            return asr
        initializer = getattr(self.conn, "_initialize_asr", None)
        if not callable(initializer):
            return None
        try:
            asr = initializer()
            self.conn.asr = asr
            return asr
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_start_asr_fallback init failed: {}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )
            return None

    def _extract_asr_transcript_text(self, value):
        if isinstance(value, Mapping):
            value = value.get("content") or value.get("text") or ""
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    payload = json.loads(stripped)
                    if isinstance(payload, Mapping):
                        value = payload.get("content") or payload.get("text") or stripped
                except json.JSONDecodeError:
                    value = stripped
        return str(value or "").strip()

    def _schedule_waiting_model_timeout_task(self):
        self._cancel_waiting_model_timeout_task()
        timeout = self._get_waiting_model_timeout_sec()
        if timeout is None:
            return
        self._waiting_model_timeout_task = asyncio.create_task(
            self._release_waiting_model_after_timeout(timeout)
        )

    async def _release_waiting_model_after_timeout(self, timeout):
        try:
            await asyncio.sleep(timeout)
            if self._closing:
                return
            if self._interaction.state != InteractionState.WAITING_MODEL:
                return
            if not self._waiting_model_release_due():
                return
            if self._lesson_conversation_tool_path_active():
                handled = await self._handle_lesson_live_interruption("timeout")
                if handled:
                    self._interaction.transition(InteractionState.IDLE)
                    self._waiting_model_since = None
                    self.conn.client_abort = False
                    return
            self._consecutive_waiting_model_timeouts += 1
            if await self._reopen_silent_live_session_after_timeouts(timeout):
                return
            await self._send_user_audio_window_expired_feedback()
            self._interaction.transition(InteractionState.IDLE)
            self._waiting_model_since = None
            self.conn.client_abort = False
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live waiting_model_timeout released_without_audio timeout_sec={}",
                timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    async def _reopen_silent_live_session_after_timeouts(self, timeout):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_RECONNECT
        ) as allowed:
            if not allowed:
                return False
            return await self._reopen_silent_live_session_after_timeouts_with_lease(
                timeout
            )

    async def _reopen_silent_live_session_after_timeouts_with_lease(
        self, timeout
    ):
        if self._consecutive_waiting_model_timeouts < self._SILENT_LIVE_REOPEN_TIMEOUTS:
            return False
        if self._client is None:
            return False
        now = time.monotonic()
        if (
            self._last_silent_live_reopen_at
            and now - self._last_silent_live_reopen_at < self._SILENT_LIVE_REOPEN_COOLDOWN_SEC
        ):
            self._consecutive_waiting_model_timeouts = 0
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live silent_session_reopen_suppressed cooldown_sec={} timeout_sec={}",
                self._SILENT_LIVE_REOPEN_COOLDOWN_SEC,
                timeout,
            )
            return False
        self._consecutive_waiting_model_timeouts = 0
        self._waiting_model_since = None
        self.conn.client_abort = False
        self.conn.google_live_session_resumption_handle = None
        self.conn.logger.bind(tag="GoogleLive").warning(
            "Google Live silent_session_reopen reason=waiting_model_timeout streak={} timeout_sec={}",
            self._SILENT_LIVE_REOPEN_TIMEOUTS,
            timeout,
        )
        self._last_silent_live_reopen_at = now
        async with self._get_live_open_lock():
            if self._client is None:
                return False
            self._reconnecting = True
            self._interaction.transition(InteractionState.RECONNECTING)
            try:
                await self._record_reconnect_attempt()
                await self._close_live_resources()
                await self._open_live_session_locked(restore_session_resumption=False)
                await self._forward_pending_reconnect_audio()
                self._interaction.transition(InteractionState.LISTENING)
                self._waiting_model_since = None
                self.conn.client_abort = False
                return True
            finally:
                self._reconnecting = False

    async def _maybe_queue_waiting_model_retry_prompt(self):
        if self._waiting_model_since is None:
            return
        elapsed = time.monotonic() - self._waiting_model_since
        if elapsed < self._get_waiting_model_retry_prompt_after_sec():
            return
        cooldown = self._get_waiting_model_retry_prompt_cooldown_sec()
        now = time.monotonic()
        if (
            self._last_waiting_model_retry_prompt_at
            and now - self._last_waiting_model_retry_prompt_at < cooldown
        ):
            return
        self._last_waiting_model_retry_prompt_at = now
        await self._send_live_text_ack(
            "Robot chưa nghe rõ, con nói lại nhé.",
            log_label="waiting_model_timeout",
            allow_lesson_output=True,
        )

    async def _handle_live_event(self, event):
        """Pre-bridge hook for live events. Model output cancels the no-response
        timeout; audio_end reopens the mic after the model finishes its turn."""
        event_type = event.get("type") if isinstance(event, Mapping) else None
        if event_type is not None:
            self._touch_live_activity()
        if (
            getattr(self.conn, "google_live_lesson_prompt_output_allowed", False)
            and self._is_model_output_event(event_type, event)
        ):
            self._lesson_prompt_output_last_activity_at = time.monotonic()
        if self._is_model_output_event(event_type, event):
            self._consecutive_waiting_model_timeouts = 0
        if event_type == "audio_start":
            self._cancel_start_lesson_asr_fallback_task()
            self._start_lesson_asr_fallback_audio.clear()
            self._cancel_waiting_model_timeout_task()
            self._cancel_input_flush_task()
            self._cancel_forced_interrupt_flush_task()
            self._clear_user_stream()
            self._waiting_model_since = None
            # Residual mic from waiting_model_retry must not keep streaming into Live
            # once the model starts speaking — that path was cutting replies mid-sentence.
            if self._interaction.state == InteractionState.USER_STREAMING:
                self._interaction.transition(InteractionState.LISTENING)
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live model_audio_start_hold_input response_id={}",
                self._response_generation,
            )
        if event_type == "audio_end":
            self._cancel_waiting_model_timeout_task()
            self._cancel_input_flush_task()
            self._clear_user_stream()
            self._waiting_model_since = None
            self._arm_post_reply_hold("audio_end")
            # Always re-open listen after model speech so the next user turn is heard.
            if self._interaction.state in (
                InteractionState.WAITING_MODEL,
                InteractionState.USER_STREAMING,
                InteractionState.INTERRUPTING,
            ):
                self._interaction.transition(InteractionState.LISTENING)
            self.conn.client_abort = False
            try:
                # Refresh conversation listen window without forcing another Hi ESP.
                self._user_audio_allowed_until = max(
                    self._user_audio_allowed_until,
                    time.monotonic() + self._get_user_audio_window_sec("audio_end"),
                )
            except Exception:
                pass
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live model_audio_end_ready_to_listen response_id={}",
                self._response_generation,
            )

    def _is_model_output_event(self, event_type, event):
        if event_type in {"audio_start", "audio", "audio_chunk", "audio_end", "tool_call"}:
            return True
        return (
            event_type == "transcript"
            and isinstance(event, Mapping)
            and event.get("source") == "model"
        )

    def _schedule_forced_interrupt_input_flush(self, reason):
        flush_delay = self._get_interrupt_finalization_delay()
        if flush_delay is None:
            return
        self._cancel_forced_interrupt_flush_task()
        self._forced_interrupt_flush_generation += 1
        generation = self._forced_interrupt_flush_generation
        response_id = self._response_generation
        self._forced_interrupt_flush_task = asyncio.create_task(
            self._flush_interrupt_input_after_delay(
                flush_delay,
                generation,
                response_id,
                reason,
            )
        )

    async def _flush_interrupt_input_after_delay(
        self,
        flush_delay,
        generation,
        response_id,
        reason,
    ):
        try:
            await asyncio.sleep(flush_delay)
            if generation != self._forced_interrupt_flush_generation:
                return
            if response_id != self._response_generation:
                return
            if self._closing:
                return
            if not self._interrupt_input_can_finalize():
                self._schedule_forced_interrupt_input_flush(reason)
                return
            if self._client is None or not hasattr(self._client, "end_audio_stream"):
                return
            if hasattr(self._client, "connected") and not self._client.connected:
                return
            await self._replay_pending_interrupt_audio("interrupt_finalized")
            if self._bridge is not None and hasattr(self._bridge, "flush_pending_input_audio"):
                await self._bridge.flush_pending_input_audio()
            await self._client.end_audio_stream()
            if self._bridge is not None and hasattr(self._bridge, "allow_model_output"):
                self._bridge.allow_model_output()
            self._interaction.transition(InteractionState.WAITING_MODEL)
            self._waiting_model_since = time.monotonic()
            self._schedule_waiting_model_timeout_task()
            elapsed_ms = self._interrupt_capture_elapsed_ms()
            self.conn.logger.bind(tag="GoogleLive").info(
                "interrupt_capture_finalized frames={} duration_ms={:.0f}",
                self._interrupt_capture_frames,
                elapsed_ms,
            )
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live interrupt_input_finalized reason={} elapsed_ms={:.0f} response_id={} frames={} bytes={} peak_rms={}",
                reason,
                elapsed_ms,
                response_id,
                self._interrupt_capture_frames,
                self._interrupt_capture_bytes,
                self._interrupt_capture_peak_rms,
            )
            self._clear_interrupt_capture_turn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    async def _flush_input_after_idle(self, flush_delay, generation):
        try:
            await asyncio.sleep(flush_delay)
            if generation != self._input_flush_generation:
                return
            if self._closing:
                return
            if self._client is None or not hasattr(self._client, "end_audio_stream"):
                return
            if hasattr(self._client, "connected") and not self._client.connected:
                return
            if self._bridge is not None and hasattr(self._bridge, "flush_pending_input_audio"):
                await self._bridge.flush_pending_input_audio()
            await self._client.end_audio_stream()
            if self._complete_lesson_child_audio_finalization("idle_flush"):
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live input stream flushed after {:.0f} ms idle",
                    flush_delay * 1000,
                )
                return
            if self._complete_lesson_audio_without_model_wait("idle_flush"):
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live input stream flushed after {:.0f} ms idle",
                    flush_delay * 1000,
                )
                return
            # The idle-flush safety-net must be a COMPLETE finalize: transition to
            # WAITING_MODEL, stamp the wait, and clear the per-turn user-stream
            # bookkeeping so the next utterance is not truncated by a stale
            # _user_stream_started_at.
            self._interaction.transition(InteractionState.WAITING_MODEL)
            self._waiting_model_since = time.monotonic()
            self._schedule_waiting_model_timeout_task()
            self._clear_user_stream()
            if (
                not self._lesson_conversation_tool_path_active()
                and self._lesson_child_response_window_active(require_audio_window=False)
            ):
                self._lesson_child_audio_pending_transcript = True
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live input stream flushed after {:.0f} ms idle",
                flush_delay * 1000,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    def _cancel_input_flush_task(self):
        self._input_flush_generation += 1
        existing_task = self._input_flush_task
        self._input_flush_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _cancel_forced_interrupt_flush_task(self):
        self._forced_interrupt_flush_generation += 1
        existing_task = self._forced_interrupt_flush_task
        self._forced_interrupt_flush_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _cancel_waiting_model_timeout_task(self):
        existing_task = self._waiting_model_timeout_task
        self._waiting_model_timeout_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _cancel_lesson_child_transcript_timeout_task(self):
        existing_task = self._lesson_child_transcript_timeout_task
        self._lesson_child_transcript_timeout_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _cancel_start_lesson_asr_fallback_task(self):
        self._start_lesson_asr_fallback_generation += 1
        existing_task = self._start_lesson_asr_fallback_task
        self._start_lesson_asr_fallback_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _cancel_user_audio_window_task(self):
        self._user_audio_window_generation += 1
        existing_task = self._user_audio_window_task
        self._user_audio_window_task = None
        if existing_task is not None:
            existing_task.cancel()

    def _complete_lesson_child_audio_finalization(self, reason):
        if self._lesson_conversation_tool_path_active():
            return False
        if not self._lesson_child_response_window_active(require_audio_window=False):
            return False
        self._cancel_waiting_model_timeout_task()
        self._waiting_model_since = None
        self._lesson_child_audio_pending_transcript = True
        self._clear_lesson_child_speech_start_frames()
        self._schedule_lesson_child_transcript_timeout_task()
        self._interaction.transition(InteractionState.LISTENING)
        self._clear_user_stream()
        self.conn.client_abort = False
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_child_audio_finalized reason={} state={} response_id={}",
                self.conn,
            ),
            reason,
            self._interaction.state.value,
            self._response_generation,
        )
        return True

    def _get_lesson_child_transcript_timeout_sec(self):
        config = self._get_live_config()
        value = (
            config.get("lesson_child_transcript_timeout_sec")
            if isinstance(config, Mapping)
            else None
        )
        try:
            timeout = float(value) if value is not None else 3.0
        except (TypeError, ValueError):
            timeout = 3.0
        if timeout < 0:
            return None
        return max(0.01, timeout)

    def _schedule_lesson_child_transcript_timeout_task(self):
        self._cancel_lesson_child_transcript_timeout_task()
        timeout = self._get_lesson_child_transcript_timeout_sec()
        if timeout is None:
            return
        self._lesson_child_transcript_timeout_task = asyncio.create_task(
            self._release_lesson_child_pending_transcript_after_timeout(timeout)
        )

    async def _release_lesson_child_pending_transcript_after_timeout(self, timeout):
        try:
            await asyncio.sleep(timeout)
            if self._closing or not self._lesson_child_audio_pending_transcript:
                return
            if self._lesson_conversation_tool_path_active():
                self._lesson_child_audio_pending_transcript = False
                self._clear_lesson_child_speech_start_frames()
                return
            if not self._lesson_child_response_window_active(require_audio_window=False):
                self._lesson_child_audio_pending_transcript = False
                self._clear_lesson_child_speech_start_frames()
                return
            self._lesson_child_audio_pending_transcript = False
            self._clear_lesson_child_speech_start_frames()
            self._lesson_child_transcript_timeout_task = None
            self.conn.client_abort = False
            self._force_lesson_session_mode("lesson_child_transcript_timeout")
            if self._bridge is not None and hasattr(self._bridge, "stop_output"):
                await self._bridge.stop_output()
            reprompted = await self._dispatch_lesson_child_response_failure(
                "stt_unavailable"
            )
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_child_transcript_timeout reopened_audio timeout_sec={} reprompted={}",
                    self.conn,
                ),
                timeout,
                reprompted,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    def _complete_lesson_audio_without_model_wait(self, reason):
        if self._lesson_conversation_tool_path_active():
            return False
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) != SessionMode.LESSON:
            return False
        if not self._lesson_runtime_active():
            return False
        if self._lesson_runtime_accepts_voice_input():
            self._lesson_child_audio_pending_transcript = True
            self._schedule_lesson_child_transcript_timeout_task()
        self._cancel_waiting_model_timeout_task()
        self._waiting_model_since = None
        self._interaction.transition(InteractionState.LISTENING)
        self._clear_user_stream()
        self.conn.client_abort = False
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "Google Live lesson_audio_finalized_no_model reason={} state={} response_id={}",
                self.conn,
            ),
            reason,
            self._interaction.state.value,
            self._response_generation,
        )
        return True

    def _get_input_flush_delay(self):
        config = self._get_live_config()
        if self._uses_lesson_input_timing():
            if self._lesson_child_response_window_active(require_audio_window=False):
                flush_delay = config.get(
                    "lesson_child_input_flush_delay_sec",
                    config.get("input_flush_delay_sec"),
                )
            else:
                flush_delay = config.get("input_flush_delay_sec")
        else:
            flush_delay = self._conversation_timing_value(
                config,
                "conversation_input_flush_delay_sec",
                "input_flush_delay_sec",
            )
        if flush_delay in (None, ""):
            return None
        try:
            flush_delay = float(flush_delay)
        except (TypeError, ValueError):
            return None
        if flush_delay <= 0:
            return None
        return flush_delay

    def _conversation_timing_value(self, merged_config, conversation_key, legacy_key):
        raw_config = self.conn.config.get("google_live", {})
        if not isinstance(raw_config, Mapping):
            raw_config = {}
        if conversation_key in raw_config:
            return raw_config.get(conversation_key)
        if legacy_key in raw_config:
            return raw_config.get(legacy_key)
        value = merged_config.get(conversation_key)
        if value is None:
            value = merged_config.get(legacy_key)
        return value

    def _uses_lesson_input_timing(self):
        if self._lesson_runtime_active():
            return True
        try:
            return normalize_session_mode(
                getattr(self.conn, "session_mode", SessionMode.DORMANT)
            ) == SessionMode.LESSON
        except Exception:
            return False

    def _get_interrupt_forced_flush_delay(self):
        config = self._get_live_config()
        flush_delay = config.get("interrupt_forced_flush_delay_sec", 0.8)
        try:
            flush_delay = float(flush_delay)
        except (TypeError, ValueError):
            return None
        if flush_delay <= 0:
            return None
        return flush_delay

    def _get_interrupt_finalization_delay(self):
        if self._interrupt_capture_response_id != self._response_generation:
            return self._get_interrupt_forced_flush_delay()
        started_at = self._interrupt_capture_started_at
        last_speech_at = self._interrupt_capture_last_speech_at or started_at
        if started_at is None or last_speech_at is None:
            return self._get_interrupt_forced_flush_delay()
        now = time.monotonic()
        min_delay = max(0.0, self._get_interrupt_min_capture_sec() - (now - started_at))
        tail_delay = max(0.0, self._get_interrupt_speech_tail_sec() - (now - last_speech_at))
        max_delay = max(0.0, self._get_interrupt_max_capture_sec() - (now - started_at))
        return min(max(min_delay, tail_delay), max_delay)

    def _interrupt_input_can_finalize(self):
        if self._interrupt_capture_response_id != self._response_generation:
            return True
        started_at = self._interrupt_capture_started_at
        last_speech_at = self._interrupt_capture_last_speech_at or started_at
        if started_at is None or last_speech_at is None:
            return True
        now = time.monotonic()
        if now - started_at >= self._get_interrupt_max_capture_sec():
            return True
        if now - started_at < self._get_interrupt_min_capture_sec():
            return False
        return now - last_speech_at >= self._get_interrupt_speech_tail_sec()

    def _interrupt_capture_elapsed_ms(self):
        if self._interrupt_capture_started_at is None:
            return 0.0
        return (time.monotonic() - self._interrupt_capture_started_at) * 1000

    def _clear_interrupt_capture_turn(self):
        self._interrupt_capture_response_id = None
        self._interrupt_capture_reason = None
        self._interrupt_capture_started_at = None
        self._interrupt_capture_last_speech_at = None
        self._interrupt_capture_frames = 0
        self._interrupt_capture_bytes = 0
        self._interrupt_capture_peak_rms = 0
        self._interrupt_replayed_once = False
        self._interrupt_forwarded_once = False

    def _get_interrupt_min_capture_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("interrupt_min_capture_ms", 360)) / 1000.0
        except (TypeError, ValueError):
            value = 0.36
        return max(0.0, value)

    def _get_interrupt_speech_tail_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("interrupt_speech_tail_ms", 240)) / 1000.0
        except (TypeError, ValueError):
            value = 0.24
        return max(0.0, value)

    def _get_interrupt_max_capture_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("interrupt_max_capture_ms", 1200)) / 1000.0
        except (TypeError, ValueError):
            value = 1.2
        return max(self._get_interrupt_min_capture_sec(), value)

    def _get_interrupt_speech_rms_threshold(self):
        config = self._get_live_config()
        default_threshold = max(
            250,
            int(config.get("robot_output_echo_bypass_rms_threshold", 650) * 0.5),
        )
        try:
            threshold = int(config.get("interrupt_speech_rms_threshold", default_threshold))
        except (TypeError, ValueError):
            threshold = default_threshold
        return max(1, threshold)

    def _get_live_config(self):
        config = self.conn.config.get("google_live", {})
        if not isinstance(config, Mapping):
            config = {}
        try:
            from config.config_loader import GOOGLE_LIVE_DEFAULTS, merge_configs

            merged = merge_configs(GOOGLE_LIVE_DEFAULTS, config)
        except Exception:
            merged = dict(config)
        # Force stable local interruption behaviour while keeping Gemini Live's
        # official activity interruption path enabled. Raw/RMS local barge-in is
        # still disabled; speaker echo is handled by AEC, echo gates, and queue
        # clearing when Live reports serverContent.interrupted.
        merged["model"] = "gemini-3.1-flash-live-preview"
        merged["voice_name"] = "Kore"
        merged["language_code"] = "vi-VN"
        merged["enable_audio_input"] = True
        merged["enable_audio_output"] = True
        merged["native_voice"] = True
        merged["aec_enabled"] = True
        try:
            waiting_timeout = float(merged.get("waiting_model_timeout_sec", 5.0))
        except (TypeError, ValueError):
            waiting_timeout = 5.0
        # Floor 5s for production agent values (>=1s). Keep sub-second timeouts for
        # unit tests; agent private 3s still becomes 5s so tools are not dropped early.
        if waiting_timeout >= 1.0:
            merged["waiting_model_timeout_sec"] = min(max(5.0, waiting_timeout), 6.0)
        else:
            merged["waiting_model_timeout_sec"] = max(0.0, waiting_timeout)
        # Patient end-of-speech so short Vietnamese pauses do not cut turns.
        merged["end_of_speech_sensitivity"] = "END_SENSITIVITY_LOW"
        try:
            silence_ms = float(merged.get("silence_duration_ms", 600))
        except (TypeError, ValueError):
            silence_ms = 600.0
        # 600–800ms: complete enough for STT; under 550 cut mid-phrase (mishears).
        merged["silence_duration_ms"] = max(600.0, min(silence_ms, 800.0))
        # Residual gate after tts:stop — shorter = snappier re-listen.
        try:
            echo_tail = float(merged.get("echo_tail_suppression_ms", 500))
        except (TypeError, ValueError):
            echo_tail = 500.0
        merged["echo_tail_suppression_ms"] = max(400.0, min(echo_tail, 700.0))
        try:
            mute_after = float(merged.get("mute_input_after_audio_start_sec", 0.4))
        except (TypeError, ValueError):
            mute_after = 0.4
        # First model-audio frames: protect AEC converge without long mute.
        merged["mute_input_after_audio_start_sec"] = max(0.28, min(mute_after, 0.6))
        # Long multi-turn: keep listen windows open (floor 120s for production values).
        # Sub-second values stay as-is for unit tests that need quick expiry.
        for key, default in (
            ("wake_audio_allow_window_sec", 900.0),
            ("conversation_audio_allow_window_sec", 900.0),
            ("idle_timeout_sec", 900.0),
        ):
            try:
                value = float(merged.get(key, default))
            except (TypeError, ValueError):
                value = default
            if value >= 1.0:
                merged[key] = max(120.0, min(value, 1800.0))
            else:
                merged[key] = max(0.0, value)
        try:
            min_output_age = float(merged.get("interruption_min_output_age_sec", 0.7))
        except (TypeError, ValueError):
            min_output_age = 0.7
        # Floor 0.7s: false barge-in under ~0.5s; above 1s felt laggy.
        merged["interruption_min_output_age_sec"] = max(0.7, min(min_output_age, 2.0))
        try:
            transcript_min_age = float(
                merged.get("barge_in_transcript_min_output_age_sec", 0.6)
            )
        except (TypeError, ValueError):
            transcript_min_age = 0.6
        merged["barge_in_transcript_min_output_age_sec"] = max(
            0.4, min(transcript_min_age, 2.0)
        )
        merged["disable_server_side_interruptions"] = False
        merged["activity_handling"] = "START_OF_ACTIVITY_INTERRUPTS"
        merged["barge_in"] = False
        merged["interrupt_on_input_while_speaking"] = False
        merged["drop_input_while_speaking"] = False
        merged["server_side_vad_enabled"] = True
        merged["context_window_compression_enabled"] = True
        # Force the RMS-based loud-input bypass interrupt OFF. Production
        # barge-in must come from Google Live VAD over AEC-cleaned input.
        merged["echo_bypass_interrupt_enabled"] = False
        try:
            bypass_threshold = int(
                merged.get("robot_output_echo_bypass_rms_threshold", 650)
            )
        except (TypeError, ValueError):
            bypass_threshold = 650
        merged["robot_output_echo_bypass_rms_threshold"] = min(
            max(1, bypass_threshold),
            650,
        )
        try:
            bypass_min_duration = float(
                merged.get("robot_output_echo_bypass_min_duration_sec", 0.06)
            )
        except (TypeError, ValueError):
            bypass_min_duration = 0.06
        merged["robot_output_echo_bypass_min_duration_sec"] = min(
            max(0.0, bypass_min_duration),
            0.06,
        )
        return merged

    def _get_reconnect_config(self):
        reconnect = self._get_live_config().get("reconnect", {})
        if not isinstance(reconnect, Mapping):
            reconnect = {}

        enabled = reconnect.get("enabled", False)
        max_retries = reconnect.get("max_retries", 0)
        backoff_ms = reconnect.get("backoff_ms", 0)
        backoff_multiplier = reconnect.get("backoff_multiplier", 1)
        try:
            max_retries = int(max_retries)
        except (TypeError, ValueError):
            max_retries = 0
        try:
            backoff_ms = int(backoff_ms)
        except (TypeError, ValueError):
            backoff_ms = 0
        try:
            backoff_multiplier = float(backoff_multiplier)
        except (TypeError, ValueError):
            backoff_multiplier = 1
        return {
            "enabled": bool(enabled) and max_retries > 0,
            "max_retries": max(0, max_retries),
            "backoff_ms": max(0, backoff_ms),
            "backoff_multiplier": max(1, backoff_multiplier),
        }

    def _get_reconnect_delay_ms(self, attempt_number):
        reconnect_config = self._get_reconnect_config()
        base_ms = reconnect_config["backoff_ms"]
        multiplier = reconnect_config["backoff_multiplier"]
        try:
            attempt_index = max(0, int(attempt_number) - 1)
        except (TypeError, ValueError):
            attempt_index = 0
        return int(base_ms * (multiplier ** attempt_index))

    def _classify_error(self, exc):
        message = str(exc).lower()
        if "api key" in message or "unauth" in message or "permission" in message:
            return "auth"
        if "quota" in message or "rate" in message or "429" in message:
            return "quota"
        if "model" in message or "config" in message:
            return "invalid_config"
        if "receive loop ended" in message or "stream" in message:
            return "stream_closed"
        if (
            "timeout" in message
            or "timed out" in message
            or "keepalive" in message
            or "connection" in message
            or "network" in message
            or "closed" in message
        ):
            return "network"
        return "unknown"

    def _safe_error_message(self, exc):
        message = str(exc)
        message = re.sub(r"AIza[0-9A-Za-z_\-]{10,}", "AIza***", message)
        message = re.sub(
            r"(?i)(api[_-]?key|token|authorization|secret)=([^\\s,;]+)",
            r"\\1=***",
            message,
        )
        return message

    def current_response_id(self):
        return self._response_generation

    def is_response_cancelled(self, response_id):
        return response_id in self._cancelled_response_ids

    def _mark_clean_user_turn_opened(self, reason):
        self._interaction.transition(InteractionState.USER_STREAMING)
        if self._bridge is not None and hasattr(self._bridge, "allow_model_output"):
            self._bridge.allow_model_output()
        if self._last_clean_user_turn_response_id == self._response_generation:
            return
        self._last_clean_user_turn_response_id = self._response_generation
        self.conn.google_live_turn_started_at = time.monotonic()
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live clean_user_turn_opened reason={} response_id={}",
            reason,
            self._response_generation,
        )

    def _mark_complete_text_user_turn(self, reason):
        self._mark_clean_user_turn_opened(reason)
        self._interaction.transition(InteractionState.WAITING_MODEL)
        self._waiting_model_since = time.monotonic()
        self._schedule_waiting_model_timeout_task()
        self._clear_user_stream()

    async def _on_user_transcript_barge_in(self, transcript_text):
        if self._lesson_conversation_tool_path_active():
            self._lesson_child_audio_pending_transcript = False
            self._cancel_lesson_child_transcript_timeout_task()
            self._record_lesson_conversation_recognized_text(transcript_text)
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_conversation_transcript_barge_in_ignored chars={}",
                    self.conn,
                ),
                len(str(transcript_text or "")),
            )
            return
        if await self._dispatch_lesson_child_response(transcript_text):
            # The child answer is the legitimate turn: do not interrupt Live or
            # dispatch chat/music intents.
            return
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON:
            return
        if await self._dispatch_music_control_intent(transcript_text):
            return
        await self._begin_user_interrupt("transcript_barge_in")

    def _active_lesson_step_is_interactive(self):
        """True when session is in LESSON mode and the active lesson step is an
        interactive (non-passive, non-completed, running) step."""
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) != SessionMode.LESSON:
            return False
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None:
            return False
        if getattr(runtime, "_step_passive", False):
            return False
        if getattr(runtime, "_step_completed", False):
            return False
        state = getattr(runtime, "state", None)
        if state is not None and str(state).upper() not in ("RUNNING",):
            return False
        return True

    def _lesson_runtime_accepts_voice_input(self):
        """Session-mode-agnostic sibling of _active_lesson_step_is_interactive:
        True when an attached lesson runtime is on a running, non-passive,
        non-completed step (regardless of conn.session_mode)."""
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None:
            return False
        if getattr(runtime, "_step_passive", False):
            return False
        if getattr(runtime, "_step_completed", False):
            return False
        state = getattr(runtime, "state", None)
        if state is not None and str(state).upper() not in ("RUNNING",):
            return False
        return True

    def _lesson_runtime_active(self):
        runtime = getattr(self.conn, "lesson_runtime", None)
        if runtime is None:
            return False
        return str(getattr(runtime, "state", "")).upper() in {"PRELOADING", "RUNNING"}

    def _lesson_conversation_tool_path_active(self):
        runtime = getattr(self.conn, "lesson_runtime", None)
        active = getattr(runtime, "conversation_tool_path_active", None)
        if callable(active):
            try:
                return bool(active())
            except Exception:
                return False
        snapshot = getattr(runtime, "conversation_tool_context", None)
        if not callable(snapshot):
            return False
        try:
            context = snapshot()
        except Exception:
            return False
        return isinstance(context, Mapping) and isinstance(context.get("identity"), Mapping)

    def _record_lesson_conversation_recognized_text(self, transcript_text):
        runtime = getattr(self.conn, "lesson_runtime", None)
        record = getattr(runtime, "record_conversation_recognized_text", None)
        if not callable(record):
            return
        try:
            record(transcript_text)
        except Exception:
            pass

    def _get_user_audio_window_sec(self, reason):
        config = self._get_live_config()
        if self._active_lesson_step_is_interactive():
            window_key = "lesson_child_response_window_sec"
            window_default = 25.0
        elif (
            normalize_session_mode(
                getattr(self.conn, "session_mode", SessionMode.DORMANT)
            )
            == SessionMode.CONVERSATION
            or reason in {"listen_start", "inbound_audio", "audio_end", "lesson_start"}
        ):
            # Multi-turn talk: do not expire back to "need Hi ESP" after 15s.
            window_key = "conversation_audio_allow_window_sec"
            window_default = 900.0
        else:
            window_key = "wake_audio_allow_window_sec"
            window_default = 900.0
        try:
            return float(config.get(window_key, window_default))
        except (TypeError, ValueError):
            return window_default

    async def _open_user_audio_window(self, reason):
        window_sec = self._get_user_audio_window_sec(reason)
        self._user_audio_allowed_until = max(
            self._user_audio_allowed_until,
            time.monotonic() + max(0.1, window_sec),
        )
        if reason == "wake_word":
            self._wake_audio_window_until = self._user_audio_allowed_until
        self._schedule_user_audio_window_expiry(reason, max(0.1, window_sec))
        user_turn_open = (
            reason == "listen_start"
            and (
                self._interaction.state == InteractionState.USER_STREAMING
                or self._user_stream_started_at is not None
            )
        )
        if (
            not user_turn_open
            and reason == "listen_start"
            and self._has_active_output()
        ):
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live listen_start_deferred reason=output_active"
            )
        elif not user_turn_open and (
            self._has_active_output() or self._has_music_session()
        ):
            await self._begin_user_interrupt(reason)
        self.conn.client_abort = False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_audio_window_open reason={} window_ms={:.0f}",
            reason,
            max(0.1, window_sec) * 1000,
        )

    def _schedule_user_audio_window_expiry(self, reason, window_sec):
        self._cancel_user_audio_window_task()
        self._user_audio_window_generation += 1
        generation = self._user_audio_window_generation
        self._user_audio_window_task = asyncio.create_task(
            self._expire_user_audio_window_after(window_sec, generation, reason)
        )

    async def _expire_user_audio_window_after(self, window_sec, generation, reason):
        try:
            await asyncio.sleep(window_sec)
            if generation != self._user_audio_window_generation:
                return
            if self._closing:
                return
            if self._user_stream_started_at is not None:
                return
            self._user_audio_allowed_until = 0.0
            if reason == "wake_word":
                self._wake_audio_window_until = 0.0
            if self._interaction.state == InteractionState.LISTENING:
                self._interaction.transition(InteractionState.IDLE)
            await self._send_user_audio_window_expired_feedback()
            self.conn.client_abort = False
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live user_audio_window_expired reason={} window_ms={:.0f} no_audio=true",
                reason,
                window_sec * 1000,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    async def _send_user_audio_window_expired_feedback(self):
        websocket = getattr(self.conn, "websocket", None)
        if websocket is None:
            return
        # In conversation mode keep firmware listening so long chats do not
        # force another wake word after a pause (continue_listening=false was
        # the main "văng khỏi giao tiếp" symptom on the robot).
        in_conversation = (
            normalize_session_mode(
                getattr(self.conn, "session_mode", SessionMode.DORMANT)
            )
            == SessionMode.CONVERSATION
        )
        if in_conversation:
            # Soft refresh: stay in realtime listen instead of dropping to manual.
            await self._open_user_audio_window("conversation_keep_alive")
            payload = {
                "type": "tts",
                "state": "stop",
                "session_id": getattr(self.conn, "session_id", None),
                "continue_listening": True,
                "listen_mode": "realtime",
            }
        else:
            payload = {
                "type": "tts",
                "state": "stop",
                "session_id": getattr(self.conn, "session_id", None),
                "continue_listening": False,
                "listen_mode": "manual",
            }
        await websocket.send(json.dumps(payload))

    async def _dispatch_music_control_intent(self, transcript_text):
        payload = self._classify_music_control_intent(transcript_text)
        if payload is None:
            return False
        await self._begin_user_interrupt("music_control_intent")
        func_handler = getattr(self.conn, "func_handler", None)
        if func_handler is None:
            return False
        try:
            await func_handler.handle_llm_function_call(self.conn, payload)
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live music_control_intent tool={} chars={}",
                payload.get("name"),
                len(str(transcript_text or "")),
            )
            self.conn.logger.bind(tag="GoogleLive").info(
                "music_state_changed state={} trigger=vietnamese_command",
                self._music_state_for_tool(payload.get("name")),
            )
            return True
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live music_control_intent failed tool={} error={}",
                payload.get("name"),
                self._safe_error_message(exc),
            )
            return False

    def _classify_music_control_intent(self, transcript_text):
        if not self._has_music_session():
            return None
        text = self._normalize_intent_text(transcript_text)
        if not text:
            return None
        resume_markers = (
            "phat tiep",
            "nghe tiep",
            "tiep tuc phat nhac",
        )
        pause_markers = ("tam dung nhac", "pause nhac", "dung tam nhac")
        stop_markers = (
            "dung nhac",
            "tat nhac",
            "ngung nhac",
            "thoi nhac",
            "stop nhac",
            "ket thuc nhac",
        )
        if any(marker in text for marker in resume_markers):
            return {
                "name": "resume_music",
                "arguments": {"response_success": "Phát tiếp nhạc."},
            }
        if any(marker in text for marker in pause_markers):
            return {
                "name": "pause_music",
                "arguments": {"response_success": "Đã tạm dừng nhạc."},
            }
        if any(marker in text for marker in stop_markers):
            return {
                "name": "stop_music",
                "arguments": {"response_success": "Đã tắt nhạc."},
            }
        title = self._extract_strict_music_title(transcript_text)
        if title:
            return {
                "name": "play_music",
                "arguments": {
                    "song_name": title,
                    "response_success": "Đang phát {title}.",
                },
            }
        return None

    def _extract_strict_music_title(self, text):
        raw = str(text or "").strip()
        if not raw:
            return None
        patterns = (
            r"^\s*phát\s+bài\s+(.+)$",
            r"^\s*phát\s+nhạc\s+bài\s+(.+)$",
            r"^\s*mở\s+bài\s+(.+)$",
            r"^\s*nghe\s+bài\s+(.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, raw, flags=re.IGNORECASE)
            if not match:
                continue
            title = match.group(1).strip(" .!?\t\n\r")
            return title if title else None
        return None

    def _music_state_for_tool(self, tool_name):
        return {
            "stop_music": "stopped",
            "pause_music": "paused",
            "resume_music": "playing",
            "play_music": "playing",
        }.get(tool_name, "unknown")

    def _normalize_intent_text(self, text):
        normalized = unicodedata.normalize("NFD", str(text or "").lower())
        normalized = "".join(
            char for char in normalized if unicodedata.category(char) != "Mn"
        )
        # The Vietnamese letter đ/Đ (U+0111/U+0110) has no combining-mark
        # decomposition, so NFD does not fold it to 'd'. Map it explicitly so
        # accent-stripped markers like 'bat dau bai hoc' match STT output.
        normalized = normalized.replace("đ", "d").replace("Đ", "d")
        normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
        return re.sub(r"\s+", " ", normalized).strip()

    def _is_wake_word_only(self, text):
        normalized = self._normalize_intent_text(text)
        if not normalized:
            return False
        wake_words = self.conn.config.get("wakeup_words", [])
        for wake_word in wake_words or []:
            if normalized == self._normalize_intent_text(wake_word):
                return True
        return normalized in LIVE_WAKE_WORD_ALIASES

    def _is_live_wake_transcript_only(self, text):
        normalized = self._normalize_intent_text(text)
        if not normalized:
            return False
        if self._is_wake_word_only(text):
            return True
        return False

    def _auto_pause_music_for_interaction(self):
        """Pause any active music playback so the user-AI exchange is audible."""
        config = self._get_live_config()
        if not bool(config.get("music_auto_pause_on_user_speech", True)):
            return
        session = getattr(self.conn, "_music_session", None)
        if session is None:
            return
        try:
            if hasattr(session, "is_paused") and session.is_paused():
                return
            if hasattr(session, "pause"):
                session.pause()
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live auto-paused music for user interaction"
                )
                self.conn.logger.bind(tag="GoogleLive").info(
                    "music_auto_paused trigger=user_interrupt"
                )
                self.conn.logger.bind(tag="GoogleLive").info(
                    "music_state_changed state=paused trigger=user_interrupt"
                )
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live music auto-pause failed: {}",
                self._safe_error_message(exc),
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

    def _has_audible_music_session(self):
        if not self._has_music_session():
            return False
        session = getattr(self.conn, "_music_session", None)
        if hasattr(session, "is_paused"):
            try:
                return not session.is_paused()
            except Exception:
                return True
        return True

    def _should_suppress_robot_output_echo(self, pcm_audio=None):
        config = self._get_live_config()
        if not bool(config.get("suppress_robot_output_echo", True)):
            return False
        now = time.monotonic()
        reason = None
        if self._has_active_output():
            reason = "robot_speaking"
        elif now < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            reason = "echo_tail"
        elif self._has_audible_music_session():
            reason = "music_playing"
        if reason is None:
            return False
        # A wake/listen window only authorizes a clean user turn after output has
        # been stopped. It must not let the robot's own speaker/music/tail audio
        # back into Gemini while output is active.
        rms = "n/a"
        if pcm_audio and self._bridge is not None and hasattr(self._bridge, "input_rms"):
            try:
                rms = self._bridge.input_rms(pcm_audio)
            except Exception:
                rms = "n/a"
        if self._should_bypass_echo_gate_for_loud_user(config, rms):
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live echo_bypass reason={} bytes={} rms={}",
                reason,
                len(pcm_audio or b""),
                rms,
            )
            return False
        if reason == "robot_speaking" and self._can_forward_aec_audio_for_live_vad(config):
            self._log_aec_live_vad_forward(pcm_audio, reason)
            return False
        # Keep the gate closed while residual speaker energy is still present
        # after tts:stop. Without this, rooms with long acoustic tails reopen
        # mic early and Gemini treats the robot's own voice as a new user turn.
        if reason in {"echo_tail", "robot_speaking", "music_playing"}:
            self._maybe_extend_echo_tail_for_residual(config, rms, reason)
        # Throttle echo_suppressed log to once per second per reason.
        # Without throttling, during music playback this fires every 60ms
        # (16 lines/sec), causing logger IO contention that delays audio
        # chunk forwarding and produces audible jitter on the device.
        last_at = self._last_echo_suppressed_log_at.get(reason, 0.0)
        if now - last_at >= 1.0:
            self._last_echo_suppressed_log_at[reason] = now
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live echo_suppressed reason={} bytes={} rms={} "
                "(throttled to 1/s)",
                reason,
                len(pcm_audio or b""),
                rms,
        )
        return True

    def _maybe_extend_echo_tail_for_residual(self, config, rms, reason):
        """Extend post-output mic suppression while residual energy remains."""
        if not isinstance(rms, (int, float)):
            return
        threshold = self._as_int(config.get("echo_tail_extend_rms_threshold", 700), 700)
        extend_ms = self._as_float(config.get("echo_tail_extend_ms", 350), 350.0)
        max_total_ms = self._as_float(config.get("echo_tail_max_total_ms", 1400), 1400.0)
        if threshold <= 0 or rms < threshold or extend_ms <= 0 or max_total_ms <= 0:
            return

        now = time.monotonic()
        started_at = getattr(self.conn, "google_live_echo_suppress_started_at", None)
        if started_at is None:
            # Seed only after stop (echo_tail); speaking/music wait for tts:stop anchor.
            if reason != "echo_tail":
                return
            self.conn.google_live_echo_suppress_started_at = now
            started_at = now
        started_at = self._as_float(started_at, None)
        if started_at is None:
            return

        elapsed_ms = max(0.0, (now - started_at) * 1000.0)
        if elapsed_ms >= max_total_ms:
            return
        apply_ms = min(extend_ms, max_total_ms - elapsed_ms)
        until = now + apply_ms / 1000.0
        current_until = self._conn_float("google_live_echo_suppress_until", 0.0)
        if until <= current_until:
            return
        self.conn.google_live_echo_suppress_until = until
        self.conn.google_live_audible_output_until = max(
            self._conn_float("google_live_audible_output_until", 0.0),
            until,
        )
        last_at = self._last_echo_suppressed_log_at.get("echo_tail_extend", 0.0)
        if now - last_at >= 1.0:
            self._last_echo_suppressed_log_at["echo_tail_extend"] = now
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live echo_tail_extended reason={} rms={} "
                "extend_ms={:.0f} total_elapsed_ms={:.0f}",
                reason,
                rms,
                apply_ms,
                elapsed_ms,
            )

    def _log_aec_live_vad_forward(self, pcm_audio=None, reason="robot_speaking"):
        log_key = "aec_live_vad_forward"
        now = time.monotonic()
        last_at = self._last_echo_suppressed_log_at.get(log_key)
        if last_at is not None and now - last_at < 1.0:
            return
        self._last_echo_suppressed_log_at[log_key] = now
        rms = "n/a"
        if pcm_audio and self._bridge is not None and hasattr(self._bridge, "input_rms"):
            try:
                rms = self._bridge.input_rms(pcm_audio)
            except Exception:
                rms = "n/a"
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live aec_live_vad_forward reason={} bytes={} rms={} "
            "(throttled to 1/s)",
            reason,
            len(pcm_audio or b""),
            rms,
        )

    def _can_forward_aec_audio_for_live_vad(self, config):
        if not bool(config.get("interrupt_on_input_while_speaking", False)):
            return False
        if bool(config.get("disable_server_side_interruptions", False)):
            return False
        activity_handling = str(
            config.get("activity_handling") or "START_OF_ACTIVITY_INTERRUPTS"
        )
        if activity_handling == "NO_INTERRUPTION":
            return False
        if not bool(config.get("server_side_vad_enabled", True)):
            return False
        bridge = self._bridge
        aec_processor = getattr(bridge, "_aec_processor", None)
        if aec_processor is None:
            return False
        return not bool(getattr(aec_processor, "bypassed", True))

    def _current_audio_suppression_reason(self):
        if self._has_active_output():
            return "robot_speaking"
        if time.monotonic() < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            return "echo_tail"
        if self._has_audible_music_session():
            return "music_playing"
        return "unknown"

    def _current_interaction_state_for_audio(self):
        if self._reconnecting:
            return InteractionState.RECONNECTING
        if self._has_active_output():
            return InteractionState.MODEL_SPEAKING
        if time.monotonic() < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            return InteractionState.MUTED
        if self._has_audible_music_session():
            return InteractionState.MUSIC_PLAYING
        return InteractionState.USER_STREAMING

    def _log_audio_decision(self, decision, reason, pcm_audio=None):
        identity = self._interaction.next_audio_identity(
            self._current_interaction_state_for_audio()
        )
        rms = "n/a"
        if pcm_audio and self._bridge is not None and hasattr(self._bridge, "input_rms"):
            try:
                rms = self._bridge.input_rms(pcm_audio)
            except Exception:
                rms = "n/a"
        self.conn.logger.bind(tag="GoogleLive").info(
            "audio_decision decision={} reason={} state={} turn_id={} response_id={} audio_seq={} bytes={} rms={}",
            decision,
            reason,
            identity["state"],
            identity["turn_id"],
            identity["response_id"],
            identity["audio_seq"],
            len(pcm_audio or b""),
            rms,
        )

    def _should_bypass_echo_gate_for_loud_user(self, config, rms):
        if not isinstance(rms, (int, float)):
            self._reset_loud_input_tracking()
            return False
        # Opt-out for hardware where speaker echo rms crosses the bypass
        # threshold (causing false loud_input interrupts mid-sentence). When
        # disabled, real user interrupts must come via wake-word or
        # transcript barge-in.
        if not bool(config.get("echo_bypass_interrupt_enabled", True)):
            self._reset_loud_input_tracking()
            return False
        try:
            threshold = int(config.get("robot_output_echo_bypass_rms_threshold", 650))
        except (TypeError, ValueError):
            threshold = 1200
        if threshold <= 0 or rms < threshold:
            self._reset_loud_input_tracking()
            return False
        if not self._sustained_input_allows_interrupt(
            config,
            "robot_output_echo_bypass_min_duration_sec",
            default_duration_sec=0.06,
        ):
            return False
        self._user_audio_allowed_until = max(
            self._user_audio_allowed_until,
            time.monotonic() + self._get_wake_audio_allow_window_sec(),
        )
        self._echo_bypass_pending_interrupt = True
        return True

    def _get_wake_audio_allow_window_sec(self):
        config = self._get_live_config()
        try:
            window_sec = float(config.get("wake_audio_allow_window_sec", 900.0))
        except (TypeError, ValueError):
            window_sec = 900.0
        return max(0.1, window_sec)

    def _extract_listen_control(self, message):
        try:
            msg_json = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return None, None
        if not isinstance(msg_json, Mapping) or msg_json.get("type") != "listen":
            return None, None
        text = msg_json.get("text")
        return msg_json.get("state"), text if isinstance(text, str) else None

    def _validation_tool_audit_enabled(self):
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) != SessionMode.LESSON:
            return False
        if not self._lesson_runtime_active():
            return False
        config = self._get_live_config()
        if config.get("validation_tool_audit_enabled") is not True:
            return False
        if config.get("validation_tool_audit_mode") != "local_soak":
            return False
        features = getattr(self.conn, "features", None)
        if not isinstance(features, Mapping) or features.get(
            "googleLiveValidationToolAuditV1"
        ) is not True:
            return False
        client_ids = config.get("validation_tool_audit_client_ids")
        device_ids = config.get("validation_tool_audit_device_ids")
        if not isinstance(client_ids, (list, tuple)) or len(client_ids) != 1:
            return False
        if not isinstance(device_ids, (list, tuple)) or len(device_ids) != 1:
            return False
        allowed_client_id = client_ids[0]
        allowed_device_id = device_ids[0]
        if not isinstance(allowed_client_id, str) or not allowed_client_id:
            return False
        if not isinstance(allowed_device_id, str) or not allowed_device_id:
            return False
        return (
            getattr(self.conn, "client_id", None) == allowed_client_id
            and getattr(self.conn, "device_id", None) == allowed_device_id
        )

    @staticmethod
    def _validation_tool_identity(source):
        if not isinstance(source, Mapping):
            return None
        lesson_session_id = source.get("lessonSessionId")
        turn_sequence_id = source.get("turnSequenceId")
        attempt_id = source.get("attemptId")
        step_key = source.get("stepKey")
        if (
            not isinstance(lesson_session_id, str)
            or not lesson_session_id
            or not isinstance(turn_sequence_id, int)
            or turn_sequence_id < 1
            or not isinstance(attempt_id, str)
            or not attempt_id
            or not isinstance(step_key, str)
            or not step_key
        ):
            return None
        identity = {
            "lessonSessionId": lesson_session_id,
            "turnSequenceId": turn_sequence_id,
            "attemptId": attempt_id,
            "stepKey": step_key,
        }
        cue_id = source.get("cueId")
        if cue_id is not None:
            identity["cueId"] = cue_id
        return identity

    async def _emit_validation_tool_audit(
        self,
        name,
        args,
        response_payload,
        validation_receipt,
    ):
        if name not in LESSON_CONVERSATION_TOOLS:
            return
        if not self._validation_tool_audit_enabled():
            return
        if not isinstance(args, Mapping) or not isinstance(response_payload, Mapping):
            return
        if response_payload.get("accepted") is not True:
            return
        if not isinstance(validation_receipt, Mapping):
            return
        if validation_receipt.get("canonicalToolName") != name:
            return
        context = response_payload.get("context")
        refreshed = context.get("identity") if isinstance(context, Mapping) else None
        identity = self._validation_tool_identity(args)
        refreshed_identity = self._validation_tool_identity(refreshed)
        receipt_identity = self._validation_tool_identity(
            validation_receipt.get("refreshedIdentity")
        )
        if identity is None or refreshed_identity is None:
            return
        if receipt_identity != refreshed_identity:
            return
        websocket = getattr(self.conn, "websocket", None)
        if websocket is None:
            return
        payload = {
            "type": "google_live_validation_tool_audit",
            "feature": "googleLiveValidationToolAuditV1",
            "protocolVersion": "teebot-lesson-renderer.v4",
            "toolName": name,
            "accepted": response_payload.get("accepted") is True,
            "code": str(response_payload.get("code") or ""),
            "identity": identity,
            "cueId": response_payload.get("cueId"),
            "effect": response_payload.get("effect"),
            "refreshedIdentity": refreshed_identity,
        }
        try:
            await websocket.send(json.dumps(payload))
        except Exception:
            return

    async def _handle_tool_call_event(self, event):
        calls = event.get("calls") if isinstance(event, Mapping) else None
        if not calls:
            return
        in_lesson = (
            normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) == SessionMode.LESSON
            or self._lesson_runtime_active()
        )
        event_generation = event.get("response_generation")
        try:
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live tool_call received count={} names=[{}]",
                len(calls),
                ",".join(
                    str(c.get("name")) for c in calls if isinstance(c, Mapping)
                ),
            )
        except Exception:
            pass
        responses = []
        for call in calls:
            call_id = call.get("id") if isinstance(call, Mapping) else None
            name = call.get("name") if isinstance(call, Mapping) else None
            if in_lesson and name not in LESSON_CONVERSATION_TOOLS:
                responses.append(
                    {
                        "id": call_id,
                        "name": name,
                        "response": self._tool_error(
                            "LESSON_MODE_TOOL_BLOCKED",
                            "Tool calls are blocked while lesson mode owns the child interaction.",
                        ),
                    }
                )
                continue
            if in_lesson and not isinstance(event_generation, int):
                responses.append(
                    {
                        "id": call_id,
                        "name": name,
                        "response": self._tool_error(
                            "MISSING_ORIGIN_GENERATION",
                            "Lesson tool call is missing its originating model response generation.",
                        ),
                    }
                )
                continue
            if in_lesson and event_generation != self._response_generation:
                responses.append(
                    {
                        "id": call_id,
                        "name": name,
                        "response": self._tool_error(
                            "STALE_MODEL_RESPONSE",
                            "The model response was cancelled before this lesson tool was admitted.",
                        ),
                    }
                )
                continue
            self.conn.logger.bind(tag="GoogleLive").info(
                "tool_call_dispatched name={} response_id={}",
                name,
                self._response_generation,
            )
            if (
                name == "start_lesson"
                and time.monotonic() < self._suppress_start_lesson_tool_call_until
            ):
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "Google Live duplicate_start_lesson_tool_call_suppressed id={}",
                        self.conn,
                    ),
                    call_id,
                )
                responses.append(
                    {
                        "id": call_id,
                        "name": name,
                        "response": {"result": "Lesson already starting."},
                    }
                )
                continue
            args = call.get("args") if isinstance(call, Mapping) else {}
            if in_lesson and isinstance(args, Mapping):
                args = {
                    key: value
                    for key, value in args.items()
                    if not str(key).startswith("_provider_admission_")
                }
            if call_id is not None:
                self._pending_tool_calls.add(call_id)
                self._cancelled_tool_call_ids.discard(call_id)
            started_at = time.monotonic()
            validation_receipt = {}
            response_payload = await self._execute_tool_call_with_timeout(
                name,
                args,
                call_id=call_id,
                lesson_admission_generation=event_generation if in_lesson else None,
                lesson_validation_receipt=validation_receipt,
            )
            latency_ms = (time.monotonic() - started_at) * 1000
            if call_id is not None and call_id in self._cancelled_tool_call_ids:
                self._pending_tool_calls.discard(call_id)
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live stale_tool_result_dropped id={} name={} latency_ms={:.1f}",
                    call_id,
                    name,
                    latency_ms,
                )
                continue
            await self._emit_validation_tool_audit(
                name,
                args,
                response_payload,
                validation_receipt,
            )
            ok = not (
                isinstance(response_payload, Mapping)
                and response_payload.get("ok") is False
            )
            error_code = ""
            if isinstance(response_payload, Mapping):
                error_code = str(response_payload.get("errorCode") or "")
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live tool_call_completed name={} id={} latency_ms={:.1f} ok={} error_code={}",
                name,
                call_id,
                latency_ms,
                ok,
                error_code,
            )
            responses.append(
                {
                    "id": call_id,
                    "name": name,
                    "response": response_payload,
                }
            )
            if call_id is not None:
                self._pending_tool_calls.discard(call_id)
        if not responses:
            return
        if self._client is None:
            return
        try:
            await self._client.send_tool_response(responses)
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live tool_response sent count={} names={}",
                len(responses),
                ",".join(str(r.get("name") or "") for r in responses),
            )
        except Exception as exc:
            await self._handle_runtime_failure(exc)

    async def _handle_tool_call_cancellation_event(self, event):
        ids = event.get("ids") if isinstance(event, Mapping) else None
        if not ids:
            return
        for call_id in ids:
            self._pending_tool_calls.discard(call_id)
            self._cancelled_tool_call_ids.add(call_id)
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live tool_call_cancellation ids={}",
            ",".join(str(i) for i in ids),
        )

    async def _execute_tool_call_with_timeout(
        self,
        name,
        args,
        call_id=None,
        lesson_admission_generation=None,
        lesson_validation_receipt=None,
    ):
        try:
            if name in LESSON_CONVERSATION_TOOLS:
                from plugins_func.functions.lesson_conversation import (
                    _google_live_lesson_tool_admission,
                )

                with _google_live_lesson_tool_admission(
                    self,
                    lesson_admission_generation,
                    lesson_validation_receipt,
                ):
                    return await self._execute_tool_call(name, args, call_id=call_id)
            timeout = self._get_tool_timeout_sec()
            if timeout is None:
                return await self._execute_tool_call(name, args, call_id=call_id)
            return await asyncio.wait_for(
                self._execute_tool_call(name, args, call_id=call_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live tool timeout name={} id={} timeout_ms={:.0f}",
                name,
                call_id,
                (self._get_tool_timeout_sec() or 0) * 1000,
            )
            return self._tool_error(
                "TOOL_TIMEOUT",
                "Tool execution timed out",
            )

    async def _execute_tool_call(self, name, args, call_id=None):
        if not name:
            return self._tool_error("MISSING_FUNCTION_NAME", "Missing function name")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            return self._tool_error(
                "INVALID_TOOL_ARGS",
                "Tool arguments must be an object",
            )
        args = dict(args)
        if self._requires_tool_confirmation(name, args):
            return self._tool_error(
                "CONFIRMATION_REQUIRED",
                "Tool requires explicit user confirmation before execution",
            )
        func_handler = getattr(self.conn, "func_handler", None)
        if func_handler is None:
            return self._tool_error(
                "TOOL_HANDLER_UNAVAILABLE",
                "Tool handler unavailable",
            )
        try:
            try:
                if name in LESSON_CONVERSATION_TOOLS:
                    self.conn.logger.bind(tag="GoogleLive").info(
                        with_lesson_log_context(
                            "Google Live lesson tool dispatch name={} argument_keys=[{}]",
                            self.conn,
                        ),
                        name,
                        ",".join(sorted(str(key) for key in args)),
                    )
                else:
                    self.conn.logger.bind(tag="GoogleLive").info(
                        "Google Live tool dispatch name={} args_type={} args={}",
                        name,
                        type(args).__name__,
                        dict(args) if hasattr(args, "items") else args,
                    )
            except Exception:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live tool dispatch name={} args_type={} args=<unprintable>",
                    name,
                    type(args).__name__,
                )
            if name == "start_lesson":
                with self._lesson_start_tool_dispatch_scope():
                    result = await func_handler.handle_llm_function_call(
                        self.conn,
                        {"name": name, "arguments": args},
                    )
            else:
                result = await func_handler.handle_llm_function_call(
                    self.conn,
                    {"name": name, "arguments": args},
                )
            try:
                action_name = getattr(getattr(result, "action", None), "name", "?")
                if name in LESSON_CONVERSATION_TOOLS:
                    self.conn.logger.bind(tag="GoogleLive").info(
                        with_lesson_log_context(
                            "Google Live lesson tool returned name={} action={}",
                            self.conn,
                        ),
                        name,
                        action_name,
                    )
                else:
                    self.conn.logger.bind(tag="GoogleLive").info(
                        "Google Live tool returned name={} action={} response={!r}",
                        name,
                        action_name,
                        getattr(result, "response", None),
                    )
            except Exception:
                pass
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live tool execution failed name={} error={}",
                name,
                self._safe_error_message(exc),
            )
            return self._tool_error("TOOL_EXCEPTION", self._safe_error_message(exc))
        return self._format_tool_response_payload(result)

    def _get_tool_timeout_sec(self):
        config = self._get_live_config()
        try:
            timeout = float(config.get("tool_timeout_sec", 10.0))
        except (TypeError, ValueError):
            timeout = 10.0
        if timeout <= 0:
            return None
        return timeout

    def _requires_tool_confirmation(self, name, args):
        config = self._get_live_config()
        explicit_names = config.get("dangerous_tool_names") or []
        try:
            explicit = {str(item) for item in explicit_names if item}
        except TypeError:
            explicit = set()
        is_dangerous = name in explicit
        if not is_dangerous:
            danger_pattern = config.get(
                "dangerous_tool_name_pattern",
                r"(?i)(delete|remove|shutdown|reboot|factory|reset|transfer|purchase|pay|send_money)",
            )
            try:
                is_dangerous = re.search(str(danger_pattern), str(name or "")) is not None
            except re.error:
                is_dangerous = False
        # Dangerous tools always require out-of-band confirmation. The model
        # supplies `args` in-band, so an args["confirmed"] flag is attacker
        # (model) controlled and MUST NOT be trusted to bypass the gate —
        # otherwise the model self-bypasses by re-issuing the call with
        # {"confirmed": true}. Only honor the flag for non-dangerous tools.
        if is_dangerous:
            return True
        return False

    @staticmethod
    def _tool_error(error_code, message):
        return {"ok": False, "errorCode": error_code, "message": message}

    def _format_tool_response_payload(self, action_response):
        if action_response is None:
            return {"result": ""}
        try:
            action = action_response.action
        except AttributeError:
            action = None
        response_value = getattr(action_response, "response", None)
        result_value = getattr(action_response, "result", None)
        text = str(response_value or result_value or "")
        action_name = action.name.lower() if action is not None and hasattr(action, "name") else None
        if action is not None and action == Action.ERROR:
            return self._tool_error("TOOL_ERROR", text or "tool error")
        if action is not None and action == Action.NOTFOUND:
            return self._tool_error("TOOL_NOT_FOUND", text or "tool not found")
        if isinstance(result_value, Mapping):
            return dict(result_value)
        payload = {"ok": True, "result": text}
        if action_name:
            payload["action"] = action_name
        return payload

    _DEBOUNCED_INTERRUPT_REASONS = frozenset(
        {"audio_input", "transcript_barge_in", "loud_input"}
    )

    # Reasons allowed during music playback. Ambient/raw audio remains blocked,
    # while explicit user gates and tested loud-speech bypass can pause music
    # and open a clean user turn.
    _MUSIC_ALLOWED_INTERRUPT_REASONS = frozenset(
        {
            "music_control_intent",
            "text_input",
            "explicit_interrupt",
            "listen_start",
            "wake_word",
            "lesson_child_response",
            "transcript_barge_in",
            "loud_input",
        }
    )

    async def transition_to_lesson_start(self) -> bool:
        """Bounded hard handoff from a Live turn into lesson runtime ownership."""
        config = getattr(self.conn, "config", {})
        lesson_config = config.get("lesson", {}) if isinstance(config, dict) else {}
        if not isinstance(lesson_config, dict):
            lesson_config = {}
        try:
            timeout_sec = max(
                0.05,
                float(lesson_config.get("live_transition_timeout_sec", 2.0)),
            )
        except (TypeError, ValueError):
            timeout_sec = 2.0

        await self._begin_user_interrupt("lesson_start_intent")
        clear_speaking = getattr(self.conn, "clearSpeakStatus", None)
        if callable(clear_speaking):
            clear_speaking()
        self._interaction.transition(InteractionState.LISTENING)
        self.conn.client_abort = False

        busy = getattr(self.conn, "is_realtime_busy", None)
        if not callable(busy):
            return True
        deadline = time.monotonic() + timeout_sec
        while True:
            # A final audio callback can publish an echo-tail-derived state after
            # the terminal stop selected LISTENING. With the microphone closed,
            # no later callback exists to clear that transient state, so settle it
            # from the real output/VAD flags before consulting the busy guard.
            if (
                self._interaction.state
                in {
                    InteractionState.USER_STREAMING,
                    InteractionState.MODEL_SPEAKING,
                    InteractionState.MUTED,
                }
                and not self._has_active_output()
                and not getattr(self.conn, "client_is_speaking", False)
                and not getattr(self.conn, "client_have_voice", False)
            ):
                self._interaction.transition(InteractionState.LISTENING)
                self.conn.client_abort = False
            if not busy():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    async def _begin_user_interrupt(self, reason):
        # Music-protection gate: keep ambient/raw audio from interrupting music,
        # but allow wake/listen, transcript, text, explicit interrupt, music
        # commands, and explicitly enabled loud-speech bypass.
        if (
            self._has_music_session()
            and reason not in self._MUSIC_ALLOWED_INTERRUPT_REASONS
        ):
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live interrupt_blocked_by_music reason={} music_active=True",
                reason,
            )
            return

        now = time.monotonic()
        debounce_sec = self._get_interrupt_debounce_sec()
        if (
            reason in self._DEBOUNCED_INTERRUPT_REASONS
            and debounce_sec > 0
            and now - self._last_interrupt_at < debounce_sec
        ):
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live interrupt_debounced reason={} age_ms={:.0f}",
                reason,
                (now - self._last_interrupt_at) * 1000,
            )
            return
        self._last_interrupt_at = now

        previous_response_id = self._response_generation
        self._response_generation += 1
        self._cancelled_response_ids.add(previous_response_id)
        self._interaction.begin_interrupt(
            reason=reason,
            turn_id=self._response_generation,
            response_id=self._response_generation,
        )
        hard_lesson_interrupt = bool(
            self._should_hard_reconnect_on_interrupt()
            and self._receive_task is not None
            and self._lesson_conversation_tool_path_active()
        )
        if not hard_lesson_interrupt:
            await self._interrupt_lesson_conversation()
        if len(self._cancelled_response_ids) > 20:
            self._cancelled_response_ids = set(
                sorted(self._cancelled_response_ids)[-10:]
            )

        self.conn.logger.bind(tag="GoogleLive").info(
            "interrupt_started reason={} state={} turn_id={} response_id={}",
            reason,
            self._interaction.state.value,
            self._interaction.turn_id,
            self._interaction.response_id,
        )

        self.conn.client_abort = True
        self.conn.google_live_audio_out_started_at = None
        self._cancel_input_flush_task()
        if reason in {"audio_input", "loud_input"}:
            self._start_interrupt_capture_turn(reason)
        # Auto-pause music when user speaks so the model and user can hear
        # each other; the model can call resume_music when user asks.
        self._auto_pause_music_for_interaction()
        if self._bridge is not None and hasattr(self._bridge, "stop_output"):
            try:
                lesson_stop = getattr(self._bridge, "stop_output_for_lesson", None)
                if reason == "lesson_start_intent" and callable(lesson_stop):
                    await lesson_stop()
                else:
                    await self._bridge.stop_output()
            except RuntimeError as exc:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live stop_output skipped after disconnect: {}",
                    self._safe_error_message(exc),
                )
                if hasattr(self.conn, "clear_queues"):
                    self.conn.clear_queues()
                if hasattr(self.conn, "clearSpeakStatus"):
                    self.conn.clearSpeakStatus()
        else:
            if hasattr(self.conn, "clear_queues"):
                self.conn.clear_queues()
            if hasattr(self.conn, "clearSpeakStatus"):
                self.conn.clearSpeakStatus()
        if (
            self._client is not None
            and getattr(self._client, "connected", False)
            and hasattr(self._client, "interrupt")
        ):
            try:
                await self._client.interrupt()
            except RuntimeError as exc:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live interrupt skipped after disconnect: {}",
                    self._safe_error_message(exc),
                )
        if (
            self._client is not None
            and getattr(self._client, "connected", False)
            and hasattr(self._client, "end_audio_stream")
        ):
            try:
                if self._bridge is not None and hasattr(self._bridge, "flush_pending_input_audio"):
                    await self._bridge.flush_pending_input_audio()
                await self._client.end_audio_stream()
            except RuntimeError as exc:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live end_audio_stream skipped after interrupt: {}",
                    self._safe_error_message(exc),
                )
        if self._should_hard_reconnect_on_interrupt() and self._receive_task is not None:
            if hard_lesson_interrupt:
                handled = await self._handle_lesson_live_interruption("interrupted")
                if not handled:
                    await self._hard_reconnect_after_interrupt(reason)
            else:
                await self._hard_reconnect_after_interrupt(reason)
        if reason in {"audio_input", "loud_input"}:
            self._schedule_forced_interrupt_input_flush(reason)
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_interrupted reason={} cancelled_response_id={} next_response_id={}",
            reason,
            previous_response_id,
            self._response_generation,
        )

    async def _interrupt_lesson_conversation(self):
        runtime = getattr(self.conn, "lesson_runtime", None)
        interrupt = getattr(runtime, "conversation_interrupt_current", None)
        snapshot = getattr(runtime, "conversation_tool_context", None)
        if not callable(interrupt) or not callable(snapshot):
            return
        try:
            decision = await interrupt()
            context = snapshot()
            identity = context.get("identity") if isinstance(context, Mapping) else None
            if isinstance(identity, Mapping):
                self._interaction.bind_lesson_attempt(
                    lesson_session_id=identity.get("lessonSessionId"),
                    attempt_id=identity.get("attemptId"),
                    step_key=identity.get("stepKey"),
                    turn_sequence_id=identity.get("turnSequenceId"),
                )
                await self.publish_lesson_conversation_context(context)
            self.conn.logger.bind(tag="GoogleLive").info(
                with_lesson_log_context(
                    "Google Live lesson_conversation_interrupted accepted={} code={}",
                    self.conn,
                ),
                bool(getattr(decision, "accepted", False)),
                getattr(decision, "code", "UNKNOWN"),
            )
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "Google Live lesson_conversation_interrupt_failed error={}",
                    self.conn,
                ),
                self._safe_error_message(exc),
            )

    async def _handle_lesson_live_interruption(self, reason):
        runtime = getattr(self.conn, "lesson_runtime", None)
        fallback = getattr(runtime, "conversation_live_interruption", None)
        if not callable(fallback):
            return False
        try:
            directive = await fallback(reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                with_lesson_log_context(
                    "lesson_live_fallback_failed diagnostic=RUNTIME_EXCEPTION error_class={}",
                    self.conn,
                ),
                type(exc).__name__,
            )
            return False
        accepted = bool(getattr(directive, "accepted", False))
        code = str(getattr(directive, "code", "INVALID_DIRECTIVE"))
        window_id = getattr(directive, "window_id", None)
        reconnect_allowed = bool(getattr(directive, "reconnect_allowed", False))
        self.conn.logger.bind(tag="GoogleLive").info(
            with_lesson_log_context(
                "lesson_live_fallback diagnostic={} reason={} reconnect_allowed={}",
                self.conn,
            ),
            code,
            reason,
            reconnect_allowed,
        )
        if not accepted:
            return False
        if reconnect_allowed:
            reconnected = await self._attempt_lesson_reconnect_once(reason)
            if reconnected:
                reset = getattr(runtime, "conversation_live_reconnect_succeeded", None)
                if callable(reset) and isinstance(window_id, str):
                    reset(window_id)
                await self._publish_current_lesson_context()
                return True
        wait_for_ack = getattr(runtime, "wait_conversation_live_fallback_ack", None)
        if not callable(wait_for_ack) or not isinstance(window_id, str):
            return False
        try:
            authorization = await wait_for_ack(
                window_id,
                timeout_sec=self._lesson_live_fallback_ack_timeout_sec(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        if not isinstance(authorization, str) or not authorization:
            return False
        prompt = getattr(directive, "prompt", "")
        sender = getattr(self._client, "send_text", None)
        if not isinstance(prompt, str) or not prompt.strip() or not callable(sender):
            expire_prompt = getattr(
                runtime,
                "expire_conversation_live_fallback_prompt",
                None,
            )
            if callable(expire_prompt):
                expire_prompt(window_id, authorization)
            return False
        claim_prompt = getattr(
            runtime,
            "claim_conversation_live_fallback_prompt",
            None,
        )
        if not callable(claim_prompt) or not bool(
            claim_prompt(window_id, authorization)
        ):
            return False
        expire_prompt = getattr(
            runtime,
            "expire_conversation_live_fallback_prompt",
            None,
        )
        try:
            await sender(prompt)
            return True
        except asyncio.CancelledError:
            if callable(expire_prompt):
                expire_prompt(window_id, authorization)
            raise
        except Exception:
            if callable(expire_prompt):
                expire_prompt(window_id, authorization)
            return False

    def _lesson_live_fallback_ack_timeout_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("lesson_live_fallback_ack_timeout_sec", 2.0))
        except (TypeError, ValueError):
            value = 2.0
        return max(0.1, min(value, 5.0))

    async def _attempt_lesson_reconnect_once(self, reason):
        async with self._get_lifecycle_lock():
            if self._closing or self._fallback_provider is not None or self._reconnecting:
                return False
            lifecycle_generation = self._lifecycle_generation

            def lifecycle_current():
                return bool(
                    not self._closing
                    and self._lifecycle_generation == lifecycle_generation
                )

            self._reconnecting = True
            self._interaction.transition(InteractionState.RECONNECTING)
            try:
                await self._close_live_resources()
                if not lifecycle_current():
                    return False
                await self._record_reconnect_attempt()
                if not lifecycle_current():
                    await self._close_live_resources()
                    return False
                await self._open_live_session()
                if not lifecycle_current():
                    await self._close_live_resources()
                    return False
                self.conn.voice_provider = self
                self.conn.logger.bind(tag="GoogleLive").info(
                    with_lesson_log_context(
                        "lesson_live_reconnect diagnostic=SUCCEEDED reason={} attempts=1",
                        self.conn,
                    ),
                    reason,
                )
                return True
            except asyncio.CancelledError:
                await self._close_live_resources()
                raise
            except Exception as exc:
                await self._close_live_resources()
                self.conn.logger.bind(tag="GoogleLive").warning(
                    with_lesson_log_context(
                        "lesson_live_reconnect diagnostic=FAILED reason={} attempts=1 error_class={}",
                        self.conn,
                    ),
                    reason,
                    self._classify_error(exc),
                )
                return False
            finally:
                self._reconnecting = False

    def _should_hard_reconnect_on_interrupt(self):
        config = self._get_live_config()
        return bool(config.get("hard_reconnect_on_interrupt", False))

    async def _hard_reconnect_after_interrupt(
        self, reason, *, restore_session_resumption=True
    ):
        with _voice_activity_lease(
            self.conn, ActivityOperation.GOOGLE_HARD_RECONNECT
        ) as allowed:
            if not allowed:
                self._pending_reconnect_audio.clear()
                return False
            return await self._hard_reconnect_after_interrupt_with_lease(
                reason,
                restore_session_resumption=restore_session_resumption,
            )

    async def _hard_reconnect_after_interrupt_with_lease(
        self, reason, *, restore_session_resumption=True
    ):
        if self._closing or self._fallback_provider is not None or self._reconnecting:
            return False
        self._pending_reconnect_audio.clear()
        self._reconnecting = True
        try:
            await self._close_live_resources()
            if restore_session_resumption:
                await self._open_live_session()
            else:
                self.conn.google_live_session_resumption_handle = None
                async with self._get_live_open_lock():
                    await self._open_live_session_locked(restore_session_resumption=False)
            self.conn.voice_provider = self
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live hard_reconnected_after_interrupt reason={} response_id={}",
                reason,
                self._response_generation,
            )
            return True
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return False
        finally:
            self._reconnecting = False

    def _get_interrupt_debounce_sec(self):
        config = self._get_live_config()
        try:
            value = float(config.get("interrupt_debounce_sec", 0.2))
        except (TypeError, ValueError):
            value = 0.2
        return max(0.0, value)

    async def _stop_live_output_for_transport_change(self):
        if self._bridge is not None and hasattr(self._bridge, "stop_output"):
            try:
                await self._bridge.stop_output()
            except Exception as exc:
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live output cleanup failed during transport change: {}",
                    self._safe_error_message(exc),
                )

    def _should_fallback_to_classic(self):
        return False

    def _should_raise_without_fallback(self, exc):
        return "aec required" in str(exc).lower()

    def _should_barge_in(self, pcm_audio=None):
        config = self._get_live_config()
        if not bool(config.get("barge_in", False)):
            return False
        if not bool(getattr(self.conn, "client_is_speaking", False)):
            return False
        started_at = getattr(self.conn, "google_live_audio_out_started_at", None)
        if started_at is not None:
            try:
                min_output_age = float(config.get("barge_in_min_output_age_sec", 0.25))
            except (TypeError, ValueError):
                min_output_age = 0.25
            if time.monotonic() - started_at < max(0, min_output_age):
                return False
        if not pcm_audio:
            return False
        try:
            threshold = int(config.get("barge_in_rms_threshold", 5000))
        except (TypeError, ValueError):
            threshold = 5000
        try:
            rms = self._bridge.input_rms(pcm_audio)
        except Exception:
            return False
        if rms < max(0, threshold):
            self._reset_loud_input_tracking()
            return False
        if not self._sustained_input_allows_interrupt(
            config,
            "barge_in_min_input_duration_sec",
            default_duration_sec=0.42,
        ):
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live barge-in rms={} threshold={}",
            rms,
            threshold,
        )
        return True

    def _should_interrupt_for_input(self, pcm_audio=None):
        config = self._get_live_config()
        if self._should_barge_in(pcm_audio):
            return True
        if bool(config.get("barge_in", False)) and bool(
            getattr(self.conn, "client_is_speaking", False)
        ):
            return False
        if not bool(config.get("interrupt_on_input_while_speaking", True)):
            return False
        if not self._has_active_output():
            self._reset_loud_input_tracking()
            return False
        started_at = getattr(self.conn, "google_live_audio_out_started_at", None)
        if started_at is not None:
            try:
                min_output_age = float(
                    config.get("interrupt_min_output_age_sec", 0.25)
                )
            except (TypeError, ValueError):
                min_output_age = 0.25
            if time.monotonic() - started_at < max(0, min_output_age):
                return False
        if pcm_audio is not None:
            try:
                threshold = int(config.get("interrupt_rms_threshold", 5000))
            except (TypeError, ValueError):
                threshold = 5000
            if threshold > 0:
                try:
                    rms = self._bridge.input_rms(pcm_audio)
                except Exception:
                    return False
                if rms < threshold:
                    self._reset_loud_input_tracking()
                    return False
                if not self._sustained_input_allows_interrupt(
                    config,
                    "interrupt_min_input_duration_sec",
                    default_duration_sec=0.42,
                ):
                    return False
        return True

    def _should_drop_input_during_output(self):
        config = self._get_live_config()
        if bool(config.get("barge_in", False)):
            return False
        if getattr(self.conn, "google_live_audio_out_started_at", None) is None:
            return False
        return bool(config.get("drop_input_while_speaking", False))

    def _should_drop_input_post_audio_start(self):
        """Drop mic frames for the first N seconds after audio_start.

        This complements the AEC stage: even with strong echo cancellation,
        the first few model-audio frames can leak enough residual energy
        to trigger Live VAD on the server side, which then cuts the model
        off after a single chunk. Muting the input pipeline during the
        critical convergence window prevents the upstream cancellation
        entirely. Real user barge-in resumes when the window expires.
        """
        config = self._get_live_config()
        try:
            window_sec = float(config.get("mute_input_after_audio_start_sec", 0))
        except (TypeError, ValueError):
            window_sec = 0.0
        if window_sec <= 0:
            return False
        started_at = getattr(self.conn, "google_live_audio_out_started_at", None)
        if started_at is None:
            return False
        return (time.monotonic() - started_at) < window_sec

    async def _interrupt_for_barge_in(self):
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live barge-in triggered by inbound audio"
        )
        await self.interrupt()
        self.conn.client_abort = False
        self.conn.client_is_speaking = False

    def _has_active_output(self):
        if getattr(self.conn, "google_live_audio_out_started_at", None) is not None:
            return True
        return time.monotonic() < getattr(self.conn, "google_live_audible_output_until", 0.0)

    def _sustained_input_allows_interrupt(
        self,
        config,
        config_key,
        default_duration_sec=0.0,
    ):
        try:
            min_duration_sec = float(config.get(config_key, default_duration_sec))
        except (TypeError, ValueError):
            min_duration_sec = default_duration_sec
        min_duration_sec = max(0.0, min_duration_sec)
        if min_duration_sec <= 0:
            return True

        now = time.monotonic()
        frame_duration_sec = self._get_input_frame_duration_sec()
        last_loud_at = self._last_loud_input_at
        if (
            last_loud_at is None
            or now - last_loud_at > max(0.25, frame_duration_sec * 3)
        ):
            self._loud_input_duration_sec = frame_duration_sec
        else:
            self._loud_input_duration_sec += frame_duration_sec
        self._last_loud_input_at = now
        return self._loud_input_duration_sec >= min_duration_sec

    def _reset_loud_input_tracking(self):
        self._loud_input_duration_sec = 0.0
        self._last_loud_input_at = None

    def _get_input_frame_duration_sec(self):
        config = self._get_live_config()
        configured_ms = config.get("input_frame_duration_ms")
        if configured_ms in (None, ""):
            configured_ms = 60
        try:
            frame_ms = float(configured_ms)
        except (TypeError, ValueError):
            frame_ms = 60
        return max(0.01, frame_ms / 1000.0)

    def _extract_user_text_message(self, message):
        try:
            msg_json = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(msg_json, Mapping):
            return None

        message_type = msg_json.get("type")
        if message_type == "listen" and msg_json.get("state") == "detect":
            text = msg_json.get("text")
        elif message_type in {"text", "chat", "input"}:
            text = msg_json.get("text") or msg_json.get("content")
        else:
            return None
        if not isinstance(text, str):
            return None
        text = text.strip()
        return text or None
