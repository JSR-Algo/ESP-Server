import importlib
import sys
import types
import unittest


def _install_listen_handler_import_stubs():
    if "core.handle.textHandler.listenMessageHandler" in sys.modules:
        return

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


_install_listen_handler_import_stubs()

listen_module = importlib.import_module("core.handle.textHandler.listenMessageHandler")
ListenTextMessageHandler = listen_module.ListenTextMessageHandler


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        return None


class _DummyVoiceProvider:
    def __init__(self):
        self.interrupt_calls = 0

    async def interrupt(self):
        self.interrupt_calls += 1


class _DummyConn:
    def __init__(self):
        self.client_listen_mode = "auto"
        self.client_is_speaking = True
        self.voice_provider = _DummyVoiceProvider()
        self.logger = _DummyLogger()
        self.reset_audio_states_calls = 0

    def reset_audio_states(self):
        self.reset_audio_states_calls += 1


class ListenMessageVoiceProviderInterruptTest(unittest.IsolatedAsyncioTestCase):
    async def test_listen_start_interrupts_voice_provider_before_reset(self):
        conn = _DummyConn()
        handler = ListenTextMessageHandler()

        await handler.handle(conn, {"state": "start"})

        self.assertEqual(conn.voice_provider.interrupt_calls, 1)
        self.assertEqual(conn.reset_audio_states_calls, 1)
