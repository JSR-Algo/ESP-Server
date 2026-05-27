"""Tests for the server-side Speex DSP echo canceller (PR3)."""

import audioop
import math
import struct
import unittest

from core.voice.aec import AEC_AVAILABLE, AecProcessor


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


class AecBypassTest(unittest.TestCase):
    def test_disabled_processor_is_no_op(self):
        aec = AecProcessor(enabled=False)
        self.assertTrue(aec.bypassed)
        self.assertEqual(aec.reason, "disabled_by_config")
        payload = bytes(640)
        aec.push_reference(payload)
        self.assertEqual(aec.process_mic(payload), payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
