# Persisted v5 phase-selection regression input

`persisted-multi-activity-v5.json` is the exact `data.manifest` object from the retained real API readback:

`/Users/manhhodinh/Documents/TBOT/task-artifacts/course-production-ready-2026-09-10/T11/runs/run-01-20260913-preview/source/inherited-manifest.json`

Source SHA256: `4e475e6f369b28621616adfda89853ff09809cb545f79cb6125d644c57b5e157`.
Extracted JSON SHA256: `39207769f35063bfd6753317d6254d10e150559faed95bb7e46d4895a7ec31f3`.

All fields are retained without substitution. There are 10 activities and 19 persisted phase records, expanding to 43 activity/phase memberships because some records are shared. The first activity owns flyIn/walk; exit belongs to the last activity; repeated phase names are scoped by activityIds. This is a historical API snapshot, not a new release contract or live-service fixture.

The mounted regression uses the actual LessonStepNavigator and RobotLessonPreview components. It intentionally aborts external media requests, retaining the original manifest URLs and declared identities. It verifies actionable selection, activity/phase ownership and rendered source attributes only. It does not qualify decoded pixels, actual HTTP media, persistence, publication, assignment or rollback.
