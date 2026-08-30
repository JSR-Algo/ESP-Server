import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { chmod, lstat, mkdtemp, mkdir, readFile, readdir, realpath, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, relative, sep } from 'node:path';
import test from 'node:test';

import {
  acquirePinnedRobotPreviewChromium,
  decodeBrowserEntryName,
  startPinnedRobotPreviewChromiumAcquisition,
} from '../../scripts/robot-preview-browser.mjs';

const CONTENT = 'pinned chromium fixture\n';
const SHA = createHash('sha256').update(CONTENT).digest('hex');

async function treeDigest(root) {
  const treeHash = createHash('sha256');
  const state = { entryCount: 0, totalBytes: 0 };
  const field = (value) => { const bytes = Buffer.from(String(value)); const length = Buffer.alloc(8); length.writeBigUInt64BE(BigInt(bytes.length)); treeHash.update(length).update(bytes); };
  async function visit(directory) {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => Buffer.from(left.name).compare(Buffer.from(right.name)));
    for (const entry of entries) {
      const path = join(directory, entry.name);
      const metadata = await lstat(path);
      const relativePath = relative(root, path).split(sep).join('/');
      const mode = metadata.mode & 0o777;
      state.entryCount += 1;
      if (metadata.isDirectory()) {
        field('directory'); field(relativePath); field(mode);
        await visit(path);
      } else {
        const content = await readFile(path);
        state.totalBytes += content.length;
        field('regular'); field(relativePath); field(mode); field(content.length); treeHash.update(content);
      }
    }
  }
  await visit(root);
  return { sha256: treeHash.digest('hex'), ...state };
}

async function fixture({ platform = 'darwin', arch = 'arm64', nestedDirectoryMode = null } = {}) {
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
  if (nestedDirectoryMode !== null) {
    const resources = join(root, 'resources');
    await mkdir(resources, { mode: 0o700 });
    await writeFile(join(resources, 'locale.pak'), 'locale\n', { mode: 0o400 });
    await chmod(resources, nestedDirectoryMode);
  }
  const metadata = join(base, 'browsers.json');
  await writeFile(metadata, JSON.stringify({ browsers: [{ name: 'chromium-headless-shell', revision: '1223' }] }));
  const tree = await treeDigest(root);
  return {
    base, root, executable, metadataPath: metadata, platform, arch,
    environment: {
      TBOT_ROBOT_PREVIEW_BROWSER_ROOT: root,
      TBOT_ROBOT_PREVIEW_BROWSER_EXECUTABLE: executable,
      TBOT_ROBOT_PREVIEW_BROWSER_ENGINE: 'chromium-headless-shell',
      TBOT_ROBOT_PREVIEW_BROWSER_REVISION: '1223',
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256: tree.sha256,
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_ENTRY_COUNT: String(tree.entryCount),
      TBOT_ROBOT_PREVIEW_BROWSER_TREE_TOTAL_BYTES: String(tree.totalBytes)
    }
  };
}

