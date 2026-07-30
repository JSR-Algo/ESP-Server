# Lesson Editor Responsive Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Lesson Editor inside the browser viewport while preserving local scrolling for asset carousels and the advanced steps table.

**Architecture:** Fix intrinsic sizing at each grid and flex boundary instead of hiding overflow globally. Add a mounted-browser regression that measures the fully rendered document at representative viewport widths, then apply responsive containment to the editor, navigation, asset picker, and shared header.

**Tech Stack:** Vue 2.6, Element UI 2.15, scoped SCSS/CSS, Node.js contract scripts, Chrome DevTools Protocol browser harness.

---

## File Map

- Modify `main/manager-web/scripts/check-lesson-builder-browser.mjs`: measure page-level overflow at 1440, 1024, 768, and 390 px after the mounted Lesson Editor is ready.
- Modify `main/manager-web/src/views/LessonEditor.vue`: add the local advanced-table scroll boundary and constrain the studio grid, workbench, preview surfaces, header actions, and long text.
- Modify `main/manager-web/src/components/lesson/LessonStepNavigator.vue`: make the desktop sidebar intrinsically safe and switch it to a horizontal rail below 760 px.
- Modify `main/manager-web/src/components/lesson/SharedAssetPicker.vue`: prevent asset tiles from sizing ancestor grids and make the filter heading responsive.
- Modify `main/manager-web/src/components/HeaderBar.vue`: move the existing compact-header breakpoint to 900 px so the header's 900 px minimum cannot overflow a 768 px viewport.

### Task 1: Add the failing page-overflow regression

**Files:**
- Modify: `main/manager-web/scripts/check-lesson-builder-browser.mjs:29-37`

- [ ] **Step 1: Add a reusable viewport audit after the mounted editor becomes ready**

Insert this block immediately after the existing `editorReady` assertion and before the authoring interaction test:

```js
  const auditLayoutAt = async (width) => {
    await cdp('Emulation.setDeviceMetricsOverride', {
      width,
      height: 900,
      deviceScaleFactor: 1,
      mobile: width <= 768,
    });
    await evaluate('new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    return evaluate(`(() => {
      const root = document.documentElement;
      const carousel = document.querySelector('.asset-picker__grid');
      const tableScroller = document.querySelector('.advanced-steps-scroll');
      const stepNav = document.querySelector('.step-nav');
      return {
        width: innerWidth,
        clientWidth: root.clientWidth,
        scrollWidth: root.scrollWidth,
        carouselOverflowX: carousel ? getComputedStyle(carousel).overflowX : '',
        tableOverflowX: tableScroller ? getComputedStyle(tableScroller).overflowX : '',
        stepNavOverflowX: stepNav ? getComputedStyle(stepNav).overflowX : '',
      };
    })()`);
  };
  const viewportWidths = [1440, 1024, 768, 390];
  const layoutAudits = [];
  for (const width of viewportWidths) {
    const audit = await auditLayoutAt(width);
    layoutAudits.push(audit);
    assert.ok(
      audit.scrollWidth <= audit.clientWidth + 1,
      `Lesson Editor overflows ${width}px viewport: ${JSON.stringify(audit)}`,
    );
    assert.equal(audit.carouselOverflowX, 'auto', 'asset carousel must own its horizontal overflow');
    assert.equal(audit.tableOverflowX, 'auto', 'advanced steps table must own its horizontal overflow');
    if (width <= 760) {
      assert.equal(audit.stepNavOverflowX, 'auto', 'narrow step navigation must be a local horizontal rail');
    }
  }
  await cdp('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
```

- [ ] **Step 2: Audit published plus loading, empty, and error states at mobile width**

After the existing `result` assertions and before `__MOUNT_DISABLED_LESSON_EDITOR__`, add:

```js
  await evaluate(`(async () => {
    const editor = window.__LESSON_BUILDER_TEST__.editor;
    editor.lesson.status = 'published';
    editor.$set(editor.cinematicLibraryLoading, 'backgroundScene', true);
    editor.$set(editor.cinematicLibraries, 'teachingObject', []);
    editor.$set(editor.cinematicLibraryErrors, 'robotOverlay', 'robot library unavailable');
    await editor.$nextTick();
  })()`);
  const conditionalStateAudit = await auditLayoutAt(390);
  assert.ok(
    conditionalStateAudit.scrollWidth <= conditionalStateAudit.clientWidth + 1,
    `Published loading/error/empty state overflows mobile viewport: ${JSON.stringify(conditionalStateAudit)}`,
  );
  await evaluate(`(async () => {
    const test = window.__LESSON_BUILDER_TEST__;
    const editor = test.editor;
    editor.lesson.status = 'draft';
    editor.$set(editor.cinematicLibraryLoading, 'backgroundScene', false);
    editor.$set(editor.cinematicLibraries, 'teachingObject', test.sharedAssets.filter((asset) => asset.category === 'teachingObject'));
    editor.$set(editor.cinematicLibraryErrors, 'robotOverlay', '');
    await editor.$nextTick();
  })()`);
```

