# Course Mode Runtime and Authoring Contract

## Purpose

This document defines the proposed server-authoritative runtime boundaries and
authored content required for Course Mode. Names and JSON are design-level
contracts for implementation planning; they are not yet production schemas.

## Versioning Decision

Course Mode introduces `courseCompanion.v2`. It must not silently change the
meaning of `tvideoJourney.v1`.

The v1 contract currently fixes exactly two authored steps, exactly two
contextual turns, three coaching levels, and per-step mastery. V2 introduces
session-level memory, optional one-word sessions, delayed recall, contextual
branches, and multi-dimensional evidence. Those are incompatible semantics and
require an explicit preset version.

## Ownership

| Component | Owns | Must not own |
| --- | --- | --- |
| Backend authoring | Approved targets, meanings, visual assets, pronunciation guidance, opening seeds, activity bank, safety-reviewed bridges | Live child state or mastery decision |
| CourseOrchestrator | Session phase, active target, branch state, time budget, support selection, evidence eligibility, close decision | Inventing curriculum or bypassing safety |
| ChildResponseInterpreter | Structured observations with confidence and provenance | Advancing mastery directly |
| FriendlyResponsePlanner | Short child-facing wording from approved facts and current state | Changing target, evidence, or next authoritative state |
| EmbodiedInteractionPlanner | Approved face, head, arm, and visual-focus intent | Raw servo control or mastery changes |
| SafetyRouter | Whether normal teaching may continue | Learning assessment |
| Firmware | Rendered visuals, motion execution, microphone/voice transport, ACKs | Semantic mastery or conversation policy |
| Progress backend | Durable evidence and review projection | Reconstructing mastery from raw chat text |

## Authored Session Contract

```json
{
  "presetId": "courseCompanion",
  "presetVersion": 2,
  "ageBand": "3-5",
  "durationPolicy": {
    "targetSeconds": 540,
    "softMinimumSeconds": 420,
    "softMaximumSeconds": 660
  },
  "languagePolicy": {
    "childFirstLanguage": "vi",
    "learningLanguage": "en",
    "supportStrategy": "balanced_fade"
  },
  "sessionOpening": {
    "checkInSeeds": [],
    "choiceSeeds": [],
    "maximumDirectTeachingTurns": 0
  },
  "wordTargets": [],
  "sessionClose": {
    "masteredTemplates": [],
    "learningTemplates": [],
    "stoppedTemplates": []
  }
}
```

`wordTargets` contains one required primary word and one optional secondary
word. More than two targets are invalid for this age-band preset.

## Authored Word Contract

```json
{
  "targetId": "animals.cat",
  "targetWord": "cat",
  "vietnameseMeanings": ["con mèo"],
  "approvedRelatedConcepts": ["pet", "meow", "tail", "ears"],
  "pronunciation": {
    "naturalModel": "cat",
    "slowModel": "cat",
    "approvedSegments": ["c", "at"],
    "vietnameseGuidance": ["Bắt đầu bằng âm /k/, rồi nói /æt/."],
    "acceptedNearForms": []
  },
  "openingMap": {
    "questionSeeds": [],
    "curiosityHooks": [],
    "likelyTopicBridges": {},
    "directElicitationSeeds": [],
    "knownWordChallengeSeeds": []
  },
  "meaningChecks": [],
  "practiceActivities": [],
  "transferChecks": [],
  "delayedRecallChecks": [],
  "visualRefs": [],
  "motionSlots": {},
  "embodiedIntents": {
    "discover": ["PRESENT_CENTER", "INVITE_CHILD"],
    "listen": ["LISTEN_STILL"],
    "encourage": ["ENCOURAGE_SMALL", "TRY_DIFFERENT_WAY"],
    "mastery": ["CELEBRATE_MASTERY"]
  },
  "contentSafetyVersion": 1
}
```

Every meaning, clue, pronunciation segment, activity, and bridge must remain
within this approved contract. Generated wording cannot add an unapproved fact.

## Runtime Session State

```text
PREPARING
  -> OPENING
  -> WORD_ACTIVE
       -> CONTEXT_BRANCH
       -> REGULATION_BREAK
       -> WORD_ACTIVE
  -> DELAYED_RECALL
  -> CLOSING
  -> COMPLETE

Any active state -> SAFETY_PAUSED -> CLOSING or COMPLETE
Any active state -> TECHNICAL_RECOVERY -> previous safe state or CLOSING
```

### Word State

```text
DISCOVER
UNDERSTAND
IMITATE
RECALL
USE
DELAYED_RECALL
DONE_FOR_SESSION
```

Word state and evidence level are separate. The orchestrator may return to a
discovery activity while retaining `SUPPORTED_SPEECH` evidence.

## Authoritative Snapshot

The snapshot must include enough information to resume without duplicate
teaching effects:

```json
{
  "lessonSessionId": "...",
  "presetVersion": 2,
  "sessionState": "WORD_ACTIVE",
  "activeTargetId": "animals.cat",
  "wordState": "RECALL",
  "evidenceLevel": "SUPPORTED_SPEECH",
  "turnSequenceId": 12,
  "attemptSequenceId": 5,
  "supportHistory": [],
  "answerLeakage": {
    "lastFullModelAtMs": 123456,
    "targetTextVisible": false,
    "interveningActivityCount": 1
  },
  "contextBranch": null,
  "ephemeralMemory": {},
  "pendingAction": null,
  "emittedEvidenceIds": []
}
```

Snapshot restore must preserve idempotency for audio prompts, visual cues,
physical motions, evidence events, and progress continuation.

