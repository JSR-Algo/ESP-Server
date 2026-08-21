# Task 07 Master Prompt: Physical Robot Validation

```text
You are executing Task 07, the physical hardware release gate for Course Mode V2.
Use only an approved internal robot and adult operators. Do not run a child pilot.

Outcome
Prove on real hardware that visuals, audio, microphone, face, head, arms, cache,
recovery, and safe rest behavior satisfy the Course Mode contract under realistic
conditions. Produce physical evidence for independent production review.

Preconditions
- Task 06 has a RUNTIME PASS with pinned candidate identities.
- Firmware/ESP/backend builds match those exact SHAs and checksums.
- Production assignment and global V2 flags remain off.
- The robot has a verified rollback firmware image and an accessible emergency stop.

Required physical lanes
1. Baseline hardware health: battery/power, display, speaker, microphone, SD/cache,
   network, servos, firmware capability payload, and safe rest pose.
2. Visual inspection on the 480x320 TFT for every pilot cue under representative
   indoor lighting: object legibility, robot/object overlap, crop, z-order, focus
   anchors, captions, listening indicator, and reduced-motion fallback.
3. Embodied behavior: CENTER/LEFT/RIGHT direction, face semantics, arm amplitude,
   celebration limits, gesture-to-rest timing, cancellation, one-servo failure,
   and restart/disconnect recovery.
4. Listening integrity: measure motor noise and robot-speaker leakage at the
   microphone; prove motors are settled before assessment opens; test barge-in,
   near/far adult speech, normal household noise, silence, and ambiguous audio.
5. Thermal/power/comfort: repeated choreographies and sessions while recording
   servo temperature, current/power behavior, abnormal sound/vibration, mechanical
   binding, stability, and adult-observed comfort concerns.
6. Recovery: cold boot, warm restart, network loss, backend loss, corrupt/missing
   asset, SD cache cold/warm, power interruption, stop, and rollback image.
7. Adult-operated end-to-end journeys matching the critical Task 06 scenarios.

Safety stop conditions
- Unexpected or forceful motion, collision, binding, overheating, unstable power,
  failure to lower arms/center head, assessment while motors move, distressed or
  shaming output, exposed private data, or inability to stop/rollback immediately.
- On a stop condition: end the test, restore safe pose/power state, preserve
  evidence, mark FAIL, and do not waive the issue.

Acceptance gates
- Every physical lane has timestamped evidence, device/build identity, operator,
  environment, measurements, capture paths, and pass/fail verdict.
- No assessment window overlaps audible/measured servo activity.
- Rest pose is restored within the approved bound on stop/restart/disconnect.
- Visual focus and arm/head direction match authored targets for every cue.
- Temperature, power, noise, and comfort remain within documented hardware limits.
- Rollback is physically rehearsed and the robot returns to known-good V1 operation.
- No production-wide deployment, assignment, or flag enablement occurs.

Deliverables
- Commit a redacted physical HIL report with serial/VPS logs, photos/video frame
  references, measurement tables, exact SHAs/checksums, failures, fixes, and reruns.
- Issue PHYSICAL PASS or PHYSICAL FAIL. PASS authorizes Task 08 review only.
- If required equipment or robot access is unavailable, report PHYSICAL BLOCKED;
  native/simulation evidence cannot substitute for this gate.

Working method
- Use existing HIL/capture/verifier scripts where available and record exact commands.
- Make fixes only in the owning repository, add regression coverage, rebuild the
  exact candidate, rerun affected runtime lanes, then repeat physical validation.
- Do not alter the Farm v9 rollout worktree or use real child data.
```