- [ ] **Step 3: Run the mounted-browser test and verify the regression fails**

Run:

```bash
cd main/manager-web
npm run test:lesson-builder-browser
```

Expected: FAIL at the first viewport audit because `document.documentElement.scrollWidth` exceeds `clientWidth`, or because `.advanced-steps-scroll` does not exist yet.

- [ ] **Step 4: Commit the failing regression**

```bash
git add main/manager-web/scripts/check-lesson-builder-browser.mjs
git commit -m "test(admin): detect lesson editor viewport overflow"
```

### Task 2: Contain the Lesson Editor workspace and table

**Files:**
- Modify: `main/manager-web/src/views/LessonEditor.vue:244-292`
- Modify: `main/manager-web/src/views/LessonEditor.vue:2732-2804`
- Test: `main/manager-web/scripts/check-lesson-builder-browser.mjs`

- [ ] **Step 1: Give the advanced steps table a local scroll boundary**

Insert the opening wrapper immediately before the existing `<el-table>`:

```vue
<div class="advanced-steps-scroll">
  <el-table :data="steps" stripe style="width: 100%">
```

Insert the closing wrapper immediately after the existing `</el-table>`:

```vue
  </el-table>
</div>
```

The existing columns and empty slot remain unchanged. The `add-row` and published-only paragraph remain outside the new wrapper.

- [ ] **Step 2: Replace the editor layout rules with shrinkable tracks and bounded children**

Update the scoped styles with these rules, merging them into the existing declarations:

```scss
.operation-bar { min-width: 0; }
.left-title { flex: 1 1 420px; min-width: 0; }
.page-title { min-width: 0; overflow-wrap: anywhere; }
.right-operations { flex: 0 1 auto; flex-wrap: wrap; justify-content: flex-end; min-width: 0; }
.main-wrapper { box-sizing: border-box; max-width: 100%; min-width: 0; }
.content-area { max-width: 100%; min-width: 0; }
.advanced-steps-scroll { max-width: 100%; min-width: 0; overflow-x: auto; }
.advanced-steps-scroll .el-table { min-width: 870px; }
.kv { min-width: 0; }
.kv > :last-child { min-width: 0; overflow-wrap: anywhere; }

.lesson-studio {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
}
.lesson-studio__canvas {
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1fr);
  max-width: 100%;
  min-width: 0;
}
.lesson-studio__canvas > *,
.lesson-studio__toolbar,
.lesson-visual-pair,
.cinematic-pickers,
.lesson-studio__workbench,
.preview-stack,
.preview-surface {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
}
.cinematic-pickers > *,
.lesson-studio__workbench > * { min-width: 0; }
.lesson-studio__toolbar { gap: 12px; }
.lesson-studio__toolbar > div { min-width: 0; }
.lesson-studio__toolbar h3 { overflow-wrap: anywhere; }
.lesson-studio__workbench {
  grid-template-columns: minmax(330px, 1fr) minmax(360px, 1fr);
}
.cinematic-frame,
.cinematic-frame iframe { max-width: 100%; min-width: 0; }
```

- [ ] **Step 3: Strengthen the narrow breakpoints**

Replace the existing media rules at the end of `LessonEditor.vue` with:

```scss
@media (max-width: 1100px) {
  .lesson-studio__workbench { grid-template-columns: minmax(0, 1fr); }
}

@media (max-width: 760px) {
  .operation-bar { align-items: flex-start; padding: 12px 14px 0; }
  .left-title { align-items: flex-start; flex-basis: 100%; }
  .right-operations { flex-basis: 100%; justify-content: flex-start; }
  .main-wrapper { padding: 12px 14px; }
  .canonical-demo,
  .lesson-studio { grid-template-columns: minmax(0, 1fr); }
  .lesson-studio { border-radius: 18px; padding: 10px; }
  .lesson-studio__toolbar,
  .lesson-visual-pair__heading { align-items: flex-start; flex-direction: column; gap: 10px; }
  .grid-2 { grid-template-columns: minmax(0, 1fr); }
  .preview-surface { padding: 10px; }
  .advanced-steps-scroll .el-table { min-width: 760px; }
}
```

