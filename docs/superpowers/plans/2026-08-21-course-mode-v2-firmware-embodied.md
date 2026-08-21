# Course Mode V2 Firmware Embodied Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute approved Course Mode face, head, and arm intents through a lesson-owned, session-bound channel that never overlaps assessed child speech.

**Architecture:** Extend the existing lesson protocol rather than enabling unrestricted MCP inside lesson mode. The ESP server sends a named embodied intent with action identity and timing. Firmware validates session/generation, resolves safe servo values, applies face/head/arms, ACKs the outcome, and automatically returns to rest. Existing V1 lesson visual states remain valid.

**Tech Stack:** C++17, ESP-IDF, cJSON, existing `LessonHandler`, `Application`, `RobotUart`, LVGL displays, host-native Clang/ASan/UBSan tests, Pytest source-contract tests.

---

## File Map

- Create `main/lesson_embodied_action.h/.cc`: parser, preset resolver, lifecycle state.
- Modify `main/lesson_handler.cc/.h`: accept `lesson_embodied_action`, sequence it with visual/speech/listen state, emit ACK.
- Modify `main/application.cc/.h`: lesson-authorized servo methods distinct from blocked normal MCP methods.
- Modify `main/CMakeLists.txt`: compile new source.
- Create `tests/native/lesson_embodied_action_host_test.cc`.
- Create `scripts/run_host_native_lesson_embodied_action_test.sh`.
- Modify `tests/native/lesson_handler_host_test.cc` and lesson coverage runner.
- Create `tests/test_lesson_embodied_action_contract.py`.

### Task 1: Freeze the Wire Contract and Safe Presets

**Files:**
- Create: `main/lesson_embodied_action.h`
- Create: `main/lesson_embodied_action.cc`
- Modify: `main/CMakeLists.txt`
- Create: `tests/native/lesson_embodied_action_host_test.cc`
- Create: `scripts/run_host_native_lesson_embodied_action_test.sh`

- [ ] **Step 1: Write failing native parser tests**

Use this exact frame body:

```json
{
  "type": "lesson_embodied_action",
  "assignmentId": "...",
  "sessionId": "...",
  "stepId": "cat",
  "sequence": 17,
  "body": {
    "actionId": "cat-present-left-1",
    "actionGeneration": 4,
    "intent": "PRESENT_LEFT",
    "listenWindowPolicy": "complete_before_listening"
  }
}
```

Reject unknown keys, raw angles/percentages, invalid intent, duplicate action ID,
stale generation, mismatched session, and action while assessment is open.

- [ ] **Step 2: Run RED**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh
```

Expected: compile fails because the new source does not exist.

- [ ] **Step 3: Implement resolver types**

```cpp
enum class LessonEmbodiedIntent {
    kRestWarm,
    kGreetSmall,
    kInviteChild,
    kPresentCenter,
    kPresentLeft,
    kPresentRight,
    kListenStill,
    kThinkCurious,
    kAcknowledgeStory,
    kModelWord,
    kEncourageSmall,
    kTryDifferentWay,
    kCelebrateRecall,
    kCelebrateMastery,
    kComfortCalm,
    kPauseChoice,
    kGoodbyeSmall,
};

struct LessonEmbodiedPreset {
    const char* face;
    int head_percent;
    int left_arm_percent;
    int right_arm_percent;
    uint32_t hold_ms;
    uint32_t settle_before_listen_ms;
    bool return_to_rest;
};
```

The resolver owns percentages. Parsed JSON never contains them. Clamp values to
existing servo limits.

The parser maps these exact wire strings to the enum and rejects every other
value:

```text
REST_WARM, GREET_SMALL, INVITE_CHILD, PRESENT_CENTER, PRESENT_LEFT,
PRESENT_RIGHT, LISTEN_STILL, THINK_CURIOUS, ACKNOWLEDGE_STORY, MODEL_WORD,
ENCOURAGE_SMALL, TRY_DIFFERENT_WAY, CELEBRATE_RECALL, CELEBRATE_MASTERY,
COMFORT_CALM, PAUSE_CHOICE, GOODBYE_SMALL
```

- [ ] **Step 4: Run GREEN and commit**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh

git add main/lesson_embodied_action.h main/lesson_embodied_action.cc \
  main/CMakeLists.txt \
  tests/native/lesson_embodied_action_host_test.cc \
  scripts/run_host_native_lesson_embodied_action_test.sh
git commit -m "feat(lesson): define embodied action presets"
```

