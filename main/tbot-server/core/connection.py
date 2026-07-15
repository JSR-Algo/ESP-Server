import os
import sys
import copy
import hashlib
import json
import re
import uuid
import time
import queue
import asyncio
import threading
import traceback
import subprocess
from contextlib import suppress
from datetime import datetime, timezone
import websockets

from core.utils.util import (
    extract_json_from_string,
    check_vad_update,
    check_asr_update,
    filter_sensitive_info,
)
from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import (
    initialize_modules,
    initialize_tts,
    initialize_asr,
)
from core.handle.reportHandle import report, enqueue_tool_report
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action, ActionResponse
from core.auth import AuthenticationError
from config.config_loader import (
    get_private_config_from_api,
    merge_configs,
    normalize_voice_config,
)
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import DeviceNotFoundException, DeviceBindException, generate_and_save_chat_title
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.voice.session_provider.factory import create_voice_session_provider
from core.utils.util import get_system_error_response
from core.utils import textUtils
from core.voice.live_admission import LiveAdmissionGate, create_live_state_store
from core.voice.session_orchestrator import SessionMode, normalize_session_mode
from core.activity_lease import ActivityLeaseCoordinator


TAG = __name__

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "x-mint-secret",
    "cookie",
    "set-cookie",
}


def _sanitize_headers_for_log(headers):
    if not isinstance(headers, dict):
        return {}
    sanitized = {}
    for key, value in headers.items():
        name = str(key)
        if name.casefold() in _SENSITIVE_HEADER_NAMES:
            sanitized[name] = "<redacted>"
        else:
            sanitized[name] = value
    return sanitized


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lesson_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    lesson_cfg = config.get("lesson", {}) or {}
    return lesson_cfg if isinstance(lesson_cfg, dict) else {}


def _private_config_log_summary(private_config: Dict[str, Any]) -> Dict[str, Any]:
    google_live = private_config.get("google_live") or {}
    if not isinstance(google_live, dict):
        google_live = {}
    return {
        "voice_mode": private_config.get("voice_mode"),
        "selected_module": private_config.get("selected_module"),
        "google_live": {
            "model": google_live.get("model"),
            "language_code": google_live.get("language_code"),
            "input_sample_rate": google_live.get("input_sample_rate"),
            "output_sample_rate": google_live.get("output_sample_rate"),
            "barge_in": google_live.get("barge_in"),
            "drop_input_while_speaking": google_live.get("drop_input_while_speaking"),
        },
        "has_prompt": private_config.get("prompt") is not None,
        "has_plugins": bool(private_config.get("plugins")),
    }


def _connection_close_log_metadata(exc) -> str:
    """Return useful close evidence without logging peer-controlled reason text."""
    close_frame = getattr(exc, "rcvd", None)
    if close_frame is None:
        close_frame = getattr(exc, "sent", None)
    code = getattr(close_frame, "code", None)
    if not isinstance(code, int) or isinstance(code, bool):
        code = "-"
    reason = getattr(close_frame, "reason", "")
    if not isinstance(reason, str):
        reason = ""
    reason_hash = (
        hashlib.sha256(reason.encode("utf-8", errors="replace")).hexdigest()[:12]
        if reason
        else "-"
    )
    return (
        f"close_code={code} close_reason_sha256={reason_hash} "
        f"close_reason_length={len(reason)}"
    )


auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass

# direct_answer VirtualTool definition
# Not real tool, is routing mechanism: send"Use tool or not"choose one of two becomes"Which one"Multi-select, prevent small model accidentally triggering real tools
DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "When user request does not match any other tool, use this option to reply directly. Put reply content in response parameter.",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "Full content you reply to user",
                },
            },
            "required": ["response"],
        },
    },
}


