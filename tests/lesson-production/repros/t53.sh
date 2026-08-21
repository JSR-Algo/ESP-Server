#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T5.3 repro — the two defects this session fixed inside the repo, both of which are
# checkable without the docker stack (the stack itself cannot be a gate: it is stateful,
# it needs seeded databases, and its verifier score is not stable run-to-run — F-T53-15).
#
# CASE 1 — a rejected lesson-event forward must name WHAT was rejected and WHY.
#   `LessonEventForwarder._handle_post_failure` logged only `type(exc).__name__`, i.e.
#   "HTTPStatusError". That cannot distinguish a retryable 502 from a permanently
#   refused payload, and it never says which event the backend refused. The cost was
#   not theoretical: two lessons back-to-back on one device left the second assignment
#   stuck at RUNNING forever, and the log gave no way to see that a 429 had eaten the
#   terminal batch carrying `lesson_completed` (F-T53-17). Runs the regression test.
#
# CASE 2 — the studio-compose contract test must pin the CURRENT asset-origin hint.
#   T5.3 session 1 changed `LESSON_ASSET_ORIGIN_BASE`'s hint to require the
#   /tvideo-demo prefix (F-T41E-05: a bare origin serves the SPA index.html, which the
#   canonical spec then sha256-hashes as if it were media) and never updated the
#   assertion. The test has been RED on main ever since, so the compose contract it is
#   supposed to guard has been unguarded.
#
# Exit 0 = both pass. Runs inside a worktree created by gate.sh.
set -uo pipefail

# gate.sh runs this with CWD set to the worktree it created — resolve from CWD, never
# from $0, which lives in lesson-prod/repros/ (outside every git repo).
ROOT="${T53_REPO_ROOT:-$(pwd)}"
SERVER="${ROOT}/main/tbot-server"
[ -d "${SERVER}" ] || { echo "FATAL: no main/tbot-server under ${ROOT}"; exit 2; }

fail=0

echo "== case 1: forwarder failure log names the status and the rejected events =="
if (cd "${SERVER}" && python3 -m pytest \
      tests/test_lesson_forwarder.py::LessonEventForwarderDurabilityTest::test_failure_log_names_the_status_and_the_rejected_events \
      -q) ; then
  echo "   PASS"
else
  echo "   FAIL — a rejected forward still does not name its status/events"
  fail=1
fi

echo "== case 3: emitted frames log the shared checkpoint contract =="
if (cd "${SERVER}" && python3 -m pytest \
      "tests/test_lesson_runtime.py::LessonRuntimeTest::test_emitted_frame_logs_carry_the_shared_checkpoint_contract" \
      "tests/test_lesson_runtime.py::LessonRuntimeTest::test_manifest_fetch_log_declares_the_full_step_roster" \
      -q) ; then
  echo "   PASS"
else
  echo "   FAIL — emitted frames do not carry type=/sequence=/media=, or the manifest"
  echo "          log does not declare the served step roster"
  fail=1
fi

echo "== case 8: the robot records WHAT it asked, and whether the child could see it =="
# Two facts only the runtime knows. Without the prompt text nothing can tell a guiding
# question from a bare command; without afterRenderAck nothing can tell whether the
# child could see the step when it was asked (the two log lines share a wire sequence
# and come from different streams, so their order is not recorded).
if (cd "${SERVER}" && python3 - <<'PYEOF'
import inspect, sys
sys.path.insert(0, ".")
import core.lesson.runtime as rt

src = inspect.getsource(rt)
assert "afterRenderAck=" in src, "prompt handoff does not record whether the render was acked"
assert 'text="{_norm_prompt_for_log(prompt)}"' in src or "_norm_prompt_for_log(prompt)" in src, (
    "prompt handoff does not record what was asked"
)
# The three-layer declaration must not carry JSON: a check skips such lines.
emit = src[src.index('"emit lesson_step "'):]
emit = emit[:emit.index("self._log", 10)] if "self._log" in emit[10:] else emit[:2000]
assert "storyBeat=" not in emit.split("completionClass=")[0], (
    "storyBeat is still embedded in the three-layer declaration line"
)
print("runtime prompt/layer contract OK")
PYEOF
) ; then
  echo "   PASS"
else
  echo "   FAIL — the runtime does not record the prompt text, the render-ack state,"
  echo "          or still embeds storyBeat in the layer declaration"
  fail=1
fi

echo "== case 7: the lesson's FIRST spoken prompt is not dropped =="
# start_lesson prewarms Live and drives straight into the lesson, so step 1's prompt is
# sent while the client object exists but its session is still connecting. Treating that
# as ready dropped the prompt (handoff=0) and the child heard nothing at the start.
if (cd "${SERVER}" && python3 -m pytest \
      "tests/test_google_live_lesson_conversation.py::LessonPromptWaitsForLiveConnectTest" \
      -q) ; then
  echo "   PASS"
