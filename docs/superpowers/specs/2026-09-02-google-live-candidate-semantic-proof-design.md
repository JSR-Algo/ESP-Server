# Google Live Candidate Semantic Proof Design

**Status:** Approved for planning

**Date:** 2026-09-02

## Problem

The candidate-soak producer now creates authenticated, bounded lifecycle
evidence, but two stage verdicts remain semantically under-proven:

- the two `quiet` executions are represented by a prompted response and a
  constant `falseInterrupts=0`, rather than two real quiet observations;
- each `bargein` execution records `latestIntentSuccesses=1` after transport and
  lifecycle replacement evidence, without proving that the replacement response
  belongs to the newest user intent.

These gaps can produce a release PASS when silence/AEC handling is broken or
when Google stops the old response but answers the stale intent. The fix must
not persist raw utterances, prompts, audio, HMACs, or reusable digests, and must
not change physical transcript proof or normal Google Live behavior.

## Decision

Add server-issued, privacy-safe semantic evidence for the exact candidate
stages:

- `quiet` has two explicit observation modes: `silence` and `robot_speaking`;
- `bargein` has an authenticated two-slot intent plan: `initial` then `newest`.

The producer derives `falseInterrupts` and `latestIntentSuccesses` only from the
canonical bounded analyzer report. Client constants, absence-only client
observations, and raw transport results cannot satisfy these verdicts.

## Enrollment Extension

The authenticated evidence enrollment control plane gains an optional semantic
plan only for these compatible claims:

```text
proofProfile=candidate-lifecycle, journeyType=bargein
proofProfile=candidate-lifecycle, journeyType=quiet
```

All other journey/profile combinations reject semantic fields.

### Barge-In Intent Plan

The barge-in enrollment contains:

```json
{
  "semanticProof": {
    "version": "google-live-candidate-intent-nfkc-casefold.v1",
    "hmacKeyBase64": "<ephemeral-32-byte-key>",
    "intentPlan": [
      {"slot": 1, "role": "initial", "expectedMac": "<64-lowercase-hex>"},
      {"slot": 2, "role": "newest", "expectedMac": "<64-lowercase-hex>"}
    ]
  }
}
```

The plan uses the existing versioned NFKC/casefold/non-alphanumeric-space
normalization and an ephemeral 32-byte HMAC key delivered through the existing
authenticated request. The key is never returned by the API. The server owns
and zeroizes it after parsing. The two roles and slot order are exact;
duplicate, reordered, missing, mismatched, or extra observations fail closed.

The candidate lifecycle enrollment remains distinct from
`physical-transcript`: it does not require a lesson or final `post_lesson` slot.
The semantic intent proof authorizes only the barge-in semantic verdict; it
never bypasses lifecycle cleanup, trusted log analysis, or transport
correlation.

### Quiet Observation Plan

Each quiet enrollment contains one authenticated safe mode claim:

```json
{"semanticProof": {"version": "google-live-candidate-quiet.v1", "mode": "silence"}}
```

or:

```json
{"semanticProof": {"version": "google-live-candidate-quiet.v1", "mode": "robot_speaking"}}
```

Exactly one execution uses each mode. The mode is immutable registry state and
cannot be supplied or overridden by hello.

## Barge-In Data Flow

1. The producer reads a protected JSON object from stdin. It contains paths to
   two approved local audio fixtures representing distinct intents and the two
   expected utterances. Raw expected text is never supplied in argv. The
   structured command records only a protected-stdin descriptor and stable
   evidence-relative fixture labels.
2. It creates an ephemeral key and posts only the two expected MACs, roles, and
   the key through the authenticated enrollment request.
3. The existing audio helper sends the initial intent, waits for active model
   output, sends `listen/start`, then sends the newest-intent audio.
4. The provider observes Google transcript text in memory before routing. It
   records a boolean HMAC match for the expected role and binds the observation
   to the accepted user-turn generation.
5. The newest slot is valid only while the old response is active and only for
   the generation that creates the interrupt/replacement chain.
6. The server emits canonical safe markers containing only journey ID, slot,
   role, character count, match boolean, and generation/response identifiers.
7. The analyzer requires the exact initial/newest order, successful newest
   match, old-response stop, replacement start, no stale old audio, and exact
   scope/candidate/connection ownership.
8. Only this analyzer result sets `latestIntentSuccesses=1`. A mismatch or stale
   response yields `0` and fails the execution contract.

Raw Task 4 transport remains `SKIPPED/PENDING`; semantic and lifecycle log
correlation together produce the composite barge-in PASS.

The protected input shape is exact:

```json
{
  "bargein": {
    "initialAudioPath": "<local-wav>",
    "initialExpected": "<protected-text>",
    "newestAudioPath": "<local-wav>",
    "newestExpected": "<protected-text>"
  },
  "robotSpeaking": {
    "triggerAudioPath": "<local-wav>"
  }
}
```

