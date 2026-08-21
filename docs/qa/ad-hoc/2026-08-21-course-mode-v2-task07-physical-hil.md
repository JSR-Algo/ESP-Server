# Course Mode V2 Task 07 Physical HIL Report

Date: 2026-08-21 (Asia/Ho_Chi_Minh)

Verdict: **PHYSICAL BLOCKED**

## Scope and safety disposition

This session performed read-only readiness only. No OTA, flash, assignment,
production flag change, production-wide enablement, motion command, lesson run,
power interruption, cache mutation, or rollback action was performed. No child
participated and no child data was used.

Task 06 thread `01a0233f-7412-79e3-bff1-040dbb137c5f` remained active and had
not issued a pinned `RUNTIME PASS`. Its latest observed status said Task 00 was
still being finalized and independently reviewed, leaving Tasks 01-05 and the
candidate freeze gated. Therefore the Task 07 candidate precondition was not
satisfied.

## Read-only bench inventory

### Robot and serial identity

- One ESP32-S3 USB JTAG/serial device was attached at
  `/dev/cu.usbmodem1101` (also `/dev/tty.usbmodem1101`).
- USB VID:PID was `303A:1001`; USB serial/MAC was redacted to the internal
  evidence identity suffix `...:AC:20`.
- No process held the serial port and no stale `screen` session was present at
  inspection time.
- Historical internal evidence binds this unit to the recent production bench,
  but Task 06 had not approved it for an exact Course Mode V2 candidate.

Commands:

```bash
python3 -m serial.tools.list_ports -v
ls -l /dev/cu.usb* /dev/tty.usb*
lsof /dev/cu.usbmodem1101
screen -ls
```

### Available host tools

| Tool | Readiness result |
| --- | --- |
| Python / pyserial | Available; enumerated the ESP32-S3 serial identity |
| `esptool.py` | Available at `/Library/Frameworks/Python.framework/Versions/3.14/bin/esptool.py` |
| ESP-IDF v5.5.2 export | `/Users/manhhodinh/esp/esp-idf-v5.5.2/export.sh` exists; not sourced |
| `ffmpeg` | Available at `/opt/homebrew/bin/ffmpeg` |
| `jq` / `ssh` | Available |
| `sox` | Not found |
| HIL capture | `robot/scripts/lesson_e2e_live_capture.py` |
| Runtime audit | `robot/esp32-server/main/tbot-server/scripts/physical_smoke_audit.py` |

The capture and audit tools were identified but not run against the candidate.
Even capture preflight can open/reset the USB device in later phases, so this
session stopped at non-invasive enumeration while the candidate gate was closed.

## Required measurement setup

The following setup is required before physical execution:

| Lane | Required setup | Readiness verdict |
| --- | --- | --- |
| Visual | Fixed camera/tripod, representative indoor lighting, 480x320 TFT framing, per-cue start/middle/end captures | BLOCKED: no operator/setup confirmation |
| Audio leakage and motor noise | IEC 61672 Class 1 SLM, calibrated reference and measurement microphones, >=2-in/2-out 24-bit/48 kHz interface, reference speaker, quiet room below 30 dBA, marked 0.1/0.3/1/2/3 m positions | BLOCKED: only built-in Mac microphone/speakers were detected; calibration equipment and noise fixtures were not present |
| Power | INA219 on the 5 V/motor rail with a 0.1 ohm shunt and real-hardware collector | BLOCKED: hardware and collector attachment not verified |
| Thermal | Case/driver probes plus ambient probe; documented placement and sampling | BLOCKED: probes were not detected or confirmed |
| Servo settle | Synchronized serial/runtime timestamps plus microphone or vibration capture; verify settled ACK before every assessment window | BLOCKED: candidate and measurement chain unavailable |
| E-stop | Accessible physical E-stop plus oscilloscope >=1 MS/s on E-stop output and DRV8833 `TP_EN` | BLOCKED: physical access and scope setup not verified |
| Adult comfort review | Named adult operators, clear work area, binding/collision watch, stop authority | BLOCKED: no adult operator was confirmed |