### Task 2: Add Lesson-Authorized Servo Execution

**Files:**
- Modify: `main/application.h`
- Modify: `main/application.cc`
- Test: `tests/native/lesson_embodied_action_host_test.cc`

- [ ] **Step 1: Add failing tests for lesson-only authority**

Prove normal `SendRightArmRaise()` remains rejected while
`lesson_runtime_active_` is true, but the new method requires a valid active
lesson token.

```cpp
CHECK_FALSE(app.SendRightArmRaise());
CHECK_TRUE(app.ApplyLessonEmbodiedPreset(active_token, preset));
CHECK_FALSE(app.ApplyLessonEmbodiedPreset(stale_token, preset));
```

- [ ] **Step 2: Implement explicit methods**

```cpp
bool ApplyLessonEmbodiedPreset(
    const LessonRuntimeToken& token,
    const LessonEmbodiedPreset& preset);
void CancelLessonEmbodiedAction(const LessonRuntimeToken& token);
void RestoreLessonRestPose(const LessonRuntimeToken& token);
```

Do not call or unblock the public MCP handler. Route directly through the
existing `RobotUart::SendLeftArmSetPercent`,
`RobotUart::SendRightArmSetPercent`, and `RobotUart::SendHeadSetPercent`
primitives after token validation.

- [ ] **Step 3: Run GREEN and commit**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh

git add main/application.h main/application.cc \
  tests/native/lesson_embodied_action_host_test.cc
git commit -m "feat(lesson): authorize session-bound servo actions"
```

### Task 3: Integrate Action Lifecycle With Lesson Handler

**Files:**
- Modify: `main/lesson_handler.h`
- Modify: `main/lesson_handler.cc`
- Modify: `tests/native/lesson_handler_host_test.cc`

- [ ] **Step 1: Write failing handler tests**

Cover applied, degraded, rejected, superseded, timed out, duplicate, stale,
disconnect, stop, pause, safety transition, and restart mid-action.

Required ACK body:

```json
{
  "acks": 17,
  "embodiedAction": {
    "actionId": "cat-present-left-1",
    "actionGeneration": 4,
    "outcome": "applied",
    "returnedToRest": true
  }
}
```

- [ ] **Step 2: Implement lifecycle state**

Track active action ID/generation, pending completion nonce, assessment-window
state, rest restoration, and consumed identities. Use the established lesson
queue/scheduler; do not block the WebSocket handler for servo hold duration.

- [ ] **Step 3: Enforce listening stillness**

Before firmware opens an interactive child response window:

```text
cancel pending motion -> center head -> lower both arms -> settle ACK
-> open child response window
```

If settling times out, degrade to screen-only and open listening only after the
commanded motion has been cancelled.

- [ ] **Step 4: Run native handler tests and commit**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh
bash scripts/run_host_native_lesson_handler_test.sh

git add main/lesson_handler.h main/lesson_handler.cc \
  tests/native/lesson_handler_host_test.cc
git commit -m "feat(lesson): execute embodied actions safely"
```

### Task 4: Replace Disappointed Course Mode Visual Semantics

**Files:**
- Modify: `main/lesson_handler.cc`
- Create: `tests/test_lesson_embodied_action_contract.py`
- Test: `tests/native/lesson_handler_host_test.cc`

- [ ] **Step 1: Write failing tests**

