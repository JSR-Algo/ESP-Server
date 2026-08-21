# Course Mode V2 ESP Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure, server-authoritative `courseCompanion.v2` runtime that teaches one or two words through truthful mastery evidence, natural contextual branches, and bounded friendly response plans.

**Architecture:** Keep `LessonConversationRuntime` and `tvideoJourney.v1` unchanged. Add a separate immutable V2 contract parser, pure word-mastery aggregate, session-level Course Orchestrator, and Google Live tool adapter. The language model submits structured observations and renders approved response intents; only the orchestrator mutates evidence and session state.

**Tech Stack:** Python 3, dataclasses/enums, Pytest, existing Google Live function tools, existing lesson runtime/forwarder, JSON manifests.

---

## File Map

- Create `main/tbot-server/core/lesson/course_mode_contract.py`: exact V2 manifest parser and immutable value types.
- Create `main/tbot-server/core/lesson/word_mastery.py`: evidence levels, support history, answer-leakage gate.
- Create `main/tbot-server/core/lesson/course_orchestrator.py`: session state machine and decisions.
- Create `main/tbot-server/core/lesson/course_response_plan.py`: child-safe response-plan validation.
- Create `main/tbot-server/core/lesson/embodied_intent.py`: V2 intent enum only; firmware dispatch lands in phase 2.
- Modify `main/tbot-server/core/lesson/runtime.py`: select V1 or V2 runtime by explicit preset and flag.
- Modify `main/tbot-server/plugins_func/functions/lesson_conversation.py`: add V2 observation/decision tools without changing V1 specs.
- Modify `main/tbot-server/core/voice/session_provider/google_live.py`: expose V2 prompt/tool context and preserve admission generation.
- Modify `main/tbot-server/core/lesson/forwarder.py`: forward privacy-safe word evidence events.
- Modify `main/tbot-server/config.yaml`: add the false-default V2 switch.
- Modify `main/tbot-server/config/config_loader.py`: parse `LESSON_COURSE_MODE_V2_ENABLED`.
- Modify `main/tbot-server/tests/test_config_loader_lesson_env_overrides.py`: lock strict flag behavior.
- Create focused tests named below.

### Task 1: Parse `courseCompanion.v2` Without Weakening V1

**Files:**
- Create: `main/tbot-server/core/lesson/course_mode_contract.py`
- Create: `main/tbot-server/tests/test_course_mode_contract.py`
- Test: `main/tbot-server/tests/test_lesson_conversation_runtime.py`

- [ ] **Step 1: Write failing exact-field parser tests**

Cover one target, two targets, wrong preset/version, target count 0/3, missing
transfer/delayed checks, unsafe IDs, duplicate activity IDs, unsupported faces,
and raw servo values.

```python
def test_v2_requires_one_primary_and_at_most_one_secondary_target():
    contract = CourseModeContract.from_mapping(_valid_manifest(target_count=1))
    assert contract.primary.target_id == "animals.cat"
    assert contract.secondary is None

    with pytest.raises(CourseModeContractError, match="TARGET_COUNT"):
        CourseModeContract.from_mapping(_valid_manifest(target_count=3))


def test_v2_rejects_raw_servo_authoring():
    manifest = _valid_manifest()
    manifest["wordTargets"][0]["practiceActivities"][0]["leftPercent"] = 60
    with pytest.raises(CourseModeContractError, match="INVALID_ACTIVITY_FIELDS"):
        CourseModeContract.from_mapping(manifest)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_course_mode_contract.py -q
```

Expected: collection fails because `course_mode_contract` does not exist.

- [ ] **Step 3: Implement immutable contract types**

Use these public types:

```python
class CourseModeContractError(ValueError):
    code: str

@dataclass(frozen=True)
class CourseActivity:
    activity_id: str
    kind: Literal["meaning", "practice", "transfer", "delayed_recall"]
    prompt_intent: str
    visual_ref: str
    embodied_intent: str
    reveals_answer: bool

@dataclass(frozen=True)
class CourseWordTarget:
    target_id: str
    target_word: str
    vietnamese_meanings: tuple[str, ...]
    approved_related_concepts: tuple[str, ...]
    opening_question_seeds: tuple[str, ...]
    meaning_checks: tuple[CourseActivity, ...]
    practice_activities: tuple[CourseActivity, ...]
    transfer_checks: tuple[CourseActivity, ...]
    delayed_recall_checks: tuple[CourseActivity, ...]

@dataclass(frozen=True)
class CourseModeContract:
    preset_id: Literal["courseCompanion"]
    preset_version: Literal[2]
    lesson_session_id: str
    primary: CourseWordTarget
    secondary: CourseWordTarget | None
```

