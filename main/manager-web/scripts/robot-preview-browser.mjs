import { chmodSync, constants, mkdtempSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { chmod, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const ENGINE = 'chromium-headless-shell';
const TREE_SCHEMA = 'sha256-path-mode-bytes-v1';
const METADATA_PATH = join(dirname(fileURLToPath(import.meta.url)), '../node_modules/playwright-core/browsers.json');

function executableLayout(platform, arch) {
  const layout = {
    'darwin-arm64': ['chrome-headless-shell-mac-arm64', 'chrome-headless-shell'],
    'darwin-x64': ['chrome-headless-shell-mac-x64', 'chrome-headless-shell'],
    'linux-arm64': ['chrome-linux', 'headless_shell'],
    'linux-x64': ['chrome-headless-shell-linux64', 'chrome-headless-shell']
  }[`${platform}-${arch}`];
  if (!layout) throw new Error(`Unsupported robot preview browser platform: ${platform}-${arch}`);
  return layout;
}

function descriptorFromEnvironment(environment) {
  const treeEntryCount = Number(environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_ENTRY_COUNT);
  const treeTotalBytes = Number(environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_TOTAL_BYTES);
  const descriptor = {
    root: environment.TBOT_ROBOT_PREVIEW_BROWSER_ROOT,
    executable: environment.TBOT_ROBOT_PREVIEW_BROWSER_EXECUTABLE,
    engine: environment.TBOT_ROBOT_PREVIEW_BROWSER_ENGINE,
    revision: environment.TBOT_ROBOT_PREVIEW_BROWSER_REVISION,
    treeDigest: {
      schema: TREE_SCHEMA,
      sha256: environment.TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256,
      entryCount: treeEntryCount,
      totalBytes: treeTotalBytes
    }
  };
  if (!isAbsolute(descriptor.root || '') || !descriptor.executable || descriptor.executable.includes('/') || descriptor.executable.includes('\\')) {
    throw new Error('Candidate-bound robot preview browser bundle is invalid');
  }
  if (descriptor.engine !== ENGINE || !/^[1-9][0-9]*$/.test(descriptor.revision || '')) {
    throw new Error('Candidate-bound robot preview browser engine/revision is invalid');
  }
  if (!/^[0-9a-f]{64}$/.test(descriptor.treeDigest.sha256 || '') || !Number.isSafeInteger(treeEntryCount) || treeEntryCount <= 0 || !Number.isSafeInteger(treeTotalBytes) || treeTotalBytes <= 0 || treeTotalBytes > 512 * 1024 * 1024) {
    throw new Error('Candidate-bound robot preview browser tree identity is invalid');
  }
  return descriptor;
}

function digestField(hash, value) {
  const bytes = Buffer.from(String(value));
  const length = Buffer.alloc(8);
  length.writeBigUInt64BE(BigInt(bytes.length));
  hash.update(length).update(bytes);
}

function throwIfAcquisitionCancelled(signal, deadline) {
  if (signal?.aborted) throw signal.reason || new Error('Candidate browser acquisition aborted');
  if (deadline !== undefined && Date.now() >= deadline) throw new Error('Candidate browser acquisition deadline exceeded');
}

export function decodeBrowserEntryName(bytes) {
  const name = bytes.toString('utf8');
  if (!Buffer.from(name, 'utf8').equals(bytes)) throw new Error('browser bundle contains an unsafe filename');
  return name;
}

async function stageVerifiedBundle(sourceRoot, stagingRoot, { signal, deadline, onStageProgress }) {
  // Mode 0700 isolates other users; the same UID and root remain trusted during this local gate.
  const hash = createHash('sha256');
  const state = { entryCount: 0, totalBytes: 0 };
  const manifest = [];

  async function visit(sourceDirectory, destinationDirectory) {
    const entries = await readdir(sourceDirectory, { withFileTypes: true, encoding: 'buffer' });
    entries.sort((left, right) => left.name.compare(right.name));
    for (const entry of entries) {
      throwIfAcquisitionCancelled(signal, deadline);
      const name = decodeBrowserEntryName(entry.name);
      const source = join(sourceDirectory, name);
      const destination = join(destinationDirectory, name);
      const relativePath = relative(sourceRoot, source).split(sep).join('/');
      const before = await lstat(source, { bigint: true });
      state.entryCount += 1;
      if (state.entryCount > 10_000) throw new Error('browser bundle has too many entries');
      const mode = Number(before.mode & 0o777n);
      if (before.isDirectory()) {
        manifest.push({ type: 'directory', path: relativePath, mode });
        digestField(hash, 'directory'); digestField(hash, relativePath); digestField(hash, mode);
        await mkdir(destination, { mode: 0o700 });
        await visit(source, destination);
        const after = await lstat(source, { bigint: true });
        const identity = (stat) => [stat.dev, stat.ino, stat.mode, stat.nlink, stat.mtimeNs, stat.ctimeNs].join(':');
        if (!after.isDirectory() || identity(before) !== identity(after)) throw new Error('browser bundle directory changed while staging');
        continue;
      }
      if (!before.isFile() || before.nlink !== 1n) throw new Error('browser bundle contains an unsafe entry');
      state.totalBytes += Number(before.size);
      if (state.totalBytes > 512 * 1024 * 1024) throw new Error('browser bundle is too large');
      let sourceHandle;
      let destinationHandle;
      const fileHash = createHash('sha256');
      try {
        sourceHandle = await open(source, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
        const opened = await sourceHandle.stat({ bigint: true });
        const identity = (stat) => [stat.dev, stat.ino, stat.mode, stat.nlink, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
        if (identity(before) !== identity(opened)) throw new Error('browser bundle changed before staging');
        destinationHandle = await open(destination, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY, mode & 0o700);
        digestField(hash, 'regular'); digestField(hash, relativePath); digestField(hash, mode); digestField(hash, before.size);
        const buffer = Buffer.allocUnsafe(1024 * 1024);
        let offset = 0n;
        while (offset < before.size) {
          throwIfAcquisitionCancelled(signal, deadline);
          const length = Number(before.size - offset > BigInt(buffer.length) ? BigInt(buffer.length) : before.size - offset);
          const { bytesRead } = await sourceHandle.read(buffer, 0, length, Number(offset));
          if (!bytesRead) throw new Error('browser bundle changed while staging');
          hash.update(buffer.subarray(0, bytesRead));
          fileHash.update(buffer.subarray(0, bytesRead));
          let written = 0;
          while (written < bytesRead) {
            const result = await destinationHandle.write(
              buffer, written, bytesRead - written, Number(offset) + written
            );
            if (!result.bytesWritten) throw new Error('staged browser write did not progress');
            written += result.bytesWritten;
          }
          offset += BigInt(bytesRead);
          await onStageProgress({ source, destination, offset, size: before.size });
        }
        await destinationHandle.sync();
        const after = await sourceHandle.stat({ bigint: true });
        const named = await lstat(source, { bigint: true });
        if (identity(before) !== identity(after) || identity(before) !== identity(named)) throw new Error('browser bundle changed while staging');
        manifest.push({
          type: 'regular', path: relativePath, mode, bytes: Number(before.size),
          sha256: fileHash.digest('hex')
        });
      } finally {
        await destinationHandle?.close();
        await sourceHandle?.close();
      }
    }
  }

  await visit(sourceRoot, stagingRoot);
  return { treeDigest: { schema: TREE_SCHEMA, sha256: hash.digest('hex'), ...state }, manifest };
}

async function sealBundle(root, manifest, { signal, deadline, onSealProgress }) {
  for (const entry of manifest.filter((item) => item.type === 'regular')) {
    throwIfAcquisitionCancelled(signal, deadline);
    await chmod(join(root, entry.path), entry.mode & 0o111 ? 0o500 : 0o400);
    await onSealProgress(entry);
  }
  const directories = manifest
    .filter((item) => item.type === 'directory')
    .sort((left, right) => right.path.split('/').length - left.path.split('/').length);
  for (const entry of directories) {
    throwIfAcquisitionCancelled(signal, deadline);
    await chmod(join(root, entry.path), 0o500);
    await onSealProgress(entry);
  }
  throwIfAcquisitionCancelled(signal, deadline);
  await chmod(root, 0o500);
}

async function verifySealedBundle(root, manifest, { signal, deadline, onVerifyProgress }) {
  const expected = new Map(manifest.map((entry) => [entry.path, entry]));
  const seen = new Set();
  const rootMetadata = await lstat(root, { bigint: true });
  if (!rootMetadata.isDirectory() || Number(rootMetadata.mode & 0o777n) !== 0o500) throw new Error('staged browser root is not sealed');

  async function visit(directory, relativeParent = '') {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      throwIfAcquisitionCancelled(signal, deadline);
      const relativePath = relativeParent ? `${relativeParent}/${entry.name}` : entry.name;
      const expectedEntry = expected.get(relativePath);
      if (!expectedEntry || seen.has(relativePath)) throw new Error('staged browser tree has unexpected entries');
      seen.add(relativePath);
      const path = join(directory, entry.name);
      const before = await lstat(path, { bigint: true });
      if (expectedEntry.type === 'directory') {
        if (!before.isDirectory() || Number(before.mode & 0o777n) !== 0o500) throw new Error('staged browser directory is mutable');
        await visit(path, relativePath);
        continue;
      }
      const sealedMode = expectedEntry.mode & 0o111 ? 0o500 : 0o400;
      if (!before.isFile() || before.nlink !== 1n || Number(before.mode & 0o777n) !== sealedMode || Number(before.size) !== expectedEntry.bytes) {
        throw new Error('staged browser file is mutable or changed');
      }
      let handle;
      try {
        handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
        const opened = await handle.stat({ bigint: true });
        const identity = (stat) => [stat.dev, stat.ino, stat.mode, stat.nlink, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
        if (identity(before) !== identity(opened)) throw new Error('staged browser file changed before verification');
        const hash = createHash('sha256');
        const buffer = Buffer.allocUnsafe(1024 * 1024);
        let offset = 0n;
        while (offset < before.size) {
          throwIfAcquisitionCancelled(signal, deadline);
          const length = Number(before.size - offset > BigInt(buffer.length) ? BigInt(buffer.length) : before.size - offset);
          const { bytesRead } = await handle.read(buffer, 0, length, Number(offset));
          if (!bytesRead) throw new Error('staged browser file changed during verification');
          hash.update(buffer.subarray(0, bytesRead));
          offset += BigInt(bytesRead);
          await onVerifyProgress({ path, offset, size: before.size });
        }
        const after = await handle.stat({ bigint: true });
        const named = await lstat(path, { bigint: true });
        if (identity(before) !== identity(after) || identity(before) !== identity(named) || hash.digest('hex') !== expectedEntry.sha256) {
          throw new Error('staged browser file identity changed');
        }
      } finally {
        await handle?.close();
      }
    }
  }

  await visit(root);
  if (seen.size !== expected.size) throw new Error('staged browser tree is incomplete');
}

async function removeBrowserLease(root, {
  deadline, spawnCleanupWorker = spawn, cleanupWorkerTimeoutMs = 10000, cleanupReapTimeoutMs = 1000,
} = {}) {
  const cleanupScript = `
    const { chmod, lstat, readdir, rm } = require('node:fs/promises');
    const root = process.argv[1];
    async function makeRemovable(path) {
      const metadata = await lstat(path);
      if (!metadata.isDirectory()) return;
      await chmod(path, 0o700);
      for (const entry of await readdir(path, { withFileTypes: true })) {
        const child = path + '/' + entry.name;
        if (entry.isDirectory()) await makeRemovable(child);
        else if (entry.isFile()) await chmod(child, 0o600);
      }
    }
    (async () => { try { await makeRemovable(root); } catch {} await rm(root, { recursive: true, force: true }); })()
      .catch((error) => { console.error(error.message); process.exitCode = 1; });
  `;
  const child = spawnCleanupWorker(process.execPath, ['-e', cleanupScript, root], { stdio: 'ignore' });
  const ownedTerminal = ownProcessTerminal(child);
  const remainingMs = deadline === undefined
    ? cleanupWorkerTimeoutMs
    : Math.min(cleanupWorkerTimeoutMs, Math.max(0, deadline - Date.now()));
  const termBudgetMs = Math.max(0, Math.floor(remainingMs / 2));
  const terminal = await waitForProcessTerminal(ownedTerminal, termBudgetMs);
  if (terminal.error) {
    if (!(Number.isSafeInteger(child.pid) && child.pid > 0)) {
      terminal.error.retainedLeasePath = root;
      terminal.error.leaseOwner = 'acquirePinnedRobotPreviewChromium';
      throw terminal.error;
    }
    let killError;
    try {
      if (!child.kill('SIGKILL')) killError = new Error('Candidate browser cleanup worker SIGKILL was not delivered');
    } catch (error) {
      killError = error;
    }
    const reapBudgetMs = deadline === undefined
      ? cleanupReapTimeoutMs
      : Math.min(cleanupReapTimeoutMs, Math.max(0, deadline - Date.now()));
    const reaped = await waitForProcessTerminal(ownedTerminal, reapBudgetMs, false);
    if (!reaped.exited) {
      const detail = killError?.message ? `: ${killError.message}` : '';
      const error = new Error(`Candidate browser cleanup worker could not be reaped after runtime error${detail}`, {
        cause: killError || terminal.error,
      });
      error.code = killError?.code || terminal.error.code;
      error.cleanupWorkerPid = child.pid;
      error.retainedLeasePath = root;
      error.leaseOwner = 'acquirePinnedRobotPreviewChromium';
      throw error;
    }
    terminal.error.retainedLeasePath = root;
    terminal.error.leaseOwner = 'acquirePinnedRobotPreviewChromium';
    throw terminal.error;
  }
  if (!terminal.exited) {
    let killError;
    try {
      if (!child.kill('SIGKILL')) killError = new Error('Candidate browser cleanup worker SIGKILL was not delivered');
    } catch (error) {
      killError = error;
    }
    const reapBudgetMs = deadline === undefined
      ? cleanupReapTimeoutMs
      : Math.min(cleanupReapTimeoutMs, Math.max(0, deadline - Date.now()));
    const reaped = await waitForProcessTerminal(ownedTerminal, reapBudgetMs, false);
    if (!reaped.exited) {
      const detail = killError?.message ? `: ${killError.message}` : '';
      const error = new Error(`Candidate browser cleanup worker could not be reaped after SIGKILL${detail}`, {
        cause: killError || ownedTerminal.error,
      });
      error.code = killError?.code || ownedTerminal.error?.code;
      error.cleanupWorkerPid = child.pid;
      error.retainedLeasePath = root;
      error.leaseOwner = 'acquirePinnedRobotPreviewChromium';
      throw error;
    }
    throw new Error('Candidate browser cleanup process timed out');
  }
  if (child.exitCode !== 0) throw new Error(`Candidate browser cleanup process exited ${child.exitCode}`);
}

function ownProcessTerminal(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return { completion: Promise.resolve({ exited: true }) };
  }
  const ownedTerminal = { completion: null, failure: null, error: null };
  let resolveFailure;
  ownedTerminal.failure = new Promise((resolve) => { resolveFailure = resolve; });
  ownedTerminal.completion = new Promise((resolve) => {
    const finish = (value) => {
      child.off('exit', onExit);
      child.off('error', onError);
      resolve(value);
    };
    const onExit = () => finish({ exited: true });
    const onError = (error) => {
      if (ownedTerminal.error) return;
      ownedTerminal.error = error;
      resolveFailure({ exited: false, error });
    };
    child.once('exit', onExit);
    child.on('error', onError);
  });
  return ownedTerminal;
}

async function waitForProcessTerminal(ownedTerminal, timeoutMs, observeFailure = true) {
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve({ exited: false }), timeoutMs);
  });
  try {
    const terminalSignals = [ownedTerminal.completion];
    if (observeFailure && ownedTerminal.failure) terminalSignals.push(ownedTerminal.failure);
    terminalSignals.push(timeout);
    return await Promise.race(terminalSignals);
  } finally {
    clearTimeout(timer);
  }
}

function waitForProcessExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    let timer;
    const finish = (value) => { clearTimeout(timer); child.off('exit', onExit); resolve(value); };
    const onExit = () => finish(true);
    child.once('exit', onExit);
    timer = setTimeout(() => finish(false), timeoutMs);
  });
}

async function acquirePinnedRobotPreviewChromiumInProcess({
  environment = process.env,
  metadataPath = METADATA_PATH,
  platform = process.platform,
  arch = process.arch,
  stagingParent = tmpdir(),
  beforeSeal = async () => {},
  afterStage = async () => {},
  signal,
  deadline,
  onStageProgress = async () => {},
  onSealProgress = async () => {},
  onVerifyProgress = async () => {},
  removeLease = removeBrowserLease,
  spawnCleanupWorker = spawn,
  cleanupWorkerTimeoutMs = 10000,
  cleanupReapTimeoutMs = 1000,
  cleanupRetryLimit = 3,
  leaseRoot: providedLeaseRoot,
  cleanupOnFailure = true,
} = {}) {
  throwIfAcquisitionCancelled(signal, deadline);
  const descriptor = descriptorFromEnvironment(environment);
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8'));
  const browser = metadata.browsers?.find((entry) => entry.name === ENGINE);
  if (!browser || browser.revision !== descriptor.revision) throw new Error('Candidate browser revision does not match installed Playwright metadata');
  const [bundleName, executable] = executableLayout(platform, arch);
  const expectedRoot = join(`chromium_headless_shell-${browser.revision}`, bundleName);
  if (!descriptor.root.endsWith(`${sep}${expectedRoot}`) || descriptor.executable !== executable || await realpath(descriptor.root) !== descriptor.root) {
    throw new Error('Candidate browser bundle does not match installed Playwright platform metadata');
  }
  const leaseRoot = providedLeaseRoot || await mkdtemp(join(stagingParent, 'tbot-robot-preview-browser-'));
  await chmod(leaseRoot, 0o700);
  let active = true;
  let cleanupPromise;
  const cleanup = () => {
    if (!active) return;
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      let lastError;
      let attemptsMade = 0;
      for (let attempt = 1; attempt <= cleanupRetryLimit; attempt += 1) {
        attemptsMade = attempt;
        const cleanupController = new AbortController();
        const cleanupRemainingMs = deadline === undefined ? 10000 : Math.max(0, deadline - Date.now());
        const cleanupTimer = setTimeout(
          () => cleanupController.abort(new Error('Candidate browser cleanup deadline exceeded')),
          cleanupRemainingMs,
        );
        try {
          await removeLease(leaseRoot, {
            deadline, signal: cleanupController.signal, spawnCleanupWorker,
            cleanupWorkerTimeoutMs, cleanupReapTimeoutMs,
          });
          active = false;
          return;
        } catch (error) {
          lastError = error;
          if (deadline !== undefined && Date.now() >= deadline) break;
        } finally {
          clearTimeout(cleanupTimer);
        }
      }
      const attemptLabel = attemptsMade === 1 ? 'attempt' : 'attempts';
      const cleanupError = new Error(`Candidate browser lease cleanup failed after ${attemptsMade} ${attemptLabel}`, { cause: lastError });
      cleanupError.retainedLeasePath = leaseRoot;
      cleanupError.leaseOwner = 'acquirePinnedRobotPreviewChromium';
      if (lastError?.cleanupWorkerPid !== undefined) cleanupError.cleanupWorkerPid = lastError.cleanupWorkerPid;
      throw cleanupError;
    })().catch((error) => {
      cleanupPromise = undefined;
      throw error;
    });
    return cleanupPromise;
  };
  try {
    const observed = await stageVerifiedBundle(descriptor.root, leaseRoot, { signal, deadline, onStageProgress });
    throwIfAcquisitionCancelled(signal, deadline);
    if (JSON.stringify(observed.treeDigest) !== JSON.stringify(descriptor.treeDigest)) throw new Error('Candidate browser bundle identity does not match staged content');
    await beforeSeal({ sourceRoot: descriptor.root, stagedRoot: leaseRoot });
    throwIfAcquisitionCancelled(signal, deadline);
    await sealBundle(leaseRoot, observed.manifest, { signal, deadline, onSealProgress });
    await afterStage({ sourceRoot: descriptor.root, stagedRoot: leaseRoot });
    throwIfAcquisitionCancelled(signal, deadline);
    await verifySealedBundle(leaseRoot, observed.manifest, { signal, deadline, onVerifyProgress });
    throwIfAcquisitionCancelled(signal, deadline);
    return { executablePath: join(leaseRoot, descriptor.executable), cleanup };
  } catch (error) {
    if (cleanupOnFailure) await cleanup();
    throw error;
  }
}

