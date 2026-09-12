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

module.exports = { gotoAppRoute, stabilizeStageMedia };
