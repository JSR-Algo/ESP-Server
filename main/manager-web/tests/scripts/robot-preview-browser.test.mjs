import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { findPinnedRobotPreviewChromium } from '../../scripts/robot-preview-browser.mjs';

async function fixture() {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'robot-preview-browser-')));
  const browser = join(root, 'ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell');
  const metadata = join(root, 'browsers.json');
  await mkdir(join(browser, '..'), { recursive: true });
  await writeFile(browser, 'pinned chromium fixture\n', { mode: 0o755 });
  await writeFile(metadata, JSON.stringify({ browsers: [{ name: 'chromium-headless-shell', revision: '1223' }] }));
  return { root, browser, metadata };
}

test('finds descriptor-bound Chromium when HOME is sanitized', async () => {
  const { root, browser, metadata } = await fixture();
  const previousHome = process.env.HOME;
  try {
    process.env.HOME = '/nonexistent';
    const selected = await findPinnedRobotPreviewChromium({
      environment: {
        TBOT_ROBOT_PREVIEW_BROWSER_PATH: browser,
        TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
        TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
        TBOT_ROBOT_PREVIEW_BROWSER_SHA256: '88ef122bd424e108314073f0949750d963365e49c2bacac8f45e87523f50d1a8',
        TBOT_ROBOT_PREVIEW_BROWSER_BYTES: '24'
      },
      metadataPath: metadata,
      platform: 'darwin',
      arch: 'arm64'
    });
    assert.equal(selected, browser);
  } finally {
    if (previousHome === undefined) delete process.env.HOME;
    else process.env.HOME = previousHome;
    await rm(root, { recursive: true, force: true });
  }
});

test('fails closed on descriptor hash drift', async () => {
  const { root, browser, metadata } = await fixture();
  try {
    await assert.rejects(findPinnedRobotPreviewChromium({
      environment: {
        TBOT_ROBOT_PREVIEW_BROWSER_PATH: browser,
        TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
        TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
        TBOT_ROBOT_PREVIEW_BROWSER_SHA256: '0'.repeat(64),
        TBOT_ROBOT_PREVIEW_BROWSER_BYTES: '24'
      },
      metadataPath: metadata,
      platform: 'darwin',
      arch: 'arm64'
    }), /identity does not match/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('fails closed on a symlink and supports linux-x64 metadata paths', async () => {
  const { root, metadata } = await fixture();
  const target = join(root, 'target');
  const browser = join(root, 'ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell');
  await writeFile(target, 'pinned chromium fixture\n', { mode: 0o755 });
  await mkdir(join(browser, '..'), { recursive: true });
  await import('node:fs/promises').then(({ symlink }) => symlink(target, browser));
  try {
    await assert.rejects(findPinnedRobotPreviewChromium({
      environment: {
        TBOT_ROBOT_PREVIEW_BROWSER_PATH: browser,
        TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
        TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
        TBOT_ROBOT_PREVIEW_BROWSER_SHA256: '88ef122bd424e108314073f0949750d963365e49c2bacac8f45e87523f50d1a8',
        TBOT_ROBOT_PREVIEW_BROWSER_BYTES: '24'
      },
      metadataPath: metadata,
      platform: 'linux',
      arch: 'x64'
    }), /regular non-symlink/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('supports the Playwright 1.60 linux-arm64 headless-shell layout', async () => {
  const { root, metadata } = await fixture();
  const browser = join(root, 'ms-playwright/chromium_headless_shell-1223/chrome-linux/headless_shell');
  await mkdir(join(browser, '..'), { recursive: true });
  await writeFile(browser, 'pinned chromium fixture\n', { mode: 0o755 });
  try {
    const selected = await findPinnedRobotPreviewChromium({
      environment: {
        TBOT_ROBOT_PREVIEW_BROWSER_PATH: browser,
        TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
        TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
        TBOT_ROBOT_PREVIEW_BROWSER_SHA256: '88ef122bd424e108314073f0949750d963365e49c2bacac8f45e87523f50d1a8',
        TBOT_ROBOT_PREVIEW_BROWSER_BYTES: '24'
      },
      metadataPath: metadata,
      platform: 'linux',
      arch: 'arm64'
    });
    assert.equal(selected, browser);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
