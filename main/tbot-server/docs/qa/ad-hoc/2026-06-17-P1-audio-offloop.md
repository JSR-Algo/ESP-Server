# P1-audio-offloop Evidence

## Task Start

- Task ID: P1-audio-offloop
- Area: `core/voice/google_live/audio_bridge.py`, app startup, WebSocket admission cap.
- Acceptance criteria:
  - AC1: Opus decode/encode, `audioop` resample, and Speex AEC run off the asyncio loop through a per-connection worker.
  - AC2: uvloop is installed as the event-loop policy.
  - AC3: measured per-active-stream cost derives a hard WebSocket accept cap; over-cap devices are rejected.
- Anti-goal: do not reorder audio frames.
- Stop-if check: no shared codec/AEC object was found. `GoogleLiveAudioBridge` owns decoder, encoder, AEC processor, resampler state, and a single-worker executor per connection.

## Read Before Code

- `docs/system-design/production-unified-runtime.md` section 7 and Appendix B require moving Opus decode/encode, resampling, and AEC off-loop, installing uvloop, and enforcing an accept cap from benchmarked stream cost.
- Current worktree already had partial implementation before this pass: per-bridge `ThreadPoolExecutor`, uvloop startup hook, WebSocket admission config, tests, and a benchmark script. This contradicted the prompt baseline that said there were zero `run_in_executor` call sites, so this pass treated the existing work as current state to verify and complete.

## Implementation Evidence

- `audio_bridge.py` uses a per-connection `ThreadPoolExecutor(max_workers=1)` and `_run_audio_cpu(...)` with `loop.run_in_executor(...)`.
- Input path: `forward_input_audio(...)` awaits `decode_input_audio_async(...)`; `_decode_input_audio(...)` performs Opus decode, input resample, and AEC in the worker.
- Output path: PCM model audio calls `_encode_output_audio(...)` through `_run_audio_cpu(...)`; that path pushes AEC reference audio, resamples output PCM, and Opus-encodes device packets in the worker.
- Frame ordering: the per-connection executor has one worker, and output streaming keeps packet order through the existing streaming encoder test.
- uvloop: `app.install_uvloop_policy()` sets `uvloop.EventLoopPolicy()` before `asyncio.run(main())`.
- Admission cap: `server.audio_admission.measured_cost_per_stream_core_fraction: 0.0293`, `cpu_cores: 1`, `headroom_fraction: 0.70` derives `floor(1 * 0.70 / 0.0293) = 23`. `WebSocketServer` rejects over-cap connections with `server_busy` and close code `1013`.

## Benchmark Evidence

Command:

```bash
.venv311/bin/python scripts/benchmark_google_live_audio_runtime.py --speakers 1,12,23 --frames 60
```

Paced full-path benchmark, 60 ms frames, Opus decode + input resample + AEC + output AEC reference/resample + Opus encode:

| speakers | execution | stream audio | cost/core stream | recommended cap | frame p95 | per-connection loop p95 | global loop heartbeat p95 | device packets |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | worker | 3600 ms | 0.0236 | 29 | 5.663 ms | 1.927 ms | 2.174 ms | 60 |
| 12 | worker | 3600 ms | 0.0186 | 37 | 5.002 ms | 1.117 ms | 1.166 ms | 720 |
| 23 | worker | 3600 ms | 0.0194 | 36 | 6.630 ms | 1.637 ms | 1.166 ms | 1380 |

The stored config uses the more conservative prior worst measured full-path value `0.0293`, yielding cap 23. At cap, frame processing stays below the 60 ms frame interval. The verification metric for the original head-of-line blocker is per-connection loop wake latency: it remains around 1-2 ms from 1 speaker through the configured cap, so audio CPU no longer blocks unrelated connection tasks on the asyncio loop.

## Verification Commands

```bash
.venv311/bin/python -m pytest tests/test_google_live_event_mapping.py::GoogleLiveEventMappingTest::test_forward_input_audio_offloads_decode_resample_and_aec_to_connection_worker tests/test_google_live_event_mapping.py::GoogleLiveEventMappingTest::test_pcm_audio_encoding_offloads_resample_aec_reference_and_opus_encode_to_connection_worker tests/test_google_live_event_mapping.py::GoogleLiveEventMappingTest::test_pcm_audio_chunks_stream_without_padding_until_audio_end tests/test_uvloop_runtime.py tests/test_websocket_server_manager_bootstrap.py::WebSocketServerManagerBootstrapTest::test_audio_admission_cap_is_derived_from_measured_stream_cost tests/test_websocket_server_manager_bootstrap.py::WebSocketServerManagerBootstrapTest::test_new_device_websocket_over_audio_admission_cap_is_rejected tests/test_benchmark_google_live_audio_runtime.py
```

Result: 7 passed in 1.78 s.

```bash
.venv311/bin/python -m py_compile scripts/benchmark_google_live_audio_runtime.py tests/test_benchmark_google_live_audio_runtime.py app.py core/websocket_server.py core/voice/google_live/audio_bridge.py tests/test_google_live_event_mapping.py tests/test_uvloop_runtime.py tests/test_websocket_server_manager_bootstrap.py
```

Result: exit 0.

`ruff` was not available in `.venv311` (`No module named ruff`), so lint could not be run from this environment.

## Critique Before Close

- The benchmark is synthetic and local: it does not open Gemini Live or firmware WebSockets. It exercises the CPU hot path and loop heartbeat, not cloud/network effects.
- Frame CPU service time still increases modestly under concurrent worker load, as expected. The acceptance load-test claim is interpreted against the blocker being closed: asyncio-loop per-connection wake latency no longer rises materially with N up to the cap.
- The hard cap is per accepted device WebSocket, not a speech-aware active-speaker lease. This is conservative because idle sockets still occupy cap slots.
- The stored cap measurement should be re-run on the actual deployment instance class; current evidence is from this local machine.
