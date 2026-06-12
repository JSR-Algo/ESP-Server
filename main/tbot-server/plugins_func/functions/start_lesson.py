from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

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
        # The description is the trigger contract for the LLM/agent. It must fire
        # on the child's intent to enter lesson mode in BOTH English and
        # Vietnamese. Keep the example phrases explicit so the model maps the
        # intent reliably.
        "description": (
            "Switch the robot into LESSON mode and start the child's currently "
            "assigned lesson. Call this whenever the user asks to begin, switch "
            "to, open, or resume the lesson / a class / the course. "
            "Triggers (English): 'switch to lesson', 'start the lesson', "
            "'let's do the lesson', 'begin the class', 'open my lesson', "
            "'I want to study now'. "
            "Triggers (Vietnamese): 'chuyển sang bài học', 'bắt đầu bài học', "
            "'vào bài học', 'mở bài học', 'học bài thôi', 'con muốn học bài', "
            "'mình học nhé'. "
            "Do NOT call this for general questions, games, music, or chit-chat."
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
        # config["lesson"]["runtime_enabled"]), the voice trigger must NOT bypass
        # it — keep the lesson layer fully dark when disabled.
        enabled_check = getattr(conn, "_lesson_runtime_enabled", None)
        if callable(enabled_check) and not enabled_check():
            conn.logger.bind(tag=TAG).info(
                "start_lesson requested but lesson runtime is disabled (flag OFF)"
            )
            return ActionResponse(
                action=Action.RESPONSE,
                result="Lesson runtime disabled",
                response="Lesson mode is not available right now.",
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

        # Reuse the connection's own wrapped entry point when present — it already
        # swallows exceptions so a lesson failure can never crash the connection
        # or touch the voice path. Fall back to the runtime entry point directly
        # if that wrapper is unavailable.
        pull = getattr(conn, "_lesson_pull_on_connect", None)
        if callable(pull):
            task = loop.create_task(pull())
        else:
            from core.lesson.runtime import maybe_start_lesson_on_connect

            task = loop.create_task(maybe_start_lesson_on_connect(conn))

        # Track the task on the connection so close() cancels it (deep-audit #9: it
        # was a local var, so a disconnect mid-pull left it running -> leak / use-
        # after-close now that the lesson HTTP layer is restored). Supersede any
        # in-flight pull task first — the spoken trigger is the user's explicit
        # "start the lesson now".
        prior_task = getattr(conn, "lesson_pull_task", None)
        if prior_task is not None and not prior_task.done():
            prior_task.cancel()
        conn.lesson_pull_task = task

        def _handle_done(fut):
            try:
                fut.result()
                conn.logger.bind(tag=TAG).info("start_lesson: lesson pull task finished")
            except Exception as exc:  # pragma: no cover - already logged inside pull
                conn.logger.bind(tag=TAG).warning(
                    f"start_lesson: lesson pull task error: {type(exc).__name__}: {exc}"
                )

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
