const { expect } = require('@playwright/test');

async function stagePlaybackState(stage) {
  return stage.evaluate(element => {
    const layer = element.querySelector('.layer-robotOverlay');
    const player = layer?.__vue__?._mjpeg;
    if (player) return { ...player.state(), frameIndex: player.index };
    const video = layer?.querySelector('video');
    return { ready: Boolean(video && video.readyState >= 2),
      pending: false, seeking: Boolean(video?.seeking), currentTimeSec: video?.currentTime || 0 };
  });
}

async function stabilizeStageMedia(stage, currentTimeSec = 0.4) {
  const seek = stage.page().getByLabel('Seek cinematic', { exact: true });
  if (await seek.count()) {
    const pause = stage.page().getByRole('button', { name: /pause cinematic/i });
    if (await pause.count()) await pause.click();
    await expect.poll(async () => (await stagePlaybackState(stage)).ready).toBe(true);
    await seek.evaluate((input, seconds) => {
      input.value = String(seconds * 1000);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, currentTimeSec);
    await expect.poll(async () => {
      const state = await stagePlaybackState(stage);
      return state.ready && !state.pending && !state.seeking
        && Math.abs(state.currentTimeSec - currentTimeSec) < 0.01;
    }).toBe(true);
    return;
  }
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

module.exports = { gotoAppRoute, stabilizeStageMedia, stagePlaybackState };