else
  echo "   FAIL — a half-open Live session still reports ready and the first prompt is lost"
  fail=1
fi

echo "== case 6: the assignment read can ask for the TERMINAL state =="
# assignment/current answers ACTIVE-only, so after a completion it is null and the
# device cannot tell a recorded completion from a lost one. Asserted against the client
# itself so the case is independent of the branch's test files.
if (cd "${SERVER}" && python3 - <<'PYEOF'
import asyncio, inspect, sys
sys.path.insert(0, ".")
import config.manage_api_client as mac

params = inspect.signature(mac.get_current_assignment).parameters
assert "include_terminal" in params, "client cannot request the terminal state"

seen = {}


async def _capture(client, method, url, **kwargs):
    seen["url"] = url
    return {"data": {"assignment": None}}


mac._lesson_request_with_retry = _capture
asyncio.run(
    mac.get_current_assignment(object(), "http://b/v1", "dev-1", include_terminal=True)
)
assert "includeTerminal=true" in seen.get("url", ""), (
    f"terminal read-back not requested; url={seen.get('url')!r}"
)

seen.clear()
asyncio.run(mac.get_current_assignment(object(), "http://b/v1", "dev-1"))
assert "includeTerminal" not in seen.get("url", ""), (
    "pull-on-connect must NOT ask for terminal assignments; "
    f"url={seen.get('url')!r}"
)
print("terminal read-back contract OK")
PYEOF
) ; then
  echo "   PASS"
else
  echo "   FAIL — the assignment read cannot request the terminal state"
  fail=1
fi

echo "== case 5: a completion the backend never recorded is surfaced =="
# F-T53-17's failure mode: a rate-limited terminal batch was discarded, the robot
# finished the lesson, and the assignment sat RUNNING forever with nothing saying so.
# The runtime now reads back and WARNS when its own assignment is still active.
if (cd "${SERVER}" && python3 -m pytest \
      "tests/test_lesson_runtime.py::LessonRuntimeTest::test_read_back_warns_when_the_completion_never_landed" \
      "tests/test_lesson_runtime.py::LessonRuntimeTest::test_completion_read_back_waits_for_its_own_forward_to_land" \
      "tests/test_lesson_runtime.py::LessonRuntimeTest::test_lesson_session_is_joinable_to_the_connection_session" \
      -q) ; then
  echo "   PASS"
else
  echo "   FAIL — a lost completion is still silent, the read-back races its own"
  echo "          forward, or the two session ids are not joined"
  fail=1
fi

echo "== case 4: the terminal frame does not fail a healthy device =="
# lesson_quiescent_after_stop scored the device's ack OF lesson_stop as post-stop
# activity, and lesson_ack_sequence_match scored the same ack as an unexpected step
# ack. Both made a correct run unpassable. Asserted through the harness's own suite.
if (cd "${ROOT}/../.." >/dev/null 2>&1 || true; cd "${ROOT}" && python3 - <<'PYEOF'
import importlib.util, sys, pathlib
snapshot = pathlib.Path("harness/lesson-e2e/lesson_e2e_log_verify.py")
spec = importlib.util.spec_from_file_location("v", snapshot)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
DEV = "14:c1:9f:d1:a8:48"
lines = [
    "I (0) Application: TBOT firmware boot complete",
    "I (0) WiFi: connected ssid=x ip=127.0.0.1",
    f"websocket hello device_id={DEV} session=s-1",
    'voice intent start_lesson text="bat dau bai hoc" handled=true',
    "LessonRuntime-INFO-emit lesson_stop type=lesson_stop stepId= sequence=12 assignmentId=a1 sessionId=s-1",
    "serial TX lesson_ack lesson_stop assignmentId=a1 sessionId=s-1 acks=12 seq=12 rendered=true",
]
report = v.evaluate_lesson_logs(lines, device_id=DEV, order_by_wire_sequence=True)
by = {c["name"]: c for c in report["checks"]}
quiescent = by["lesson_quiescent_after_stop"]
assert "activity_after_stop=none" in str(quiescent.get("evidence")), quiescent
acks = by["lesson_ack_sequence_match"]
assert "unexpected_ack=lesson_step:12" not in str(acks.get("evidence")), acks
print("terminal-frame model OK")
PYEOF
) ; then
  echo "   PASS"
else
  echo "   FAIL — acking lesson_stop is still scored as post-stop activity or as an"
  echo "          unexpected step ack"
  fail=1
fi

echo "== case 2: studio-compose test pins the current asset-origin hint =="
if (cd "${SERVER}" && python3 -m pytest \
      tests/test_lesson_studio_e2e_compose.py::test_lesson_studio_compose_is_test_owned_and_complete \
      -q) ; then
  echo "   PASS"
else
  echo "   FAIL — the compose contract test is red"
  fail=1
fi

exit "${fail}"
