# Interactive Speaking Lesson — Production Deploy + Flash Runbook

> Scope: deploy the interactive speaking-lesson subsystem (robot-server + ESP32 firmware) to
> production and flash the real LCDWiki ES3C35P robot (MAC `14:c1:9f:d1:a8:48`).
> Every step below is grounded in the four-dimension audit (config/flag-gating, child-safety/consent,
> endpoints/image-provenance, firmware face-hide). Do **not** add env or steps not grounded here.
>
> Source-of-truth file paths are absolute. Code citations are `path:line`.

---

## 1. What shipped (in-env)

Three coupled pieces are wired and pass their in-env gates:

### 1a. Speaking-lesson subsystem (robot-server)
- Dual admission topology: real runtime (`lesson.runtime_enabled`, env `LESSON_RUNTIME_ENABLED`)
  and built-in sample demo (`lesson.sample_lesson`, env `LESSON_SAMPLE_ENABLED`) are **deliberately
  decoupled** (`config/config_loader.py:266-268`, `core/connection.py:1836-1849`).
- `start_lesson` tool refuses when **both** runtime disabled AND sample off, keeping the lesson layer
  dark by default (`plugins_func/functions/start_lesson.py:128-136`).

### 1b. Interactive sample (self-contained demo)
- `LESSON_SAMPLE_MODE=interactive` selects `build_interactive_sample_manifest` with a
  `completionClass=='interactive'` SAY-IT step that opens a child-response window over Google Live
  (`core/lesson/sample.py:176-225, 320-327, 383-389`).
- Fully self-contained: `SampleAssetCache` (passthrough, no download/sha256, `sample.py:258-265`),
  `NoOpLessonForwarder` (`sample.py:288`), manifest `assets:[]` (`sample.py:221`) — needs **no**
  `COURSE_BACKEND_URL`, **no** `LESSON_ASSET_ORIGIN_BASE`, **no** `TBOT_DEVICE_MINT_SECRET`.

### 1c. Firmware face-hide (`SetLessonMode`, LCDWiki ES3C35P)
- Wired across 4 files and compiles: base virtual no-op `lvgl_display.h:43`; override
  `lcd_display.h:62`; impl `lcd_display.cc:1341-1357` (hides `emoji_box_` via
  `LV_OBJ_FLAG_HIDDEN` + stops `gif_controller_` on active; clears flag on inactive); invoked from
  `lesson_handler.cc:739` (start→`true`), `:758` (stop→`false`), `:778` (error→`false`).

### What is verified in-env
- Lesson test suites green (per project memory: ~242 lesson tests across the runtime + e2e walk).
- Firmware native host gate: `scripts/run_host_native_lesson_coverage.sh` compiles the **real**
  `main/lesson_handler.cc` and asserts `disp.lesson_mode_calls.back()==true` after `lesson_start`
  (`tests/native/lesson_handler_host_test.cc:583`) and `==false` after `lesson_stop` (`:595`) —
  `gcovr --fail-under-line 100` (387-check class host harness).

> Caveat: the native gate covers `lesson_handler.cc` dispatch only. `lcd_display.cc:1341` (the LVGL
> `emoji_box_` toggle that produces the visible effect) has **zero** automated coverage — proven only
> on-device (CP-7, §5).

---

## 2. Production readiness checklist

