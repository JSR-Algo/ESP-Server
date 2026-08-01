import unittest
import asyncio
from types import SimpleNamespace

from core.providers.tools.product_toolset import product_tool_names
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.session_orchestrator import SessionMode
from core.voice.session_provider.google_live import GoogleLiveProvider
from plugins_func.functions.lesson_conversation import (
    LESSON_CONVERSATION_TOOL_SPECS,
    lesson_child_response,
)
from plugins_func.register import Action, ActionResponse


LESSON_TOOL_NAMES = {
    "lesson_child_response",
    "lesson_pronunciation_outcome",
    "lesson_context_turn",
    "lesson_visual_reaction",
    "lesson_continue",
}


class _Logger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Conn:
    def __init__(self, handler=None):
        self.device_id = "robot-1"
        self.session_id = "server-session"
        self.session_mode = SessionMode.LESSON
        self.config = {
            "lesson": {
                "runtime_enabled": True,
                "rollout_device_allowlist": ["robot-1"],
            },
            "google_live": {"api_key": "key", "model": "gemini-live"},
            "prompt": "General child-safe chat prompt.",
        }
        self.logger = _Logger()
        self.func_handler = handler
        self.lesson_runtime = None
        self.client_abort = False
        self.client_is_speaking = False
        self.sample_rate = 24000
        self.google_live_audio_out_started_at = None

    def _lesson_runtime_enabled(self):
        return True

    def clear_queues(self):
        pass

    def clearSpeakStatus(self):
        pass


class _Client:
    connected = True

    def __init__(self):
        self.responses = []

    async def send_tool_response(self, responses):
        self.responses.append(responses)

    async def send_text(self, text):
        self.responses.append(text)


class _Handler:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or ActionResponse(
            action=Action.REQLLM,
            result={
                "accepted": True,
                "code": "ACCEPTED",
                "nextIntent": "bridge_vietnamese",
                "cueId": "word-1-teach",
                "effect": "show_teaching_scene",
            },
        )

    async def handle_llm_function_call(self, _conn, payload):
        self.calls.append(payload)
        return self.response


