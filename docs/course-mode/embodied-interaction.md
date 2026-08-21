# Course Mode Embodied Interaction

## Purpose

This document defines how Course Mode coordinates the robot's screen emotion,
head direction, arm movement, lesson visual, and speech. Physical action is a
teaching cue and a listening signal, not decoration.

## Current Source Reality

The existing source already provides useful building blocks:

- `core/lesson/motion_presets.py` defines named presets: `rest`, `teach`,
  `presentLeft`, `presentRight`, `listen`, `thinking`, `encourage`, `tryAgain`,
  `celebrate`, and `goodbye`;
- device MCP exposes left/right arm raise/lower, percentage positioning, head
  left/right/center, angle, percentage, and a left-to-right sweep;
- lesson visual-state frames already carry `state`, `overlayKey`,
  `motionPreset`, and `visualGeneration`;
- firmware maps lesson visual states to face/status presentations;
- the TFT neon face set contains 21 expressions: `neutral`, `happy`, `laughing`,
  `funny`, `sad`, `angry`, `crying`, `loving`, `embarrassed`, `surprised`,
  `shocked`, `thinking`, `winking`, `cool`, `relaxed`, `delicious`, `kissy`,
  `confident`, `sleepy`, `silly`, and `confused`;
- firmware accepts servo primitives for both arms and head outside lesson mode.

The current path is not sufficient for Course Mode:

- normal servo actions are explicitly ignored while lesson runtime is active;
- server lesson motion presets call the normal MCP tools, so a preset can be
  rejected in lesson mode;
- generic emotion-to-arm coupling is disabled in current firmware;
- several current visual labels, such as sad face plus "Chưa đúng", conflict
  with the non-shaming pedagogy;
- presets do not define timing, return-to-rest, interruption, or idempotency;
- motion and facial emotion are not selected from one pedagogical action plan.

Course Mode therefore needs a lesson-owned embodied-action path. Re-enabling
unrestricted MCP during lessons is not acceptable.

## Design Principles

1. Every action has a teaching or relational purpose.
2. Listening posture is more important than constant movement.
3. The robot never physically acts disappointed in the child.
4. Strong celebration is reserved for strong evidence.
5. Temporary gestures automatically return to a safe rest pose.
6. The server owns semantic intent; firmware owns safe servo execution.
7. Motion failure degrades to face and screen behavior without blocking speech.
8. Duplicate, stale, or interrupted gestures must not replay.
9. Servo movement must not create audio that contaminates speech assessment.
10. Reduced-motion capability is a first-class supported mode.

## Embodied Intent Contract

The Course Orchestrator selects one named intent:

```text
REST_WARM
GREET_SMALL
INVITE_CHILD
PRESENT_CENTER
PRESENT_LEFT
PRESENT_RIGHT
LISTEN_STILL
THINK_CURIOUS
ACKNOWLEDGE_STORY
MODEL_WORD
ENCOURAGE_SMALL
TRY_DIFFERENT_WAY
CELEBRATE_RECALL
CELEBRATE_MASTERY
COMFORT_CALM
PAUSE_CHOICE
GOODBYE_SMALL
```

The language model may request an intent through a response plan, but only the
authoritative orchestrator may approve it.

## Recommended Preset Matrix

