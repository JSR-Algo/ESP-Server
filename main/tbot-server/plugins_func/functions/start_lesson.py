import asyncio

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

_FAILURE_STATUS_CODES = {
    "NO_CURRENT_ASSIGNMENT",
    "BACKEND_UNAVAILABLE",
    "LESSON_CONFIG_MISSING",
    "LESSON_CAPABILITY_MISSING",
    "DEVICE_TOKEN_UNAVAILABLE",
    "ASSIGNMENT_TERMINAL",
    "ASSIGNMENT_INVALID",
    "MANIFEST_EMPTY",
    "MANIFEST_IDENTITY_MISMATCH",
    "MANIFEST_CHECKSUM_MISSING",
    "MANIFEST_CHECKSUM_MISMATCH",
    "START_REFUSED",
    "SAMPLE_SD_PACK_UNSUPPORTED",
}
_SAMPLE_FALLBACK_STATUS_CODES = {"NO_CURRENT_ASSIGNMENT", "BACKEND_UNAVAILABLE"}
_SPOKEN_START_ORIGIN = "spoken_start"


def _lesson_start_status(conn: "ConnectionHandler"):
    status = getattr(conn, "lesson_start_status", None)
    return status if isinstance(status, dict) else {}

def _sample_fallback_allowed(status: dict) -> bool:
    code = status.get("code")
    return code in _SAMPLE_FALLBACK_STATUS_CODES or (
        code == "DEVICE_TOKEN_UNAVAILABLE" and status.get("reason") == "missing_identity"
    )


def _schedule_lesson_start_feedback(conn: "ConnectionHandler", message: str) -> None:
    provider = getattr(conn, "voice_provider", None)
    sender = getattr(provider, "_send_lesson_start_ack", None)
    loop = getattr(conn, "loop", None)
    if not message or not callable(sender) or loop is None or not loop.is_running():
        return
    response = ActionResponse(
        action=Action.RESPONSE,
        result="lesson_start_failed",
        response=message,
    )
    loop.create_task(sender(response))

