import asyncio
import os
import queue
import threading
import types

import pytest

from core.providers.asr import base as asr_base


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, message):
        self.records.append(("debug", message))

    def info(self, message):
        self.records.append(("info", message))

    def warning(self, message):
        self.records.append(("warning", message))

    def error(self, message):
        self.records.append(("error", message))


class _ASR(asr_base.ASRProviderBase):
    def __init__(self, output_dir, text="recognized", fail=False):
        super().__init__()
        self.output_dir = str(output_dir)
        self.delete_audio_file = True
        self.text = text
        self.fail = fail
        self.calls = []
        self._current_artifacts = "current"

    async def speech_to_text(self, opus_data, session_id, audio_format="opus", artifacts=None):
        self.calls.append((opus_data, session_id, audio_format, artifacts))
        if self.fail:
            raise RuntimeError("asr down")
        return self.text, "ignored"


class _Voiceprint:
    def __init__(self, result=None, fail=False):
        self.result = result
        self.fail = fail

    async def identify_speaker(self, wav_data, session_id):
        if self.fail:
            raise RuntimeError("voiceprint down")
        return self.result


def _conn(**kwargs):
    values = {
        "client_listen_mode": "auto",
        "asr_audio": [],
        "client_have_voice": False,
        "client_voice_stop": False,
        "asr": types.SimpleNamespace(interface_type="batch"),
        "audio_format": "pcm",
        "voiceprint_provider": None,
        "session_id": "session-1",
    }
    values.update(kwargs)
    conn = types.SimpleNamespace(**values)
    conn.reset_audio_states_called = 0

    def reset_audio_states():
        conn.reset_audio_states_called += 1
        conn.asr_audio = []

    conn.reset_audio_states = reset_audio_states
    return conn


@pytest.fixture(autouse=True)
def _logger(monkeypatch):
    logger = _Logger()
    monkeypatch.setattr(asr_base, "logger", logger)
    return logger


@pytest.mark.asyncio
async def test_open_audio_channels_and_priority_thread_processes_queue(monkeypatch):
    provider = _ASR("/tmp")
    started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.daemon))

    monkeypatch.setattr(asr_base.threading, "Thread", FakeThread)
    conn = types.SimpleNamespace()
    await provider.open_audio_channels(conn)
    assert conn.asr_priority_thread.daemon is True
    assert started[0][0] == provider.asr_text_priority_thread

    handled = []
    stop_event = types.SimpleNamespace(calls=0)

    def is_set():
        stop_event.calls += 1
        return stop_event.calls > 2

    stop_event.is_set = is_set

    class OneMessageQueue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout):
            self.calls += 1
            if self.calls == 1:
                return b"audio"
            raise queue.Empty

    class Future:
        def result(self):
            handled.append("result")

    monkeypatch.setattr(asr_base, "handleAudioMessage", lambda conn, message: asyncio.sleep(0))
    monkeypatch.setattr(asr_base.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), Future())[1])
    conn = types.SimpleNamespace(stop_event=stop_event, asr_audio_queue=OneMessageQueue(), loop=object())
    provider.asr_text_priority_thread(conn)
    assert handled == ["result"]

    stop_event.calls = 0

    class ErrorFuture:
        def result(self):
            raise RuntimeError("future down")

    monkeypatch.setattr(asr_base.asyncio, "run_coroutine_threadsafe", lambda coro, loop: (coro.close(), ErrorFuture())[1])
    provider.asr_text_priority_thread(types.SimpleNamespace(stop_event=stop_event, asr_audio_queue=OneMessageQueue(), loop=object()))


@pytest.mark.asyncio
async def test_receive_audio_manual_auto_trim_and_voice_stop(monkeypatch, tmp_path):
    provider = _ASR(tmp_path)
    conn = _conn(client_listen_mode="manual")
    await provider.receive_audio(conn, b"m", audio_have_voice=False)
    assert conn.asr_audio == [b"m"]

    conn = _conn(asr_audio=[bytes([i]) for i in range(12)])
    await provider.receive_audio(conn, b"new", audio_have_voice=False)
    assert len(conn.asr_audio) == 10

    handled = []

    async def fake_handle(conn, task):
        handled.append(task)

    monkeypatch.setattr(provider, "handle_voice_stop", fake_handle)
    conn = _conn(asr_audio=[b"x"] * 16, client_have_voice=True, client_voice_stop=True)
    await provider.receive_audio(conn, b"last", audio_have_voice=True)
    assert conn.reset_audio_states_called == 1
    assert len(handled[0]) == 17

    stream_conn = _conn(asr_audio=[b"x"] * 16, client_voice_stop=True, asr=types.SimpleNamespace(interface_type=asr_base.InterfaceType.STREAM))
    await provider.receive_audio(stream_conn, b"last", audio_have_voice=True)
    assert stream_conn.reset_audio_states_called == 0


