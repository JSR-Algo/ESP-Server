"""S8 — single-step lesson interpreter (+ S6 capability gate, restart re-attest).

Drives the slice state machine for ONE espTft ``model`` step, byte-consistent with
the frozen S2 fixture ``fixtures/lesson-protocol.v1.json``:

    prepare(seq1) -> [preload, ESP-synth status] -> start(seq2) -> step s4(seq3) -> stop(seq4)

P0 ack contract (plan §5.3): the ESP correlates each inbound ``lesson_ack`` on
``body.acks == the sequence of the outstanding S->F frame``. The ack's OWN envelope
``sequence`` is the firmware's F->S counter and is NEVER used for correlation; there
is NO ``ackFor`` field anywhere.

Everything here lives in the ``ws_server`` process and is additive — it never
touches the voice path.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.lesson.errors import (
    LessonError,
    ProtocolSequenceError,
    StepTimeout,
    PROTOCOL_VERSION,
    LESSON_VERSION_UNSUPPORTED,
    lesson_capability_ok,
    device_renderer_capabilities,
)
from core.utils.util import get_vision_url

TAG = "LessonRuntime"

NO_CURRENT_ASSIGNMENT_MESSAGE = "Robot chưa có bài học nào được giao."


def _set_lesson_start_status(conn: Any, code: str, message: str = "") -> None:
    try:
        conn.lesson_start_status = {"code": code, "message": message}
    except Exception:
        pass

# Runtime states (a slice subset of the assignment state machine).
S_IDLE = "IDLE"
S_PRELOADING = "PRELOADING"
S_READY = "READY"
S_RUNNING = "RUNNING"
S_COMPLETED = "COMPLETED"
S_FAILED = "FAILED"

# ── per-step completion semantics (P5 playability fix + L3 P1 author types) ──────
# A step splits into one of two completion classes ON THE WIRE:
#
#   PASSIVE narration — the robot just speaks/animates; the child does NOT tap or
#   answer, so the FIRMWARE NEVER emits a step_completed progress for it. It
#   AUTO-ADVANCES once the firmware acks the lesson_step (render confirmed). A
#   passive step's per-step timer is a display DWELL that, if it fires, is a NORMAL
#   advance — never a FAILED StepTimeout.
#
#   INTERACTIVE — the child taps/answers, so the firmware DOES emit step_completed.
#   These keep the slice contract verbatim: wait for BOTH the ack AND step_completed,
#   with STEP_TIMEOUT firing on ACK ABSENCE.
#
# AUTHORITATIVE CLASSIFIER (L3 P1): the backend manifest step now carries an
# explicit ``completionClass`` ('passive' | 'interactive') per step. The runtime
# trusts THAT — a step is PASSIVE iff completionClass == 'passive', INTERACTIVE iff
# == 'interactive'. This lets authors define NEW step types (e.g. 'songBreak',
# 'warmup', 'recap') that reuse existing render triples without being misclassified
# by a hardcoded type set. NO protocol-version change: completionClass is an additive
# field inside the existing renderer-v1 manifest step.
#
# BACKWARD-COMPAT FALLBACK: a step with NO completionClass (older backend, or a v1
# manifest predating this field) falls back to the v1 BUILTIN set membership below,
# so nothing regresses for the current seed/manifests. An unknown/None stepType under
# that fallback is treated as INTERACTIVE (conservative: keep waiting for
# step_completed rather than silently auto-advancing an unrecognized step).
#
# LOAD-BEARING INVARIANT (pin the dependency): a PASSIVE step auto-advances on its
# ack; an INTERACTIVE step waits for the firmware step_completed. The firmware
# currently emits step_completed for EVERY rendered step — so an interactive step
# never hangs today. That is the coincidence completionClass makes explicit: do NOT
# rely on the firmware's unconditional step_completed to classify a step; classify
# from completionClass (falling back to PASSIVE_STEP_TYPES) and treat the firmware
# emission as confirmation only.
#
# v1 BUILTIN fallback set: the 5 passive narration kinds of the original 9-type
# STEP_RENDER_MAP. Documented + retained ONLY as the no-completionClass fallback.
PASSIVE_STEP_TYPES = frozenset(
    {"greeting", "review", "focus", "feedback", "celebrate"}
)


def _is_passive_step(step: Optional[Dict[str, Any]]) -> bool:
    """True iff the step is a passive narration step (no child interaction, so no
    firmware step_completed) and therefore AUTO-ADVANCES on its ack.

    Classification order:
      1) explicit ``completionClass`` ('passive'|'interactive') — authoritative;
      2) fallback to ``PASSIVE_STEP_TYPES`` membership when completionClass absent.
    """
    if not step:
        return False
    completion_class = step.get("completionClass")
    if completion_class == "passive":
        return True
    if completion_class == "interactive":
        return False
    # Backward-compat: no completionClass -> v1 builtin type-set fallback.
    return step.get("type") in PASSIVE_STEP_TYPES


def _wire_timestamp() -> int:
    """Epoch milliseconds (plan §5.2: wire timestamp is epoch ms, never RFC3339)."""
    return int(time.time() * 1000)


def _coerce_ack_seq(acked: Any) -> Optional[int]:
    """Coerce an inbound ``body.acks`` to the int S->F sequence used as the
    ``_outstanding`` dict key, or ``None`` if it is not a well-formed sequence.

    Tolerates an ``int`` (canonical), a numeric ``str`` ("3"), or a ``bool`` (an int
    subclass — explicitly rejected since True/False are never a real sequence). Any
    unhashable/non-numeric value (list, dict, None, "abc") -> ``None`` so the caller
    treats it as a malformed/stale ack and no-ops, instead of raising TypeError into
    the dict ``.pop()`` (which would otherwise tear down the connection + voice)."""
    if isinstance(acked, bool):
        return None
    if isinstance(acked, int):
        return acked
    if isinstance(acked, str):
        try:
            return int(acked.strip())
        except (ValueError, TypeError):
            return None
    return None


def parse_manifest_checksum(etag: Optional[str]) -> str:
    """ETag is ``"lesson-<version>-<profile>-<checksum>"`` (backend etagFor)."""
    if not etag:
        return ""
    parts = etag.strip().strip('"').split("-")
    return parts[3] if len(parts) >= 4 else ""

def lesson_asset_public_base_url(config: Dict[str, Any]) -> str:
    lesson_cfg = config.get("lesson", {}) or {}
    server_cfg = config.get("server", {}) or {}
    explicit = (
        lesson_cfg.get("asset_public_base_url")
        or lesson_cfg.get("asset_public_base")
        or server_cfg.get("asset_public_base_url")
    )
    if explicit:
        return str(explicit).rstrip("/")
    if "server" not in config:
        return ""
    vision_url = get_vision_url(config)
    if vision_url and "/mcp/vision/explain" in vision_url:
        return vision_url.replace("/mcp/vision/explain", "").rstrip("/")
    return ""


class LessonRuntime:
    """Per-device lesson session state, held on ``ConnectionHandler.lesson_runtime``.

    Injected deps (``send``, ``clock``, ``sleep``) keep the §10.2 pytest free of a
    real socket / wall clock.
    """

    def __init__(
        self,
        conn: Any,
        *,
        assignment: Dict[str, Any],
        manifest: Dict[str, Any],
        asset_cache: Any,
        forwarder: Any,
        manifest_checksum: str = "",
        send: Optional[Callable[[str], Awaitable[None]]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        default_step_timeout_sec: float = 12.0,
        alarm: Any = None,
    ) -> None:
        self.conn = conn
        self.logger = getattr(conn, "logger", None)
        self.assignment_id = assignment.get("assignmentId")
        self.assignment_version = int(assignment.get("assignmentVersion", 1))
        self.lesson_id = assignment.get("lessonId")
        self.lesson_version = int(assignment.get("lessonVersion", 1))
        self.profile = assignment.get("profile", "espTft")
        # sessionId SHOULD equal the WS session_id; a fresh WS connection already
        # mints a fresh session_id, which cleanly resumes the (assignmentId,sessionId)
        # sequence namespace on ESP restart (plan §6.3.5 — "fresh sessionId" option).
        self.session_id = assignment.get("sessionId") or getattr(conn, "session_id", None)
        self.manifest = manifest
        self.manifest_checksum = manifest_checksum
        # L3 P3 — the device's advertised renderer-capability SET (forward-modelled
        # string|list from hello.features.renderer; defaults to the v1-only set for
        # every current firmware). A served manifestVersion MUST be in this set or
        # the start() gate rejects it (LESSON_VERSION_UNSUPPORTED).
        self.renderer_capabilities = device_renderer_capabilities(
            getattr(conn, "features", None)
        )
        # The renderer version actually negotiated/served for this session: the
        # manifest's manifestVersion when present, else the v1 PROTOCOL_VERSION
        # fallback. Stamped into every outbound envelope's protocolVersion. Today
        # (v1 manifest, v1 device) this is identical to PROTOCOL_VERSION.
        self.negotiated_version = manifest.get("manifestVersion") or PROTOCOL_VERSION
        self.asset_cache = asset_cache
        self.forwarder = forwarder
        self._send = send or self._default_send
        self._sleep = sleep or asyncio.sleep
        self._default_step_timeout_sec = default_step_timeout_sec
        # S13 alarm (plan §11.2 / CP-8): brackets the preload window so the voice
        # round-trip p95 is measured "during an active preload". Optional + best-effort
        # — a missing alarm or a raising hook never affects the lesson run.
        self._alarm = alarm

        self._seq = 0  # S->F monotonic counter; first emitted frame is sequence 1.
        self._outstanding: Dict[int, Dict[str, Any]] = {}  # S->F seq -> {type, stepId}
        self._last_inbound_sequence = 0  # F->S gap detector
        self.state = S_IDLE
        self.last_error: Optional[LessonError] = None

        self._preload_task: Optional[asyncio.Task] = None
        self._step_timeout_task: Optional[asyncio.Task] = None
        self._step_seq: Optional[int] = None
        self._step_id: Optional[str] = None
        self._step: Optional[Dict[str, Any]] = None  # the in-flight step row
        self._step_passive = False  # cached _is_passive_step(self._step)
        self._step_acked = False
        self._step_completed = False
        self._closed = False

        # P5 multi-step playback: the ordered renderable manifest steps + a cursor.
        # The slice ran ONE step; P5 advances through ALL of them in manifest order,
        # one lesson_step per step, each gated on its own ack + step_completed.
        self._steps: List[Dict[str, Any]] = self._select_steps()
        self._step_index = -1  # bumped to 0 by the first _emit_step()
        self._steps_completed = 0  # real count for lesson_completed.summary

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Validate gates, then send ``lesson_prepare`` (seq 1). Raises a
        ``LessonError`` for any pre-send gate failure (capability / protocol /
        profile) so the caller logs it and NEVER puts a frame on the wire."""
        features = getattr(self.conn, "features", None)
        if not lesson_capability_ok(features):
            # D-CAP-FLAG: absence = no support; MUST NOT send lesson_prepare.
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED, "device did not advertise lesson capability"
            )
            raise self.last_error
        # L3 P3 negotiated-version gate: the served manifestVersion MUST be a version
        # the DEVICE advertised it can render. For every current device this set is
        # exactly {teebot-lesson-renderer.v1}, so a v1 manifest passes and a v2
        # manifest served to a v1-only device is rejected here (LESSON_VERSION_UNSUPPORTED,
        # no crash) — the structural guard that survives a future v2 renderer. Net
        # effect today is identical to the old ``!= PROTOCOL_VERSION`` check.
        manifest_version = self.manifest.get("manifestVersion")
        if manifest_version not in self.renderer_capabilities:
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED, f"unsupported manifestVersion {manifest_version!r}"
            )
            raise self.last_error
        # Gate passed -> the negotiated version is the served version (validated above).
        self.negotiated_version = manifest_version
        # DEVICE-RENDERER profile gate: a published lesson with a non-espTft profile
        # (piTft/mobile) can be accepted upstream, but espTft-only firmware renders a
        # non-espTft lesson_prepare BLANK. The backend has no device-renderer model, so
        # the gate lives HERE where both the device AND the assignment's profile are
        # known. CONFIG-DRIVEN: a future piTft/mobile firmware just adds its profile to
        # config lesson.supported_profiles (do NOT hardcode espTft). Default ['espTft'].
        # Mirrors the capability/manifestVersion gates above -> caller logs, NO frame on
        # the wire, the lesson is skipped instead of rendering blank.
        config = getattr(self.conn, "config", {}) or {}
        supported = (config.get("lesson", {}) or {}).get("supported_profiles") or ["espTft"]
        if self.profile not in supported:
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED,
                f"profile {self.profile!r} not renderable by this device (supported={supported})",
            )
            raise self.last_error
        # Profile reject (forced full-video espTft backgroundScene) BEFORE prepare.
        self.asset_cache.assert_profile_renderable()

        self.state = S_PRELOADING
        await self._emit("lesson_prepare", body=self._prepare_body())

    async def close(self) -> None:
        self._closed = True
        self._cancel_step_timeout()
        if self._preload_task is not None and not self._preload_task.done():
            self._preload_task.cancel()
        if self.forwarder is not None:
            await self.forwarder.aclose()
        if self.asset_cache is not None:
            await self.asset_cache.aclose()

    async def replay_pending_terminal_event(self) -> bool:
        replay = getattr(self.forwarder, "replay_pending_terminal_event", None)
        if not callable(replay):
            return False
        return bool(await replay())

    # ── inbound handlers (called by lessonMessageHandler via conn.lesson_runtime) ─

    async def on_lesson_ack(self, msg_json: Dict[str, Any]) -> None:
        if self.state in (S_FAILED, S_COMPLETED):
            return  # terminal is absorbing — no late frame can resurrect/override it
        body = msg_json.get("body") or {}
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return
        acked = body.get("acks")  # P0: correlate on body.acks, NOT envelope.sequence.
        # DEFENSIVE COERCE: body.acks MUST be the int S->F sequence of the outstanding
        # frame. A malformed firmware/replay frame could send a list (e.g. [3]) or a
        # str — an unhashable/wrong-typed key would raise TypeError on the dict .pop()
        # below, which (pre-isolation) tore down the connection + voice. Coerce to int;
        # anything that is not a hashable int (None, list, dict, non-numeric str) is a
        # malformed ack -> idempotent no-op, identical to a stale/unknown ack.
        acked = _coerce_ack_seq(acked)
        frame = self._outstanding.pop(acked, None) if acked is not None else None
        if frame is None:
            # Stale / unknown ack -> idempotent no-op (re-ack semantics, plan §5.8).
            return
        await self._on_frame_acked(frame, body)

    async def on_lesson_progress(self, msg_json: Dict[str, Any]) -> None:
        if self.state in (S_FAILED, S_COMPLETED):
            return  # terminal is absorbing (e.g. no PROTOCOL_SEQUENCE_ERROR after STEP_TIMEOUT)
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return
        body = msg_json.get("body") or {}
        event = body.get("event")
        step_id = msg_json.get("stepId")
        # Forward the firmware-observed progress (result->outcome rename owned by the
        # forwarder / post_lesson_event). The wire sequence rides through for dedup.
        self._forward(
            {
                "type": event,
                "sequence": msg_json.get("sequence"),
                "stepId": step_id,
                "stepType": body.get("stepType"),
                "result": body.get("result"),
                "detail": body.get("detail"),
            }
        )
        if event == "step_completed":
            # LATCH-CONTAMINATION GUARD: only the step_completed for the CURRENT
            # in-flight step may set the completion latch. The firmware emits an
            # UNCONDITIONAL step_completed for every rendered step (lesson_handler.cc
            # :327 ack then :339 step_completed) — including a PASSIVE step that has
            # ALREADY auto-advanced on its ack. With WS in-order delivery that stray
            # passive step_completed lands AFTER _emit_step has moved on to the next
            # (now-current) step and reset the latch; without this guard it would set
            # _step_completed=True on the WRONG step, so that step would finish on its
            # ack alone — skipping its own step_completed and cascading an off-by-one
            # for the rest of the run. The stepId rides the top-level envelope (the
            # firmware echoes the inbound frame's stepId into the F->S envelope), so a
            # step_completed whose stepId != self._step_id is a STALE/leftover event:
            # it is still forwarded above (log/observability) but MUST NOT latch.
            if step_id == self._step_id:
                self._step_completed = True
                await self._maybe_finish_step()

    async def on_lesson_error(self, msg_json: Dict[str, Any]) -> None:
        # lesson_error rides the same F->S sequence stream but is a status report (not
        # acked). Count it so a later ack/progress is not mis-flagged as a gap
        # (symmetric accounting with on_lesson_ack/on_lesson_progress).
        seq = msg_json.get("sequence")
        if isinstance(seq, int) and seq > self._last_inbound_sequence:
            self._last_inbound_sequence = seq
        if self.state in (S_FAILED, S_COMPLETED):
            return
        body = msg_json.get("body") or {}
        code = body.get("code")
        self.last_error = LessonError(
            code or "LESSON_ERROR", body.get("message") or "", retryable=bool(body.get("retryable"))
        )
        self._log("warning", f"inbound lesson_error code={code}")
        # A firmware-reported error on the active step fails the run (slice scope).
        if self.state in (S_RUNNING, S_PRELOADING):
            self.state = S_FAILED
            self._cancel_step_timeout()
            await self._notify_lesson_terminal("lesson_error")

    # ── state machine ──────────────────────────────────────────────────────────

    async def _on_frame_acked(self, frame: Dict[str, Any], ack_body: Dict[str, Any]) -> None:
        ftype = frame.get("type")
        if ftype == "lesson_prepare":
            # Prepare delivered -> begin the download+verify (D-PRELOAD-OWNER).
            self._preload_task = asyncio.create_task(self._run_preload())
        elif ftype == "lesson_start":
            self.state = S_RUNNING
            self._forward({"type": "lesson_started", "startedAt": _wire_timestamp()})
            await self._emit_step()
        elif ftype == "lesson_step":
            # Step delivery is confirmed ONLY by its ack (plan §5.8) -> clear timeout.
            self._cancel_step_timeout()
            self._step_acked = True
            if self._step_passive:
                # PASSIVE narration (greeting/review/focus/feedback/celebrate): the
                # firmware NEVER sends step_completed, so the ack IS the completion
                # signal — auto-advance. Without this the run would hang forever in
                # S_RUNNING (the per-step timeout is cancelled on ack, so it can no
                # longer fire either). Interactive steps still wait for step_completed.
                self._step_completed = True
            await self._maybe_finish_step()
        elif ftype == "lesson_stop":
            self.state = S_COMPLETED
            self._forward(
                {
                    "type": "lesson_completed",
                    "completedAt": _wire_timestamp(),
                    "summary": {"stepsCompleted": self._steps_completed},
                }
            )
            await self._notify_lesson_terminal("lesson_completed")

    async def _notify_lesson_terminal(self, reason: str) -> None:
        release = getattr(self.conn, "release_lesson_mode", None)
        if not callable(release):
            return
        try:
            await release(reason=reason)
        except Exception as exc:  # pragma: no cover - orchestrator release is best-effort
            self._log("warning", f"lesson terminal mode release failed: {type(exc).__name__}")

    def _alarm_preload(self, active: bool) -> None:
        """Best-effort bracket of the preload window for the S13 voice-latency alarm.
        Never raises into the lesson run (the alarm is observability, not a gate)."""
        if self._alarm is None:
            return
        try:
            self._alarm.set_preload_active(active)
        except Exception:  # pragma: no cover - alarm is best-effort
            pass

    async def _run_preload(self) -> None:
        # The "active preload window" the S13 alarm measures against is exactly the
        # download phase. Bracket only that await (the ready/start logic below is not
        # downloading); finally guarantees the window closes on every exit, incl.
        # cancellation during teardown.
        self._alarm_preload(True)
        try:
            ready = await self.asset_cache.preload()
        except LessonError as err:
            # ASSET_CHECKSUM_MISMATCH / ASSET_PROFILE_UNAVAILABLE / PRELOAD_TIMEOUT.
            self.last_error = err
            self.state = S_FAILED
            self._log("error", f"preload failed: {err.code}")
            await self._emit_error(err)
            await self._notify_lesson_terminal("preload_failed")
            return
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # pragma: no cover - unexpected
            self._log("error", f"preload crashed: {type(exc).__name__}")
            self.state = S_FAILED
            await self._notify_lesson_terminal("preload_crashed")
            return
        finally:
            self._alarm_preload(False)

        # ESP synthesizes lesson_preload_status from its OWN cache; ready is THE gate.
        status = self.asset_cache.synthesize_preload_status(self.assignment_version)
        if not ready or not status.get("ready"):
            # A non-mismatch shortfall (e.g. critical network failure) leaves it not
            # ready; lesson_start is gated below and will not fire.
            self._log("info", "preload not ready; lesson_start gated")
            return

        self.state = S_READY
        self._forward({"type": "preload_ready"})
        # Start gate satisfied -> now (and only now) emit lesson_start (seq 2).
        await self._emit("lesson_start", body={})

    async def _emit_step(self) -> None:
        # P5: advance the cursor and emit the NEXT renderable step. The first call
        # (from lesson_start ack) moves -1 -> 0; subsequent calls (from a completed
        # step) move 0 -> 1 -> ... Each emission resets the per-step ack/completed
        # latches so one step's completion can never satisfy the next.
        if self._step_index < 0 and not self._steps:
            # No renderable step in the whole manifest -> fail before any lesson_step.
            self.last_error = LessonError("LESSON_STEP_MISSING", "no model step in manifest")
            self.state = S_FAILED
            self._log("error", "no renderable model step found in manifest")
            return
        self._step_index += 1
        step = self._steps[self._step_index]
        self._step = step
        self._step_passive = _is_passive_step(step)
        self._step_id = step.get("id")
        self._step_acked = False
        self._step_completed = False
        timeout_sec = step.get("timeoutSec") or self._default_step_timeout_sec
        self._step_seq = await self._emit(
            "lesson_step", step_id=self._step_id, body=self._step_body(step)
        )
        self._start_step_timeout(self._step_seq, self._step_id, float(timeout_sec))

    async def _maybe_finish_step(self) -> None:
        if not (self.state == S_RUNNING and self._step_acked and self._step_completed):
            return
        # A step is done once it is acked AND its step_completed progress arrived
        # (plan §5.1). Count it, then either advance to the next manifest step or,
        # if this was the last one, stop with the real stepsCompleted count.
        self._steps_completed += 1
        if self._step_index + 1 < len(self._steps):
            await self._emit_step()  # next step in manifest order
        else:
            await self._emit("lesson_stop", body={"reason": "COMPLETED"})

    # ── STEP_TIMEOUT (distinct from PROTOCOL_SEQUENCE_ERROR) ─────────────────────

    def _start_step_timeout(self, step_seq: int, step_id: Optional[str], timeout_sec: float) -> None:
        async def _timeout() -> None:
            try:
                await self._sleep(timeout_sec)
            except asyncio.CancelledError:
                return
            if self._step_acked or self.state != S_RUNNING:
                return
            if self._step_passive:
                # Defensive: a passive step normally auto-advances on its ack (which
                # cancels this task). If the timer ever wins the race, an UN-acked
                # passive step is a render stall just like an interactive one — but
                # an ACKED passive step is already handled above. A passive step's
                # timeoutSec is a display DWELL, never a FAILED StepTimeout once
                # acked; the ack-absence path below still applies when truly stalled.
                pass
            # Ack-absence within timeoutSec -> STEP_TIMEOUT (RUNNING->FAILED). This is
            # a runtime stall, NOT an ordering fault — never PROTOCOL_SEQUENCE_ERROR.
            err = StepTimeout(step_id, step_seq)
            self.last_error = err
            self.state = S_FAILED
            self._log("error", f"STEP_TIMEOUT step={step_id} seq={step_seq}")
            await self._emit_error(err)

        self._step_timeout_task = asyncio.create_task(_timeout())

    def _cancel_step_timeout(self) -> None:
        if self._step_timeout_task is not None and not self._step_timeout_task.done():
            self._step_timeout_task.cancel()
        self._step_timeout_task = None

    # ── inbound sequence guard ──────────────────────────────────────────────────

    async def _accept_inbound(self, seq: Optional[int]) -> str:
        """Returns ``ok`` | ``duplicate`` | ``gap`` for the F->S envelope sequence.

        Gap (seq > last+1) -> emit ``PROTOCOL_SEQUENCE_ERROR`` (retryable) and HOLD.
        Duplicate/stale (seq <= last) -> idempotent no-op. (plan §5.8)
        """
        if seq is None:
            return "ok"
        if seq == self._last_inbound_sequence + 1:
            self._last_inbound_sequence = seq
            return "ok"
        if seq <= self._last_inbound_sequence:
            return "duplicate"
        await self._emit_error(
            ProtocolSequenceError(
                f"sequence gap: got {seq}, expected {self._last_inbound_sequence + 1}",
                context={"expected": self._last_inbound_sequence + 1, "got": seq},
            )
        )
        return "gap"

    # ── frame construction + send ───────────────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _envelope(self, frame_type: str, *, step_id: Optional[str], sequence: int, body: Dict[str, Any]) -> Dict[str, Any]:
        # The frozen §5.2 envelope, key order matching the S2 fixture. The
        # protocolVersion is the NEGOTIATED renderer version (the served
        # manifestVersion, validated by the start() gate to be in the device's
        # capability set), falling back to the v1 PROTOCOL_VERSION default. Today
        # (v1 manifest, v1 device) this stamps v1 — byte-identical to the fixture.
        return {
            "type": frame_type,
            "protocolVersion": self.negotiated_version,
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,  # integer on the wire (D-LV)
            "stepId": step_id,
            "sequence": sequence,
            "timestamp": _wire_timestamp(),
            "body": body,
        }

    async def _emit(self, frame_type: str, *, step_id: Optional[str] = None, body: Optional[Dict[str, Any]] = None) -> int:
        seq = self._next_seq()
        frame = self._envelope(frame_type, step_id=step_id, sequence=seq, body=body or {})
        # Outstanding S->F frames are correlated by THIS sequence vs inbound body.acks.
        self._outstanding[seq] = {"type": frame_type, "stepId": step_id}
        await self._send(json.dumps(frame, ensure_ascii=False))
        return seq

    async def _emit_error(self, err: LessonError) -> None:
        seq = self._next_seq()
        frame = self._envelope("lesson_error", step_id=None, sequence=seq, body=err.to_body())
        await self._send(json.dumps(frame, ensure_ascii=False))

    async def _default_send(self, payload: str) -> None:
        ws = getattr(self.conn, "websocket", None)
        if ws is not None:
            await ws.send(payload)

    # ── projections from the backend manifest ───────────────────────────────────

    def _prepare_body(self) -> Dict[str, Any]:
        return {
            "assignmentVersion": self.assignment_version,
            "profile": self.profile,
            "manifestRef": {
                "lessonId": self.lesson_id,
                "lessonVersion": self.lesson_version,
                "url": f"GET /v1/lessons/{self.lesson_id}/manifest?profile={self.profile}",
                "manifestChecksum": self.manifest_checksum,
            },
            "criticalAssets": self._critical_assets_payload(),
            "preloadTimeoutSec": int(self.asset_cache.preload_timeout_sec),
        }

    def _critical_assets_payload(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for a in self.manifest.get("assets", []):
            if not a.get("critical"):
                continue
            out.append(
                {
                    "key": a.get("id") or a.get("assetId"),
                    "role": a.get("role"),
                    "layer": a.get("layer"),
                    "mediaType": a.get("mediaType") or a.get("media_type"),
                    "path": a.get("path"),
                    "sha256": a.get("sha256"),
                    "critical": True,
                }
            )
        return out

    def _select_steps(self) -> List[Dict[str, Any]]:
        """P5: the ORDERED list of renderable steps the robot plays, in manifest
        step order. P5 replaces the slice's single-step pick — an authored lesson
        now plays ALL its steps, not just the first ``model`` step.

        Renderable == any step carrying a ``type``. The authored step kinds are the
        9 keys of the backend STEP_RENDER_MAP / render-contract.json ``stepRenderMap``:
        ``greeting``, ``review``, ``focus``, ``model``, ``listen``, ``repeat``,
        ``fillBlank``, ``feedback``, ``celebrate``. Of these, the PASSIVE narration
        kinds (``greeting``/``review``/``focus``/``feedback``/``celebrate``)
        auto-advance on their ack (no firmware step_completed), while the INTERACTIVE
        kinds (``model``/``listen``/``repeat``/``fillBlank``) wait for step_completed
        — see ``PASSIVE_STEP_TYPES`` and ``_on_frame_acked``. Steps without a ``type``
        (pure metadata rows) are skipped. Manifest order is authoritative; we never
        re-sort, so the author's sequence is the playback sequence.

        Back-compat: a manifest with a single ``model`` step yields ``[that step]``,
        byte-identical to the slice. The legacy ``s4`` fallback is retained for a
        manifest that omits ``type`` on its only step.
        """
        steps = self.manifest.get("steps", []) or []
        ordered = [s for s in steps if s.get("type")]
        if ordered:
            return ordered
        # Legacy fallback: a single typeless ``s4`` step (slice manifests).
        for s in steps:
            if s.get("id") == "s4":
                return [s]
        return []

    def _step_body(self, step: Dict[str, Any]) -> Dict[str, Any]:
        # Byte-consistent with the fixture lesson_step.body: the scene IS the frozen
        # 3-layer projection from the manifest step (back->front, no lessonUi).
        scene = self._scene_with_cached_asset_urls(step.get("scene"))
        body = {
            "assignmentVersion": self.assignment_version,
            "stepType": step.get("type"),
            "profile": self.profile,
            "timeoutSec": step.get("timeoutSec"),
            "audio": step.get("audio"),
            "scene": scene,
        }
        # Renderer-v1 additive field (NO protocol-version bump): forward the AUTHOR's
        # explicit ``completionClass`` ('passive'|'interactive') so the firmware uses
        # it as the authoritative passive/interactive classifier instead of re-deriving
        # from ``stepType`` (which MISCLASSIFIES author-defined step types -> spurious
        # step_completed / off-by-one). camelCase mirrors ``stepType``. Omitted when the
        # manifest step lacks it, keeping the wire body byte-identical to the frozen
        # fixtures (whose firmware then falls back to the v1 type-set, unchanged).
        completion_class = step.get("completionClass")
        if completion_class is not None:
            body["completionClass"] = completion_class
        return body

    def _scene_with_cached_asset_urls(self, scene: Any) -> Any:
        if scene is None:
            return None
        rewritten = copy.deepcopy(scene)
        self._rewrite_cached_asset_sources(rewritten)
        return rewritten

    def _rewrite_cached_asset_sources(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key == "src" and isinstance(child, str):
                    resolver = getattr(self.asset_cache, "public_url_for_source", None)
                    if callable(resolver):
                        cached = resolver(child)
                        if cached:
                            value[key] = cached
                            continue
                self._rewrite_cached_asset_sources(child)
        elif isinstance(value, list):
            for child in value:
                self._rewrite_cached_asset_sources(child)

    # ── progress forward (own dispatch path) ────────────────────────────────────

    def _forward(self, event: Dict[str, Any]) -> None:
        if self.forwarder is None:
            return
        clean = {k: v for k, v in event.items() if v is not None}
        batch = {
            "assignmentId": self.assignment_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,
            "sessionId": self.session_id,
            "events": [clean],
        }
        self.forwarder.enqueue(batch)

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger.bind(tag=TAG), level)(message)
        except Exception:
            pass


async def maybe_start_lesson_on_connect(conn: Any) -> Optional[LessonRuntime]:
    """Serialize concurrent lesson pulls (connect-time pull + spoken start_lesson) so
    they cannot create two runtimes / emit duplicate lesson_prepare (deep-audit). The
    per-connection lock is lazily created; the lazy-init is atomic under asyncio (no
    await between the getattr and the assignment), so two schedulers racing here both
    end up using the same lock, then run the impl serially — the loser re-reads
    conn.lesson_runtime and returns the winner's session instead of duplicating it."""
    lock = getattr(conn, "_lesson_pull_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        conn._lesson_pull_lock = lock
    async with lock:
        return await _maybe_start_lesson_on_connect_impl(conn)


async def _maybe_start_lesson_on_connect_impl(conn: Any) -> Optional[LessonRuntime]:
    """S6 pull-on-connect glue (authoritative hand-off, ADR 0013 §A/§B).

    Gated by the dark-rollout flag. Fetches the device's current assignment + the
    espTft manifest from ``server.api_url`` (the Nest backend), wires the runtime,
    and sends ``lesson_prepare``. Any failure is swallowed (logged) — the lesson
    layer must NEVER break the connection or the voice path.
    """
    config = getattr(conn, "config", {}) or {}
    _set_lesson_start_status(conn, "CHECKING_ASSIGNMENT")
    lesson_cfg = config.get("lesson", {}) or {}
    server_cfg = config.get("server", {}) or {}
    base_url = lesson_cfg.get("api_base") or server_cfg.get("api_url")
    device_id = getattr(conn, "device_id", None)
    logger = getattr(conn, "logger", None)

    def _log(level: str, message: str) -> None:
        if logger is None:
            return
        try:
            getattr(logger.bind(tag=TAG), level)(message)
        except Exception:
            pass

    if not base_url or not device_id:
        _set_lesson_start_status(conn, "LESSON_CONFIG_MISSING", "Robot chưa kết nối được máy chủ bài học.")
        _log("info", "lesson pull-on-connect skipped: no api_base or device_id")
        return None

    token = lesson_cfg.get("device_token")  # D-RUNTOKEN: optional, ops/backend follow-up.

    try:
        import httpx
        from config import manage_api_client as backend_api
    except Exception as exc:  # pragma: no cover
        _log("warning", f"lesson pull-on-connect unavailable: {exc}")
        return None

    from core.lesson.errors import lesson_capability_ok as _cap_ok
    from core.lesson.errors import device_renderer_capabilities as _device_caps
    from core.lesson.asset_cache import AssetCache
    from core.lesson.forwarder import LessonEventForwarder

    # Wait briefly for hello/features so the capability gate is meaningful.
    for _ in range(50):
        if getattr(conn, "features", None) is not None:
            break
        await asyncio.sleep(0.1)
    if not _cap_ok(getattr(conn, "features", None)):
        _set_lesson_start_status(conn, "LESSON_CAPABILITY_MISSING", "Robot chưa sẵn sàng hiển thị bài học.")
        _log("info", "device lacks lesson capability; pull-on-connect no-op")
        return None

    # L3 P3 — the device's advertised renderer-capability set (v1-only for every
    # current firmware). Forwarded to the manifest fetch so the backend serves a
    # manifest this device can render. The runtime re-derives the same set from
    # conn.features for its start() gate; computing it here keeps the fetch honest.
    renderer_capabilities = _device_caps(getattr(conn, "features", None))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=True,
    ) as client:
        # D-RUNTOKEN bridge: resolve the robot's Wi-Fi MAC -> backend device UUID
        # + a short-lived device-scoped JWT so the assignment pull authenticates as
        # the device. A mint failure is surfaced locally and the pull is skipped;
        # a legacy MAC/no-token request only creates a swallowed backend 401.
        backend_device_id = device_id
        try:
            from config.device_token_client import resolve_device_identity

            minted_uuid, minted_token = await resolve_device_identity(
                client, base_url, device_id, logger=logger
            )
            if minted_uuid and minted_token:
                backend_device_id = minted_uuid
                token = minted_token
            else:
                _set_lesson_start_status(conn, "DEVICE_TOKEN_UNAVAILABLE", "Robot chưa xác thực được với máy chủ bài học.")
                _log("warning", "device token required for lesson pull; skipping tokenless request")
                return None
        except Exception as exc:  # pragma: no cover - bridge is best-effort
            _log("warning", f"device-token mint unavailable: {type(exc).__name__}: {exc}")
            return None
        assignment = await backend_api.get_current_assignment(client, base_url, backend_device_id, token=token)
        if not assignment:
            _set_lesson_start_status(conn, "NO_CURRENT_ASSIGNMENT", NO_CURRENT_ASSIGNMENT_MESSAGE)
            _log("info", "no current assignment for device; nothing to preload")
            return None
        if assignment.get("state") in ("COMPLETED", "CANCELLED", "FAILED"):
            _set_lesson_start_status(conn, "ASSIGNMENT_TERMINAL", "Bài học này đã kết thúc.")
            _log("info", f"assignment in terminal state {assignment.get('state')}; skipping")
            return None
        assignment_id = assignment.get("assignmentId")
        if isinstance(assignment_id, str) and assignment_id:
            try:
                from core.lesson.forwarder import replay_stored_terminal_event

                replayed_terminal = await replay_stored_terminal_event(
                    device_id=backend_device_id,
                    assignment_id=assignment_id,
                    base_url=base_url,
                    token=token,
                    client=client,
                    logger=logger,
                )
            except Exception as exc:  # pragma: no cover - replay is best-effort
                _log("warning", f"stored terminal lesson event replay failed: {type(exc).__name__}")
                replayed_terminal = False
            if replayed_terminal:
                _log("info", "replayed pending terminal lesson event; skipping lesson restart")
                return None
        profile = assignment.get("profile", "espTft")
        manifest, etag = await backend_api.get_lesson_manifest(
            client,
            base_url,
            assignment.get("lessonId"),
            profile,
            token=token,
            renderer_capabilities=renderer_capabilities,
        )

    if not manifest:
        _set_lesson_start_status(conn, "MANIFEST_EMPTY", "Robot chưa tải được nội dung bài học.")
        _log("warning", "manifest fetch returned empty; aborting lesson start")
        return None

    # ── P5 republish-on-connect (no reconnect required) ─────────────────────────
    # If a runtime is already pinned for THIS device, compare the freshly-pulled
    # assignment's (lessonVersion, assignmentVersion) to the live runtime. Unchanged
    # -> keep the existing session (idempotent no-op). Changed -> the author
    # republished; tear down the stale version's cache + runtime and re-pull the new
    # manifest in place. The whole re-pull is GUARDED on is_realtime_busy so it never
    # interrupts an active voice turn — we simply defer until the next connect/poll.
    new_lesson_version = int(assignment.get("lessonVersion", 1))
    new_assignment_version = int(assignment.get("assignmentVersion", 1))
    existing = getattr(conn, "lesson_runtime", None)
    if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
        unchanged = (
            existing.lesson_version == new_lesson_version
            and existing.assignment_version == new_assignment_version
        )
        if unchanged:
            replay = getattr(existing, "replay_pending_terminal_event", None)
            if getattr(existing, "state", None) in (S_COMPLETED, S_FAILED) and callable(replay):
                try:
                    await replay()
                except Exception as exc:  # pragma: no cover - replay is best-effort
                    _log("warning", f"terminal lesson event replay failed: {type(exc).__name__}")
            _log("info", "lesson republish-on-connect: version unchanged; keeping session")
            return existing
        busy_check = getattr(conn, "is_realtime_busy", None)
        if callable(busy_check):
            try:
                if busy_check():
                    _log("info", "lesson republish deferred: realtime voice busy")
                    return existing
            except Exception:  # pragma: no cover - busy_check is best-effort
                pass
        _log(
            "info",
            "lesson republish-on-connect: version changed "
            f"v{existing.lesson_version}/a{existing.assignment_version} -> "
            f"v{new_lesson_version}/a{new_assignment_version}; tearing down + re-pulling",
        )
        old_cache = getattr(existing, "asset_cache", None)
        try:
            # Republish EVICTS the old version's bytes (disjoint version-scoped dir),
            # unlike a plain reconnect close() which keeps verified bytes for
            # re-attest. evict() reuses aclose() for client teardown.
            if old_cache is not None and hasattr(old_cache, "evict"):
                await old_cache.evict()
            await existing.close()  # forwarder + (idempotent) asset client teardown
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"old lesson runtime teardown failed: {type(exc).__name__}")
        conn.lesson_runtime = None
        # fall through to a fresh pull of the republished version below.

    asset_cache = AssetCache(
        assets=[
            {
                "key": a.get("id") or a.get("assetId"),
                "path": a.get("path"),
                "url": a.get("url"),
                "sha256": a.get("sha256"),
                "critical": a.get("critical"),
                "layer": a.get("layer"),
                "role": a.get("role"),
                "mediaType": a.get("mediaType") or a.get("media_type"),
            }
            for a in manifest.get("assets", [])
        ],
        profile=profile,
        asset_origin_base=lesson_cfg.get("asset_origin_base"),
        public_base_url=lesson_asset_public_base_url(config),
        lesson_key=str(assignment.get("lessonId") or "lesson"),
        lesson_version=int(assignment.get("lessonVersion", 1)),
        manifest_checksum=parse_manifest_checksum(etag),
        preload_timeout_sec=float(lesson_cfg.get("preload_timeout_sec", 90)),
        concurrency=int(lesson_cfg.get("preload_concurrency", 2)),
        busy_check=getattr(conn, "is_realtime_busy", None),
        logger=logger,
    )
    forwarder = LessonEventForwarder(
        device_id=backend_device_id, base_url=base_url, token=token, logger=logger
    )
    # S13 voice-latency-during-preload auto-disable alarm (plan §11.2 / CP-8). One
    # alarm per connection, reused across runtimes so its sample window survives a
    # re-pull. The disable callback flips the ESP LESSON_RUNTIME_ENABLED flag off.
    alarm = getattr(conn, "lesson_voice_alarm", None)
    if alarm is None:
        try:
            from core.lesson.preload_voice_alarm import PreloadVoiceLatencyAlarm

            alarm = PreloadVoiceLatencyAlarm(
                disable_callback=getattr(conn, "_disable_lesson_runtime", None),
                threshold_ms=lesson_cfg.get("voice_rt_p95_disable_ms"),
                logger=logger,
            )
            conn.lesson_voice_alarm = alarm
        except Exception as exc:  # pragma: no cover - alarm is best-effort
            _log("warning", f"voice-latency alarm unavailable: {type(exc).__name__}")
            alarm = None
    runtime = LessonRuntime(
        conn,
        assignment=assignment,
        manifest=manifest,
        asset_cache=asset_cache,
        forwarder=forwarder,
        manifest_checksum=parse_manifest_checksum(etag),
        alarm=alarm,
    )
    # Close any prior runtime for a DIFFERENT assignment before replacing it, so its
    # forwarder worker task + asset httpx client are not leaked (deep-audit #7). The
    # same-assignment republish path above already closed + nulled it (prior is None
    # there); this catches the new-assignment case that fell straight through.
    prior = getattr(conn, "lesson_runtime", None)
    if prior is not None and prior is not runtime:
        try:
            await prior.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"prior lesson runtime teardown failed: {type(exc).__name__}")
    conn.lesson_runtime = runtime
    try:
        enter_lesson = getattr(conn, "enter_lesson_mode", None)
        if callable(enter_lesson):
            await enter_lesson(reason="lesson_start")
        await runtime.start()
        _set_lesson_start_status(conn, "STARTED")
    except LessonError as err:
        _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
        _log("warning", f"lesson start refused: {err.code}")
        release_lesson = getattr(conn, "release_lesson_mode", None)
        if callable(release_lesson):
            await release_lesson(reason="lesson_start_refused")
    return runtime
