# Course Mode V2 Task 07 Software Readiness

Date: 2026-08-22 (Asia/Ho_Chi_Minh)

Disposition: **PHYSICAL BLOCKED**

This is a redacted, software-only prerequisite record. No serial port was
opened, and no flash, OTA, reset, motion, assignment, feature flag, production
mutation, child pilot, or real child data was used.

## Exact candidate firmware identity

An isolated detached worktree at exact firmware main
`df70b5a12c68f5a6ab07f981cb7c10113e7dbc01` completed the canonical
`build-lcdwiki.sh --no-flash` build. The successful toolchain was the already
installed ESP-IDF 5.5.4 tree at commit
`8e48797f0c7e5849050e88e42100164f5898f9db`, with Python 3.9.6 and
`xtensa-esp-elf-g++` 14.2.0 (`esp-14.2.0_20260121`). The production config
assertion passed. A subsequent incremental `idf.py build` preserved the exact
application SHA-256.

Exact isolated build invocation, with no flash target or serial port:

```bash
export IDF_PATH=/path/to/pinned/esp-idf
export IDF_PYTHON_ENV_PATH=/path/to/pinned/idf5.5-python-env
source "$IDF_PATH/export.sh"
./build-lcdwiki.sh --no-flash
```

The checksum-pinned machine-readable identity is
`candidate-firmware-identity.json`. Its flash map, copied from the generated
`flasher_args.json`, is:

| Offset | Artifact | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `0x0` | `bootloader/bootloader.bin` | 16,256 | `aaa1bfe9535e78e567bc472ee5cbbfca312e50631fa407c87e36329c52d9dac6` |
| `0x8000` | `partition_table/partition-table.bin` | 3,072 | `4811619cacae08ef2e0e71b7220c6033a346ca5da7ca179082408c963ef530b5` |
| `0xd000` | `ota_data_initial.bin` | 8,192 | `7d2c7ac4888bfd75cd5f56e8d61f69595121183afc81556c876732fd3782c62f` |
| `0x20000` | `xiaozhi.bin` | 3,611,920 | `8182dcbb3d23eac255614bf8eafac455053d4f9d5965670257d9071f6ff5e059` |
| `0x800000` | `generated_assets.bin` | 5,693,495 | `d03b074c39d78601b2a2f6c3438620adc1cf779d634825385e63cafc4528a52b` |

Only the redacted identity is committed; the generated binary files are not.
Before an authorized HIL session, a staged candidate bundle must be verified
against every recorded byte size, SHA-256 value, metadata hash, and flash
offset. Any mismatch requires a new reviewed candidate identity; operators must
not silently substitute a rebuild.

This identity assembles the candidate bundle but does not authorize installing
it. Candidate flash, reset, serial capture, readback, and any motion remain
state-changing HIL actions requiring the current explicit authority, the named
operators, an authoritative sole physical lease, and every safety prerequisite
below.

## Rollback bundle audit

Result: **SOFTWARE-QUALIFIED ROLLBACK CANDIDATE; NOT PHYSICALLY REHEARSED
KNOWN-GOOD V1**.

The annotated firmware tag `lesson-prod-campaign-2026-08-21` resolves through
tag object `d9366b373a822a94016f3b87ffc9fcafacbe306d` to exact source
`03b3a392091afdd6b65d6e0812f1e4eed67087a6`, immediately before the Course
Mode campaign commits. T6.5 reviewed this source lineage and recorded 1,243
passed / 1 skipped plus a successful LCDWiki no-flash build. A fresh detached
build with the same pinned IDF/toolchain also passed, along with 90 focused
Python tests, 1,243 passed / 1 skipped for `python3 -m pytest -q tests`, and all
40 source-appropriate `run_host_native_*_test.sh` scripts.

A second clean build was run with all registry/proxy traffic forced to a
closed local endpoint. The component manager reported that registry access was
unavailable and completed from the already-cached 67 managed components. The
second build passed, but its bootloader, application, ELF, and merged hashes
differed from the first clean build, so source plus toolchain alone is not a
byte-reproducible rollback identity. The manifest and durable bundle therefore
pin the exact second, network-blocked output; any rebuild is a new candidate
until its hashes are independently reviewed.

