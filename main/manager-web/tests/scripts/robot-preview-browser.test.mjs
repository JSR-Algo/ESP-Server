import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, readdir, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { acquirePinnedRobotPreviewChromium } from '../../scripts/robot-preview-browser.mjs';

const CONTENT = 'pinned chromium fixture\n';
const SHA = createHash('sha256').update(CONTENT).digest('hex');

async function fixture({ platform = 'darwin', arch = 'arm64' } = {}) {
  const base = await realpath(await mkdtemp(join(tmpdir(), 'robot-preview-browser-test-')));
  const layouts = {
    'darwin-arm64': ['chrome-headless-shell-mac-arm64', 'chrome-headless-shell'],
    'linux-arm64': ['chrome-linux', 'headless_shell']
  };
  const [bundle, executable] = layouts[`${platform}-${arch}`];
  const root = join(base, `chromium_headless_shell-1223/${bundle}`);
  await mkdir(root, { recursive: true, mode: 0o700 });
  await writeFile(join(root, executable), CONTENT, { mode: 0o700 });
  await writeFile(join(root, 'icudtl.dat'), 'icu\n', { mode: 0o600 });
  const metadata = join(base, 'browsers.json');
  await writeFile(metadata, JSON.stringify({ browsers: [{ name: 'chromium-headless-shell', revision: '1223' }] }));
  const treeHash = createHash('sha256');
  const field = (value) => { const bytes = Buffer.from(String(value)); const length = Buffer.alloc(8); length.writeBigUInt64BE(BigInt(bytes.length)); treeHash.update(length).update(bytes); };
  for (const [name, content, mode] of [[executable, CONTENT, 448], ['icudtl.dat', 'icu\n', 384]]) {
    field('regular'); field(name); field(mode); field(Buffer.byteLength(content)); treeHash.update(content);
  }
  return {
    base, root, executable, metadata, platform, arch,
    environment: {
      TBOT_ROBOT_PREVIEW_BROWSER_ROOT: root,
      TBOT_ROBOT_PREVIEW_BROWSER_EXECUTABLE: executable,
      TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
      TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256: treeHash.digest('hex'),
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_ENTRY_COUNT: '2',
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_TOTAL_BYTES: String(Buffer.byteLength(CONTENT) + 4)
    }
  };
}

test('stages candidate-bound bundle and cleans the private lease', async () => {
  const value = await fixture();
  try {
    const lease = await acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base });
    assert.equal(await readFile(lease.executablePath, 'utf8'), CONTENT);
    assert.equal((await readdir(join(lease.executablePath, '..'))).includes('icudtl.dat'), true);
    await lease.cleanup();
    assert.deepEqual((await readdir(value.base)).sort(), ['browsers.json', 'chromium_headless_shell-1223']);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('atomic source replacement after staging cannot change executable lease', async () => {
  const value = await fixture();
  const original = join(value.root, value.executable);
  try {
    const lease = await acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      afterStage: async () => {
        await rename(original, `${original}.verified`);
        await writeFile(original, 'malicious replacement\n', { mode: 0o700 });
      }
    });
    assert.equal(await readFile(lease.executablePath, 'utf8'), CONTENT);
    assert.equal(createHash('sha256').update(await readFile(lease.executablePath)).digest('hex'), SHA);
    await lease.cleanup();
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('identity failure removes partial staged bundle', async () => {
  const value = await fixture();
  value.environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256 = '0'.repeat(64);
  try {
    await assert.rejects(
      acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base }),
      /identity does not match/
    );
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('supports Playwright 1.60 linux-arm64 bundle layout', async () => {
  const value = await fixture({ platform: 'linux', arch: 'arm64' });
  try {
    const lease = await acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base });
    assert.equal(lease.executablePath.endsWith('/headless_shell'), true);
    await lease.cleanup();
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});
