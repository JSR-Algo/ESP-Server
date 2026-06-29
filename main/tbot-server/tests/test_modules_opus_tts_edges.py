import importlib.util
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import core.utils as core_utils
from core.utils import opus_encoder_utils, tts


class _BoundLogger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def info(self, message):
        self.records.append(("info", message))

    def warning(self, message):
        self.records.append(("warning", message))

    def error(self, message):
        self.records.append(("error", message))


def _load_real_modules_initialize(monkeypatch):
    sys.modules.pop("core.handle.reportHandle", None)
    module_path = Path(__file__).resolve().parents[1] / "core" / "utils" / "modules_initialize.py"
    spec = importlib.util.spec_from_file_location("core.utils.modules_initialize", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setitem(sys.modules, "core.utils.modules_initialize", module)
    monkeypatch.setattr(core_utils, "modules_initialize", module, raising=False)
    spec.loader.exec_module(module)
    return module


def test_initialize_modules_builds_selected_factories_and_voiceprint_paths(monkeypatch):
    module = _load_real_modules_initialize(monkeypatch)
    logger = _BoundLogger()
    calls = []
    monkeypatch.setattr(module, "logger", logger)

    def fake_factory(label):
        def create_instance(*args):
            calls.append((label, args))
            return {"label": label, "args": args}

        return types.SimpleNamespace(create_instance=create_instance)

    monkeypatch.setattr(module, "tts", fake_factory("tts"))
    monkeypatch.setattr(module, "llm", fake_factory("llm"))
    monkeypatch.setattr(module, "intent", fake_factory("intent"))
    monkeypatch.setattr(module, "memory", fake_factory("memory"))
    monkeypatch.setattr(module, "vad", fake_factory("vad"))
    monkeypatch.setattr(module, "asr", fake_factory("asr"))
    config = {
        "selected_module": {
            "TTS": "edge",
            "LLM": "local_llm",
            "Intent": "intent_mod",
            "Memory": "memory_mod",
            "VAD": "vad_mod",
            "ASR": "asr_mod",
        },
        "TTS": {"edge": {"voice": "nova"}},
        "LLM": {"local_llm": {"temperature": 0}},
        "Intent": {"intent_mod": {"type": "rule_intent"}},
        "Memory": {"memory_mod": {"window": 3}},
        "VAD": {"vad_mod": {"type": "silero"}},
        "ASR": {"asr_mod": {"model": "small"}},
        "delete_audio": "no",
        "summaryMemory": {"enabled": True},
    }

    modules = module.initialize_modules(
        logger,
        config,
        init_vad=True,
        init_asr=True,
        init_llm=True,
        init_tts=True,
        init_memory=True,
        init_intent=True,
    )

    assert set(modules) == {"tts", "llm", "intent", "memory", "vad", "asr"}
    assert ("tts", ("edge", {"voice": "nova"}, False)) in calls
    assert ("llm", ("local_llm", {"temperature": 0})) in calls
    assert ("intent", ("rule_intent", {"type": "rule_intent"})) in calls
    assert ("memory", ("memory_mod", {"window": 3}, {"enabled": True})) in calls
    assert ("vad", ("silero", {"type": "silero"})) in calls
    assert ("asr", ("asr_mod", {"model": "small"}, False)) in calls

    assert module.initialize_modules(logger, config) == {}
    assert module.initialize_voiceprint(object(), {}) is False
    assert module.initialize_voiceprint(object(), {"voiceprint": {"url": "https://voice.test"}}) is False

    class VoiceprintASR:
        def __init__(self, should_fail=False):
            self.should_fail = should_fail
            self.config = None

        def init_voiceprint(self, voiceprint_config):
            if self.should_fail:
                raise RuntimeError("voiceprint down")
            self.config = voiceprint_config

    voiceprint_config = {"url": "https://voice.test", "speakers": ["a", "b"]}
    asr_instance = VoiceprintASR()
    assert module.initialize_voiceprint(asr_instance, {"voiceprint": voiceprint_config}) is True
    assert asr_instance.config is voiceprint_config
    assert module.initialize_voiceprint(VoiceprintASR(should_fail=True), {"voiceprint": voiceprint_config}) is False
    assert any(level == "error" and "voiceprint down" in msg for level, msg in logger.records)


def test_opus_encoder_streams_buffers_resets_and_handles_errors(monkeypatch, caplog):
    class FakeEncoder:
        def __init__(self, sample_rate, channels, application):
            self.sample_rate = sample_rate
            self.channels = channels
            self.application = application
            self.reset_called = False

        def encode(self, frame_bytes, frame_size):
            assert frame_size == 2
            return b"opus" + frame_bytes[:2]

        def reset_state(self):
            self.reset_called = True

    monkeypatch.setattr(opus_encoder_utils, "Encoder", FakeEncoder)
    encoder = opus_encoder_utils.OpusEncoderUtils(sample_rate=1000, channels=1, frame_size_ms=2)
    outputs = []

    encoder.encode_pcm_to_opus_stream(np.array([1], dtype=np.int16).tobytes(), False, outputs.append)
    assert outputs == []
    encoder.encode_pcm_to_opus_stream(np.array([2, 3], dtype=np.int16).tobytes(), True, outputs.append)
    assert outputs == [b"opus\x01\x00", b"opus\x03\x00"]
    assert len(encoder.buffer) == 0

    encoder.reset_state()
    assert encoder.encoder.reset_called is True
    assert len(encoder.buffer) == 0

    with caplog.at_level(logging.WARNING):
        encoder._validate_pcm_data(np.array([40000], dtype=np.int32))
    assert "Invalid PCM samples" in caplog.text

    encoder.encoder = None
    assert encoder._encode(np.array([1, 2], dtype=np.int16)) is None

    class BrokenEncoder(FakeEncoder):
        def encode(self, frame_bytes, frame_size):
            raise RuntimeError("encode failed")

    encoder.encoder = BrokenEncoder(1000, 1, object())
    assert encoder._encode(np.array([1, 2], dtype=np.int16)) is None

    encoder.encoder = FakeEncoder(1000, 1, object())
    encoder.close()
    assert encoder.encoder is None


def test_opus_encoder_init_and_close_error_paths(monkeypatch, caplog):
    class RaisingEncoder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("missing opus")

    monkeypatch.setattr(opus_encoder_utils, "Encoder", RaisingEncoder)
    with pytest.raises(RuntimeError, match="Initialization failed"):
        opus_encoder_utils.OpusEncoderUtils(16000, 1, 20)

    class CloseRaises(opus_encoder_utils.OpusEncoderUtils):
        def __delattr__(self, name):
            if name == "encoder":
                raise RuntimeError("cannot delete")
            super().__delattr__(name)

    instance = CloseRaises.__new__(CloseRaises)
    instance.encoder = object()
    with caplog.at_level(logging.ERROR):
        instance.close()
    assert "cannot delete" in caplog.text


def test_tts_create_instance_markdown_and_range_edges(monkeypatch):
    monkeypatch.setattr(tts.os.path, "exists", lambda path: False)
    with pytest.raises(ValueError, match="Unsupported TTS type"):
        tts.create_instance("missing")

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(tts.os.path, "exists", lambda path: True)
    monkeypatch.setattr(tts.importlib, "import_module", lambda name: types.SimpleNamespace(TTSProvider=FakeProvider))
    instance = tts.create_instance("edge", {"voice": "nova"}, delete_audio=False)
    assert isinstance(instance, FakeProvider)
    assert instance.args == ({"voice": "nova"},)
    assert instance.kwargs == {"delete_audio": False}

    table_text = "| Name | Age |\n| --- | --- |\n| An | 7 |\n| Extra | 8 | tail |\n"
    cleaned = tts.MarkdownCleaner.clean_markdown(
        "# Title\n"
        "**bold** and _italic_ [link](https://example.test) ![img](x)\n"
        f"{table_text}\n"
        "Formula $x+1$ and price $20$\n"
        "$$hidden$$\n"
        "- item🙂"
    )
    assert "Header is:Name, Age" in cleaned
    assert "Name = An" in cleaned
    assert "Extra" in cleaned
    assert "Formula x+1 and price $20$" in cleaned
    assert "hidden" not in cleaned
    assert "🙂" not in cleaned

    assert tts.MarkdownCleaner.clean_markdown("| Only | Row |\n") == "Single-line table: Only, Row"
    assert tts.MarkdownCleaner.clean_markdown("  tiếng Việt  ") == "tiếng Việt"
    assert tts.MarkdownCleaner._replace_table_block(types.SimpleNamespace(group=lambda name: "|\n")) == ""
    assert tts.convert_percentage_to_range(-50, 0, 10) == 2.5
    assert tts.convert_percentage_to_range(50, 0, 10, base_val=2) == 6.0
    assert tts.convert_percentage_to_range(200, 0, 10) == 10.0