V1 `incorrect` rendering remains unchanged for compatibility. V2 must map a
normal miss/review path to `thinking`, `relaxed`, `neutral`, `winking`, `funny`,
or `silly`, never `sad`, `angry`, `crying`, `shocked`, or `embarrassed`.

- [ ] **Step 2: Add V2-specific presentation mapping**

Select by embodied intent, not by reusing V1 `incorrect`. Capability fallback:

```text
loving -> relaxed -> neutral
confident/laughing -> happy -> neutral
winking/funny/silly -> happy -> neutral
```

- [ ] **Step 3: Run tests and commit**

```bash
python3 -m pytest tests/test_lesson_embodied_action_contract.py -q
bash scripts/run_host_native_lesson_handler_test.sh

git add main/lesson_handler.cc tests/native/lesson_handler_host_test.cc \
  tests/test_lesson_embodied_action_contract.py
git commit -m "feat(course): align faces with supportive teaching"
```

### Task 5: Add Reduced-Motion and Capability Negotiation

**Files:**
- Modify: `main/application.cc`
- Modify: `main/lesson_handler.cc`
- Modify: `main/lesson_handler.h`
- Test: `tests/native/lesson_embodied_action_host_test.cc`
- Test: `tests/test_lesson_embodied_action_contract.py`

- [ ] **Step 1: Write failing capability tests**

Expected capability:

```json
{
  "lessonCourseMode": {
    "version": 2,
    "embodiedActions": true,
    "reducedMotion": true,
    "faces": ["neutral", "happy", "thinking", "relaxed"]
  }
}
```

- [ ] **Step 2: Implement reduced-motion fallback**

When enabled or when servo capability is absent, apply face/status/visual focus,
ACK `degraded` with reason `reducedMotion`, and never mark the lesson failed.

- [ ] **Step 3: Run GREEN and commit**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh
python3 -m pytest tests/test_lesson_embodied_action_contract.py -q

git add main/application.cc main/lesson_handler.cc main/lesson_handler.h \
  tests/native/lesson_embodied_action_host_test.cc \
  tests/test_lesson_embodied_action_contract.py
git commit -m "feat(course): negotiate reduced embodied motion"
```

### Task 6: Add Physical Journey Cases 16-20 and Coverage

**Files:**
- Modify: `tests/native/lesson_handler_host_test.cc`
- Modify: `scripts/run_host_native_lesson_coverage.sh`
- Create: `docs/validation/course-mode-embodied-hardware.md`

- [ ] **Step 1: Add software journeys**

Lock left/right alignment, lost ACK without replay, child barge-in cancellation,
emotional share calm pose, and reduced-motion completion.

- [ ] **Step 2: Run full firmware software gates**

```bash
bash scripts/run_host_native_lesson_embodied_action_test.sh
bash scripts/run_host_native_lesson_handler_test.sh
bash scripts/run_host_native_lesson_coverage.sh --txt --print-summary
python3 -m pytest \
  tests/test_lesson_embodied_action_contract.py \
  tests/test_lesson_content_contract.py -q
```

Expected: all pass and lesson line coverage remains 100%.

- [ ] **Step 3: Execute hardware validation**

Record exact device/build identity, 20 repeated sessions, motor-noise traces,
servo temperature, supply stability, ACK latency, stop/reconnect rest pose, and
reduced-motion run. Acceptance: zero commanded motion inside assessed speech
windows and zero terminal path leaving arms/head away from rest.

- [ ] **Step 4: Commit software and evidence separately**

```bash
git add tests/native/lesson_handler_host_test.cc \
  scripts/run_host_native_lesson_coverage.sh
git commit -m "test(course): lock embodied interaction journeys"

git add docs/validation/course-mode-embodied-hardware.md
git commit -m "docs(course): record embodied hardware validation"
```

## Phase Exit Gate

Do not enable backend V2 publishing until physical hardware proves listening
stillness, return-to-rest, duplicate rejection, and safe degradation.
