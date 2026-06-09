"""US-006 Slice-01 learning-course runtime (LANE-ESP, S6-S9).

Additive lesson layer that lives ENTIRELY in the ``ws_server`` process and never
touches the Google-Live / classic voice path. The ESP Server is the sole
downloader + sha256 verifier (D-PRELOAD-OWNER, ADR 0013 §C) and synthesizes
``lesson_preload_status``; firmware downloads nothing.
"""