function acquisitionOwnership(error, leaseRoot, workerPid) {
  error.retainedLeasePath ||= leaseRoot;
  error.leaseOwner ||= 'acquirePinnedRobotPreviewChromium';
  if (error.workerPid === undefined) {
    error.workerPid = Number.isSafeInteger(workerPid) && workerPid > 0 ? workerPid : null;
  }
  return error;
}

function serializeError(error) {
  return {
    message: error?.message || String(error), code: error?.code,
    retainedLeasePath: error?.retainedLeasePath, leaseOwner: error?.leaseOwner,
    workerPid: error?.workerPid, childPid: error?.childPid, cleanupWorkerPid: error?.cleanupWorkerPid,
  };
}

function ipcSafe(value) {
  if (typeof value === 'bigint') return Number(value);
  if (Array.isArray(value)) return value.map(ipcSafe);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, ipcSafe(item)])
  );
  return value;
}

function deserializeError(value) {
  const error = new Error(value?.message || 'Candidate browser acquisition worker failed');
  for (const key of ['code', 'retainedLeasePath', 'leaseOwner', 'workerPid', 'childPid', 'cleanupWorkerPid']) {
    if (value?.[key] !== undefined) error[key] = value[key];
  }
  return error;
}

async function terminateOwnedWorker(child, deadline, timeoutMs) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  const remainingMs = () => deadline === undefined
    ? timeoutMs
    : Math.max(0, Math.min(timeoutMs, deadline - Date.now()));
  child.kill('SIGTERM');
  if (await waitForProcessExit(child, Math.floor(remainingMs() / 2))) return true;
  child.kill('SIGKILL');
  return waitForProcessExit(child, remainingMs());
}