| Intent | Face | Head | Arms | Teaching purpose |
| --- | --- | --- | --- | --- |
| `REST_WARM` | `neutral` or `relaxed` | center | both lowered | Calm default between turns |
| `GREET_SMALL` | `happy` | center | right arm raises then lowers | Friendly welcome without overwhelming the child |
| `INVITE_CHILD` | `winking` or `happy` | center | right arm at a moderate level, then rest | Offer a game or choice |
| `PRESENT_CENTER` | `happy` | center | right arm moderate | Draw attention to the central teaching object |
| `PRESENT_LEFT` | `neutral` | left | left arm moderate | Refer to the left visual choice |
| `PRESENT_RIGHT` | `neutral` | right | right arm moderate | Refer to the right visual choice |
| `LISTEN_STILL` | `relaxed` or `thinking` | center | both lowered | Show patience and minimize motor noise while listening |
| `THINK_CURIOUS` | `thinking` | brief left turn, then center | both lowered | Signal that the robot is processing, not judging |
| `ACKNOWLEDGE_STORY` | `loving` or `happy` | center | one small open/present gesture | Show the child's story was received |
| `MODEL_WORD` | `confident` | center | right arm moderate, held only during model | Mark a clear teaching model |
| `ENCOURAGE_SMALL` | `happy` | center | short right-arm lift | Recognize effort without claiming mastery |
| `TRY_DIFFERENT_WAY` | `funny`, `silly`, or `winking` | side then center | small alternating presentation | Change modality playfully; never display sadness |
| `CELEBRATE_RECALL` | `happy` | center | both arms moderate, once | Celebrate independent recall |
| `CELEBRATE_MASTERY` | `laughing` or `confident` | center | both arms high, once, then rest | Strongest reward for delayed mastery evidence |
| `COMFORT_CALM` | `loving` or `relaxed` | center | both lowered | Reduce stimulation during emotional sharing |
| `PAUSE_CHOICE` | `relaxed` | center | both lowered | Offer rest/change/stop without pressure |
| `GOODBYE_SMALL` | `happy` | center | right arm raises and lowers | Warm close for any outcome |

The exact face availability is capability-negotiated. If a preferred face is
unavailable, firmware falls back to `happy`, `thinking`, `relaxed`, or `neutral`
according to intent.

## Pedagogical Use by Word Stage

### Opening

- Begin in `REST_WARM` rather than a continuous celebration loop.
- Use `GREET_SMALL` once per session.
- Pair a choice question with `INVITE_CHILD`, then stop moving before the child
  answers.

### Discover and Understand

- Use `PRESENT_CENTER` to introduce a single object.
- Use `PRESENT_LEFT` and `PRESENT_RIGHT` sequentially for two-choice meaning
  checks. Speech, highlight, head direction, and arm direction must agree.
- Return to `LISTEN_STILL` before opening the microphone assessment window.

### Imitate and Pronunciation

- Use `MODEL_WORD` only while the robot provides a full model.
- Do not move servos during the child's speech window; motor noise can corrupt
  ASR and make the robot appear inattentive.
- After a near attempt, use `ENCOURAGE_SMALL`, not a sad or disappointed face.
- When changing strategy, `TRY_DIFFERENT_WAY` communicates playfulness rather
  than correction.

### Recall and Transfer

- The elicitation uses a presenting intent followed by `LISTEN_STILL`.
- A supported answer receives `ENCOURAGE_SMALL`.
- Independent recall receives `CELEBRATE_RECALL`.
- Only delayed recall meeting `MASTERED_TODAY` may trigger
  `CELEBRATE_MASTERY`.

### Side Conversation

- Related stories use `ACKNOWLEDGE_STORY`, then return to `LISTEN_STILL`.
- Ordinary questions use `THINK_CURIOUS` while processing, followed by a brief
  response.
- Emotional sharing uses `COMFORT_CALM`; no playful or celebratory motion should
  run until the child appears ready.

### Close

- Every outcome can use `GOODBYE_SMALL`.
- Do not use a sad face when a word remains `REVIEW_NEEDED`.
- Close in a stable rest pose so a stopped lesson does not leave arms raised or
  the head turned.

## Action Envelope

```json
{
  "actionId": "...",
  "lessonSessionId": "...",
  "turnSequenceId": 12,
  "intent": "PRESENT_LEFT",
  "face": "neutral",
  "head": {
    "action": "turn_left",
    "returnToCenter": true
  },
  "arms": {
    "leftPercent": 60,
    "rightPercent": 0,
    "returnToRest": true
  },
  "timing": {
    "startAfterSpeechMs": 0,
    "holdMs": 700,
    "settleBeforeListenMs": 250
  },
  "audioPolicy": "complete_before_listening",
  "fallbackFace": "neutral"
}
```

Raw percentages are produced by the trusted preset resolver, not authored by a
lesson or generated by a model. Firmware clamps every value and may replace the
motion with reduced-motion behavior.

