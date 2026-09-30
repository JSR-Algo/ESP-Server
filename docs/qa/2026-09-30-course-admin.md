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
