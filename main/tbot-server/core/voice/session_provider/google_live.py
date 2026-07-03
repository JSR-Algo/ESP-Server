import asyncio
import hashlib
import json
import re
import time
import unicodedata
from collections import deque
from collections.abc import Mapping

from core.voice.google_live import GoogleLiveAudioBridge, GoogleLiveClientFactory
from core.voice.child_safety import ensure_child_safety_block
from core.voice.output_safety_judge import judge_output_unsafe
from core.voice.google_live.interaction_controller import (
    GoogleLiveInteractionController,
    InteractionState,
)
from core.voice.session_provider.base import VoiceSessionProvider
from core.providers.tools.product_toolset import product_tool_names
from core.voice.live_admission import AdmissionDecision, AdmissionReason, LiveAdmissionGate
from core.voice.session_orchestrator import SessionMode, normalize_session_mode
from config.voice_consent_client import get_voice_consent_client
from plugins_func.register import Action


LESSON_LIVE_TEXT_INSTRUCTION = (
    "Đọc nguyên văn câu sau bằng giọng Google Live đã cấu hình. "
    "Giữ đúng ngôn ngữ từng phần: tiếng Việt đọc tiếng Việt, từ hoặc cụm tiếng Anh "
    "đọc tiếng Anh, không dịch, không thêm nội dung, không bỏ sót, không rút gọn: "
)