- [ ] **Step 4: Run the mounted-browser regression**

Run:

```bash
cd main/manager-web
npm run test:lesson-builder-browser
```

Expected: the 1440 and 1024 audits pass. The test may still fail at 768 or 390 until the step navigator, picker, and shared header are updated in Task 3.

- [ ] **Step 5: Commit the editor containment**

```bash
git add main/manager-web/src/views/LessonEditor.vue
git commit -m "fix(admin): contain lesson editor workspace"
```

### Task 3: Make navigation, asset picking, and the shared header responsive

**Files:**
- Modify: `main/manager-web/src/components/lesson/LessonStepNavigator.vue:2-16`
- Modify: `main/manager-web/src/components/lesson/LessonStepNavigator.vue:30-39`
- Modify: `main/manager-web/src/components/lesson/SharedAssetPicker.vue:45-48`
- Modify: `main/manager-web/src/components/HeaderBar.vue:628-938`
- Test: `main/manager-web/scripts/check-lesson-builder-browser.mjs`

- [ ] **Step 1: Add an accessible name and responsive rail styles to the step navigator**

Change the opening element to:

```vue
<aside class="step-nav" aria-label="Lesson steps">
```

Replace the component styles with:

```css
.step-nav { background:#16332f; border-radius:18px; color:#f8f1da; max-width:100%; min-width:0; padding:14px; }
.step-nav__heading { color:#ffd36a; font-size:12px; font-weight:800; letter-spacing:.12em; margin:4px 6px 12px; text-transform:uppercase; }
.step-nav__item, .step-nav__add { border:0; border-radius:12px; box-sizing:border-box; cursor:pointer; display:flex; min-width:0; text-align:left; width:100%; }
.step-nav__item { align-items:center; background:transparent; color:inherit; gap:10px; margin-bottom:6px; padding:9px; }
.step-nav__item > span:last-child { min-width:0; }
.step-nav__item.active { background:#f7c956; color:#15312d; }
.step-nav__number { align-items:center; background:rgba(255,255,255,.14); border-radius:50%; display:flex; flex:0 0 28px; height:28px; justify-content:center; }
.step-nav__item small { display:block; margin-top:3px; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.step-nav__add { background:#fff8df; color:#16332f; font-weight:700; justify-content:center; margin-top:12px; padding:10px; }

@media (max-width:760px) {
  .step-nav { align-items:center; display:flex; gap:8px; overflow-x:auto; overscroll-behavior-inline:contain; padding:10px; scroll-snap-type:x proximity; }
  .step-nav__heading { display:none; }
  .step-nav__item { flex:0 0 min(190px,75vw); margin:0; scroll-snap-align:start; width:auto; }
  .step-nav__add { flex:0 0 auto; margin:0; padding:10px 14px; white-space:nowrap; width:auto; }
}
```

- [ ] **Step 2: Bound the asset picker and stack its heading on narrow screens**

Replace the `SharedAssetPicker.vue` styles with:

```css
.asset-picker { border-top:1px solid #eee3cd; box-sizing:border-box; margin-top:16px; max-width:100%; min-width:0; padding-top:14px; }
.asset-picker__head { align-items:center; display:flex; gap:14px; justify-content:space-between; min-width:0; }
.asset-picker__head > strong { min-width:0; overflow-wrap:anywhere; }
.asset-picker__head .el-input { flex:0 1 190px; min-width:0; width:190px; }
.asset-picker__grid { display:flex; gap:9px; margin-top:10px; max-width:100%; min-width:0; overflow-x:auto; overscroll-behavior-inline:contain; }
.asset-picker__state { border-radius:10px; margin-top:10px; max-width:100%; min-width:0; overflow-wrap:anywhere; padding:16px; }
.asset-picker__loading,.asset-picker__empty { background:#f3f6f4; color:#66736f; }
.asset-picker__error { background:#fff1f0; color:#a63b32; }
.asset-tile { background:#fff; border:2px solid transparent; border-radius:12px; display:grid; flex:0 0 145px; gap:4px; min-width:0; padding:7px; text-align:left; }
.asset-tile.selected { border-color:#e6a62c; }
.asset-tile__select { background:transparent; border:0; cursor:pointer; display:grid; gap:4px; min-width:0; padding:0; text-align:left; width:100%; }
.asset-tile__select:disabled { cursor:not-allowed; opacity:.55; }
.asset-tile__select strong,.asset-tile small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.asset-tile__preview { align-items:center; background:#edf2ef; border-radius:8px; display:flex; height:68px; justify-content:center; overflow:hidden; }
.asset-tile__preview img,.asset-tile__preview video { height:100%; object-fit:cover; width:100%; }
.asset-tile small { color:#7c8582; }
.asset-tile__actions { display:flex; gap:4px; }
.asset-tile__actions button { background:#edf2ef; border:0; border-radius:7px; color:#31524a; cursor:pointer; flex:1; font-size:11px; padding:5px; }
.asset-tile__actions button:disabled { cursor:not-allowed; opacity:.55; }

@media (max-width:560px) {
  .asset-picker__head { align-items:stretch; flex-direction:column; gap:8px; }
  .asset-picker__head .el-input { flex-basis:auto; width:100%; }
}
```