test('stages candidate-bound bundle and cleans the private lease', async () => {
  const value = await fixture();
  try {
    const lease = await acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base });
    assert.equal(await readFile(lease.executablePath, 'utf8'), CONTENT);
    assert.equal((await readdir(join(lease.executablePath, '..'))).includes('icudtl.dat'), true);
    assert.equal((await lstat(join(lease.executablePath, '..'))).mode & 0o777, 0o500);
    assert.equal((await lstat(lease.executablePath)).mode & 0o777, 0o500);
    assert.equal((await lstat(join(lease.executablePath, '../icudtl.dat'))).mode & 0o777, 0o400);
    await lease.cleanup();
    assert.deepEqual((await readdir(value.base)).sort(), ['browsers.json', 'chromium_headless_shell-1223']);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('ignores hostile ambient cache paths and stages only the custom candidate root', async () => {
  const value = await fixture();
  value.environment.PLAYWRIGHT_BROWSERS_PATH = join(value.base, 'hostile-default-cache');
  try {
    const lease = await acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base });
    assert.equal(lease.executablePath.startsWith(join(value.base, 'tbot-robot-preview-browser-')), true);
    assert.equal(await readFile(lease.executablePath, 'utf8'), CONTENT);
    await lease.cleanup();
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

test('rejects browser entry names that are not valid UTF-8', () => {
  assert.throws(() => decodeBrowserEntryName(Buffer.from([0x69, 0x6e, 0x76, 0xff])), /unsafe filename/);
});

test('afterStage substitution of sealed executable fails closed and cleans lease', async () => {
  const value = await fixture();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      afterStage: async ({ stagedRoot }) => {
        await chmod(stagedRoot, 0o700);
        const staged = join(stagedRoot, value.executable);
        await rename(staged, `${staged}.verified`);
        await writeFile(staged, 'malicious staged replacement\n', { mode: 0o500 });
      }
    }), /staged browser/);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('caller cleanup removes sealed lease after simulated spawn failure', async () => {
  const value = await fixture();
  try {
    const lease = await acquirePinnedRobotPreviewChromium({ ...value, stagingParent: value.base });
    try {
      throw new Error('simulated spawn failure');
    } catch (error) {
      assert.match(error.message, /spawn failure/);
    } finally {
      await lease.cleanup();
    }
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

test('keeps staged directories owner-accessible until the bundle is sealed', async () => {
  const value = await fixture({ nestedDirectoryMode: 0o550 });
  try {
    const lease = await acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      beforeSeal: async ({ stagedRoot }) => {
        assert.equal((await lstat(join(stagedRoot, 'resources'))).mode & 0o777, 0o700);
      },
      afterStage: async ({ stagedRoot }) => {
        assert.equal((await lstat(join(stagedRoot, 'resources'))).mode & 0o777, 0o500);
      }
    });
    assert.equal(await readFile(join(lease.executablePath, '../resources/locale.pak'), 'utf8'), 'locale\n');
    await lease.cleanup();
  } finally {
    await chmod(join(value.root, 'resources'), 0o700).catch(() => {});
    await rm(value.base, { recursive: true, force: true });
  }
});

test('abort during bundle copy removes the partial lease before acquisition rejects', async () => {
  const value = await fixture();
  const controller = new AbortController();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      signal: controller.signal,
      deadline: Date.now() + 1000,
      onStageProgress: async () => controller.abort(new Error('copy aborted')),
    }), /copy aborted/);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('abort after staging completes cleans the sealed lease before returning', async () => {
  const value = await fixture();
  const controller = new AbortController();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      signal: controller.signal,
      deadline: Date.now() + 1000,
      afterStage: async () => controller.abort(new Error('post-stage aborted')),
    }), /post-stage aborted/);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('transient acquisition cleanup failure is retried before rejection', async () => {
  const value = await fixture();
  value.environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256 = '0'.repeat(64);
  let cleanupCalls = 0;
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value,
      stagingParent: value.base,
      deadline: Date.now() + 1000,
      removeLease: async (root) => {
        cleanupCalls += 1;
        if (cleanupCalls === 1) throw new Error('transient removal failure');
        await rm(root, { recursive: true, force: true });
      },
    }), /identity does not match/);
    assert.equal(cleanupCalls, 2);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('abort during seal traversal cleans the staged lease before rejection', async () => {
  const value = await fixture({ nestedDirectoryMode: 0o700 });
  const controller = new AbortController();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value, stagingParent: value.base, signal: controller.signal, deadline: Date.now() + 1000,
      onSealProgress: async () => controller.abort(new Error('seal aborted')),
    }), /seal aborted/);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await chmod(join(value.root, 'resources'), 0o700).catch(() => {});
    await rm(value.base, { recursive: true, force: true });
  }
});

test('abort during sealed-file verification hashing cleans before rejection', async () => {
  const value = await fixture();
  const controller = new AbortController();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value, stagingParent: value.base, signal: controller.signal, deadline: Date.now() + 1000,
      onVerifyProgress: async () => controller.abort(new Error('verify aborted')),
    }), /verify aborted/);
    assert.equal((await readdir(value.base)).some((name) => name.startsWith('tbot-robot-preview-browser-')), false);
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

function fakeCleanupWorker({ reapAfterKill }) {
  const child = new EventEmitter();
  child.pid = 4242;
  child.exitCode = null;
  child.signalCode = null;
  child.kill = (signal) => {
    if (reapAfterKill) queueMicrotask(() => {
      child.signalCode = signal;
      child.emit('exit', null, signal);
    });
    return true;
  };
  return child;
}

function fakeAcquisitionWorker({ pid = 4343, spawnError, reapAfterKill = true } = {}) {
  const child = new EventEmitter();
  child.pid = pid;
  child.exitCode = null;
  child.signalCode = null;
  child.connected = true;
  child.send = () => true;
  child.kill = (signal) => {
    if (reapAfterKill && signal === 'SIGKILL') queueMicrotask(() => {
      child.signalCode = signal;
      child.emit('exit', null, signal);
    });
    return true;
  };
  if (spawnError) queueMicrotask(() => child.emit('error', Object.assign(new Error(spawnError), { code: spawnError })));
  return child;
}

