from __future__ import annotations

import asyncio
import json

import pytest

from scripts.course_mode_resource_soak import (
    ResourceSoakConfig,
    bounded_verdict,
    main,
    monotonic_growth_slope,
    parse_args,
    run_resource_soak,
)


def _sample(index: int, *, rss: int, fds: int, tasks: int, threads: int, cache: int) -> dict:
    return {
        "index": index,
        "phase": "injected",
        "rssBytes": rss,
        "fdCount": fds,
        "asyncioTaskCount": tasks,
        "threadCount": threads,
        "cacheBytes": cache,
    }


def test_cli_defaults_match_release_sized_resource_soak() -> None:
    args = parse_args([])

    assert args.cycles == 52
    assert args.ws_reconnects == 100
    assert args.sd_cycles == 10
    assert args.idle_mode == "virtual"
    assert args.idle_seconds == 3600.0


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--cycles", "0"),
        ("--cycles", "-1"),
        ("--ws-reconnects", "0"),
        ("--sd-cycles", "0"),
        ("--idle-seconds", "-1"),
    ],
)
def test_cli_rejects_non_positive_workload_and_negative_idle(option: str, value: str) -> None:
    with pytest.raises(SystemExit):
        parse_args([option, value])


def test_monotonic_growth_slope_reports_fitted_growth_despite_sample_noise() -> None:
    assert monotonic_growth_slope([10, 12, 14, 16]) == 2.0
    assert monotonic_growth_slope([10, 14, 13, 16]) > 1.0
    assert monotonic_growth_slope([10, 20, 19, 30, 29, 40]) > 5.0
    assert monotonic_growth_slope([10]) == 0.0


def test_bounded_verdict_fails_injected_resource_leaks() -> None:
    samples = [
        _sample(index, rss=100 + index * 2_000_000, fds=4 + index, tasks=1 + index, threads=2, cache=8)
        for index in range(5)
    ]

    verdict = bounded_verdict(samples, workload_failures=[])

    assert verdict["verdict"] == "FAIL"
    assert verdict["checks"]["rssSlopeBounded"] is False
    assert verdict["checks"]["fdSlopeBounded"] is False
    assert verdict["checks"]["taskSlopeBounded"] is False
    assert verdict["checks"]["threadSlopeBounded"] is True
    assert {failure["code"] for failure in verdict["failures"]} >= {
        "RSS_SLOPE_EXCEEDED",
        "FD_SLOPE_EXCEEDED",
        "ASYNCIO_TASK_SLOPE_EXCEEDED",
    }


def test_resource_soak_exercises_real_runtime_restore_and_sd_gc(tmp_path) -> None:
    report = asyncio.run(
        run_resource_soak(
            ResourceSoakConfig(cycles=2, ws_reconnects=3, sd_cycles=2, idle_mode="virtual", idle_seconds=60),
            work_root=tmp_path,
        )
    )

    assert report["schemaVersion"] == "course-mode-resource-soak.v1"
    assert report["verdict"] == "PASS"
    assert report["dataPolicy"] == {"input": "synthetic", "childDataCollected": False}
    assert report["workload"] == {
        "cycles": 2,
        "wsReconnects": 3,
        "sdCycles": 2,
        "idle": {"mode": "virtual", "durationSeconds": 60.0},
    }
    assert report["totals"] == {
        "lessonSimulations": 2,
        "terminalLessonSimulations": 2,
        "runtimeSnapshotRestores": 2,
        "wsReconnects": 3,
        "wsConnectionsOpened": 3,
        "wsConnectionsClosed": 3,
        "lessonRuntimeReconnects": 3,
        "lessonRuntimeClosures": 3,
        "forwarderWorkersStarted": 3,
        "forwarderWorkersClosed": 3,
        "forwarderPosts": 6,
        "terminalOutboxReplays": 3,
        "terminalOutboxCarryovers": 2,
        "terminalOutboxStores": 3,
        "terminalOutboxClears": 3,
        "terminalOutboxPending": 0,
        "stuckSessions": 0,
        "sdCacheCycles": 2,
        "sdGcDeletes": 2,
    }
    assert report["retryCounts"] == {"runtimeRestore": 0, "wsReconnect": 0, "sdCacheGc": 0}
    assert report["samples"][0]["phase"] == "baseline"
    assert report["samples"][-1]["phase"] == "final"
    assert report["samples"][-1]["cacheBytes"] <= report["limits"]["cacheDeltaBytes"]
    assert all(sample["asyncioTaskCount"] >= 1 for sample in report["samples"])
    assert report["syntheticProof"]["contractFixture"].endswith("course-mode-pilot-cat-ball.json")
    assert report["syntheticProof"]["networkConnectionsOpened"] == 3
    assert report["syntheticProof"]["networkScope"] == "loopback-only"


def test_resource_soak_injection_drives_deterministic_fail_verdict(tmp_path) -> None:
    def inject(index: int, sample: dict) -> dict:
        return {
            **sample,
            "rssBytes": 1_000_000 + index * 2_000_000,
            "fdCount": 10 + index,
            "asyncioTaskCount": 1,
            "threadCount": 1,
            "cacheBytes": index * 8_192,
        }

    report = asyncio.run(
        run_resource_soak(
            ResourceSoakConfig(cycles=2, ws_reconnects=2, sd_cycles=1, idle_mode="none", idle_seconds=0),
            work_root=tmp_path,
            sample_injector=inject,
        )
    )

    assert report["verdict"] == "FAIL"
    assert report["slopes"]["rssBytesPerSample"] == 2_000_000.0
    assert report["slopes"]["fdCountPerSample"] == 1.0
    assert report["slopes"]["cacheBytesPerSample"] == 8_192.0
    assert report["checks"]["workloadCompleted"] is True


def test_cli_emits_one_machine_readable_json_document(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--cycles",
            "1",
            "--ws-reconnects",
            "1",
            "--sd-cycles",
            "1",
            "--idle-mode",
            "none",
            "--work-root",
            str(tmp_path),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["verdict"] == "PASS"
    assert report["schemaVersion"] == "course-mode-resource-soak.v1"
