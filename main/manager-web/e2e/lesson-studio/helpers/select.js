// Course/lesson taxonomy fields (locale, age band, difficulty) are Element-UI
// `el-select`s, not plain inputs: their inner <input> carries readonly="readonly",
// so `fill()` times out with "element is not editable". Drive them the way a user
// does — open the dropdown, then click the option.
async function selectOption(page, trigger, value) {
  await trigger.click();
  // The dropdown is teleported to <body>, so scope the option lookup to the page
  // and take the visible one (Element-UI keeps closed dropdowns in the DOM).
  const option = page.locator('.el-select-dropdown__item')
    .filter({ hasText: new RegExp(`^\\s*${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`) })
    .locator('visible=true')
    .first();
  await option.click();
}

/** Selects `value` in the el-select carrying `data-testid="<testId>"`. */
async function selectByTestId(page, scope, testId, value) {
  await selectOption(page, scope.getByTestId(testId), value);
}

module.exports = { selectByTestId, selectOption };