class LessonConversationSchemaTest(unittest.TestCase):
    def test_exact_five_tool_schemas_are_closed_and_identity_bound(self):
        self.assertEqual(set(LESSON_CONVERSATION_TOOL_SPECS), LESSON_TOOL_NAMES)
        common = {"lessonSessionId", "turnSequenceId", "attemptId", "stepKey"}
        for name, spec in LESSON_CONVERSATION_TOOL_SPECS.items():
            params = spec["function"]["parameters"]
            self.assertIs(params["additionalProperties"], False)
            self.assertTrue(common.issubset(set(params["required"])))
            self.assertTrue(common.issubset(set(params["properties"])))
            forbidden = {"filename", "mastered", "lessonPath", "nextStepKey"}
            self.assertTrue(forbidden.isdisjoint(params["properties"]))
            if name == "lesson_visual_reaction":
                self.assertIn("cueId", params["required"])

    def test_live_declarations_preserve_closed_lesson_schema(self):
        from core.voice.google_live.client import GoogleLiveClient

        client = GoogleLiveClient(
            {"functions": list(LESSON_CONVERSATION_TOOL_SPECS.values())},
            _Logger(),
        )
        declarations = client._build_tools()[0]["function_declarations"]
        self.assertEqual({item["name"] for item in declarations}, LESSON_TOOL_NAMES)
        self.assertTrue(all(item["parameters"]["additionalProperties"] is False for item in declarations))

    def test_typed_sdk_boundary_strips_unsupported_schema_without_mutating_canonical(self):
        from core.voice.google_live.client import GoogleLiveClient

        captured = []

        class FunctionDeclaration:
            def __init__(self, **kwargs):
                def assert_supported(value):
                    if isinstance(value, dict):
                        assert "additionalProperties" not in value
                        for nested in value.values():
                            assert_supported(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            assert_supported(nested)

                assert_supported(kwargs.get("parameters"))
                captured.append(kwargs)

        client = GoogleLiveClient(
            {"functions": list(LESSON_CONVERSATION_TOOL_SPECS.values())},
            _Logger(),
        )
        client._types = SimpleNamespace(
            FunctionDeclaration=FunctionDeclaration,
            Tool=lambda **kwargs: kwargs,
        )
        client._build_tools()

        self.assertEqual(len(captured), 5)
        self.assertIs(
            LESSON_CONVERSATION_TOOL_SPECS["lesson_child_response"]["function"]["parameters"]["additionalProperties"],
            False,
        )

    def test_pinned_google_genai_constructs_all_lesson_declarations_when_available(self):
        try:
            from google.genai import types
        except ImportError:
            self.skipTest("google-genai is not installed in this interpreter")
        from core.voice.google_live.client import GoogleLiveClient

        client = GoogleLiveClient(
            {"functions": list(LESSON_CONVERSATION_TOOL_SPECS.values())},
            _Logger(),
        )
        client._types = types

        tools = client._build_tools()

        self.assertEqual(len(tools[0].function_declarations), 5)

    def test_lesson_enabled_product_surface_includes_only_semantic_lesson_tools(self):
        names = set(product_tool_names(_Conn()))
        self.assertTrue(LESSON_TOOL_NAMES.issubset(names))
        self.assertNotIn("reboot_device", names)

    def test_lesson_instruction_is_scoped_to_active_lesson(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn)
        prompt = provider._get_live_config_with_functions()["system_prompt"]
        self.assertIn("never say", prompt.lower())
        self.assertIn("two contextual turns", prompt.lower())
        self.assertIn("Vietnamese", prompt)

        conn.session_mode = SessionMode.CONVERSATION
        conn.lesson_runtime = None
        prompt = GoogleLiveProvider(conn)._get_live_config_with_functions()["system_prompt"]
        self.assertEqual(prompt, "General child-safe chat prompt.")


class _Decision:
    def to_mapping(self):
        return {
            "accepted": True,
            "code": "ACCEPTED",
            "state": "BRIDGING",
            "next_intent": "bridge_vietnamese",
            "cue_id": "word-1-teach",
            "effect": "show_teaching_scene",
            "coaching_level": 0,
            "outcome": "comprehension_only",
            "review_needed": False,
            "guidance": {"target_word": "barn"},
        }


class _Runtime:
    def __init__(self):
        self.calls = []

    async def conversation_child_response(self, identity, response_class):
        self.calls.append((identity, response_class))
        return _Decision()

    def conversation_tool_context(self):
        return {
            "identity": {
                "lessonSessionId": "lesson-session",
                "turnSequenceId": 3,
                "attemptId": "attempt-1",
                "stepKey": "word-1",
                "cueId": "word-1-teach",
            },
            "allowedTools": ["lesson_visual_reaction"],
        }


class _InterruptConversation:
    attempt_id = "attempt-1"
    turn_sequence_id = 4

    def identity(self):
        return SimpleNamespace(
            lesson_session_id="lesson-session",
            attempt_id=self.attempt_id,
            step_key="word-1",
        )


class _InterruptRuntime:
    def __init__(self):
        self.conversation = _InterruptConversation()
        self.identities = []

    async def conversation_interrupt_current(self):
        self.identities.append(self.conversation.identity())
        self.conversation.turn_sequence_id += 1
        return SimpleNamespace(accepted=True, code="ACCEPTED")

    def conversation_tool_context(self):
        return {
            "identity": {
                "lessonSessionId": "lesson-session",
                "turnSequenceId": self.conversation.turn_sequence_id,
                "attemptId": "attempt-1",
                "stepKey": "word-1",
                "cueId": "word-1-listen",
            },
            "allowedTools": ["lesson_visual_reaction"],
        }


class LessonConversationPluginTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_maps_camel_identity_to_authoritative_runtime(self):
        conn = _Conn()
        conn.lesson_runtime = _Runtime()
        result = await lesson_child_response(
            conn,
            lessonSessionId="lesson-session",
            turnSequenceId=2,
            attemptId="attempt-1",
            stepKey="word-1",
            responseClass="meaning_vi",
        )

        identity, response_class = conn.lesson_runtime.calls[0]
        self.assertEqual(identity.lesson_session_id, "lesson-session")
        self.assertEqual(identity.turn_sequence_id, 2)
        self.assertEqual(identity.cue_id, None)
        self.assertEqual(response_class, "meaning_vi")
        self.assertEqual(result.result["nextIntent"], "bridge_vietnamese")
        self.assertEqual(result.result["context"]["identity"]["turnSequenceId"], 3)

    async def test_handler_rejects_extra_fields_before_runtime_mutation(self):
        conn = _Conn()
        conn.lesson_runtime = _Runtime()
        result = await lesson_child_response(
            conn,
            lessonSessionId="lesson-session",
            turnSequenceId=2,
            attemptId="attempt-1",
            stepKey="word-1",
            responseClass="meaning_vi",
            mastered=True,
        )

        self.assertEqual(conn.lesson_runtime.calls, [])
        self.assertEqual(result.result["code"], "INVALID_TOOL_ARGS")

    async def test_handler_rejects_when_conversation_is_not_active(self):
        conn = _Conn()
        result = await lesson_child_response(
            conn,
            lessonSessionId="lesson-session",
            turnSequenceId=2,
            attemptId="attempt-1",
            stepKey="word-1",
            responseClass="meaning_vi",
        )

        self.assertEqual(result.result["code"], "CONVERSATION_NOT_ACTIVE")


class LessonConversationProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_captures_generation_before_delayed_tool_only_turn_arrives(self):
        from core.voice.google_live.client import GoogleLiveClient

        release = asyncio.Event()
        current = 7

        class Session:
            def __init__(self):
                self.calls = 0

            def receive(self):
                self.calls += 1

                async def messages():
                    if self.calls == 1:
                        await release.wait()
                        yield SimpleNamespace(
                            tool_call=SimpleNamespace(
                                function_calls=[
                                    SimpleNamespace(
                                        id="tool-only",
                                        name="lesson_continue",
                                        args={},
                                    )
                                ]
                            )
                        )

                return messages()

        client = GoogleLiveClient({}, _Logger())
        client.connected = True
        client._session = Session()
        client.set_response_generation_getter(lambda: current)
        events = client.receive_events()
        pending = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        current = 8
        release.set()

        event = await pending

        self.assertEqual(event["response_generation"], 7)
        await events.aclose()

    async def test_reused_live_activation_injects_instruction_and_context_once(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn)
        conn.voice_provider = provider
        provider._client = _Client()
        provider._session_generation = 4
        context = {
            "identity": {
                "lessonSessionId": "lesson-session",
                "turnSequenceId": 2,
                "attemptId": "attempt-1",
                "stepKey": "word-1",
                "cueId": "word-1-listen",
            },
            "nextIntent": "scene_question",
            "allowedTools": ["lesson_visual_reaction"],
            "cueId": "word-1-listen",
            "effect": "show_listening_scene",
        }

        await provider.publish_lesson_conversation_context(context)
        await provider.publish_lesson_conversation_context(context)

        self.assertEqual(len(provider._client.responses), 1)
        sent = provider._client.responses[0]
        self.assertIn("Never say", sent)
        self.assertIn('"attemptId":"attempt-1"', sent)

    async def test_fresh_lesson_connect_sends_context_without_duplicate_instruction(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn)
        conn.voice_provider = provider
        provider._client = _Client()
        provider._session_generation = 4
        provider._lesson_instruction_generation = 4
        context = {"identity": {"attemptId": "attempt-1"}, "allowedTools": []}

        await provider.publish_lesson_conversation_context(context)

        self.assertEqual(len(provider._client.responses), 1)
        self.assertNotIn("Never say", provider._client.responses[0])
        self.assertIn('"attemptId":"attempt-1"', provider._client.responses[0])

    async def test_lesson_exit_clears_reused_live_coaching_constraint(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn)
        provider._client = _Client()
        provider._session_generation = 2
        provider._lesson_instruction_generation = 2

        await provider.deactivate_lesson_conversation_context()

        self.assertIn("lesson has ended", provider._client.responses[0].lower())
        self.assertIsNone(provider._lesson_instruction_generation)

    async def test_barge_in_interrupts_authoritative_conversation(self):
        conn = _Conn()
        conn.lesson_runtime = _InterruptRuntime()
        provider = GoogleLiveProvider(conn)

        await provider._interrupt_lesson_conversation()

        self.assertEqual(len(conn.lesson_runtime.identities), 1)
        snapshot = provider._interaction.snapshot()
        self.assertEqual(snapshot["lesson_session_id"], "lesson-session")
        self.assertEqual(snapshot["lesson_turn_sequence_id"], 5)

    async def test_lesson_mode_admits_semantic_tool_and_preserves_structured_decision(self):
        handler = _Handler()
        conn = _Conn(handler)
        provider = GoogleLiveProvider(conn)
        provider._client = _Client()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "response_generation": 0,
                "calls": [
                    {
                        "id": "call-1",
                        "name": "lesson_context_turn",
                        "args": {
                            "lessonSessionId": "lesson-session",
                            "turnSequenceId": 2,
                            "attemptId": "attempt-1",
                            "stepKey": "word-1",
                        },
                    }
                ],
            }
        )

        self.assertEqual(len(handler.calls), 1)
        response = provider._client.responses[0][0]["response"]
        self.assertIs(response["accepted"], True)
        self.assertEqual(response["nextIntent"], "bridge_vietnamese")
        self.assertNotIn("result", response)

    async def test_lesson_mode_still_blocks_non_lesson_tools(self):
        handler = _Handler()
        conn = _Conn(handler)
        provider = GoogleLiveProvider(conn)
        provider._client = _Client()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-2", "name": "center_head", "args": {}}],
            }
        )

        self.assertEqual(handler.calls, [])
        payload = provider._client.responses[0][0]["response"]
        self.assertEqual(payload["errorCode"], "LESSON_MODE_TOOL_BLOCKED")

    async def test_active_runtime_blocks_non_lesson_tool_before_mode_switch_finishes(self):
        handler = _Handler()
        conn = _Conn(handler)
        conn.session_mode = SessionMode.CONVERSATION
        conn.lesson_runtime = SimpleNamespace(state="RUNNING")
        provider = GoogleLiveProvider(conn)
        provider._client = _Client()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-early", "name": "center_head", "args": {}}],
            }
        )

        self.assertEqual(handler.calls, [])
        payload = provider._client.responses[0][0]["response"]
        self.assertEqual(payload["errorCode"], "LESSON_MODE_TOOL_BLOCKED")

    async def test_old_response_generation_cannot_dispatch_lesson_mutation(self):
        handler = _Handler()
        conn = _Conn(handler)
        provider = GoogleLiveProvider(conn)
        provider._client = _Client()
        provider._response_generation = 4

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "response_generation": 3,
                "calls": [
                    {
                        "id": "late-call",
                        "name": "lesson_continue",
                        "args": {
                            "lessonSessionId": "lesson-session",
                            "turnSequenceId": 9,
                            "attemptId": "attempt-1",
                            "stepKey": "word-1",
                        },
                    }
                ],
            }
        )

        self.assertEqual(handler.calls, [])
        payload = provider._client.responses[0][0]["response"]
        self.assertEqual(payload["errorCode"], "STALE_MODEL_RESPONSE")

    async def test_audio_bridge_forwards_only_allowlisted_lesson_tool_calls(self):
        conn = _Conn()
        calls = []

        async def handler(event):
            calls.append(event)

        bridge = GoogleLiveAudioBridge(
            conn,
            SimpleNamespace(),
            conn.logger,
            tool_call_handler=handler,
        )
        await bridge.handle_event(
            {
                "type": "tool_call",
                "response_generation": 0,
                "calls": [{"id": "1", "name": "lesson_child_response", "args": {}}],
            }
        )
        await bridge.handle_event(
            {
                "type": "tool_call",
                "calls": [{"id": "2", "name": "center_head", "args": {}}],
            }
        )

        self.assertEqual([event["calls"][0]["name"] for event in calls], ["lesson_child_response"])
        await bridge.close()

    async def test_tool_only_event_preserves_client_captured_origin_generation(self):
        conn = _Conn()
        calls = []

        async def handler(event):
            calls.append(event)

        bridge = GoogleLiveAudioBridge(
            conn,
            SimpleNamespace(),
            conn.logger,
            response_id_getter=lambda: 8,
            tool_call_handler=handler,
        )
        await bridge.handle_event(
            {
                "type": "tool_call",
                "response_generation": 7,
                "calls": [{"id": "old", "name": "lesson_continue", "args": {}}],
            }
        )

        self.assertEqual(calls[0]["response_generation"], 7)
        await bridge.close()

    async def test_post_audio_end_late_tool_keeps_pre_barge_generation(self):
        conn = _Conn()
        calls = []
        current = 3

        async def handler(event):
            calls.append(event)

        bridge = GoogleLiveAudioBridge(
            conn,
            SimpleNamespace(),
            conn.logger,
            response_id_getter=lambda: current,
            tool_call_handler=handler,
        )
        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event({"type": "audio_end"})
        current = 4
        await bridge.handle_event(
            {
                "type": "tool_call",
                "calls": [{"id": "late", "name": "lesson_continue", "args": {}}],
            }
        )

        self.assertEqual(calls[0]["response_generation"], 3)
        await bridge.close()

    async def test_lesson_tool_without_origin_generation_is_dropped_fail_closed(self):
        conn = _Conn()
        calls = []

        async def handler(event):
            calls.append(event)

        bridge = GoogleLiveAudioBridge(
            conn,
            SimpleNamespace(),
            conn.logger,
            response_id_getter=lambda: 5,
            tool_call_handler=handler,
        )
        await bridge.handle_event(
            {
                "type": "tool_call",
                "calls": [{"id": "unknown", "name": "lesson_continue", "args": {}}],
            }
        )

        self.assertEqual(calls, [])
        await bridge.close()