The complete checksum identity, split flash map, merged reference image, source
bundle, and V1/default-off assertions are recorded in
`v1-rollback-candidate-identity.json`. The exact non-executed preserving
restoration/readback procedure is committed below. The binary and source bundles
are retained outside Git at the manifest's durable local path; large generated
binaries were not committed. The reviewed bundle root is its `SHA256SUMS` file,
SHA-256 `ac9e6cd079fbade699d726a1bcdedea042e55801b2e1b1ba6d3ce688d1b44031`.

This does not establish physical known-good status. T7.1 explicitly records no
firmware flash, while T7.4 lists `03b3a392...` only as firmware source main.
The T7.4 evidence has no runtime app/ELF hash, build ID, flash transcript, or
readback tying the actual robot image to this exact bundle. Therefore Task 07
must not relabel it as deployed, physically rehearsed, or known-good V1.

The preserving restore procedure uses split artifacts and does not write NVS
at `0x9000..0xcfff`; it also does not address the external SD device. The merged
reference binary must not be used for a preserve-NVS restore because its gap
fill spans NVS. All flash and readback commands remain **NOT EXECUTED**.

### Future authorized preserving rollback procedure - NOT EXECUTED

Run only from the checksum-pinned durable bundle after every physical Task 07
prerequisite is closed. `ESP_PYTHON`, `ESPTOOL_PY`, and `PORT` must be bound by
the authorized adult operator to the reviewed IDF environment, esptool, and sole
leased robot. Stop without writing if `get_security_info` reports secure-boot or
flash-encryption requirements that do not match the reviewed bundle. Never erase
the chip and never use `merged-binary.bin` for this preserving restore.

```bash
set -euo pipefail

test "$(shasum -a 256 SHA256SUMS | awk '{print $1}')" = \
  "ac9e6cd079fbade699d726a1bcdedea042e55801b2e1b1ba6d3ce688d1b44031"
shasum -a 256 -c SHA256SUMS
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" \
  --after no_reset get_security_info

"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" \
  --before no_reset --after no_reset \
  read_flash 0x9000 0x4000 readback-nvs-before.bin
shasum -a 256 readback-nvs-before.bin > readback-nvs-before.sha256

"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" -b 460800 \
  --before no_reset --after no_reset write_flash \
  --flash_mode dio --flash_size 16MB --flash_freq 80m \
  0x0 bootloader/bootloader.bin \
  0x8000 partition_table/partition-table.bin \
  0xd000 ota_data_initial.bin \
  0x20000 xiaozhi.bin \
  0x800000 generated_assets.bin

mkdir -p readback
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0x0 16256 readback/bootloader.bin
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0x8000 3072 readback/partition-table.bin
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0xd000 8192 readback/ota_data_initial.bin
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0x20000 3597792 readback/xiaozhi.bin
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0x800000 5693495 readback/generated_assets.bin
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" --before no_reset --after no_reset read_flash 0x9000 0x4000 readback-nvs-after.bin

cmp bootloader/bootloader.bin readback/bootloader.bin
cmp partition_table/partition-table.bin readback/partition-table.bin
cmp ota_data_initial.bin readback/ota_data_initial.bin
cmp xiaozhi.bin readback/xiaozhi.bin
cmp generated_assets.bin readback/generated_assets.bin
cmp readback-nvs-before.bin readback-nvs-after.bin

# Only after every byte comparison succeeds, leave the bootloader and run.
"$ESP_PYTHON" "$ESPTOOL_PY" --chip esp32s3 --port "$PORT" \
  --before no_reset run
```

The external SD card is not a target of these commands and must remain installed
and unmodified. Byte verification still does not establish known-good V1:
the two adults must separately record boot, display, audio, microphone, safe
rest, SD mount/cache, network, stop, and rollback behavior.

Historical alternatives remain non-qualifying:

