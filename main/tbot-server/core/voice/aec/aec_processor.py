"""Speex-DSP-based acoustic echo canceller.

Speex DSP exposes a SWIG-wrapped ``EchoCanceller`` that processes mono 16-bit
PCM at a fixed frame size. We use 10 ms frames at 16 kHz (160 samples,
320 bytes) because that is the canonical Speex frame and matches the input
sample rate the Google Live pipeline already produces after resampling.

Lifecycle (per ConnectionHandler):
  push_reference(...)   far-end signal (what the server is sending to the
                        device speaker, time-aligned approximately by the
                        rate at which we feed it relative to mic frames)
  process_mic(...)      near-end signal from the device mic; returns the
                        echo-cancelled mono PCM ready to forward upstream

If the Speex DSP library is not installed (build wheel failed for the
running arch, etc.), this module degrades to a no-op pass-through and
logs a single warning. Production behaviour then matches the pre-AEC
state — half-duplex / barge-in disabled — rather than crashing the
voice provider.
"""

from __future__ import annotations

import logging

try:  # the wheel is only built when libspeexdsp-dev is present on the host
    from speexdsp import EchoCanceller_create as _create_speex_ec

    AEC_AVAILABLE = True
except Exception:  # pragma: no cover - environments without the dep
    _create_speex_ec = None
    AEC_AVAILABLE = False


log = logging.getLogger(__name__)


class AecProcessor:
    """Time-aligned far-end / near-end echo cancellation for one session."""

    #: 10 ms frame at the configured sample rate (the Speex AEC works on a
    #: fixed sub-frame; we slice each 60 ms mic chunk into six of these).
    DEFAULT_FRAME_MS = 10
    #: 200 ms echo tail covers typical small-speaker setups including the
    #: TBOT robot whose ES8311 + acoustic enclosure produces a short tail.
    DEFAULT_FILTER_MS = 200
    #: Cap the reference FIFO so an idle conversation does not grow without
    #: bound — 4 s of 16-bit mono 16 kHz audio is 128 kB.
    DEFAULT_REFERENCE_BUFFER_SEC = 4.0

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = DEFAULT_FRAME_MS,
        filter_ms: int = DEFAULT_FILTER_MS,
        reference_buffer_sec: float = DEFAULT_REFERENCE_BUFFER_SEC,
        enabled: bool = True,
    ):
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.filter_ms = int(filter_ms)
        self._frame_samples = self.sample_rate * self.frame_ms // 1000
        self._frame_bytes = self._frame_samples * 2  # int16 mono
        self._filter_samples = self.sample_rate * self.filter_ms // 1000
        self._max_ref_bytes = int(self.sample_rate * reference_buffer_sec) * 2
        self._silence_frame = bytes(self._frame_bytes)
        self._ref_buffer = bytearray()
        self.bypassed = not enabled or not AEC_AVAILABLE
        self._reason = None
        if not enabled:
            self._reason = "disabled_by_config"
        elif not AEC_AVAILABLE:
            self._reason = "library_unavailable"
            log.warning(
                "AEC library unavailable; bypassing AEC stage. Install"
                " libspeexdsp-dev + pip install speexdsp to enable."
            )
        try:
            if not self.bypassed:
                self._ec = _create_speex_ec(
                    self._frame_samples,
                    self._filter_samples,
                    self.sample_rate,
                )
            else:
                self._ec = None
        except Exception as exc:  # pragma: no cover - defensive
            self.bypassed = True
            self._reason = f"init_failed: {exc!r}"
            self._ec = None
            log.warning("AEC init failed, bypassing: %s", exc)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def reason(self) -> str | None:
        """Reason the processor is in bypass mode, or None when active."""
        return self._reason

    def push_reference(self, pcm_bytes: bytes) -> None:
        """Append a chunk of far-end (speaker) audio at the configured rate.

        The caller is responsible for resampling to the processor's sample
        rate before pushing. Excess buffered audio (e.g. when the model is
        ahead of the mic for a long pause) is dropped to keep memory bounded.
        """
        if self.bypassed or not pcm_bytes:
            return
        self._ref_buffer.extend(pcm_bytes)
        if len(self._ref_buffer) > self._max_ref_bytes:
            # Drop oldest to retain a fresh tail; this can introduce a delay
            # mismatch but only when the speaker has been running far ahead
            # of the mic — typically right after a long model utterance.
            self._ref_buffer = self._ref_buffer[-self._max_ref_bytes :]

    def process_mic(self, pcm_bytes: bytes) -> bytes:
        """Return echo-cancelled near-end audio.

        Input must be int16 mono PCM at ``self.sample_rate``. The input
        length should be a multiple of the 10 ms frame size; any trailing
        partial frame is dropped (consistent with how the Live pipeline
        slices audio).
        """
        if self.bypassed or not pcm_bytes:
            return pcm_bytes
        if len(pcm_bytes) % 2:
            # The caller should have already validated this. Bail out so we
            # do not feed garbage into Speex which assumes int16-aligned data.
            return pcm_bytes
        out = bytearray()
        for offset in range(0, len(pcm_bytes), self._frame_bytes):
            near = pcm_bytes[offset : offset + self._frame_bytes]
            if len(near) < self._frame_bytes:
                # Partial frame at end — pass through unchanged so we do not
                # silently truncate audio. The upstream resampler usually
                # emits exact multiples but stitching across reconnects can
                # cause this.
                out.extend(near)
                break
            far = self._pop_reference_frame()
            cleaned = self._ec.process(bytes(near), far)
            out.extend(cleaned)
        return bytes(out)

    def reset(self) -> None:
        """Drop buffered reference audio (e.g. on session re-init)."""
        self._ref_buffer.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pop_reference_frame(self) -> bytes:
        if len(self._ref_buffer) >= self._frame_bytes:
            frame = bytes(self._ref_buffer[: self._frame_bytes])
            del self._ref_buffer[: self._frame_bytes]
            return frame
        # No reference yet (model is silent) — feed Speex zeros so it still
        # tracks but does not subtract anything.
        return self._silence_frame
