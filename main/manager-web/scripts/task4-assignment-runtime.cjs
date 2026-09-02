const { lstatSync, realpathSync } = require('node:fs');
const { basename, isAbsolute, resolve, sep } = require('node:path');

const OWNER_KEY = 'TASK4_ASSIGNMENT_CAPSULE_ROOT';
const RUNTIME_KEY = 'TASK4_ASSIGNMENT_RUNTIME_ROOT';
const OWNER_PREFIX = 'course-mode-assignment-runtime-';

function requireAbsolutePath(environment, key) {
  const value = environment[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${key} must be a nonempty absolute path`);
  }
  if (value.includes('\0')) throw new Error(`${key} must not contain NUL bytes`);
  if (!isAbsolute(value)) throw new Error(`${key} must be an absolute path`);
  return resolve(value);
}

function inspectDirectory(pathValue, label) {
  const metadata = lstatSync(pathValue);
  if (metadata.isSymbolicLink()) throw new Error(`${label} must not be a symlink`);
  if (!metadata.isDirectory()) throw new Error(`${label} must be a directory`);
  return metadata;
}

function pathsOverlap(left, right) {
  return left === right || left.startsWith(`${right}${sep}`) || right.startsWith(`${left}${sep}`);
}

function validateAssignmentRuntimeCapsule(
  environment,
  protectedRoots,
  effectiveUid = process.getuid(),
) {
  const capsuleRoot = requireAbsolutePath(environment, OWNER_KEY);
  const runtimeRoot = requireAbsolutePath(environment, RUNTIME_KEY);
  if (runtimeRoot !== resolve(capsuleRoot, 'runtime')) {
    throw new Error(`${RUNTIME_KEY} must be the exact direct runtime child of ${OWNER_KEY}`);
  }

  const capsuleMetadata = inspectDirectory(capsuleRoot, 'assignment capsule owner');
  const runtimeMetadata = inspectDirectory(runtimeRoot, 'assignment runtime');
  const realOwner = realpathSync(capsuleRoot);
  const realRuntime = realpathSync(runtimeRoot);

  if (realRuntime !== resolve(realOwner, 'runtime')) {
    throw new Error('assignment runtime must resolve to the direct runtime child of its owner');
  }
  if (!basename(realOwner).startsWith(OWNER_PREFIX)) {
    throw new Error(`assignment capsule owner must start with ${OWNER_PREFIX}`);
  }
  if ((capsuleMetadata.mode & 0o777) !== 0o700) {
    throw new Error('assignment capsule owner mode must be 0700');
  }
  if ((runtimeMetadata.mode & 0o777) !== 0o700) {
    throw new Error('assignment runtime mode must be 0700');
  }
  const ownershipMismatches = [];
  if (capsuleMetadata.uid !== effectiveUid) ownershipMismatches.push('owner UID');
  if (runtimeMetadata.uid !== effectiveUid) ownershipMismatches.push('runtime UID');
  if (ownershipMismatches.length > 0) {
    throw new Error(`assignment capsule ${ownershipMismatches.join(' and ')} must match the effective UID`);
  }

  for (const protectedRoot of protectedRoots) {
    const realProtectedRoot = realpathSync(protectedRoot);
    if (pathsOverlap(realOwner, realProtectedRoot) || pathsOverlap(realRuntime, realProtectedRoot)) {
      throw new Error('assignment runtime capsule must not overlap a protected root');
    }
  }

  return Object.freeze({ capsuleRoot: realOwner, runtimeRoot: realRuntime });
}

module.exports = { validateAssignmentRuntimeCapsule };
