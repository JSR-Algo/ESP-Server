# Course admin local completion

The course admin now blocks incomplete media previews and rejects obsolete
request callbacks when an author changes learners, courses or filters.

- TVideo preview requires current loaded sources and supported alpha, surfaces
  missing/loading/error states, and no longer draws substitute media.
- CourseInsights keeps save targets aligned with the selected learner and
  preserves newer form edits. Superseded reads cannot replace current results.
- CourseLessons handles reused course routes and fences old list/mutation
  callbacks. Assignment dialogs retain their existing session guards.
- DeviceManagement learner search rejects superseded callbacks.

Run `npm run test:lesson-studio` and `npm run build` from `main/manager-web`.
The aggregate includes 85 focused regression cases, controlled-callback mounted
Chromium/WebKit interactions, actual HTTP media failure/loading checks, route
reuse, and the existing repository checks. Live publication E2E is separate.

Three native E2E journeys also pass on Chromium/WebKit desktop/mobile (12/12,
zero skips) against an isolated actual manager/API/database stack: draft
authoring and persistence with precise missing-media rejection, learner saves
across selection changes with API readback/full-profile restoration, and
Insights/lifecycle history. The authoring test waits for automatic preview
before Validate. Its source fixture is a retained real W1 v108 legacy
publication, not canonical original-source replacement acceptance.

Workspace evidence: `task-artifacts/admin-completion-20260930/report.md`, with
RED logs, exact command receipts, source hashes and independent reviews.

Canonical original-source timeline/publication integration and full real
publication/rollback acceptance remain blocked on their producer/consumer
dependencies. WebKit unsupported-alpha rejection is not successful original
alpha rendering. No deployment, physical-device or full-catalog content
approval is established by this change.

## Detail audit continuation

Course list responses and course/lesson modal callbacks now respect request and
dialog sessions. Draft lookup adds Resume draft beside the authoritative
published row, using the existing all-version API and explicit load failure
feedback. The lookup supports prototype-like lesson keys. Lesson rename ignores
callbacks from replaced lessons. Explicitly empty helper/transfer hints persist
through PATCH. Lesson actions and the metadata dialog fit the mobile viewport;
the CRUD browser journey checks dialog bounds before saving.

Removed the Courses backend/proxy hint, the archived filter unsupported by the
authoritative endpoint, and unreachable V5 ternaries inside the legacy selector.
Native E2E coverage now includes course edit/clone/delete, lesson metadata/delete,
resuming an existing draft, hint clearing/reload and delayed course-save response
delivery against the real owned API/database stack. Evidence is recorded under
workspace `task-artifacts/admin-detail-20260930/`.
