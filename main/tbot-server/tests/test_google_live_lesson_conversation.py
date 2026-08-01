import unittest
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

    async def conversation_interrupt(self, identity):
        self.identities.append(identity)
        self.conversation.turn_sequence_id += 1
        return SimpleNamespace(accepted=True, code="ACCEPTED")


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
