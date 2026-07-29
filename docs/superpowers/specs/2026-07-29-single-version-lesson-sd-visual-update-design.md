# Single-Version Lesson SD Visual Update Design

**Date:** 2026-07-29

**Status:** Approved in conversation; awaiting written-spec review

**Intake:** Change request, normal lane with stronger validation

## Goal

Let an administrator change one background and one teaching object for an
entire lesson. The lesson has one editable current version. Saving either
selection automatically updates every lesson step and synchronizes the new
visuals to robot SD storage without a separate publish action.

## Product Contract

- A lesson has one current background and one current teaching object.
- Both selections apply to every existing step in the lesson.
- A newly created step inherits the lesson's current selections.
- Admins select only from existing published visual-library assets.
- Changes are allowed while the lesson is in use; the admin does not create a
  new lesson version and does not publish again.
- A connected robot receives the update automatically. An offline robot
  receives it during its next asset synchronization.
- A robot already rendering a step keeps the current visual until the next
  step boundary. A newly started lesson uses the latest synchronized visuals.

## Admin Experience

The lesson editor shows a single lesson-level visual panel rather than treating
the selectors as per-step controls:

1. `Background` displays the current background and the available scene assets.
2. `Object` displays the current teaching object and the available
   `teachingObject` assets.
3. Selecting an item saves immediately. The UI disables both selectors while
   the pair is being applied so concurrent clicks cannot create a mixed state.
4. The panel displays one of four synchronization states: `Đang đồng bộ`, `Đã
   đồng bộ`, `Chờ đồng bộ`, or `Đồng bộ lỗi`.
5. A failed or pending synchronization exposes `Thử lại`. There is no Publish
   or Create Version action for a visual-only change.

The exact robot preview refreshes only after the backend confirms that the
lesson-level selection was saved. The preview may show the new selection while
the SD status is still pending, but it must label that state clearly.

## Data Model and Compatibility

The first ordered lesson step is the canonical source for the lesson-level
background and object selection. The backend applies the same two visual
references to every step in one transaction. Step creation copies the canonical
pair to the new step.

This design deliberately avoids a new lesson-defaults table and avoids changing
the robot manifest shape. Existing per-step `lesson_visual_refs` remain the
runtime representation, so the firmware continues receiving the same scene
contract.

The admin workflow edits the current lesson row in place and must not invoke the
existing `new-version` flow. Removing historical database columns or deleting
pre-existing lesson-version records is outside this change; the product-facing
workflow behaves as single-version and creates no additional version.

If a lesson has no steps, the lesson-level selectors remain disabled until the
first step exists because there is no canonical visual reference to persist.

## API and Transaction

Add one lesson-level command endpoint conceptually shaped as:

```text
PUT /v1/admin/lessons/:lessonId/visuals
{
  "backgroundAssetVersionId": "uuid",
  "objectAssetVersionId": "uuid"
}
```

The command:

1. Parses and validates both UUIDs.
2. Loads both shared visual versions and verifies they are published,
   `espTft` compatible, and respectively categorized as `scene` and
   `teachingObject`.
3. Locks the current lesson and its steps.
4. Replaces both visual references on every step in one database transaction.
5. Rebuilds the current lesson manifest identity/checksum without incrementing
   `lesson_version`.
6. Commits the visual change and queues the existing SD asset-generation and
   fanout path.
7. Returns the authoritative selections and synchronization state.

Both asset IDs are always submitted together. Even when the admin changes only
one selector, the client includes the other current selection. This prevents a
half-updated background/object pair.

## SD Synchronization

The product behavior is a logical overwrite of the active background and object
on SD. Internally, synchronization may stage verified bytes and atomically
activate the refreshed lesson pack rather than modifying a file while the robot
could be reading it.

- The selected library bytes remain server-authoritative.
- The sync worker verifies size and SHA-256 before activation.
- Both visuals are activated as one lesson pack update.
- The active lesson version number does not change.
- Connected robots are notified through the existing SD fanout mechanism.
- Offline robots remain pending and converge on reconnect.
- Old inactive cache content may be removed later by the existing garbage
  collector; cache cleanup is not exposed as lesson versioning.

This keeps the admin workflow simple while preserving the current corruption
and partial-download protections.

## Failure Handling

- Validation failure writes nothing and reports the invalid selector.
- Database failure rolls back both visual-reference changes.
- Asset generation or device delivery failure does not revert the saved admin
  selection. It records `pending` or `failed`, keeps the last fully activated SD
  pack on the robot, and allows retry.
- A robot never activates only one of the two new visuals.
- Repeated save or retry requests are idempotent for the same lesson and asset
  pair.
- If another admin changes the pair during synchronization, only the newest
  saved pair may become the active target; stale completion must not overwrite
  the newer state.

## Validation

### Backend unit and integration proof

- Accept a valid published `scene` plus `teachingObject` pair.
- Reject swapped categories, unpublished versions, incompatible profiles, and
  invalid identifiers without writes.
- Replace both refs on every existing step in one transaction.
- Copy the canonical pair when creating a new step.
- Keep `lesson_version` unchanged while recomputing the current checksum.
- Roll back the complete pair when any reference update fails.
- Make repeated identical requests idempotent.
- Ignore stale synchronization completion after a newer selection is saved.

### Admin proof

- Render one lesson-level background selector and one object selector.
- Load the current pair independently of the selected step.
- Submit the complete pair after either selector changes.
- Disable conflicting changes while a save is active.
- Refresh preview and render each synchronization state correctly.
- Retry a pending or failed synchronization.

### End-to-end and device proof

- Change both visuals from the admin and observe every step using the same pair.
- Confirm the lesson version is unchanged.
- Confirm the robot SD active pack contains the new verified assets.
- Confirm a connected robot picks up the update automatically.
- Confirm an offline robot receives it after reconnect.
- Inject a failed second asset and prove the robot keeps the previous complete
  pair rather than activating a mixed pair.

## Non-Goals

- Uploading new assets from the lesson editor.
- Per-step background or object customization.
- Creating, publishing, or rolling back lesson versions.
- Changing renderer or firmware scene schemas.
- Removing historical database records as part of this feature.
- Interrupting and redrawing the middle of an actively rendered step.

## Acceptance Criteria

The change is accepted when an admin can select an existing background and
object once at lesson level, every current and future step uses that pair, the
current lesson version number remains unchanged, and the existing SD sync path
automatically and safely activates the new pair on connected and reconnecting
robots.