| Item | Status | Notes |
|---|---|---|
| Speaking-lesson subsystem code (runtime + interactive sample) | ✅ done | Wired, in-env tests green. Reaches prod only via a **fresh image rebuild** (§3, item below). |
| Firmware face-hide (`SetLessonMode`) code | ⚠️ needs-action | Wired + native gate green, but **uncommitted** on `ci/firmware-local-gates` (4 files working-tree only). Commit + push before fleet build. |
| Provider conversation refactor / lesson model-muting-in-lesson | ❌ blocker | During the interactive answer window the model is **not muted**: child audio is forwarded into the AUDIO-modality Live session and can free-form an audio reply; suppression (`stop_output`) is reactive, post-transcript (`session_provider/google_live.py:356-381, 824-845`). Audio chunks never screened. |
| Child-safety settings (`safety_settings` BLOCK_LOW_AND_ABOVE) | ❌ blocker | **Absent.** `_build_connect_config` attaches no `safety_settings` (`google_live/client.py:252-308`); repo-wide zero `HarmBlockThreshold` usage. Child session runs at Gemini default thresholds. |
| Consent gate | ✅ done (with caveats) | `start_session` gates on `_voice_consent_allows_live`, fail-closed (`session_provider/google_live.py:101-114`, `config/voice_consent_client.py:21-90`). ⚠️ three bypasses must be off (`TBOT_BYPASS_VOICE_CONSENT`, `factory_test_claimed_all`, `factory_test_claimed_devices`); re-open path `_ensure_live_open_for_audio` does **not** re-verify consent. |
| Endpoint stability | ❌ blocker | Committed device endpoint is a **QUICK trycloudflare tunnel** in every source of truth (`config.yaml:20/35`, firmware `Kconfig.projbuild:5/18`, all `sdkconfig` variants). Restart = new random hostname = stranded fleet. |
| Robot-server image rebuild from this tree | ❌ blocker | Image is `COPY main/tbot-server .` (`Dockerfile-server:5`). Last documented prod tag `dinhmanh11/tbot-server:vps-20260525144756` (2026-05-25, `deploy/README.md:11`) predates current lesson/TTS/provider work (tree mtime 2026-06-25) → prod lacks the lesson code. |
| Firmware flash (LCDWiki ES3C35P) | ⚠️ needs-action | Not built/flashed from current tree; no device connected at audit time. USB flash is the only reliable path (OTA `IsNewVersionAvailable()==false`, same `PROJECT_VER 2.2.34`). |
| Real AIza Google Live key (with quota) | ❌ blocker | Per MEMORY the prod key is a Live **ephemeral** token / 429-quota'd — operational live blocker. Need a real Live AUDIO key with quota for `gemini-3.1-flash-live-preview`. |

**Tally: 2 ✅ · 2 ⚠️ · 4 ❌**

---

## 3. Server deploy runbook (robot-server)

> Canonical compose for any networked deploy is **`deploy/docker-compose.prod.yml`**
> (`deploy/README.md:126`). The single-service root `docker-compose.yml` is **local/dev only** and
> does **not** forward `TBOT_PUBLIC_WEBSOCKET_URL` / `TBOT_BACKEND_API_URL` — using it silently falls
> back to the baked quick tunnel (`docker-compose.yml:11-20`).

### Step 1 — Rebuild the image from THIS tree (mandatory)
The lesson runtime + Google-Live provider only reach prod if rebuilt (`Dockerfile-server:5`).

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
./deploy/build-local.sh --tag <new-tag>          # builds via Dockerfile-server
# push dinhmanh11/tbot-server:<new-tag> to the registry
```

Do **not** reuse `vps-20260525144756` — it predates the lesson code.

### Step 2 — Set the FULL env block

> **BOOT-SAFETY CRASH WARNING.** The git-ignored `data/.config.yaml` (mounted Docker config volume)
> commits `lesson.runtime_enabled: true` and, lacking a `manager-api.url` key, **merges over**
> `config.yaml`'s safe `false` (`data/.config.yaml:24-28`, merge at `config_loader.py:560-572`).
> Any run that does **not** pass `LESSON_RUNTIME_ENABLED` at all (e.g. `python app.py` directly, or a
> compose variant) will see `runtime_enabled:true` and **HARD-CRASH at boot** unless
> `TBOT_DEVICE_MINT_SECRET` + `LESSON_ASSET_ORIGIN_BASE` are set
> (`_assert_lesson_runtime_boot_safe`, `config_loader.py:419-436`). Always pin the flag explicitly.

#### HARD — interactive sample demo (self-contained, no backend)
| Env | Value | Why |
|---|---|---|
| `LESSON_SAMPLE_ENABLED` | `true` | Enables the self-contained sample admission path; without it `start_lesson` refuses (`start_lesson.py:128-136`, `connection.py:1843-1849`). |
| `LESSON_SAMPLE_MODE` | `interactive` | Default is `passive` (no mic window). Selects the `completionClass=interactive` SAY-IT manifest (`config_loader.py:273,325-326`; `sample.py:201-207,383-389`). |
| `GOOGLE_API_KEY` | `<real Live AUDIO key w/ quota>` | Resolves `google_live.api_key` (`client.py:573-578`) / injected (`config_loader.py:390-397`). Must be a real Live key, **NOT** a Live ephemeral token. |
| `LESSON_RUNTIME_ENABLED` | `false` | Pin explicitly: keeps real runtime dark AND **overrides** `data/.config.yaml`'s `true` to prevent the boot crash above (`config_loader.py:295-296, 419-436`). |

#### HARD — real assigned-lesson path (instead of / in addition to sample)
| Env | Value | Why |
|---|---|---|
| `LESSON_RUNTIME_ENABLED` | `true` | Gates the real assigned-lesson runtime (off by default, `docker-compose.prod.yml:43`). |
| `COURSE_BACKEND_URL` | `https://<course-backend>/v1` | Where the runtime fetches the assigned manifest; sets `server.api_url` + `lesson.api_base` (`config_loader.py:328-336`); production auto-enable trigger (`337-343`). |
| `TBOT_DEVICE_MINT_SECRET` | `<real device-mint secret>` | Required by `_assert_lesson_runtime_boot_safe` when runtime true (`config_loader.py:427-436`) and by prod assert (`458-472`). Missing → boot crash. |
| `LESSON_ASSET_ORIGIN_BASE` | `https://assets.../lessons` | Required alongside the mint secret by both boot-safe asserts (`config_loader.py:427-436, 458-472`); auto-enable trigger (`337-343`). |

