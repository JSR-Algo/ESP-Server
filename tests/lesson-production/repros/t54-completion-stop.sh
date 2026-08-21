#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

test_file="main/tbot-server/tests/test_lesson_runtime.py"

python3 - "$test_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
name = "test_v2_passive_final_step_stops_when_completion_motion_is_missing"
if name not in text:
    marker = "    # 18) greeting -> model -> celebrate plays to lesson_stop"
    test = '''    async def test_v2_passive_final_step_stops_when_completion_motion_is_missing(self):
        conn = _FakeConn(
            features={
                "lesson": True,
                "renderer": [
                    "teebot-lesson-renderer.v1",
                    "teebot-lesson-renderer.v2",
                ],
            }
        )
        conn.device_id = "robot-01"
        conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
            }
        }
        manifest = _build_class_steps_manifest(
            [("s9", "celebrate", "passive")]
        )
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        self.assertNotIn("motion", manifest["steps"][0])

        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)
        rt._step_acked = True
        rt._step_completed = True
        await rt._maybe_finish_step()
        await rt._visual_transition_task

        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

'''
    text = text.replace(marker, test + marker, 1)
    path.write_text(text, encoding="utf-8")
PY

PYTHON=${TBOT_REPRO_PYTHON:-python3}
"$PYTHON" -m pytest -q \
  "main/tbot-server/tests/test_lesson_runtime.py::LessonRuntimeTest::test_v2_passive_final_step_stops_when_completion_motion_is_missing"
