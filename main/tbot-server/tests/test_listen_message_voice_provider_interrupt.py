import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


_STUBBED_MODULES = (
    "core.handle.receiveAudioHandle",
    "core.handle.reportHandle",
    "core.handle.sendAudioHandle",
    "core.handle.textMessageHandler",
    "core.handle.textMessageType",
    "core.utils.util",
    "core.providers.asr.dto.dto",
)


def _restore_import_stubs(originals):
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _install_listen_handler_import_stubs():
    if "core.handle.textHandler.listenMessageHandler" in sys.modules:
        return {}

    originals = {name: sys.modules.get(name) for name in _STUBBED_MODULES}

    receive_audio_module = types.ModuleType("core.handle.receiveAudioHandle")
    receive_audio_module.startToChat = lambda *args, **kwargs: None

    report_module = types.ModuleType("core.handle.reportHandle")
    report_module.enqueue_asr_report = lambda *args, **kwargs: None

    send_audio_module = types.ModuleType("core.handle.sendAudioHandle")
    send_audio_module.send_stt_message = lambda *args, **kwargs: None
    send_audio_module.send_tts_message = lambda *args, **kwargs: None

    text_message_handler_module = types.ModuleType("core.handle.textMessageHandler")
    text_message_handler_module.TextMessageHandler = object

    text_message_type_module = types.ModuleType("core.handle.textMessageType")

    class _DummyTextMessageType:
        LISTEN = "listen"

    text_message_type_module.TextMessageType = _DummyTextMessageType

    util_module = types.ModuleType("core.utils.util")
    util_module.remove_punctuation_and_length = lambda text: (len(text), text)

    asr_dto_module = types.ModuleType("core.providers.asr.dto.dto")

    class _DummyInterfaceType:
        STREAM = "stream"

    asr_dto_module.InterfaceType = _DummyInterfaceType

    sys.modules.update(
        {
            "core.handle.receiveAudioHandle": receive_audio_module,
            "core.handle.reportHandle": report_module,
            "core.handle.sendAudioHandle": send_audio_module,
            "core.handle.textMessageHandler": text_message_handler_module,
            "core.handle.textMessageType": text_message_type_module,
            "core.utils.util": util_module,
            "core.providers.asr.dto.dto": asr_dto_module,
        }
    )
    return originals


_ORIGINAL_MODULES = _install_listen_handler_import_stubs()

listen_module = importlib.import_module("core.handle.textHandler.listenMessageHandler")
_restore_import_stubs(_ORIGINAL_MODULES)
ListenTextMessageHandler = listen_module.ListenTextMessageHandler


class _DummyLogger:
    def __init__(self):
        self.debugs = []

    def bind(self, **kwargs):
        return self

    def debug(self, message, *args, **kwargs):
        self.debugs.append(message)


class _DummyVoiceProvider:
    def __init__(self):
        self.interrupt_calls = 0

    async def interrupt(self):
        self.interrupt_calls += 1


class _DummyConn:
    def __init__(self):
        self.client_listen_mode = "auto"
        self.client_is_speaking = True
        self.client_voice_stop = False
        self.client_have_voice = True
        self.just_woken_up = False
        self.last_activity_time = 0
        self.asr_audio = []
        self.config = {"wakeup_words": ["hiesp"], "enable_greeting": True}
        self.voice_provider = _DummyVoiceProvider()
        self.logger = _DummyLogger()
        self.reset_audio_states_calls = 0
        self.asr = types.SimpleNamespace(
            interface_type="non-stream",
            _send_stop_request=AsyncMock(),
            handle_voice_stop=AsyncMock(),
        )

    def reset_audio_states(self):
        self.reset_audio_states_calls += 1


