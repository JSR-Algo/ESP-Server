# Course Mode Measurement, Safety, and Validation

## Purpose

This document defines how Course Mode proves teaching quality, conversational
quality, safety, and runtime correctness. It prevents step completion, model
fluency, or one repeated word from being mistaken for learning.

## Success Metrics

### Primary Learning Metrics

| Metric | Definition |
| --- | --- |
| Independent recall rate | Percentage of target words named without answer leakage |
| Delayed recall rate | Percentage independently named after an intervening activity |
| Transfer rate | Percentage retrieved in a second visual or semantic context |
| Support depth | Highest and total support types used before independent recall |
| Model exposure count | Number of complete target models before first independent recall |
| Review-needed rate | Percentage ending below `MASTERED_TODAY`, segmented by reason |

### Interaction Quality Metrics

| Metric | Definition |
| --- | --- |
| Child talk share | Child voiced time divided by total voiced interaction time, excluding playback |
| Interruption rate | Robot barge-ins before child end-of-turn confidence |
| Question stacking | Robot turns containing more than one question |
| Acknowledgment coverage | Child turns followed by a response grounded in their content or intent |
| Context return quality | Context branches returned through a relevant bridge rather than abrupt reset |
| Voluntary continuation | Child accepts another activity when offered a genuine choice |
| Repeated-prompt rate | Same semantic prompt repeated without a support or modality change |
| Embodied alignment | Speech, visual focus, face, head, and arm cues communicate the same intent |
| Listening stillness | Percentage of assessed child speech windows with no servo movement |
| Motion usefulness | Purposeful gestures divided by all gestures; decorative or repeated motion is a defect |

Metrics must be interpreted cautiously. Longer child talk is not always better,
and a child choosing to stop is not a product failure.

## Evidence Event

```json
{
  "eventType": "word_evidence_recorded",
  "eventId": "...",
  "lessonSessionId": "...",
  "targetId": "animals.cat",
  "evidenceLevel": "INDEPENDENT_RECALL",
  "activityId": "cat-recall-visual-02",
  "contextId": "second_visual",
  "supportCodesSinceLastModel": [],
  "fullModelCount": 1,
  "elapsedSinceFullModelMs": 32000,
  "interveningActivityCount": 1,
  "assessmentConfidenceBand": "high",
  "provenance": "voice_transcript",
  "reviewNeeded": false
}
```

Do not include raw audio, full transcript, free-form family stories, inferred
diagnoses, or sensitive disclosures in the learning event.

## Session Summary

The parent-facing projection may state:

- words encountered;
- words understood;
- words said with support;
- words independently recalled;
- words scheduled for review;
- activity types the child engaged with.

It must not display:

- raw transcript or audio;
- pronunciation ranking against other children;
- labels such as lazy, shy, weak, delayed, difficult, or inattentive;
- emotional or family disclosures;
- a mastery claim unsupported by evidence.

## Safety Routing

### Normal Learning

Continue through the orchestrator with approved content and bounded model
wording.

### Ordinary Emotional Share

Pause direct teaching, acknowledge, listen briefly, and offer a choice to resume,
change activity, or stop. Do not persist the story as learning data.

### Immediate Help or Danger Signal

Suspend teaching. Use approved, calm language that directs the child to a nearby
trusted adult. Do not investigate, challenge, promise secrecy, or autonomously
contact third parties unless a separately approved product policy explicitly
authorizes that behavior.

### Model or Classifier Uncertainty

Choose the safer non-assessing path. Do not mutate mastery. Use a short fallback,
offer a visual choice, or close the lesson.

## Automated Validation Layers

### 1. Pure State-Machine Tests

Cover every transition for:

- one-word and two-word sessions;
- supported speech never producing independent recall;
- answer-leakage cooldown;
- delayed recall with and without intervening activity;
- transfer using a distinct context identity;
- support changes without repeated pressure;
- contextual branch open, continue, return, pause, and close;
- refusal, fatigue, time exhaustion, and safety pause;
- snapshot restore and idempotent event emission.

### 2. Contract and Authoring Tests

Reject:

- missing secondary visual/context for a mastery-capable target;
- independent checks that display or speak the answer;
- direct-instruction openings;
- overclaiming praise;
- more than two word targets;
- unsupported age bands;
- unapproved pronunciation guidance;
- bridge content outside the approved concept set.

### 3. Response-Plan Tests

For a corpus of child utterances, assert structural behavior rather than one
exact sentence:

- response acknowledges a concrete child detail;
- at most one question;
- no prohibited wording;
- target and approved facts remain unchanged;
- side-story reply occurs before redirection;
- safety cases do not redirect to vocabulary;
- uncertain ASR does not blame or assess the child.

### 4. Audio and Turn-Taking Tests

Prove:

- barge-in stops robot speech cleanly;
- robot playback does not contaminate recall assessment;
- hesitant pauses do not close the listening window too early;
- long child speech does not trigger overlapping prompts;
- ASR failure preserves the current learning state;
- reconnect does not replay prompts or duplicate evidence.
- servo movement completes before the assessment window opens;
- late motion is cancelled rather than overlapping child speech.