> Note the **production auto-enable**: even with `LESSON_RUNTIME_ENABLED` unset, the runtime
> auto-enables when `TBOT_DEVICE_MINT_SECRET` + `LESSON_ASSET_ORIGIN_BASE` + an api_base are all
> present (`config_loader.py:337-343`).

#### HARD — endpoints (prod compose requires these `:?`)
| Env | Value | Why |
|---|---|---|
| `TBOT_PUBLIC_WEBSOCKET_URL` | `wss://<stable-domain>/tbot/v1/` | Overrides `config.yaml:20` quick tunnel at load (`config_loader.py:509-520`). `deploy-vps.sh:197` **rejects** any `*.trycloudflare.com`. |
| `TBOT_BACKEND_API_URL` | `https://tbot-backend-8wmh.onrender.com/v1` | Overrides `server.api_url` + `lesson.api_base` (`config_loader.py:521-536`). `deploy-vps.sh:205` enforces the `/v1` prefix. |
| `TBOT_SERVER_IMAGE` | `dinhmanh11/tbot-server:<new-tag>` | `docker-compose.prod.yml:19` (`:?` required). Must be the freshly built tag from Step 1, not `vps-20260525144756`. |

#### Optional / posture
| Env | Value | Why |
|---|---|---|
| `LESSON_SAMPLE_ASSET_BASE` | `https://<image-host>` | Overrides sample step image host only (`config_loader.py:270,318-319`; `sample.py:301-304`). Sample ships non-blank srcs already. |
| `TBOT_TTS_PROVIDER` | `google` (only if routing narration via Gemini TTS) | On google_live, narration rides the Live session — TTS not needed. If set, selects `GeminiTTS` and makes `GEMINI_API_KEY` HARD (`config_loader.py:120-173`). |
| `GEMINI_API_KEY` | `<real AIza... REST key>` | HARD only when `TBOT_TTS_PROVIDER=google\|gemini`. Gemini TTS REST rejects the Live ephemeral token; the Live token is **not** inherited (`config_loader.py:126-153,184-200`). |
| `GOOGLE_API_KEY` | empty for manager-driven prod | Prod reads the per-agent key from the Admin role-config page (agentId `dd81bae707804544ac7404d4e389d280`, MAC `3c:0f:02:de:c2:e0`); env set is the local/non-manager path only (`deploy/README.md:33`, `.env.example:55`). |
| `NODE_ENV` | `production` (hardened boot only) | Gates `_assert_production_boot_safe` (`config_loader.py:447`). Adds HARD requirements: `server.auth.enabled=true`, `TBOT_REQUIRE_DEVICE_TOKEN=true`, `JWT_PUBLIC_KEY`, mint secret, asset origin, non-bypassed AEC; forbids `ADMIN_AUTH_DISABLED=true` (`446-497`). |

