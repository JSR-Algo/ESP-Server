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
import threading

try:  # the wheel is only built when libspeexdsp-dev is present on the host
    from speexdsp import EchoCanceller_create as _create_speex_ec

    AEC_AVAILABLE = True
except Exception:  # pragma: no cover - environments without the dep
    _create_speex_ec = None
    AEC_AVAILABLE = False


log = logging.getLogger(__name__)


def release_aec_processor(owner, attr: str = "_aec_processor", logger=None) -> bool:
    """Detach and close the AEC processor held by ``owner``. Idempotent.

    A Google Live audio bridge builds one :class:`AecProcessor` per live
    session open and keeps it in ``_aec_processor``. Dropping the bridge does
    NOT free the native echo canceller (see :meth:`AecProcessor.close`), so
    whoever discards a bridge must call this. Returns True when a processor
    was closed.
    """
    if owner is None:
        return False
    processor = getattr(owner, attr, None)
    try:
        setattr(owner, attr, None)
    except Exception:  # pragma: no cover - read-only attribute on a test double
        pass
    if processor is None:
        return False
    close = getattr(processor, "close", None)
    if not callable(close):
        return False
    try:
        close()
    except Exception as exc:  # pragma: no cover - defensive
        # Pre-format: the caller's logger may be loguru (brace style), not stdlib.
        message = f"AEC release failed to close the processor: {exc!r}"
        target = logger if logger is not None else log
        try:
            target.warning(message)
        except Exception:
            pass
        return False
    return True


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
        # Guards ``self._ec`` against a close() racing an in-flight process_mic:
        # the Live pipeline runs process_mic on the bridge's audio executor
        # thread while close() is driven from the event loop.
        self._lock = threading.Lock()
        self._closed = False
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

    def close(self) -> None:
        """Release the native Speex echo canceller. Idempotent.

        speexdsp 0.1.1 ships a SWIG 2.0.11 binding in which ``EchoCanceller``
        is created through a *static factory* (``EchoCanceller_create``). SWIG
        only transfers ownership for a factory when the interface declares
        ``%newobject``; this one does not, so the returned proxy reports
        ``proxy.this.own() is False`` and the generated ``__swig_destroy__``
        (``delete_EchoCanceller``) is never invoked when the proxy is garbage
        collected. The native ``EchoCanceller`` — 91.5 kB of Speex echo state
        per instance at 16 kHz / 200 ms, measured in T19 run-13 — therefore
        lives for the lifetime of the process.

        One processor is built per Google Live session open, and lesson churn
        opens a session per prompt, so without this explicit destroy the
        server's RSS grows without bound (finding F9). Nothing else frees it:
        the Python side stays flat, which is exactly why the object counters
        in the T19 run-12 soak saw nothing.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.bypassed = True
            if self._reason is None:
                self._reason = "closed"
            ec, self._ec = self._ec, None
            self._ref_buffer = bytearray()
        if ec is None:
            return
        destroy = getattr(type(ec), "__swig_destroy__", None)
        if not callable(destroy):
            log.warning(
                "AEC close: speexdsp binding exposes no __swig_destroy__;"
                " the native echo canceller cannot be released."
            )
            return
        try:
            destroy(ec)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("AEC close failed to release the echo canceller: %s", exc)

    def __del__(self):  # pragma: no cover - interpreter-shutdown dependent
        """Best-effort release for any construction site that forgets close()."""
        try:
            self.close()
        except Exception:
            pass

    def push_reference(self, pcm_bytes: bytes) -> None:
        """Append a chunk of far-end (speaker) audio at the configured rate.

        The caller is responsible for resampling to the processor's sample
        rate before pushing. Excess buffered audio (e.g. when the model is
        ahead of the mic for a long pause) is dropped to keep memory bounded.
        """
        if self.bypassed or not pcm_bytes:
            return
        with self._lock:
            if self._closed:
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
        with self._lock:
            if self._closed or self._ec is None:
                # close() won the race with this chunk; pass audio through
                # rather than touching a destroyed native canceller.
                return pcm_bytes
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
        with self._lock:
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
