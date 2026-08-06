"""T6.2 — lesson log correlation + reconnect-storm counters.

Two regressions are locked in here:

1. Every lesson log line emitted while a lesson session exists must carry
   ``assignment_id`` and ``session_id``. Before T6.2 the suffix was implemented
   three times over three different id shapes, and every lesson log line outside
   those three helpers carried neither.

2. A superseded device connection and a peer-silence socket close — the two
   signals a reconnect storm shows up in — were log lines only, with no counter
   to alert on (T2.4 finding routed to T6.2).
"""

import ast
import re
from pathlib import Path

import pytest

TBOT_SERVER = Path(__file__).resolve().parents[1]

# Imported inside the tests, not at module scope. At the pre-fix base commit
# neither module exists, and a module-level import would turn the whole file
# into one collection error — so the grep audits below, which are the real
# behavioural evidence and fail on their own terms at base, would never run.


@pytest.fixture
def lesson_log_context():
    from core.lesson.log_context import lesson_log_context as fn

    return fn


@pytest.fixture
def with_lesson_log_context():
    from core.lesson.log_context import with_lesson_log_context as fn

    return fn


@pytest.fixture
def runtime_counters():
    from core.lesson import runtime_counters as module

    module.reset()
    return module


class _Runtime:
    def __init__(self, assignment_id=None, session_id=None):
        self.assignment_id = assignment_id
        self.session_id = session_id


class _Conn:
    def __init__(self, *, session_id=None, device_id=None, lesson_runtime=None):
        self.session_id = session_id
        self.device_id = device_id
        self.lesson_runtime = lesson_runtime


# ── 1. correlation helper ────────────────────────────────────────────────────


def test_context_from_wire_batch_dict(lesson_log_context):
    batch = {"assignmentId": "a-1", "sessionId": "s-1"}
    assert lesson_log_context(batch) == "assignment_id=a-1 session_id=s-1"


def test_context_from_runtime_object(lesson_log_context):
    assert (
        lesson_log_context(_Runtime("a-2", "s-2")) == "assignment_id=a-2 session_id=s-2"
    )


def test_context_follows_conn_to_its_lesson_runtime(lesson_log_context):
    # The connection knows the session; only the runtime knows the assignment.
    conn = _Conn(session_id="s-3", lesson_runtime=_Runtime(assignment_id="a-3"))
    assert lesson_log_context(conn) == "assignment_id=a-3 session_id=s-3"


def test_device_id_is_the_fallback_for_sessionless_lesson_work(lesson_log_context):
    # SD-pack fanout / GC / retry workers and nudge identity resolution run
    # outside any session by construction; device_id is the correlation key
    # there, and must never displace a known session.
    assert lesson_log_context(_Conn(device_id="d-1")) == "device_id=d-1"
    conn = _Conn(session_id="s-4", device_id="d-1")
    assert lesson_log_context(conn) == "session_id=s-4"


def test_no_context_available_leaves_the_message_untouched(lesson_log_context, with_lesson_log_context):
    assert with_lesson_log_context("lesson thing failed", _Conn()) == "lesson thing failed"
    assert lesson_log_context(None) == ""


def test_ids_already_spelled_out_are_not_duplicated(with_lesson_log_context):
    message = "lesson_peer_silent device_id={} session_id={} idle_sec={:.1f}"
    out = with_lesson_log_context(message, _Conn(session_id="s-5", lesson_runtime=_Runtime("a-5")))
    assert out.count("session_id=") == 1
    assert "assignment_id=a-5" in out


def test_helper_never_raises_on_hostile_sources(lesson_log_context, with_lesson_log_context):
    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    assert with_lesson_log_context("lesson x", Exploding()) == "lesson x"
    assert lesson_log_context(object()) == ""


def test_blank_and_non_string_ids_are_ignored(lesson_log_context):
    assert lesson_log_context({"assignmentId": "  ", "sessionId": 7}) == ""


# ── 2. the three historical helpers now share one implementation ─────────────


def test_runtime_and_forwarder_delegate_to_the_shared_helper():
    # Triplicated correlation logic is how the ids drifted apart in the first
    # place; keep the delegation.
    for rel in ("core/lesson/runtime.py", "core/lesson/forwarder.py"):
        source = (TBOT_SERVER / rel).read_text()
        assert "from core.lesson.log_context import with_lesson_log_context" in source, rel
        assert "with_lesson_log_context(" in source, rel


# ── 3. grep audit: no bare lesson log lines left on the lesson surface ───────

