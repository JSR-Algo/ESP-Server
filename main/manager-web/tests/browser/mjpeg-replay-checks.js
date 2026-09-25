// Runs in a mounted fixture using the actual preview, controls and image decoder.
window.verifyResponsiveStage = async function verifyResponsiveStage(manifest, mount = true) {
  const rows = [];
  if (mount) await window.mountFixture(manifest, 314);
  const root = window.preview.$el;
  for (const width of [314, 240, 360, 600, 314]) {
    root.style.width = width + 'px';
    const stage = root.querySelector('[data-testid="esp-tft-stage"]');
    const shell = stage.parentElement;
    let geometry;
    for (let attempt = 0; attempt < 100; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 10));
      const bounds = stage.getBoundingClientRect();
      const outer = shell.getBoundingClientRect();
      const css = getComputedStyle(shell);
      const available = shell.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
      geometry = { width: bounds.width, height: bounds.height, available, left: bounds.left, right: bounds.right,
        shellLeft: outer.left + parseFloat(css.paddingLeft), shellRight: outer.right - parseFloat(css.paddingRight),
        top: bounds.top, bottom: bounds.bottom, shellTop: outer.top, shellBottom: outer.bottom,
        clientWidth: stage.clientWidth, clientHeight: stage.clientHeight, scrollLeft: shell.scrollLeft,
        controls: [...root.querySelectorAll('button, input')].filter(el => el.getClientRects().length).map(el => {
          const rect = el.getBoundingClientRect();
          return { text: el.textContent || el.getAttribute('aria-label'), left: rect.left, right: rect.right };
        }), rootLeft: root.getBoundingClientRect().left, rootRight: root.getBoundingClientRect().right };
      if (Math.abs(bounds.width - Math.min(480, available)) < 1 && bounds.left >= geometry.shellLeft - 0.5
        && bounds.right <= geometry.shellRight + 0.5 && stage.clientWidth === 480 && stage.clientHeight === 320) break;
    }
    rows.push({ action: 'responsive-stage-' + width, ...geometry,
      pass: Math.abs(geometry.width - Math.min(480, geometry.available)) < 1
        && Math.abs(geometry.height - geometry.width * 320 / 480) < 1
        && geometry.left >= geometry.shellLeft - 0.5 && geometry.right <= geometry.shellRight + 0.5
        && geometry.top >= geometry.shellTop && geometry.bottom <= geometry.shellBottom
        && geometry.controls.every(rect => rect.left >= geometry.rootLeft && rect.right <= geometry.rootRight)
        && geometry.clientWidth === 480 && geometry.clientHeight === 320 && geometry.scrollLeft === 0 });
  }
  return rows;
};

window.verifyMjpegReplay = async function verifyMjpegReplay(manifest, replacement) {
  const rows = [];
  const waitFor = async (predicate, label) => {
    for (let i = 0; i < 300; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error('Timed out: ' + label);
  };
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  for (const action of ['play-after-completion', 'seek-then-play', 'pause-during-seek',
    'repeated-play', 'source-replacement', 'destroy-during-seek', 'paused-seek']) {
    let release, player;
    const row = { action };
    try {
      await window.mountFixture(manifest);
      const parent = window.preview;
      await waitFor(() => parent.cinematicMediaReady(), 'first frame');
      const child = parent.cinematicLayerById('robotOverlay');
      player = child._mjpeg;
      const button = () => parent.$el.querySelector('.cinematic-controls .play-btn');
      const seek = ms => {
        const input = parent.$el.querySelector('input[aria-label="Seek cinematic"]');
        input.value = ms; input.dispatchEvent(new Event('input', { bubbles: true }));
      };
      seek(parent.cinematicDurationMs - 100);
      await Vue.nextTick(); await player.settled();
      button().click();
      await waitFor(() => player.ended && !parent.cinematicPlaying, 'natural once completion');
      const canvas = child.$refs.canvas;
      const finalCanvas = canvas.toDataURL();
      row.completed = { ...player.state(), clockMs: parent.cinematicClockMs };
      let entered = false, decodeCalls = 0;
      const decode = player.decode;
      const gate = new Promise(resolve => { release = resolve; });
      player.decode = async blob => { entered = true; decodeCalls++; await gate; return decode(blob); };
      if (action === 'seek-then-play' || action === 'paused-seek') seek(1000);
      if (action !== 'paused-seek') button().click();
      await Vue.nextTick();
      await waitFor(() => entered, 'asynchronous replay seek');
      row.pending = { ...player.state(), playing: player.playing, clockMs: parent.cinematicClockMs };
      if (action === 'pause-during-seek') button().click();
      if (action === 'repeated-play') { child.syncPlayback(); child.syncPlayback(); }
      if (action === 'source-replacement') {
        window.host.manifest = replacement;
        await Vue.nextTick();
      }
      if (action === 'destroy-during-seek') window.host.$destroy();
      await Vue.nextTick();
      release(); await player.settled();
      if (action === 'source-replacement') await waitFor(() => window.preview.cinematicMediaReady(), 'replacement ready');
      const settledTime = player.time;
      await delay(350);
      const advances = ['play-after-completion', 'seek-then-play', 'repeated-play'].includes(action);
      row.after = { ...player.state(), playing: player.playing,
        clockMs: parent.cinematicClockMs, disposed: player.disposed, decodeCalls };
      if (advances) {
        row.pass = player.playing && player.time > settledTime + 0.15
          && parent.cinematicClockMs > settledTime * 1000 + 150;
      } else if (action === 'source-replacement' || action === 'destroy-during-seek') {
        row.pass = player.disposed && !player.playing && player.bytes === null
          && canvas.toDataURL() === finalCanvas;
        if (action === 'source-replacement') {
          const next = window.preview.cinematicLayerById('robotOverlay')._mjpeg;
          row.replacement = { ...next.state(), playing: next.playing };
          row.pass = row.pass && next !== player && !next.playing && next.time === 0;
        }
      } else row.pass = !player.playing && player.time === settledTime && !parent.cinematicPlaying;
    } catch (error) { row.pass = false; row.error = String(error); }
    finally {
      if (release) release();
      if (player) await player.dispose();
      if (window.host) window.host.$destroy();
    }
    rows.push(row);
  }
  return rows;
};