class ListenMessageVoiceProviderInterruptTest(unittest.IsolatedAsyncioTestCase):
    def test_message_type_is_listen(self):
        self.assertEqual(ListenTextMessageHandler().message_type, listen_module.TextMessageType.LISTEN)

    async def test_listen_start_interrupts_voice_provider_before_reset(self):
        conn = _DummyConn()
        handler = ListenTextMessageHandler()

        await handler.handle(conn, {"state": "start"})

        self.assertEqual(conn.voice_provider.interrupt_calls, 1)
        self.assertEqual(conn.reset_audio_states_calls, 1)

    async def test_listen_start_updates_mode_and_skips_interrupt_for_manual_or_missing_provider(self):
        handler = ListenTextMessageHandler()
        manual = _DummyConn()

        await handler.handle(manual, {"state": "start", "mode": "manual"})

        self.assertEqual(manual.client_listen_mode, "manual")
        self.assertIn("Client listening mode: manual", manual.logger.debugs)
        self.assertEqual(manual.voice_provider.interrupt_calls, 0)
        self.assertEqual(manual.reset_audio_states_calls, 1)

        no_provider = _DummyConn()
        no_provider.voice_provider = None
        await handler.handle(no_provider, {"state": "start"})
        self.assertEqual(no_provider.reset_audio_states_calls, 1)

    async def test_listen_stop_streaming_schedules_asr_stop_request(self):
        conn = _DummyConn()
        conn.asr.interface_type = listen_module.InterfaceType.STREAM
        created = []

        def create_task(coro):
            created.append(coro)
            coro.close()
            return types.SimpleNamespace(done=lambda: True)

        with patch.object(listen_module.asyncio, "create_task", side_effect=create_task):
            await ListenTextMessageHandler().handle(conn, {"state": "stop"})

        self.assertTrue(conn.client_voice_stop)
        self.assertEqual(len(created), 1)

    async def test_listen_stop_non_streaming_handles_buffered_audio_and_ignores_empty_audio(self):
        handler = ListenTextMessageHandler()
        conn = _DummyConn()
        conn.asr_audio = [b"a", b"b"]

        await handler.handle(conn, {"state": "stop"})

        self.assertTrue(conn.client_voice_stop)
        self.assertEqual(conn.reset_audio_states_calls, 1)
        conn.asr.handle_voice_stop.assert_awaited_once_with(conn, [b"a", b"b"])

        empty = _DummyConn()
        await handler.handle(empty, {"state": "stop"})
        empty.asr.handle_voice_stop.assert_not_awaited()

    async def test_detect_wakeup_without_greeting_sends_stt_stop_and_does_not_chat(self):
        conn = _DummyConn()
        conn.config["enable_greeting"] = False
        starts = []

        async def send_stt(_conn, text):
            starts.append(("stt", text))

        async def send_tts(_conn, state, text=None):
            starts.append(("tts", state, text))

        with patch.object(listen_module, "send_stt_message", new=send_stt), patch.object(
            listen_module, "send_tts_message", new=send_tts
        ), patch.object(listen_module, "startToChat", new=AsyncMock()) as start_chat, patch.object(
            listen_module.time, "time", return_value=12.5
        ):
            await ListenTextMessageHandler().handle(conn, {"state": "detect", "text": "hiesp"})

        self.assertFalse(conn.client_have_voice)
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(conn.reset_audio_states_calls, 1)
        self.assertEqual(conn.last_activity_time, 12500)
        self.assertEqual(starts, [("stt", "hiesp"), ("tts", "stop", None)])
        start_chat.assert_not_awaited()

    async def test_detect_wakeup_with_greeting_reports_synthetic_hello_and_starts_chat(self):
        conn = _DummyConn()
        reports = []

        with patch.object(listen_module, "enqueue_asr_report", side_effect=lambda *args: reports.append(args)), patch.object(
            listen_module, "startToChat", new=AsyncMock()
        ) as start_chat:
            await ListenTextMessageHandler().handle(conn, {"state": "detect", "text": "hiesp"})

        self.assertTrue(conn.just_woken_up)
        self.assertEqual(reports, [(conn, "Hey, hello", [])])
        start_chat.assert_awaited_once_with(conn, "Hey, hello")

    async def test_detect_regular_text_reports_original_text_and_starts_chat(self):
        conn = _DummyConn()
        reports = []

        with patch.object(listen_module, "enqueue_asr_report", side_effect=lambda *args: reports.append(args)), patch.object(
            listen_module, "startToChat", new=AsyncMock()
        ) as start_chat:
            await ListenTextMessageHandler().handle(conn, {"state": "detect", "text": "xin chao"})

        self.assertTrue(conn.just_woken_up)
        self.assertEqual(reports, [(conn, "xin chao", [])])
        start_chat.assert_awaited_once_with(conn, "xin chao")

    async def test_detect_without_text_only_resets_voice_state(self):
        conn = _DummyConn()

        with patch.object(listen_module, "startToChat", new=AsyncMock()) as start_chat:
            await ListenTextMessageHandler().handle(conn, {"state": "detect"})

        self.assertFalse(conn.client_have_voice)
        self.assertEqual(conn.reset_audio_states_calls, 1)
        start_chat.assert_not_awaited()
