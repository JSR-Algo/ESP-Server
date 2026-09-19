"""Tests for the server-side Speex DSP echo canceller (PR3)."""

import audioop
import math
import struct
import unittest

from core.voice.aec import AEC_AVAILABLE, AecProcessor, release_aec_processor


def _sine(sample_rate, duration_sec, frequency=440.0, amplitude=0.6):
    sample_count = int(sample_rate * duration_sec)
    buf = bytearray()
    for i in range(sample_count):
        s = int(amplitude * 32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        buf.extend(struct.pack("<h", s))
    return bytes(buf)


def _delayed_echo(reference_bytes, sample_rate, delay_ms=50, gain=0.8, noise_amp=100):
    delay_samples = int(sample_rate * delay_ms / 1000)
    total_samples = len(reference_bytes) // 2
    out = bytearray(2 * total_samples)
    for i in range(total_samples):
        sample = 0
        if i >= delay_samples:
            ref = struct.unpack_from("<h", reference_bytes, (i - delay_samples) * 2)[0]
            sample = int(gain * ref)
        sample += (i * 131) % (2 * noise_amp) - noise_amp
        sample = max(-32768, min(32767, sample))
        struct.pack_into("<h", out, i * 2, sample)
    return bytes(out)


def _user_voice(sample_rate, duration_sec, frequency=200.0, amplitude=0.3):
    return _sine(sample_rate, duration_sec, frequency, amplitude)


def _mix(a, b):
    out = bytearray(len(a))
    for i in range(0, len(a), 2):
        x = struct.unpack_from("<h", a, i)[0]
        y = struct.unpack_from("<h", b, i)[0]
        struct.pack_into("<h", out, i, max(-32768, min(32767, x + y)))
    return bytes(out)


@unittest.skipUnless(AEC_AVAILABLE, "speexdsp not installed in this environment")
class AecEchoReductionTest(unittest.TestCase):
    SAMPLE_RATE = 16000
    CHUNK_BYTES = 1920  # 60 ms @ 16 kHz mono int16, matches Live pipeline

    def _process(self, far, near, frame_ms=10, filter_ms=200):
        aec = AecProcessor(
            sample_rate=self.SAMPLE_RATE,
            frame_ms=frame_ms,
            filter_ms=filter_ms,
            enabled=True,
        )
        self.assertFalse(aec.bypassed, msg=f"AEC unexpectedly bypassed: {aec.reason}")
        out = bytearray()
        for i in range(0, len(near), self.CHUNK_BYTES):
            aec.push_reference(far[i : i + self.CHUNK_BYTES])
            out.extend(aec.process_mic(near[i : i + self.CHUNK_BYTES]))
        return bytes(out)

    def test_reduces_echo_in_steady_state(self):
        far = _sine(self.SAMPLE_RATE, 0.5)
        near = _delayed_echo(far, self.SAMPLE_RATE)
        pre = audioop.rms(near, 2)
        post = audioop.rms(self._process(far, near), 2)
        reduction_db = 20 * math.log10(max(1, pre) / max(1, post))
        self.assertGreaterEqual(
            reduction_db,
            12.0,
            f"AEC reduction {reduction_db:.1f} dB below 12 dB target "
            f"(pre={pre}, post={post})",
        )

    def test_preserves_user_voice_when_no_echo_present(self):
        far_silent = bytes(self.SAMPLE_RATE * 2)  # 1s silence
        user = _user_voice(self.SAMPLE_RATE, 1.0)
        pre = audioop.rms(user, 2)
        post = audioop.rms(self._process(far_silent, user), 2)
        # Without echo to cancel, output should retain most of the energy.
        attenuation_db = 20 * math.log10(max(1, pre) / max(1, post))
        self.assertLessEqual(
            attenuation_db,
            6.0,
            f"AEC attenuated clean user voice by {attenuation_db:.1f} dB",
        )

    def test_handles_partial_trailing_frame_without_error(self):
        far = _sine(self.SAMPLE_RATE, 0.05)
        # 25 ms near-end audio = 800 bytes which is not a multiple of 320 (10ms)
        near = _sine(self.SAMPLE_RATE, 0.025)
        aec = AecProcessor(sample_rate=self.SAMPLE_RATE)
        aec.push_reference(far)
        out = aec.process_mic(near)
        self.assertEqual(len(out), len(near))


    def test_reference_buffer_bounds_reset_and_missing_reference_paths(self):
        aec = AecProcessor(sample_rate=self.SAMPLE_RATE, reference_buffer_sec=0.01)
        self.assertFalse(aec.bypassed, msg=f"AEC unexpectedly bypassed: {aec.reason}")

        overflow = bytes(aec._max_ref_bytes + aec._frame_bytes)
        aec.push_reference(overflow)
        self.assertEqual(len(aec._ref_buffer), aec._max_ref_bytes)

        aec.reset()
        self.assertEqual(aec._ref_buffer, bytearray())
        self.assertEqual(aec.process_mic(b"odd"), b"odd")

        out = aec.process_mic(bytes(aec._frame_bytes))
        self.assertEqual(len(out), aec._frame_bytes)


class AecBypassTest(unittest.TestCase):
    def test_disabled_processor_is_no_op(self):
        aec = AecProcessor(enabled=False)
        self.assertTrue(aec.bypassed)
        self.assertEqual(aec.reason, "disabled_by_config")
        payload = bytes(640)
        aec.push_reference(payload)
        self.assertEqual(aec.process_mic(payload), payload)


    def test_library_unavailable_bypasses_with_reason(self):
        import core.voice.aec.aec_processor as aec_module

        original_available = aec_module.AEC_AVAILABLE
        try:
            aec_module.AEC_AVAILABLE = False
            aec = AecProcessor(enabled=True)
        finally:
            aec_module.AEC_AVAILABLE = original_available

        self.assertTrue(aec.bypassed)
        self.assertEqual(aec.reason, "library_unavailable")


def _rss_kb():
    """Resident set size of this process, or None when /proc is unavailable."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:  # pragma: no cover - non-Linux
        return None
    return None  # pragma: no cover


class _FakeSwigEchoCanceller:
    """Stand-in for the speexdsp SWIG proxy, with the same destroy contract."""

    destroyed = []

    def __swig_destroy__(self):  # noqa: N807 - mirrors SWIG's generated name
        _FakeSwigEchoCanceller.destroyed.append(self)

    def process(self, near, far):  # pragma: no cover - not exercised here
        return near


class AecCloseContractTest(unittest.TestCase):
    """close() must release the native canceller exactly once (F9).

    These run everywhere: they use a fake proxy, so they do not need speexdsp.
    """

    def _processor_with_fake_ec(self):
        # Build bypassed (so no native canceller is allocated), then present it
        # as an active processor holding a fake proxy with the SWIG contract.
        aec = AecProcessor(enabled=False)
        aec._ec = _FakeSwigEchoCanceller()
        aec.bypassed = False
        aec._reason = None
        aec._closed = False
        return aec

    def setUp(self):
        _FakeSwigEchoCanceller.destroyed = []

    def test_close_destroys_the_native_canceller_once(self):
        aec = self._processor_with_fake_ec()
        ec = aec._ec
        aec.close()
        self.assertEqual(_FakeSwigEchoCanceller.destroyed, [ec])
        aec.close()
        aec.close()
        self.assertEqual(
            _FakeSwigEchoCanceller.destroyed,
            [ec],
            msg="close() must be idempotent - a double destroy is a double free",
        )
        self.assertIsNone(aec._ec)

    def test_process_and_push_are_inert_after_close(self):
        aec = self._processor_with_fake_ec()
        aec.close()
        payload = b"\x01\x02" * 160
        self.assertEqual(aec.process_mic(payload), payload)
        aec.push_reference(payload)
        self.assertTrue(aec.bypassed)
        self.assertEqual(aec.reason, "closed")

    def test_close_survives_a_binding_without_a_destructor(self):
        aec = AecProcessor(enabled=False)
        aec._ec = object()
        aec.bypassed = False
        aec.close()  # must not raise
        self.assertIsNone(aec._ec)


class ReleaseAecProcessorTest(unittest.TestCase):
    """T19 F9: whoever discards a Google Live audio bridge must release its
    echo canceller, because dropping the reference frees nothing."""

    class _Owner:
        def __init__(self, processor):
            self._aec_processor = processor

    class _Closable:
        def __init__(self, raises=False):
            self.closes = 0
            self.raises = raises

        def close(self):
            self.closes += 1
            if self.raises:
                raise RuntimeError("destroy failed")

    def test_release_closes_and_detaches(self):
        processor = self._Closable()
        owner = self._Owner(processor)

        self.assertTrue(release_aec_processor(owner))
        self.assertEqual(processor.closes, 1)
        self.assertIsNone(owner._aec_processor)

        self.assertFalse(
            release_aec_processor(owner),
            msg="a second release must not destroy the canceller twice",
        )
        self.assertEqual(processor.closes, 1)

    def test_release_tolerates_failures_and_missing_owners(self):
        self.assertFalse(release_aec_processor(None))
        self.assertFalse(release_aec_processor(self._Owner(None)))
        self.assertFalse(release_aec_processor(self._Owner(object())))

        failing = self._Closable(raises=True)
        owner = self._Owner(failing)
        self.assertFalse(release_aec_processor(owner))  # must not propagate
        self.assertIsNone(owner._aec_processor)

    def test_release_closes_a_real_processor(self):
        aec = AecProcessor(enabled=False)
        owner = self._Owner(aec)
        self.assertTrue(release_aec_processor(owner))
        self.assertTrue(aec._closed)


@unittest.skipUnless(AEC_AVAILABLE, "speexdsp not installed in this environment")
class AecNativeMemoryBoundedTest(unittest.TestCase):
    """T19 F9 regression: constructing echo cancellers must not grow RSS forever.

    T19 run-12 measured +98 MB per ESP server over a 65.6 min lesson soak with a
    flat Python object count. T19 run-13 attributed it to this allocation site:
    the speexdsp 0.1.1 SWIG binding creates EchoCanceller through a static
    factory that does NOT transfer ownership, so the native object (91.5 kB at
    16 kHz / 200 ms) is never freed when the proxy is collected.
    """

    SAMPLE_RATE = 16000
    FILTER_MS = 200
    ITERATIONS = 400
    #: A leaked canceller costs ~91.5 kB; allow generous headroom for allocator
    #: behaviour and interpreter noise but stay an order of magnitude below it.
    MAX_KB_PER_ITERATION = 8.0

    def test_swig_proxy_does_not_own_the_native_canceller(self):
        """Documents why close() exists. If this ever fails, speexdsp gained
        %newobject and the explicit destroy became redundant (but still safe)."""
        aec = AecProcessor(
            sample_rate=self.SAMPLE_RATE, filter_ms=self.FILTER_MS, enabled=True
        )
        try:
            self.assertFalse(aec.bypassed, msg=f"AEC bypassed: {aec.reason}")
            self.assertFalse(
                aec._ec.this.own(),
                msg="speexdsp now transfers ownership; revisit AecProcessor.close",
            )
        finally:
            aec.close()

    def test_construct_and_close_keeps_rss_bounded(self):
        import gc

        baseline = _rss_kb()
        if baseline is None:  # pragma: no cover - non-Linux
            self.skipTest("/proc/self/status unavailable")

        # Warm the allocator and the import path out of the measurement.
        for _ in range(10):
            AecProcessor(
                sample_rate=self.SAMPLE_RATE, filter_ms=self.FILTER_MS, enabled=True
            ).close()
        gc.collect()
        before = _rss_kb()

        for _ in range(self.ITERATIONS):
            aec = AecProcessor(
                sample_rate=self.SAMPLE_RATE, filter_ms=self.FILTER_MS, enabled=True
            )
            self.assertFalse(aec.bypassed, msg=f"AEC bypassed: {aec.reason}")
            aec.close()
        gc.collect()
        after = _rss_kb()

        per_iteration = (after - before) / self.ITERATIONS
        self.assertLess(
            per_iteration,
            self.MAX_KB_PER_ITERATION,
            msg=(
                f"RSS grew {per_iteration:.1f} kB per AecProcessor over "
                f"{self.ITERATIONS} construct/close cycles "
                f"({before} kB -> {after} kB); the native echo canceller is "
                "not being released (T19 F9)"
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
