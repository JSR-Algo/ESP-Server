# Course Mode Pedagogy and Word Mastery

## Purpose

This document defines how Course Mode teaches one English word to a
Vietnamese-speaking child aged 3-5. It is normative for learning progression,
support escalation, mastery decisions, and review scheduling.

## Teaching Principles

1. Meaning precedes testing.
2. The child talks more by the end of the word journey; the robot does not fill
   every silence.
3. Immediate repetition is practice, not proof of memory.
4. Support decreases gradually and returns when needed.
5. A new context is required to show transfer rather than script memorization.
6. Praise describes what the child actually did.
7. The robot protects confidence without fabricating success.
8. One deeply learned word is preferable to two rushed words.

## Evidence Levels

| Level | Meaning | Minimum evidence | Must not be inferred from |
| --- | --- | --- | --- |
| `NOT_STARTED` | No learning evidence yet | Session entered | Assignment or asset playback |
| `EXPOSED` | Child has encountered the word | Target shown or spoken meaningfully | Lesson step completion alone |
| `UNDERSTOOD` | Child connects the word to its meaning | Correct visual choice, relevant Vietnamese label, gesture-equivalent supported by UI choice, or correct semantic answer | English sound similarity alone |
| `SUPPORTED_SPEECH` | Child says the word with active support | Speaking together, immediate repetition, visible answer choice, first-sound hint, or slow model | Independent recall |
| `INDEPENDENT_RECALL` | Child names the target without answer leakage | Valid English naming from a picture or clue after the model-cooldown rule | Repeating within the cooldown window |
| `TRANSFERRED` | Child retrieves or uses the word in a different context | Correct naming from a second visual, story, contrast, or short phrase | Same prompt or same picture repeated |
| `MASTERED_TODAY` | Child demonstrates stable learning in this session | `UNDERSTOOD` + `INDEPENDENT_RECALL` + `TRANSFERRED` + delayed recall | A pronunciation score or one correct attempt |
| `REVIEW_NEEDED` | Word should return later | Session closes without complete mastery or confidence is insufficient | A negative label about the child |

Evidence levels are monotonic within one authoritative session, except that an
invalidated ASR result may be withdrawn before persistence. A later mistake does
not erase prior learning evidence; it changes the review recommendation.

## Word Journey

### 1. Discover

Goal: create curiosity and attach the target to something visible or meaningful.

Suitable activities:

- reveal part of an image;
- play an animal or object sound;
- ask a familiar experience question;
- let the robot perform a related motion;
- introduce a tiny problem the target can solve.

The robot does not provide the answer before giving the child a fair chance to
recognize the object in Vietnamese or English.

### 2. Understand

Goal: establish meaning separately from pronunciation.

Accepted evidence includes:

- selecting the target between two clear visuals;
- answering with the correct Vietnamese concept;
- rejecting a deliberately silly mismatch;
- answering a simple function or attribute question authored for the target.

The contrast must be developmentally fair. Similar-looking distractors must not
be used as the first meaning check.

### 3. Imitate

Goal: let the child experience the sound safely.

The robot may:

- say the word naturally once;
- repeat slowly using approved segments;
- invite the child to whisper, speak together, or use a funny voice;
- use Vietnamese articulatory guidance approved by content authors;
- acknowledge partial sound success specifically.

The robot must not expose numeric pronunciation scores or compare the child to
other children.

### 4. Recall

Goal: remove answer support and elicit independent naming.

Before a recall trial, the system checks answer leakage. The trial is not
independent if any of these happened too recently:

- robot spoke the complete target;
- target audio played;
- target text remained visibly readable;
- the robot offered a rhyming completion that reveals the answer;
- a multiple-choice option contained the spoken target.

The initial model-cooldown target is one intervening activity and at least 20
seconds. Research validation may adjust the duration, but it cannot be reduced
to zero.

### 5. Use and Transfer

Goal: demonstrate that the child recognizes the word outside the memorized
prompt.

For a single-word objective, acceptable transfer includes:

- naming a different picture of the same concept;
- finding the target in a two-item scene;
- completing a meaningful two-word phrase with strong visual support;
- answering a tiny story question where the target is the natural answer.