@pytest.mark.asyncio
async def test_handle_voice_stop_plain_dict_voiceprint_and_error_paths(monkeypatch, tmp_path, _logger):
    provider = _ASR(tmp_path, text="hello")
    chats = []
    reports = []
    monkeypatch.setattr(asr_base, "startToChat", lambda conn, text: asyncio.sleep(0, result=chats.append(text)))
    monkeypatch.setattr(asr_base, "enqueue_asr_report", lambda conn, text, audio: reports.append((text, audio)))
    stopped = []
    monkeypatch.setattr(provider, "stop_ws_connection", lambda: stopped.append(True))
    conn = _conn(voiceprint_provider=_Voiceprint("Alice"))
    await provider.handle_voice_stop(conn, [b"\x01\x00"] * 16)
    assert chats and '"speaker": "Alice"' in chats[0]
    assert reports[0][1] == [b"\x01\x00"] * 16
    assert stopped == [True]

    provider = _ASR(tmp_path, text={"content": "hi", "language": "vi", "emotion": "happy"})
    monkeypatch.setattr(provider, "stop_ws_connection", lambda: None)
    conn = _conn(voiceprint_provider=_Voiceprint("Bob"))
    await provider.handle_voice_stop(conn, [b"\x01\x00"] * 16)
    assert any("RecognizeLanguage" in message for level, message in _logger.records if level == "info")

    provider = _ASR(tmp_path, fail=True)
    async def failing_wrapper(*args):
        raise RuntimeError("asr down")

    monkeypatch.setattr(provider, "speech_to_text_wrapper", failing_wrapper)
    conn = _conn(voiceprint_provider=_Voiceprint(fail=True))
    await provider.handle_voice_stop(conn, [b"\x01\x00"] * 16)
    assert any(level == "error" and "ASR recognition failed" in message for level, message in _logger.records)
    assert any(level == "error" and "Voiceprint recognition failed" in message for level, message in _logger.records)

    provider = _ASR(tmp_path)
    monkeypatch.setattr(provider, "speech_to_text_wrapper", lambda *args: (_ for _ in ()).throw(RuntimeError("wrapper down")))
    await provider.handle_voice_stop(_conn(), [b"x"])
    assert any(level == "error" and "Handle voice stop failed" in message for level, message in _logger.records)

    provider = _ASR(tmp_path, text="plain")
    monkeypatch.setattr(provider, "decode_opus", lambda frames: [b"\x01\x00"])
    await provider.handle_voice_stop(_conn(audio_format="opus", voiceprint_provider=None), [b"opus"] * 16)