The parser must require exact object keys, unique IDs, at least one meaning
check, one transfer check, one delayed check, and `reveals_answer=False` for
transfer/delayed activities.

- [ ] **Step 4: Verify V2 GREEN and V1 unchanged**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_course_mode_contract.py \
  tests/test_lesson_conversation_runtime.py -q
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/core/lesson/course_mode_contract.py \
  main/tbot-server/tests/test_course_mode_contract.py
git commit -m "feat(course): parse course mode v2 contracts"
```

### Task 2: Implement Truthful Word Mastery and Answer Leakage

**Files:**
- Create: `main/tbot-server/core/lesson/word_mastery.py`
- Create: `main/tbot-server/tests/test_word_mastery.py`

- [ ] **Step 1: Write failing evidence tests**

```python
def test_immediate_repetition_is_supported_not_independent():
    mastery = WordMastery(target_id="animals.cat")
    mastery.record_model(now_ms=1_000)
    result = mastery.record_speech(
        activity_id="cat-repeat",
        context_id="first-visual",
        now_ms=5_000,
        semantic="target_en",
        speech="exact",
        assessment_eligible=True,
    )
    assert result.level is EvidenceLevel.SUPPORTED_SPEECH


def test_mastery_requires_meaning_independent_transfer_and_delayed_recall():
    mastery = _mastery_with_meaning_and_independent_recall()
    mastery.record_transfer(activity_id="cat-second-visual", context_id="second-visual")
    result = mastery.record_delayed_recall(
        activity_id="cat-callback",
        context_id="callback-story",
        now_ms=70_000,
        assessment_eligible=True,
    )
    assert result.level is EvidenceLevel.MASTERED_TODAY
```

Also test target text visible, no intervening activity, echo contamination,
low-confidence assessment, duplicate evidence identity, later miss preserving
prior evidence, and review recommendation.

- [ ] **Step 2: Run RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_word_mastery.py -q
```

- [ ] **Step 3: Implement the aggregate**

```python
class EvidenceLevel(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    EXPOSED = "EXPOSED"
    UNDERSTOOD = "UNDERSTOOD"
    SUPPORTED_SPEECH = "SUPPORTED_SPEECH"
    INDEPENDENT_RECALL = "INDEPENDENT_RECALL"
    TRANSFERRED = "TRANSFERRED"
    MASTERED_TODAY = "MASTERED_TODAY"
    REVIEW_NEEDED = "REVIEW_NEEDED"

@dataclass(frozen=True)
class AnswerLeakage:
    last_full_model_at_ms: int | None
    target_text_visible: bool
    intervening_activity_count: int
    robot_audio_contaminated: bool

    def independent_eligible(self, now_ms: int) -> bool:
        return (
            not self.target_text_visible
            and not self.robot_audio_contaminated
            and self.intervening_activity_count >= 1
            and self.last_full_model_at_ms is not None
            and now_ms - self.last_full_model_at_ms >= 20_000
        )
```

Use immutable evidence records and consumed evidence IDs. Do not store transcript
text or pronunciation scores.

- [ ] **Step 4: Run GREEN**

```bash
cd main/tbot-server
python3 -m pytest tests/test_word_mastery.py -q
```

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/core/lesson/word_mastery.py \
  main/tbot-server/tests/test_word_mastery.py
git commit -m "feat(course): add truthful word mastery evidence"
```

### Task 3: Build the Session-Level Course Orchestrator

**Files:**
- Create: `main/tbot-server/core/lesson/course_orchestrator.py`
- Create: `main/tbot-server/core/lesson/embodied_intent.py`
- Create: `main/tbot-server/tests/test_course_orchestrator.py`

- [ ] **Step 1: Write failing session journey tests**

Cover opening, Vietnamese meaning, supported speech, independent recall,
context branch, refusal, fatigue, one-word time exhaustion, optional second word,
delayed recall, safety pause, snapshot restore, and duplicate decision IDs.

```python
def test_related_story_returns_through_child_detail_without_resetting_mastery():
    course = _opened_course()
    before = course.active_mastery.level
    opened = course.observe(_observation(intent="story", semantic="related"))
    assert opened.session_state is SessionState.CONTEXT_BRANCH
    assert opened.response_intent == "acknowledge_related_story"

    returned = course.close_context_branch(
        topic_summary="grandmother has a white cat",
        bridge_intent="white_cat_visual",
    )
    assert returned.session_state is SessionState.WORD_ACTIVE
    assert course.active_mastery.level is before
