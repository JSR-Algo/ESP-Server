# Task 07 Master Prompt: Production Canary and Child Pilot

```text
You are executing Task 07 only after Task 06 has a signed GO verdict and the
user has explicitly authorized production deployment, assignment, and a
supervised child pilot.

Outcome
Roll Course Mode V2 out through a reversible internal canary, then collect
educator-supervised usability and learning-quality evidence without overclaiming
results or exposing child data.

Preconditions
- Exact release-candidate SHAs/checksums from Task 06 are approved.
- Backend, ESP, and firmware changes have passed their deployment procedures.
- COURSE_MODE_V2_PUBLISH_ENABLED and LESSON_COURSE_MODE_V2_ENABLED remain off
  until health checks pass.
- Pilot lesson is versioned, immutable, reviewed, and not assigned broadly.
- Consent, supervision, privacy, incident, and stop procedures are approved by
  the responsible adults/organization.

Rollout sequence
1. Deploy exact backend/worker SHA with publish gate off; run health, migration,
   V1 smoke, and observability checks.
2. Deploy exact ESP SHA with runtime gate off; verify V1 and voice smoke tests.
3. OTA exact firmware only to the approved internal canary robot; verify full V2
   capability, rest pose, audio, renderer, cache, and rollback image.
4. Publish the immutable pilot lesson but keep it unassigned; validate served
   manifest/checksum/derivatives from production read paths.
5. Enable V2 admission only for the canary scope and assign only the approved
   internal test account/device. Never enable globally.
6. Run adult-operated production smoke journeys, then a supervised educator
   session. Inspect telemetry and physical behavior before any child session.
7. Run the explicitly consented child pilot with an adult able to stop the robot
   immediately. Do not use the child to debug known software failures.
8. Disable new admissions after the observation window, preserve approved
   evidence, and decide expand/iterate/rollback.

Canary metrics and stop conditions
- Track session start/completion, independent/transfer/delayed evidence,
  review-needed rate, branch return rate, ASR uncertainty, motion degradation,
  ACK latency, reconnect, crash, cache error, stop-to-rest latency, and adult
  stop requests.
- Immediately stop/rollback for unsafe motion, distress, shaming feedback,
  privacy leakage, false mastery, repeated stuck prompts, thermal/power issue,
  capability mismatch, or unexplained V1 regression.
- Metrics are evidence about system behavior, not proof of educational efficacy.

Pilot observation
- Record structured, privacy-minimized observations: engagement, whether the
  child felt heard, naturalness of redirection, willingness to continue,
  independent naming, transfer, delayed recall, fatigue, confusion, and adult
  intervention.
- Do not store raw audio/transcripts unless a separately approved consent and
  retention protocol explicitly requires it. Prefer live observation and coded
  events.
- Educator reviews every questionable mastery claim against the interaction
  sequence, not only the final event.

Completion criteria
- Production canary completes without release stop condition.
- Rollback is still executable and is tested or invoked as required.
- Evidence bundle contains exact identities, timestamps, redacted logs,
  structured observations, incidents, and follow-up actions.
- Final status is one of: rollback, iterate with gates off, continue internal
  canary, or propose a separately approved limited expansion.
- Never declare broad production readiness or learning efficacy solely from one
  successful child session.

Working method
- Production mutations require explicit user authorization at the point of use.
- Use existing APIs/runbooks; no direct database edits unless the approved
  rollback procedure explicitly requires them.
- Report each state-changing action and verify its observed result before the
  next action. Stop on identity drift or unexpected concurrent deployment.
```