A sentence is enrichment, not a mandatory pass condition for children aged 3-5.

### 6. Delayed Recall

Goal: retrieve the word after attention has moved elsewhere.

The check occurs after a movement break, second word, short story, or other
intervening activity. The robot does not announce that it is testing memory. It
uses a playful callback, ideally connected to something the child said earlier.

## Support Ladder

Support is represented by type, not only a numeric attempt count.

| Support code | Robot action | Answer leakage |
| --- | --- | --- |
| `WAIT` | Quiet listening posture for 4-6 seconds | None |
| `SHORTEN` | Ask the same idea with fewer words | None |
| `REFOCUS_VISUAL` | Highlight or animate the relevant object | None |
| `SEMANTIC_CLUE` | Give function, sound, color, or story clue | Low |
| `TWO_CHOICE` | Offer a developmentally fair contrast | Medium |
| `FIRST_SOUND` | Give approved starting sound | High |
| `SLOW_MODEL` | Speak approved segmented model | Complete |
| `SPEAK_TOGETHER` | Invite simultaneous supported speech | Complete |
| `CHANGE_ACTIVITY` | Move to another modality without increasing pressure | Depends on activity |
| `DEFER_RECALL` | Continue session and test later | None |

The orchestrator should not repeat the same support code more than twice in a
row. After two ineffective supports, it changes modality, defers the check, or
closes the word positively.

## Response Cases

### Correct English Target

The interpreter records whether the trial was supported or independent. The
robot gives specific acknowledgment and either changes context or schedules
delayed recall. It does not immediately ask the identical question again.

### Correct Vietnamese Meaning

Record `UNDERSTOOD`. Respond to the meaning first, then offer the English label
as teaching. A Vietnamese answer is not a failure.

### Near Pronunciation

If confidence is sufficient, record a brave attempt and identify at most one
approved sound focus. Avoid correcting multiple phonetic details in one turn.
If confidence is insufficient, treat it as uncertain rather than incorrect.

### Unrelated Answer

Check whether it begins a meaningful story or question. If so, open a contextual
branch. Otherwise acknowledge briefly and use a visual or semantic bridge.

### Silence

Silence may mean thinking, shyness, distraction, technical failure, or refusal.
The first response is time, not another instruction. Subsequent supports become
shorter and more playful. Silence cannot be recorded as inability.

### Refusal or Fatigue

Offer a choice: change activity, take a short pause, or finish. A child who
chooses to stop receives a warm close and `REVIEW_NEEDED`, not a failure result.

## Session-Level Word Selection

The session begins with one primary and one optional secondary target. The
secondary target is activated only when:

- the primary word has at least independent-recall evidence or the child is
  clearly ready for a change;
- enough time remains for meaning and one speaking opportunity;
- starting it will not remove the primary word's delayed-recall check.

If those conditions are false, the session teaches one word only.

## Review Scheduling

| End state | Suggested next exposure |
| --- | --- |
| `MASTERED_TODAY` with low support | 2-3 days |
| `MASTERED_TODAY` with high support | Next day |
| `TRANSFERRED` but delayed recall missed | Next day |
| `SUPPORTED_SPEECH` only | Later the same day or next session |
| `UNDERSTOOD` without speech | Next session with a different playful speaking activity |
| `EXPOSED` only | Next session, beginning from meaning rather than testing |
| Refusal/distress/technical uncertainty | No ability inference; retry only in a comfortable future session |

These intervals are initial product defaults, not clinical claims.

## Truthful Feedback Examples

Preferred:

- "Con đã tìm đúng bạn mèo rồi."
- "Con vừa tự nhớ ra `cat` đó!"
- "Robot nghe phần đầu rất rõ. Mình nói chậm cùng nhau nhé."
- "Con chưa muốn nói cũng được. Mình chơi bằng hình trước nhé."

Avoid:

- "Perfect!" when the evidence is uncertain.
- "Wrong" or "Not correct."
- "Try harder."
- "You knew this yesterday."
- "Say it three times" as an automatic punishment for a miss.