```

- [ ] **Step 2: Run RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_course_orchestrator.py -q
```

- [ ] **Step 3: Implement state and decisions**

```python
class SessionState(str, Enum):
    PREPARING = "PREPARING"
    OPENING = "OPENING"
    WORD_ACTIVE = "WORD_ACTIVE"
    CONTEXT_BRANCH = "CONTEXT_BRANCH"
    REGULATION_BREAK = "REGULATION_BREAK"
    DELAYED_RECALL = "DELAYED_RECALL"
    SAFETY_PAUSED = "SAFETY_PAUSED"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"

class WordState(str, Enum):
    DISCOVER = "DISCOVER"
    UNDERSTAND = "UNDERSTAND"
    IMITATE = "IMITATE"
    RECALL = "RECALL"
    USE = "USE"
    DELAYED_RECALL = "DELAYED_RECALL"
    DONE_FOR_SESSION = "DONE_FOR_SESSION"

@dataclass(frozen=True)
class CourseDecision:
    decision_id: str
    next_state: SessionState
    action: str
    acknowledgment_intent: str
    teaching_intent: str | None
    question_intent: str | None
    embodied_intent: EmbodiedIntent
    may_model_target: bool
    evidence_event: dict[str, object] | None
```

Implement a soft session deadline, one-question decisions, support-change rule,
and one primary/optional secondary target selection.

- [ ] **Step 4: Run GREEN**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_word_mastery.py \
  tests/test_course_orchestrator.py -q
```

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/core/lesson/course_orchestrator.py \
  main/tbot-server/core/lesson/embodied_intent.py \
  main/tbot-server/tests/test_course_orchestrator.py
git commit -m "feat(course): orchestrate adaptive word journeys"
```

### Task 4: Validate Friendly Response Plans

**Files:**
- Create: `main/tbot-server/core/lesson/course_response_plan.py`
- Create: `main/tbot-server/tests/test_course_response_plan.py`

- [ ] **Step 1: Write failing plan-validation tests**

Reject two questions, unsupported target facts, prohibited wording, mastery
praise without evidence, vocabulary redirection during safety, and disappointed
emotion on a miss.

```python
def test_normal_plan_has_at_most_one_question_and_acknowledges_child_detail():
    plan = CourseResponsePlan.from_mapping({
        "acknowledgment": "Một bạn mèo trắng ở nhà bà!",
        "relation": "Robot nghe con kể rồi.",
        "guidance": "Mình nhìn bạn trong hình nhé.",
        "invitation": "Trong tiếng Anh, bạn mèo là gì nhỉ?",
        "questionCount": 1,
        "embodiedIntent": "ACKNOWLEDGE_STORY",
    })
    assert plan.question_count == 1
```

- [ ] **Step 2: Run RED, implement, and run GREEN**

```bash
cd main/tbot-server
python3 -m pytest tests/test_course_response_plan.py -q
```

Implement exact keys and these prohibited teaching-feedback tokens in both
Vietnamese and English: `wrong`, `incorrect`, `easy`, `try harder`, `sai rồi`,
`dễ mà`, `cố hơn`. Safety plans must have `question_count <= 1`, no target
elicitation, and `COMFORT_CALM` or `PAUSE_CHOICE` embodied intent.

- [ ] **Step 3: Commit**

```bash
git add main/tbot-server/core/lesson/course_response_plan.py \
  main/tbot-server/tests/test_course_response_plan.py
git commit -m "feat(course): validate child-friendly response plans"
```

### Task 5: Add V2 Google Live Tools and Admission

**Files:**
- Modify: `main/tbot-server/plugins_func/functions/lesson_conversation.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Create: `main/tbot-server/tests/test_google_live_course_mode.py`
- Test: `main/tbot-server/tests/test_google_live_lesson_conversation.py`

- [ ] **Step 1: Write failing V2 tool tests**

Add separate tool names so V1 schemas remain frozen:

```text
course_observe_child
course_open_context
course_close_context
course_apply_response_plan
course_continue
```

`course_observe_child` arguments:

```json
{
  "lessonSessionId": "...",
  "turnSequenceId": 3,
  "observationId": "...",
  "semanticClass": "target_en",
  "speechClass": "exact",
  "language": "en",
  "intent": "answer",
  "engagement": "engaged",
  "safetyClass": "normal",
  "assessmentEligible": true,
  "confidenceBand": "high"
}
```

Tests must prove generation admission, stale model rejection, exact argument
sets, no transcript field, V1 tool equality, and model inability to submit
mastery directly.

- [ ] **Step 2: Run RED**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_course_mode.py \
  tests/test_google_live_lesson_conversation.py -q
```