## Lesson-Owned Motion Channel

The motion channel must be distinct from unrestricted voice MCP tools.

Required properties:

- accepted only for the active lesson session and authoritative action identity;
- named preset or resolved safe envelope only;
- servo limits enforced in firmware;
- one action at a time with cancellation and return-to-rest;
- ACK states: `applied`, `degraded`, `rejected`, `superseded`, or `timed_out`;
- stale generation and duplicate action rejection;
- motion disabled while a child assessment window is open;
- immediate cancellation on stop, pause, disconnect, replacement, or safety
  transition;
- no fallback to unrestricted MCP when the lesson channel rejects an action.

This closes the current gap where lesson mode blocks the normal servo path.

## Timing and Turn Taking

The safe sequence for an elicitation is:

```text
speech/presentation -> gesture settles -> return to listening pose
-> microphone assessment window opens -> child speaks -> window closes
-> thinking face -> response
```

Never start an arm or head motion after the assessment window opens. If a motion
is late, cancel or suppress it instead of contaminating the audio turn.

## Frequency and Comfort Limits

Initial conservative limits:

- no more than one purposeful servo gesture per robot speaking turn;
- at least 1.5 seconds between servo choreographies;
- no continuous head scanning during conversation;
- no more than two high-energy both-arm celebrations per session;
- no high-energy gesture during emotional sharing, fatigue, refusal, or safety
  response;
- all gestures return to rest within two seconds unless the preset is explicitly
  a stable listening pose;
- reduced-motion mode replaces all gestures with face and screen cues.

These limits require hardware validation for servo temperature, noise, wear,
power draw, and child comfort.

## Emotion Semantics

Faces communicate the robot's stance, not a judgment of the child.

- `thinking` means the robot is processing.
- `confused` may be used only to own robot uncertainty, such as unclear audio;
  it must not label the child's answer.
- `sad`, `crying`, `angry`, `shocked`, and `embarrassed` are not normal teaching
  feedback states.
- `loving` and `relaxed` may support warm listening but must not imply exclusive
  attachment.
- `happy`, `laughing`, and `confident` scale with evidence strength.
- `silly`, `funny`, and `winking` are occasional modality-change tools, not
  constant stimulation.

The current firmware mapping of `incorrect` to a sad face and "Chưa đúng" must
not be used by Course Mode v2. V2 should use `TRY_DIFFERENT_WAY` with neutral,
thinking, relaxed, or playful presentation.

## Degradation

| Failure | Required behavior |
| --- | --- |
| Face unavailable | Use semantic fallback face |
| One servo unavailable | Perform remaining safe channels or screen-only cue |
| Motion channel unavailable | Continue lesson with face, visual highlight, and speech |
| ACK slow or missing | Mark degraded; do not replay automatically |
| Motion overlaps listen opening | Cancel motion and delay/listen safely |
| Restart mid-gesture | Restore neutral face, center head, lower arms before resuming |
| Reduced-motion enabled | Face and screen cues only |

Motion degradation cannot change learning evidence.

## Authoring Rules

Content authors choose an embodied intent, never raw device commands. Each
activity declares:

- allowed intent before speech;
- required `LISTEN_STILL` transition;
- visual focus target;
- whether any playful face is allowed;
- whether reduced-motion still preserves meaning;
- fallback when the child is tired, upset, or not looking at the robot.

An activity is invalid if it requires motion to understand the answer. Visual
and verbal alternatives are mandatory because servos can degrade.

## Validation

Automated and physical tests must prove:

- every intent maps to available face and motion capabilities;
- speech, screen highlight, head direction, and arm direction agree;
- listen windows never overlap servo movement;
- duplicate/stale actions do not move the robot;
- stop and disconnect return the robot to rest;
- unsupported motion degrades without stopping the lesson;
- `REVIEW_NEEDED` never triggers disappointed emotion;
- celebration intensity matches evidence level;
- reduced-motion mode completes every activity;
- repeated sessions remain inside servo temperature and power limits;
- real children do not interpret head/arm cues as threatening, impatient, or
  distracting.
