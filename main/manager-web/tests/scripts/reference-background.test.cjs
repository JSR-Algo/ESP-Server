const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function embed(search = '?embed=1') {
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
  const objectTag = html.match(/<div class="object" id="obj">\s*<img([^>]*)>/)[1];
  const object = element(objectTag.match(/src="([^"]*)"/)?.[1]);
  let receive;
  vm.runInNewContext(scripts.at(-1)[1], {
    URLSearchParams, location: { search }, navigator: { userAgent: 'Safari' },
    document: { readyState: 'complete', documentElement: { classList: { add() {} } },
      getElementById: id => ({ bgv: video, bgi: image })[id],
      querySelector: selector => selector === '#obj img' ? object : null },
    window: { addEventListener: (event, handler) => { if (event === 'message') receive = handler; } },
  });
  return { video, image, object, apply: payload => receive({ data: { type: 'tvideo-params', payload } }) };
}

test('embedded object waits for authoring and retains the selected image on empty updates', () => {
  const fixture = embed();
  assert.equal(fixture.object.src, '');
  fixture.apply({ obj: 'selected.png' });
  fixture.apply({ obj: '' });
  fixture.apply({ word: 'hello' });
  assert.equal(fixture.object.src, 'selected.png');
});

test('first missing or empty object retains the standalone fallback choice', () => {
  for (const payload of [{}, { obj: '' }, { replay: true }]) {
    const fixture = embed();
    fixture.apply(payload);
    assert.equal(fixture.object.src, 'assets/objects/barn.png');
    fixture.apply({ obj: 'selected.png' });
    assert.equal(fixture.object.src, 'selected.png');
  }
});

test('query object initializes directly to the authored source', () => {
  const fixture = embed('?embed=1&obj=selected.png');
  assert.equal(fixture.object.src, 'selected.png');
});

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