- [ ] **Step 3: Activate the existing compact header before its minimum width can overflow**

In `HeaderBar.vue`, change only the compact-layout media query:

```scss
@media (max-width: 900px) {
  .header {
    min-width: 0;
    height: auto !important;
    padding: 8px 6px;
  }

  .header-container {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0;
  }

  .header-left { flex: 1 1 100%; min-width: 0; }
  .logo-img { width: 34px; height: 34px; }
  .brand-text { font-size: 22px; }
  .header-center { order: 2; position: static; transform: none; flex: 1 1 auto; gap: 8px; max-width: 100%; min-width: 0; overflow-x: auto; padding-bottom: 2px; }
  .header-right { order: 3; flex: 0 1 auto; min-width: 0; margin-left: auto; }
  .equipment-management { height: 28px; min-width: 0; padding: 0 10px; }
  .nav-text { max-width: 72px; }
}
```

Remove the old duplicate `@media (max-width: 720px)` block after moving its rules to 900 px.

- [ ] **Step 4: Run the responsive browser regression**

Run:

```bash
cd main/manager-web
npm run test:lesson-builder-browser
```

Expected: PASS, including all four viewport audits and existing authoring/preview assertions.

- [ ] **Step 5: Commit the responsive components**

```bash
git add main/manager-web/src/components/lesson/LessonStepNavigator.vue \
  main/manager-web/src/components/lesson/SharedAssetPicker.vue \
  main/manager-web/src/components/HeaderBar.vue
git commit -m "fix(admin): make lesson editor controls responsive"
```

### Task 4: Run focused and full Lesson Studio verification

**Files:**
- Verify: `main/manager-web/src/views/LessonEditor.vue`
- Verify: `main/manager-web/src/components/lesson/LessonStepNavigator.vue`
- Verify: `main/manager-web/src/components/lesson/SharedAssetPicker.vue`
- Verify: `main/manager-web/src/components/HeaderBar.vue`
- Verify: `main/manager-web/scripts/check-lesson-builder-browser.mjs`

- [ ] **Step 1: Run the static Lesson Editor contracts**

```bash
cd main/manager-web
npm run test:lesson-editor-ui
```

Expected: PASS with the existing Lesson Editor UI contract success messages.

- [ ] **Step 2: Run the visual-selection and builder logic contracts**

```bash
cd main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-builder-logic
```

Expected: both commands PASS.

- [ ] **Step 3: Run the mounted-browser regression again in isolation**

```bash
cd main/manager-web
npm run test:lesson-builder-browser
```

Expected: PASS with `mounted visual LessonEditor selection, authoring PATCH, readiness, and preview props PASS` and no viewport-overflow assertion.

- [ ] **Step 4: Run the complete Lesson Studio gate**

```bash
cd main/manager-web
npm run test:lesson-studio
```

Expected: every chained contract and browser command exits 0. If the environment lacks Chrome, rerun with `CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`.

- [ ] **Step 5: Build the production bundle**

```bash
cd main/manager-web
npm run build
```

Expected: Vue production build completes successfully. Existing bundle-size warnings are acceptable; compilation errors are not.

- [ ] **Step 6: Inspect the final diff and commit any verification-only adjustment**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only the planned files are modified. If Task 4 required a small test-only correction, commit it with:

```bash
git add main/manager-web/scripts/check-lesson-builder-browser.mjs
git commit -m "test(admin): finalize responsive lesson editor coverage"
```