Unknown fields, aliased input/output paths, non-regular files, symlinks, unsafe
audio formats, and missing protected input fail before enrollment. The producer
holds expected strings only long enough to normalize and HMAC them, then drops
the references and overwrites the local key buffer.

## Quiet Data Flow

### Silence

The producer opens a bounded `quiet` scope and sends no user prompt or audio for
the configured observation duration. The server/analyzer require:

- the authenticated mode is `silence`;
- a positive bounded UTC window and continuous trusted connection ownership;
- zero scoped user turns, model responses, interrupts, response replacements,
  reconnects, and fallbacks;
- zero unexpected audio output and zero pending cleanup state.

The verdict is not inferred solely from missing client messages; it is derived
from the complete server-issued bounded window and explicit scoped counters.

### Robot Speaking

The producer initiates one existing local, non-secret response fixture through
the normal Google Live path, then observes the response while AEC/server-side
VAD forwarding remains enabled. The analyzer requires:

- the authenticated mode is `robot_speaking`;
- exactly one response start and one matching terminal response end;
- zero user-turn admission, interrupts, replacements, reconnects, fallbacks,
  or stale audio;
- a positive bounded window and verified cleanup.

AEC frames may be forwarded as in production, but they must not open a clean
user turn, change response generation, or cancel the active response.

The analyzer derives `falseInterrupts=0` only when all quiet-mode invariants are
present. Any forbidden scoped event fails the quiet execution.

## Server Markers And Analyzer

Markers are emitted only from registry-bound candidate scopes and use canonical
file-log formatting. New markers contain fixed field sets and are full-line
parsed. Unknown, duplicate, extra, foreign-scope, wrong-role, wrong-mode, or
malformed marker families fail the bounded report.

The analyzer report adds safe stage-specific evidence:

```json
{
  "candidateSemanticEvidence": {
    "status": "PASS",
    "kind": "bargein-intent",
    "latestIntentMatched": true,
    "initialSlotMatched": true,
    "newestSlotMatched": true,
    "orderingValid": true,
    "replacementOwnedByNewestGeneration": true
  }
}
```

or:

```json
{
  "candidateSemanticEvidence": {
    "status": "PASS",
    "kind": "quiet",
    "mode": "silence",
    "falseInterrupts": 0,
    "responseStarts": 0,
    "responseEnds": 0,
    "replacements": 0,
    "fallbacks": 0
  }
}
```

The `robot_speaking` form uses `responseStarts=1` and `responseEnds=1`. Reports
contain only booleans, counts, safe roles/modes, slots, IDs, timestamps, and
character counts.

## Privacy And Key Ownership

- Audio fixtures remain local input files and are never copied into evidence.
- Raw or normalized transcripts, prompts, audio bytes, expected/observed MACs,
  HMAC keys, credentials, cookies, session handles, and raw exceptions never
  enter logs, reports, manifests, status output, or provenance.
- Local key buffers are overwritten after enrollment/finalization/cancellation.
- Registry key ownership, expiry, zeroization, tombstones, and bounded capacity
  follow the existing evidence enrollment rules.
- Recursive value-level privacy scanning remains mandatory before manifest and
  report publication.

## Failure And Cleanup

- Semantic mismatch is sticky and cannot be repaired by later observations in
  the same journey.
- A semantic failure still requires verified provider/client cleanup before a
  terminal failure marker or tombstone is emitted.
- Timeout, cancellation, close-in-progress, pending tasks, invalid lineage, or
  incomplete analyzer proof remains nonterminal/retryable under the existing
  bounded lifecycle policy.
- Cleanup is exactly once unless the provider explicitly proves retry safety.
- Failed or cancelled production leaves no partial `journey-evidence.json`.

## Testing

TDD coverage must include:

- strict semantic enrollment shapes and compatibility rejection;
- HMAC role ordering, duplicate/mismatch/extra observations, zeroization, and
  privacy scans;
- newest intent arriving during active old output and binding the replacement
  generation;
- stale-intent output, unmatched newest intent, stale old audio, wrong response
  ownership, and missing replacement all failing;
- silence mode with injected response, interrupt, reconnect, fallback, or user
  turn failing;
- robot-speaking mode with exactly one response passing and injected AEC false
  interrupt/replacement/fallback failing;
- canonical marker spoof/malformed/foreign-scope rejection;
- producer deriving semantic counters only from bound analyzer evidence;
- exact 33-execution multiplicity and the two distinct quiet modes;
- cancellation, cleanup, manifest atomicity, privacy, replay, physical profile,
  normal conversation, AEC, and barge-in regressions.

All implementation tasks require independent spec review, independent quality
review, and fresh controller verification.

## Non-Goals

- No speech-to-text or semantic classification on the client.
- No client-authored success flags.
- No persisted transcript, prompt, audio, key, or digest.
- No change to model, voice, language, prompts, lesson, fallback, or normal
  client behavior outside authenticated evidence scopes.
- No automatic deployment, firmware flashing, hardware reset, reconnect, or
  robot control.
