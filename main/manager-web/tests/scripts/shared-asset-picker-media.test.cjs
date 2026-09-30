const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium, webkit, expect } = require('@playwright/test');

const root = path.resolve(__dirname, '../..');
const identity = require('../fixtures/mjpeg/current-flyIn.json');
const media = fs.readFileSync(path.join(root, 'tests/fixtures/mjpeg/current-flyIn.mp4'));
const nativeMedia = fs.readFileSync(path.join(root, 'public/tvideo-demo/assets/scenes/deep-barn-farm-background-6s-480x320-hq.mp4'));
const distinctMedia = fs.readFileSync(path.join(root, 'public/tvideo-demo/assets/t54-layered/robot-teach.mp4'));
assert.equal(media.length, identity.bytes);
assert.equal(crypto.createHash('sha256').update(media).digest('hex'), identity.sha256);

// Mount the real Vue component and decoder; only the unrelated input widget is stubbed.
function componentModule(file) {
  const source = fs.readFileSync(file, 'utf8');
  if (!file.endsWith('.vue')) return source;
  const template = source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1];
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'const component =');
  const style = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
  return script + '\ncomponent.template=' + JSON.stringify(template) + ';\n'
    + 'const style=document.createElement("style");style.textContent=' + JSON.stringify(style)
    + ';document.head.append(style);export default component;';
}