> Consent-safety env (from child-safety audit): `TBOT_BYPASS_VOICE_CONSENT` **must NOT be `true`**
> (`voice_consent_client.py:22-28`); `TBOT_DEVICE_MINT_SECRET` must be **set** (consent fails closed
> without it, `:40-43`); `server.factory_test_claimed_all` must be **false** in prod (`:118-119`).

### Step 3 — Redeploy
```bash
# on the VPS host: set the env block above in /opt/tbot/.env, then:
./deploy/deploy-vps.sh --host <ip> --user <user> --tag <new-tag>
```
`deploy-vps.sh:154-216` preflight-rejects quick tunnels and enforces the `/tbot/v1/` + `/v1` paths.

### Step 4 — Smoke check
```bash
./deploy/smoke-vps.sh        # rejects quick tunnels (:69-80); confirms WS path + backend prefix
```
Then confirm the **new image actually carries the lesson runtime**: `curl` the OTA endpoint and trigger
`start_lesson` once before any demo.

---

## 4. Firmware deploy runbook (LCDWiki ES3C35P)

> **Pre-step (P0):** the face-hide is **uncommitted** on `ci/firmware-local-gates` (4 files:
> `main/lesson_handler.cc`, `main/display/lcd_display.cc`, `main/display/lcd_display.h`,
> `main/display/lvgl_display/lvgl_display.h`). Commit + push on a dedicated branch, then build from the
> committed tree. A clean checkout/stash/CI build ships **without** the fix.

### Step 1 — Build + flash (fleet-safe, black-screen-proof)
```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
./build-lcdwiki.sh /dev/cu.usbmodem101      # replace port with actual (see Step 3)
```
The script sources `$HOME/esp/esp-idf/export.sh`, exports
`SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.defaults.local"`
(`.local` **LAST** so `CONFIG_BOARD_TYPE_LCDWIKI_ES3C35P=y` wins), `rm -f sdkconfig`,
`idf.py set-target esp32s3`, **hard-gates** on the board config (`build-lcdwiki.sh:96-105`), then
`idf.py build` + `idf.py -p <PORT> flash`.

#### THE LCDWiki ES3C35P sdkconfig TRAP
ESP-IDF v5.5.4 only auto-appends `.<target>`, **not** `.local`. If you build by hand and omit
`sdkconfig.defaults.local`, the config falls back to Kconfig default `BOARD_TYPE_BREAD_COMPACT_WIFI`
(no LCD driver) → **black screen** (WiFi still works, masking it). Raw equivalent if not using the
script:
```bash
export SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.defaults.local"
rm -f sdkconfig
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/cu.usbmodem101 flash
```

### Step 2 — Verify the board landed (before/after build)
```bash
grep -E '^CONFIG_BOARD_TYPE_LCDWIKI_ES3C35P=y$' sdkconfig    # MUST print the line
```
Confirmed present in the current `sdkconfig` and in `sdkconfig.defaults.local:1`. The script aborts the
build/flash if it is absent.

### Step 3 — Flash port
```bash
ls /dev/cu.usbmodem*       # find the actual port after plugging in the robot
```
Default in the script is `/dev/cu.usbmodem101`; pass the real one. Device MAC `14:c1:9f:d1:a8:48`.
USB flash is the source of truth — OTA cannot correct a wrong-board unit (every image reports the same
`PROJECT_VER 2.2.34`, `CMakeLists.txt:24`, so `IsNewVersionAvailable()==false`).

App-partition headroom is healthy (4,128,768 B partition; `xiaozhi.bin` ~3.45 MB → ~661 KiB / 16.4%
free; `partitions/v2/16m.csv:6-7`) — the face-hide adds no partition risk.

### Step 4 — Native host gate (logic regression guard)
```bash
bash /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/scripts/run_host_native_lesson_coverage.sh
```
clang++ compiles the real `lesson_handler.cc` + stubs + cJSON, runs, `gcovr --fail-under-line 100`.
Non-tautological for the face-hide (`lesson_handler_host_test.cc:583/595`).
Env it needs (auto-set by the script; override only if your layout differs):
`SDKCONFIG_DEFAULTS`, `IDF_EXPORT=$HOME/esp/esp-idf/export.sh`,
`CJSON_DIR=$HOME/esp/esp-idf-v5.5.2/components/json/cJSON`, and `LLVM_COV_BIN`/`GCOVR_BIN`
(falls back to Xcode llvm-cov + `~/.espressif/.../gcovr`; exits 127 if absent).

