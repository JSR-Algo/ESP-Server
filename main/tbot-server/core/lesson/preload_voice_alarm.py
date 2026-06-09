"""S13 — automated voice-latency-during-preload auto-disable alarm (CP-8).

Plan §11.2 (reviewer P2): tie the lesson-runtime rollback to a *measured* signal,
not a human's ear. The alarm watches the **p95 voice round-trip latency measured
specifically during windows where a lesson asset preload is active**, and when that
p95 regresses past a threshold it **auto-disables ``LESSON_RUNTIME_ENABLED``** (the
ESP-side flag in ``config["lesson"]["runtime_enabled"]``, read by
``ConnectionHandler._lesson_runtime_enabled``). Flag-off stops the ESP from sending
``lesson_*``; the voice + 8-type paths are unaffected (plan §12.1).

Design constraints honored (ADDITIVE; never on the voice hot path):

* This object is a passive sampler. It is fed a single number per completed voice
  turn via :meth:`record_round_trip`; it does NOT poll, instrument, or wrap the
  Google-Live / classic voice pipeline. The one wiring hook the connection adds is
  exception-guarded so the alarm can never break a voice turn.
* It only counts samples that occurred while a preload was active
  (:meth:`set_preload_active`) — a regression that is NOT concurrent with a download
  is NOT attributable to the lesson layer and must NOT trip the rollback.
* Auto-disable is **latched** (one-shot): once tripped it flips the flag and stays
  tripped until explicitly reset, so a single regression cannot thrash the flag.
* Deterministic + injectable (``clock``) like ``asset_cache.AssetCache`` so the
  §10 pytest drives it with no wall clock and no real voice traffic.

This is wired to be live BEFORE any second / non-bench enablement (CP-8); the first
single-internal-device bench flip (S12) is covered by human-operator rollback only
(plan §11.2 "first-device timing").
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Callable, Deque, List, Optional

TAG = "LessonPreloadVoiceAlarm"

# Defaults (plan §11.2 leaves the numeric threshold to ops tuning, D-CAPS style —
# these are conservative stand-ins, overridable from config). A regression must be
# both large (p95 over the threshold) AND backed by enough concurrent-preload
# samples to be statistically meaningful before it trips the rollback.
_DEFAULT_REGRESSION_THRESHOLD_MS = 1500.0
_DEFAULT_MIN_SAMPLES = 5
_DEFAULT_WINDOW = 50


def _percentile(sorted_values: List[float], pct: float) -> float:
    """Nearest-rank p(pct) over an already-sorted, non-empty list.

    Nearest-rank (not interpolation) is intentional: with the small sample counts a
    single bench device produces, nearest-rank is the documented, reproducible rule
    and never invents a value between observed samples.
    """
    if not sorted_values:
        raise ValueError("percentile of empty sample set")
    n = len(sorted_values)
    # Textbook nearest-rank: rank = ceil(pct/100 * n), clamped to [1, n].
    rank = max(1, min(n, int(math.ceil((pct / 100.0) * n))))
    return sorted_values[rank - 1]


class PreloadVoiceLatencyAlarm:
    """Auto-disable guard for voice latency measured during active preloads.

    Typical wiring (additive, in the ``ws_server`` process):

        alarm = PreloadVoiceLatencyAlarm(
            disable_callback=conn._disable_lesson_runtime,  # flips the flag off
            threshold_ms=cfg.get("voice_rt_p95_disable_ms"),
        )
        # around the preload window (lesson runtime):
        alarm.set_preload_active(True)   # ... preload runs ...
        alarm.set_preload_active(False)
        # per completed voice turn (one guarded hook):
        alarm.record_round_trip(latency_ms)

    The voice path calls :meth:`record_round_trip` and nothing else; everything else
    is owned by the lesson layer.
    """

    def __init__(
        self,
        *,
        disable_callback: Optional[Callable[[], Any]] = None,
        threshold_ms: Optional[float] = None,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        window: int = _DEFAULT_WINDOW,
        clock: Optional[Callable[[], float]] = None,
        logger: Any = None,
    ) -> None:
        self._disable_callback = disable_callback
        self.threshold_ms = float(
            threshold_ms if threshold_ms is not None else _DEFAULT_REGRESSION_THRESHOLD_MS
        )
        self.min_samples = max(1, int(min_samples))
        self._now = clock or time.monotonic
        self._logger = logger

        # Only samples taken while a preload was active are retained — those are the
        # only ones attributable to the lesson layer. A bounded window keeps the p95
        # responsive (a long-past clean run never masks a current regression).
        self._during_preload: Deque[float] = deque(maxlen=max(1, int(window)))
        self._preload_active = False
        self._preload_depth = 0  # refcount: concurrent/overlapping preload windows
        self.tripped = False
        self.last_p95_ms: Optional[float] = None
        self.last_disabled_at: Optional[float] = None
        # Observability counters (plan §11.2 "download-pause events correlated with
        # voice-active windows"): totals for the rollout dashboard.
        self.samples_total = 0
        self.samples_during_preload = 0

    # ── preload window tracking ──────────────────────────────────────────────

    @property
    def preload_active(self) -> bool:
        return self._preload_depth > 0

    def set_preload_active(self, active: bool) -> None:
        """Mark the start/end of an active preload window.

        Refcounted so overlapping windows (a second assignment beginning before the
        first's optional assets finish) do not prematurely close the window. ``False``
        never drives the depth negative.
        """
        if active:
            self._preload_depth += 1
        else:
            self._preload_depth = max(0, self._preload_depth - 1)
        self._preload_active = self._preload_depth > 0

    # ── sampling ─────────────────────────────────────────────────────────────

    def record_round_trip(self, latency_ms: float) -> bool:
        """Record one completed voice round-trip (request -> first model audio).

        Returns True iff this sample caused the alarm to trip (auto-disable) now.
        Safe to call from the voice path: it never raises for a bad input and never
        blocks. Samples taken outside a preload window are counted for observability
        but do NOT feed the rollback p95.
        """
        self.samples_total += 1
        try:
            value = float(latency_ms)
        except (TypeError, ValueError):
            return False
        if value < 0:
            return False
        if not self.preload_active:
            return False
        self.samples_during_preload += 1
        self._during_preload.append(value)
        return self._evaluate()

    # ── evaluation + auto-disable ────────────────────────────────────────────

    def current_p95(self) -> Optional[float]:
        """p95 over the during-preload samples, or None until ``min_samples`` exist."""
        if len(self._during_preload) < self.min_samples:
            return None
        return _percentile(sorted(self._during_preload), 95.0)

    def _evaluate(self) -> bool:
        if self.tripped:
            return False  # latched: one-shot, never thrash the flag
        p95 = self.current_p95()
        self.last_p95_ms = p95
        if p95 is None:
            return False
        if p95 <= self.threshold_ms:
            return False
        return self._trip(p95)

    def _trip(self, p95: float) -> bool:
        self.tripped = True
        self.last_disabled_at = self._now()
        self._log(
            "error",
            "AUTO-DISABLE LESSON_RUNTIME_ENABLED: voice p95 during preload "
            f"{p95:.0f}ms > {self.threshold_ms:.0f}ms "
            f"(n={len(self._during_preload)} concurrent-preload samples)",
        )
        if self._disable_callback is not None:
            try:
                self._disable_callback()
            except Exception as exc:  # pragma: no cover - callback is best-effort
                self._log("error", f"auto-disable callback failed: {type(exc).__name__}")
        return True

    def reset(self) -> None:
        """Clear the latch + samples (e.g. after an ops re-enable). Counters persist."""
        self.tripped = False
        self.last_p95_ms = None
        self.last_disabled_at = None
        self._during_preload.clear()

    def snapshot(self) -> dict:
        """Observability projection for the rollout dashboard (plan §11.2)."""
        return {
            "tripped": self.tripped,
            "thresholdMs": self.threshold_ms,
            "p95DuringPreloadMs": self.last_p95_ms,
            "samplesTotal": self.samples_total,
            "samplesDuringPreload": self.samples_during_preload,
            "preloadActive": self.preload_active,
            "lastDisabledAt": self.last_disabled_at,
        }

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger.bind(tag=TAG), level)(message)
        except Exception:
            pass