Repository evidence proves that `build-blufi-fix/xiaozhi.bin`, SHA-256
`f6c5fab3b0886e6deb90e242d0172d540d80c1206a0eece419e8efee7c335469`,
was app-only flashed at `0x20000` in the 2026-08-11 known-good A/B and had
historically connected on 2026-08-10. That evidence identifies application
version 2.2.89 and ELF SHA-256
`9dd549a8aa84d7f6361a11b62986c7e9c34053cc1a50b7d0c02ce1213986c4c2`.
It does not pin the source Git SHA that produced the binary and does not prove
that it is the required known-good Course Mode V1 rollback state.

The older `firmware-backup/main-new-2.2.74-speedapp/` record is not
recoverable: Git contains only its README, flash script, and expected
application checksum. The referenced bootloader, partition table, OTA data,
application, and generated-assets binaries are absent.

Before HIL can call any rollback image known-good V1, an owner must still
provide all of:

- a verified known-good V1 source SHA and corresponding adult-observed
  operational evidence;
- all required binary files, byte sizes, SHA-256 values, board/config identity,
  and generated flash offsets;
- an attended restore command reviewed against that exact manifest;
- a distinct readback file produced with `read_flash 0x20000 <exact-app-size>`
  and verified byte-for-byte against the rollback application;
- post-restore boot/runtime evidence proving return to known-good V1 operation
  using the staged `03b3a392...` candidate or another independently reviewed
  bundle.

The existing offline verifier in
`TBOT-Firmware/scripts/lesson_cinematic_release_evidence_verify.py` demonstrates
the required exact app-offset, size, distinct-readback, and checksum checks, but
it cannot replace missing rollback provenance or a physical rollback rehearsal.

## Operator prerequisite checklist

Every box is mandatory before any candidate install or physical lane begins.

- [ ] **Adult HIL Operator** is named; owns the run, approved commands,
  timestamps, and evidence capture.
- [ ] **Independent Adult Safety Observer** is named; watches motion, binding,
  thermal, power, acoustic/privacy, and has unconditional stop authority.
- [ ] The approved internal robot is bound to the candidate manifest by its
  redacted bench identity, board revision, servo models, and power supply.
- [ ] A current sole physical lease record names the robot, both adults, start
  and expiry times, and proves no competing serial monitor, flasher, capture,
  Farm v9, T54, T65, or other hardware owner.
- [ ] The attended preflight physically demonstrates an accessible E-stop,
  immediate power isolation, DRV8833 `TP_EN` cutoff, clear motion envelope,
  safe rest pose, and the observer's stop procedure.
- [ ] Acoustic equipment is present: calibrated IEC 61672 Class 1 SLM,
  reference and measurement microphones, at least 2-in/2-out 24-bit/48 kHz
  interface, reference speaker, quiet-room/distance fixture, current calibration
  certificates, and recorded pre/post calibration checks.
- [ ] Power equipment is present: suitable rail current/voltage instrumentation,
  verified shunt/range/sample rate, current calibration evidence, synchronized
  logger, and probes installed without defeating the E-stop.
- [ ] Thermal equipment is present: calibrated left/right/head servo-case,
  driver/case, and ambient probes with placement photos, current calibration
  evidence, synchronized logger, and baseline readings.
- [ ] The complete checksum-pinned V1 rollback bundle, reviewed restore command,
  exact app readback command, and hash verifier are staged.
- [ ] No child participates, no real child data is used, and pilot publication,
  assignment, production flags, and production-wide deployment remain off.
- [ ] Safe-idle privacy preflight is prepared: after reconnect/startup and
  before any intentional voice or lesson action, observe zero microphone
  processor uplink, zero queued/sent mic packets, and zero server-accepted audio
  for the approved observation window. Any occurrence is an immediate
  FAIL/stop; do not continue other lanes.

If serial logs are needed for that proof, open the port read-only with Darwin
`O_RDONLY|O_NONBLOCK` and do not assert DTR/RTS or issue a reset. Record only
event names, counters, and timestamps; never retain audio payloads, transcripts,
full device identities, credentials, or real child data. A serial tool that
implicitly toggles reset lines is a state-changing action and requires separate
authorization. The 2026-08-22 incident remains open until the reviewed candidate
is physically rerun and the safe-idle zero-uplink result is checksum-pinned.