test('owned acquisition exposes lease identity and worker handle before staging completes', async () => {
  const value = await fixture();
  const child = fakeAcquisitionWorker();
  try {
    const acquisition = await startPinnedRobotPreviewChromiumAcquisition({
      ...value, stagingParent: value.base, deadline: Date.now() + 100,
      spawnAcquisitionWorker: () => child,
    });
    assert.match(acquisition.retainedLeasePath, /tbot-robot-preview-browser-/);
    assert.equal(acquisition.leaseOwner, 'acquirePinnedRobotPreviewChromium');
    assert.equal(acquisition.worker, child);
    assert.equal(acquisition.workerPid, 4343);
    await assert.rejects(acquisition.cancel(new Error('test cancellation')), (error) => {
      assert.equal(error.retainedLeasePath, acquisition.retainedLeasePath);
      assert.equal(error.leaseOwner, acquisition.leaseOwner);
      assert.equal(error.workerPid, 4343);
      return true;
    });
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('hung acquisition worker is killed and reaped before its retained lease is reported', async () => {
  const value = await fixture();
  const child = fakeAcquisitionWorker();
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value, stagingParent: value.base, deadline: Date.now() + 20,
      acquisitionWorkerReapTimeoutMs: 20,
      spawnAcquisitionWorker: () => child,
      removeLease: async () => { throw new Error('cleanup remains owned'); },
      cleanupRetryLimit: 1,
    }), (error) => {
      assert.equal(child.signalCode, 'SIGKILL');
      assert.match(error.retainedLeasePath, /tbot-robot-preview-browser-/);
      assert.equal(error.leaseOwner, 'acquirePinnedRobotPreviewChromium');
      assert.equal(error.workerPid, 4343);
      return true;
    });
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

for (const code of ['EAGAIN', 'ENOENT']) {
  test(`asynchronous acquisition worker spawn ${code} never crashes and retains auditable cleanup ownership`, async () => {
    const value = await fixture();
    try {
      await assert.rejects(acquirePinnedRobotPreviewChromium({
        ...value, stagingParent: value.base, deadline: Date.now() + 100,
        spawnAcquisitionWorker: () => fakeAcquisitionWorker({ pid: null, spawnError: code }),
        removeLease: async () => { throw new Error('cleanup remains owned'); },
        cleanupRetryLimit: 1,
      }), (error) => {
        assert.match(error.message, new RegExp(code));
        assert.match(error.retainedLeasePath, /tbot-robot-preview-browser-/);
        assert.equal(error.leaseOwner, 'acquirePinnedRobotPreviewChromium');
        assert.equal(error.workerPid, null);
        return true;
      });
    } finally {
      await rm(value.base, { recursive: true, force: true });
    }
  });
}

test('unreaped cleanup worker exposes pid and retained lease ownership synchronously', async () => {
  const value = await fixture();
  value.environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256 = '0'.repeat(64);
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value, stagingParent: value.base, deadline: Date.now() + 1000,
      cleanupRetryLimit: 1, cleanupWorkerTimeoutMs: 1, cleanupReapTimeoutMs: 5,
      spawnCleanupWorker: () => fakeCleanupWorker({ reapAfterKill: false }),
    }), (error) => {
      assert.equal(error.cleanupWorkerPid, 4242);
      assert.equal(Number.isSafeInteger(error.workerPid), true);
      assert.notEqual(error.workerPid, 4242);
      assert.equal(error.leaseOwner, 'acquirePinnedRobotPreviewChromium');
      assert.match(error.retainedLeasePath, /tbot-robot-preview-browser-/);
      return true;
    });
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});

test('SIGKILL cleanup worker is reaped before timeout error returns', async () => {
  const value = await fixture();
  value.environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256 = '0'.repeat(64);
  const child = fakeCleanupWorker({ reapAfterKill: true });
  try {
    await assert.rejects(acquirePinnedRobotPreviewChromium({
      ...value, stagingParent: value.base, deadline: Date.now() + 1000,
      cleanupRetryLimit: 1, cleanupWorkerTimeoutMs: 1, cleanupReapTimeoutMs: 20,
      spawnCleanupWorker: () => child,
    }), (error) => {
      assert.equal(error.cleanupWorkerPid, undefined);
      assert.equal(error.leaseOwner, 'acquirePinnedRobotPreviewChromium');
      assert.match(error.retainedLeasePath, /tbot-robot-preview-browser-/);
      assert.equal(child.signalCode, 'SIGKILL');
      return true;
    });
  } finally {
    await rm(value.base, { recursive: true, force: true });
  }
});