class GoogleLiveProvider(VoiceSessionProvider):
    """Google Live session provider for production robot speech."""

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
        self._fallback_provider = None
        self._fallback_activating = False
        self._reconnect_attempts = 0
        self._reconnecting = False
        self._closing = False
        self._lifecycle_lock = None
        self._live_open_lock = None
        self._session_generation = 0
        self._response_generation = 0
        self._cancelled_response_ids = set()
        self._input_flush_generation = 0
        self._forced_interrupt_flush_generation = 0
        self._loud_input_duration_sec = 0.0
        self._last_loud_input_at = None
        self._pending_reconnect_audio = deque(maxlen=self._get_reconnect_buffer_capacity())
        self._pending_interrupt_audio = deque(
            maxlen=self._get_interrupt_replay_buffer_capacity()
        )
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
        # Lesson child-response window bookkeeping. _last_lesson_prompt_len sizes
        # the pre-listen guard delay; the _user_stream_* fields mark an in-flight
        # user audio stream so a WAITING_MODEL frame is forwarded (not dropped)
        # while the lesson listening window is open.
        self._last_lesson_prompt_len = 0
        self._lesson_prompt_reopen_fast = False
        self._user_stream_response_id = None
        self._user_stream_started_at = None
        self._user_stream_last_speech_at = None
        self._user_stream_frames = 0
        # Conversation-turn finalization / waiting-model reopen bookkeeping.
        self._waiting_model_since = None
        self._last_waiting_model_retry_prompt_at = 0.0
        self._echo_bypass_pending_interrupt = False
        self._last_clean_user_turn_response_id = None
        self._interaction = GoogleLiveInteractionController(conn)
        self._idle_close_task = None
        self._voice_consent_denied = False

    async def start_session(self):
        async with self._get_lifecycle_lock():
            self._closing = False
            if self._client is not None and self._bridge is not None:
                self.conn.voice_provider = self
                return
            if not await self._voice_consent_allows_live():
                self._voice_consent_denied = True
                self.conn.voice_provider = self
                await self._send_voice_consent_required()
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live start denied: missing active AI voice consent"
                )
                return
            if self._has_session_orchestrator():
                self.conn.voice_provider = self
                self._voice_consent_denied = False
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live provider initialized dormant"
                )
                return
            try:
                await self._ensure_func_handler()
                await self._open_live_session()
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
        selected_intent = config.get("selected_module", {}).get("Intent")
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
        if self._fallback_provider is not None:
            return await self._fallback_provider.handle_text_message(message)
        listen_state, listen_text = self._extract_listen_control(message)
        if listen_state == "start":
            await self._open_user_audio_window("listen_start")
            return True
        if listen_state == "stop":
            await self._finalize_user_audio_input("listen_stop")
            return True
        text = self._extract_user_text_message(message)
        if text is None and listen_state == "detect":
            return True
        if text is None:
            return False
        try:
            if await self._dispatch_lesson_child_response(text):
                return True
            if self._is_local_stop_word(text):
                await self._handle_local_stop_word(text)
                return True
            if listen_state == "detect" and self._is_wake_word_only(text):
                await self._send_wake_listening_feedback(listen_text or text)
                await self._open_user_audio_window("wake_word")
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
                self._mark_clean_user_turn_opened("text_input")
                return True
            except Exception as exc:
                await self._handle_runtime_failure(exc)
                return True
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True

    async def handle_audio_bytes(self, audio_bytes):
        # Re-verify ACTIVE parental voice consent on every inbound frame, not just at
        # start_session: a mid-session withdrawal must stop voice BEFORE the next frame is
        # forwarded (tears down Live/classic and sends a voice_consent_required alert). When
        # allowed this is a cheap cached check that just clears the denied latch. Runs ahead
        # of the classic-fallback delegation so a withdrawal also stops the fallback.
        if not await self._ensure_active_voice_consent():
            return True
        if self._fallback_provider is not None:
            return await self._fallback_provider.handle_audio_bytes(audio_bytes)
        if self._has_session_orchestrator() and self._bridge is None:
            if not await self._ensure_live_open_for_audio():
                return True
        if self._reconnecting:
            if audio_bytes:
                self._pending_reconnect_audio.append(
                    (self._response_generation, audio_bytes)
                )
            return True
        if self._bridge is None:
            return True
        if await self._forward_lesson_child_audio(audio_bytes):
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
            else:
                self.conn.client_abort = False
                return True
        try:
            decoded_audio = None
            if hasattr(self._bridge, "decode_input_audio_async"):
                decoded_audio = await self._bridge.decode_input_audio_async(audio_bytes)
            elif hasattr(self._bridge, "decode_input_audio"):
                decoded_audio = self._bridge.decode_input_audio(audio_bytes)
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
            if self._should_interrupt_for_input(decoded_audio):
                await self._begin_user_interrupt("audio_input")
            elif self._should_drop_input_during_output():
                self._log_audio_decision("drop_input", "output_active", decoded_audio)
                self.conn.client_abort = False
                return True
            self.conn.client_abort = False
            if decoded_audio is not None and hasattr(
                self._bridge, "forward_decoded_input_audio"
            ):
                await self._bridge.forward_decoded_input_audio(decoded_audio)
            else:
                await self._bridge.forward_input_audio(audio_bytes)
            self._log_audio_decision("forward_input", "accepted", decoded_audio)
            if self._interrupt_capture_response_id == self._response_generation:
                self._interrupt_forwarded_once = True
            if not buffered_current_frame:
                self._record_interrupt_capture_audio(decoded_audio)
            if not buffered_current_frame:
                self._buffer_pending_interrupt_audio_while_blocked(decoded_audio)
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
        if not self._lesson_child_response_window_active():
            return False
        self._force_lesson_session_mode("lesson_child_audio")
        bridge = self._bridge
        if bridge is None or not hasattr(bridge, "forward_decoded_input_audio"):
            return False
        try:
            decoded = None
            decode_async = getattr(bridge, "decode_input_audio_async", None)
            if callable(decode_async):
                decoded = await decode_async(audio_bytes)
            elif hasattr(bridge, "decode_input_audio"):
                decoded = bridge.decode_input_audio(audio_bytes)
            if decoded is None:
                return False
            await bridge.forward_decoded_input_audio(decoded)
            # The child's continued audio closes the WAITING_MODEL turn so the
            # next lesson step's audio is not dropped.
            if self._interaction.state == InteractionState.WAITING_MODEL:
                self._interaction.transition(InteractionState.USER_STREAMING)
            self.conn.client_abort = False
            return True
        except Exception as exc:
            await self._handle_runtime_failure(exc)
            return True

    async def interrupt(self):
        if self._fallback_provider is not None:
            await self._fallback_provider.interrupt()
            return
        await self._begin_user_interrupt("explicit_interrupt")

    async def close(self):
        async with self._get_lifecycle_lock():
            self._closing = True
            await self._close_live_resources()
            if self._fallback_provider is not None:
                await self._fallback_provider.close()

    def _has_session_orchestrator(self):
        return hasattr(self.conn, "session_mode")

    async def _voice_consent_allows_live(self):
        client = getattr(self.conn, "voice_consent_client", None)
        if client is None:
            client = get_voice_consent_client()
        try:
            return bool(await client.ensure_voice_allowed(self.conn))
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live consent check failed: {}",
                self._safe_error_message(exc),
            )
            return False

    async def _ensure_active_voice_consent(self):
        if await self._voice_consent_allows_live():
            self._voice_consent_denied = False
            return True
        if not self._voice_consent_denied:
            await self._close_live_resources()
            if self._fallback_provider is not None:
                await self._fallback_provider.close()
        self._voice_consent_denied = True
        await self._send_voice_consent_required()
        return False

    async def _send_voice_consent_required(self):
        message = "Ask a parent to finish setup."
        payload = {
            "type": "alert",
            "status": "voice_consent_required",
            "session_id": getattr(self.conn, "session_id", None),
            "message": message,
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
                "Google Live consent prompt send failed: {}",
                self._safe_error_message(exc),
            )

    async def _ensure_live_open_for_audio(self):
        if normalize_session_mode(getattr(self.conn, "session_mode", SessionMode.DORMANT)) == SessionMode.LESSON:
            # In LESSON mode only an interactive (non-passive) step warrants
            # opening a Live session for the child's spoken answer; passive steps
            # stay TTS-only.
            if not self._active_lesson_step_is_interactive():
                return False
        decision = await self._admit_live_open()
        if decision.decision == AdmissionDecision.FRIENDLY_BREAK:
            await self._send_live_unavailable(decision.reason)
            return False
        if decision.decision == AdmissionDecision.DEGRADE_TTS_ONLY:
            await self._activate_budget_degrade(decision.reason)
            return False
        try:
            await self._ensure_func_handler()
            await self._open_live_session()
            self.conn.voice_provider = self
            self._voice_consent_denied = False
            return True
        except Exception as exc:
            await self._close_live_resources()
            handled = await self._activate_classic_fallback(exc)
            if not handled and self._should_raise_without_fallback(exc):
                raise
            return False

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

    async def _activate_budget_degrade(self, reason: AdmissionReason):
        await self._close_live_resources()
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

    def _idle_timeout_sec(self):
        config = getattr(self.conn, "config", {}) or {}
        live_admission = config.get("live_admission", {}) if isinstance(config, Mapping) else {}
        timeout = live_admission.get("idle_timeout_sec") if isinstance(live_admission, Mapping) else None
        if timeout is None:
            google_live = config.get("google_live", {}) if isinstance(config, Mapping) else {}
            timeout = google_live.get("idle_timeout_sec", 45) if isinstance(google_live, Mapping) else 45
        try:
            return max(0.0, float(timeout))
        except (TypeError, ValueError):
            return 45.0

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

    async def _on_user_transcript(self, transcript_text):
        if await self._dispatch_lesson_child_response(transcript_text):
            return True
        if await self._dispatch_lesson_start_intent(transcript_text):
            return True
        if self._is_live_wake_transcript_only(transcript_text):
            await self._send_wake_listening_feedback(transcript_text)
            await self._open_user_audio_window("wake_word")
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live wake_transcript_only text_preview={!r}",
                str(transcript_text or "")[:40],
            )
            return True
        if await self._dispatch_music_control_intent(transcript_text):
            return True
        return False

    async def _dispatch_lesson_start_intent(self, transcript_text):
        payload = self._classify_lesson_start_intent(transcript_text)
        if payload is None:
            self._log_lesson_start_intent_miss(transcript_text)
            return False
        await self._begin_user_interrupt("lesson_start_intent")
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
            result = await func_handler.handle_llm_function_call(self.conn, payload)
            await self._send_lesson_start_ack(result)
            # The local tool dispatch finished: release the realtime busy/interrupt
            # latch set by _begin_user_interrupt so the controller does not stay
            # stuck in INTERRUPTING with client_abort latched.
            self._interaction.transition(InteractionState.IDLE)
            self.conn.client_abort = False
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live lesson_start_intent tool={} text_preview={!r}",
                payload.get("name"),
                (transcript_text or "")[:40],
            )
            return True
        except Exception as exc:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live lesson_start_intent failed tool={} error={}",
                payload.get("name"),
                self._safe_error_message(exc),
            )
            return False

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
                "Google Live lesson_start_intent miss normalized={!r} text_preview={!r}",
                text[:80],
                str(transcript_text or "")[:80],
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
        self._lesson_prompt_reopen_fast = bool(continue_listening)
        if await self._send_live_text_ack(
            text,
            log_label="lesson_step_prompt",
            allow_lesson_output=True,
        ):
            return True
        return False

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

        open_delay = _read_float("lesson_child_response_open_delay_sec", 0.0)
        chars_per_sec = _read_float("lesson_prompt_tts_chars_per_sec", 12.0)
        max_open_delay = _read_float("lesson_child_response_max_open_delay_sec", 8.0)
        fast_reopen_sec = _read_float("lesson_child_response_fast_reopen_sec", 1.2)
        window_sec = _read_float("lesson_child_response_window_sec", 25.0)

        prompt_estimate = 0.0
        if chars_per_sec > 0 and self._last_lesson_prompt_len > 0:
            prompt_estimate = self._last_lesson_prompt_len / chars_per_sec
        delay = open_delay + prompt_estimate
        if max_open_delay > 0:
            delay = min(delay, max_open_delay)
        if self._lesson_prompt_reopen_fast and fast_reopen_sec >= 0:
            delay = min(delay, fast_reopen_sec)
        self._lesson_prompt_reopen_fast = False
        if delay > 0:
            # Call asyncio.sleep through the module-level asyncio so the test's
            # google_live_module.asyncio.sleep monkeypatch is honoured.
            await asyncio.sleep(delay)
        if not await self._wait_for_lesson_prompt_output_idle(config):
            return False
        if runtime is not None:
            current_runtime = getattr(self.conn, "lesson_runtime", None)
            if (
                current_runtime is not runtime
                or getattr(current_runtime, "_step_id", None) != guarded_step_id
                or not self._lesson_runtime_accepts_voice_input()
            ):
                return False

        self._user_audio_allowed_until = max(
            self._user_audio_allowed_until,
            time.monotonic() + max(0.1, window_sec),
        )
        self._force_lesson_session_mode("lesson_child_response_window")
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live lesson_child_response_window_open delay_sec={:.2f} window_sec={:.1f}",
            delay,
            window_sec,
        )
        return True

    def close_lesson_child_response_window(self):
        self._user_audio_allowed_until = 0.0
        self._user_stream_response_id = None
        self._user_stream_started_at = None
        self._user_stream_last_speech_at = None
        self._user_stream_frames = 0
        if self._interaction.state == InteractionState.USER_STREAMING:
            self._interaction.transition(InteractionState.LISTENING)

    async def _wait_for_lesson_prompt_output_idle(self, config):
        poll_sec = self._read_lesson_guard_float(
            config, "lesson_prompt_output_poll_sec", 0.1
        )
        poll_sec = max(0.01, poll_sec)
        output_timeout = self._read_lesson_guard_float(
            config, "lesson_prompt_output_guard_timeout_sec", 15.0
        )
        playback_timeout = self._read_lesson_guard_float(
            config, "lesson_prompt_playback_guard_timeout_sec", 12.0
        )
        playback_tail = self._read_lesson_guard_float(
            config, "lesson_prompt_playback_tail_sec", 0.6
        )

        remaining = max(0.0, output_timeout)
        while getattr(self.conn, "google_live_lesson_prompt_output_allowed", False):
            if remaining <= 0:
                self.conn.google_live_lesson_prompt_output_allowed = False
                self.conn.logger.bind(tag="GoogleLive").warning(
                    "Google Live lesson_prompt_output_guard_timeout timeout_sec={:.1f}",
                    output_timeout,
                )
                return False
            sleep_for = min(poll_sec, remaining)
            await asyncio.sleep(sleep_for)
            remaining -= sleep_for

        rate_controller = getattr(self.conn, "audio_rate_controller", None)
        queue_empty_event = getattr(rate_controller, "queue_empty_event", None)
        queue_obj = getattr(rate_controller, "queue", None)
        if queue_empty_event is None or queue_obj is None:
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
                queue_empty_event.wait(),
                timeout=max(0.01, playback_timeout),
            )
            if playback_tail > 0:
                await asyncio.sleep(playback_tail)
        except asyncio.TimeoutError:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live lesson_prompt_playback_guard_timeout timeout_sec={:.1f} queue_len={}",
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

    def _lesson_child_response_window_active(self):
        """True when a child-response window is open AND the active lesson
        runtime is interactive and running."""
        if time.monotonic() >= self._user_audio_allowed_until:
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
        if getattr(runtime, "_child_response_window_open", True) is False:
            return False
        state = getattr(runtime, "state", None)
        if state is not None and state not in ("RUNNING",):
            return False
        return True

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
        if not self._lesson_child_response_window_active():
            return None
        runtime = self.conn.lesson_runtime
        self._force_lesson_session_mode("lesson_child_response")
        # Block model output BEFORE advancing the runtime so the runtime never
        # races the model's audio for this turn.
        if self._bridge is not None and hasattr(self._bridge, "stop_output"):
            await self._bridge.stop_output()
        # Close any open WAITING_MODEL turn so next-step audio is forwarded.
        self._interaction.transition(InteractionState.LISTENING)
        handled = await runtime.on_child_response(
            transcript_text, source="voice_transcript"
        )
        if handled:
            return True
        if not str(transcript_text or "").strip():
            return False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live lesson_child_response_consumed_unhandled text_preview='{}'",
            str(transcript_text or "").strip()[:80],
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

    def _lesson_start_ack_text(self, action_response):
        fallback = "Bắt đầu bài học nhé."
        if action_response is None:
            return fallback
        action = getattr(action_response, "action", None)
        result = str(getattr(action_response, "result", "") or "").lower()
        response = str(getattr(action_response, "response", "") or "").strip()
        if action == Action.ERROR:
            return response or "Xin lỗi, robot chưa bắt đầu bài học được."
        if action == Action.RESPONSE and any(
            marker in result for marker in ("disabled", "busy", "failed", "error")
        ):
            return response or "Robot chưa bắt đầu bài học được."
        return fallback

    async def _send_live_text_ack(self, text, *, log_label="lesson_start_ack", allow_lesson_output=False):
        if not await self._ensure_live_open_for_lesson_text():
            return False
        client = self._client
        if client is None or not hasattr(client, "send_text"):
            return False
        try:
            if allow_lesson_output:
                self.conn.google_live_lesson_prompt_output_allowed = True
            if self._bridge is not None:
                if allow_lesson_output and hasattr(self._bridge, "force_allow_model_output"):
                    self._bridge.force_allow_model_output()
                elif hasattr(self._bridge, "allow_model_output"):
                    self._bridge.allow_model_output()
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

    async def _ensure_live_open_for_lesson_text(self):
        client = self._client
        if client is not None and hasattr(client, "send_text"):
            return True
        if not self._has_session_orchestrator():
            return False
        if not await self._ensure_active_voice_consent():
            return False
        decision = await self._admit_live_open()
        if decision.decision == AdmissionDecision.FRIENDLY_BREAK:
            await self._send_live_unavailable(decision.reason)
            return False
        if decision.decision == AdmissionDecision.DEGRADE_TTS_ONLY:
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live lesson_text_open degraded reason={}",
                decision.reason.value,
            )
            return False
        try:
            await self._ensure_func_handler()
            await self._open_live_session()
            self.conn.voice_provider = self
            self._voice_consent_denied = False
            return self._client is not None and hasattr(self._client, "send_text")
        except Exception as exc:
            await self._close_live_resources()
            self.conn.logger.bind(tag="GoogleLive").warning(
                "Google Live lesson_text_open failed: {}",
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
        # Production Google Live ASR has misheard the Vietnamese command
        # "bắt đầu bài học" as this exact phrase. Keep it exact to avoid turning
        # arbitrary English "high speed" mentions inside longer utterances into a
        # lesson start command.
        exact_markers = {
            "high speed",
            "hi speed",
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
            "Google Live local_stop_word detected text_preview={!r}",
            str(text or "")[:40],
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
        try:
            async for event in self._client.receive_events():
                if generation != self._session_generation:
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
            await self._handle_runtime_failure(exc)
        else:
            if not self._closing and self._fallback_provider is None:
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

        if await self._try_reconnect(exc):
            return
        if self._closing:
            return

        self._fallback_activating = True
        try:
            await self._close_live_resources()
            await self._activate_classic_fallback(exc)
        finally:
            self._fallback_activating = False

    async def _close_live_resources(self):
        current_task = asyncio.current_task()
        receive_task = self._receive_task
        flush_task = self._input_flush_task
        forced_interrupt_flush_task = self._forced_interrupt_flush_task
        waiting_model_timeout_task = self._waiting_model_timeout_task
        proactive_task = self._proactive_reconnect_task
        idle_task = self._idle_close_task
        self._receive_task = None
        self._input_flush_task = None
        self._forced_interrupt_flush_task = None
        self._waiting_model_timeout_task = None
        self._proactive_reconnect_task = None
        self._idle_close_task = None
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
        if restore_session_resumption:
            await self._restore_session_resumption_handle()
        self._session_generation += 1
        generation = self._session_generation
        self._interaction.start_live_connection(generation)
        self._cancelled_response_ids.clear()
        self._pending_tool_calls.clear()
        self._cancelled_tool_call_ids.clear()
        self._client = self._client_factory(
            self._get_live_config_with_functions(),
            self.conn.logger,
        )
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
        if prompt:
            config["system_prompt"] = self._augment_prompt_with_child_name(prompt)
        return config

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
        self._interaction.transition(InteractionState.WAITING_MODEL)
        self._waiting_model_since = time.monotonic()
        self._schedule_waiting_model_timeout_task()
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
        self._interaction.transition(InteractionState.WAITING_MODEL)
        self._waiting_model_since = time.monotonic()
        self._schedule_waiting_model_timeout_task()
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
        value = config.get("input_max_capture_ms") if isinstance(config, Mapping) else None
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
            await self._maybe_queue_waiting_model_retry_prompt()
            self._interaction.transition(InteractionState.LISTENING)
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
        if event_type == "audio_start":
            self._cancel_waiting_model_timeout_task()
        if event_type == "audio_end":
            if self._interaction.state == InteractionState.WAITING_MODEL:
                self._cancel_waiting_model_timeout_task()
                self._interaction.transition(InteractionState.LISTENING)
                self._waiting_model_since = None

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
            # The idle-flush safety-net must be a COMPLETE finalize: transition to
            # WAITING_MODEL, stamp the wait, and clear the per-turn user-stream
            # bookkeeping so the next utterance is not truncated by a stale
            # _user_stream_started_at.
            self._interaction.transition(InteractionState.WAITING_MODEL)
            self._waiting_model_since = time.monotonic()
            self._schedule_waiting_model_timeout_task()
            self._clear_user_stream()
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

    def _get_input_flush_delay(self):
        config = self._get_live_config()
        if self._uses_lesson_input_timing():
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
            waiting_timeout = float(merged.get("waiting_model_timeout_sec", 2.0))
        except (TypeError, ValueError):
            waiting_timeout = 2.0
        merged["waiting_model_timeout_sec"] = min(max(0.0, waiting_timeout), 2.0)
        merged["interruption_min_output_age_sec"] = 0.0
        merged["barge_in_transcript_min_output_age_sec"] = 0.0
        merged["disable_server_side_interruptions"] = False
        merged["activity_handling"] = "START_OF_ACTIVITY_INTERRUPTS"
        merged["barge_in"] = False
        merged["interrupt_on_input_while_speaking"] = False
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
        if getattr(self.conn, "google_live_turn_started_at", None) is None:
            self.conn.google_live_turn_started_at = time.monotonic()
        if self._last_clean_user_turn_response_id == self._response_generation:
            return
        self._last_clean_user_turn_response_id = self._response_generation
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live clean_user_turn_opened reason={} response_id={}",
            reason,
            self._response_generation,
        )

    async def _on_user_transcript_barge_in(self, transcript_text):
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

    def _get_user_audio_window_sec(self, reason):
        config = self._get_live_config()
        if self._active_lesson_step_is_interactive():
            window_key = "lesson_child_response_window_sec"
            window_default = 25.0
        else:
            window_key = "wake_audio_allow_window_sec"
            window_default = 5.0
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
        if self._has_active_output() or self._has_music_session():
            await self._begin_user_interrupt(reason)
        self.conn.client_abort = False
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_audio_window_open reason={} window_ms={:.0f}",
            reason,
            max(0.1, window_sec) * 1000,
        )

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
                "Google Live music_control_intent tool={} text_preview={!r}",
                payload.get("name"),
                (transcript_text or "")[:40],
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
        return False

    def _is_live_wake_transcript_only(self, text):
        normalized = self._normalize_intent_text(text)
        if not normalized:
            return False
        if self._is_wake_word_only(text):
            return True
        wake_aliases = {
            "hi esp",
            "hai esp",
            "hey esp",
            "hi spy",
            "hai spy",
            "hey spy",
            "i spy",
            "high spy",
            "hi tam",
            "hai tam",
            "hey tam",
        }
        return normalized in wake_aliases

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
        if now < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            reason = "echo_tail"
        elif self._has_active_output():
            reason = "robot_speaking"
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
            log_key = "aec_live_vad_forward"
            last_at = self._last_echo_suppressed_log_at.get(log_key, 0.0)
            if now - last_at >= 1.0:
                self._last_echo_suppressed_log_at[log_key] = now
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live aec_live_vad_forward reason={} bytes={} rms={} "
                    "(throttled to 1/s)",
                    reason,
                    len(pcm_audio or b""),
                    rms,
                )
            return False
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

    def _can_forward_aec_audio_for_live_vad(self, config):
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
        if time.monotonic() < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            return "echo_tail"
        if self._has_active_output():
            return "robot_speaking"
        if self._has_audible_music_session():
            return "music_playing"
        return "unknown"

    def _current_interaction_state_for_audio(self):
        if self._reconnecting:
            return InteractionState.RECONNECTING
        if time.monotonic() < getattr(self.conn, "google_live_echo_suppress_until", 0.0):
            return InteractionState.MUTED
        if self._has_active_output():
            return InteractionState.MODEL_SPEAKING
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
            window_sec = float(config.get("wake_audio_allow_window_sec", 5.0))
        except (TypeError, ValueError):
            window_sec = 5.0
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

    async def _handle_tool_call_event(self, event):
        calls = event.get("calls") if isinstance(event, Mapping) else None
        if not calls:
            return
        if normalize_session_mode(
            getattr(self.conn, "session_mode", SessionMode.DORMANT)
        ) == SessionMode.LESSON:
            responses = []
            for call in calls:
                call_id = call.get("id") if isinstance(call, Mapping) else None
                name = call.get("name") if isinstance(call, Mapping) else None
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
            try:
                self.conn.logger.bind(tag="GoogleLive").info(
                    "Google Live lesson_mode_tool_call_blocked count={} names={}",
                    len(responses),
                    ",".join(str(r.get("name") or "") for r in responses),
                )
            except Exception:
                pass
            if self._client is not None:
                await self._client.send_tool_response(responses)
            return
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
            self.conn.logger.bind(tag="GoogleLive").info(
                "tool_call_dispatched name={} response_id={}",
                name,
                self._response_generation,
            )
            args = call.get("args") if isinstance(call, Mapping) else {}
            if call_id is not None:
                self._pending_tool_calls.add(call_id)
                self._cancelled_tool_call_ids.discard(call_id)
            started_at = time.monotonic()
            response_payload = await self._execute_tool_call_with_timeout(
                name,
                args,
                call_id=call_id,
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

    async def _execute_tool_call_with_timeout(self, name, args, call_id=None):
        try:
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
            result = await func_handler.handle_llm_function_call(
                self.conn,
                {"name": name, "arguments": args},
            )
            try:
                action_name = getattr(getattr(result, "action", None), "name", "?")
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
        text = ""
        if getattr(action_response, "response", None):
            text = str(action_response.response)
        elif getattr(action_response, "result", None):
            text = str(action_response.result)
        action_name = action.name.lower() if action is not None and hasattr(action, "name") else None
        if action is not None and action == Action.ERROR:
            return self._tool_error("TOOL_ERROR", text or "tool error")
        if action is not None and action == Action.NOTFOUND:
            return self._tool_error("TOOL_NOT_FOUND", text or "tool not found")
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
            "transcript_barge_in",
            "loud_input",
        }
    )

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
            await self._bridge.stop_output()
        else:
            if hasattr(self.conn, "clear_queues"):
                self.conn.clear_queues()
            if hasattr(self.conn, "clearSpeakStatus"):
                self.conn.clearSpeakStatus()
        if self._client is not None and hasattr(self._client, "interrupt"):
            await self._client.interrupt()
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
            await self._hard_reconnect_after_interrupt(reason)
        if reason in {"audio_input", "loud_input"}:
            self._schedule_forced_interrupt_input_flush(reason)
        self.conn.logger.bind(tag="GoogleLive").info(
            "Google Live user_interrupted reason={} cancelled_response_id={} next_response_id={}",
            reason,
            previous_response_id,
            self._response_generation,
        )

    def _should_hard_reconnect_on_interrupt(self):
        config = self._get_live_config()
        return bool(config.get("hard_reconnect_on_interrupt", False))

    async def _hard_reconnect_after_interrupt(self, reason):
        if self._closing or self._fallback_provider is not None or self._reconnecting:
            return
        self._reconnecting = True
        try:
            await self._close_live_resources()
            await self._open_live_session()
            self.conn.voice_provider = self
            self.conn.logger.bind(tag="GoogleLive").info(
                "Google Live hard_reconnected_after_interrupt reason={} response_id={}",
                reason,
                self._response_generation,
            )
        except Exception as exc:
            await self._handle_runtime_failure(exc)
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
        return getattr(self.conn, "google_live_audio_out_started_at", None) is not None

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