The ESP and firmware fixes add defense in depth: the server accepts binary mic
frames only after an explicit `listen start`, while firmware requires a live
owned turn, online non-passive transport, and a valid lesson window where
applicable. A realtime no-speech watchdog applies only when VAD is available;
the production device-AEC configuration uses the explicit authorization and
server admission gates because VAD is disabled. These controls reduce software
risk but do not substitute for the physical safe-idle rerun.

Absence of a competing process is not proof of lease ownership. Instrument
model names without current calibration certificates and preflight checks are
not calibration evidence.

## Numeric hardware release limits

Only values with an authoritative committed source are release criteria. Draft
fixture values are listed only to expose conflicts and are not promoted.

| Measurement or invariant | Release limit | Authority / status |
| --- | --- | --- |
| Settle before listening opens | At least `250 ms` after returned-to-rest ACK | Task 06 committed candidate manifest and runtime report |
| Return to safe rest after teardown | Within `2,000 ms` while hardware is responsive | `course-mode-embodied-hardware.md` and embodied interaction contract |
| Inter-choreography interval | At least `1.5 s` | Embodied interaction contract |
| Purposeful servo gestures | At most `1` per robot speaking turn | Embodied interaction contract |
| High-energy both-arm celebrations | At most `2` per session | Embodied interaction contract |
| Assessed-speech motion overlap | `0` servo commands and `0` continued mechanical movement | Master prompt and embodied hardware validation |
| Reduced-motion servo commands | `0` | Embodied hardware validation |
| Physical soak record | `20` completed sessions, every required cell attributable | Embodied hardware validation |
| Rest pose | Head `50%`, left arm `0%`, right arm `0%` | Embodied hardware validation |
| Supply voltage and tolerance | **NEEDS_HUMAN_APPROVAL** tied to board, servo, supply, and wiring specifications | No approved numeric specification identified |
| Idle/peak/stalled current | **NEEDS_HUMAN_APPROVAL** | Lane D is draft and conflicts between `1.2 A`, `1.5 A`, and `1.8 A`; none is promoted |
| Servo-case/driver/case temperature | **NEEDS_HUMAN_APPROVAL** tied to exact servo/driver vendors, probe placement, ambient, and stop margin | Current contract says vendor operating limit but provides no approved number |
| Motor noise at robot microphone | **NEEDS_HUMAN_APPROVAL** in calibrated units, bandwidth, distance, and analysis window | No approved speech-detector/noise-budget threshold identified |
| Robot-speaker leakage at microphone | **NEEDS_HUMAN_APPROVAL** in calibrated units and assessment-window method | No approved leakage threshold identified |
| Ambient acoustic floor and reference playback | **NEEDS_HUMAN_APPROVAL** for Task 07 | Lane A's `<30 dBA` room and `70 dBA at 1 m` are draft fixture values only |
| Abnormal vibration/chatter/comfort | **NEEDS_HUMAN_APPROVAL** with measurable stop criteria | Current source is qualitative only; no child pilot is permitted |
| Representative indoor lighting | **NEEDS_HUMAN_APPROVAL** in lux and geometry | Master prompt requires it but no numeric bound is approved |
| E-stop/`TP_EN` cutoff latency | **NEEDS_HUMAN_APPROVAL** | Lane D's `300 ms` production target and `500 ms` MVP threshold are unresolved draft values |

Any row marked `NEEDS_HUMAN_APPROVAL` blocks a measurement PASS. It may not be
waived or replaced by native, simulated, mock-instrument, or subjective evidence.

## Post-finding candidate status

The Task 06 firmware identity remains authoritative historical evidence for the
runtime-approved preflight candidate, but it predates the fail-closed microphone
uplink remediation committed on firmware `main` as
`039ded40387edbb8907b551dd957bde45b1b1fb6`. It must not be installed or accepted
as the Task 07 HIL candidate after the privacy stop condition. The offline
evidence validator therefore keeps `PHYSICAL_PASS` locked until a replacement
privacy-remediated artifact identity is checksum-pinned and independently
reviewed. A local no-flash build alone does not establish that replacement
release identity or authorize a flash.