# Conversational "switch to lesson" trigger.
#
# Modeled on plugins_func/functions/change_role.py (same register_function
# decorator + ToolType enum). Where change_role is CHANGE_SYS_PROMPT, this is a
# SYSTEM_CTL tool because it must run an async side task on the live connection
# (the lesson pull/start path) and pass ``conn`` through — exactly like
# play_music.py, which schedules its work via ``conn.loop.create_task`` and
# returns an immediate ActionResponse so the realtime voice path is never
# blocked.
#
# The handler does NOT duplicate the lesson start logic. It invokes the SAME
# entry point connection.py uses for connect-time pull
# (_lesson_pull_on_connect -> core.lesson.runtime.maybe_start_lesson_on_connect),
# turning "switch to the lesson / chuyển sang bài học" from a reconnect-only
# side effect into an in-conversation trigger. maybe_start_lesson_on_connect is
# idempotent: if a runtime for the device's current assignment is already
# pinned, it keeps the existing session (republish-on-connect no-op).
start_lesson_function_desc = {
    "type": "function",
    "function": {
        "name": "start_lesson",
        # ── TRIGGER CONTRACT cho Live function-calling ──────────────────────────
        # Đây là phần model dùng để khớp Ý ĐỊNH của trẻ. Ranh giới đúng là:
        # "VÀO BÀI HỌC ĐÃ GIAO" (assignment của thiết bị) — KHÔNG phải "dạy con ngay".
        # Phải nêu RÕ phản-ví-dụ tiếng Việt nguy hiểm nhất: 'dạy con / cho con học'.
        "description": (
            "Chuyển robot sang CHẾ ĐỘ BÀI HỌC và bắt đầu ĐÚNG bài học đang được "
            "GIAO cho trẻ (assignment hiện hành của thiết bị). Gọi hàm này khi trẻ "
            "muốn bắt đầu, vào, mở, chuyển sang, hoặc học tiếp BÀI HỌC / TIẾT HỌC / "
            "KHOÁ HỌC của mình. Switch the robot into LESSON mode and start the "
            "child's currently ASSIGNED lesson. Call this when the child wants to "
            "begin, enter, open, switch to, or resume their lesson / class / course. "
            "Triggers (Tiếng Việt): 'học bài thôi', 'con muốn học bài', "
            "'vào bài học', 'mở bài học của con', 'bắt đầu bài học', "
            "'bắt đầu học bài', 'mở khóa học của con', 'vào khóa học của con', "
            "'chuyển sang bài học', 'học tiếp bài', 'tiếp tục khóa học', "
            "'bài học của con đâu'. "
            "Triggers (English): 'start the lesson', \"let's do the lesson\", "
            "'open my lesson', 'begin the class', 'switch to lesson', "
            "'continue the lesson', 'continue the course', 'resume course', "
            "'resume my class'. "
            "KHÔNG gọi / Do NOT call khi trẻ chỉ muốn được DẠY hoặc CHƠI HỌC NGAY "
            "trong lúc trò chuyện — đó là hội thoại thường, hãy tự dạy luôn, đừng "
            "vào lesson runtime: 'dạy con đi', 'cho con học chữ', 'con muốn học số', "
            "'con muốn hát', 'đố con đi', 'kể chuyện cho con' (teach-me-now / play / "
            "sing / quiz / story = normal chat, NOT the assigned lesson). Cũng KHÔNG "
            "gọi cho hỏi đáp chung, hỏi giờ/thời tiết, hay tán gẫu. Khi mơ hồ thì "
            "hỏi lại trẻ trước, đừng gọi hàm (if unclear, ask the child first)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


@register_function("start_lesson", start_lesson_function_desc, ToolType.SYSTEM_CTL)
def start_lesson(conn: "ConnectionHandler"):
    """Trigger the lesson runtime start path on the live connection.

    Returns immediately; the actual assignment/manifest fetch + lesson_prepare
    runs as a fire-and-forget task so the voice turn is never delayed. Mirrors
    play_music.py's async-dispatch pattern.
    """
    try:
        # Respect the same dark-rollout gate the connect-time pull honors. If an
        # operator has not enabled the lesson runtime (LESSON_RUNTIME_ENABLED /
        # config["lesson"]["runtime_enabled"]) NOR the built-in sample demo
        # (LESSON_SAMPLE_ENABLED / config["lesson"]["sample_lesson"]), the voice
        # trigger must NOT bypass it — keep the lesson layer fully dark when disabled.
        # BACKWARD-COMPAT: an ABSENT _lesson_runtime_enabled means "proceed" (the
        # original gate only refused when the method was PRESENT and returned False);
        # preserve that so callers without the method keep working. The sample demo
        # flag adds an INDEPENDENT admission path.
        from core.providers.tools.product_toolset import runtime_rollout_allows_device, sample_lesson_enabled

        runtime_check = getattr(conn, "_lesson_runtime_enabled", None)
        runtime_disabled = callable(runtime_check) and (
            not bool(runtime_check()) or not runtime_rollout_allows_device(conn)
        )
        runtime_admitted = not runtime_disabled
        sample_on = sample_lesson_enabled(conn)
        if runtime_disabled and not sample_on:
            conn.logger.bind(tag=TAG).info(
                "start_lesson requested but lesson runtime is disabled (flag OFF)"
            )
            return ActionResponse(
                action=Action.RESPONSE,
                result="Lesson runtime disabled",
                response="Robot chưa sẵn sàng vào bài học lúc này.",
            )

        loop = getattr(conn, "loop", None)
        if loop is None or not loop.is_running():
            conn.logger.bind(tag=TAG).error(
                "start_lesson: event loop not running, cannot schedule lesson pull"
            )
            return ActionResponse(
                action=Action.RESPONSE,
                result="System busy",
                response="Please try again in a moment.",
            )

        prior_task = getattr(conn, "lesson_pull_task", None)
        if (
            prior_task is not None
            and not prior_task.done()
            and getattr(conn, "lesson_pull_task_origin", None) == _SPOKEN_START_ORIGIN
        ):
            conn.logger.bind(tag=TAG).info(
                "start_lesson: lesson start already pending; coalescing duplicate"
            )
            return ActionResponse(
                action=Action.RECORD,
                result="Lesson start scheduled",
                response="Okay, let's start the lesson.",
            )

        # DISPATCH. The sample lesson is a FALLBACK, never an unconditional winner.
        #
        #   - runtime DISABLED + sample_on  -> pure demo: the only admitted path is the
        #     built-in sample, so load it directly (smoke-test path, unchanged).
        #   - runtime ADMITTED (the normal case), regardless of sample_on -> run the REAL
        #     assignment pull first. The parent's backend assignment ALWAYS wins. Only if
        #     that pull finds NO real assignment (NO_CURRENT_ASSIGNMENT) AND sample_on do
        #     we fall back to the canned demo, scheduled from the done-callback below.
        #
        # The wrapped assignment-pull entry point already swallows exceptions so a lesson
        # failure can never crash the connection or touch the voice path.
        handoff_token_getter = getattr(conn, "lesson_start_handoff_token", None)
        task_handoff_token = (
            handoff_token_getter() if callable(handoff_token_getter) else None
        )
        sample_fallback_enabled = bool(sample_on) and runtime_admitted
        sample_fallback_handoff_token = None
        if runtime_disabled and sample_on:
            # runtime OFF -> the assignment pull is gated dark; the sample is the ONLY
            # admitted lesson path, so it runs directly (no real assignment to prefer).
            from core.lesson.sample import start_sample_lesson

            task = loop.create_task(start_sample_lesson(conn))
        else:
            pull = getattr(conn, "_lesson_pull_on_connect", None)
            if callable(pull):
                task = loop.create_task(pull())
            else:
                from core.lesson.runtime import maybe_start_lesson_on_connect

                task = loop.create_task(maybe_start_lesson_on_connect(conn))

        # create_task cannot begin executing until this synchronous tool handler
        # yields back to the loop. Reserve the optional sample-fallback holder only
        # after scheduling succeeds, so a scheduling exception cannot strand it.
        if sample_fallback_enabled:
            handoff_active = getattr(conn, "lesson_start_handoff_active", None)
            begin_handoff = getattr(conn, "begin_lesson_start_handoff", None)
            if (
                callable(handoff_active)
                and handoff_active()
                and callable(begin_handoff)
            ):
                sample_fallback_handoff_token = begin_handoff(
                    reason="sample_fallback_reserve"
                )

        # Track the task on the connection so close() cancels it (deep-audit #9: it
        # was a local var, so a disconnect mid-pull left it running -> leak / use-
        # after-close now that the lesson HTTP layer is restored). Supersede any
        # in-flight pull task first — the spoken trigger is the user's explicit
        # "start the lesson now".
        if prior_task is not None and not prior_task.done():
            prior_task.cancel()
        conn.lesson_pull_task = task
        conn.lesson_pull_task_origin = _SPOKEN_START_ORIGIN
        conn.lesson_pull_task_handoff_token = (
            sample_fallback_handoff_token or task_handoff_token
        )

        def _clear_spoken_start_origin(fut) -> None:
            if getattr(conn, "lesson_pull_task", None) is fut:
                conn.lesson_pull_task_origin = None
                conn.lesson_pull_task_handoff_token = None

        def _release_handoff_token(token, *, outcome, restore_conversation):
            release = getattr(conn, "release_lesson_start_handoff", None)
            if token is None or not callable(release):
                return
            cleanup = release(
                token,
                outcome=outcome,
                restore_conversation=restore_conversation,
            )
            if asyncio.iscoroutine(cleanup):
                loop.create_task(cleanup)

        def _release_sample_fallback_reserve(*, outcome, restore_conversation):
            _release_handoff_token(
                sample_fallback_handoff_token,
                outcome=outcome,
                restore_conversation=restore_conversation,
            )

        async def sample_fallback():
            from core.lesson.sample import start_sample_lesson

            try:
                return await start_sample_lesson(conn)
            finally:
                release = getattr(conn, "release_lesson_start_handoff", None)
                if sample_fallback_handoff_token is not None and callable(release):
                    await release(
                        sample_fallback_handoff_token,
                        outcome="sample_fallback_task_done_cleanup",
                        restore_conversation=True,
                    )

        def _handle_done(fut):
            try:
                runtime = fut.result()
                status = _lesson_start_status(conn)
                code = status.get("code")
                if runtime is None and _sample_fallback_allowed(status) and sample_fallback_enabled:
                    # The real pull found no usable backend lesson path, but the sample
                    # demo flag is on -> fall back to the built-in sample lesson. Schedule
                    # it as a new task and re-track it on the connection so close() can
                    # cancel it and a later spoken trigger can supersede it. Do NOT emit
                    # the audible setup/no-assignment feedback here — the sample takes over.
                    conn.logger.bind(tag=TAG).info(
                        "start_lesson: no backend assignment, falling back to sample lesson"
                    )
                    fallback_task = loop.create_task(sample_fallback())
                    conn.lesson_pull_task = fallback_task
                    conn.lesson_pull_task_origin = _SPOKEN_START_ORIGIN
                    conn.lesson_pull_task_handoff_token = (
                        sample_fallback_handoff_token
                    )
                    fallback_task.add_done_callback(_handle_sample_done)
                    return
                if sample_fallback_handoff_token is not None:
                    _release_sample_fallback_reserve(
                        outcome=(
                            "lesson_started"
                            if runtime is not None
                            else (code or "lesson_start_failed")
                        ),
                        restore_conversation=runtime is None,
                    )
                if runtime is None and code in _FAILURE_STATUS_CODES:
                    _schedule_lesson_start_feedback(conn, status.get("message") or "Robot chưa bắt đầu bài học được.")
                conn.logger.bind(tag=TAG).info("start_lesson: lesson pull task finished")
            except asyncio.CancelledError:
                _release_sample_fallback_reserve(
                    outcome="cancelled",
                    restore_conversation=True,
                )
                conn.logger.bind(tag=TAG).info("start_lesson: lesson pull task cancelled")
            except Exception as exc:  # pragma: no cover - already logged inside pull
                _release_sample_fallback_reserve(
                    outcome="lesson_start_exception",
                    restore_conversation=True,
                )
                conn.logger.bind(tag=TAG).warning(
                    f"start_lesson: lesson pull task error: {type(exc).__name__}: {exc}"
                )
            finally:
                _release_handoff_token(
                    task_handoff_token,
                    outcome="lesson_pull_task_done_cleanup",
                    restore_conversation=True,
                )
                _clear_spoken_start_origin(fut)

        def _handle_sample_done(fut):
            try:
                fut.result()
                conn.logger.bind(tag=TAG).info("start_lesson: sample fallback task finished")
            except asyncio.CancelledError:
                conn.logger.bind(tag=TAG).info("start_lesson: sample fallback task cancelled")
            except Exception as exc:  # pragma: no cover - already logged inside sample
                conn.logger.bind(tag=TAG).warning(
                    f"start_lesson: sample fallback task error: {type(exc).__name__}: {exc}"
                )
            finally:
                # A task cancelled before its first coroutine step never enters the
                # wrapper's finally block, so keep this idempotent callback fallback.
                _release_sample_fallback_reserve(
                    outcome="sample_fallback_task_done_cleanup",
                    restore_conversation=True,
                )
                _clear_spoken_start_origin(fut)

        task.add_done_callback(_handle_done)

        conn.logger.bind(tag=TAG).info("start_lesson: scheduled lesson start on live connection")
        # RECORD (not RESPONSE): log the tool call to history without forcing an
        # extra LLM round-trip — the lesson runtime itself drives the device via
        # lesson_* frames from here.
        return ActionResponse(
            action=Action.RECORD,
            result="Lesson start scheduled",
            response="Okay, let's start the lesson.",
        )
    except Exception as exc:
        conn.logger.bind(tag=TAG).error(f"start_lesson failed: {exc}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(exc),
            response="Sorry, I couldn't start the lesson.",
        )