for (const [name, engine, fallback] of [['Chromium', chromium, false], ['WebKit', webkit, false],
  ['Chromium fallback', chromium, true], ['WebKit fallback', webkit, true]]) {
  test(`${name}: picker bounds catalog previews and validates selected MJPEG bytes`, { timeout: 30000 }, async () => {
    const delayedResponses = [];
    const server = http.createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://localhost').pathname;
        let body, type = 'text/javascript';
        if (url === '/fixture.mp4') { body = media; type = 'video/mp4'; }
        else if (url === '/delayed.mp4') {
          response.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': media.length });
          response.write(media.subarray(0, 1024));
          delayedResponses.push(response);
          return;
        }
        else if (url === '/distinct.mp4') { body = distinctMedia; type = 'video/mp4'; }
        else if (url === '/thumbnail.svg') { body = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="green"/></svg>'; type = 'image/svg+xml'; }
        else if (url === '/native.mp4') {
          const range = request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
          const start = range ? Number(range[1]) : 0;
          const end = range && range[2] ? Math.min(Number(range[2]), nativeMedia.length - 1) : nativeMedia.length - 1;
          const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1 };
          if (range) headers['Content-Range'] = `bytes ${start}-${end}/${nativeMedia.length}`;
          response.writeHead(range ? 206 : 200, headers);
          response.write(nativeMedia.subarray(start, Math.min(end + 1, start + 1024)));
          setTimeout(() => response.end(nativeMedia.subarray(Math.min(end + 1, start + 1024), end + 1)), 150);
          return;
        }
        else if (url === '/vue.js') body = fs.readFileSync(path.join(root, 'node_modules/vue/dist/vue.js'));
        else if (url.startsWith('/component/')) {
          const name = url.slice('/component/'.length);
          assert.match(name, /^[A-Za-z0-9.-]+$/);
          body = componentModule(path.join(root, 'src/components/lesson', path.extname(name) ? name : name + '.js'));
        } else if (url === '/') {
          type = 'text/html';
          body = '<!doctype html><script src="/vue.js"></script><div id="app"></div>'
            + '<script type="module">import Picker from "/component/SharedAssetPicker.vue";'
            + 'Vue.prototype.$t=k=>k;Vue.component("el-input",{render:h=>h("input")});'
            + 'window.mountAsset=async (asset,assets=null)=>{if(window.host)window.host.$destroy();document.body.innerHTML="<div id=app></div>";'
            + 'window.host=new Vue({data:{asset,assets,selected:""},render(h){return h(Picker,{props:{assets:this.assets||[this.asset],selectedVersionId:this.selected,title:"Source preview"},on:{"select-version":id=>{this.selected=id}}})}}).$mount("#app");await Vue.nextTick();};window.ready=true;</script>';
        } else { response.writeHead(404).end(); return; }
        response.writeHead(200, { 'Content-Type': type, 'Content-Length': Buffer.byteLength(body) }).end(body);
      } catch (error) { response.writeHead(500).end(String(error)); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
      browser = await engine.launch({ headless: true });
      const page = await browser.newPage();
      page.setDefaultTimeout(10000);
      if (fallback) await page.addInitScript(() => { delete window.IntersectionObserver; });
      const failures = [], pageErrors = [], mediaRequests = [], nativeRequests = [];
      const activeNative = new Set();
      let nativeChangedAt = 0;
      let nativePhase = 'before-native';
      page.on('request', request => { if (new URL(request.url()).pathname === '/fixture.mp4') mediaRequests.push(request); });
      page.on('request', request => {
        if (new URL(request.url()).pathname === '/native.mp4') {
          nativeRequests.push(request); activeNative.add(request); nativeChangedAt = Date.now();
        }
      });
      const finishNative = request => { if (activeNative.delete(request)) nativeChangedAt = Date.now(); };
      page.on('requestfinished', finishNative);
      page.on('requestfailed', finishNative);
      const settleNative = () => expect.poll(() => ({ active: activeNative.size, quiet: Date.now() - nativeChangedAt >= 250 }),
        { message: 'current native requests finish and remain quiet before retirement', timeout: 10000 }).toEqual({ active: 0, quiet: true });
      page.on('requestfailed', request => {
        const url = new URL(request.url());
        failures.push({ path: url.pathname + url.search, resourceType: request.resourceType(),
          phase: nativePhase, error: request.failure()?.errorText });
      });
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.goto('http://127.0.0.1:' + server.address().port);
      await page.waitForFunction(() => window.ready);
      const asset = { versionId: 'original', assetKey: 'robot.flyIn', version: 1,
        url: '/fixture.mp4', mimeType: 'video/mp4', bytes: identity.bytes, sha256: identity.sha256,
        width: identity.metadata.width, height: identity.metadata.height, compatibilityMetadata: identity.metadata };
      await page.evaluate(asset => window.mountAsset(asset), asset);
      await page.waitForFunction(() => {
        const video = document.querySelector('video');
        const canvas = document.querySelector('canvas');
        return video?.error || video?.readyState >= 2 || (canvas && canvas.width > 0 && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0));
      }, null, { timeout: 10000 });
      const state = await page.evaluate(() => ({ videos: document.querySelectorAll('video').length,
        error: document.querySelector('video')?.error?.message || null, canvases: document.querySelectorAll('canvas').length }));
      assert.equal(state.error, null, 'MJPEG must not enter an unsupported native decoder');
      assert.equal(state.videos, 0);
      assert.equal(state.canvases, 1);
      assert.deepEqual(failures, []);
      assert.deepEqual(pageErrors, []);
      await page.evaluate(async () => {
        window.host.$children[0].query = 'robot';
        await Vue.nextTick();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(mediaRequests.length, 1, 'retained tiles do not reload when the filter rerenders');
      await page.evaluate(async () => {
        window.host.asset = JSON.parse(JSON.stringify(window.host.asset));
        await Vue.nextTick();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(mediaRequests.length, 1, 'identical catalog readback must retain the verified decoder');
      const delayedRequest = page.waitForRequest('**/delayed.mp4');
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, url: '/delayed.mp4' });
      await delayedRequest;
      await page.evaluate(async () => {
        document.querySelector('.asset-picker').style.marginTop = '2000px';
        window.dispatchEvent(new Event('resize'));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      // Response headers already arrived; finishing the accepted body must not depend on visibility.
      for (const response of delayedResponses) response.end(media.subarray(1024));
      await page.waitForLoadState('networkidle');
      assert.deepEqual(failures, [], 'hiding a pending visible thumbnail must not cancel its accepted fetch');
      await page.evaluate(() => {
        document.querySelector('.asset-picker').style.marginTop = '';
        window.dispatchEvent(new Event('resize'));
      });
      await page.waitForFunction(() => {
        const canvas = document.querySelector('canvas');
        return canvas && canvas.width > 0 && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0);
      });
      assert.equal(delayedResponses.length, 1, 'returning to a completed thumbnail reuses its verified first frame');
      const delayedStart = delayedResponses.length;
      await page.evaluate(async asset => {
        await window.mountAsset(asset);
        window.host.assets = Array.from({ length: 169 }, (_, index) => ({ ...asset,
          versionId: 'pending-' + index, url: '/delayed.mp4?job=' + index }));
        await Vue.nextTick();
      }, asset);
      await expect.poll(() => delayedResponses.length).toBe(delayedStart + 2);
      await page.evaluate(async () => {
        document.querySelector('.asset-picker__grid').scrollLeft = 100000;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(delayedResponses.length, delayedStart + 2, 'rapid scroll cannot expand admitted unique downloads');
      const beforeReplacement = mediaRequests.length;
      await page.evaluate(asset => window.mountAsset(asset), asset);
      await page.waitForFunction(() => document.querySelector('canvas'));
      assert.equal(mediaRequests.length, beforeReplacement, 'new picker waits for globally bounded accepted work');
      for (const response of delayedResponses.slice(delayedStart)) response.end(media.subarray(1024));
      await page.waitForFunction(() => document.querySelector('canvas')?.width > 0);
      await page.waitForLoadState('networkidle');
      assert.equal(delayedResponses.length, delayedStart + 2, 'stale queued work never starts after replacement');
      assert.deepEqual(failures, [], 'retiring the picker during accepted downloads also preserves strict request settlement');
      await page.evaluate(asset => window.mountAsset(asset), asset);
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => {
        window.host.asset = { ...window.host.asset, sha256: '0'.repeat(64) };
      });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /SHA-256 mismatch/,
        'changed media identity must retire the old decoder and revalidate bytes');
      const beforeRetry = mediaRequests.length;
      await page.getByRole('button', { name: 'Retry preview' }).click();
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /SHA-256 mismatch/);
      assert.equal(mediaRequests.length, beforeRetry + 1, 'explicit retry revalidates once without suppressing integrity failure');
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, sha256: '0'.repeat(64) });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /SHA-256 mismatch/);
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, bytes: asset.bytes + 1 });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /length mismatch/);
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, width: asset.width + 1 });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /metadata mismatch/);
      await page.setViewportSize({ width: 6000, height: 600 });
      await page.evaluate(async asset => {
        await window.mountAsset(asset);
        window.host.assets = Array.from({ length: 26 }, (_, index) => ({ ...asset,
          versionId: 'failed-' + index, url: '/fixture.mp4?failed=' + index }));
        await Vue.nextTick();
      }, { ...asset, sha256: '0'.repeat(64) });
      await expect(page.getByRole('alert')).toHaveCount(26);
      await page.waitForLoadState('networkidle');
      const beforeEvictedRetry = mediaRequests.length;
      await page.getByRole('button', { name: 'Retry preview' }).first().click();
      await expect.poll(() => mediaRequests.length).toBe(beforeEvictedRetry + 1);
      await expect(page.getByRole('alert')).toHaveCount(26);

      // Every version stays selectable, but only the visible horizontal window decodes.
      await page.setViewportSize({ width: 600, height: 600 });
      await page.evaluate(async asset => {
        await window.mountAsset(asset);
        window.host.assets = [asset, { ...asset, versionId: 'edge' }];
        await Vue.nextTick();
        const grid = document.querySelector('.asset-picker__grid');
        const second = document.querySelectorAll('.asset-tile')[1];
        grid.style.width = (second.getBoundingClientRect().left - grid.getBoundingClientRect().left) + 'px';
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, asset);
      await page.evaluate(() => { document.querySelector('.asset-picker__grid').scrollLeft = 50; });
      await page.waitForFunction(() => document.querySelector('.asset-tile:last-child canvas'), null, { timeout: 3000 });
      await page.waitForFunction(() => [...document.querySelectorAll('canvas')].every(canvas => canvas.width > 0));
      const requestStart = mediaRequests.length;
      const catalogFailureStart = failures.length;
      await page.evaluate(async asset => {
        await window.mountAsset(asset);
        window.host.assets = Array.from({ length: 169 }, (_, index) => ({ ...asset,
          versionId: 'version-' + index, version: index + 1,
          assetKey: index === 168 ? 'robot.final' : 'robot.flyIn' }));
        await Vue.nextTick();
      }, asset);
      await page.waitForFunction(() => {
        const canvases = [...document.querySelectorAll('canvas')];
        return canvases.length > 0 && canvases.every(canvas => canvas.width > 0);
      });
      assert.equal(await page.locator('.asset-tile').count(), 169, 'all published versions remain available');
      assert.ok(mediaRequests.length - requestStart <= 6, '169 versions must not trigger 169 media downloads');
      assert.ok(await page.locator('canvas').count() <= 6, 'live decoders are bounded by the visible window');
      const retainedRequestCount = mediaRequests.length;
      await page.evaluate(async () => {
        window.host.assets = JSON.parse(JSON.stringify(window.host.assets));
        window.host.$children[0].query = 'robot';
        await Vue.nextTick();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(mediaRequests.length, retainedRequestCount, 'readback and matching filter reuse visible previews');
      await page.evaluate(() => {
        window.retiredCanvases = [...document.querySelectorAll('canvas')];
        document.querySelector('.asset-picker__grid').scrollLeft = 100000;
      });
      await page.waitForFunction(() => {
        const last = document.querySelector('.asset-tile:last-child canvas');
        return last && window.retiredCanvases.every(canvas => !canvas.isConnected)
          && [...document.querySelectorAll('canvas')].every(canvas => canvas.width > 0);
      });
      await page.locator('.asset-tile__select').last().click();
      assert.equal(await page.evaluate(() => window.host.selected), 'version-168');
      // Keyboard focus must bring a previously offscreen choice and its preview into view.
      await page.locator('.asset-tile__select').first().focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelector('.asset-tile:first-child canvas')
        && [...document.querySelectorAll('canvas')].every(canvas => canvas.width > 0));
      assert.equal(await page.evaluate(() => window.host.selected), 'version-0');
      await page.evaluate(() => { window.host.$children[0].query = 'final'; });
      await page.waitForFunction(() => document.querySelectorAll('.asset-tile').length === 1 && document.querySelector('canvas'));
      await page.locator('.asset-tile__select').click();
      assert.equal(await page.evaluate(() => window.host.selected), 'version-168');
      await page.waitForFunction(() => [...document.querySelectorAll('canvas')].every(canvas => canvas.width > 0));
      assert.equal(failures.length, catalogFailureStart, 'settled catalog navigation has no cancelled or failed requests');
      const failuresAfterSettling = failures.length;
      await page.evaluate(() => {
        window.finalRegistry = window.host.$children[0].thumbnailRegistry;
        window.host.$destroy();
      });
      assert.equal(await page.evaluate(() => window.finalRegistry.disposed && window.finalRegistry.entries.size === 0 && window.finalRegistry.cachedBytes === 0), true);
      assert.equal(failures.length, failuresAfterSettling, 'settled disposal must not cancel network work');
      assert.deepEqual(pageErrors, []);
      const distinctAsset = { ...asset, url: '/distinct.mp4', bytes: distinctMedia.length,
        sha256: crypto.createHash('sha256').update(distinctMedia).digest('hex'),
        compatibilityMetadata: { ...identity.metadata, fps: 10, frameCount: 30, durationMs: 3000 } };
      await page.evaluate(async ({ asset, distinctAsset }) => {
        await window.mountAsset(asset);
        window.host.assets = Array.from({ length: 169 }, (_, index) => ({ ...(index % 2 ? distinctAsset : asset),
          versionId: 'mixed-' + index, version: index + 1 }));
        await Vue.nextTick();
      }, { asset, distinctAsset });
      await page.waitForFunction(() => {
        const canvases = [...document.querySelectorAll('canvas')];
        return canvases.length >= 2 && canvases.every(canvas => canvas.width > 0);
      });
      assert.ok(await page.locator('canvas').count() <= 6, 'distinct verified media also obey the visibility bound');
      assert.equal(await page.evaluate(() => new Set(window.host.$children[0].$children.filter(child => child.$options.name === 'PickerMjpegThumbnail').map(child => child.src)).size), 2);
      assert.equal(await page.getByRole('alert').count(), 0);
      const nativeFailureStart = failures.length;
      nativePhase = 'mount-native';
      await page.evaluate(async asset => {
        await window.mountAsset(asset, Array.from({ length: 169 }, (_, index) => ({ ...asset,
          versionId: 'native-' + index, version: index + 1, url: '/native.mp4?version=' + index })));
      }, { ...asset, url: '/native.mp4', compatibilityMetadata: { codec: 'h264' } });
      await page.waitForFunction(() => {
        const videos = [...document.querySelectorAll('video')];
        return videos.length > 0 && videos.every(video => video.readyState >= 1 && video.videoWidth > 0 && Number.isFinite(video.duration) && !video.error);
      }).catch(async error => { throw new Error(error.message + ' ' + JSON.stringify(await page.locator('video').evaluateAll(videos => videos.map(video => ({ readyState: video.readyState, error: video.error?.message, src: video.src }))))); });
      // WebKit honors metadata-only preload. Explicit playback verifies decoded H264 frames.
      nativePhase = 'play-initial';
      await page.locator('video').evaluateAll(videos => Promise.all(videos.map(async video => { await video.play(); video.pause(); })));
      await page.waitForFunction(() => [...document.querySelectorAll('video')].every(video => video.readyState >= 2 && !video.error));
      await settleNative();
      assert.equal(await page.locator('.asset-tile').count(), 169);
      assert.ok(await page.locator('video').count() <= 6, 'native H264 has the same visible-window bound');
      assert.ok(new Set(nativeRequests.map(request => request.url())).size <= 6, 'offscreen native sources do not preload');
      assert.ok(nativeRequests.every(request => new URL(request.url()).searchParams.has('version')), 'fixture mounts the intended catalog atomically without a temporary singleton video');
      nativePhase = 'scroll-native';
      await page.evaluate(() => {
        window.retiredVideos = [...document.querySelectorAll('video')];
        document.querySelector('.asset-picker__grid').scrollLeft = 100000;
      });
      await page.waitForFunction(() => document.querySelector('.asset-tile:last-child video')?.readyState >= 1);
      nativePhase = 'play-scrolled';
      await page.locator('video').evaluateAll(videos => Promise.all(videos.map(async video => { await video.play(); video.pause(); })));
      await page.waitForFunction(() => [...document.querySelectorAll('video')].every(video => video.readyState >= 2 && !video.error));
      await settleNative();
      assert.equal(await page.evaluate(() => window.retiredVideos.every(video => !video.hasAttribute('src') && video.paused)), true,
        'offscreen native videos release their media sources');
      nativePhase = 'dispose-native';
      await page.evaluate(() => {
        window.finalVideos = [...document.querySelectorAll('video')];
        window.host.$destroy();
      });
      assert.equal(await page.evaluate(() => window.finalVideos.every(video => !video.hasAttribute('src') && video.paused)), true);
      await settleNative();
      assert.deepEqual(failures.slice(nativeFailureStart), [], 'settled native navigation and disposal have no request failures');
      assert.deepEqual(pageErrors, []);
      await page.evaluate(async asset => {
        await window.mountAsset(asset);
        window.host.assets = Array.from({ length: 169 }, (_, index) => ({ ...asset,
          versionId: 'image-' + index, version: index + 1 }));
        await Vue.nextTick();
      }, { ...asset, url: '/thumbnail.svg', mimeType: 'image/svg+xml', compatibilityMetadata: {} });
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll('img')];
        return images.length > 0 && images.every(image => image.complete && image.naturalWidth > 0);
      });
      assert.equal(await page.locator('.asset-tile').count(), 169);
      assert.ok(await page.locator('img').count() <= 6);
      await page.locator('.asset-tile__select').last().focus();
      await page.waitForFunction(() => document.querySelector('.asset-tile:last-child img')?.naturalWidth > 0);
      await page.keyboard.press('Enter');
      assert.equal(await page.evaluate(() => window.host.selected), 'image-168');
      await page.waitForLoadState('networkidle');
      assert.deepEqual(failures.slice(nativeFailureStart), []);
      assert.deepEqual(pageErrors, []);
    } finally {
      if (browser) await browser.close();
      await new Promise(resolve => server.close(resolve));
    }
  });
}