- [ ] **Step 3: Implement V2 adapters**

Keep `_google_live_lesson_tool_admission`. Route based on runtime type and reject
V2 tools for V1 with `COURSE_MODE_NOT_ACTIVE`. Tool results expose decision
intents and identities, never mutable state references.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_course_mode.py \
  tests/test_google_live_lesson_conversation.py -q

git add plugins_func/functions/lesson_conversation.py \
  core/voice/session_provider/google_live.py \
  tests/test_google_live_course_mode.py
git commit -m "feat(course): expose authoritative v2 live tools"
```

### Task 6: Select V2 Runtime and Forward Privacy-Safe Evidence

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Modify: `main/tbot-server/core/lesson/forwarder.py`
- Modify: `main/tbot-server/config.yaml`
- Modify: `main/tbot-server/config/config_loader.py`
- Modify: `main/tbot-server/tests/test_config_loader_lesson_env_overrides.py`
- Create: `main/tbot-server/tests/test_course_mode_runtime_integration.py`
- Create: `main/tbot-server/tests/test_course_mode_forwarder.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Write failing selection and event tests**

Prove the flag defaults false, V1 is unchanged, V2 requires exact preset, and
the forwarder emits only:

```json
{
  "type": "word_evidence_recorded",
  "sequence": 12,
  "targetId": "animals.cat",
  "evidenceLevel": "INDEPENDENT_RECALL",
  "activityId": "cat-recall-02",
  "contextId": "second-visual",
  "supportCodesSinceLastModel": [],
  "elapsedSinceFullModelMs": 32000,
  "interveningActivityCount": 1,
  "assessmentConfidenceBand": "high",
  "reviewNeeded": false
}
```

Assert transcript, utterance, audio, score, pronunciation, child story, and
free-form confidence values are absent.

- [ ] **Step 2: Implement runtime selection and event serialization**

Add `LESSON_COURSE_MODE_V2_ENABLED` with false default. Create the V2 runtime
only after contract validation. Do not fall back into V1 after V2 session start.

- [ ] **Step 3: Run focused and V1 regression suites**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_course_mode_runtime_integration.py \
  tests/test_course_mode_forwarder.py \
  tests/test_config_loader_lesson_env_overrides.py \
  tests/test_lesson_runtime.py \
  tests/test_lesson_conversation_integration.py -q
```

- [ ] **Step 4: Commit**

```bash
git add core/lesson/runtime.py core/lesson/forwarder.py config.yaml \
  config/config_loader.py tests/test_config_loader_lesson_env_overrides.py \
  tests/test_course_mode_runtime_integration.py \
  tests/test_course_mode_forwarder.py
git commit -m "feat(course): run and report course mode v2"
```

### Task 7: Lock Fifteen Software-Only Child Journeys

**Files:**
- Create: `main/tbot-server/tests/fixtures/course_mode_journeys.json`
- Create: `main/tbot-server/tests/test_course_mode_e2e_journeys.py`

- [ ] **Step 1: Encode journeys 1-15 from the validation spec**

Each fixture contains observations and expected decisions, evidence levels,
branch outcomes, close reason, and emitted event count. Do not put real child
data in fixtures.

- [ ] **Step 2: Run all focused V2 and V1 tests**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_course_mode_contract.py \
  tests/test_word_mastery.py \
  tests/test_course_orchestrator.py \
  tests/test_course_response_plan.py \
  tests/test_google_live_course_mode.py \
  tests/test_course_mode_runtime_integration.py \
  tests/test_course_mode_forwarder.py \
  tests/test_course_mode_e2e_journeys.py \
  tests/test_lesson_conversation_runtime.py \
  tests/test_google_live_lesson_conversation.py -q
```

Expected: all pass; journey 3 repeats correctly but ends below mastery; journey
10 enters safety pause without a vocabulary prompt.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/course_mode_journeys.json \
  tests/test_course_mode_e2e_journeys.py
git commit -m "test(course): lock child conversation journeys"
```

## Phase Exit Gate

Do not begin firmware work until Tasks 1-7 are green and the decision/evidence/
embodied-intent names are frozen. The ESP server may emit embodied intent in
logs and decisions, but it must not dispatch servo motion yet.
