#!/usr/bin/env python3
"""Bounded software resource soak for Course Mode runtime and SD cache paths."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import resource
import sys
import tempfile
import threading
import types
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

import websockets  # noqa: E402

from core.lesson.course_orchestrator import CourseDecision, SessionState  # noqa: E402
from core.lesson.embodied_intent import EmbodiedIntent  # noqa: E402
from core.lesson.forwarder import LessonEventForwarder  # noqa: E402
from core.lesson.runtime import (  # noqa: E402
    CourseModeRuntimeAdapter,
    LessonRuntime,
    course_mode_runtime_from_manifest,
)
from core.lesson.sd_pack_gc import SdPackActivationState, SdPackGarbageCollector  # noqa: E402
from core.lesson.shared_asset_store import SharedAssetStore  # noqa: E402

SCHEMA_VERSION = "course-mode-resource-soak.v1"
CONTRACT_FIXTURE = SERVER_ROOT / "tests" / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.json"
LIMITS = {
    "rssDeltaBytes": 32 * 1024 * 1024,
    "fdDelta": 8,
    "asyncioTaskDelta": 4,
    "threadDelta": 4,
    "cacheDeltaBytes": 4 * 1024,
    "rssSlopeBytesPerSample": 1024 * 1024,
    "fdSlopePerSample": 0.25,
    "asyncioTaskSlopePerSample": 0.25,
    "threadSlopePerSample": 0.25,
    "cacheSlopeBytesPerSample": 4 * 1024,
}

SampleInjector = Callable[[int, dict[str, Any]], dict[str, Any]]
Sleeper = Callable[[float], Awaitable[Any]]


@dataclass(frozen=True)
class ResourceSoakConfig:
    cycles: int = 52
    ws_reconnects: int = 100
    sd_cycles: int = 10
    idle_mode: str = "virtual"
    idle_seconds: float = 3600.0

    def __post_init__(self) -> None:
        for name in ("cycles", "ws_reconnects", "sd_cycles"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.idle_mode not in {"none", "virtual", "real"}:
            raise ValueError("idle_mode must be none, virtual, or real")
        if not isinstance(self.idle_seconds, (int, float)) or self.idle_seconds < 0:
            raise ValueError("idle_seconds must be non-negative")


class _VirtualClock:
    def __init__(self) -> None:
        self.monotonic_seconds = 0.0
        self.wall_seconds = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def wall(self) -> float:
        return self.wall_seconds

    def advance(self, seconds: float) -> None:
        self.monotonic_seconds += seconds
        self.wall_seconds += seconds


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=_positive_int, default=52)
    parser.add_argument("--ws-reconnects", type=_positive_int, default=100)
    parser.add_argument("--sd-cycles", type=_positive_int, default=10)
    parser.add_argument("--idle-mode", choices=("none", "virtual", "real"), default="virtual")
    parser.add_argument("--idle-seconds", type=_non_negative_float, default=3600.0)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def monotonic_growth_slope(values: Sequence[int | float]) -> float:
    """Return the non-negative least-squares trend across noisy samples."""
    if len(values) < 2:
        return 0.0
    midpoint = (len(values) - 1) / 2
    denominator = sum((index - midpoint) ** 2 for index in range(len(values)))
    if denominator == 0:
        return 0.0
    mean = sum(float(value) for value in values) / len(values)
    slope = sum(
        (index - midpoint) * (float(value) - mean)
        for index, value in enumerate(values)
    ) / denominator
    return max(0.0, slope)


def _process_rss_bytes() -> int:
    if sys.platform == "darwin":
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(usage)
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, AttributeError, OSError):
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(usage if sys.platform == "darwin" else usage * 1024)


def _fd_count() -> int:
    if sys.platform == "darwin":
        try:
            return len(list(Path("/dev/fd").iterdir()))
        except OSError:
            return 0
    try:
        import psutil

        counter = getattr(psutil.Process(), "num_fds", None)
        if counter is not None:
            return int(counter())
    except (ImportError, AttributeError, OSError):
        pass
    for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return len(list(directory.iterdir()))
        except OSError:
            continue
    return 0


def _sample_resources(index: int, phase: str, cache_bytes: int) -> dict[str, Any]:
    return {
        "index": index,
        "phase": phase,
        "rssBytes": _process_rss_bytes(),
        "fdCount": _fd_count(),
        "asyncioTaskCount": len(asyncio.all_tasks()),
        "threadCount": threading.active_count(),
        "cacheBytes": cache_bytes,
    }


def _metric_values(samples: Sequence[dict[str, Any]], field: str) -> list[int | float]:
    return [sample[field] for sample in samples]


def bounded_verdict(
    samples: Sequence[dict[str, Any]], *, workload_failures: Sequence[dict[str, str]]
) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one resource sample is required")
    fields = {
        "rss": "rssBytes",
        "fd": "fdCount",
        "task": "asyncioTaskCount",
        "thread": "threadCount",
        "cache": "cacheBytes",
    }
    slopes = {
        "rssBytesPerSample": monotonic_growth_slope(_metric_values(samples, fields["rss"])),
        "fdCountPerSample": monotonic_growth_slope(_metric_values(samples, fields["fd"])),
        "asyncioTaskCountPerSample": monotonic_growth_slope(_metric_values(samples, fields["task"])),
        "threadCountPerSample": monotonic_growth_slope(_metric_values(samples, fields["thread"])),
        "cacheBytesPerSample": monotonic_growth_slope(_metric_values(samples, fields["cache"])),
    }
    deltas = {
        "rssBytes": max(0, samples[-1]["rssBytes"] - samples[0]["rssBytes"]),
        "fdCount": max(0, samples[-1]["fdCount"] - samples[0]["fdCount"]),
        "asyncioTaskCount": max(0, samples[-1]["asyncioTaskCount"] - samples[0]["asyncioTaskCount"]),
        "threadCount": max(0, samples[-1]["threadCount"] - samples[0]["threadCount"]),
        "cacheBytes": max(0, samples[-1]["cacheBytes"] - samples[0]["cacheBytes"]),
    }
    checks = {
        "workloadCompleted": not workload_failures,
        "rssDeltaBounded": deltas["rssBytes"] <= LIMITS["rssDeltaBytes"],
        "fdDeltaBounded": deltas["fdCount"] <= LIMITS["fdDelta"],
        "taskDeltaBounded": deltas["asyncioTaskCount"] <= LIMITS["asyncioTaskDelta"],
        "threadDeltaBounded": deltas["threadCount"] <= LIMITS["threadDelta"],
        "cacheDeltaBounded": deltas["cacheBytes"] <= LIMITS["cacheDeltaBytes"],
        "rssSlopeBounded": slopes["rssBytesPerSample"] <= LIMITS["rssSlopeBytesPerSample"],
        "fdSlopeBounded": slopes["fdCountPerSample"] <= LIMITS["fdSlopePerSample"],
        "taskSlopeBounded": slopes["asyncioTaskCountPerSample"] <= LIMITS["asyncioTaskSlopePerSample"],
        "threadSlopeBounded": slopes["threadCountPerSample"] <= LIMITS["threadSlopePerSample"],
        "cacheSlopeBounded": slopes["cacheBytesPerSample"] <= LIMITS["cacheSlopeBytesPerSample"],
    }
    failures = list(workload_failures)
    failure_codes = (
        ("rssDeltaBounded", "RSS_DELTA_EXCEEDED"),
        ("fdDeltaBounded", "FD_DELTA_EXCEEDED"),
        ("taskDeltaBounded", "ASYNCIO_TASK_DELTA_EXCEEDED"),
        ("threadDeltaBounded", "THREAD_DELTA_EXCEEDED"),
        ("cacheDeltaBounded", "CACHE_DELTA_EXCEEDED"),
        ("rssSlopeBounded", "RSS_SLOPE_EXCEEDED"),
        ("fdSlopeBounded", "FD_SLOPE_EXCEEDED"),
        ("taskSlopeBounded", "ASYNCIO_TASK_SLOPE_EXCEEDED"),
        ("threadSlopeBounded", "THREAD_SLOPE_EXCEEDED"),
        ("cacheSlopeBounded", "CACHE_SLOPE_EXCEEDED"),
    )
    failures.extend(
        {"code": code, "phase": "resource_verdict"}
        for check, code in failure_codes
        if not checks[check]
    )
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failures": failures,
        "deltas": deltas,
        "slopes": slopes,
        "limits": dict(LIMITS),
    }


def _load_contract_manifest() -> dict[str, Any]:
    return {"courseModeContract": json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))}


def _new_runtime(
    manifest: dict[str, Any], session_id: str, *, clock: Callable[[], float], wall_clock: Callable[[], float]
) -> CourseModeRuntimeAdapter:
    runtime = course_mode_runtime_from_manifest(
        manifest,
        enabled=True,
        clock=clock,
        wall_clock=wall_clock,
        runtime_session_id=session_id,
        assignment_id="synthetic-resource-soak",
    )
    if runtime is None:
        raise RuntimeError("course mode runtime did not initialize")
    return runtime


async def _exercise_lesson_runtime(
    manifest: dict[str, Any], cycle: int, retry_counts: dict[str, int]
) -> str:
    clock = _VirtualClock()
    session_id = f"synthetic-soak-lesson-{cycle:03d}"
    runtime = _new_runtime(manifest, session_id, clock=clock.monotonic, wall_clock=clock.wall)
    runtime.start_course_budget()

    async def settle(decision: dict[str, Any], operation_id: str) -> None:
        if decision.get("accepted") is not True:
            raise RuntimeError("course mode lesson decision was rejected")
        if runtime.tool_context().get("pendingDecision") is None:
            return
        identity = runtime.tool_context()["identity"]
        arguments = {
            "lessonSessionId": identity["lessonSessionId"],
            "turnSequenceId": identity["turnSequenceId"],
            "observationId": f"{operation_id}-plan",
            "planId": f"plan-{operation_id}",
            "decisionId": decision["decisionId"],
            "acknowledgment": "Robot heard you.",
            "relation": "We can keep learning.",
            "guidance": "Look at the picture.",
            "invitation": "Ready?",
            "questionCount": 1,
            "embodiedIntent": decision["embodiedIntent"],
            "targetFactsUsed": [runtime.orchestrator.active_target_id],
            "praiseLevel": "engagement",
            "safetyMode": False,
            "normalMiss": False,
        }
        applied = await runtime.course_apply_response_plan(arguments)
        if applied.get("accepted") is not True:
            raise RuntimeError("course mode response plan was rejected")
        if not runtime.mark_response_plan_delivery_attempted(arguments):
            raise RuntimeError("course mode response plan delivery was not marked")
        if not runtime.commit_course_response_plan(arguments):
            raise RuntimeError("course mode response plan was not committed")

    async def continue_course(operation_id: str) -> dict[str, Any]:
        identity = runtime.tool_context()["identity"]
        decision = await runtime.course_continue({
            "lessonSessionId": identity["lessonSessionId"],
            "turnSequenceId": identity["turnSequenceId"],
            "observationId": operation_id,
        })
        await settle(decision, operation_id)
        return decision

    async def observe(
        operation_id: str, activity_id: str, context_id: str,
        semantic_class: str, speech_class: str,
    ) -> dict[str, Any]:
        identity = runtime.tool_context()["identity"]
        decision = await runtime.course_observe_child({
            "lessonSessionId": identity["lessonSessionId"],
            "turnSequenceId": identity["turnSequenceId"],
            "observationId": operation_id,
            "semanticClass": semantic_class,
            "speechClass": speech_class,
            "language": "vi" if semantic_class == "meaning_vi" else "en",
            "intent": "answer",
            "engagement": "engaged",
            "safetyClass": "normal",
            "assessmentEligible": True,
            "confidenceBand": "high",
            "activityId": activity_id,
            "contextId": context_id,
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        })
        await settle(decision, operation_id)
        return decision

    for opening in range(1, 4):
        await continue_course(f"synthetic-{cycle:03d}-opening-{opening}")
    clock.advance(5)
    await observe(
        f"synthetic-{cycle:03d}-meaning", "cat-meaning-left-right-01",
        "cat_dog_visual_contrast", "meaning_vi", "not_applicable",
    )
    await observe(
        f"synthetic-{cycle:03d}-model", "cat-recall-visual-02",
        "cat_primary_visual_recall", "unknown", "silence",
    )
    await continue_course(f"synthetic-{cycle:03d}-intervening")
    clock.advance(25)
    await observe(
        f"synthetic-{cycle:03d}-recall", "cat-recall-visual-02",
        "cat_primary_visual_recall", "target_en", "exact",
    )
    clock.advance(10)
    await observe(
        f"synthetic-{cycle:03d}-transfer", "cat-transfer-scene-01",
        "cat_second_visual_scene", "target_en", "exact",
    )
    clock.advance(30)
    delayed = await observe(
        f"synthetic-{cycle:03d}-delayed", "cat-delayed-recall-01",
        "cat_delayed_callback", "target_en", "exact",
    )
    if delayed.get("evidenceEvent", {}).get("evidenceLevel") != "MASTERED_TODAY":
        raise RuntimeError("course mode lesson did not reach mastery")
    await continue_course(f"synthetic-{cycle:03d}-secondary")
    await continue_course(f"synthetic-{cycle:03d}-closing")
    terminal_state = runtime.orchestrator.session_state.value
    if terminal_state not in {"CLOSING", "COMPLETE"}:
        raise RuntimeError("course mode lesson simulation did not terminate")
    snapshot = runtime.durable_snapshot()
    restored = course_mode_runtime_from_manifest(
        manifest,
        enabled=True,
        clock=clock.monotonic,
        wall_clock=clock.wall,
        runtime_session_id=session_id,
        assignment_id="synthetic-resource-soak",
        authoritative_snapshot=snapshot,
    )
    if (
        restored is None
        or restored.orchestrator.snapshot() != runtime.orchestrator.snapshot()
        or restored.orchestrator.session_state.value != terminal_state
    ):
        retry_counts["runtimeRestore"] += 1
        raise RuntimeError("course mode runtime snapshot restore diverged")
    return terminal_state


def _prepare_sd_cache(root: Path) -> tuple[SharedAssetStore, SdPackGarbageCollector, str, str]:
    store = SharedAssetStore(root / "sd-card")
    content = b"synthetic-course-mode-soak-asset"
    digest = hashlib.sha256(content).hexdigest()
    store.put_bytes(content, digest)
    current_key = f"course-mode-soak/v1-{digest}"
    stale_key = f"course-mode-soak/v0-{digest}"
    store.commit_pack(current_key, {"synthetic.bin": digest})
    activation = SdPackActivationState(
        store,
        current={"cacheKey": current_key, "lessonVersion": 1, "manifestChecksum": digest},
    )
    collector = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: type("Usage", (), {"total": 100, "used": 50, "free": 50})(),
        protected_keys_provider=lambda: {activation.current_cache_key},
    )
    return store, collector, stale_key, digest


def _exercise_sd_cycle(
    store: SharedAssetStore,
    collector: SdPackGarbageCollector,
    stale_key: str,
    digest: str,
    retry_counts: dict[str, int],
) -> None:
    store.commit_pack(stale_key, {"synthetic.bin": digest})
    result = collector.collect_one()
    if result.get("deleted") != stale_key or store.is_pack_ready(stale_key):
        retry_counts["sdCacheGc"] += 1
        raise RuntimeError("SD cache GC did not delete the stale synthetic pack")


async def _exercise_idle(
    manifest: dict[str, Any], config: ResourceSoakConfig, sleeper: Sleeper
) -> None:
    if config.idle_mode == "none":
        return
    clock = _VirtualClock()
    session_id = "synthetic-soak-idle-session"
    runtime = _new_runtime(manifest, session_id, clock=clock.monotonic, wall_clock=clock.wall)
    snapshot = runtime.durable_snapshot()
    if config.idle_mode == "real":
        await sleeper(float(config.idle_seconds))
    else:
        await sleeper(0)
    clock.advance(float(config.idle_seconds))
    restored = course_mode_runtime_from_manifest(
        manifest,
        enabled=True,
        clock=clock.monotonic,
        wall_clock=clock.wall,
        runtime_session_id=session_id,
        assignment_id="synthetic-resource-soak",
        authoritative_snapshot=snapshot,
    )
    if restored is None or restored.lesson_session_id != session_id:
        raise RuntimeError("idle snapshot restore failed")


class _SoakAssetCache:
    async def aclose(self) -> None:
        return None


class _SoakTerminalStore:
    def __init__(self) -> None:
        self.batches: dict[tuple[str, str], dict[str, Any]] = {}
        self.stores = 0
        self.clears = 0

    async def store(self, device_id: str, batch: dict[str, Any]) -> None:
        self.stores += 1
        self.batches[(device_id, batch["assignmentId"])] = batch

    async def load(self, device_id: str, assignment_id: str) -> dict[str, Any] | None:
        return self.batches.get((device_id, assignment_id))

    async def clear(self, device_id: str, batch: dict[str, Any]) -> None:
        events = batch.get("events")
        if isinstance(events, list) and any(
            isinstance(event, dict) and event.get("type") == "lesson_completed"
            for event in events
        ):
            self.clears += 1
        self.batches.pop((device_id, batch["assignmentId"]), None)


async def _exercise_websocket_reconnects(
    manifest: dict[str, Any], reconnects: int, retry_counts: dict[str, int],
    totals: dict[str, int], sample: Callable[[str], None],
) -> None:
    session_id = "synthetic-soak-ws-session"
    seed = _new_runtime(manifest, session_id, clock=lambda: 0.0, wall_clock=lambda: 1_800_000_000.0)
    seed.start_course_budget()
    seed.orchestrator.session_state = SessionState.WORD_ACTIVE
    decision = CourseDecision(
        "synthetic-reconnect-decision", True, SessionState.WORD_ACTIVE,
        "RETRY_ACTIVITY", "acknowledge_child", None, "invite_retry",
        EmbodiedIntent.ENCOURAGE_SMALL, False, None,
        activity_id="cat-recall-visual-02", visual_state="retry", attempt=1,
    )
    seed._decisions[decision.decision_id] = decision
    seed._pending_activity_decision_ids = [decision.decision_id]
    seed._activity_delivery_ids[decision.decision_id] = "synthetic-reconnect-delivery"
    snapshot = seed.durable_snapshot()
    terminal_store = _SoakTerminalStore()
    post_count = 0
    active_sessions: set[int] = set()
    completed: asyncio.Queue[tuple[int, Exception | None]] = asyncio.Queue()
    initial_terminal = {
        "assignmentId": "synthetic-resource-soak", "sessionId": session_id,
        "events": [{"type": "lesson_completed"}],
    }
    await terminal_store.store("synthetic-soak-device", initial_terminal)

    async def post_event(_client, _base_url, _device_id, _batch, *, token=None):
        nonlocal post_count
        post_count += 1
        return {"accepted": 1}

    async def handle_socket(websocket) -> None:
        index = -1
        runtime = None
        forwarder = None
        error: Exception | None = None
        forwarder = LessonEventForwarder(
            device_id="synthetic-soak-device", base_url="http://loopback.invalid/v1",
            post_fn=post_event, retry_backoff_sec=0, terminal_store=terminal_store,
        )
        conn = types.SimpleNamespace(
            config={"lesson": {"course_mode_v2_enabled": True}}, device_id="synthetic-soak-device",
            logger=None, features=None, headers={}, session_id="synthetic-connection",
            client_is_speaking=False, google_live_lesson_prompt_output_allowed=False,
            google_live_echo_suppress_until=0.0, lesson_runtime=None,
            lesson_runtime_candidate=None,
        )
        try:
            request = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2.0))
            index = request.get("index")
            if type(index) is not int or index in active_sessions:
                raise RuntimeError("invalid synthetic reconnect identity")
            active_sessions.add(index)
            totals["wsConnectionsOpened"] += 1
            runtime = LessonRuntime(
                conn,
                assignment={"assignmentId": "synthetic-resource-soak", "lessonId": "synthetic"},
                manifest=manifest, asset_cache=_SoakAssetCache(), forwarder=forwarder,
                send=websocket.send, course_mode_snapshot=snapshot,
            )
            conn.lesson_runtime = runtime
            await runtime._deliver_pending_course_activity_frames()
            pending_terminal = await terminal_store.load(
                "synthetic-soak-device", "synthetic-resource-soak",
            )
            if pending_terminal is None:
                raise RuntimeError("terminal outbox did not survive reconnect")
            forwarder.pending_terminal_batch = pending_terminal
            if not await runtime.replay_pending_terminal_event():
                raise RuntimeError("terminal outbox replay failed")
            totals["terminalOutboxReplays"] += 1
            forwarder.enqueue({
                "assignmentId": runtime.assignment_id, "sessionId": runtime.session_id,
                "events": [{"type": "step_completed", "sequence": 1}],
            })
            if forwarder._worker is None:
                raise RuntimeError("lesson forwarder worker did not start")
            totals["forwarderWorkersStarted"] += 1
            await forwarder.drain()
            await forwarder.aclose()
            if index < reconnects - 1:
                if await runtime._forward_terminal({"type": "lesson_completed"}):
                    raise RuntimeError("closed forwarder unexpectedly accepted terminal event")
                totals["terminalOutboxCarryovers"] += 1
            if (
                forwarder.pending_terminal_batch is not None
                or bool(terminal_store.batches) != (index < reconnects - 1)
            ):
                raise RuntimeError("terminal outbox did not clear after forward")
            totals["lessonRuntimeReconnects"] += 1
        except Exception as exc:
            error = exc
        finally:
            if runtime is not None:
                await runtime.close()
                totals["lessonRuntimeClosures"] += 1
            elif forwarder is not None:
                await forwarder.aclose()
            if forwarder is not None and forwarder._worker is None:
                totals["forwarderWorkersClosed"] += 1
            if index in active_sessions:
                active_sessions.remove(index)
            if index >= 0:
                totals["wsConnectionsClosed"] += 1
            await completed.put((index, error))

    async with websockets.serve(handle_socket, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}/course-mode-soak"
        for index in range(reconnects):
            try:
                async with websockets.connect(uri) as websocket:
                    await websocket.send(json.dumps({"index": index}))
                    frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2.0))
                    if (
                        frame.get("type") != "lesson_course_activity"
                        or frame.get("sessionId") != session_id
                    ):
                        raise RuntimeError("course mode reconnect frame identity diverged")
                completed_index, error = await asyncio.wait_for(completed.get(), timeout=2.0)
                if completed_index != index or error is not None:
                    raise error or RuntimeError("course mode reconnect teardown diverged")
            except Exception:
                retry_counts["wsReconnect"] += 1
                raise
            totals["wsReconnects"] += 1
            sample("wsReconnect")
    totals["forwarderPosts"] = post_count
    totals["terminalOutboxStores"] = terminal_store.stores
    totals["terminalOutboxClears"] = terminal_store.clears
    totals["terminalOutboxPending"] = len(terminal_store.batches)
    totals["stuckSessions"] = len(active_sessions)
    if post_count != reconnects * 2:
        raise RuntimeError("lesson forwarder reconnect posts diverged")
    if totals["wsConnectionsOpened"] != reconnects or totals["wsConnectionsClosed"] != reconnects:
        raise RuntimeError("loopback WebSocket connection count diverged")
    if active_sessions or terminal_store.batches:
        raise RuntimeError("reconnect soak left stuck session or terminal outbox state")


async def run_resource_soak(
    config: ResourceSoakConfig,
    *,
    work_root: Path | None = None,
    sample_injector: SampleInjector | None = None,
    sleeper: Sleeper = asyncio.sleep,
) -> dict[str, Any]:
    temporary = tempfile.TemporaryDirectory(prefix="tbot-course-mode-resource-soak-") if work_root is None else None
    root = Path(temporary.name if temporary is not None else work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = _load_contract_manifest()
    store, collector, stale_key, digest = _prepare_sd_cache(root)
    samples: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    retry_counts = {"runtimeRestore": 0, "wsReconnect": 0, "sdCacheGc": 0}
    totals = {
        "lessonSimulations": 0,
        "terminalLessonSimulations": 0,
        "runtimeSnapshotRestores": 0,
        "wsReconnects": 0,
        "wsConnectionsOpened": 0,
        "wsConnectionsClosed": 0,
        "lessonRuntimeReconnects": 0,
        "lessonRuntimeClosures": 0,
        "forwarderWorkersStarted": 0,
        "forwarderWorkersClosed": 0,
        "forwarderPosts": 0,
        "terminalOutboxReplays": 0,
        "terminalOutboxCarryovers": 0,
        "terminalOutboxStores": 0,
        "terminalOutboxClears": 0,
        "terminalOutboxPending": 0,
        "stuckSessions": 0,
        "sdCacheCycles": 0,
        "sdGcDeletes": 0,
    }

    def sample(phase: str) -> None:
        value = _sample_resources(len(samples), phase, collector.physical_usage_bytes())
        if sample_injector is not None:
            value = sample_injector(len(samples), value)
        samples.append(value)

    sample("baseline")
    try:
        for cycle in range(1, config.cycles + 1):
            await _exercise_lesson_runtime(manifest, cycle, retry_counts)
            totals["lessonSimulations"] += 1
            totals["terminalLessonSimulations"] += 1
            totals["runtimeSnapshotRestores"] += 1
            sample("lesson")

        await _exercise_websocket_reconnects(
            manifest, config.ws_reconnects, retry_counts, totals, sample,
        )

        for _index in range(config.sd_cycles):
            _exercise_sd_cycle(store, collector, stale_key, digest, retry_counts)
            totals["sdCacheCycles"] += 1
            totals["sdGcDeletes"] += 1
            sample("sdCacheGc")

        await _exercise_idle(manifest, config, sleeper)
        sample("idle")
    except Exception as exc:
        failures.append({"code": "WORKLOAD_EXCEPTION", "phase": type(exc).__name__})
    sample("final")

    verdict = bounded_verdict(samples, workload_failures=failures)
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "verdict": verdict["verdict"],
        "dataPolicy": {"input": "synthetic", "childDataCollected": False},
        "workload": {
            "cycles": config.cycles,
            "wsReconnects": config.ws_reconnects,
            "sdCycles": config.sd_cycles,
            "idle": {"mode": config.idle_mode, "durationSeconds": float(config.idle_seconds)},
        },
        "totals": totals,
        "retryCounts": retry_counts,
        "samples": samples,
        "deltas": verdict["deltas"],
        "slopes": verdict["slopes"],
        "limits": verdict["limits"],
        "checks": verdict["checks"],
        "failures": verdict["failures"],
        "syntheticProof": {
            "contractFixture": str(CONTRACT_FIXTURE),
            "runtimePrimitive": f"{CourseModeRuntimeAdapter.__module__}.{CourseModeRuntimeAdapter.__name__}",
            "lessonRuntimePrimitive": f"{LessonRuntime.__module__}.{LessonRuntime.__name__}",
            "forwarderPrimitive": f"{LessonEventForwarder.__module__}.{LessonEventForwarder.__name__}",
            "sdStorePrimitive": f"{SharedAssetStore.__module__}.{SharedAssetStore.__name__}",
            "sdGcPrimitive": f"{SdPackGarbageCollector.__module__}.{SdPackGarbageCollector.__name__}",
            "networkConnectionsOpened": totals["wsReconnects"],
            "networkScope": "loopback-only",
            "productionChaosEndpoint": False,
        },
    }
    if temporary is not None:
        temporary.cleanup()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ResourceSoakConfig(
        cycles=args.cycles,
        ws_reconnects=args.ws_reconnects,
        sd_cycles=args.sd_cycles,
        idle_mode=args.idle_mode,
        idle_seconds=args.idle_seconds,
    )
    report = asyncio.run(run_resource_soak(config, work_root=args.work_root))
    output = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
