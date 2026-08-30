import assert from 'node:assert/strict';
import { userInfo } from 'node:os';
import test from 'node:test';

import { findPinnedRobotPreviewChromium } from '../../scripts/robot-preview-browser.mjs';

test('finds pinned Chromium from the OS account home when HOME is sanitized', () => {
  const accountHome = userInfo().homedir;
  const pinned = `${accountHome}/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
  const checked = [];
  const previousHome = process.env.HOME;
  let selected;
  try {
    process.env.HOME = '/nonexistent';
    selected = findPinnedRobotPreviewChromium({
      exists: (candidate) => {
        checked.push(candidate);
        return candidate === pinned;
      }
    });
  } finally {
    process.env.HOME = previousHome;
  }

  assert.equal(selected, pinned);
  assert.deepEqual(checked, [pinned]);
});

test('fails closed instead of falling back when pinned Chromium is absent', () => {
  assert.throws(
    () => findPinnedRobotPreviewChromium({
      accountHome: '/Users/tester',
      exists: () => false
    }),
    /Pinned Playwright Chromium 1223 is required/
  );
});
