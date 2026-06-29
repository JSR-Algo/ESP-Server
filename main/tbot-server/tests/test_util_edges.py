import subprocess
import types

import pytest

from core.utils import util


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def error(self, message):
        self.records.append(("error", message))


class _Socket:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def connect(self, target):
        if self.fail:
            raise OSError("offline")

    def getsockname(self):
        return ("192.168.1.7", 12345)

    def close(self):
        self.closed = True


class _Cache:
    def __init__(self):
        self.values = {}
        self.sets = []

    def get(self, cache_type, key):
        return self.values.get((cache_type, key))

    def set(self, cache_type, key, value):
        self.sets.append((cache_type, key, value))
        self.values[(cache_type, key)] = value


class _Segment:
    def __init__(self, raw_data=b"\x01\x00\x02\x00"):
        self.raw_data = raw_data
        self.calls = []

    def set_channels(self, channels):
        self.calls.append(("channels", channels))
        return self

    def set_frame_rate(self, rate):
        self.calls.append(("rate", rate))
        return self

    def set_sample_width(self, width):
        self.calls.append(("width", width))
        return self


class _AudioSegment:
    calls = []

    @staticmethod
    def from_file(source, format=None, parameters=None):
        _AudioSegment.calls.append((source, format, parameters))
        return _Segment()


class _Encoder:
    def __init__(self, sample_rate, channels, application):
        self.sample_rate = sample_rate
        self.channels = channels
        self.application = application

    def encode(self, frame_bytes, frame_size):
        return b"opus" + frame_bytes[:2]


class _Decoder:
    def __init__(self, sample_rate, channels):
        self.sample_rate = sample_rate
        self.channels = channels

    def decode(self, opus_frame, frame_size):
        return b"\x01\x00" * frame_size


def test_network_config_string_and_validation_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(util.socket, "socket", lambda *args: _Socket())
    assert util.get_local_ip() == "192.168.1.7"
    monkeypatch.setattr(util.socket, "socket", lambda *args: _Socket(fail=True))
    assert util.get_local_ip() == "127.0.0.1"

    assert util.is_private_ip("10.0.0.1") is True
    assert util.is_private_ip("172.16.0.1") is True
    assert util.is_private_ip("192.168.0.1") is True
    assert util.is_private_ip("127.0.0.1") is True
    assert util.is_private_ip("169.254.1.1") is True
    assert util.is_private_ip("8.8.8.8") is False
    assert util.is_private_ip("fc00:0000:0000:0000:0000:0000:0000:0001") is True
    assert util.is_private_ip("fd00:0000:0000:0000:0000:0000:0000:0001") is True
    assert util.is_private_ip("fe80:0000:0000:0000:0000:0000:0000:0001") is True
    assert util.is_private_ip("2001:0db8:0000:0000:0000:0000:0000:0001") is False
    assert util.is_private_ip("bad") is False
    assert util.is_private_ip("999.1.1.1") is False

    path = tmp_path / "data.json"
    util.write_json_file(path, {"hello": "world"})
    assert '"hello"' in path.read_text(encoding="utf-8")
    assert util.remove_punctuation_and_length("Yeah!") == (0, "")
    assert util.remove_punctuation_and_length("hi, bot") == (5, "hibot")
    assert util.check_model_key("LLM", "Your API key") == "Config error: API key for LLM not set, current value: Your API key"
    assert util.check_model_key("LLM", "set") is None
    assert util.parse_string_to_list(None) == []
    assert util.parse_string_to_list("") == []
    assert util.parse_string_to_list("a; b; ;c") == ["a", "b", "c"]
    assert util.parse_string_to_list(["x"]) == ["x"]
    assert util.parse_string_to_list(123) == []
    assert util.extract_json_from_string("before {\"ok\": true} after") == '{"ok": true}'
    assert util.extract_json_from_string("none") is None
    assert util.sanitize_tool_name("tool name/天气") == "tool_name_天气"
    assert util.validate_mcp_endpoint("http://x/mcp/device") is False
    assert util.validate_mcp_endpoint("ws://x/key/mcp/device") is False
    assert util.validate_mcp_endpoint("ws://x/device") is False
    assert util.validate_mcp_endpoint("ws://x/mcp/device") is True
    assert util.get_system_error_response({})
    assert util.get_system_error_response({"system_error_response": "custom"}) == "custom"
    assert util.is_valid_image_file(b"\xff\xd8\xffdata") is True
    assert util.is_valid_image_file(b"\x89PNG\r\n\x1a\n") is True
    assert util.is_valid_image_file(b"GIF87a") is True
    assert util.is_valid_image_file(b"GIF89a") is True
    assert util.is_valid_image_file(b"BM") is True
    assert util.is_valid_image_file(b"II*\x00") is True
    assert util.is_valid_image_file(b"MM\x00*") is True
    assert util.is_valid_image_file(b"RIFFxxxxWEBP") is True
    assert util.is_valid_image_file(b"text") is False


