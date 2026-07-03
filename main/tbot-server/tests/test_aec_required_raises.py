"""CR-RUN-14: AEC must HARD-FAIL (raise), not silently degrade, when required.

Pins the raise-vs-degrade contract that the lesson runtime rides on:

  * ``AecProcessor`` silently sets ``bypassed=True`` (reason
    ``library_unavailable``) when the native ``libspeexdsp``/``speexdsp``
    wheel is missing from the server image. This is the *degrade* half.
  * ``GoogleLiveProvider._ensure_required_aec_ready`` RAISES ``RuntimeError``
    when ``aec_enabled`` is true but the bridge's processor is bypassed. This
    is the *hard-fail* half — a server image lacking the lib must fail Live
    startup loudly instead of running echo-cancellation-less voice.

The native lib is usually present in this dev venv (``AEC_AVAILABLE`` True), so
we faithfully simulate the production image lacking it by flipping
``aec_processor.AEC_AVAILABLE`` to False for the duration of construction —
the exact technique used by ``tests/test_aec_processor.py`` — which drives the
REAL ``AecProcessor.__init__`` ``library_unavailable`` branch. No behavior is
mocked away: the real processor is fed through the real provider method.
"""

import unittest
from types import SimpleNamespace

import core.voice.aec.aec_processor as aec_module
from core.voice.aec import AecProcessor
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def bind(self, **_kwargs):
        return self

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Conn:
    """Minimal connection fake; mirrors tests/test_google_live_provider_edges.py."""

    def __init__(self, aec_enabled=True):
        self.config = {
            "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": True},
            "google_live": {
                "api_key": "test-key",
                "model": "gemini-test",
                "aec_enabled": aec_enabled,
                "reconnect": {"enabled": False},
            },
            "wakeup_words": ["TeeBot"],
            "prompt": "system prompt",
        }
        self.logger = _Logger()
        self.session_id = "session-1"
        self.device_id = "device-1"
        self.household_id = "home-1"


def _build_real_aec_processor(*, lib_available):
    """Construct a REAL AecProcessor simulating lib present/absent.

    When ``lib_available`` is False we flip the module-level availability flag
    so ``AecProcessor.__init__`` takes its real ``library_unavailable`` branch
    (server image missing the native speexdsp wheel). The flag is always
    restored.
    """
    original = aec_module.AEC_AVAILABLE
    try:
        aec_module.AEC_AVAILABLE = bool(lib_available)
        return AecProcessor(enabled=True)
    finally:
        aec_module.AEC_AVAILABLE = original


def _make_provider(conn):
    return GoogleLiveProvider(
        conn,
        client_factory=lambda *_a: SimpleNamespace(),
        classic_provider_factory=lambda _c: SimpleNamespace(),
    )


class AecRequiredRaisesTest(unittest.TestCase):
    def test_missing_native_lib_makes_real_processor_bypass_with_reason(self):
        """Degrade half: real AecProcessor goes bypassed when speexdsp absent."""
        processor = _build_real_aec_processor(lib_available=False)
        self.assertTrue(
            processor.bypassed,
            "AecProcessor must self-bypass when the native lib is missing",
        )
        self.assertEqual(processor.reason, "library_unavailable")

    def test_required_aec_raises_when_processor_bypassed_by_missing_lib(self):
        """Hard-fail half: required AEC + bypassed processor -> RuntimeError.

        This is the path the lesson runtime rides on at Live boot: a server
        image lacking libspeexdsp must hard-fail rather than degrade silently.
        """
        conn = _Conn(aec_enabled=True)
        provider = _make_provider(conn)
        # Real processor that went bypassed because the native lib is "missing".
        provider._bridge = SimpleNamespace(
            _aec_processor=_build_real_aec_processor(lib_available=False)
        )

        with self.assertRaises(RuntimeError) as ctx:
            provider._ensure_required_aec_ready()
        # The raised error must surface the real bypass reason, not a generic
        # placeholder — proves it read the actual processor state.
        self.assertIn("library_unavailable", str(ctx.exception))

    def test_required_aec_raises_when_processor_is_missing_entirely(self):
        """No processor at all (e.g. build returned None) also hard-fails."""
        conn = _Conn(aec_enabled=True)
        provider = _make_provider(conn)
        provider._bridge = SimpleNamespace(_aec_processor=None)

        with self.assertRaises(RuntimeError) as ctx:
            provider._ensure_required_aec_ready()
        self.assertIn("processor_unavailable", str(ctx.exception))

    def test_required_aec_does_not_raise_when_lib_present(self):
        """Positive control: lib present -> active processor -> no raise.

        Without this, the test could pass simply because the method always
        raises. The real AecProcessor (lib available) must NOT be bypassed and
        the guard must return cleanly under aec_enabled:true.
        """
        if not aec_module.AEC_AVAILABLE:
            self.skipTest("speexdsp not installed; cannot exercise the active path")

        active = _build_real_aec_processor(lib_available=True)
        self.assertFalse(
            active.bypassed,
            f"expected active AEC but got bypassed: {active.reason}",
        )

        conn = _Conn(aec_enabled=True)
        provider = _make_provider(conn)
        provider._bridge = SimpleNamespace(_aec_processor=active)

        # Must not raise — required AEC is satisfied.
        self.assertIsNone(provider._ensure_required_aec_ready())

    def test_no_raise_when_aec_not_required_even_if_bypassed(self):
        """aec_enabled:false -> degrade is allowed; bypassed processor is fine."""
        conn = _Conn(aec_enabled=False)
        provider = _make_provider(conn)
        provider._bridge = SimpleNamespace(
            _aec_processor=_build_real_aec_processor(lib_available=False)
        )

        # Guard short-circuits before inspecting the processor; no raise.
        self.assertIsNone(provider._ensure_required_aec_ready())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
