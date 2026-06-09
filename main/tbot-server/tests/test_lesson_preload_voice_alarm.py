"""S13 / CP-8 — voice-latency-during-preload auto-disable alarm.

Proves the reviewer-P2 guardrail (plan §11.2): a voice round-trip p95 measured
DURING an active lesson preload, regressing past a threshold, auto-disables
``LESSON_RUNTIME_ENABLED`` — and that a regression OUTSIDE a preload window (not
attributable to the lesson layer) does NOT trip the rollback. Pure-module test:
no socket, no wall clock, no voice traffic.
"""

import unittest

from core.lesson.preload_voice_alarm import (
    PreloadVoiceLatencyAlarm,
    _percentile,
)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _alarm(**kw):
    calls = {"disabled": 0}

    def disable():
        calls["disabled"] += 1

    kw.setdefault("disable_callback", disable)
    kw.setdefault("threshold_ms", 1500.0)
    kw.setdefault("min_samples", 5)
    kw.setdefault("clock", _Clock())
    a = PreloadVoiceLatencyAlarm(**kw)
    return a, calls


class PercentileTest(unittest.TestCase):
    def test_nearest_rank_p95(self):
        # 20 values 1..20; nearest-rank p95 = rank ceil(0.95*20)=19 -> value 19.
        self.assertEqual(_percentile(list(range(1, 21)), 95.0), 19)

    def test_single_value(self):
        self.assertEqual(_percentile([7.0], 95.0), 7.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _percentile([], 95.0)


class PreloadVoiceLatencyAlarmTest(unittest.TestCase):
    # ── happy: regression DURING preload trips the auto-disable ────────────────
    def test_regression_during_preload_trips_and_disables(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=5)
        a.set_preload_active(True)
        tripped = False
        for ms in (1600, 1700, 1800, 1900, 2000):  # all > threshold, p95 high
            tripped = a.record_round_trip(ms) or tripped
        self.assertTrue(a.tripped)
        self.assertTrue(tripped)
        self.assertEqual(calls["disabled"], 1)
        self.assertIsNotNone(a.last_p95_ms)
        self.assertGreater(a.last_p95_ms, 1500.0)
        self.assertEqual(a.last_disabled_at, 1000.0)  # injected clock

    # ── negative: a regression OUTSIDE a preload window must NOT trip ──────────
    def test_regression_without_active_preload_does_not_trip(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=5)
        # No preload active: even a brutal regression is not attributable to lessons.
        for ms in (5000, 6000, 7000, 8000, 9000):
            self.assertFalse(a.record_round_trip(ms))
        self.assertFalse(a.tripped)
        self.assertEqual(calls["disabled"], 0)
        self.assertIsNone(a.current_p95())  # zero during-preload samples
        self.assertEqual(a.samples_total, 5)
        self.assertEqual(a.samples_during_preload, 0)

    # ── healthy voice during preload stays enabled ────────────────────────────
    def test_healthy_latency_during_preload_does_not_trip(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=5)
        a.set_preload_active(True)
        for ms in (200, 250, 300, 280, 240, 260, 290):
            self.assertFalse(a.record_round_trip(ms))
        self.assertFalse(a.tripped)
        self.assertEqual(calls["disabled"], 0)
        self.assertLessEqual(a.current_p95(), 1500.0)

    # ── min-samples gate: too few samples never trips ─────────────────────────
    def test_below_min_samples_never_trips(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=5)
        a.set_preload_active(True)
        for ms in (9000, 9000, 9000, 9000):  # 4 < min_samples
            self.assertFalse(a.record_round_trip(ms))
        self.assertFalse(a.tripped)
        self.assertIsNone(a.current_p95())
        self.assertEqual(calls["disabled"], 0)
        # The 5th concurrent sample crosses the gate and trips.
        self.assertTrue(a.record_round_trip(9000))
        self.assertTrue(a.tripped)
        self.assertEqual(calls["disabled"], 1)

    # ── latched: one-shot, never thrashes the flag ────────────────────────────
    def test_latched_one_shot(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=5)
        a.set_preload_active(True)
        for ms in (2000, 2000, 2000, 2000, 2000):
            a.record_round_trip(ms)
        self.assertEqual(calls["disabled"], 1)
        # Further regressions do NOT re-fire the callback.
        for ms in (3000, 4000, 5000):
            self.assertFalse(a.record_round_trip(ms))
        self.assertEqual(calls["disabled"], 1)

    # ── refcounted preload window (overlapping assignments) ───────────────────
    def test_preload_window_refcount(self):
        a, _ = _alarm()
        self.assertFalse(a.preload_active)
        a.set_preload_active(True)
        a.set_preload_active(True)  # overlapping window
        self.assertTrue(a.preload_active)
        a.set_preload_active(False)
        self.assertTrue(a.preload_active)  # still one open window
        a.set_preload_active(False)
        self.assertFalse(a.preload_active)
        a.set_preload_active(False)  # never goes negative
        self.assertFalse(a.preload_active)

    # ── only during-preload samples feed the p95 (mixed stream) ───────────────
    def test_only_during_preload_samples_feed_p95(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=3)
        # Outside the window: huge but ignored for rollback.
        for ms in (9000, 9000):
            a.record_round_trip(ms)
        # Inside the window: healthy.
        a.set_preload_active(True)
        for ms in (300, 320, 310):
            a.record_round_trip(ms)
        a.set_preload_active(False)
        self.assertFalse(a.tripped)
        self.assertEqual(calls["disabled"], 0)
        self.assertEqual(a.samples_total, 5)
        self.assertEqual(a.samples_during_preload, 3)

    # ── bad inputs are swallowed (voice path safety) ──────────────────────────
    def test_bad_inputs_safe(self):
        a, _ = _alarm()
        a.set_preload_active(True)
        self.assertFalse(a.record_round_trip("nope"))   # non-numeric
        self.assertFalse(a.record_round_trip(None))     # None
        self.assertFalse(a.record_round_trip(-5))       # negative ignored
        self.assertEqual(a.samples_during_preload, 0)

    # ── disable callback raising never propagates ─────────────────────────────
    def test_disable_callback_exception_is_swallowed(self):
        def boom():
            raise RuntimeError("flag store down")

        a = PreloadVoiceLatencyAlarm(
            disable_callback=boom, threshold_ms=1000.0, min_samples=3
        )
        a.set_preload_active(True)
        # Should trip (latch set) and NOT raise despite the callback blowing up.
        a.record_round_trip(2000)
        a.record_round_trip(2000)
        self.assertTrue(a.record_round_trip(2000))
        self.assertTrue(a.tripped)

    # ── reset clears the latch + samples but keeps counters ───────────────────
    def test_reset(self):
        a, calls = _alarm(threshold_ms=1500.0, min_samples=3)
        a.set_preload_active(True)
        for ms in (2000, 2000, 2000):
            a.record_round_trip(ms)
        self.assertTrue(a.tripped)
        a.reset()
        self.assertFalse(a.tripped)
        self.assertIsNone(a.last_p95_ms)
        self.assertIsNone(a.current_p95())
        # Counters persist across reset (cumulative observability).
        self.assertEqual(a.samples_during_preload, 3)

    # ── snapshot projection shape (dashboard) ─────────────────────────────────
    def test_snapshot_shape(self):
        a, _ = _alarm(threshold_ms=1234.0)
        snap = a.snapshot()
        for key in (
            "tripped",
            "thresholdMs",
            "p95DuringPreloadMs",
            "samplesTotal",
            "samplesDuringPreload",
            "preloadActive",
            "lastDisabledAt",
        ):
            self.assertIn(key, snap)
        self.assertEqual(snap["thresholdMs"], 1234.0)
        self.assertFalse(snap["tripped"])

    # ── default threshold when none configured ────────────────────────────────
    def test_default_threshold(self):
        a = PreloadVoiceLatencyAlarm()  # no threshold_ms
        self.assertEqual(a.threshold_ms, 1500.0)


if __name__ == "__main__":
    unittest.main()