## Interpreter Output

```json
{
  "semanticClass": "target_en",
  "speechClass": "near",
  "language": "en",
  "intent": "answer",
  "engagement": "hesitant",
  "emotionSignal": "unknown",
  "asrConfidence": 0.82,
  "assessmentEligible": true,
  "evidenceProvenance": "voice_transcript",
  "safetyClass": "normal"
}
```

Allowed `semanticClass` values initially:

- `target_en`
- `meaning_vi`
- `related`
- `unrelated`
- `unknown`

Allowed `speechClass` values initially:

- `exact`
- `near`
- `partial`
- `silence`
- `uncertain`
- `not_applicable`

`assessmentEligible` is false when ASR confidence is below threshold, robot
speech may have leaked into input, audio overlap is unresolved, or the child had
complete answer support.

## Decision Output

The orchestrator returns a deterministic action envelope:

```json
{
  "decisionId": "...",
  "nextState": "RECALL",
  "action": "SEMANTIC_CLUE",
  "acknowledgmentIntent": "recognize_effort",
  "teachingIntent": "contrast_cat_and_dog",
  "questionIntent": "elicit_target_from_visual",
  "visualCueId": "cat-contrast-02",
  "motionCueId": "listen-gentle",
  "embodiedIntent": "LISTEN_STILL",
  "mayModelTarget": false,
  "evidenceMutation": null,
  "branchMutation": null
}
```

The response planner renders child-facing speech from this envelope and the
approved word facts. It cannot edit `evidenceMutation` or `nextState`.

The embodied planner resolves `embodiedIntent` through the approved preset
matrix in `embodied-interaction.md`. It cannot send raw servo parameters through
the normal voice MCP path.

## Answer-Leakage Gate

An independent recall event is accepted only when:

- `mayModelTarget` was false for the current elicitation;
- no full target audio played during the cooldown;
- target text was hidden or not readable as an answer;
- at least one intervening activity occurred;
- the configured minimum elapsed time passed;
- ASR input was not contaminated by robot playback;
- no human/operator diagnostic endpoint injected the answer;
- the evidence identity has not already been consumed.

If any condition fails, the same speech may produce `SUPPORTED_SPEECH`, but not
`INDEPENDENT_RECALL`.

## Context Branch Runtime

Opening a branch does not advance or reset word evidence.

```json
{
  "branchId": "...",
  "type": "RELATED_STORY",
  "topicSummary": "grandmother has a white cat",
  "openedAtTurn": 7,
  "exchangeCount": 1,
  "softDeadlineMs": 60000,
  "returnBridgeIntents": ["white_cat_visual", "pet_sound_clue"],
  "readyToReturn": false
}
```

There is no universal hard two-turn limit. Routine branches have a soft budget;
emotional and safety branches follow their own policy. The orchestrator must
still prevent unbounded model conversation by using time, exchange, engagement,
and session-state limits.

## Technical Recovery

### ASR Uncertain or Failed

- Do not mutate mastery.
- Preserve the current word state.
- Use a child-safe ownership phrase about the robot's ears.
- Retry once through the same provider if policy allows, then use a non-audio
  choice activity or close positively.

### TTS Failed

- Do not display target text during an independent recall trial.
- Use an approved visual instruction that does not reveal the answer, or defer
  the trial.
- Preserve action identity so a late TTS completion cannot replay.

### Disconnect or Restart

- Restore the latest acknowledged authoritative snapshot.
- Do not replay a mastered celebration or count a duplicate response.
- If the conversational thread cannot be restored safely, acknowledge the
  interruption briefly and resume from a neutral activity.

### Time Budget Exhausted

- Finish the current child turn.
- Do not start a new word.
- Perform delayed recall only if it can be done without rushing.
- Close warmly and schedule review.

## Authoring Validation

A v2 Course Mode lesson is invalid when:

- age band is not supported;
- target count is zero or greater than two;
- the primary target lacks two distinct visuals or contexts;
- no independent and delayed-recall activities exist;
- an activity reveals the target while marked independent;
- Vietnamese meanings or pronunciation guidance are empty;
- opening seeds start with direct instruction rather than interaction;
- a bridge introduces facts outside approved concepts;
- praise templates overclaim mastery;
- a safety-sensitive topic lacks an approved response route;
- visual, motion, or audio references are missing from the lesson assets.
- an activity opens a listening window without a preceding `LISTEN_STILL`
  transition;
- a normal miss uses sad, angry, crying, shocked, or embarrassed emotion;
- an activity requires working servos to communicate its meaning.

## Migration Surface

Expected implementation areas include, without committing to exact task order:

- `core/lesson/conversation_contract.py`: add a separate v2 parser and immutable
  types; keep the v1 parser exact;
- `core/lesson/course_orchestrator.py`: new session-level pure state machine;
- `core/lesson/word_mastery.py`: evidence and answer-leakage rules;
- `core/lesson/conversation_persona.py`: bounded response-plan validation;
- `core/lesson/embodied_interaction.py`: intent resolution, capability fallback,
  timing, and return-to-rest policy;
- Google Live lesson tools: accept interpreter observations and execute
  authoritative decisions;
- backend lesson authoring and renderer-v4+ manifest generation: publish v2
  Course Mode content;
- progress events: persist mastery evidence without raw conversation content.
- firmware/server lesson motion protocol: provide a session-bound action channel
  because current normal servo MCP calls are rejected while lessons are active.

These paths are design guidance. The implementation plan must verify current
repository ownership before fixing exact files and interfaces.
