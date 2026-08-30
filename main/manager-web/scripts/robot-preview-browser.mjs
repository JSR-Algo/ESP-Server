import { constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { chmod, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rm } from 'node:fs/promises';
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

async function stageVerifiedBundle(sourceRoot, stagingRoot) {
  // Mode 0700 isolates other users; the same UID and root remain trusted during this local gate.
  const hash = createHash('sha256');
  const state = { entryCount: 0, totalBytes: 0 };

  async function visit(sourceDirectory, destinationDirectory) {
    const entries = await readdir(sourceDirectory, { withFileTypes: true });
    entries.sort((left, right) => Buffer.from(left.name).compare(Buffer.from(right.name)));
    for (const entry of entries) {
      const source = join(sourceDirectory, entry.name);
      const destination = join(destinationDirectory, entry.name);
      const relativePath = relative(sourceRoot, source).split(sep).join('/');
      const before = await lstat(source, { bigint: true });
      state.entryCount += 1;
      if (state.entryCount > 10_000) throw new Error('browser bundle has too many entries');
      const mode = Number(before.mode & 0o777n);
      if (before.isDirectory()) {
        digestField(hash, 'directory'); digestField(hash, relativePath); digestField(hash, mode);
        await mkdir(destination, { mode: 0o700 });
        await visit(source, destination);
        const after = await lstat(source, { bigint: true });
        const identity = (stat) => [stat.dev, stat.ino, stat.mode, stat.nlink, stat.mtimeNs, stat.ctimeNs].join(':');
        if (!after.isDirectory() || identity(before) !== identity(after)) throw new Error('browser bundle directory changed while staging');
        await chmod(destination, mode & 0o700);
        continue;
      }
      if (!before.isFile() || before.nlink !== 1n) throw new Error('browser bundle contains an unsafe entry');
      state.totalBytes += Number(before.size);
      if (state.totalBytes > 512 * 1024 * 1024) throw new Error('browser bundle is too large');
      let sourceHandle;
      let destinationHandle;
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
          const length = Number(before.size - offset > BigInt(buffer.length) ? BigInt(buffer.length) : before.size - offset);
          const { bytesRead } = await sourceHandle.read(buffer, 0, length, Number(offset));
          if (!bytesRead) throw new Error('browser bundle changed while staging');
          hash.update(buffer.subarray(0, bytesRead));
          let written = 0;
          while (written < bytesRead) {
            const result = await destinationHandle.write(
              buffer, written, bytesRead - written, Number(offset) + written
            );
            if (!result.bytesWritten) throw new Error('staged browser write did not progress');
            written += result.bytesWritten;
          }
          offset += BigInt(bytesRead);
        }
        await destinationHandle.sync();
        const after = await sourceHandle.stat({ bigint: true });
        const named = await lstat(source, { bigint: true });
        if (identity(before) !== identity(after) || identity(before) !== identity(named)) throw new Error('browser bundle changed while staging');
      } finally {
        await destinationHandle?.close();
        await sourceHandle?.close();
      }
    }
  }

  await visit(sourceRoot, stagingRoot);
  return { schema: TREE_SCHEMA, sha256: hash.digest('hex'), ...state };
}

export async function acquirePinnedRobotPreviewChromium({
  environment = process.env,
  metadataPath = METADATA_PATH,
  platform = process.platform,
  arch = process.arch,
  stagingParent = tmpdir(),
  afterStage = async () => {}
} = {}) {
  const descriptor = descriptorFromEnvironment(environment);
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8'));
  const browser = metadata.browsers?.find((entry) => entry.name === ENGINE);
  if (!browser || browser.revision !== descriptor.revision) throw new Error('Candidate browser revision does not match installed Playwright metadata');
  const [bundleName, executable] = executableLayout(platform, arch);
  const expectedRoot = join(`chromium_headless_shell-${browser.revision}`, bundleName);
  if (!descriptor.root.endsWith(`${sep}${expectedRoot}`) || descriptor.executable !== executable || await realpath(descriptor.root) !== descriptor.root) {
    throw new Error('Candidate browser bundle does not match installed Playwright platform metadata');
  }
  const leaseRoot = await mkdtemp(join(stagingParent, 'tbot-robot-preview-browser-'));
  await chmod(leaseRoot, 0o700);
  let active = true;
  const cleanup = async () => {
    if (!active) return;
    active = false;
    await rm(leaseRoot, { recursive: true, force: true });
  };
  try {
    const observed = await stageVerifiedBundle(descriptor.root, leaseRoot);
    if (JSON.stringify(observed) !== JSON.stringify(descriptor.treeDigest)) throw new Error('Candidate browser bundle identity does not match staged content');
    await afterStage({ sourceRoot: descriptor.root, stagedRoot: leaseRoot });
    return { executablePath: join(leaseRoot, descriptor.executable), cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}
