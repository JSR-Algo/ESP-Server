# Course Mode Candidate Freeze

Date: 2026-08-29

Status: software-only candidate-source freeze. This document does not authorize
deployment, database mutation, lesson publication, device assignment, OTA, or
flash.

## Backend integration identity

- Target branch before merge: `8672e1b527515259d75228ab485694146e8fe1e0`
  (`docs(course-mode): enforce one active implementation`).
- Reviewed source tip: `0efd1bed84a3ef160bab2aa5fb4357bf521fde7e`
  (`fix(course-mode): support Docker API 1.48 barn verification`).
- Merge commit: `0783ddba5474c418a2830a761eda78c7b57cacdf`.
- Merge parents, in order: target `8672e1b527515259d75228ab485694146e8fe1e0`
  and source `0efd1bed84a3ef160bab2aa5fb4357bf521fde7e`.
- Curriculum source SHA-256:
  `d156ab52f3ee094b2e6f3a69a8b0eb56b501527db7c70279a3308851251acbd3`.

The integration contains one canonical copy of each required source:

- `src/lessons/course-mode/curriculum-course-mode.ts`
- `src/lessons/course-mode/curriculum-pedagogy.ts`
- `src/database/migrations/126_course_mode_curriculum.sql`
- `scripts/verify-course-mode-curriculum.mjs`

No `v3`, `next`, `new`, or second renderer-v5 implementation was introduced by
the merge. No backend follow-up commit was required because the integration
checks reproduced no merge defect.

## Commands and evidence

All commands ran in the isolated Task 1 worktrees. No command contacted or
mutated production services.

```text
git show -s --format='merge=%H%ntargetParent=%P%nsubject=%s' HEAD
git show -s --format='target=%H %s' HEAD^1
git show -s --format='source=%H %s' HEAD^2
shasum -a 256 src/lessons/course-mode/curriculum-course-mode.ts
find src -type f (canonical source names)
find scripts -type f -name verify-course-mode-curriculum.mjs
npm run lint
npm run typecheck
npx vitest run src/lessons/course-mode tests/verify-course-mode-curriculum.spec.ts
npm run build
node scripts/verify-course-mode-curriculum.mjs
```

Observed backend evidence:

- lint: exit 0;
- typecheck: exit 0;
- focused Vitest: 14 files and 223 tests passed;
- build: exit 0;
- verifier: `status=pass`, course `english-6month-4-6`, 26 lessons,
  256 activities, and 6 pedagogies.

ESP validation uses only an explicit `--backend-root` or
`COURSE_MODE_BACKEND_ROOT`. Candidate-bound runs additionally require the exact
backend SHA. Missing or drifting authority returns stable
`BACKEND_ROOT_REQUIRED` or `BACKEND_IDENTITY_MISMATCH` JSON instead of searching
sibling repositories or worktrees.

## ESP TDD chronology and evidence

The initial RED run failed because of the missing manifest module and explicit-root resolver:

```text
COURSE_MODE_BACKEND_ROOT=/Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/prod-readiness-task1-backend python3 -m pytest -q tests/test_course_mode_candidate_manifest.py tests/test_course_mode_curriculum_e2e.py
```

The next RED cycles rejected missing checksum/SHA binding and dirty-root
enforcement. The minimal implementation then reached GREEN, after which the
same focused command reported `47 passed` with stable failure-envelope tests
still active.

The committed simulator was run with explicit backend authority:

```text
python3 scripts/course_mode_26week_simulation.py --backend-root /Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/prod-readiness-task1-backend
```

Observed simulator/verifier evidence was 26 lessons, 256 activities, 6 pedagogies, and 11 response classes, bound to backend SHA
`0783ddba5474c418a2830a761eda78c7b57cacdf`.

Final GREEN: focused ESP tests passed, the explicit-root simulator returned
`status=pass`, and both task worktrees were clean.

## Limitations

- This freeze validates source and software behavior only; it does not prove a
  container image, firmware binary, database materialization, physical TFT,
  audio, motion, power, thermal, soak, rollback, or child-learning outcome.
- The candidate schema permits exact hash-bound dirty exceptions, but a later
  gate must create and sign the final manifest after all repository SHAs,
  images, firmware, database, tools, and evidence paths are frozen.
- No key was provisioned and no private signing material was created or read.
- No production database, Admin data, deployment, robot, or firmware was
  changed during Task 1.