> `lcd_display.cc:1341` (the visible LVGL toggle) has no host coverage → it must be proven on-device
> in §5.

---

## 5. On-device CP-7 acceptance walk

Goal: prove the whole arc on the real robot (LCDWiki ES3C35P, MAC `14:c1:9f:d1:a8:48`).
Pre-reqs: server deployed (§3) with `LESSON_SAMPLE_ENABLED=true`, `LESSON_SAMPLE_MODE=interactive`, a
real Google Live key with quota, and a **stable** WS endpoint; firmware flashed (§4) with the board
config confirmed.

1. **Boot + endpoint.** Power the robot. Confirm it reaches the stable OTA host and the OTA-advertised
   `wss://<stable-domain>/tbot/v1/` (not a quick tunnel). Confirm consent is satisfied for this device
   (gate is fail-closed; bypasses must be off).
2. **Say the trigger.** Speak the `start_lesson` voice trigger to enter the interactive sample lesson
   (admission via `connection.py:1843-1849`, sample branch ignores assignment).
3. **Face off + 3 layers.** Verify the idle emoji face **disappears** on `lesson_start`
   (`lesson_handler.cc:739` → `SetLessonMode(true)` → `lcd_display.cc:1341` hides `emoji_box_` + stops
   the GIF), and the lesson's 3 visual layers render.
4. **Gemini narration.** Confirm the robot narrates the step over the Live session (narration rides
   Live; no separate TTS needed).
5. **Say the word.** At the `completionClass=interactive` SAY-IT step, say the target word (e.g.
   **"barn"**). The child-response window forwards your audio to Live and routes the recognized
   transcript to the lesson runtime.
6. **Advances.** Confirm the lesson advances past the SAY-IT step on recognition.
7. **Happy + back to conversation.** Confirm the lesson completes with the happy state and the idle
   emoji face **returns** on `lesson_stop` (`lesson_handler.cc:758` → `SetLessonMode(false)`), with the
   robot returning to normal conversation. (Verify the error path too: `lesson_error` →
   `SetLessonMode(false)`, `:778`.)

> Watch for the safety gap (§6): during step 5 the model is not API-muted and may begin a free-form
> audio reply before the transcript routes — note any such leakage as a CP-7 finding.

---

## 6. Open P0 / P1 risks (from the audit)

### P0
1. **No `safety_settings` on the child Live session.** `_build_connect_config` attaches none
   (`google_live/client.py:252-308`); child session runs at Gemini default thresholds. Only a
   prompt-text `<child_safety>` block + an 8-pattern EN/VI transcript regex back it.
   **Fix:** attach `safety_settings` for all four `HarmCategory` (harassment, hate, sexually_explicit,
   dangerous_content) at `BLOCK_LOW_AND_ABOVE` in `_build_connect_config`; make it non-optional for
   child sessions.
2. **Lesson model-mute absent (reactive-only).** Child audio is forwarded into the AUDIO-modality Live
   session (`forward_decoded_input_audio → send_audio`); model can free-form an audio reply;
   `stop_output` fires only **after** a transcript routes (`session_provider/google_live.py:356-381,
   824-845`); audio chunks never screened (`audio_bridge.py:247-271`).
   **Fix:** open the lesson-answer turn `response_modalities=TEXT`-only, or set
   `_block_model_output_until_user_ack=True` at `open_lesson_child_response_window` time (before
   forwarding child audio).
3. **Production endpoint is a quick trycloudflare tunnel** in every committed source of truth
   (`config.yaml:20/35`, firmware `Kconfig.projbuild:5/18`, all `sdkconfig` variants). Tunnel restart =
   new random hostname = whole fleet stranded (OTA + WS unreachable).
   **Fix:** stand up a stable domain (named tunnel or real DNS+TLS) for OTA + WS; set
   `TBOT_PUBLIC_WEBSOCKET_URL` (env, no rebuild); reflash firmware with `CONFIG_OTA_URL`/
   `CONFIG_WEBSOCKET_URL` at the stable host (or seed the `wifi/ota_url` NVS key per
   `Kconfig.projbuild:13`); verify with `smoke-vps.sh`.
