import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/robot-preview-projection.js', root), 'utf8');
const projectionApi = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const update = process.argv.includes('--update');
const chromeCandidates = [
  process.env.CHROME_BIN,
  join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'),
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
].filter(Boolean);
const chrome = chromeCandidates.find(existsSync);
assert.ok(chrome, 'Chromium is required for robot preview screenshot verification');

const escapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const temp = await mkdtemp(join(tmpdir(), 'tbot-preview-'));
await mkdir(new URL('tests/screenshots/', root), { recursive: true });

try {
  for (const minutes of [3, 5, 8]) {
    const fixture = JSON.parse(await readFile(new URL(`tests/fixtures/robot-preview-${minutes}m.json`, root), 'utf8'));
    const preview = projectionApi.projectEspTftPreview(fixture.manifest, fixture.stepIndex, fixture.path);
    const layer = (id) => preview.layers.find((item) => item.id === id);
    const word = layer('wordPill');
    const prompt = layer('prompt');
    const progress = layer('progress');
    const dots = Array.from({ length: progress.total }, (_, index) => `<i class="${index < progress.active ? 'on' : ''}"></i>`).join('');
    const html = `<!doctype html><meta charset="utf-8"><style>
      *{box-sizing:border-box}html,body{margin:0;width:480px;height:320px;overflow:hidden}body{font-family:"Trebuchet MS",sans-serif;background:linear-gradient(160deg,#9ed7da,#d7e894 58%,#6c9c53)}
      .object{position:absolute;left:129px;top:53px;width:221px;height:160px;border-radius:48% 52% 46% 54%;background:radial-gradient(circle at 40% 34%,#fff7b5 0 12%,#eb653d 13% 48%,#9f2e22 49%);filter:drop-shadow(0 8px 4px #25451f55)}
      .robot{position:absolute;left:20px;top:158px;width:154px;height:109px;border-radius:54px 54px 28px 28px;background:linear-gradient(#edf4ec 0 62%,#78a994 63%);border:5px solid #1b3028}.robot:before{content:"•  •";position:absolute;left:33px;top:17px;width:78px;padding:4px 0;border-radius:18px;background:#14241e;color:#b9ec45;font-size:24px;text-align:center}
      .word{position:absolute;left:166px;top:26px;width:148px;height:42px;display:flex;align-items:center;justify-content:center;border:3px solid #16251c;border-radius:24px;background:#fff8dc;box-shadow:0 4px 0 #16251c;font-size:25px;font-weight:900}
      .dots{position:absolute;left:188px;top:218px;width:104px;height:12px;display:flex;justify-content:center;gap:6px}.dots i{width:10px;height:10px;border:2px solid white;border-radius:50%;background:#0006}.dots i.on{background:#b9ec45}
      .prompt{position:absolute;left:19px;top:238px;width:442px;height:77px;display:flex;align-items:center;justify-content:center;padding:8px 24px;border-radius:19px 19px 0 0;background:#0c1811e8;color:white;font-size:20px;font-weight:800;text-align:center}
    </style><div class="object"></div><div class="robot"></div><div class="word">${escapeHtml(word.text)}</div><div class="dots">${dots}</div><div class="prompt">${escapeHtml(prompt.text)}</div>`;
    const htmlPath = join(temp, `${minutes}m.html`);
    const actualPath = join(temp, `${minutes}m.png`);
    const baselineUrl = new URL(`tests/screenshots/robot-preview-${minutes}m.png`, root);
    await writeFile(htmlPath, html);
    const result = spawnSync(chrome, ['--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=480,320', `--screenshot=${actualPath}`, pathToFileURL(htmlPath).href], { encoding: 'utf8', timeout: 20000 });
    assert.equal(result.status, 0, `Chromium screenshot failed: ${result.stderr || result.stdout}`);
    const actual = await readFile(actualPath);
    assert.equal(actual.subarray(1, 4).toString(), 'PNG');
    if (update) await writeFile(baselineUrl, actual);
    const baseline = await readFile(baselineUrl);
    assert.deepEqual(actual, baseline, `${minutes}-minute PNG baseline drifted; inspect and run with --update intentionally`);
  }
  console.log('robot lesson preview: actual PNG screenshot baselines 3/5/8 PASS');
} finally {
  await rm(temp, { recursive: true, force: true });
}