def test_text_wav_artifact_defaults_and_temp_file_edges(monkeypatch, tmp_path, _logger):
    provider = _ASR(tmp_path)
    assert provider._build_enhanced_text("hello", " Alice ") == '{"speaker": " Alice ", "content": "hello"}'
    assert provider._build_enhanced_text("hello", " ") == "hello"
    assert provider._pcm_to_wav(b"") == b""
    assert provider._pcm_to_wav(b"\x01\x00\x02")[:4] == b"RIFF"
    assert provider.requires_file() is False
    assert provider.prefers_temp_file() is False
    assert provider.get_current_artifacts() == "current"
    provider.stop_ws_connection()
    awaitable_close(provider.close())
    assert provider.build_temp_file(b"\x01\x00") and os.path.exists(provider.build_temp_file(b"\x01\x00"))
    monkeypatch.setattr(asr_base.wave, "open", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("wave down")))
    assert provider._pcm_to_wav(b"\x01\x00") == b""
    monkeypatch.setattr(asr_base.tempfile, "NamedTemporaryFile", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temp down")))
    assert provider.build_temp_file(b"x") is None
    assert any(level == "error" and "TemporaryAudio" in message for level, message in _logger.records)


@pytest.mark.asyncio
async def test_speech_to_text_wrapper_file_temp_cleanup_and_error_paths(monkeypatch, tmp_path, _logger):
    provider = _ASR(tmp_path, text="ok")
    provider.delete_audio_file = False
    text, file_path = await provider.speech_to_text_wrapper([b"\x01\x00"], "session", audio_format="pcm")
    assert text == "ok"
    assert file_path is not None and os.path.exists(file_path)

    provider.delete_audio_file = True
    provider.requires_file = lambda: True
    provider.prefers_temp_file = lambda: False
    text, file_path = await provider.speech_to_text_wrapper([b"\x01\x00"], "session", audio_format="pcm")
    assert text == "ok"
    assert file_path is not None and not os.path.exists(file_path)

    provider = _ASR(tmp_path, text="ok")
    provider.requires_file = lambda: True
    provider.prefers_temp_file = lambda: True
    text, file_path = await provider.speech_to_text_wrapper([b"\x01\x00"], "session", audio_format="pcm")
    assert text == "ok"
    artifacts = provider.calls[-1][3]
    assert artifacts.temp_path and not os.path.exists(artifacts.temp_path)

    monkeypatch.setattr(asr_base.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=1))
    assert await provider.speech_to_text_wrapper([b"x" * 10], "session", audio_format="pcm") == (None, None)

    provider = _ASR(tmp_path, fail=True)
    monkeypatch.setattr(asr_base.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=10_000_000))
    assert await provider.speech_to_text_wrapper([b"\x01\x00"], "session", audio_format="pcm") == (None, None)
    assert any(level == "error" and "Speech recognition failed" in message for level, message in _logger.records)

    provider = _ASR(tmp_path, text="opus")
    monkeypatch.setattr(provider, "decode_opus", lambda packets: [b"\x01\x00"])
    assert await provider.speech_to_text_wrapper([b"opus"], "session", audio_format="opus") == ("opus", None)

    provider = _ASR(tmp_path, text="empty")
    assert await provider.speech_to_text_wrapper([], "session", audio_format="pcm") == ("empty", None)
    assert provider.calls[-1][3] is None

    provider = _ASR(tmp_path, text="cleanup")
    provider.requires_file = lambda: True
    provider.prefers_temp_file = lambda: False
    monkeypatch.setattr(asr_base.os, "remove", lambda path: (_ for _ in ()).throw(RuntimeError("remove down")))
    assert await provider.speech_to_text_wrapper([b"\x01\x00"], "session", audio_format="pcm") == ("cleanup", provider.calls[-1][3].file_path)
    assert any(level == "error" and "File cleanup failed" in message for level, message in _logger.records)

    assert await asr_base.ASRProviderBase.speech_to_text(provider, [], "session") is None


def test_decode_opus_success_packet_errors_and_outer_error(monkeypatch, _logger):
    class OpusError(Exception):
        pass

    class Decoder:
        def __init__(self, sample_rate, channels):
            self.calls = 0

        def decode(self, packet, buffer_size):
            self.calls += 1
            if packet == b"opus-error":
                raise OpusError("bad opus")
            if packet == b"generic-error":
                raise RuntimeError("bad packet")
            if packet == b"empty-output":
                return b""
            return b"pcm"

    monkeypatch.setattr(asr_base.opuslib_next, "OpusError", OpusError)
    monkeypatch.setattr(asr_base.opuslib_next, "Decoder", Decoder)
    assert asr_base.ASRProviderBase.decode_opus([b"", b"ok", b"opus-error", b"generic-error", b"empty-output"]) == [b"pcm"]
    assert any(level == "warning" and "OpusDecodeError" in message for level, message in _logger.records)
    assert any(level == "error" and "Audio ProcessingError" in message for level, message in _logger.records)

    monkeypatch.setattr(asr_base.opuslib_next, "Decoder", lambda *args: (_ for _ in ()).throw(RuntimeError("decoder down")))
    assert asr_base.ASRProviderBase.decode_opus([b"ok"]) == []
    assert any(level == "error" and "Audio decoding process" in message for level, message in _logger.records)


def awaitable_close(coro):
    return asyncio.run(coro)