export function startPinnedRobotPreviewChromiumAcquisition({
  environment = process.env,
  metadataPath = METADATA_PATH,
  platform = process.platform,
  arch = process.arch,
  stagingParent = tmpdir(),
  beforeSeal = async () => {},
  afterStage = async () => {},
  signal,
  deadline,
  cleanupDeadline = deadline,
  onStageProgress = async () => {},
  onSealProgress = async () => {},
  onVerifyProgress = async () => {},
  removeLease = removeBrowserLease,
  spawnCleanupWorker = spawn,
  spawnAcquisitionWorker = (file, args, options) => spawn(process.execPath, [file, ...args], options),
  acquisitionWorkerReapTimeoutMs = 400,
  cleanupWorkerTimeoutMs = 10000,
  cleanupReapTimeoutMs = 1000,
  cleanupRetryLimit = 3,
} = {}) {
  const owner = 'acquirePinnedRobotPreviewChromium';
  const leaseRoot = mkdtempSync(join(stagingParent, 'tbot-robot-preview-browser-'));
  try {
    chmodSync(leaseRoot, 0o700);
  } catch (error) {
    try {
      rmSync(leaseRoot, { recursive: true, force: true });
    } catch (cleanupError) {
      throw acquisitionOwnership(
        new Error(`${error.message}; synchronous lease cleanup failed: ${cleanupError.message}`, { cause: cleanupError }),
        leaseRoot,
        null,
      );
    }
    throw acquisitionOwnership(error, leaseRoot, null);
  }
  let worker;
  let settled = false;
  let workerSpawnFailed = false;
  let deadlineTimer;

  const removeOwnedLease = async (workerPid, deadlineOverride = cleanupDeadline) => {
    let lastError;
    let attemptsMade = 0;
    for (let attempt = 1; attempt <= cleanupRetryLimit; attempt += 1) {
      attemptsMade = attempt;
      try {
        await removeLease(leaseRoot, {
          deadline: deadlineOverride, spawnCleanupWorker, cleanupWorkerTimeoutMs, cleanupReapTimeoutMs,
        });
        return;
      } catch (error) {
        lastError = error;
        if (error.cleanupWorkerPid !== undefined) break;
        if (deadlineOverride !== undefined && Date.now() >= deadlineOverride) break;
      }
    }
    const detail = lastError?.message ? `: ${lastError.message}` : '';
    const attemptLabel = attemptsMade === 1 ? 'attempt' : 'attempts';
    const cleanupError = new Error(`Candidate browser lease cleanup failed after ${attemptsMade} ${attemptLabel}${detail}`, { cause: lastError });
    if (lastError?.code !== undefined) cleanupError.code = lastError.code;
    if (lastError?.cleanupWorkerPid !== undefined) {
      cleanupError.cleanupWorkerPid = lastError.cleanupWorkerPid;
    }
    throw acquisitionOwnership(cleanupError, leaseRoot, workerPid);
  };
  const cleanupOwnedLease = async (sourceError, workerPid) => {
    try {
      await removeOwnedLease(workerPid);
    } catch (cleanupError) {
      const error = new Error(`${sourceError.message}; ${cleanupError.message}`, { cause: cleanupError });
      if (cleanupError.code !== undefined) error.code = cleanupError.code;
      if (cleanupError.cleanupWorkerPid !== undefined) error.cleanupWorkerPid = cleanupError.cleanupWorkerPid;
      throw acquisitionOwnership(error, leaseRoot, workerPid);
    }
    throw sourceError;
  };

  let resolveWorkerResult;
  let rejectWorkerResult;
  const workerResult = new Promise((resolve, reject) => {
    resolveWorkerResult = resolve;
    rejectWorkerResult = reject;
  });
  const finish = (callback, value) => {
    if (settled) return;
    settled = true;
    clearTimeout(deadlineTimer);
    signal?.removeEventListener('abort', onAbort);
    callback(value);
  };
  const fail = (error) => finish(rejectWorkerResult, error);
  const onAbort = () => fail(signal.reason || new Error('Candidate browser acquisition aborted'));
  const sendToWorker = (message) => {
    try {
      worker.send(message, (error) => { if (error) fail(error); });
    } catch (error) {
      fail(error);
    }
  };
  if (signal?.aborted || (deadline !== undefined && Date.now() >= deadline)) {
    fail(signal?.reason || new Error('Candidate browser acquisition deadline exceeded'));
  } else {
    try {
      worker = spawnAcquisitionWorker(fileURLToPath(import.meta.url), ['--acquisition-worker'], {
        stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      });
    } catch (error) {
      fail(error);
    }
  }
  if (worker) {
    worker.on('error', (error) => { workerSpawnFailed = true; fail(error); });
    worker.once('exit', (code, workerSignal) => {
      if (!settled) fail(new Error(`Candidate browser acquisition worker exited ${workerSignal || code}`));
    });
    worker.on('message', async (message) => {
      if (settled) return;
      if (message?.type === 'ready') {
        sendToWorker({ type: 'start', options: {
          environment: Object.fromEntries(Object.entries(environment).filter(([key]) => key.startsWith('TBOT_ROBOT_PREVIEW_BROWSER_'))),
          metadataPath, platform, arch, leaseRoot, deadline,
        } });
        return;
      }
      if (message?.type === 'progress') {
        const hook = { beforeSeal, afterStage, stage: onStageProgress, seal: onSealProgress, verify: onVerifyProgress }[message.phase];
        try {
          await hook(message.payload);
          sendToWorker({ type: 'continue', id: message.id });
        } catch (error) {
          sendToWorker({ type: 'reject', id: message.id, error: serializeError(error) });
        }
        return;
      }
      if (message?.type === 'success') finish(resolveWorkerResult, message);
      if (message?.type === 'failure') fail(deserializeError(message.error));
    });
  }
  signal?.addEventListener('abort', onAbort, { once: true });
  const remaining = deadline === undefined ? 60000 : Math.max(0, deadline - Date.now());
  if (!settled) deadlineTimer = setTimeout(() => fail(new Error('Candidate browser acquisition deadline exceeded')), remaining);

  const workerPid = Number.isSafeInteger(worker?.pid) && worker.pid > 0 ? worker.pid : null;
  const completion = (async () => {
    try {
      const result = await workerResult;
      if (!await terminateOwnedWorker(worker, cleanupDeadline, acquisitionWorkerReapTimeoutMs)) {
        throw acquisitionOwnership(new Error('Candidate browser acquisition worker could not be reaped after success'), leaseRoot, workerPid);
      }
      const cleanup = (options) => {
        const cleanupLeaseDeadline = options?.deadline;
        const completion = removeOwnedLease(workerPid, cleanupLeaseDeadline ?? cleanupDeadline);
        completion.catch(() => {});
        // Keep the public acquisition helper Promise-compatible while the harness requests an owned handle.
        if (options === undefined) return completion;
        return { completion };
      };
      return {
        executablePath: result.executablePath,
        leasePath: leaseRoot,
        leaseOwner: owner,
        workerPid,
        cleanup,
      };
    } catch (error) {
      const spawnFailedWithoutChild = workerSpawnFailed && !(Number.isSafeInteger(worker?.pid) && worker.pid > 0);
      const reaped = spawnFailedWithoutChild
        || await terminateOwnedWorker(worker, cleanupDeadline, acquisitionWorkerReapTimeoutMs);
      const ownedError = acquisitionOwnership(error, leaseRoot, workerPid);
      if (!reaped) {
        throw acquisitionOwnership(
          new Error('Candidate browser acquisition worker could not be reaped after SIGKILL', { cause: ownedError }),
          leaseRoot,
          workerPid,
        );
      }
      return cleanupOwnedLease(ownedError, workerPid);
    }
  })();
  completion.catch(() => {});
  const cancel = (reason = new Error('Candidate browser acquisition cancelled')) => {
    fail(reason);
  };
  return {
    retainedLeasePath: leaseRoot,
    leaseOwner: owner,
    worker,
    workerPid,
    completion,
    cancel,
  };
}