4. **Lesson + provider code only ships on a fresh image rebuild** (`Dockerfile-server:5`). Last
   documented prod tag `vps-20260525144756` (2026-05-25, `README.md:11`) predates the lesson/TTS/
   provider work (tree mtime 2026-06-25).
   **Fix:** `build-local.sh --tag <new>` → push → set `TBOT_SERVER_IMAGE=<new>` → `deploy-vps.sh` →
   `smoke-vps.sh`; confirm the new image runs a lesson before the demo.
5. **Face-hide change is uncommitted** on `ci/firmware-local-gates` (4 working-tree files); any clean
   checkout/stash/CI build ships without it.
   **Fix:** commit the 4 files on a dedicated branch, push, rebuild from the committed tree before
   flashing the fleet.

### P1
6. **Output screening is transcript-only + regex-only.** Audio chunks forwarded unscreened; screening
   silently disabled if `transcript_events_enabled` is false (`audio_bridge.py:177-181, 247-271`).
   **Fix:** make output transcription mandatory for any child session (treat screener-disabled as
   fail-closed), gate audio_chunk delivery on the screened transcript, back the regex with a real
   moderation pass.
7. **Consent not re-verified on Live re-open.** `_ensure_live_open_for_audio` checks only budget
   admission, not consent (`session_provider/google_live.py:451-474`); a mid-life withdrawal may not
   re-gate a later re-open.
   **Fix:** call `_ensure_active_voice_consent` (fail-closed) inside `_ensure_live_open_for_audio`
   before `_open_live_session`.
8. **Boot-crash footgun in `data/.config.yaml`.** Its `runtime_enabled:true` merges over the safe
   default; any run not passing `LESSON_RUNTIME_ENABLED` hard-crashes without mint-secret + asset-origin
   (`config_loader.py:424-436`).
   **Fix:** set `runtime_enabled:false` in `data/.config.yaml` and drive enablement from env, or
   document that its `true` requires the mint-secret + asset-origin env (add a flag comment in-file).
9. **Root `docker-compose.yml` can't run the demo + drops URL envs.** It does not forward
   `LESSON_SAMPLE_ENABLED`/`LESSON_SAMPLE_MODE`/`LESSON_SAMPLE_ASSET_BASE` nor
   `TBOT_PUBLIC_WEBSOCKET_URL`/`TBOT_BACKEND_API_URL` (`docker-compose.yml:11-20`); host exports are
   silently dropped, falling back to the baked quick tunnel.
   **Fix:** use `deploy/docker-compose.prod.yml` for any networked deploy (treat root compose as
   local-only); optionally add the missing passthroughs to the root compose.
10. **WS auth disabled in committed config** (`config.yaml:56 auth.enabled:false`); empty-token
    admission (`ota_handler.py:262-263`). Acceptable only behind an unguessable tunnel.
    **Fix for prod:** set `server.auth.enabled:true` (also unlocks factory_test self-claim), provision
    `auth_key`, confirm OTA minting + WS `_handle_auth` stay in lockstep.
11. **Firmware not built/flashed from current tree; `lcd_display.cc:1341` has no automated coverage.**
    **Fix:** flash per §4, then run the §5 on-device walk to prove the emoji_box_ toggle.

---

## Quick reference — minimal interactive-sample demo env

```
# server (deploy/docker-compose.prod.yml + /opt/tbot/.env)
TBOT_SERVER_IMAGE=dinhmanh11/tbot-server:<new-tag-from-this-tree>
TBOT_PUBLIC_WEBSOCKET_URL=wss://<stable-domain>/tbot/v1/
TBOT_BACKEND_API_URL=https://tbot-backend-8wmh.onrender.com/v1
LESSON_SAMPLE_ENABLED=true
LESSON_SAMPLE_MODE=interactive
LESSON_RUNTIME_ENABLED=false          # pin: prevents data/.config.yaml boot crash
GOOGLE_API_KEY=<real Live AUDIO key with quota>   # NOT a Live ephemeral token
TBOT_BYPASS_VOICE_CONSENT=            # must NOT be true
```