def test_get_ip_info_cache_private_public_and_error(monkeypatch):
    from core.utils.cache import manager as cache_module

    cache = _Cache()
    monkeypatch.setattr(cache_module, "cache_manager", cache)
    logger = _Logger()
    cache.values[(cache_module.CacheType.IP_INFO, "cached")] = {"city": "Cached"}
    assert util.get_ip_info("cached", logger) == {"city": "Cached"}

    class Response:
        def json(self):
            return {"city": "Remote"}

    requested = []
    monkeypatch.setattr(util.requests, "get", lambda url: requested.append(url) or Response())
    assert util.get_ip_info("8.8.8.8", logger) == {"city": "Remote"}
    assert "ip=8.8.8.8" in requested[-1]
    assert util.get_ip_info("192.168.1.1", logger) == {"city": "Remote"}
    assert requested[-1].endswith("ip=")

    monkeypatch.setattr(util.requests, "get", lambda url: (_ for _ in ()).throw(RuntimeError("network down")))
    assert util.get_ip_info("4.4.4.4", logger) == {}
    assert any(level == "error" and "network down" in msg for level, msg in logger.records)


def test_ffmpeg_check_success_and_failure_messages(monkeypatch):
    monkeypatch.setattr(
        util.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout="ffmpeg version 6", stderr=""),
    )
    assert util.check_ffmpeg_installed() is True

    monkeypatch.setattr(
        util.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout="tool", stderr=""),
    )
    with pytest.raises(ValueError, match="No valid ffmpeg"):
        util.check_ffmpeg_installed()

    def raise_called(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr="libiconv.so.2 missing")

    monkeypatch.setattr(util.subprocess, "run", raise_called)
    with pytest.raises(ValueError, match="libiconv"):
        util.check_ffmpeg_installed()

    monkeypatch.setattr(util.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("ffmpeg no such file or directory")))
    with pytest.raises(ValueError, match="ffmpeg executable"):
        util.check_ffmpeg_installed()

    def raise_other(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr="bad codec")

    monkeypatch.setattr(util.subprocess, "run", raise_other)
    with pytest.raises(ValueError, match="bad codec"):
        util.check_ffmpeg_installed()


