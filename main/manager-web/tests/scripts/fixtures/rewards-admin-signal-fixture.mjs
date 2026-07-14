import { access, mkdir, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import {
  createProcessLifecycle,
  executeWithSignalFinalization,
  finalizeArtifactPrivacy,
  scanArtifactPrivacy,
} from '../../../scripts/_lib/rewards-admin-browser-lifecycle.mjs';

const root = process.argv[2];
const artifacts = resolve(root, 'artifacts');
const raw = resolve(root, 'raw');
const serviceReady = resolve(root, 'service-ready');
const secret = 'fixture-secret';
const lifecycle = createProcessLifecycle({
  cleanupContainer: async () => undefined,
  cleanupTimeoutMs: 1_000,
});

const outcome = await executeWithSignalFinalization({
  processTarget: process,
  abortWorkflow: (signal) => lifecycle.abort(signal),
  escalateAbort: () => lifecycle.forceKill(),
  runWorkflow: async () => {
    await mkdir(artifacts, { recursive: true });
    await mkdir(raw, { recursive: true });
    await writeFile(resolve(artifacts, 'last-state.png'), secret);
    await writeFile(resolve(artifacts, 'trace.zip'), secret);
    await writeFile(resolve(artifacts, 'summary.json'), JSON.stringify({ safe: true }));
    await writeFile(resolve(raw, 'trace.zip'), secret);
    const service = lifecycle.spawnTracked(process.execPath, [
      '-e',
      "const fs = require('node:fs'); process.on('SIGTERM', () => setTimeout(() => fs.writeFileSync(process.argv[2], 'fixture-secret'), 50)); fs.writeFileSync(process.argv[1], 'ready'); setInterval(() => {}, 1000)",
      serviceReady,
      resolve(artifacts, 'late-state.png'),
    ], { stdio: 'ignore' });
    await writeFile(resolve(root, 'service.pid'), String(service.pid));
    while (true) {
      try {
        await access(serviceReady);
        break;
      } catch (error) {
        if (error?.code !== 'ENOENT') throw error;
        await new Promise((resolveWait) => setTimeout(resolveWait, 10));
      }
    }
    await writeFile(resolve(root, 'ready'), 'ready');
    await new Promise(() => {});
  },
  finalizeArtifacts: async ({ workflowSucceeded }) => {
    await finalizeArtifactPrivacy(artifacts, {
      workflowSucceeded,
      scanPrivacy: async () => {
        await writeFile(resolve(root, 'finalized'), 'finalized');
        if (process.env.SIGNAL_FIXTURE_PRIVACY_FAILURE === '1') {
          throw new Error(`privacy scanner failed ${secret}`);
        }
        return scanArtifactPrivacy(artifacts, { forbiddenValues: [secret] });
      },
    });
  },
  cleanup: () => lifecycle.cleanup(),
  cleanupRawArtifacts: () => rm(raw, { recursive: true, force: true }),
  forbiddenValues: [secret],
});

if (outcome.error) process.stderr.write(`${outcome.error.message}\n`);
