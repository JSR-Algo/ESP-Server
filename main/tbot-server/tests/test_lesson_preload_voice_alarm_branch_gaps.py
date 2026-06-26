"""Close the lone remaining *branch* gap in ``core.lesson.preload_voice_alarm``.

``--cov-branch`` flags ``184->189`` in ``PreloadVoiceLatencyAlarm._trip``: the
``if self._disable_callback is not None:`` guard taking its False edge (no callback
registered) and skipping straight to ``return True``. Every existing trip test
supplies a ``disable_callback``, so the no-callback edge is never taken. The alarm
can legitimately run with ``disable_callback=None`` (it defaults to None and is
``Optional``) -- e.g. a metrics-only deployment that latches the regression but lets
ops flip the flag manually.

Mirrors ``test_lesson_preload_voice_alarm.py``: drives the REAL alarm via
``record_round_trip`` with an injected clock, asserting on real latched state.
"""

import unittest

from core.lesson.preload_voice_alarm import PreloadVoiceLatencyAlarm


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TripWithoutDisableCallbackTest(unittest.TestCase):
    def test_trip_without_disable_callback_still_latches(self):
        """A regression DURING preload with disable_callback=None -> _trip latches
        (tripped=True, last_disabled_at set) but the ``if self._disable_callback is
        not None`` block is skipped (branch 184->189). Mutation guard: if the source
        called the callback unconditionally, this would raise TypeError ('NoneType'
        not callable) instead of returning True cleanly."""
        a = PreloadVoiceLatencyAlarm(
            disable_callback=None,
            threshold_ms=1500.0,
            min_samples=5,
            clock=_Clock(),
        )
        a.set_preload_active(True)
        tripped = False
        for ms in (1600, 1700, 1800, 1900, 2000):
            tripped = a.record_round_trip(ms) or tripped

        self.assertTrue(tripped)
        self.assertTrue(a.tripped)
        # Latched even though no callback fired.
        self.assertEqual(a.last_disabled_at, 1000.0)
        self.assertIsNotNone(a.last_p95_ms)


if __name__ == "__main__":
    unittest.main()
