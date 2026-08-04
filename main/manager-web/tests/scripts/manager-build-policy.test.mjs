import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const config = await readFile(new URL('../../vue.config.js', import.meta.url), 'utf8');

test('production minimizer preserves the configured splitChunks policy', () => {
  assert.doesNotMatch(config, /config\.optimization\s*=\s*\{/);
  assert.match(config, /config\.optimization\.minimizer\s*=/);
});

test('demo video archives remain explicitly on-demand', () => {
  assert.match(config, /\/\^tvideo-demo\\\/\.\*\\\.\(\?:mp4\|mov\|webm\|zip\)\$\//);
});

test('webpack performance hints enforce admin-specific production budgets', () => {
  assert.match(config, /maxAssetSize:\s*1024\s*\*\s*1024/);
  assert.match(config, /maxEntrypointSize:\s*2\s*\*\s*1024\s*\*\s*1024/);
  assert.match(config, /assetFilter:\s*filename\s*=>/);
});