### 5. Embodied Interaction Tests

Prove:

- every pedagogical intent resolves to a supported face and safe choreography;
- left/right speech, screen highlight, head direction, and arm direction agree;
- normal misses never use sad, angry, crying, shocked, or embarrassed faces;
- supported speech receives smaller feedback than independent recall;
- delayed mastery is the only path to the strongest celebration;
- stale and duplicate action identities do not move servos;
- stop, pause, disconnect, safety pause, and restart return to rest;
- motion failure preserves the lesson and learning evidence;
- reduced-motion mode preserves every learning activity;
- continuous physical sessions stay within approved noise, temperature, power,
  and wear limits.

### 6. End-to-End Scripted Child Journeys

Minimum journeys:

1. child knows the first word immediately;
2. child answers only in Vietnamese;
3. child repeats correctly but cannot recall later;
4. child uses a near pronunciation and improves with one sound focus;
5. child remains silent through several supports;
6. child tells a related story and returns naturally;
7. child asks an unrelated question and returns by choice;
8. child says they are tired and ends early;
9. child shares sadness and the robot pauses teaching;
10. safety disclosure suspends teaching;
11. ASR and TTS fail independently;
12. disconnect occurs during a contextual branch;
13. session spends all available time on one word;
14. delayed recall succeeds after a second activity;
15. repeated speech is correctly excluded from mastery.
16. two-choice visuals coordinate left/right head and arm presentation;
17. a servo ACK is lost and the session continues without replay;
18. the child starts speaking before a gesture and the gesture is cancelled;
19. an emotional share cancels playful motion and enters a calm listening pose;
20. reduced-motion mode completes the same word journey.

Each journey must assert authoritative state, child-facing intent, emitted
events, rendered cue, and absence of duplicate actions.

## Human Quality Review

Before supervised child sessions, early-childhood educators and Vietnamese
English teachers review:

- question complexity;
- cultural and linguistic naturalness;
- pronunciation guidance;
- visual distractor fairness;
- praise calibration;
- redirection tone;
- emotional and safety scripts;
- expected session length and cognitive load.

## Supervised Child Validation

Automated tests cannot prove that children enjoy, understand, or trust the
interaction. Production readiness requires supervised observation under an
approved consent, privacy, and safeguarding protocol.

Observe:

- whether children understand the first question;
- whether they wait for or interrupt the robot naturally;
- whether the robot gives enough response time;
- whether side-story responses feel genuine;
- whether redirection causes frustration;
- whether visual and verbal clues match developmental ability;
- whether the child independently recalls the word;
- whether the child wants to continue or re-engage later.

Researchers must not pressure a child to finish a session or produce a target.

## Release Gates

Course Mode v2 cannot ship until:

1. v1 lessons remain behaviorally unchanged;
2. all pure state-machine and contract suites pass;
3. no supported-speech path can emit independent mastery;
4. answer-leakage and echo contamination are tested;
5. all 20 scripted journeys pass;
6. safety review approves response routes;
7. privacy review approves durable event fields and retention;
8. educator review approves the initial content pack;
9. supervised child validation meets product-defined thresholds;
10. rollback can disable v2 assignment while allowing active sessions to close
    according to the established lesson lifecycle policy.
11. physical motion testing proves no servo movement overlaps assessed child
    speech and all terminal paths return head and arms to rest.

## Initial Product Thresholds

These are candidate launch thresholds for a pilot, not universal learning
claims:

- zero false `MASTERED_TODAY` results in the scripted corpus;
- zero safety cases redirected back to vocabulary;
- zero raw transcript/audio fields in learning evidence events;
- less than 2% robot question-stacking turns in reviewed sessions;
- less than 5% abrupt context returns in reviewed sessions;
- at least 90% acknowledgment coverage for intelligible child turns;
- median time from child end-of-turn to robot acknowledgment below two seconds,
  excluding explicitly communicated technical recovery;
- no repeated identical prompt more than twice consecutively.
- 100% assessed child speech windows are free of commanded servo movement;
- zero normal miss/review outcomes display disappointed emotion;
- zero duplicate or stale embodied actions produce physical movement.

Learning rates should be baselined during the pilot rather than assigned an
arbitrary pass threshold before observing real children.

## Observability

Operational logs may include identifiers, state names, latency, support codes,
confidence bands, and event IDs. They should avoid raw child content.

Required counters include:

- session starts, closes, early stops, and safety pauses;
- word evidence transitions;
- invalid independent-recall attempts by leakage reason;
- contextual branch type and close reason;
- support-code selection and repeated-support prevention;
- ASR/TTS recovery paths;
- snapshot restores and duplicate action rejections;
- v1/v2 capability and fallback selection.
- embodied intent selection, motion ACK/degradation, listen-window exclusion,
  and return-to-rest outcome.

## Failure Interpretation

- Failure to master a word is a review signal, not a child failure.
- Failure to return from a side conversation is a dialogue-design observation,
  unless the child chose not to resume.
- Low ASR confidence is a technical uncertainty, not pronunciation evidence.
- Early stopping can be a successful respectful outcome.
- High completion with low independent recall indicates the system is still
  optimizing steps rather than teaching.
