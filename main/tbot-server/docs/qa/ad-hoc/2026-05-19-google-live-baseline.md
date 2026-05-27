# Google Live baseline — production log analysis (PR1)

**Source log:** `tmp/server.log`
**Total log lines parsed:** 4,203
**Sessions detected:** 37
**Generated:** 2026-05-19T11:11:17

## Verification matrix row

| Task | AC | PASS / FAIL / PARTIAL | Evidence |
|---|---|---|---|
| adhoc-2026-05-19-google-live-baseline | PR1.6 baseline file exists | PASS | 37 sessions analysed |
| adhoc-2026-05-19-google-live-baseline | AEC necessity gate computed | PASS | verdict=AEC_OPTIONAL |

## Session timing

- **connect_ms** — {'count': 36, 'min': 328.6, 'max': 3528.3, 'mean': 587.12, 'median': 451.65, 'p95': 962.4, 'p99': 3528.3}
- **first_audio_out_ms** — {'count': 31, 'min': 516.5, 'max': 63036.8, 'mean': 3831.35, 'median': 642.3, 'p95': 6137.9, 'p99': 63036.8}
- **session_duration_sec** — {'count': 37, 'min': 1.0, 'max': 1026.0, 'mean': 176.38, 'median': 75.0, 'p95': 769.0, 'p99': 1026.0}

## RMS distributions (the key data for AEC decision)

| Metric | count | min | median | p95 | max |
|---|---:|---:|---:|---:|---:|
| RMS while model speaking (echo proxy) | 358 | 80 | 386.0 | 2186 | 8310 |
| RMS while silent / user-turn (noise floor + user speech) | 635 | 14 | 205 | 759 | 2787 |
| RMS at barge-in trigger | 1 | 9044 | 9044 | 9044 | 9044 |

## Totals (signs of pain points)

- barge_in fires: **1**
- user_interrupts: **155**
- server interrupts (suppressed by config): **157**
- audio chunks delivered: 13621
- audio bytes delivered: 95,724,198
- recv timeouts: **1**
- reconnect-attempted sessions: **2**
- fallback-triggered sessions: **0**
- abrupt drop sessions (websocket 1000 mid-send): **2**
- goAway / 1008 / 1011 sessions: **3**

- interrupt reason distribution: `{'explicit_interrupt': 3, 'text_input': 151, 'audio_input': 1}`

## AEC necessity gate

- median RMS while model speaking: **386.0**
- median RMS at barge-in trigger: **9044**
- ratio (speaking / barge-in): **0.043**
- rule: ratio > 0.25 means echo during model output is loud enough to be mistaken for barge-in or to mask real user speech
- **VERDICT: AEC_OPTIONAL**

## Decision implications for plan v2

- If verdict = AEC_REQUIRED → proceed with PR3 (server-side AEC) before tuning
  barge-in thresholds in PR4.
- If verdict = AEC_OPTIONAL → AEC may be deferred; PR4 threshold tuning alone
  could be enough. Re-confirm by repeating measurement in 3 different rooms.

## Per-session snapshot

