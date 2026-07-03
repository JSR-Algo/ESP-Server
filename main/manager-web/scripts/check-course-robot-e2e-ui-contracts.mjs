import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function extractFunction(source, name, prefix = 'export function ') {
  const marker = `${prefix}${name}`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} not found`);
  const fnStart = source.indexOf('function', start);
  const braceStart = source.indexOf('{', fnStart);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(fnStart, i + 1);
    }
  }
  throw new Error(`${name} body not closed`);
}

function extractObjectMethod(source, name) {
  const marker = `${name}(`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} method not found`);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) {
        const params = source.slice(paramsStart + 1, paramsEnd);
        const body = source.slice(braceStart + 1, i);
        return `function ${name}(${params}) {${body}}`;
      }
    }
  }
  throw new Error(`${name} method body not closed`);
}

function loadSettle() {
  const source = read('src/apis/nestHttp.js');
  const fnSource = extractFunction(source, 'settle');
  const context = { clearNestSessionCalled: false };
  context.clearNestSession = () => {
    context.clearNestSessionCalled = true;
  };
  return { settle: vm.runInNewContext(`(${fnSource})`, context), context };
}

function loadBuildScene() {
  const source = read('src/views/LessonEditor.vue');
  return vm.runInNewContext(`(${extractObjectMethod(source, 'buildScene')})`);
}

{
  const { settle } = loadSettle();
  let received;
  settle(
    { status: 403, response: { data: { message: 'admin role required' } } },
    null,
    (msg) => { received = msg; },
  );
  assert.equal(received, 'admin role required');
}

{
  const { settle } = loadSettle();
  let received;
  settle(
    { status: 500, response: { data: { error: { message: 'manifest validation failed' } } } },
    null,
    (msg) => { received = msg; },
  );
  assert.equal(received, 'manifest validation failed');
}

{
  const { settle, context } = loadSettle();
  let received;
  settle(
    { status: 401, response: { data: { message: 'bad credentials' } } },
    null,
    (msg) => { received = msg; },
    false,
  );
  assert.equal(received, 'bad credentials');
  assert.equal(context.clearNestSessionCalled, false);
}

{
  const buildScene = loadBuildScene();
  const assets = {
    'backgroundScene.poster': {
      assetKey: 'backgroundScene.poster',
      path: 'lesson-assets/background-poster.jpg',
      url: 'https://old-cdn.example.com/lesson-assets/background-poster.jpg',
      sha256: 'bg-sha',
    },
    'teachingObject.barn': {
      assetKey: 'teachingObject.barn',
      path: 'lesson-assets/barn.png',
      url: 'https://old-cdn.example.com/lesson-assets/barn.png',
      sha256: 'obj-sha',
    },
    'robotOverlay.teach': {
      assetKey: 'robotOverlay.teach',
      path: 'lesson-assets/bright-teach.png',
      url: 'https://old-cdn.example.com/lesson-assets/bright-teach.png',
      sha256: 'overlay-sha',
    },
  };
  const scene = buildScene.call(
    {
      stepForm: {
        stepType: 'model',
        renderExpression: '',
        scene: {
          backgroundKey: 'backgroundScene.poster',
          objectKey: 'teachingObject.barn',
          fit: 'contain',
          altCaption: 'barn poster',
          primaryWord: 'barn',
          supportWords: ['farm', 'hay', ''],
          placementAnchor: 'center',
          activeWindows: [{ tStart: 0.2, tEnd: 1.8, x: 1.2, y: -0.5, w: 0.5, h: 0.25 }],
          successUtterance: 'Good barn',
          missUtterance: 'Try barn',
          timeoutSec: 15,
        },
      },
      assetByKey(key) { return assets[key]; },
    },
    'barn',
  );

  assert.equal(scene.backgroundScene.poster.src, 'lesson-assets/background-poster.jpg');
  assert.equal(scene.teachingObject.asset.src, 'lesson-assets/barn.png');
  assert.equal(scene.backgroundScene.poster.sha256, 'bg-sha');
  assert.equal(scene.teachingObject.asset.sha256, 'obj-sha');
  assert.equal(scene.robotOverlay.pose, 'teach');
  assert.equal(scene.robotOverlay.expression, 'teaching');
  assert.equal(scene.robotOverlay.asset.key, 'robotOverlay.teach');
  assert.equal(scene.robotOverlay.asset.src, 'lesson-assets/bright-teach.png');
  assert.equal(scene.robotOverlay.asset.sha256, 'overlay-sha');
  assert.equal(scene.robotOverlay.atlas.image, 'lesson-assets/bright-teach.png');
  assert.equal(JSON.stringify(scene.teachingObject.focusTarget.activeWindows[0]), JSON.stringify({
    tStart: 0.2,
    tEnd: 1.8,
    x: 1,
    y: 0,
    w: 0.5,
    h: 0.25,
  }));
}

{
  const buildScene = loadBuildScene();
  const assets = {
    'backgroundScene.poster': { assetKey: 'backgroundScene.poster', path: 'assets/background/barn.jpg', sha256: 'bg' },
    'teachingObject.barn': { assetKey: 'teachingObject.barn', path: 'assets/objects/barn.png', sha256: 'obj' },
    'robotOverlay.thinking': { assetKey: 'robotOverlay.thinking', path: 'assets/robot/poses/bright-thinking.png', sha256: 'think' },
  };
  const scene = buildScene.call(
    {
      stepForm: {
        stepType: 'listen',
        renderExpression: 'thinking',
        scene: {
          backgroundKey: 'backgroundScene.poster',
          objectKey: 'teachingObject.barn',
          fit: 'cover',
          altCaption: '',
          primaryWord: 'barn',
          supportWords: [],
          placementAnchor: 'center',
          activeWindows: [],
          successUtterance: '',
          missUtterance: '',
          timeoutSec: 12,
        },
      },
      assetByKey(key) { return assets[key]; },
    },
    'barn',
  );

  assert.equal(scene.robotOverlay.pose, 'thinking');
  assert.equal(scene.robotOverlay.expression, 'thinking');
  assert.equal(scene.robotOverlay.asset.src, 'assets/robot/poses/bright-thinking.png');
}

console.log('course robot E2E UI contracts OK');