class ConnectionHandler:
    def __init__(
            self,
            config: Dict[str, Any],
            _vad,
            _asr,
            _llm,
            _memory,
            _intent,
            server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # Saveserverinstance reference

        self.need_bind = False  # NeedBind device
        self.bind_completed_event = asyncio.Event()
        self.bind_code = None  # Bind deviceofVerification code
        self.last_bind_prompt_time = 0  # Last playback bindingPromptofTimestamp(seconds)
        self.bind_prompt_interval = 60  # BindPromptPlayback Interval(seconds)

        self.read_config_from_api = self.config.get("read_config_from_api", False)

        self.websocket: websockets.ServerConnection | None = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 0
        self.audio_format = "opus"
        self.sample_rate = 24000  # Default sample rate, from client hello MessageDynamically update in

        # ClientStatusRelated
        self.client_abort = False
        self.client_is_speaking = False
        self.client_listen_mode = "auto"
        self.google_live_audio_out_started_at = None
        self.google_live_turn_started_at = None
        self.voice_metric_samples = deque(maxlen=100)
        self._lesson_asset_audio_inflight = 0

        # Thread task related
        self.loop = None  # in handle_connection Get running fromEventLoop
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

        # Add report thread pool
        self.report_queue = queue.Queue()
        self.report_thread = None
        # Future can viaModifyHere, adjustasrreport andttsreporting, currently enabled by default
        self.report_asr_enable = self.read_config_from_api
        self.report_tts_enable = self.read_config_from_api

        # Dependent component
        self.vad = None
        self.asr = None
        self.tts = None
        self._lesson_tts_lock = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.memory = _memory
        self.intent = _intent

        # Manage voiceprint recognition separately for each connection
        self.voiceprint_provider = None
        self.voice_provider = None
        self.voice_provider_task = None
        self.session_mode = SessionMode.DORMANT
        self.audio_channel_owner = SessionMode.DORMANT
        self.last_live_activity_at = None
        self.google_live_session_resumption_handle = None
        self.live_state_store = create_live_state_store(self.config)
        self.live_resumption_store = self.live_state_store
        self.live_admission_gate = LiveAdmissionGate.from_config(self.config, self.live_state_store)

        # US-006 lesson runtime (additive; dark unless lesson.runtime_enabled).
        # Holds the per-device lesson session state when a lesson is in flight.
        self.lesson_runtime = None
        self.lesson_runtime_candidate = None
        self.lesson_pull_task = None
        self._lesson_preload_reset_waiter = None
        self.sd_pack_sync_task = None
        self.safety_event_forwarder = None
        self.mcp_client = None
        self.mcp_background_tasks = set()
        self.mcp_tasks_closed = False
        self._lesson_pull_lock = asyncio.Lock()
        self.activity_leases = None
        self._activity_leases_closed = False
        try:
            self.activity_leases = ActivityLeaseCoordinator(asyncio.get_running_loop())
        except RuntimeError:
            pass
        # S13 voice-latency-during-preload auto-disable alarm (plan §11.2 / CP-8);
        # lazily created on the first lesson pull. None until a lesson runs.
        self.lesson_voice_alarm = None

        # vadRelated Variables
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_window = deque(maxlen=5)
        self.first_activity_time = 0.0  # Record first activity time (ms)
        self.last_activity_time = 0.0  # Unified activityTimestamp(ms)
        self.vad_last_voice_time = 0.0  # Record user's last speaking time (ms)
        self.client_voice_stop = False
        self.last_is_voice = False

        # asrRelated Variables
        # Because actual deployment may use public localASR, cannot expose variables to publicASR
        # So related toASRVariables need define here,Belongs toconnectionprivate variable
        self.asr_audio = []
        self.asr_audio_queue = queue.Queue()
        self.current_speaker = None  # Store current speaker

        # llmRelated Variables
        self.dialogue = Dialogue()

        # ttsRelated Variables
        self.sentence_id = None
        # ProcessTTSResponseNo text returned
        self.tts_MessageText = ""

        # iotRelated Variables
        self.iot_descriptors = {}
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]

        # Whether close connection after chat ends
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_seconds = max(
            int(self.config.get("close_connection_no_voice_time", 120)) + 60,
            61 * 60,
        )  # Keep WebSocket alive for 60-minute Live/lesson sessions.
        self.timeout_task = None

        # {"mcp":true} IndicateEnableMCPFunction
        self.features = None

        # Mark whether connection comes fromMQTT
        self.conn_from_mqtt_gateway = False

        # InitializePromptWord Manager
        self.prompt_manager = PromptManager(self.config, self.logger)

    async def handle_connection(self, ws: websockets.ServerConnection):
        try:
            # Get runningEventLoop (must be in async context)
            self.loop = asyncio.get_running_loop()
            self._ensure_activity_leases()

            # Get and validateheaders
            self.headers = dict(ws.request.headers)
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:  # pragma: no cover - exercised by websocket integration tests
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {_sanitize_headers_for_log(self.headers)}"
            )

            self.device_id = self.headers.get("device-id", None)

            # Auth Passed,Continue Processing
            self.websocket = ws

            # Check whether fromMQTTConnect
            request_path = ws.request.path
            self.conn_from_mqtt_gateway = request_path.endswith("?from=mqtt_gateway")
            if self.conn_from_mqtt_gateway:  # pragma: no cover - integration header variant
                self.logger.bind(tag=TAG).info("Connection from: MQTT gateway")

            # Initialize activityTimestamp
            self.first_activity_time = time.time() * 1000
            self.last_activity_time = time.time() * 1000

            # Start timeout check task
            self.timeout_task = asyncio.create_task(self._check_timeout())

            self.welcome_msg = self.config["tbot"]
            self.welcome_msg["session_id"] = self.session_id

            # Read sample rate from config
            self.sample_rate = self.welcome_msg["audio_params"]["sample_rate"]
            self.logger.bind(tag=TAG).info(f"Set output audio sample rate to: {self.sample_rate}")

            if not self.read_config_from_api:
                self.voice_provider = create_voice_session_provider(self)
                await self.voice_provider.start_session()
            self.voice_provider_task = asyncio.create_task(
                self._initialize_voice_session_async()
            )

            # US-006 pull-on-connect (authoritative lesson hand-off, ADR 0013 §A/§B).
            # Dark unless lesson.runtime_enabled; runs as a side task so it can NEVER
            # delay or intercept the realtime voice path.
            if self._lesson_runtime_enabled():
                self.lesson_pull_task = asyncio.create_task(self._lesson_pull_on_connect())

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed as close_exc:  # pragma: no cover - websocket integration close path
                self.logger.bind(tag=TAG).info(
                    f"Client disconnected device_id={self.device_id or ''} "
                    f"client_ip={self.client_ip or ''} "
                    f"{_connection_close_log_metadata(close_exc)}"
                )

        except AuthenticationError as e:  # pragma: no cover - auth middleware integration path
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:  # pragma: no cover - top-level connection safety net
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:  # pragma: no cover - cleanup safety net
                self.logger.bind(tag=TAG).error(f"Error during final cleanup: {final_error}")
                # Ensure EvenSaveIf memory fails, also close connection
                try:
                    await self.close(ws)
                except Exception as close_error:  # pragma: no cover - cleanup safety net
                    self.logger.bind(tag=TAG).error(
                        f"Error force closing connection: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """Save memory and close connection"""
        try:
            # Daemon Thread1: independently generateTitle(not relying on memory model)
            if self.session_id:
                def generate_title_task():
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            generate_and_save_chat_title(self.session_id)
                        )
                    except Exception as e:  # pragma: no cover - daemon title best-effort failure
                        self.logger.bind(tag=TAG).error(f"Failed to generate title: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:  # pragma: no cover - event-loop close best-effort failure
                            pass

                threading.Thread(target=generate_title_task, daemon=True).start()

            # Daemon Thread2: use old memory flowSave(Memory only, noTitle)
            if self.memory:
                # Use thread pool asyncSaveMemory
                def save_memory_task():
                    try:
                        # Create newEventLoop (avoid conflict with main loop)
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(
                                self.dialogue.dialogue, self.session_id
                            )
                        )
                    except Exception as e:  # pragma: no cover - daemon memory best-effort failure
                        self.logger.bind(tag=TAG).error(f"Failed to save memory: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:  # pragma: no cover - event-loop close best-effort failure
                            pass

                # Start ThreadSaveMemory, do not wait for completion
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:  # pragma: no cover - save/close outer safety net
            self.logger.bind(tag=TAG).error(f"Failed to save memory: {e}")
        finally:
            # Close connection immediately, do not wait for memorySaveComplete
            try:
                await self.close(ws)
            except Exception as close_error:  # pragma: no cover - close safety net
                self.logger.bind(tag=TAG).error(
                    f"Failed to close connection after saving memory: {close_error}"
                )

    async def _discard_message_with_bind_prompt(self):
        """Discard message and check whether binding prompt needs playback"""
        if self.tts is None:
            return
        if self._google_live_mode_configured():
            return
        current_time = time.time()
        # Check whether need play bindingPrompt
        if current_time - self.last_bind_prompt_time >= self.bind_prompt_interval:
            self.last_bind_prompt_time = current_time
            # Reuse existing bindingPromptLogic
            from core.handle.receiveAudioHandle import check_bind_device

            asyncio.create_task(check_bind_device(self))

    async def _route_message(self, message):
        """Message routing"""
        self.last_activity_time = time.time() * 1000
        if (
            isinstance(message, str)
            and self._google_live_mode_configured()
            and self._is_listen_control_message(message)
        ):
            await self._wait_for_voice_provider_ready()
            if self.voice_provider is not None:
                await self.voice_provider.handle_text_message(message)
            return

        if isinstance(message, bytes) and self._google_live_mode_configured():
            await self._wait_for_voice_provider_ready()
            await self._route_audio_message(message)
            return

        if isinstance(message, str) and (
            self._is_hello_message(message)
            or self._is_ping_message(message)
            or self._is_mcp_message(message)
            or self._is_lesson_control_message(message)
        ):
            await handleTextMessage(self, message)
            return

        # Check whether real binding already obtainedStatus
        if not self.bind_completed_event.is_set():
            # Still not got realStatuswait until real is obtainedStatusOr timeout
            try:
                await asyncio.wait_for(self.bind_completed_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                # Timed out but real not gotStatus, discardMessage
                await self._discard_message_with_bind_prompt()
                return

        # Already got realStatusCheck whether binding needed
        if self.need_bind:
            # Need bind, discardMessage
            await self._discard_message_with_bind_prompt()
            return

        # No binding needed, continue processingMessage
        await self._wait_for_voice_provider_ready()

        if isinstance(message, str):
            if self.voice_provider is not None:
                handled = await self.voice_provider.handle_text_message(message)
                if handled:
                    return
            if self._google_live_mode_configured():
                return
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            handled = await self._route_audio_message(message)
            if handled:
                return

            if self.vad is None or self.asr is None:
                return

            # Handle FromMQTTgateway audio packet
            if self.conn_from_mqtt_gateway and len(message) >= 16:
                handled = await self._process_mqtt_audio_message(message)
                if handled:  # pragma: no cover - MQTT gateway handled branch covered in integration
                    return  # pragma: no cover - MQTT gateway handled branch covered in integration

            # When no header processing needed or no header, process raw directlyMessage
            self.asr_audio_queue.put(message)

    async def _route_audio_message(self, message: bytes) -> bool:
        self._lesson_asset_audio_inflight += 1
        try:
            return await self._route_audio_message_impl(message)
        finally:
            self._lesson_asset_audio_inflight = max(
                0, self._lesson_asset_audio_inflight - 1
            )

    async def _route_audio_message_impl(self, message: bytes) -> bool:
        """Route one inbound audio frame through the single audio-channel owner.

        `LESSON` owns the channel exclusively, so Live/classic voice does not see
        raw mic audio while scripted lesson audio is running, except while an
        interactive lesson step is explicitly waiting for a child response.
        `DORMANT` lazily enters conversation mode before the selected voice
        provider handles audio.
        """
        if self._lesson_runtime_active():
            if normalize_session_mode(self.session_mode) != SessionMode.LESSON:
                self._set_session_mode(SessionMode.LESSON, reason="lesson_runtime_active")
            if not self._lesson_runtime_accepts_voice_input():
                return True
            if self.voice_provider is None:  # pragma: no cover - defensive lesson provider guard
                return True
            handled = await self.voice_provider.handle_audio_bytes(message)
            if handled:
                self.last_activity_time = time.time() * 1000
                self.last_live_activity_at = time.monotonic()
            return True
        if normalize_session_mode(self.session_mode) == SessionMode.LESSON:
            if not self._lesson_runtime_accepts_voice_input():
                return True
            if self.voice_provider is None:  # pragma: no cover - defensive lesson provider guard
                return True
            handled = await self.voice_provider.handle_audio_bytes(message)
            if handled:
                self.last_activity_time = time.time() * 1000
                self.last_live_activity_at = time.monotonic()
            return True
        if self.voice_provider is None:
            if self._google_live_mode_configured():
                return True
            return False
        if normalize_session_mode(self.session_mode) == SessionMode.DORMANT:
            await self.enter_conversation_mode(reason="inbound_audio")
        handled = await self.voice_provider.handle_audio_bytes(message)
        if handled:
            self.last_activity_time = time.time() * 1000
            self.last_live_activity_at = time.monotonic()
        return bool(handled)

    def _lesson_runtime_active(self) -> bool:
        runtime = getattr(self, "lesson_runtime", None)
        if runtime is None:
            return False
        runtime_state = str(getattr(runtime, "state", "")).upper()
        return runtime_state in {"PRELOADING", "RUNNING"}

    def _google_live_mode_configured(self) -> bool:
        voice_mode = (self.config or {}).get("voice_mode") or {}
        return isinstance(voice_mode, dict) and voice_mode.get("type") == "google_live"

    def _lesson_runtime_accepts_voice_input(self) -> bool:
        runtime = getattr(self, "lesson_runtime", None)
        if runtime is None:
            return False
        runtime_state = str(getattr(runtime, "state", "")).upper()
        return (
            runtime_state == "RUNNING"
            and getattr(runtime, "_step", None) is not None
            and getattr(runtime, "_step_id", None) is not None
            and not bool(getattr(runtime, "_step_passive", True))
            and not bool(getattr(runtime, "_step_completed", False))
        )

    def _set_session_mode(self, mode, *, reason: str = "") -> SessionMode:
        mode = normalize_session_mode(mode)
        self.session_mode = mode
        self.audio_channel_owner = mode
        try:
            self.logger.bind(tag=TAG).info(
                "session_mode_changed mode={} reason={}",
                mode.value,
                reason,
            )
        except Exception:
            pass
        return mode

    async def enter_conversation_mode(self, *, reason: str = "conversation") -> bool:
        if normalize_session_mode(self.session_mode) == SessionMode.LESSON:
            return False
        self._set_session_mode(SessionMode.CONVERSATION, reason=reason)
        provider = self.voice_provider
        # Prefer ensure_live_ready/prewarm over start_session (dormant path never opens Live).
        if provider is not None and getattr(provider, "_client", None) is None:
            ensure = getattr(provider, "ensure_live_ready", None)
            if callable(ensure):
                try:
                    await ensure(reason=reason)
                except Exception as exc:
                    self.logger.bind(tag=TAG).warning(
                        "ensure_live_ready failed reason={} error={}",
                        reason,
                        exc,
                    )
            else:
                schedule = getattr(provider, "_schedule_live_prewarm", None)
                if callable(schedule):
                    schedule(reason or "conversation", delay_sec=0.0)
                else:
                    start_session = getattr(provider, "start_session", None)
                    if callable(start_session):
                        await start_session()
        self.last_live_activity_at = time.monotonic()
        return True

    async def enter_dormant_mode(self, *, reason: str = "dormant") -> None:
        self._set_session_mode(SessionMode.DORMANT, reason=reason)

    async def enter_lesson_mode(self, *, reason: str = "lesson_start") -> None:
        # Keep the Google Live session open while lesson mode owns the audio
        # channel. Closing it here races with in-flight mic frames and forces a
        # reconnect flicker just as the device starts rendering the lesson.
        await self._persist_live_resumption_handle()
        self._set_session_mode(SessionMode.LESSON, reason=reason)

    async def request_lesson_preload_reset(
        self, *, assignment_id: str, lesson_id: str, profile: str
    ) -> bool:
        """Quiesce a stale firmware lesson before retrying the internal SD sync."""
        ws = self.websocket
        if ws is None:
            return False
        session_id = f"preload-reset-{uuid.uuid4()}"
        future = asyncio.get_running_loop().create_future()
        self._lesson_preload_reset_waiter = {
            "sessionId": session_id,
            "future": future,
        }
        frame = {
            "protocolVersion": "teebot-lesson-renderer.v1",
            "type": "lesson_prepare",
            "assignmentId": assignment_id,
            "lessonId": lesson_id,
            "sessionId": session_id,
            "sequence": 1,
            "stepId": None,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "body": {
                "assignmentVersion": 0,
                "profile": profile,
                "preloadResetOnly": True,
            },
        }
        lesson_cfg = self.config.get("lesson", {}) if isinstance(self.config, dict) else {}
        timeout = lesson_cfg.get("sd_sync_reset_timeout_sec", 3.0) if isinstance(lesson_cfg, dict) else 3.0
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
            return bool(await asyncio.wait_for(future, timeout=max(0.1, float(timeout))))
        except (asyncio.TimeoutError, TypeError, ValueError):
            return False
        finally:
            if self._lesson_preload_reset_waiter is not None and self._lesson_preload_reset_waiter.get(
                "sessionId"
            ) == session_id:
                self._lesson_preload_reset_waiter = None

    def _accept_lesson_preload_reset_ack(self, msg_json: Dict[str, Any]) -> bool:
        waiter = self._lesson_preload_reset_waiter
        if not isinstance(waiter, dict) or msg_json.get("type") != "lesson_ack":
            return False
        if msg_json.get("sessionId") != waiter.get("sessionId"):
            return False
        body = msg_json.get("body") or {}
        if body.get("acks") != 1:
            return False
        future = waiter.get("future")
        if future is None or future.done():
            return False
        future.set_result(True)
        return True

    async def release_lesson_mode(self, *, reason: str = "lesson_terminal") -> None:
        if normalize_session_mode(self.session_mode) == SessionMode.LESSON:
            await self.enter_dormant_mode(reason=reason)

    async def finish_lesson_mode(self, *, reason: str = "lesson_completed") -> None:
        """Lesson terminal transition: show an appropriate face on the device, then
        return to normal CONVERSATION mode (re-opening Live) so the child can keep
        talking instead of seeing a silent dormant hop.

        The firmware's own lesson_stop handler already cleared the 3 lesson layers and
        set a NEUTRAL face; leave LESSON mode before sending the happy llm-emotion
        frame so the realtime face is visible again when the frame arrives. Gated by
        lesson.return_to_conversation (default True): set false to keep the
        cost-conscious dormant fallback while still showing the happy face. Best-effort
        — never raises into the lesson terminal path."""
        if normalize_session_mode(self.session_mode) != SessionMode.LESSON:
            return
        await self._stop_lesson_terminal_speaking_status()
        lesson_cfg = _lesson_config(self.config)
        if lesson_cfg.get("return_to_conversation", True):
            if lesson_cfg.get("smooth_finish_to_conversation", True):
                self._set_session_mode(SessionMode.CONVERSATION, reason=reason)
                provider = self.voice_provider
                if provider is not None and getattr(provider, "_client", None) is None:
                    start_session = getattr(provider, "start_session", None)
                    if callable(start_session):
                        await start_session()
                self.last_live_activity_at = time.monotonic()
            else:
                # Legacy path retained behind config for cost-conscious dormant
                # fallback and rollback of the direct completion transition.
                self._set_session_mode(SessionMode.DORMANT, reason=reason)
                await self.enter_conversation_mode(reason=reason)
        else:
            await self.enter_dormant_mode(reason=reason)
        await self._send_lesson_emotion(self._lesson_terminal_emotion(reason))

    async def _stop_lesson_terminal_speaking_status(self) -> None:
        if not getattr(self, "client_is_speaking", False):
            return
        ws = getattr(self, "websocket", None)
        if ws is not None:
            try:
                await ws.send(
                    json.dumps(
                        {
                            "type": "tts",
                            "state": "stop",
                            "session_id": self.session_id,
                        }
                    )
                )
            except Exception as e:  # pragma: no cover - best-effort terminal clear
                try:
                    self.logger.bind(tag=TAG).warning(f"send lesson tts stop failed: {e}")
                except Exception:
                    pass
        self.clearSpeakStatus()

    @staticmethod
    def _lesson_terminal_emotion(reason: str) -> str:
        if str(reason or "") == "lesson_completed":
            return "happy"
        return "sad"

    async def _send_lesson_emotion(self, emotion: str) -> None:
        """Push a single emotion face to the device over the realtime WS, reusing the
        proven get_emotion wire shape ({"type":"llm","text":<emoji>,"emotion":<name>}).
        Used to show the post-lesson happy face on the normal (non-lesson) display."""
        ws = getattr(self, "websocket", None)
        if ws is None:
            return
        emoji = textUtils.EMOTION_EMOJI.get(emotion, "🙂")
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "llm",
                        "text": emoji,
                        "emotion": emotion,
                        "session_id": self.session_id,
                    }
                )
            )
        except Exception as e:  # pragma: no cover - best-effort face push
            try:
                self.logger.bind(tag=TAG).warning(f"send lesson emotion failed: {e}")
            except Exception:
                pass

    async def _suspend_live_for_lesson(self) -> None:
        await self._persist_live_resumption_handle()
        provider = self.voice_provider
        close_live = getattr(provider, "_close_live_resources", None)
        if callable(close_live):
            await close_live()
            return
        close_provider = getattr(provider, "close", None)
        if callable(close_provider):
            await close_provider()

    async def _persist_live_resumption_handle(self) -> None:
        handle = getattr(self, "google_live_session_resumption_handle", None)
        if not handle:  # pragma: no cover - defensive empty resumption handle guard
            return
        store = getattr(self, "live_resumption_store", None)
        save = getattr(store, "save", None)
        if callable(save):
            await save(getattr(self, "device_id", None), handle)

    async def close_live_if_idle(self, *, now=None) -> bool:
        if normalize_session_mode(self.session_mode) != SessionMode.CONVERSATION:
            return False
        cfg = self.config.get("live_admission", {}) if isinstance(self.config, dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        timeout = cfg.get("idle_timeout_sec")
        if timeout is None:
            google_cfg = self.config.get("google_live", {}) if isinstance(self.config, dict) else {}
            if not isinstance(google_cfg, dict):
                google_cfg = {}
            timeout = google_cfg.get("idle_timeout_sec", 900)
        timeout = _float_or_none(timeout)
        if timeout is None:
            timeout = 900.0
        now = time.monotonic() if now is None else float(now)
        last_activity = self.last_live_activity_at
        if last_activity is None:
            last_activity = getattr(self, "google_live_session_started_at", None)
        if last_activity is None:
            return False
        last_activity = float(last_activity)
        if now - last_activity < timeout:
            return False
        await self._suspend_live_for_lesson()
        await self.enter_dormant_mode(reason="idle_timeout")
        return True

    def _is_hello_message(self, message):
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("type") == "hello"

    def _is_ping_message(self, message):
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("type") == "ping"

    def _is_mcp_message(self, message):
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("type") == "mcp"

    def _is_listen_control_message(self, message):
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("type") == "listen"

    def _is_lesson_control_message(self, message):
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("type") in {
            "lesson_ack",
            "lesson_progress",
            "lesson_error",
        }

    async def _wait_for_voice_provider_ready(self):
        """In manager mode, keep early user input on the selected voice provider path."""
        if not self.read_config_from_api:
            return
        provider_task = self.voice_provider_task
        if provider_task is None or provider_task.done():
            return
        current_task = asyncio.current_task()
        if provider_task is current_task:
            return
        try:
            if self._google_live_mode_configured() and self.voice_provider is not None:
                try:
                    # ponytail: base/env Google Live is the production fast path.
                    # If per-device config is slow, route wake audio now; make this
                    # configurable only if non-Live manager profiles need it.
                    await asyncio.wait_for(asyncio.shield(provider_task), timeout=0.5)
                except asyncio.TimeoutError:
                    self.logger.bind(tag=TAG).warning(
                        "voice_provider_ready_wait_timeout; continuing with existing provider"
                    )
                return
            await provider_task
        except asyncio.CancelledError:  # pragma: no cover - task cancellation propagation guard
            cancelling = getattr(current_task, "cancelling", None)
            if current_task is not None and callable(cancelling) and cancelling():
                raise

    async def _start_classic_pipeline_session(self):
        """Start legacy pipeline initialization without changing routing behavior."""
        await self._ensure_classic_pipeline_modules()
        asyncio.create_task(self._background_initialize())

    async def _ensure_classic_pipeline_modules(self):
        """Initialize classic LLM-side modules when Google Live falls back after skipping them."""
        selected = self.config.get("selected_module", {}) or {}
        init_llm = self.llm is None and "LLM" in selected
        init_memory = self.memory is None and "Memory" in selected
        init_intent = self.intent is None and "Intent" in selected
        if not (init_llm or init_memory or init_intent):
            return

        try:
            modules = await self.loop.run_in_executor(
                None,
                initialize_modules,
                self.logger,
                self.config,
                False,
                False,
                init_llm,
                False,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(
                f"Classic fallback component initialization failed: {e}"
            )
            return

        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]
            self._initialize_memory()
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
            self._initialize_intent()

    async def _initialize_voice_session_async(self):
        """Resolve per-connection config and bootstrap selected voice provider."""
        try:
            await self._initialize_private_config_async()
            resolved_provider = create_voice_session_provider(self)
            if (
                self.voice_provider is not None
                and type(resolved_provider) is type(self.voice_provider)
            ):
                return

            old_provider = self.voice_provider
            switching_to_google_live = (
                old_provider is not None
                and type(resolved_provider) is not type(old_provider)
                and self._google_live_mode_configured()
            )
            if switching_to_google_live:
                self.voice_provider = None
            await resolved_provider.start_session()
            if self.voice_provider is old_provider or self.voice_provider is None:
                self.voice_provider = resolved_provider
            if old_provider is not None and old_provider is not self.voice_provider:
                await old_provider.close()
        except Exception as e:
            self.logger.bind(tag=TAG).error(
                f"Voice session initialization failed: {e}"
            )
            self.bind_completed_event.set()

    async def _process_mqtt_audio_message(self, message):
        """
        Handle audio message from MQTT gateway, parse 16-byte header and extract audio data

        Args:
            message: audio message containing header

        Returns:
            bool: whether message was processed successfully
        """
        try:
            # Extract HeaderInfo
            timestamp = int.from_bytes(message[8:12], "big")
            audio_length = int.from_bytes(message[12:16], "big")

            # Extract audio data
            if audio_length > 0 and len(message) >= 16 + audio_length:
                # Has specified length, extract exact audio data
                audio_data = message[16 : 16 + audio_length]
                # Based onTimestampPerformSortProcess
                self._process_websocket_audio(audio_data, timestamp)
                return True
            elif len(message) > 16:
                # No specified length or invalid length. Remove header then process remaining data
                audio_data = message[16:]
                self.asr_audio_queue.put(audio_data)
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to parse WebSocket audio packet: {e}")

        # Processing failed, returnFalseMeans need continue processing
        return False

    def _process_websocket_audio(self, audio_data, timestamp):
        """Handle WebSocket-format audio packet"""
        # InitializeTimestampSequence Management
        if not hasattr(self, "audio_timestamp_buffer"):
            self.audio_timestamp_buffer = {}
            self.last_processed_timestamp = 0
            self.max_timestamp_buffer_size = 20

        # IfTimestampIs incremental, handle directly
        if timestamp >= self.last_processed_timestamp:
            self.asr_audio_queue.put(audio_data)
            self.last_processed_timestamp = timestamp

            # Process subsequent packets in buffer
            processed_any = True
            while processed_any:
                processed_any = False
                for ts in sorted(self.audio_timestamp_buffer.keys()):
                    if ts > self.last_processed_timestamp:
                        buffered_audio = self.audio_timestamp_buffer.pop(ts)
                        self.asr_audio_queue.put(buffered_audio)
                        self.last_processed_timestamp = ts
                        processed_any = True
                        break
        else:
            # Out-of-order packet, cache
            if len(self.audio_timestamp_buffer) < self.max_timestamp_buffer_size:
                self.audio_timestamp_buffer[timestamp] = audio_data
            else:
                self.asr_audio_queue.put(audio_data)

    async def handle_restart(self, message):
        """Handle server restart request"""
        try:

            self.logger.bind(tag=TAG).info("Received server restart instruction, preparing to execute...")

            # Send ConfirmationResponse
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "Server restarting...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # Execute restart operation asynchronously
            def restart_server():  # pragma: no cover - would spawn app.py and os._exit
                """Actual restart method"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("Restarting server...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # Use thread to execute restart, avoid blockingEventLoop
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Restart failed: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _initialize_components(self):
        try:
            if self.tts is None:
                try:
                    self.tts = self._initialize_tts()
                except Exception as e:
                    self.logger.bind(tag=TAG).error(
                        f"TTS initialization failed, using DefaultTTS fallback: {e}"
                    )
                    self.tts = DefaultTTS(self.config, delete_audio_file=True)
            # Open speech synthesis channel
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )
            if self.need_bind:
                self.bind_completed_event.set()
                return
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """Initialize component"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # Use FastPromptInitialize with words
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"Quick init component: prompt success {prompt[:50]}..."
                )

            """Initialize local components"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            # Init voiceprint recognition
            self._initialize_voiceprint()
            # Open speech recognition channel
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )

            """Load memory"""
            self._initialize_memory()
            """Load intent recognition"""
            self._initialize_intent()
            """Initialize reporting thread"""
            self._init_report_threads()
            """Update system prompt"""
            self._init_prompt_enhancement()
            """Inject tool-call few-shot examples (function_call mode only)"""  # pragma: no cover - statement marker only
            self._inject_tool_call_fewshot()  # pragma: no cover - covered via direct unit tests

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Failed to instantiate component: {e}")

    def _init_prompt_enhancement(self):

        # Update contextInfo
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"],
            self.device_id,
            self.client_ip,
            emoji_enabled=(self.features or {}).get("emoji", True),
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).debug("System prompt enhanced and updated")

    def _inject_tool_call_fewshot(self):
        """Inject tool call few-shot examples into conversation history.
        Structure: positive samples (tool call examples) placed before dynamic system, can hit prefix cache;
        negative samples (direct answer examples) placed after dynamic system, next to real user message,
        ensures model last sees "do not call tool" behavior pattern before processing user message.
        """
        if self.intent_type != "function_call":
            return
        if not hasattr(self, "func_handler") or self.func_handler is None:
            return

        tools = self.func_handler.get_functions()
        if not tools:
            return

        tool_names = {t.get("function", {}).get("name") for t in tools}

        # === few-shot Example (is_temporary)===
        # Display direct_answer Carry response Parameter usage, complete reply in one call

        # Example1:direct_answer(Reply contentWrite in response In parameters, no recursion needed)
        da_tc_id = "fewshot_da_001"
        self.dialogue.put(Message(role="user", content="Tell me story", is_temporary=True))
        self.dialogue.put(Message(
            role="assistant",
            tool_calls=[{
                "id": da_tc_id,
                "function": {"arguments": '{"response": "Sure, what type do you want to hear? Fairy tale, adventure, or funny? Pick one and I’ll start~"}', "name": "direct_answer"},
                "type": "function", "index": 0,
            }],
            is_temporary=True,
        ))
        self.dialogue.put(Message(
            role="tool", tool_call_id=da_tc_id,
            content="Replied directly", is_temporary=True,
        ))

        # Example2: real tool call (handle_exit_intent)
        if "handle_exit_intent" in tool_names:
            tc_id = "fewshot_exit_001"
            self.dialogue.put(Message(role="user", content="Bye", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {"arguments": '{"say_goodbye": "Goodbye, chat next time~"}', "name": "handle_exit_intent"},
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="Exit intent handled", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant", content="Goodbye, chat next time~", is_temporary=True,
            ))

        self.logger.bind(tag=TAG).debug("Injected tool-call few-shot examples")

    def _init_report_threads(self):
        """Initialize ASR and TTS report threads"""
        if not self.read_config_from_api or self.need_bind:
            return
        if self.chat_history_conf == 0:  # pragma: no cover - simple reporting disabled guard
            return
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("TTS reporting thread started")

    async def ensure_lesson_tts(self):
        """Initialize the local TTS output path for passive lesson prompts."""
        if self.tts is not None and getattr(self.tts, "tts_text_queue", None) is not None:
            return True
        if self._lesson_tts_lock is None:
            self._lesson_tts_lock = asyncio.Lock()
        async with self._lesson_tts_lock:
            if self.tts is not None and getattr(self.tts, "tts_text_queue", None) is not None:  # pragma: no cover - double-checked lock fast path
                return True
            try:
                loop = self.loop or asyncio.get_running_loop()
                self.tts = await loop.run_in_executor(None, self._initialize_tts)
                await self.tts.open_audio_channels(self)
                self.logger.bind(tag=TAG).info("lesson_tts_initialized")
                return getattr(self.tts, "tts_text_queue", None) is not None
            except Exception as exc:
                self.logger.bind(tag=TAG).warning(
                    f"lesson_tts_initialize_failed: {type(exc).__name__}"
                )
                return False

    def _initialize_tts(self):
        """Initialize TTS"""
        tts = None
        if not self.need_bind:
            tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """Initialize ASR"""
        if (
                self._asr is not None
                and hasattr(self._asr, "interface_type")
                and self._asr.interface_type == InterfaceType.LOCAL
        ):
            # If PublicASRIf local service, return directly
            # Because one local instanceASRcan be shared by multiple connections
            asr = self._asr
        else:
            # If PublicASRIs remote service, initialize new instance
            # Because RemoteASR, involvingwebsocketConnection and receiving thread, need one instance per connection
            asr = initialize_asr(self.config)

        return asr

    def _initialize_voiceprint(self):
        """Initialize voiceprint recognition for current connection"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                voiceprint_provider = VoiceprintProvider(voiceprint_config)
                if voiceprint_provider is not None and voiceprint_provider.enabled:
                    self.voiceprint_provider = voiceprint_provider
                    self.logger.bind(tag=TAG).info("Voiceprint recognition dynamically enabled on connection")
                else:
                    self.logger.bind(tag=TAG).warning("Voiceprint recognition enabled but config incomplete")
            else:
                self.logger.bind(tag=TAG).info("Voiceprint recognition not enabled")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"Voiceprint recognition initialization failed: {str(e)}")

    async def _background_initialize(self):
        """Initialize config and components in background (does not block main loop at all)"""
        try:
            # In thread poolInitialize component
            self.executor.submit(self._initialize_components)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Background initialization failed: {e}")

    async def _initialize_private_config_async(self):
        """Fetch differentiated config from API asynchronously (async version, does not block main loop)"""
        if not self.read_config_from_api:
            self.need_bind = False
            self.bind_completed_event.set()
            return
        try:
            begin_time = time.time()
            private_config = await get_private_config_from_api(
                self.config,
                self.headers.get("device-id"),
                self.headers.get("client-id", self.headers.get("device-id")),
            )
            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))
            self.logger.bind(tag=TAG).info(
                f"{time.time() - begin_time} seconds, async differentiated config fetched successfully: {json.dumps(_private_config_log_summary(filter_sensitive_info(private_config)), ensure_ascii=False)}"
            )
            self.need_bind = False
            if self.stop_event.is_set():  # pragma: no cover - race guard after private config fetch
                self.bind_completed_event.set()
                return
        except DeviceNotFoundException as e:
            if self.config.get("allow_device_config_fallback"):
                self.need_bind = False
                private_config = {}
                self.logger.bind(tag=TAG).warning(
                    "device config not found; falling back to base config"
                )
            else:
                self.need_bind = True
                private_config = {}
        except DeviceBindException as e:
            self.need_bind = True
            self.bind_code = e.bind_code
            private_config = {}
        except Exception as e:
            self.need_bind = True
            self.logger.bind(tag=TAG).error(f"Async differential config fetch failed: {e}")
            private_config = {}

        init_llm, init_tts, init_memory, init_intent = (
            False,
            False,
            False,
            False,
        )

        init_vad = check_vad_update(self.common_config, private_config)
        init_asr = check_asr_update(self.common_config, private_config)

        if init_vad:
            self.config["VAD"] = private_config["VAD"]
            self.config["selected_module"]["VAD"] = private_config["selected_module"][
                "VAD"
            ]
        if init_asr:
            self.config["ASR"] = private_config["ASR"]
            self.config["selected_module"]["ASR"] = private_config["selected_module"][
                "ASR"
            ]
        if private_config.get("TTS", None) is not None:
            init_tts = True
            self.config["TTS"] = private_config["TTS"]
            self.config["selected_module"]["TTS"] = private_config["selected_module"][
                "TTS"
            ]
        if private_config.get("LLM", None) is not None:
            init_llm = True
            self.config["LLM"] = private_config["LLM"]
            self.config["selected_module"]["LLM"] = private_config["selected_module"][
                "LLM"
            ]
        if private_config.get("VLLM", None) is not None:
            self.config["VLLM"] = private_config["VLLM"]
            self.config["selected_module"]["VLLM"] = private_config["selected_module"][
                "VLLM"
            ]
        if private_config.get("Memory", None) is not None:
            init_memory = True
            self.config["Memory"] = private_config["Memory"]
            self.config["selected_module"]["Memory"] = private_config[
                "selected_module"
            ]["Memory"]
        if private_config.get("Intent", None) is not None:
            init_intent = True
            # Merge instead of wholesale replace so config.yaml profiles
            # (e.g. Intent.function_call.functions used by Google Live)
            # are preserved alongside API-provided ones.
            self.config["Intent"] = merge_configs(
                self.config.get("Intent", {}) or {},
                private_config["Intent"],
            )
            selected_module = private_config.get("selected_module") or {}
            model_intent = (
                selected_module.get("Intent")
                if isinstance(selected_module, dict)
                else None
            )
            if not model_intent:
                model_intent = self.config.get("selected_module", {}).get("Intent")
            if model_intent:
                self.config["selected_module"]["Intent"] = model_intent
            # Load plugin config
            if model_intent and model_intent != "Intent_nointent":
                plugin_from_server = private_config.get("plugins", {})
                if not isinstance(plugin_from_server, dict):
                    plugin_from_server = {}
                for plugin, config_str in plugin_from_server.items():
                    plugin_from_server[plugin] = json.loads(config_str)
                self.config["plugins"] = plugin_from_server
                functions = list(plugin_from_server.keys())
                self.config["Intent"][self.config["selected_module"]["Intent"]][
                    "functions"
                ] = functions
        if private_config.get("prompt", None) is not None:
            self.config["prompt"] = private_config["prompt"]
        if private_config.get("child_profile", None) is not None:
            self.config["child_profile"] = private_config["child_profile"]
        # Get VoiceprintInfo
        if private_config.get("voiceprint", None) is not None:
            self.config["voiceprint"] = private_config["voiceprint"]
        if private_config.get("summaryMemory", None) is not None:
            self.config["summaryMemory"] = private_config["summaryMemory"]
        if private_config.get("device_max_output_size", None) is not None:
            self.max_output_size = int(private_config["device_max_output_size"])
        if private_config.get("chat_history_conf", None) is not None:
            self.chat_history_conf = int(private_config["chat_history_conf"])
        if private_config.get("mcp_endpoint", None) is not None:
            self.config["mcp_endpoint"] = private_config["mcp_endpoint"]
        if private_config.get("context_providers", None) is not None:
            self.config["context_providers"] = private_config["context_providers"]
        if private_config.get("voice_mode", None) is not None:
            self.config["voice_mode"] = private_config["voice_mode"]
        if private_config.get("google_live", None) is not None:
            # Merge so config.yaml google_live defaults (incl. functions
            # override) survive API config injection.
            self.config["google_live"] = merge_configs(
                self.config.get("google_live", {}) or {},
                private_config["google_live"],
            )
        if private_config.get("lesson", None) is not None:
            private_lesson = private_config["lesson"]
            if isinstance(private_lesson, dict):
                self.config["lesson"] = merge_configs(
                    self.config.get("lesson", {}) or {},
                    private_lesson,
                )
        self.config = normalize_voice_config(self.config)

        voice_mode_config = self.config.get("voice_mode", {})
        if (
                isinstance(voice_mode_config, dict)
                and voice_mode_config.get("type") == "google_live"
        ):
            self.bind_completed_event.set()
            return

        # InjectReplacement wordto TTS Module Config
        if private_config.get("correct_words", None) is not None:
            select_tts_module = self.config["selected_module"]["TTS"]
            self.config["TTS"][select_tts_module]["correct_words"] = private_config[
                "correct_words"
            ]

        if self.stop_event.is_set():  # pragma: no cover - race guard before component init
            self.bind_completed_event.set()
            return

        # Use run_in_executor Execute in thread pool initialize_modules, avoid blocking main loop
        try:
            modules = await self.loop.run_in_executor(
                None,  # Use default thread pool
                initialize_modules,
                self.logger,
                private_config,
                init_vad,
                init_asr,
                init_llm,
                init_tts,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Component initialization failed: {e}")
            modules = {}
        if self.stop_event.is_set():  # pragma: no cover - race guard after component init
            self.bind_completed_event.set()
            return
        if modules.get("tts", None) is not None:
            self.tts = modules["tts"]
        if modules.get("vad", None) is not None:
            self.vad = modules["vad"]
        if modules.get("asr", None) is not None:
            self.asr = modules["asr"]
        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]
        self.bind_completed_event.set()

    def _initialize_memory(self):
        if self.memory is None:
            return
        """Initialize memory module"""
        self.memory.init_memory(
            role_id=self.device_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=not self.read_config_from_api,
        )

        # Get memory summary config
        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]][
            "type"
        ]
        # If Use nomen or mem_report_only, directly return
        if memory_type == "nomem" or memory_type == "mem_report_only":
            return
        # Use mem_local_short Mode
        elif memory_type == "mem_local_short":
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]][
                "llm"
            ]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                # If dedicated configuredLLM, create independentLLMInstance
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"Created dedicated LLM for memory summary: {memory_llm_name}, type: {memory_llm_type}"
                )
                self.memory.set_llm(memory_llm)
            else:
                # Otherwise use mainLLM
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("Use main LLM as intent recognition model")

    def _initialize_intent(self):
        if self.intent is None:
            return
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        """Initialize intent recognition module"""
        # Get intent recognition config
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # If Use nointent, directly return
        if intent_type == "nointent":
            return
        # Use intent_llm Mode
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # If dedicated configuredLLM, create independentLLMInstance
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"Created dedicated LLM for intent recognition: {intent_llm_name}, type: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # Otherwise use mainLLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("Use main LLM as intent recognition model")

        """Load unified tool handler"""
        self.func_handler = UnifiedToolHandler(self)

        # Async initializationTool handler
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # Update Systempromptto Context
        self.dialogue.update_system_message(self.prompt)

    def chat(self, query, depth=0):
        # Savecurrent tasksentence_idTo local variable, avoid being overwritten by new task
        current_sentence_id = None

        if query is not None:
            self.logger.bind(tag=TAG).info(f"LLM received user message: {query}")

        if self._google_live_mode_configured():
            return None

        # Create new when topmostSession IDAnd sendFIRSTRequest
        if depth == 0:
            current_sentence_id = str(uuid.uuid4().hex)
            self.sentence_id = current_sentence_id  # Update shared attributes
            self.dialogue.put(Message(role="user", content=query))
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        else:
            # During recursive call, use currentsentence_id
            current_sentence_id = self.sentence_id

        # Set max recursion depth to avoid infinite loop. Can adjust based on actual needs
        MAX_DEPTH = 5
        force_final_answer = False  # Mark whether force final answer

        if depth >= MAX_DEPTH:
            self.logger.bind(tag=TAG).debug(
                f"Max tool call depth {MAX_DEPTH} reached, force answer based on existing info"
            )
            force_final_answer = True
            # Add system instruction, require LLM Based on ExistingInfoAnswer
            self.dialogue.put(
                Message(
                    role="user",
                    content="[System Prompt] Max tool call limit reached. Based on all info obtained so far, give final answer directly. Do not try to call any more tools.",
                )
            )

        # Define intent functions
        functions = None
        # When reaching max depth,DisableTool call, force LLM Answer Directly
        if (
                self.intent_type == "function_call"
                and hasattr(self, "func_handler")
                and not force_final_answer
        ):
            functions = list(self.func_handler.get_functions())
            # 仅在第一层调用时注入 direct_answer 虚拟工具
            # 递归调用（depth>0）不注入，避免模型在生成文本回复时再次调 direct_answer 导致循环
            if functions is not None and depth == 0:
                functions.append(DIRECT_ANSWER_TOOL)

        response_message = []

        try:
            # Use conversation with memory
            memory_str = None
            # Only whenqueryQuery memory when non-empty (means user asks)
            if self.memory is not None and query:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            if self.intent_type == "function_call" and functions is not None:
                # Use SupportfunctionsofstreamingInterface
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                    functions=functions,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {})
                    ),
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM processing error {query}: {e}")
            if depth == 0 and self.tts is not None:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
            return None

        # Handle StreamingResponse
        tool_call_flag = False
        # Support multiple parallel tool calls - Use list storage
        tool_calls_list = []  # Format: [{"id": "", "name": "", "arguments": ""}]
        content_arguments = ""
        emotion_flag = True
        try:
            for response in llm_responses:
                if self.client_abort:
                    break
                if self.intent_type == "function_call" and functions is not None:
                    content, tools_call = response
                    if "content" in response:  # pragma: no cover - SDK dict chunk compatibility path
                        content = response["content"]
                        tools_call = None
                    if content is not None and len(content) > 0:
                        content_arguments += content

                    if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                        # print("content_arguments", content_arguments)
                        tool_call_flag = True

                    if tools_call is not None and len(tools_call) > 0:
                        tool_call_flag = True
                        self._merge_tool_calls(tool_calls_list, tools_call)

                    # Streaming Extract direct_answer of response parameters, send in real time TTS
                    # Use safe buffer to prevent JSON Closing symbol leaks to TTS
                    _DA_STREAM_BUFFER = 5
                    for tc in tool_calls_list:
                        if tc["name"] == "direct_answer" and tc.get("arguments"):
                            da_text = self._extract_direct_answer_response(tc["arguments"])
                            sent_len = tc.get("_da_sent", 0)
                            if da_text and len(da_text) > sent_len:
                                safe_end = max(sent_len, len(da_text) - _DA_STREAM_BUFFER)
                                if safe_end > sent_len:
                                    new_part = da_text[sent_len:safe_end]
                                    # Clean delta may leak in JSON Close Garbage
                                    new_part = self._clean_response_garbage(new_part)
                                    if new_part:
                                        tc["_da_sent"] = safe_end
                                        self.tts.tts_text_queue.put(
                                            TTSMessageDTO(
                                                sentence_id=current_sentence_id,
                                                sentence_type=SentenceType.MIDDLE,
                                                content_type=ContentType.TEXT,
                                                content_detail=new_part,
                                            )
                                        )
                else:
                    content = response

                # inllmGet emotion emoji from reply, only get once at start of one dialog round
                if emotion_flag and content is not None and content.strip():
                    if (self.features or {}).get("emoji", True):
                        asyncio.run_coroutine_threadsafe(
                            textUtils.get_emotion(self, content),
                            self.loop,
                        )
                    emotion_flag = False

                if content is not None and len(content) > 0:
                    if not tool_call_flag:
                        response_message.append(content)
                        self.tts.tts_text_queue.put(
                            TTSMessageDTO(
                                sentence_id=current_sentence_id,
                                sentence_type=SentenceType.MIDDLE,
                                content_type=ContentType.TEXT,
                                content_detail=content,
                            )
                        )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=ContentType.TEXT,
                    content_detail=get_system_error_response(self.config),
                )
            )
            if depth == 0:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
            return
        # Processfunction call
        if tool_call_flag:
            bHasError = False
            # Handle text-based tool call format
            if len(tool_calls_list) == 0 and content_arguments:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        tool_calls_list.append(
                            {
                                "id": str(uuid.uuid4().hex),
                                "name": content_arguments_json["name"],
                                "arguments": json.dumps(
                                    content_arguments_json["arguments"],
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )

            if not bHasError and len(tool_calls_list) > 0:
                # Process direct_answer Virtual Tool
                direct_answer_calls = [tc for tc in tool_calls_list if tc["name"] == "direct_answer"]
                real_tool_calls = [tc for tc in tool_calls_list if tc["name"] != "direct_answer"]

                if direct_answer_calls:
                    self.logger.bind(tag=TAG).debug(
                        f"Model selected direct_answer, streaming already played, write to dialogue history"
                    )
                    for tc in direct_answer_calls:
                        da_response = self._extract_direct_answer_response(tc.get("arguments", "{}"))
                        if da_response:
                            # Flush unsent part in streaming buffer
                            sent_len = tc.get("_da_sent", 0)
                            remaining = da_response[sent_len:]
                            if remaining:
                                remaining = self._clean_response_garbage(remaining)
                                if remaining:
                                    self.tts.tts_text_queue.put(
                                        TTSMessageDTO(
                                            sentence_id=current_sentence_id,
                                            sentence_type=SentenceType.MIDDLE,
                                            content_type=ContentType.TEXT,
                                            content_detail=remaining,
                                        )
                                    )
                            # Write conversation history
                            da_response = self._clean_response_garbage(da_response)
                            self.tts.store_tts_text(current_sentence_id, da_response)
                            self.dialogue.put(Message(role="assistant", content=da_response))

                    if not real_tool_calls:
                        if depth == 0:
                            self.tts.tts_text_queue.put(
                                TTSMessageDTO(
                                    sentence_id=current_sentence_id,
                                    sentence_type=SentenceType.LAST,
                                    content_type=ContentType.ACTION,
                                )
                            )
                        return

                    tool_calls_list = real_tool_calls  # pragma: no cover - mixed virtual/real tool edge

            if not bHasError and len(tool_calls_list) > 0:
                self.logger.bind(tag=TAG).debug(
                    f"Detected {len(tool_calls_list)} tool calls"
                )

                # LLM text already broadcast in streaming stage
                streamed_text = ""
                if len(response_message) > 0:  # pragma: no cover - streamed pre-tool text edge
                    streamed_text = "".join(response_message)
                    self.tts.store_tts_text(current_sentence_id, streamed_text)
                    self.dialogue.put(Message(role="assistant", content=streamed_text))
                response_message.clear()

                # Collect all tool call Future
                futures_with_data = []
                for tool_call_data in tool_calls_list:
                    self.logger.bind(tag=TAG).debug(
                        f"function_name={tool_call_data['name']}, function_id={tool_call_data['id']}, function_arguments={tool_call_data['arguments']}"
                    )

                    # Use common method to report tool call
                    tool_input = json.loads(tool_call_data.get("arguments") or "{}")
                    enqueue_tool_report(self, tool_call_data['name'], tool_input)

                    future = asyncio.run_coroutine_threadsafe(
                        self.func_handler.handle_llm_function_call(
                            self, tool_call_data
                        ),
                        self.loop,
                    )
                    futures_with_data.append((future, tool_call_data, tool_input))

                # Tool call timeout, configurable, default30seconds
                tool_call_timeout = int(self.config.get("tool_call_timeout", 30))
                # Wait for coroutine end (actual wait time is slowest one)
                tool_results = []

                for future, tool_call_data, tool_input in futures_with_data:
                    try:
                        result = future.result(timeout=tool_call_timeout)
                        tool_results.append((result, tool_call_data))
                        # Use public method to report tool call result
                        enqueue_tool_report(self, tool_call_data['name'], tool_input, str(result.result) if result.result else None, report_tool_call=False)

                    except Exception as e:
                        self.logger.bind(tag=TAG).error(
                            f"Tool call timeout orException: {tool_call_data['name']}, Error: {e}"
                        )
                        # Return on timeoutErrorResponseAvoid whole flow stuck
                        tool_results.append((
                            ActionResponse(action=Action.ERROR, result="Oops, network has problem, try again later!"),
                            tool_call_data
                        ))
                        # Report tool callError
                        enqueue_tool_report(self, tool_call_data['name'], tool_input, str(e), report_tool_call=False)

                # Unified handling of tool call results
                if tool_results:
                    self._handle_function_result(tool_results, depth=depth, streamed_text=streamed_text)

        # Store DialogueContent
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts.store_tts_text(current_sentence_id, text_buff)
            self.dialogue.put(Message(role="assistant", content=text_buff))

        if depth == 0:
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            # UselambdaLazy compute, only whenDEBUGExecute only at levelget_llm_dialogue()
            self.logger.bind(tag=TAG).debug(
                lambda: json.dumps(
                    self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
                )
            )

        return True

    def _handle_function_result(self, tool_results, depth, streamed_text=""):
        need_llm_tools = []
        record_tools = []

        for result, tool_call_data in tool_results:
            if result.action in [
                Action.RESPONSE,
                Action.NOTFOUND,
                Action.ERROR,
            ]:
                text = result.response if result.response else result.result
                if streamed_text and text in streamed_text:
                    self.logger.bind(tag=TAG).debug(
                        f"Skipping duplicate TTS for tool {tool_call_data['name']}, already streamed"
                    )
                else:
                    self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
                    self.tts.store_tts_text(self.sentence_id, text)
                self.dialogue.put(Message(role="assistant", content=text))
            elif result.action == Action.REQLLM:
                need_llm_tools.append((result, tool_call_data))
            elif result.action == Action.RECORD:
                record_tools.append((result, tool_call_data))
            else:
                pass

        # Action.RECORD: write full tool call chain (assistant(tool_calls) → tool(result) → assistant(response))
        # Model learned tool call pattern from history, no extra callLLM
        if record_tools:
            # Construct assistant Message(including tool_calls), record"Which tools model called"
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(record_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            # Write execution result of each tool, record"What tool returned"
            for result, tool_call_data in record_tools:
                text = result.result or ""
                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4())
                            if tool_call_data["id"] is None
                            else tool_call_data["id"]
                        ),
                        content=text,
                    )
                )

            # Use fixed text as final reply, complete standard three-part format, ensure nextMessageis user Rather than receive tool
            response_parts = []
            for result, _ in record_tools:
                resp = result.response or result.result
                if resp:
                    response_parts.append(resp)
            if response_parts:
                self.dialogue.put(Message(role="assistant", content=",".join(response_parts)))

        if need_llm_tools:
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(need_llm_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            for result, tool_call_data in need_llm_tools:
                text = result.result
                if text is not None and len(text) > 0:
                    self.dialogue.put(
                        Message(
                            role="tool",
                            tool_call_id=(
                                str(uuid.uuid4())
                                if tool_call_data["id"] is None
                                else tool_call_data["id"]
                            ),
                            content=text,
                        )
                    )

            self.chat(None, depth=depth + 1)

    def _report_worker(self):
        """Chat history reporting worker thread"""
        while not self.stop_event.is_set():
            try:
                # Get data from queue, set timeout to periodically check stopEvent
                item = self.report_queue.get(timeout=1)
                if item is None:  # Detect poison pill object
                    break
                try:
                    # Check thread poolStatus
                    if self.executor is None:
                        continue
                    # Submit task to thread pool
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"Chat history report threadException: {e}")
            except queue.Empty:  # pragma: no cover - one-second polling idle path
                continue
            except Exception as e:  # pragma: no cover - report worker safety net
                self.logger.bind(tag=TAG).error(f"Chat history reporting worker threadException: {e}")

        self.logger.bind(tag=TAG).info("Chat record reporting thread exited")

    def _process_report(self, type, text, audio_data, report_time):
        """Handle reporting task"""
        try:
            # Execute async report (inEventrun in loop)
            asyncio.run(report(self, type, text, audio_data, report_time))
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Report ProcessingException: {e}")
        finally:
            # Mark task complete
            self.report_queue.task_done()

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"Clear server speakingStatus")

    # ── US-006 lesson runtime hooks (additive; never on the voice hot path) ──────

    def _lesson_runtime_enabled(self) -> bool:
        """Lesson-runtime admission gate. The config loader may auto-enable this when
        production lesson prerequisites are present; an explicit false keeps the
        lesson layer dark."""
        from core.providers.tools.product_toolset import lesson_runtime_config_enabled

        return lesson_runtime_config_enabled(self)

    def _sample_lesson_enabled(self) -> bool:
        """DEMO gate: when lesson.sample_lesson (env LESSON_SAMPLE_ENABLED) is on, the
        spoken start_lesson trigger loads the built-in sample lesson IGNORING any backend
        assignment. Default OFF in robot config; never consulted at connect-time
        (only the explicit start_lesson tool path) so production behavior is unchanged."""
        from core.providers.tools.product_toolset import sample_lesson_config_enabled

        return sample_lesson_config_enabled(self)

    @staticmethod
    def _consume_cancelled_task(task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _disable_lesson_runtime(self) -> None:
        """S13 auto-disable target (plan §11.2 / CP-8): stop lesson work.

        The voice-latency-during-preload alarm uses this as a rollback lever. It
        flips the in-process admission flag off, cancels any active assignment
        pull, and detaches/closes the current runtime so no further lesson frames
        are emitted from this connection after the breaker trips.
        """
        if not isinstance(self.config, dict):
            return
        lesson_cfg = self.config.get("lesson")
        if not isinstance(lesson_cfg, dict):
            lesson_cfg = {}
            self.config["lesson"] = lesson_cfg
        lesson_cfg["runtime_enabled"] = False
        if self.lesson_pull_task and not self.lesson_pull_task.done():
            self.lesson_pull_task.cancel()
            self.lesson_pull_task.add_done_callback(self._consume_cancelled_task)
        runtime = self.lesson_runtime
        self.lesson_runtime = None
        if runtime is not None and hasattr(runtime, "close"):
            try:
                close_result = runtime.close()
                if asyncio.iscoroutine(close_result):
                    try:
                        asyncio.get_running_loop().create_task(close_result)
                    except RuntimeError:
                        close_result.close()
            except Exception as cleanup_error:
                try:
                    self.logger.bind(tag=TAG).error(
                        f"Error auto-disabling lesson runtime: {cleanup_error}"
                    )
                except Exception:
                    pass
        try:
            self.logger.bind(tag=TAG).error(
                "LESSON_RUNTIME_ENABLED auto-disabled (voice-latency-during-preload alarm)"
            )
        except Exception:
            pass

    def note_voice_round_trip(self, latency_ms: float) -> None:
        """Feed one completed voice round-trip to the S13 alarm (plan §11.2 / CP-8).

        THE single voice-side hook for the auto-disable guard. It is exception-safe
        and a no-op when no lesson alarm exists, so the voice hot path can call it
        unconditionally without risk: if the lesson layer is dark (or this turn was
        not concurrent with a preload) the sample is simply not counted toward the
        rollback p95. When the measured during-preload p95 regresses past the
        threshold the alarm flips ``LESSON_RUNTIME_ENABLED`` off via
        :meth:`_disable_lesson_runtime`."""
        alarm = self.lesson_voice_alarm
        if alarm is None:  # pragma: no cover - no alarm fast path
            return
        try:
            alarm.record_round_trip(latency_ms)
        except Exception:  # pragma: no cover - alarm is observability-only, never fatal
            pass

    def record_voice_metric(self, name: str, value: float, labels=None) -> None:
        """Best-effort bounded voice metric sink until MP-14's exporter lands."""
        try:
            sample = {
                "name": str(name),
                "value": float(value),
                "labels": dict(labels or {}),
                "timestamp": time.time(),
            }
        except Exception:
            return
        self.voice_metric_samples.append(sample)
        try:
            self.logger.bind(tag=TAG).info(
                "voice_metric name={} value_ms={:.1f} labels={}",
                sample["name"],
                sample["value"],
                sample["labels"],
            )
        except Exception:
            pass

    def _realtime_interaction_state(self):
        """Provider-agnostic read of the realtime interaction state.

        Reads the controller via ``getattr`` (NEVER hard-codes the private attr) so
        the classic pipeline — which has no controller — simply returns None. Also
        checks an active Google-Live fallback provider.
        """
        provider = self.voice_provider
        for candidate in (provider, getattr(provider, "_fallback_provider", None)):
            controller = getattr(candidate, "_interaction", None)
            if controller is not None:
                state = getattr(controller, "state", None)
                if state is not None:
                    return getattr(state, "value", state)
        return None

    def is_realtime_busy(self) -> bool:
        """Realtime guard source (plan §6.2.6): the voice path always wins, so lesson
        asset downloads PAUSE during any voice turn.

        Passive LISTENING is download-safe; the transient voice flags below still
        pause immediately when audio starts. Active interaction states such as
        WAITING_MODEL / MUSIC_PLAYING / INTERRUPTING / RECONNECTING / FALLBACK /
        MUTED pause downloads. Plus two provider-independent fallback signals that cover the
        classic pipeline (which has no interaction controller):
        - ``client_is_speaking`` — the robot is mid-TTS playback (output turn).
        - ``client_have_voice``  — VAD currently hears the child (input turn).

        NOTE (plan-vs-code, surfaced): plan §6.2.6 lists ``client_listen_mode`` as the
        third condition, but in live code that field is a PERSISTENT mode
        ("auto"/"manual", connection.py:135), not an open/closed window — pausing on
        it would stall downloads for the whole session. The real transient
        "child speaking now" signal is ``client_have_voice`` (set by VAD,
        providers/vad/silero.py), used here instead.
        """
        if getattr(self, "client_is_speaking", False):
            return True
        if getattr(self, "client_have_voice", False):
            return True
        if getattr(self, "_lesson_asset_audio_inflight", 0) > 0:
            return True
        state = self._realtime_interaction_state()
        if state is not None and state not in ("IDLE", "LISTENING"):
            return True
        return False

    async def _lesson_pull_on_connect(self):
        """Side task: fetch + drive the device's current lesson assignment.

        Wrapped so a lesson failure can never crash the connection or touch voice.
        """
        try:
            from core.lesson.runtime import maybe_start_lesson_on_connect

            return await maybe_start_lesson_on_connect(self)
        except Exception as exc:  # pragma: no cover - lesson runtime must never break voice connect
            self.logger.bind(tag=TAG).warning(
                f"lesson pull-on-connect failed: {type(exc).__name__}: {exc}"
            )
            return None

    def schedule_cached_lesson_sd_sync(self):
        if self.sd_pack_sync_task and not self.sd_pack_sync_task.done():
            return
        self.sd_pack_sync_task = asyncio.create_task(self._sync_cached_lesson_assets_to_sd())

    def schedule_mcp_background_task(self, coro):
        """Attach MCP work to this connection so teardown can drain it."""
        if self.mcp_tasks_closed:
            if hasattr(coro, "close"):
                coro.close()
            return None

        task = asyncio.create_task(coro)
        self.mcp_background_tasks.add(task)
        task.add_done_callback(self._mcp_background_task_done)
        return task

    def _ensure_activity_leases(self):
        if self.activity_leases is not None:
            return self.activity_leases
        if self._activity_leases_closed:
            return None
        self.activity_leases = ActivityLeaseCoordinator(asyncio.get_running_loop())
        return self.activity_leases

    def _close_activity_leases(self):
        if self._activity_leases_closed:
            return
        self._activity_leases_closed = True
        if self.activity_leases is not None:
            self.activity_leases.close()

    def _mcp_background_task_done(self, task):
        self.mcp_background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            self.logger.bind(tag=TAG).warning(
                f"MCP background task failed errorType={type(exc).__name__}"
            )

    async def _close_mcp_background_tasks(self):
        self.mcp_tasks_closed = True
        tasks = tuple(self.mcp_background_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.mcp_background_tasks.clear()

    async def _close_connection_owned_mcp_callers(self):
        """Stop every connection-owned path that can use device/server MCP."""
        await self._close_mcp_background_tasks()

        tasks = []
        for attr in ("lesson_pull_task", "sd_pack_sync_task"):
            task = getattr(self, attr, None)
            setattr(self, attr, None)
            if task is not None:
                if not task.done():
                    task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        runtimes = []
        runtime_ids = set()
        for attr in ("lesson_runtime", "lesson_runtime_candidate"):
            runtime = getattr(self, attr, None)
            setattr(self, attr, None)
            if runtime is not None and id(runtime) not in runtime_ids:
                runtime_ids.add(id(runtime))
                runtimes.append(runtime)
        for runtime in runtimes:
            try:
                await runtime.close()
            except Exception as exc:
                self.logger.bind(tag=TAG).error(
                    f"Error cleaning lesson runtime: {type(exc).__name__}"
                )

        forwarder = self.safety_event_forwarder
        self.safety_event_forwarder = None
        if forwarder is not None:
            try:
                await forwarder.aclose()
            except Exception as exc:
                self.logger.bind(tag=TAG).error(
                    f"Error cleaning safety event forwarder: {type(exc).__name__}"
                )

    async def _sync_cached_lesson_assets_to_sd(self):
        try:
            from core.lesson.sd_pack_fanout import drain_pending_for_connection
            from core.lesson.sd_pack_sync import sync_cached_lesson_assets_to_sd

            # Drain admin fan-out markers first (offline queue), then full cache sync.
            pending_result = await drain_pending_for_connection(self)
            full_result = await sync_cached_lesson_assets_to_sd(self)
            return {"pending": pending_result, "full": full_result}
        except Exception as exc:  # pragma: no cover - background sync must not break voice
            self.logger.bind(tag=TAG).warning(
                f"cached lesson SD sync failed: {type(exc).__name__}: {exc}"
            )
            return None

    async def close(self, ws=None):
        """Resource cleanup method"""
        try:
            if self.stop_event:
                self.stop_event.set()

            # Stop every path that can use MCP before cleaning its tool clients.
            try:
                await self._close_connection_owned_mcp_callers()
            finally:
                self._close_activity_leases()

            # Clean VAD Connection Resource
            if (
                    hasattr(self, "vad")
                    and self.vad
                    and hasattr(self.vad, "release_conn_resources")
            ):
                self.vad.release_conn_resources(self)

            # Clean audio buffer
            if hasattr(self, "audio_buffer"):
                self.audio_buffer.clear()

            # Cancel timeout task
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # Clean tool handler resources
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"Error cleaning tool handler: {cleanup_error}"
                    )

            if self.voice_provider is not None:
                try:
                    await self.voice_provider.close()
                except Exception as provider_cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"Error cleaning voice provider: {provider_cleanup_error}"
                    )

            if self.voice_provider_task and not self.voice_provider_task.done():
                self.voice_provider_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.voice_provider_task

            # Clear task queue
            self.clear_queues()
            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                await self.audio_rate_controller.stop_sending_and_wait()

            # Close WebSocket connection
            try:
                if ws:
                    # Safely checkWebSocketStatusAnd close
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:  # pragma: no cover - websocket implementation fallback
                            # If noneclosedAttribute, try close directly
                            await ws.close()
                    except Exception:  # pragma: no cover - websocket close best-effort failure
                        # If close fails, ignoreError
                        pass
                elif self.websocket:
                    try:
                        if (
                                hasattr(self.websocket, "closed")
                                and not self.websocket.closed
                        ):
                            await self.websocket.close()  # pragma: no cover - self websocket closed-attr branch
                        elif (
                                hasattr(self.websocket, "state")
                                and self.websocket.state.name != "CLOSED"
                        ):
                            await self.websocket.close()
                        else:
                            # If noneclosedAttribute, try close directly
                            await self.websocket.close()
                    except Exception:  # pragma: no cover - websocket close best-effort failure
                        # If close fails, ignoreError
                        pass
            except Exception as ws_error:  # pragma: no cover - websocket close safety net
                self.logger.bind(tag=TAG).error(f"Close WebSocket connectionError when: {ws_error}")

            if self.tts:
                await self.tts.close()
            if self.asr:
                await self.asr.close()

            # Finally close thread pool (avoid blocking)
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:  # pragma: no cover - executor shutdown safety net
                    self.logger.bind(tag=TAG).error(
                        f"Error closing thread pool: {executor_error}"
                    )
                self.executor = None
            self.logger.bind(tag=TAG).info("Connection resources released")
        except Exception as e:  # pragma: no cover - close outer safety net
            self.logger.bind(tag=TAG).error(f"Error closing connection: {e}")
        finally:
            # Ensure StopEventSet
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """Clear all task queues"""
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"Start Cleanup: TTSQueue Size={self.tts.tts_text_queue.qsize()}, Audio queue size={self.tts.tts_audio_queue.qsize()}"
            )

            # Clear queue non-blocking
            for q in [
                self.tts.tts_text_queue,
                self.tts.tts_audio_queue,
                self.report_queue,
            ]:
                if not q:  # pragma: no cover - defensive queue slot guard
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            # Reset audio flow controller (cancel background tasks and clear queue)
            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                self.audio_rate_controller.reset()
                self.logger.bind(tag=TAG).debug("Audio flow controller reset")

            self.logger.bind(tag=TAG).debug(
                f"Cleanup End: TTSQueue Size={self.tts.tts_text_queue.qsize()}, Audio queue size={self.tts.tts_audio_queue.qsize()}"
            )

    def reset_audio_states(self):
        """
        Reset all audio-related states (VAD + ASR)
        """
        # Reset VAD states
        self.client_audio_buffer.clear()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.client_voice_window.clear()
        self.last_is_voice = False
        self.vad_last_voice_time = 0.0

        # Clear ASR buffers
        self.asr_audio.clear()

        self.logger.bind(tag=TAG).debug("All audio states reset.")

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def _check_timeout(self):
        """Check connection timeout"""
        try:
            while not self.stop_event.is_set():
                last_activity_time = self.last_activity_time
                if self.need_bind:
                    last_activity_time = self.first_activity_time

                # Check whether timed out (only whenTimestampAlready initialized case)
                if last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if current_time - last_activity_time > self.timeout_seconds * 1000:
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("Connection timeout, prepare close")
                            # Set StopEvent, prevent duplicate processing
                            self.stop_event.set()
                            # Use try-except Wrap close operation, ensure not becauseExceptionBut blocked
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"Error closing timeout connection: {close_error}"
                                )
                        break
                # each10Check once per second, avoid too frequent
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Timeout check task error: {e}")
        finally:
            self.logger.bind(tag=TAG).info("Timeout check task exited")

    @staticmethod
    def _extract_direct_answer_response(arguments_str):
        """Extract response value from direct_answer parameters.
        Prefer json.loads standard parsing, fallback to string extraction during streaming stage.
        """
        if not arguments_str:
            return ""
        # First try standard JSON parsing (for complete, correctly formatted JSON)
        try:
            data = json.loads(arguments_str)
            if isinstance(data, dict) and "response" in data:
                return data["response"]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: streaming phase JSON May be incomplete, use string extraction
        marker = '"response": "'
        idx = arguments_str.find(marker)
        if idx < 0:
            marker = '"response":"'
            idx = arguments_str.find(marker)
        if idx < 0:
            return ""
        start = idx + len(marker)
        raw = arguments_str[start:]
        # Remove trailing JSON closing symbol (if complete)
        if raw.endswith('"}'):
            raw = raw[:-2]
        elif raw.endswith('"'):
            raw = raw[:-1]
        # Process JSON Escape
        raw = raw.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        return raw

    @staticmethod
    def _clean_response_garbage(text):
        """Clean JSON closing symbols that may leak in response.
        Model sometimes generates JSON closing characters in response content (such as )"}} or '}),
        these are not part of story content and need removal.
        """
        if not text:
            return text
        # Clean standalone JSON closing junk (like )"}}  '}}  "}}  }}  } )
        _garbage_chars = frozenset('")\'})')
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) <= 8 and all(c in _garbage_chars for c in stripped):
                continue
            cleaned.append(line)
        result = '\n'.join(cleaned)
        # Clean trailing residual JSON Close Symbol
        result = re.sub(r'["\'}\]]+$', '', result.rstrip()).rstrip()
        return result

    def _merge_tool_calls(self, tool_calls_list, tools_call):
        """Merge tool call list

        Args:
            tool_calls_list: collected tool call list
            tools_call: new tool call
        """
        for tool_call in tools_call:
            tool_index = getattr(tool_call, "index", None)
            if tool_index is None:
                if tool_call.function.name:
                    # have function_nameindicates new tool call
                    tool_index = len(tool_calls_list)
                else:
                    tool_index = len(tool_calls_list) - 1 if tool_calls_list else 0

            # Ensure list has enough positions
            if tool_index >= len(tool_calls_list):
                tool_calls_list.append({"id": "", "name": "", "arguments": ""})

            # Update tool callInfo
            if tool_call.id:
                tool_calls_list[tool_index]["id"] = tool_call.id
            if tool_call.function.name:
                tool_calls_list[tool_index]["name"] = tool_call.function.name
            if tool_call.function.arguments:
                tool_calls_list[tool_index]["arguments"] += tool_call.function.arguments
