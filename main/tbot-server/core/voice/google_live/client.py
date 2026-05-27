import os
import time
import asyncio
from contextlib import suppress
from collections.abc import Iterable, Mapping

# Eager import of google.genai at module load (server startup).
# First-time import of this SDK costs 80-100 seconds (protobuf + grpc + auth
# transitive imports). Doing it at startup hides the cost from device-side
# WebSocket idle timeout — without this, the first device connection blocks
# inside _import_genai_module() and disconnects before Live session is ready.
# Failure mode (no SDK installed) is preserved: _import_genai_module() still
# raises RuntimeError at call time, just from a cached None instead of import.
try:
    from google import genai as _GENAI_MODULE_EAGER
except ImportError:
    _GENAI_MODULE_EAGER = None


class GoogleLiveClient:
    """Google Live transport with eager SDK import and normalized events."""

    def __init__(self, config, logger):
        self.config = dict(config or {})
        self.logger = logger
        self.connected = False
        self._sdk_client = None
        self._live_context = None
        self._session = None
        self._types = None
        self._audio_started = False
        self._audio_chunk_count = 0
        self._audio_byte_count = 0
        # Debug: confirm disable_server_side_interruptions actually lands in client config
        try:
            self.logger.bind(tag="GoogleLive").info(
                "Google Live client config disable_server_side_interruptions={} barge_in={}",
                self.config.get("disable_server_side_interruptions"),
                self.config.get("barge_in"),
            )
        except Exception:
            pass

    async def connect(self):
        genai_module = self._import_genai_module()
        self._types = getattr(genai_module, "types", None)
        api_key = self._resolve_api_key()
        if not api_key:
            raise RuntimeError("Google Live API key is missing")

        model = self.config.get("model")
        if not model:
            raise RuntimeError("Google Live model is missing")

        self._sdk_client = genai_module.Client(api_key=api_key)
        self._live_context = self._sdk_client.aio.live.connect(
            model=model,
            config=self._build_connect_config(),
        )
        connect_started_at = time.monotonic()
        try:
            self._session = await asyncio.wait_for(
                self._live_context.__aenter__(),
                timeout=self._get_connect_timeout(),
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Google Live connect timed out") from exc
        self.connected = True
        self.logger.bind(tag="GoogleLive").info(
            "Google Live session connected in {:.1f} ms",
            (time.monotonic() - connect_started_at) * 1000,
        )

    async def send_audio(self, audio_bytes):
        if not self.connected or self._session is None:
            raise RuntimeError("Google Live client not connected")
        if not self._audio_input_enabled():
            return None
        if not audio_bytes:
            return None
        if len(audio_bytes) % 2:
            self.logger.bind(tag="GoogleLive").warning(
                "Google Live skipped corrupt pcm16 input bytes={}",
                len(audio_bytes),
            )
            return None

        mime_type = (
            f"audio/pcm;rate={int(self.config.get('input_sample_rate', 16000))}"
        )
        blob = self._build_blob(audio_bytes, mime_type)
        await self._session.send_realtime_input(audio=blob)
        return None

    async def end_audio_stream(self):
        if not self.connected or self._session is None:
            raise RuntimeError("Google Live client not connected")
        if not self._audio_input_enabled():
            return None
        await self._session.send_realtime_input(audio_stream_end=True)
        return None

    async def send_text(self, text):
        if not self.connected or self._session is None:
            raise RuntimeError("Google Live client not connected")
        if not text:
            return None
        if hasattr(self._session, "send_client_content"):
            await self._session.send_client_content(
                turns=self._build_text_turn(text),
                turn_complete=True,
            )
            return None
        if hasattr(self._session, "send_realtime_input"):
            await self._session.send_realtime_input(text=text)
            return None
        raise RuntimeError("Google Live session does not support text input")

    async def send_tool_response(self, responses):
        if not self.connected or self._session is None:
            raise RuntimeError("Google Live client not connected")
        if not responses:
            return None
        if not hasattr(self._session, "send_tool_response"):
            raise RuntimeError("Google Live session does not support tool responses")
        function_responses = [self._build_function_response(r) for r in responses]
        await self._session.send_tool_response(function_responses=function_responses)
        return None

    async def receive_events(self):
        if not self.connected or self._session is None:
            return
        self.logger.bind(tag="GoogleLive").info("Google Live receive loop started")
        pending_message_task = None
        try:
            while self.connected and self._session is not None:
                received_turn_message = False
                message_iterator = self._session.receive().__aiter__()
                while self.connected:
                    if pending_message_task is None:
                        pending_message_task = asyncio.create_task(
                            message_iterator.__anext__()
                        )
                    message, pending_message_task = await self._next_message(
                        pending_message_task,
                    )
                    if message is None:
                        continue
                    if message is False:
                        for event in self._finish_open_audio_turn("stream_end"):
                            yield event
                        break
                    received_turn_message = True
                    for event in self._normalize_message(message):
                        yield event
                if not received_turn_message:
                    break
        finally:
            if pending_message_task is not None:
                if not pending_message_task.done():
                    pending_message_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pending_message_task
            self.logger.bind(tag="GoogleLive").info("Google Live receive loop stopped")

    async def _next_message(self, pending_message_task):
        recv_timeout = self._get_receive_timeout()
        try:
            if recv_timeout is None:
                result = await pending_message_task
                self._log_recv_timer_reset(result)
                return result, None
            done, _pending = await asyncio.wait(
                {pending_message_task},
                timeout=recv_timeout,
            )
            if not done:
                self.logger.bind(tag="GoogleLive").warning("Google Live receive timed out")
                return None, pending_message_task
            result = pending_message_task.result()
            self._log_recv_timer_reset(result)
            return result, None
        except StopAsyncIteration:
            return False, None

    def _log_recv_timer_reset(self, message):
        # Each yielded server message implicitly resets the recv_timeout window
        # because _next_message awaits a fresh asyncio.wait per call. Emitting
        # this log only when the message carries an audio chunk gives us the
        # explicit observability event referenced in plan v2.1 Section 10
        # (Google Live recv_timer_reset on chunk) without spamming on
        # transcript-only or control messages.
        if message in (None, False):
            return
        if not self._message_has_audio_chunk(message):
            return
        try:
            self.logger.bind(tag="GoogleLive").debug(
                "Google Live recv_timer_reset on chunk"
            )
        except Exception:
            pass

    def _message_has_audio_chunk(self, message):
        server_content = self._extract_field(message, "server_content")
        if server_content is None:
            return False
        model_turn = self._extract_field(server_content, "model_turn")
        parts = self._extract_field(model_turn, "parts") or []
        for part in parts:
            inline_data = self._extract_field(part, "inline_data")
            if inline_data is None:
                continue
            if self._extract_field(inline_data, "data"):
                return True
        return False

    async def interrupt(self):
        if self._session is None:
            return None
        if hasattr(self._session, "send_client_content"):
            await self._session.send_client_content(turns=[], turn_complete=False)
        return None

    async def close(self):
        self.connected = False
        self._audio_started = False
        self._audio_chunk_count = 0
        self._audio_byte_count = 0
        if self._live_context is not None:
            await self._live_context.__aexit__(None, None, None)
        self._sdk_client = None
        self._live_context = None
        self._session = None

    def _build_connect_config(self):
        response_modalities = []
        audio_output_enabled = bool(self.config.get("enable_audio_output", True))
        transcript_events_enabled = bool(
            self.config.get("send_transcript_events", True)
        )
        llm_state_events_enabled = bool(
            self.config.get("send_llm_state_events", False)
        )
        if audio_output_enabled:
            response_modalities.append("AUDIO")
        elif transcript_events_enabled or llm_state_events_enabled:
            response_modalities.append("TEXT")

        connect_config = {"response_modalities": response_modalities or ["AUDIO"]}

        if transcript_events_enabled:
            connect_config["input_audio_transcription"] = {}
        if transcript_events_enabled or llm_state_events_enabled:
            connect_config["output_audio_transcription"] = {}

        speech_config = self._build_speech_config()
        if speech_config:
            connect_config["speech_config"] = speech_config
        realtime_input_config = self._build_realtime_input_config()
        if realtime_input_config:
            connect_config["realtime_input_config"] = realtime_input_config
        tools = self._build_tools()
        if tools:
            connect_config["tools"] = tools
            try:
                tool_names = self._summarize_tool_names(tools)
                self.logger.bind(tag="GoogleLive").info(
                    "Google Live connect config: tools={} names=[{}]",
                    len(tool_names),
                    ",".join(tool_names),
                )
            except Exception:
                pass
        else:
            self.logger.bind(tag="GoogleLive").info(
                "Google Live connect config: no tools attached"
            )
        system_instruction = self._build_system_instruction()
        if system_instruction is not None:
            connect_config["system_instruction"] = system_instruction
            self.logger.bind(tag="GoogleLive").info(
                "Google Live connect config: system_instruction attached chars={}",
                self._system_instruction_length(system_instruction),
            )
        return connect_config

    def _build_system_instruction(self):
        prompt = self.config.get("system_prompt") or self.config.get("prompt")
        if not prompt:
            return None
        text = str(prompt).strip()
        if not text:
            return None
        # Live API accepts either a Content object or a plain dict. We use the
        # dict form so we do not depend on SDK version differences in how the
        # Content type is exposed.
        if self._types is not None and hasattr(self._types, "Content"):
            try:
                return self._types.Content(
                    role="user",
                    parts=[self._types.Part(text=text)],
                )
            except Exception:
                pass
        return {"parts": [{"text": text}], "role": "user"}

    def _system_instruction_length(self, instruction):
        try:
            if isinstance(instruction, Mapping):
                parts = instruction.get("parts") or []
                return sum(len(p.get("text") or "") for p in parts if isinstance(p, Mapping))
            parts = getattr(instruction, "parts", None) or []
            return sum(len(getattr(p, "text", "") or "") for p in parts)
        except Exception:
            return -1

    def _summarize_tool_names(self, tools):
        names = []
        for tool in tools:
            declarations = None
            if isinstance(tool, Mapping):
                declarations = tool.get("function_declarations")
            else:
                declarations = getattr(tool, "function_declarations", None)
            for declaration in declarations or []:
                if isinstance(declaration, Mapping):
                    name = declaration.get("name")
                else:
                    name = getattr(declaration, "name", None)
                if name:
                    names.append(str(name))
        return names

    def _build_tools(self):
        funcs = self.config.get("functions") or []
        function_declarations = []
        for entry in funcs:
            spec = entry.get("function") if isinstance(entry, Mapping) else None
            if not isinstance(spec, Mapping):
                continue
            name = spec.get("name")
            if not name:
                continue
            description = spec.get("description", "") or ""
            parameters = self._sanitize_schema(spec.get("parameters"))
            function_declarations.append(
                self._build_function_declaration(name, description, parameters)
            )
        if not function_declarations:
            return None
        if self._types is not None and hasattr(self._types, "Tool"):
            return [self._types.Tool(function_declarations=function_declarations)]
        return [{"function_declarations": function_declarations}]

    def _build_function_declaration(self, name, description, parameters):
        if self._types is not None and hasattr(self._types, "FunctionDeclaration"):
            kwargs = {"name": name, "description": description}
            if parameters:
                kwargs["parameters"] = parameters
            return self._types.FunctionDeclaration(**kwargs)
        declaration = {"name": name, "description": description}
        if parameters:
            declaration["parameters"] = parameters
        return declaration

    def _sanitize_schema(self, schema):
        if isinstance(schema, Mapping):
            sanitized = {}
            for key, value in schema.items():
                if key in {"additionalProperties", "$schema", "title", "default"}:
                    continue
                sanitized[key] = self._sanitize_schema(value)
            return sanitized
        if isinstance(schema, list):
            return [self._sanitize_schema(value) for value in schema]
        return schema

    def _build_function_response(self, response):
        if not isinstance(response, Mapping):
            raise TypeError("Tool response must be a mapping")
        name = response.get("name")
        if not name:
            raise ValueError("Tool response is missing 'name'")
        payload = response.get("response")
        if not isinstance(payload, Mapping):
            payload = {"result": "" if payload is None else str(payload)}
        call_id = response.get("id")
        if self._types is not None and hasattr(self._types, "FunctionResponse"):
            kwargs = {"name": name, "response": dict(payload)}
            if call_id is not None:
                kwargs["id"] = call_id
            try:
                return self._types.FunctionResponse(**kwargs)
            except TypeError:
                kwargs.pop("id", None)
                return self._types.FunctionResponse(**kwargs)
        result = {"name": name, "response": dict(payload)}
        if call_id is not None:
            result["id"] = call_id
        return result

    def _audio_input_enabled(self):
        return bool(self.config.get("enable_audio_input", True))

    def _build_speech_config(self):
        if not self.config.get("native_voice", True):
            return None

        speech_config = {}
        language_code = self.config.get("language_code")
        if language_code:
            speech_config["language_code"] = language_code

        voice_name = self.config.get("voice_name")
        if voice_name:
            speech_config["voice_config"] = {
                "prebuilt_voice_config": {
                    "voice_name": voice_name,
                }
            }

        return speech_config or None

    def _build_realtime_input_config(self):
        if bool(self.config.get("disable_server_side_interruptions", True)):
            return {
                "activity_handling": "NO_INTERRUPTION",
                "turn_coverage": self.config.get(
                    "turn_coverage",
                    "TURN_INCLUDES_ALL_INPUT",
                ),
            }
        # DSI=false: Live VAD is in charge of interruption. Expose sensitivity
        # knobs so we can dial down trigger-on-echo without flipping back to
        # no-interruption mode. These pass straight through to the Live API
        # `automatic_activity_detection` sub-config when set.
        aad = {}
        for key in (
            "start_of_speech_sensitivity",
            "end_of_speech_sensitivity",
        ):
            value = self.config.get(key)
            if value:
                aad[key] = value
        for key in ("prefix_padding_ms", "silence_duration_ms"):
            value = self.config.get(key)
            if value is None:
                continue
            try:
                aad[key] = int(value)
            except (TypeError, ValueError):
                continue
        if not aad:
            return None
        return {
            "automatic_activity_detection": aad,
            "turn_coverage": self.config.get(
                "turn_coverage",
                "TURN_INCLUDES_ALL_INPUT",
            ),
        }

    def _get_connect_timeout(self):
        timeout_value = self.config.get("connect_timeout_sec")
        return self._normalize_timeout(timeout_value)

    def _get_receive_timeout(self):
        timeout_value = self.config.get("recv_timeout_sec")
        return self._normalize_timeout(timeout_value)

    def _normalize_timeout(self, timeout_value):
        if timeout_value in (None, ""):
            return None
        try:
            timeout = float(timeout_value)
        except (TypeError, ValueError):
            return None
        if timeout <= 0:
            return None
        return timeout

    def _build_blob(self, audio_bytes, mime_type):
        if self._types is not None and hasattr(self._types, "Blob"):
            return self._types.Blob(data=audio_bytes, mime_type=mime_type)
        return {"data": audio_bytes, "mime_type": mime_type}

    def _build_text_turn(self, text):
        if (
            self._types is not None
            and hasattr(self._types, "Content")
            and hasattr(self._types, "Part")
        ):
            return self._types.Content(
                role="user",
                parts=[self._types.Part(text=text)],
            )
        return {"role": "user", "parts": [{"text": text}]}

    def _resolve_api_key(self):
        api_key = self.config.get("api_key") or ""
        if api_key.startswith("${") and api_key.endswith("}"):
            env_name = api_key[2:-1]
            api_key = os.environ.get(env_name, "")
        return "".join(str(api_key).split())

    def _import_genai_module(self):
        # Return the eagerly-imported module from server startup so that the
        # first device connection does not pay the 80-100s SDK-import cost.
        if _GENAI_MODULE_EAGER is not None:
            return _GENAI_MODULE_EAGER
        # Fallback: if eager import failed at module load (package missing),
        # retry here and raise the same RuntimeError as before.
        try:
            from google import genai as genai_module
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is required for Google Live mode"
            ) from exc
        return genai_module

    def _normalize_message(self, message):
        events = []
        go_away = self._extract_field(message, "go_away")
        if go_away is not None:
            events.append(
                {
                    "type": "session_expiring",
                    "time_left_ms": self._extract_time_left_ms(go_away),
                }
            )

        tool_call = self._extract_field(message, "tool_call")
        tool_call_event = self._normalize_tool_call(tool_call)
        if tool_call_event is not None:
            events.append(tool_call_event)

        tool_call_cancellation = self._extract_field(
            message, "tool_call_cancellation"
        )
        cancellation_event = self._normalize_tool_call_cancellation(
            tool_call_cancellation
        )
        if cancellation_event is not None:
            events.append(cancellation_event)

        server_content = getattr(message, "server_content", None)
        if server_content is None and isinstance(message, Mapping):
            server_content = message.get("server_content")

        if server_content is not None:
            events.extend(self._normalize_server_content(server_content))
            return events

        if events:
            return events

        text = self._extract_field(message, "text")
        if text:
            events.append({"type": "transcript", "text": text, "source": "model"})
        return events

    def _normalize_tool_call(self, tool_call):
        if tool_call is None:
            return None
        function_calls = self._extract_field(tool_call, "function_calls") or []
        calls = []
        for function_call in function_calls:
            name = self._extract_field(function_call, "name")
            if not name:
                continue
            calls.append(
                {
                    "id": self._extract_field(function_call, "id"),
                    "name": name,
                    "args": self._extract_field(function_call, "args") or {},
                }
            )
        if not calls:
            return None
        return {"type": "tool_call", "calls": calls}

    def _normalize_tool_call_cancellation(self, cancellation):
        if cancellation is None:
            return None
        ids = self._extract_field(cancellation, "ids") or []
        cancelled_ids = [str(call_id) for call_id in ids if call_id is not None]
        if not cancelled_ids:
            return None
        return {"type": "tool_call_cancellation", "ids": cancelled_ids}

    def _extract_time_left_ms(self, go_away):
        time_left = self._extract_field(go_away, "time_left")
        if time_left is None:
            return None
        if isinstance(time_left, (int, float)):
            return int(float(time_left) * 1000)
        if isinstance(time_left, str):
            try:
                trimmed = time_left.rstrip("sS")
                return int(float(trimmed) * 1000)
            except ValueError:
                return None
        seconds = self._extract_field(time_left, "seconds")
        nanos = self._extract_field(time_left, "nanos") or 0
        if seconds is None:
            return None
        try:
            return int(float(seconds) * 1000 + float(nanos) / 1_000_000)
        except (TypeError, ValueError):
            return None

    def _normalize_server_content(self, server_content):
        events = []
        if self._extract_field(server_content, "interrupted"):
            events.append({"type": "interruption"})

        transcript_candidates = (
            ("user", self._extract_field(server_content, "input_transcription")),
            ("model", self._extract_field(server_content, "output_transcription")),
        )
        for source, candidate in transcript_candidates:
            text = self._extract_field(candidate, "text")
            if text:
                events.append({"type": "transcript", "text": text, "source": source})

        model_turn = self._extract_field(server_content, "model_turn")
        parts = self._extract_field(model_turn, "parts") or []
        for part in parts:
            inline_data = self._extract_field(part, "inline_data")
            audio_bytes = self._extract_field(inline_data, "data")
            mime_type = self._extract_field(inline_data, "mime_type")
            if audio_bytes:
                if not self._audio_started:
                    events.append({"type": "audio_start"})
                    self._audio_started = True
                    self._audio_chunk_count = 0
                    self._audio_byte_count = 0
                self._audio_chunk_count += 1
                self._audio_byte_count += len(audio_bytes)
                events.append(
                    {
                        "type": "audio_chunk",
                        "audio": audio_bytes,
                        "mime_type": mime_type,
                    }
                )

        if self._extract_field(server_content, "turn_complete") and self._audio_started:
            events.extend(self._finish_open_audio_turn("turn_complete"))
        return events

    def _finish_open_audio_turn(self, reason):
        if not self._audio_started:
            return []
        self.logger.bind(tag="GoogleLive").info(
            "Google Live audio_end reason={} chunks={} bytes={}",
            reason,
            self._audio_chunk_count,
            self._audio_byte_count,
        )
        self._audio_started = False
        self._audio_chunk_count = 0
        self._audio_byte_count = 0
        return [{"type": "audio_end"}]

    def _extract_field(self, obj, key):
        if obj is None:
            return None
        if isinstance(obj, Mapping):
            return obj.get(key)
        return getattr(obj, key, None)


class GoogleLiveClientFactory:
    @staticmethod
    def create(config, logger):
        return GoogleLiveClient(config, logger)
