#!/usr/bin/env bash
# repo: robot/esp32-server
# T2.3 repro — ESP voice-pipeline integration during lessons.
#
# Two defects, both RED on the pre-patch base:
#   1. Audio-overlap invariant: on the classic pipeline, voice-pipeline TTS audio
#      streamed to the device while a lesson owned the speaker (sendAudioHandle had
#      no lesson output-queue discipline; Google Live already had one upstream in
#      audio_bridge._should_drop_lesson_model_output).
#   2. Vietnamese đ/Đ (U+0111) has no combining-mark decomposition, so NFKD did not
#      fold it: accent-stripped STT ("bat dau bai hoc", "doc lai", "con khong lam
#      duoc") missed every marker phrase in the in-lesson child-answer classifier —
#      while the Google Live *trigger* matcher already folded it explicitly.
#
# The invariant file is copied in from the campaign dir so the SAME assertions run
# on the pre-patch base (real RED failures, not a missing-file error) and on the
# fix tip. It also locks the Google Live half of the invariant, so a classic-side
# fix cannot leak across pipelines (campaign ground rule 1).
set -euo pipefail

REPRO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_REL="tests/test_lesson_voice_output_discipline.py"

cd main/tbot-server
cp "$REPRO_DIR/t23_voice_output_discipline_test.py" "$TEST_REL"

python3 -m pytest -q -p no:randomly "$TEST_REL"

echo "REPRO PASS: T2.3 lesson audio-overlap invariant holds on BOTH pipelines and accent-stripped child answers classify correctly."
