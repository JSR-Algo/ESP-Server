# Course Mode V2 Task 07 Physical HIL Report

Date: 2026-08-21 (Asia/Ho_Chi_Minh)

Verdict: **PHYSICAL BLOCKED**

## Privacy stop condition: unauthorized idle microphone uplink - 2026-08-22

During an adult-operated preflight on the approved internal robot with redacted
identity suffix `...:AC:20`, the application was found already running after
USB reconnect. No lesson, child interaction, or intentional voice turn had been
started. An eight-second read-only serial capture, opened with Darwin
`O_RDONLY|O_NONBLOCK` and without DTR, RTS, or reset, repeatedly reported voice
processor output, queued microphone uplink packets, successful WebSocket audio
sends, and firmware audio-send events. Packet counters advanced from roughly
5,725 to 5,850. No audio payload or transcript was captured or retained.

This is a material privacy and safe-idle stop condition: ambient microphone
uplink was active without a current intentional interaction. The failed
`esptool run --before no-reset` attempt (`Invalid head 0x49`) did not start the
application; the read-only capture established that it was already running.
The exact wall-clock time of those device actions was not supplied, so this
report records the evidence date without inventing a timestamp.

Under the user's explicit containment authority, the previously reviewed
identity/security preflight was repeated with `--before default-reset --after
no-reset`, leaving the device in the bootloader. Identity and security state
matched the earlier redacted record. A subsequent three-second read-only raw
capture returned zero bytes, confirming that application execution and the
observed audio activity had stopped. No flash, OTA, firmware readback, motion,
audio playback, assignment, feature-flag change, or deployment occurred. The
device was not touched again.

Software audit identified three fail-open paths in the Task 06 firmware:

1. realtime listening was exempt from the listening watchdog and could remain
   open indefinitely after a missed stop or stale conversation;
2. the main audio-send loop transmitted queued microphone packets without an
   independent authorization check for an owned listening turn;
3. a server `tts stop` carrying `continue_listening=true` could reopen the mic
   from an idle/passive socket without proving a current owned voice turn.

Historical retained logs contain the same sustained queued/sent-audio shape
after `mic_loop_resumed`, supporting a stale realtime turn rather than a
startup-only diagnostic artifact. Firmware remediation now adds an explicit
fail-closed uplink authorization latch, queue drain on unauthorized/expired
input, rejection of unowned continue-listen requests, and a realtime no-speech
watchdog only when VAD is available. The production device-AEC configuration
disables VAD, so its realtime privacy boundary is the authorization latch and
server admission gate rather than an inferred-silence timer. The ESP server
independently rejects binary microphone frames unless an explicit `listen
start` has opened the connection input gate and closes that gate on `listen
stop` or `listen detect`.

These software changes are not physical evidence. Before any Task 07 PASS, an
authorized attended rerun must prove that reconnect/startup with no intentional
lesson or voice action produces zero microphone uplink. Any queued or sent mic
packet, voice-processor uplink output, or server acceptance before an explicit
listen start is an immediate FAIL/stop condition. Task 07 remains **PHYSICAL
BLOCKED**, and Task 08 remains locked.

The checksum-pinned Task 06 firmware artifact was built before this remediation
and is now retained as historical evidence only; it is not eligible for Task 07
installation. Firmware `main` contains the reviewed software fix at
`3d4a1e2a32359278124c61e56fd459fac618506e`. The software-only reconciliation
below creates a checksum-pinned reproducible replacement candidate. Independent
software review passed for its artifact identity, reproducibility, flash map,
and runbook. The bundle is eligible to be presented for a bounded installation
authorization request, but it does not authorize installation or any other
device mutation. Current explicit point-of-use user authorization remains
required. No flash is authorized.

## Remediated candidate identity reconciliation - 2026-08-22

Independent closeout correctly found that the mutable default build artifact
`TBOT-Firmware/build/xiaozhi.bin` had SHA-256 `da61d09b...`, while the earlier
closeout reported `a4d0ab45...`. These are different build outputs:

