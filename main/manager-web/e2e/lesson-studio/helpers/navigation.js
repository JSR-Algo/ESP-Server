const { resolve } = require('node:path');

const cinematicAsset = (path) => resolve(__dirname, '../../../public/tvideo-demo/assets', path);

async function installCinematicTestRoutes(page) {
  await page.route('**/tvideo-demo/index.html?embed=1', (route) => route.fulfill({
    status: 200,
    contentType: 'text/html',
    body: '<!doctype html><script>parent.postMessage({type:"tvideo-ready"},"*")</script>',
  }));
  await page.route('https://fonts.googleapis.com/**', (route) => route.fulfill({
    status: 200,
    contentType: 'text/css',
    body: '',
  }));
  await page.route('**/tvideo-demo/assets/t54-layered/background-farm.jpg', (route) => route.fulfill({
    status: 200,
    contentType: 'image/jpeg',
    headers: { 'access-control-allow-origin': '*' },
    path: cinematicAsset('t54-layered/background-farm.jpg'),
  }));
  await page.route('**/tvideo-demo/assets/objects/barn.png', (route) => route.fulfill({
    status: 200,
    contentType: 'image/png',
    headers: { 'access-control-allow-origin': '*' },
    path: cinematicAsset('objects/barn.png'),
  }));
  await page.route('**/tvideo-demo/assets/t54-layered/robot-teach.mp4', (route) => route.fulfill({
    status: 200,
    contentType: 'video/mp4',
    headers: { 'access-control-allow-origin': '*' },
    path: cinematicAsset('t54-layered/robot-teach.mp4'),
  }));
}

async function stabilizeStageMedia(stage, currentTimeSec = 0.4) {
  await stage.locator('video').evaluateAll(async (videos, targetTime) => {
    await Promise.all(videos.map(async (video) => {
      video.pause();
      if (video.readyState < 1) {
        await new Promise((resolveReady) => video.addEventListener('loadedmetadata', resolveReady, { once: true }));
      }
      if (Math.abs(video.currentTime - targetTime) > 0.01) {
        await new Promise((resolveSeek) => {
          video.addEventListener('seeked', resolveSeek, { once: true });
          video.currentTime = Math.min(targetTime, Math.max(0, video.duration - 0.05));
        });
      }
      video.pause();
    }));
  }, currentTimeSec);
  await stage.page().waitForTimeout(100);
}

async function gotoAppRoute(page, hash) {
  await page.evaluate((nextHash) => { window.location.hash = nextHash; }, hash);
  await page.waitForURL((url) => url.hash === hash);
}

module.exports = { gotoAppRoute, installCinematicTestRoutes, stabilizeStageMedia };