@pytest.mark.asyncio
async def test_audio_conversion_helpers_with_fakes(monkeypatch):
    from core.utils.cache import manager as cache_module

    cache = _Cache()
    monkeypatch.setattr(cache_module, "cache_manager", cache)
    monkeypatch.setattr(util, "AudioSegment", _AudioSegment)
    monkeypatch.setattr(util.opuslib_next, "Encoder", _Encoder)
    monkeypatch.setattr(util.opuslib_next, "Decoder", _Decoder)
    monkeypatch.setattr(util.opuslib_next, "APPLICATION_AUDIO", object())

    streamed = []
    util.audio_to_data_stream("sound.wav", is_opus=False, callback=streamed.append, sample_rate=1000)
    assert len(streamed) == 1
    assert len(streamed[0]) == 120

    pcm_streamed = []
    util.pcm_to_data_stream(b"\x01\x00", is_opus=False, callback=pcm_streamed.append, sample_rate=1000)
    assert len(pcm_streamed[0]) == 120

    opus_streamed = []
    util.pcm_to_data_stream(b"\x01\x00", is_opus=True, callback=opus_streamed.append, sample_rate=1000)
    assert opus_streamed == [b"opus\x01\x00"]

    class ExternalEncoder:
        def __init__(self):
            self.calls = []

        def encode_pcm_to_opus_stream(self, chunk, end_of_stream, callback):
            self.calls.append((chunk, end_of_stream))
            callback(b"external")

    external = ExternalEncoder()
    external_outputs = []
    util.pcm_to_data_stream(b"\x01\x00", is_opus=True, callback=external_outputs.append, sample_rate=1000, opus_encoder=external)
    assert external_outputs == [b"external"]
    assert external.calls[0][1] is True

    monkeypatch.setattr(util.p3, "decode_opus_from_bytes_stream", lambda data, callback: callback(b"p3"), raising=False)
    p3_outputs = []
    util.audio_bytes_to_data_stream(b"p3-bytes", "p3", True, p3_outputs.append)
    assert p3_outputs == [b"p3"]

    bytes_outputs = []
    util.audio_bytes_to_data_stream(b"wav-bytes", "wav", False, bytes_outputs.append, sample_rate=1000)
    assert len(bytes_outputs) == 1

    data = await util.audio_to_data("sound.wav", is_opus=True, use_cache=True)
    assert data == [b"opus\x01\x00"]
    assert cache.values[(cache_module.CacheType.AUDIO_DATA, "sound.wav:True")] == data
    assert await util.audio_to_data("sound.wav", is_opus=True, use_cache=True) == data
    pcm_data = await util.audio_to_data("sound.wav", is_opus=False, use_cache=False)
    assert len(pcm_data) == 1
    assert len(pcm_data[0]) == 1920

    wav_bytes = util.opus_datas_to_wav_bytes([b"opus"], sample_rate=1000, channels=1)
    assert wav_bytes.startswith(b"RIFF")


def test_config_update_and_vision_helpers(monkeypatch):
    before = {"selected_module": {"VAD": "vad_a", "ASR": "asr_a"}, "VAD": {"vad_a": {}}, "ASR": {"asr_a": {}}}
    assert util.check_vad_update(before, {}) is False
    assert util.check_vad_update(before, {"selected_module": {}}) is False
    assert util.check_vad_update(before, {"selected_module": {"VAD": "vad_b"}, "VAD": {"vad_b": {}}}) is True
    assert util.check_vad_update(before, {"selected_module": {"VAD": "vad_a"}, "VAD": {"vad_a": {}}}) is False
    assert util.check_vad_update(
        {"selected_module": {"VAD": "vad_a"}, "VAD": {"vad_a": {"type": "same"}}},
        {"selected_module": {"VAD": "vad_b"}, "VAD": {"vad_b": {"type": "same"}}},
    ) is False

    assert util.check_asr_update(before, {}) is False
    assert util.check_asr_update(before, {"selected_module": {}}) is False
    assert util.check_asr_update(before, {"selected_module": {"ASR": "asr_b"}, "ASR": {"asr_b": {}}}) is True
    assert util.check_asr_update(before, {"selected_module": {"ASR": "asr_a"}, "ASR": {"asr_a": {}}}) is False
    assert util.check_asr_update(
        {"selected_module": {"ASR": "asr_a"}, "ASR": {"asr_a": {"type": "same"}}},
        {"selected_module": {"ASR": "asr_a"}, "ASR": {"asr_a": {"type": "other"}}},
    ) is True

    filtered = util.filter_sensitive_info(
        {
            "api_key": "secret",
            "nested": {"token": "abc", "safe": 1},
            "items": [{"secret_key": "hidden"}, "plain"],
            "json": '{"access_token": "abc", "safe": 2}',
            "json_list": "[1, 2]",
            "bad_json": "{bad",
            "number": 3,
        }
    )
    assert filtered["api_key"] == "***"
    assert filtered["nested"]["token"] == "***"
    assert filtered["items"][0]["secret_key"] == "***"
    assert '"access_token": "***"' in filtered["json"]
    assert filtered["json_list"] == "[1, 2]"
    assert filtered["bad_json"] == "{bad"
    assert filtered["number"] == 3

    monkeypatch.setattr(util, "get_local_ip", lambda: "10.0.0.5")
    assert util.get_vision_url({"server": {"vision_explain": "Your URL", "http_port": "9000"}}) == "http://10.0.0.5:9000/mcp/vision/explain"
    assert util.get_vision_url({"server": {"vision_explain": "https://vision.test"}}) == "https://vision.test"
