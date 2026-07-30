# Lesson Editor Responsive Overflow Design

**Date:** 2026-07-30  
**Status:** Approved design, pending written-spec review  
**Surface:** `main/manager-web` Lesson Editor

## Problem

The Lesson Editor can expand far beyond the browser viewport. On the production lesson supplied for review, a 1470 px viewport produced a document width of 7367 px. The page therefore opens at an unexpected horizontal position, hides primary content, and makes the authoring and robot-preview workflow difficult to use.

The immediate cause is intrinsic sizing inside the studio layout. `.lesson-studio__canvas` is a grid without an explicit shrinkable track. Wide descendants, especially horizontal asset lists and preview surfaces, contribute their min-content width to the implicit grid track. Several descendants consequently render at roughly 7100 px even though the studio canvas itself has about 1160 px available.

## Goals

- Keep the entire Lesson Editor within the viewport at desktop, tablet, and mobile widths.
- Preserve a balanced workspace for lesson authoring and robot QA.
- Limit horizontal scrolling to components that intentionally need it, such as asset carousels and the advanced steps table.
- Preserve all existing APIs, permissions, lesson lifecycle behavior, and validation/publish logic.
- Make loading, empty, error, draft, and published states obey the same layout constraints.

## Non-Goals

- Redesigning backend APIs or lesson data contracts.
- Changing draft or published mutation rules.
- Replacing Element UI or migrating Vue 2.
- Adding new authoring or robot-simulation features.
- Redesigning unrelated admin pages.

## Layout Design

### Studio containment

The two-column studio remains the desktop structure: a step navigator beside a shrinkable canvas. Every grid boundary that contains flexible content must use an explicitly shrinkable track such as `minmax(0, 1fr)`. Direct grid and flex children must use `min-width: 0` where intrinsic content could otherwise widen the page.

The page root, operation bar, main wrapper, lesson studio, canvas, visual pair, workbench, and preview stack must remain at or below their containing width. Page-level horizontal overflow is not an accepted fallback.

### Desktop and tablet

- Keep the step navigator as the left column when sufficient width is available.
- Keep the authoring form and preview stack side by side while both columns remain usable.
- Stack the form and preview vertically at the existing medium-width breakpoint when the two-column workbench no longer fits.
- Allow the header and action groups to wrap without hiding actions or widening the page.

### Mobile and narrow windows

- Convert the vertical step navigator into a horizontal, locally scrollable rail.
- Stack all authoring and preview surfaces in one column.
- Allow the lesson title to wrap or truncate safely while keeping its status visible.
- Keep action buttons reachable through wrapping rather than page-level horizontal scrolling.
- Reduce outer padding so the working area remains useful on a 390 px viewport.

## Component Behavior

### `LessonEditor.vue`

- Define shrinkable columns for `.lesson-studio__canvas` and `.lesson-studio__workbench`.
- Constrain toolbar, visual-pair, workbench, simulation, engagement, readiness, and preview sections to `max-width: 100%` with safe intrinsic sizing.
- Wrap the advanced steps table in a local horizontal scroll container if Element UI's table wrapper does not already provide a reliable boundary.
- Preserve existing rendering conditions, events, and business logic.

### `LessonStepNavigator.vue`

- Retain the vertical navigation on wide layouts.
- Switch to a horizontal rail below the narrow breakpoint.
- Keep each step target large enough to select and prevent prompt text from widening the rail.
- Confine overflow to the navigator itself.

### `SharedAssetPicker.vue`

- Keep the asset list as a horizontal carousel with local scrolling.
- Ensure the carousel, picker root, heading, filter input, and tiles cannot establish the width of an ancestor grid track.
- Stack or wrap the heading and filter input when the available width is small.
- Preserve tile size and selection behavior.

### Preview components

- Constrain cinematic iframe and robot preview surfaces to the available column width.
- Preserve their intended aspect ratios.
- Scale the 480 x 320 robot stage within its shell rather than allowing the stage to widen the document.
- Keep preview-specific controls wrapped or locally scrollable where necessary.

### Header and action bar

- Allow the lesson title group and action group to wrap independently.
- Prevent long lesson keys, checksums, and etags from increasing page width.
- Keep `Validate`, `Preview`, and `Publish` visible and operable at supported widths.

## Data Flow and State

No data-flow changes are required. Lesson loading, asset-library loading, visual selection, validation, preview generation, simulation, and publish flows continue to use their current state and events. The change is restricted to presentation and responsive behavior.

All conditional states must share the same containment rules:

- initial loading and delayed lesson hydration;
- populated, empty, and failed asset libraries;
- draft and published lessons;
- generated and missing robot previews;
- validation not run, passed, or failed;
- long prompts, asset keys, checksums, and error messages.

## Accessibility and Interaction

- Do not replace semantic buttons, fields, tables, headings, or regions.
- Preserve visible keyboard focus inside locally scrollable rails and carousels.
- Use responsive wrapping without changing keyboard navigation order.
- Do not reduce touch targets merely to fit more content.
- Respect existing reduced-motion behavior in preview components.

## Validation

The implementation is complete when all of the following are true:

- At viewport widths 1440, 1024, 768, and 390 px, `document.documentElement.scrollWidth` is no greater than `document.documentElement.clientWidth` after lesson data and previews load.
- Asset carousels and the advanced steps table can scroll inside their own boundaries when their content is wider than the available space.
- Lesson title, status, and primary actions remain visible or safely wrapped.
- Cinematic and robot previews preserve their aspect ratios and remain within their containers.
- The step navigator changes from a vertical sidebar to a horizontal rail at the narrow breakpoint.
- The authoring form and preview stack vertically when the two-column workbench cannot fit.
- Published and draft lesson states both pass the overflow checks.
- Loading, empty, and error states do not introduce page-level overflow.
- Existing Lesson Editor contract tests pass.
- A focused automated regression test detects page-level horizontal overflow at representative viewport widths.

## Expected Files

- `main/manager-web/src/views/LessonEditor.vue`
- `main/manager-web/src/components/lesson/LessonStepNavigator.vue`
- `main/manager-web/src/components/lesson/SharedAssetPicker.vue`
- Preview component styles only if verification shows they still violate containment.
- Lesson Editor UI/browser contract tests covering horizontal overflow.

## Risk Controls

- Prefer targeted CSS containment and responsive rules over template or state changes.
- Avoid global `overflow-x: hidden`, which could conceal unresolved layout defects or clip legitimate controls.
- Verify computed document width after asynchronous lesson and preview content is fully rendered.
- Keep local scrolling explicit so users can distinguish a carousel or table from the page itself.