export async function acquirePinnedRobotPreviewChromium(options = {}) {
  const acquisition = startPinnedRobotPreviewChromiumAcquisition(options);
  return acquisition.completion;
}

if (process.argv[2] === '--acquisition-worker' && process.send) {
  const pendingHooks = new Map();
  let hookId = 0;
  const sendToParent = (message) => new Promise((resolve, reject) => {
    try {
      process.send(message, (error) => error ? reject(error) : resolve());
    } catch (error) {
      reject(error);
    }
  });
  const remoteHook = (phase) => (payload) => new Promise((resolve, reject) => {
    const id = ++hookId;
    pendingHooks.set(id, { resolve, reject });
    sendToParent({ type: 'progress', phase, id, payload: ipcSafe(payload) }).catch((error) => {
      pendingHooks.delete(id);
      reject(error);
    });
  });
  process.on('message', async (message) => {
    if (message?.type === 'continue' || message?.type === 'reject') {
      const pending = pendingHooks.get(message.id);
      if (!pending) return;
      pendingHooks.delete(message.id);
      message.type === 'continue' ? pending.resolve() : pending.reject(deserializeError(message.error));
      return;
    }
    if (message?.type !== 'start') return;
    try {
      const lease = await acquirePinnedRobotPreviewChromiumInProcess({
        ...message.options, cleanupOnFailure: false,
        beforeSeal: remoteHook('beforeSeal'), afterStage: remoteHook('afterStage'),
        onStageProgress: remoteHook('stage'), onSealProgress: remoteHook('seal'), onVerifyProgress: remoteHook('verify'),
      });
      await sendToParent({ type: 'success', executablePath: lease.executablePath });
    } catch (error) {
      await sendToParent({ type: 'failure', error: serializeError(error) }).catch(() => {});
    }
  });
  sendToParent({ type: 'ready' }).catch(() => {});
}