The older Lane D draft contains case-temperature and current targets, but it
also documents an unresolved motion-current conflict (1.2 A strict target versus
1.5/1.8 A interim values). Those draft values were not promoted to Task 07
release criteria. Approved hardware limits must be pinned before measurements
can receive a PASS verdict.

## Emergency stop and safe-rest plan

The software stop path is documented as an unconditional backend assignment
cancel (omit `assignmentVersion` for an emergency), followed by `lesson_stop`,
firmware stop ACK, centered head, lowered arms, neutral/idle face, and normal
voice restoration. Reboot is a last resort, not the primary stop.

The Course Mode embodied contract additionally requires:

- no motion after an assessment window opens;
- at least 1.5 seconds between choreographies;
- no more than two high-energy both-arm celebrations per session;
- rest within two seconds when responsive;
- immediate cancellation on stop, pause, disconnect, replacement, or safety
  transition.

The master prompt also requires an accessible physical emergency stop. Its
location, operation, and motor-enable cutoff were not physically inspected in
this session, so the HIL run cannot start.

## Rollback readiness

Historical evidence under
`robot/docs/evidence/t54-live-20260811-known-good-ab/` records successful app
flashes and a known-good V1-era boot (`App version 2.2.89`, truncated ELF hash
`9dd549a8a...`) on the same bench identity. However:

- the evidence directory does not contain the exact rollback binary;
- it does not pin a complete SHA-256 for a recoverable image bundle;
- multiple local firmware build directories contain ambiguous binaries;
- Task 06 had not pinned the candidate or its required rollback image.

Therefore rollback is **not ready**. Before HIL, the operator must stage one
explicit known-good V1 flash bundle (bootloader, partition table, OTA data,
application, assets where required), record offsets and full SHA-256 values,
perform a non-mutating manifest verification, and have the attended restore
command ready. Task 07 still requires physically rehearsing that rollback after
candidate validation.

## Evidence and source paths

- Master gate: `robot/esp32-server/docs/course-mode/production-ready/task-07-physical-robot-validation.md`
- Measurement contract: `robot/esp32-server/docs/course-mode/measurement-and-validation.md`
- Embodied limits: `robot/esp32-server/docs/course-mode/embodied-interaction.md`
- Physical capture checklist: `robot/docs/course-lesson-physical-test-checklist.md`
- Stop/rollback runbook: `robot/docs/runbooks/lesson-production-runbook.md`
- Audio fixture draft: `robot/tests/docs/lane-a-audio-fixture.md`
- Power/thermal/E-stop draft: `robot/tests/docs/lane-d-safety-state.md`
- Historical rollback evidence: `robot/docs/evidence/t54-live-20260811-known-good-ab/`

No new serial log, VPS log, photo, video, audio recording, thermal trace, power
trace, or motion trace was created because opening the physical lanes before the
runtime freeze and safety setup would violate the gate.

## Blockers and unblock criteria

1. Task 06 must commit a release-candidate manifest and issue pinned
   `RUNTIME PASS` with exact backend, ESP, firmware, renderer, pilot lesson,
   fixture, derivative, flags, checksums, and tool identities.
2. An adult HIL operator and an independent adult safety observer must be
   scheduled; no child pilot is permitted.
3. The attached robot must be explicitly approved against the Task 06 manifest.
4. The physical E-stop, clear motion envelope, safe rest pose, and immediate
   power isolation must be demonstrated before candidate installation.
5. The exact known-good V1 rollback image bundle and full checksums must be
   staged and verified.
6. Calibrated acoustic, power, thermal, visual, and timing instrumentation must
   be present, and approved numeric hardware limits must resolve the current
   draft conflict.
7. Exclusive serial and robot lease must be reacquired immediately before the
   attended run, with Farm v9 worktree isolation preserved.

Until every item is closed, native or simulation evidence cannot substitute for
this physical gate. **PHYSICAL BLOCKED does not authorize Task 08.**