| open_at | conn_ms | 1st_audio_ms | chunks | bytes | bargein | interrupts | drop | goaway |
|---|---:|---:|---:|---:|---:|---:|---|:---:|
| 2026-05-18T12:39:21 | 399.9 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T12:39:22 | 393.3 | 5896.0 | 115 | 815048 | 0 | 1 | - | - |
| 2026-05-18T12:47:27 | 371.3 | 4440.3 | 607 | 4914742 | 0 | 1 | - | - |
| 2026-05-18T13:04:33 | 564.0 | 3810.4 | 541 | 3942752 | 0 | 0 | - | - |
| 2026-05-18T13:19:19 | 440.1 | 1653.5 | 274 | 1939250 | 0 | 0 | - | - |
| 2026-05-18T13:47:10 | 404.4 | 2418.3 | 37 | 252482 | 0 | 0 | - | - |
| 2026-05-18T13:59:59 | 413.7 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T14:02:27 | 354.9 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T14:03:33 | 450.8 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T14:11:52 | 409.9 | 3419.2 | 0 | 0 | 0 | 0 | yes | - |
| 2026-05-18T14:16:35 | 523.3 | 603.3 | 32 | 147372 | 0 | 4 | - | - |
| 2026-05-18T14:19:28 | 442.8 | 588.1 | 470 | 3061984 | 0 | 10 | - | - |
| 2026-05-18T14:37:51 | 480.8 | 579.6 | 764 | 5109208 | 0 | 10 | - | - |
| 2026-05-18T14:42:45 | 3528.3 | 716.0 | 457 | 2955932 | 0 | 10 | - | - |
| 2026-05-18T15:10:08 | 586.0 | 63036.8 | 256 | 1839884 | 0 | 1 | - | - |
| 2026-05-18T15:14:15 | 619.2 | 2364.5 | 761 | 5801320 | 0 | 0 | - | - |
| 2026-05-18T15:37:03 | 616.1 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T15:39:51 | 359.3 | 632.1 | 70 | 485290 | 0 | 2 | - | - |
| 2026-05-18T15:40:08 | 962.4 | 2054.5 | 1852 | 14214390 | 0 | 7 | - | - |
| 2026-05-18T16:37:55 | 515.0 | 8659.9 | 29 | 168482 | 0 | 0 | - | - |
| 2026-05-18T16:38:11 | 1022.7 | 632.9 | 1287 | 9234908 | 0 | 10 | - | yes |
| 2026-05-18T16:42:07 | 672.8 | - | 0 | 0 | 0 | 0 | - | - |
| 2026-05-18T16:48:24 | 612.6 | 1816.1 | 291 | 2169612 | 0 | 0 | - | - |
| 2026-05-18T16:54:23 | 509.3 | 565.6 | 392 | 2519568 | 0 | 7 | - | yes |
| 2026-05-18T17:03:47 | 452.5 | 642.3 | 381 | 2291586 | 0 | 10 | - | - |
| 2026-05-18T17:45:52 | 668.8 | 585.7 | 457 | 2899262 | 0 | 10 | - | - |
| 2026-05-18T17:54:04 | 390.6 | 2241.8 | 882 | 6625486 | 0 | 0 | - | - |
| 2026-05-18T18:01:41 | 328.6 | 535.4 | 686 | 4703150 | 0 | 10 | - | yes |
| 2026-05-18T18:04:07 | 417.6 | 6137.9 | 104 | 776162 | 0 | 0 | - | - |
| 2026-05-18T19:39:15 | 432.5 | 516.5 | 403 | 2542182 | 0 | 10 | - | - |
| 2026-05-18T20:03:08 | 569.4 | 617.2 | 391 | 2408708 | 0 | 10 | - | - |
| 2026-05-18T21:15:05 | 448.8 | 625.2 | 455 | 3000064 | 0 | 10 | - | - |
| 2026-05-18T21:20:07 | 463.8 | 571.7 | 698 | 4739156 | 0 | 10 | - | - |
| 2026-05-18T21:29:09 | 457.3 | 577.0 | 505 | 3466148 | 0 | 10 | - | - |
| 2026-05-18T21:46:23 | 412.2 | 588.1 | 55 | 360966 | 0 | 1 | - | - |
| 2026-05-18T21:48:01 | 441.4 | 566.5 | 344 | 2192702 | 0 | 10 | - | - |
| 2026-05-18T21:54:25 | - | 679.3 | 25 | 146402 | 1 | 1 | yes | - |

## Next steps (per plan v2)

1. If AEC_REQUIRED: green-light PR3 (server-side AEC implementation).
2. Phase 1 also requires fresh capture with controlled scenarios (silence
   only / robot-only / close-mic user) on the physical robot — this log
   analysis is a strong starting point but does not isolate each condition.
3. PR2 (stability) can start in parallel — disconnect evidence is already
   sufficient (see abrupt-drop and recv_timeouts totals above).