- `da61d09b...` was produced at 10:48, before the privacy-remediation commits
  `039ded40...` and `3d4a1e2a...`. Because the working tree may already have
  contained uncommitted remediation, timing alone does not prove its behavioral
  content. It has no immutable source binding and is not a Task 07 candidate.
- `a4d0ab45...` was produced at 11:32 in the explicit
  `build-task07-final/` directory from final firmware main `3d4a1e2a...` with
  the pinned LCDWiki config and toolchain. It is a valid preliminary output,
  but the enabled app/bootloader compile-time descriptors make an ordinary
  clean rebuild timestamp-dependent, so it is superseded rather than promoted.

Two fresh no-flash builds were then run in separate ignored build directories
with ESP-IDF's supported `CONFIG_APP_REPRODUCIBLE_BUILD=y` mode. This removes
only the app and bootloader wall-clock descriptors. Source, production profile,
LCDWiki ES3C35P board selection, component lock, partition table, flash mode,
and flash offsets remained pinned. Both builds produced byte-identical
bootloader, partition table, OTA data, application, generated assets, and ELF.
The reproducible application is 3,612,672 bytes with SHA-256
`84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e`.

The complete redacted identity is
`docs/qa/artifacts/2026-08-22-course-mode-task07/remediated-candidate-identity.json`.
Large binaries are retained outside Git at
`task-artifacts/course-mode-task07/remediated-candidate-3d4a1e2a32359278124c61e56fd459fac618506e`.
Its `SHA256SUMS` root is
`9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6`.
The generated five-region flash map and merged-image slice verification passed.
The source bundle verifies as complete at exact firmware SHA `3d4a1e2a...`.
The independent review receipt is retained outside the bundle root at
`docs/qa/artifacts/2026-08-22-course-mode-task07/remediated-candidate-independent-review.json`
so it can bind this final root without circular self-attestation.

This closes the missing durable/reproducible build-output sub-blocker only. The
bundle is classified **SOFTWARE-QUALIFIED PRIVACY-REMEDIATED CANDIDATE;
INDEPENDENT SOFTWARE REVIEW PASSED; DEVICE MUTATION REQUIRES SEPARATE CURRENT
USER AUTHORIZATION**. This closes the artifact-review blocker only. Exact
cross-repository HIL binding, current point-of-use authorization, and every
physical gate remain outstanding, so the offline evidence remains
`PHYSICAL_BLOCKED`. No device, serial port, flash, readback, reset, motion,
audio, assignment, feature flag, or production system was accessed.

## Authorized serial identity/security preflight - 2026-08-22

Evidence was recorded in this report at `2026-08-22T08:17:37+07:00`
(Asia/Ho_Chi_Minh). The operator did not supply a separate wall-clock timestamp
for the command, so none is inferred. This section appends the newly authorized
preflight evidence; it does not rewrite or relabel the historical blocked
evidence below.

The user confirmed the intended internal test robot, confirmed that they are an
adult HIL operator holding the sole physical lease, and named David as the
independent adult safety observer. The operator also confirmed immediate power
isolation and a clear motion envelope. Authorization was limited to serial
identity/security preflight; it did not authorize candidate installation,
readback, reset after the preflight, motion, audio, or production changes.

Read-only enumeration identified one Espressif USB JTAG/serial debug unit at
`/dev/cu.usbmodem1101`, VID:PID `0x303a:0x1001`, with redacted USB serial/MAC
suffix `...:AC:20`. No process owned the port at enumeration time. With the
operator's explicit authority, `esptool` v5.3.1 ran the equivalent of:

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
  --before default-reset --after no-reset get-security-info