# Files whose lesson log lines must route through a correlation helper. The
# Google Live voice provider is deliberately excluded: it is a separate flow from
# classic_pipeline, its lesson-adjacent lines are voice-interaction diagnostics,
# and T6.2 does not touch it (routed to §5 instead).
CORRELATED_FILES = (
    "core/lesson/runtime.py",
    "core/lesson/forwarder.py",
    "core/handle/textHandler/lessonMessageHandler.py",
)

_LOG_CALL = re.compile(
    r"\blogger[a-zA-Z_]*\s*(?:\.bind\([^)]*\))?\s*\.\s*(?:info|warning|error|debug|critical)\s*\("
)


def _lesson_log_statements(path: Path):
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if not _LOG_CALL.search(line):
            continue
        statement = " ".join(part.strip() for part in lines[index : index + 8])
        if "lesson" in statement.lower():
            yield index + 1, statement


@pytest.mark.parametrize("rel", CORRELATED_FILES)
def test_lesson_log_lines_carry_correlation(rel):
    offenders = [
        (line_no, statement[:160])
        for line_no, statement in _lesson_log_statements(TBOT_SERVER / rel)
        if "with_lesson_log_context" not in statement
        and "_with_log_context" not in statement
        and "_with_lesson_log_context" not in statement
    ]
    assert not offenders, f"{rel}: lesson log lines without correlation context: {offenders}"


def test_connection_lesson_lifecycle_logs_carry_correlation():
    # connection.py also logs plenty of non-lesson lines; only assert on the
    # lesson-named ones.
    source = TBOT_SERVER / "core/connection.py"
    offenders = [
        (line_no, statement[:160])
        for line_no, statement in _lesson_log_statements(source)
        if re.search(r"\"lesson[_ ]|'lesson[_ ]|f\"lesson|f\"send lesson|LESSON_RUNTIME_ENABLED", statement)
        and "with_lesson_log_context" not in statement
    ]
    assert not offenders, f"connection.py lesson log lines without correlation: {offenders}"


# ── 4. reconnect-storm counters ──────────────────────────────────────────────


def test_counters_report_known_names_even_at_zero(runtime_counters):
    snapshot = runtime_counters.snapshot()
    # An absent key and a genuinely quiet counter must not look the same.
    assert snapshot[runtime_counters.CONNECTION_SUPERSEDED] == 0
    assert snapshot[runtime_counters.PEER_SILENCE_CLOSED] == 0


def test_counters_increment_and_are_monotonic(runtime_counters):
    runtime_counters.reset()
    runtime_counters.increment(runtime_counters.CONNECTION_SUPERSEDED)
    runtime_counters.increment(runtime_counters.CONNECTION_SUPERSEDED, 2)
    runtime_counters.increment(runtime_counters.PEER_SILENCE_CLOSED)
    runtime_counters.increment(runtime_counters.CONNECTION_SUPERSEDED, 0)
    runtime_counters.increment(runtime_counters.CONNECTION_SUPERSEDED, -5)
    snapshot = runtime_counters.snapshot()
    assert snapshot[runtime_counters.CONNECTION_SUPERSEDED] == 3
    assert snapshot[runtime_counters.PEER_SILENCE_CLOSED] == 1
    runtime_counters.reset()


def test_snapshot_is_a_copy(runtime_counters):
    runtime_counters.reset()
    snapshot = runtime_counters.snapshot()
    snapshot[runtime_counters.CONNECTION_SUPERSEDED] = 999
    assert runtime_counters.snapshot()[runtime_counters.CONNECTION_SUPERSEDED] == 0


def test_supersede_and_peer_silence_paths_increment_their_counters():
    """The emit sites are wired, not just the counter module.

    Driving a real supersede needs a full server + two live sockets; asserting
    the call is present at the two emit sites is the proportionate check.
    """
    ws_source = (TBOT_SERVER / "core/websocket_server.py").read_text()
    assert "CONNECTION_SUPERSEDED" in ws_source
    supersede_fn = _function_source(ws_source, "_scrap_superseded_connection")
    assert "increment(CONNECTION_SUPERSEDED)" in supersede_fn

    conn_source = (TBOT_SERVER / "core/connection.py").read_text()
    assert "lesson_runtime_counters.PEER_SILENCE_CLOSED" in conn_source
    # …and it must sit on the peer-silent branch, not merely somewhere in the file.
    peer_silent_branch = conn_source.split("if peer_silent:")[1][:400]
    assert "PEER_SILENCE_CLOSED" in peer_silent_branch


def test_esp_metrics_endpoint_exposes_the_counters():
    source = (TBOT_SERVER / "core/http_server.py").read_text()
    assert "lesson_runtime_counters.snapshot()" in source


def _function_source(module_source: str, name: str) -> str:
    tree = ast.parse(module_source)
    lines = module_source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{name} not found")
