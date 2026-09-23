const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function embed() {
  const html = fs.readFileSync(path.resolve(__dirname, '../../public/tvideo-demo/index.html'), 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  function element(src = '') {
    const attrs = { src };
    return { style: {}, loads: 0, pauses: 0, plays: 0,
      get src() { return attrs.src; }, set src(value) { attrs.src = value; },
      getAttribute: name => attrs[name], setAttribute: (name, value) => { attrs[name] = value; },
      removeAttribute: name => { delete attrs[name]; },
      load() { this.loads++; }, pause() { this.pauses++; }, play() { this.plays++; return Promise.resolve(); } };
  }
  const video = element('initial.mp4'); const image = element();
  let receive;
  vm.runInNewContext(scripts.at(-1)[1], {
    URLSearchParams, location: { search: '?embed=1' }, navigator: { userAgent: 'Safari' },
    document: { readyState: 'complete', documentElement: { classList: { add() {} } },
      getElementById: id => ({ bgv: video, bgi: image })[id], querySelector: () => null },
    window: { addEventListener: (event, handler) => { if (event === 'message') receive = handler; } },
  });
  return { video, image, apply: payload => receive({ data: { type: 'tvideo-params', payload } }) };
}

test('reference preview displays original image bytes through an image element', () => {
  const fixture = embed();
  for (const extension of ['jpg', 'jpeg', 'png', 'webp']) {
    const bg = `https://assets.tjbot.vn/course-mvp/background.${extension}?v=1`;
    fixture.apply({ bg });
    assert.equal(fixture.image.src, bg);
    assert.equal(fixture.image.style.display, 'block');
    assert.equal(fixture.video.style.display, 'none');
    assert.equal(fixture.video.src, undefined);
  }
  assert.equal(fixture.video.plays, 0);
});

test('reference preview does not restart an unchanged video on step updates', () => {
  const fixture = embed();
  fixture.apply({ bg: 'scene.mp4' });
  fixture.apply({ bg: 'scene.mp4', word: 'hello' });
  assert.equal(fixture.video.loads, 1);
  assert.equal(fixture.video.plays, 1);
});

test('reference preview restores video after an image background', () => {
  const fixture = embed();
  fixture.apply({ bg: 'scene.png' });
  fixture.apply({ bg: 'scene.mp4' });
  assert.equal(fixture.image.style.display, 'none');
  assert.equal(fixture.video.style.display, 'block');
  assert.equal(fixture.video.src, 'scene.mp4');
});
