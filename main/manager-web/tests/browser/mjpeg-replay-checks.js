// Runs in a mounted fixture using the actual preview, controls and image decoder.
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
