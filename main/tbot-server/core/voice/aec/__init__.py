"""Server-side acoustic echo cancellation.

The robot's TBOT firmware advertises ``features.aec=true`` to the server
(``CONFIG_USE_SERVER_AEC=y``) but historically the server did not run AEC.
That gap meant the device's speaker output bled into its microphone
unattenuated, which made any voice barge-in path (Live VAD, RMS gating,
transcript-driven) flip-flop between "deaf to the user" and "self-interrupt
loop". This package provides a Speex DSP based AEC stage that hooks into
the Google Live pipeline (and any other voice-mode that wants it).
"""

from .aec_processor import AecProcessor, AEC_AVAILABLE

__all__ = ["AecProcessor", "AEC_AVAILABLE"]
