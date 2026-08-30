import { constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { lstat, open, readFile, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ENVIRONMENT_KEYS = {
  path: 'TBOT_ROBOT_PREVIEW_BROWSER_PATH',
  engine: 'TBOT_ROBOT_PREVIEW_BROWSER_ENGINE',
  revision: 'TBOT_ROBOT_PREVIEW_BROWSER_REVISION',
  sha256: 'TBOT_ROBOT_PREVIEW_BROWSER_SHA256',
  bytes: 'TBOT_ROBOT_PREVIEW_BROWSER_BYTES'
};
const ENGINE = 'chromium-headless-shell';
const METADATA_PATH = join(dirname(fileURLToPath(import.meta.url)), '../node_modules/playwright-core/browsers.json');

function executableSuffix(platform, arch) {
  const suffix = {
    'darwin-arm64': 'chrome-headless-shell-mac-arm64/chrome-headless-shell',
    'darwin-x64': 'chrome-headless-shell-mac-x64/chrome-headless-shell',
    'linux-arm64': 'chrome-linux/headless_shell',
    'linux-x64': 'chrome-headless-shell-linux64/chrome-headless-shell'
  }[`${platform}-${arch}`];
  if (!suffix) throw new Error(`Unsupported robot preview browser platform: ${platform}-${arch}`);
  return suffix;
}

function descriptorFromEnvironment(environment) {
  const descriptor = Object.fromEntries(Object.entries(ENVIRONMENT_KEYS).map(([key, name]) => [key, environment[name]]));
  if (!isAbsolute(descriptor.path || '')) throw new Error('Candidate-bound robot preview browser path is required');
  if (descriptor.engine !== ENGINE || !/^[1-9][0-9]*$/.test(descriptor.revision || '')) {
    throw new Error('Candidate-bound robot preview browser engine/revision is invalid');
  }
  if (!/^[0-9a-f]{64}$/.test(descriptor.sha256 || '') || !/^[1-9][0-9]*$/.test(descriptor.bytes || '')) {
    throw new Error('Candidate-bound robot preview browser identity is invalid');
  }
  const bytes = Number(descriptor.bytes);
  if (!Number.isSafeInteger(bytes) || bytes > 512 * 1024 * 1024) {
    throw new Error('Candidate-bound robot preview browser size is invalid');
  }
  return { ...descriptor, bytes };
}

async function secureExecutableIdentity(path) {
  let handle;
  try {
    if (await realpath(path) !== path) throw new Error('not canonical');
    handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n || before.size <= 0n || (before.mode & 0o111n) === 0n) {
      throw new Error('not executable');
    }
    const hash = createHash('sha256');
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let offset = 0n;
    while (offset < before.size) {
      const length = Number(before.size - offset > BigInt(buffer.length) ? BigInt(buffer.length) : before.size - offset);
      const { bytesRead } = await handle.read(buffer, 0, length, Number(offset));
      if (!bytesRead) throw new Error('changed while reading');
      hash.update(buffer.subarray(0, bytesRead));
      offset += BigInt(bytesRead);
    }
    const after = await handle.stat({ bigint: true });
    const named = await lstat(path, { bigint: true });
    const identity = (stat) => [stat.dev, stat.ino, stat.mode, stat.nlink, stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
    if (!named.isFile() || identity(before) !== identity(after) || identity(before) !== identity(named)) {
      throw new Error('changed while reading');
    }
    return { sha256: hash.digest('hex'), bytes: Number(before.size) };
  } catch {
    throw new Error('Robot preview browser must be a canonical regular non-symlink executable');
  } finally {
    await handle?.close();
  }
}

export async function findPinnedRobotPreviewChromium({
  environment = process.env,
  metadataPath = METADATA_PATH,
  platform = process.platform,
  arch = process.arch
} = {}) {
  const descriptor = descriptorFromEnvironment(environment);
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8'));
  const browser = metadata.browsers?.find((entry) => entry.name === ENGINE);
  if (!browser || browser.revision !== descriptor.revision) {
    throw new Error('Candidate browser revision does not match installed Playwright metadata');
  }
  const expectedTail = join(`chromium_headless_shell-${browser.revision}`, executableSuffix(platform, arch));
  if (!descriptor.path.endsWith(`${sep}${expectedTail}`)) {
    throw new Error('Candidate browser path does not match installed Playwright platform metadata');
  }
  const observed = await secureExecutableIdentity(descriptor.path);
  if (observed.sha256 !== descriptor.sha256 || observed.bytes !== descriptor.bytes) {
    throw new Error('Candidate browser identity does not match the executable');
  }
  return descriptor.path;
}
