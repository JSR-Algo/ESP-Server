# Course Mode Word Mastery Design

## Status

Approved product direction on 2026-08-21. This document is the design source of
truth for implementation planning. It does not authorize a production rollout.

## Decision Summary

Course Mode teaches Vietnamese-speaking children aged 3-5 through one warm,
patient conversation lasting approximately 8-10 minutes. A session teaches one
or two English target words deeply. The robot listens to what the child actually
says, responds to relevant side conversations, and gently returns to the current
learning objective.

The primary learning outcome is not step completion or immediate repetition. A
word is learned for the day only when the child independently names it from a
visual or meaningful context without hearing the answer immediately beforehand.

The detailed normative contracts are split into five companion documents:

- [Pedagogy and mastery](../../course-mode/pedagogy-and-mastery.md)
- [Conversation persona and redirection](../../course-mode/conversation-persona.md)
- [Embodied interaction: face, head, and arms](../../course-mode/embodied-interaction.md)
- [Runtime and authoring contract](../../course-mode/runtime-contract.md)
- [Measurement, safety, and validation](../../course-mode/measurement-and-validation.md)

## Problem

The current conversational lesson runtime is bounded and safe, but it still
behaves primarily as a step executor:

- `tvideoJourney.v1` requires exactly two authored conversation steps;
- each word owns an isolated runtime rather than sharing session-level teaching
  memory;
- contextual conversation is capped at two turns per word;
- support escalates through three coaching levels and then advances;
- one authoritative pronunciation result can mark a word as mastered;
- the runtime does not require delayed, independent recall in a second context.

Those rules are useful for predictable delivery, but they are insufficient for
the requested teaching quality. A child can finish the lesson by following the
robot without proving that they understand and can independently say the word.
The robot can also sound dismissive when the child shares something meaningful
that does not fit the current step.

## Product Goal

Create a Course Mode in which the robot behaves like a patient early-years
teacher and trusted learning companion while retaining a bounded curriculum,
approved vocabulary, child-safety rules, deterministic progress authority, and
recoverable runtime state.

The robot's body is part of the teaching language. Screen emotion, head
direction, and arm movement are selected from the same authoritative teaching
decision as speech. They reinforce attention, listening, meaning, and
encouragement rather than running as decorative side effects.

At the end of a successful session:

1. the child feels heard and is willing to keep interacting;
2. the child understands the target word;
3. the child has practised its sound without shame or pressure;
4. the child can independently name the word from a visual or situation;
5. the child encounters the word again after a short delay;
6. the system records truthful evidence and schedules review when needed.

## Target Learner and Session Shape

| Dimension | Contract |
| --- | --- |
| Age | 3-5 years |
| First language | Vietnamese |
| Learning language | English |
| Language balance | Vietnamese and English balanced; Vietnamese support decreases as understanding grows |
| Session duration | Approximately 8-10 minutes, governed by engagement and safety rather than a hard content quota |
| New target count | One or two words |
| Primary completion criterion | Independent naming without an immediately preceding model |
| Available modalities | Robot voice, physical motion, and screen visuals; no camera assumption |
| Conversation style | Warm, brief, curious, patient, responsive, and non-judgmental |

## Core Design

Course Mode adds a session-level `CourseOrchestrator` above the existing
per-step conversation runtime. It owns the learning objective and decides which
bounded activity should happen next. A language model may phrase a response,
but it does not own mastery, lesson identity, safety policy, timing policy, or
the next curriculum target.

```text
Authored Course Mode contract
        |
        v
CourseOrchestrator -----> Session memory and time budget
        |
        +----> OpeningConversation
        +----> ChildResponseInterpreter
        +----> WordMasteryLoop
        +----> FriendlyResponsePlanner
        +----> SafetyRouter
        |
        v
Voice, visual, and motion actions
        |
        v
Authoritative learning evidence and review schedule
```

### CourseOrchestrator

The orchestrator owns:

- the current target word and mastery stage;
- the session time budget and natural stopping points;
- whether the robot is teaching, listening, chatting briefly, regulating, or
  closing the session;
- the child's recent utterance, conversational thread, and observed engagement;
- which supports have already been used;
- the independent-recall eligibility timer;
- the next best action within the authored contract.

It must be possible to serialize and restore its authoritative snapshot without
replaying a prompt, celebration, mastery event, or physical action.

### OpeningConversation

The lesson does not start by announcing a vocabulary list. It begins with one
easy, relational question, listens to the answer, and uses the answer or an
authored visual clue to approach the first target.

The opening follows:

```text
greet -> check in -> acknowledge -> invite curiosity -> show clue -> elicit word
```

The authored opening map supplies several question seeds and bridges. The model
may select and phrase a bridge, but every bridge must terminate at the active
target or at a safe pause. A child who already knows the word skips immediate
modeling and moves to an independent transfer check.

### ChildResponseInterpreter

The interpreter produces structured observations rather than free-form progress
decisions. At minimum it classifies:

- semantic relation to the active target;
- language used: Vietnamese, English, mixed, non-verbal/unknown;
- speaking evidence: exact, near, partial, unrelated, silence, or uncertain;
- conversational intent: answer, question, story, request for help, refusal,
  distress, or unclear;
- engagement signal: engaged, hesitant, distracted, tired, or unknown;
- ASR confidence and whether the evidence is sufficient for assessment.

Low-confidence ASR cannot produce mastery. Ambiguous evidence causes the robot
to continue naturally without blaming the child.

### WordMasteryLoop

Each word moves through:

```text
DISCOVER -> UNDERSTAND -> IMITATE -> RECALL -> USE -> DELAYED_RECALL
```

The child can remain in a stage, receive a different form of support, move
forward, or return to an earlier activity. The stages are evidence milestones,
not a mandatory sequence of spoken prompts.

`MASTERED_TODAY` requires all of the following:

- meaning demonstrated from a visual or meaningful contrast;
- independent English naming without a recent answer model;
- successful naming or use in a second context;
- a later recall after another activity or meaningful delay;
- assessment evidence above the configured confidence floor.

Immediate repetition is `SUPPORTED_SPEECH`, never independent mastery.

### FriendlyResponsePlanner

Every normal response is assembled in this order:

```text
acknowledge -> respond to meaning or feeling -> teach/redirect gently -> ask one thing
```

The response is normally one or two short sentences. The robot must not ask
multiple questions in one turn, deliver a lecture, ignore the child's story, or
use praise that is not supported by evidence.

### EmbodiedInteractionPlanner

The embodied planner converts the same teaching decision into an approved face,
head pose, arm choreography, and visual focus cue. It uses only named presets;
the language model never sends raw servo values.

- Center head, lower arms, and show a relaxed/listening face while the child
  speaks.
- Turn and present toward the relevant side for a two-picture meaning choice.
- Use a small arm gesture after a brave attempt.
- Reserve both-arms celebration for independent or delayed recall.
- Return to a neutral rest pose after every temporary gesture.

Motion is best-effort and never blocks listening or mastery. A missing servo ACK
degrades to face and screen cues without replaying the motion blindly.

### Contextual Conversation

Side conversation is controlled at session level rather than exhausted after a
fixed two-turn allowance on one word. A contextual branch has:

- a reason for opening;
- a child-led topic summary;
- a soft time budget;
- a return bridge candidate;
- an explicit outcome: returned, paused, child chose to continue chatting,
  session ended, or safety escalated.

Routine side conversation normally lasts one or two exchanges. A child who is
sharing feelings may receive more time. The robot never treats the learning
objective as more important than immediate emotional or physical safety.

## Session Flow

The expected 8-10 minute shape is adaptive:

| Phase | Typical duration | Outcome |
| --- | ---: | --- |
| Relationship opening | 45-90 seconds | Child responds and the robot reaches a relevant clue |
| First word journey | 3-5 minutes | Meaning, supported speech, independent attempt, and transfer opportunity |
| Movement or story transition | 30-60 seconds | Attention resets and delayed-recall spacing begins |
| Second word or deeper first-word practice | 2-4 minutes | Orchestrator chooses depth over quota |
| Delayed recall | 60-90 seconds | Previously taught word is elicited without a recent model |
| Warm close | 20-40 seconds | Specific truthful feedback and a positive stopping point |

The second word is optional. If the first word needs more support or the child is
deeply engaged in a productive activity, the orchestrator spends the session on
one word.

## Patience Policy

Patience means changing support, not repeating the same demand indefinitely.
After insufficient evidence, the robot chooses among:

1. wait silently for 4-6 seconds while showing a listening state;
2. shorten the question;
3. acknowledge a Vietnamese answer and ask for the English label;
4. contrast two visuals or offer two choices;
5. provide a semantic, motion, or first-sound hint;
6. model slowly and invite speaking together;
7. change to a playful activity;
8. defer the independent check until later;
9. close positively and schedule review.

The robot does not say `wrong`, `easy`, `you already know this`, or equivalents.
It does not require the child to continue until correct.

## Memory and Privacy

### Ephemeral Session Memory

Course Mode may retain during the active session:

- recent child statements needed for coherent replies;
- current mood or engagement observation, expressed as uncertain and temporary;
- the child's chosen character, picture, or game preference;
- support and modeling history;
- word-stage evidence;
- open contextual branch and return bridge;
- pending safety or pause state.

### Durable Learning Memory

The system may durably retain only learning-relevant summaries defined by the
product data policy, such as:

- mastery stage reached;
- support level and activity type that helped;
- review-needed reason;
- timestamps and evidence provenance;
- aggregate engagement and interruption counts.

Raw audio, full transcripts, family stories, inferred personality traits,
health conclusions, or emotional diagnoses are not required for this design and
must not be introduced implicitly.

## Compatibility and Migration

The current `tvideoJourney.v1` contract remains readable during migration.
Course Mode uses a new versioned preset, provisionally `courseCompanion.v2`,
because the old exact-two-step, two-context-turn, and immediate-mastery semantics
cannot be reinterpreted safely in place.

A v1 lesson continues to use the v1 runtime. A v2-capable lesson and server use
the session orchestrator. Capability negotiation must fail closed to the v1
experience rather than partially applying v2 mastery semantics to v1 content.

## Non-Goals

- Open-ended companion chat outside Course Mode.
- Camera-based gaze, gesture, pointing, face, or emotion recognition.
- Diagnosing speech disorders, emotional conditions, family situations, or
  developmental ability.
- Letting a language model invent target vocabulary or declare mastery.
- Requiring sentence production to pass a single-word lesson.
- Replacing the existing lesson rendering, asset, motion, assignment, or
  lifecycle authority.
- Storing full child conversations by default.

## Rollout Slices

### Slice 1: Truthful Mastery

Introduce session-level word evidence, distinguish supported speech from
independent recall, require delayed recall, and preserve v1 compatibility.

### Slice 2: Natural Opening and Redirection

Add authored opening maps, child-response intent classification, contextual
branches, and response-planning constraints.

### Slice 3: Adaptive Activities

Allow the orchestrator to select visual contrasts, movement, sound play, mini
stories, and deferred checks according to evidence and engagement. This slice
also introduces lesson-owned embodied action presets grounded in the existing
arm, head, emotion, visual-state, and motion-preset capabilities.

### Slice 4: Parent Learning Summary

Project privacy-safe mastery evidence and review needs without exposing raw
audio or transcripts.

## Acceptance Boundary

Implementation planning may begin only after the product owner reviews this
document and its four companion contracts. Production rollout additionally
requires the validation gates in `measurement-and-validation.md`, including
scripted adversarial dialogue tests and supervised sessions with real children
under an approved research and privacy protocol.