```

The command exited `0`, loaded its stub, and reported ESP32-S3 QFN56 revision
v0.2, Wi-Fi/BT5, 8 MB embedded PSRAM, 40 MHz crystal, USB-Serial/JTAG, and the
same redacted identity suffix `...:AC:20`. Security flags were `0`: Secure Boot
was disabled, Flash Encryption was disabled, and `SPI_BOOT_CRYPT_CNT` was `0`.
The device was intentionally left in the bootloader because `--after no-reset`
was used. That resulting state is evidence only and is not authorization for a
follow-up serial command, reset, flash, readback, or run command.

No flash, OTA, firmware readback, motor or servo command, audio action,
assignment, feature-flag change, deployment, or other production mutation
occurred. No further physical or serial action was taken while recording this
evidence.

This preflight reduces the connection, robot-identity, and security-state
blockers and records the operator's sole-use assertion, the two named adult
roles, immediate power isolation, and a clear motion envelope. It does not yet
provide the formal candidate-manifest binding record for board revision, servo
models, and power supply, or the time-bounded sole-lease record required before
candidate installation. It also does **not** demonstrate an accessible physical
E-stop or DRV8833 `TP_EN` cutoff, calibrated acoustic, power, or thermal
instruments and calibration evidence, approved numeric hardware limits,
candidate installation, any visual/audio/motion/thermal/power/cache/stop/rest
behavior, or a physically rehearsed known-good V1 rollback.
The checksum-pinned rollback bundle remains only a **SOFTWARE-QUALIFIED
ROLLBACK CANDIDATE**. Task 07 therefore remains **PHYSICAL BLOCKED**, and this
preflight does not authorize Task 08.

## Historical hardware-binding provenance assessment - 2026-08-22

Read-only review found strong historical continuity for the redacted device
identity suffix `...:AC:20` and the candidate board family:

- The 2026-08-06 configuration audit identifies the matching full device
  identity as the real lab robot. A later mobile discovery snapshot labels the
  same identity as `TBOT-...AC20`.
- At log time `2026-08-16 01:11:14`, preserved server evidence records an OTA
  request carrying the same device identity in its device and affinity headers
  and payload; the following WebSocket connection carries a redacted
  authorization header and matching build identity. The robot self-reported
  board type/name `lcdwiki-es3c35p`, a 480x320 color display, ESP32-S3 chip
  revision `2`,
  16 MB flash, and firmware `2.2.89`. The server selected the
  `lcdwiki-es3c35p` model and reported that firmware `2.2.89` was current.
- The current authorized security preflight independently observed the matching
  redacted identity suffix, ESP32-S3 revision v0.2, 8 MB embedded PSRAM, and
  USB-Serial/JTAG. The Task 07 candidate identity targets ESP32-S3 board
  `lcdwiki-es3c35p`, project version `2.2.89`, and 16 MB flash settings. Matching
  project-version labels do not establish that the historical and candidate
  application binaries are identical.
- Firmware history and the current servant-controller source map head, left-arm,
  and right-arm servo signals to GPIO 11, 12, and 13 respectively. This is
  wiring/source provenance, not evidence of the servo models currently fitted
  to the observed robot.

The adult operator now explicitly attests that the connected device with the
matching redacted suffix is the same main robot they routinely use and that its
hardware has never been changed from the historically evidenced configuration.
This first-party operational-history attestation strengthens continuity and
does not require disassembly. It is not independent proof of concealed or
unlabeled details such as the PCB revision, servo manufacturer/models and
linkage, power-supply model/rating, or E-stop and `TP_EN` implementation.

These independently preserved records make accidental selection of an unrelated
board family unlikely. Together with the operator attestation, they provide
strong identity, board-family, and operational-continuity provenance for
planning the attended HIL session. They do **not** establish the formal current
candidate-manifest binding required before installation. No independent current
record establishes the physical PCB revision label, servo manufacturer/models
and linkage, power-supply model/rating, E-stop or DRV8833 `TP_EN` path, or a
current labeled inventory of the display, microphone, speaker, SD, motor-driver,
and servo peripherals. The time-bounded lease record and attended safety-path
verification also remain outstanding. Formal hardware binding therefore remains
**BLOCKED**.

Evidence reviewed without opening the serial port or touching the device:

The configuration audit and captured server logs are the primary historical
records. The untracked mobile XML snapshot is corroborative only and is not
treated as a durable binding record.

| Redacted evidence source | SHA-256 |
| --- | --- |
| `robot/docs/qa/ad-hoc/2026-08-06-t03-config-audit.md` | `1ee9e6122e011a1cfa28c3c33baf5ce8632175e6a2d5a257e8d42e17eed11c66` |
| `tmp/tbot-found.xml` | `5c51ce0fa3d33ceb803a0a17581141f20295852d3144e107f02186d4ea088998` |
| `robot/docs/evidence/t54-live-20260816-v7-render-task-boot-smoke/timeline.log` | `f60ca906c29a8fbb0d4d2c55150c6a64bd6a56982cd18a310b575971cac9889e` |
| `robot/docs/evidence/t54-live-20260816-v7-render-task-boot-smoke/esp-server.log` | `c7d18b11b6c77c6b93dd7f46dee62f6c1fb8b1dd08065cf1cba3a9121ac0b9a7` |
| `docs/qa/artifacts/2026-08-22-course-mode-task07/candidate-firmware-identity.json` | `e03942e6b0c9069a98363821209cdc62ea141b52d7b6529632eb156ff2a37938` |
| `TBOT-Servant-Firmware` main `1be3ff13ee5e75056d48defc02ca62415384b84d`, `main/main.c` | `478781702b04fea9edd0e145813e8c9edc87989ff528aea4316d0273da6abab9` |

This assessment is documentation-only. It does not authorize serial access,
reset, flash, readback, motion, audio, assignment, deployment, or any other
physical or production action. Task 07 remains **PHYSICAL BLOCKED**, and Task 08
remains locked.

## Software-only blocker reduction - 2026-08-22

The tagged pre-Course Mode firmware source
`lesson-prod-campaign-2026-08-21` / `03b3a392091afdd6b65d6e0812f1e4eed67087a6`
now has a fresh pinned no-flash build, complete split/merged artifact identity,
source bundle, 90 focused Python passes, 1,243 full Python passes with one
skip, and all 40 native test scripts passing. It is retained as a
**SOFTWARE-QUALIFIED ROLLBACK CANDIDATE**, not a physically rehearsed known-good
V1 image. T7.1 records that no firmware flash occurred, and T7.4 does not bind
the running robot image to this source with an app hash, ELF hash, build ID,
flash transcript, or readback. Exact identity evidence is in
`docs/qa/artifacts/2026-08-22-course-mode-task07/v1-rollback-candidate-identity.json`,
and the non-executed preserving restore/readback procedure is in
`docs/qa/artifacts/2026-08-22-course-mode-task07/software-readiness.md`.

The final retained binary is the second clean build, which passed with registry
traffic forced to an unreachable local endpoint and cached components only.
That rebuild was not byte-identical to the first build for bootloader, app,
ELF, and merged output, so the durable bundle hashes are authoritative and a
later rebuild may not be silently substituted.

This reduces the missing-binary blocker but does not close the rollback gate:
an attended physical rollback rehearsal must still prove known-good V1
operation. No flash, readback, reset, serial access, motion, or production
mutation occurred.

The exact final firmware source
`df70b5a12c68f5a6ab07f981cb7c10113e7dbc01` now has a successful isolated
no-flash LCDWiki build. The checksum-pinned five-artifact flash identity,
toolchain identity, generated configuration hashes, and offsets are recorded in
`docs/qa/artifacts/2026-08-22-course-mode-task07/candidate-firmware-identity.json`.
This supersedes the earlier statement in the reopened audit that no final-main
binary had been demonstrated. It does not prove installation or physical
behavior and does not authorize flashing.

The companion software-readiness record at
`docs/qa/artifacts/2026-08-22-course-mode-task07/software-readiness.md` contains
the two-adult operator checklist and the complete numeric-limit authority table.
It now records a checksum-pinned, durably retained software-qualified rollback
candidate. It does not call that bundle physically known-good; the historical
known-good application remains source-ambiguous, the older 2.2.74 backup still
lacks its binaries, and the new candidate still requires an attended physical
rollback rehearsal.

No serial port was opened. No candidate install, OTA, flash, reset, motion,
assignment, publication, flag change, production mutation, readback, or
rollback rehearsal occurred. Task 07 remains **PHYSICAL BLOCKED** on the sole
physical lease, named adult operators, approved internal robot binding,
attended E-stop/`TP_EN` verification, calibrated instruments and calibration
evidence, approved numeric hardware limits, and the recoverable checksum-pinned
known-good V1 rollback bundle. The preserved original blocked evidence remains
unchanged below and **PHYSICAL BLOCKED does not authorize Task 08**.

## Reopened prerequisite audit - 2026-08-22

Task 06 is now closed with `RUNTIME_PASS`, clean independent ESP review, and
15/15 committed artifact hashes verified locally. This opens the software gate
for Task 07, but it does not clear the physical safety and equipment gates.
The original 2026-08-21 blocked evidence below is preserved as the record of
that earlier audit; this section records only the changed prerequisite state.

### Exact candidate identity

The authoritative post-merge repository heads match the Task 06 handoff:

| Component | Final owning-repository main SHA |
| --- | --- |
| ESP server | `a714da67a3382999bed2c4132637352bb2a27eba` |
| Backend | `1920586d48e5448dcf653dbfa2391c7ef346fcd9` |
| Firmware | `df70b5a12c68f5a6ab07f981cb7c10113e7dbc01` |

The committed Task 06 manifest separately freezes the validated source bases:
ESP `7e2628a9b9b4c3c7bbde4b426455700a4e0b7268`, backend
`657474ff3b58fba2c3c31f2978d53370ffad8b11`, and firmware
`d47174daebe17b9c1a9d1a1eb506711a57cd3512`. Each frozen base is an ancestor of
its final main SHA. The reviewed executable validation tag resolves to
`7c75ddf26ed2e495829b661c297894c2e5aa7813`. The pilot remains
`course-mode-pilot-cat-ball@v1`, renderer `teebot-lesson-renderer.v4`, draft,
unpublished, unassigned, and production-disabled.

No firmware image has been demonstrated as built from final firmware main
`df70b5a1...`. Existing local build directories contain multiple mutually
different application and bootloader hashes without a candidate-to-binary
provenance manifest. Therefore the exact installable hardware candidate remains
unavailable even though the source/runtime gate is open.

### Fresh physical readiness result

| Prerequisite | 2026-08-22 read-only evidence | Verdict |
| --- | --- | --- |
| Approved internal robot | One ESP32-S3 remains attached at `/dev/cu.usbmodem1101`, USB identity suffix `...:AC:20`, matching historical internal bench evidence | BLOCKED: no current approval binds this unit to the exact Task 06 candidate |
| Sole physical lease | No serial holder, monitor, flasher, capture process, or current hardware-lock file was observed | BLOCKED: absence of a holder does not establish an authoritative sole lease or operator ownership |
| Adult operators | No named HIL operator or independent adult safety observer is recorded | BLOCKED |
| Physical E-stop | No attended inspection confirms the accessible E-stop, DRV8833 `TP_EN`, immediate power isolation, or safe motion envelope | BLOCKED |
| Acoustic chain | OS inventory shows only built-in/virtual audio devices; no calibrated SLM, reference/measurement microphones, external interface, reference speaker, distance fixture, or required audio samples were identified; `sox` and Python `sounddevice` are unavailable | BLOCKED |
| Power instrumentation | No INA219 attachment or real-hardware collector is confirmed; Python INA219 support is unavailable | BLOCKED |
| Thermal instrumentation | No case/driver/ambient probe is confirmed; Python DHT support is unavailable | BLOCKED |
| Approved numeric limits | Course timing limits are pinned at 250 ms settle-before-listen and two seconds to rest | BLOCKED: power, thermal, motor-noise, and leakage release limits remain unapproved; the older draft still conflicts between 1.2 A and 1.5/1.8 A motion-current values |
| Rollback image | Historical logs prove a V1-era boot/flash, but no recoverable binary bundle is tied to a known-good V1 source SHA with complete SHA-256 values, offsets, and restore command | BLOCKED |

### Current disposition

No candidate install, serial capture, OTA, flash, reset, servo action,
assignment, pilot publication, flag change, production mutation, or rollback
rehearsal was performed. Task 07 remains **PHYSICAL BLOCKED** until every row
above is closed in an attended session. The Task 06 pass authorizes physical
testing only; it does not override lease, operator, safety, instrumentation, or
rollback requirements and does not authorize Task 08 while this gate is
blocked.

## Original 2026-08-21 blocked evidence

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
