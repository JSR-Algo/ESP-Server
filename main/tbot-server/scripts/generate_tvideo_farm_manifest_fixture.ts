import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = new Map(process.argv.slice(2).map((value, index, all) => [value, all[index + 1]]));
const backendBuildRoot = resolve(args.get('--backend-build-root') ?? '');
const output = resolve(args.get('--output') ?? '');
if (!backendBuildRoot || !output) throw new Error('usage: --backend-build-root PATH --output PATH');
process.env.LESSON_ASSET_ORIGIN_BASE = 'https://fixtures.example.test';

const load = async (relative: string) => import(pathToFileURL(join(
  backendBuildRoot,
  relative.replace(/^src\//, '').replace(/\.ts$/, '.js'),
)).href);
const { FARM_TVIDEO_JOURNEY_V1 } = await load('src/lessons/tvideo-journey/fixtures/farm-golden.ts');
const { deriveTVideoJourneyCuePlan } = await load('src/lessons/tvideo-journey/tvideo-journey.cues.ts');
const {
  buildReadyTVideoCinematicCues,
  buildTVideoAuthoringCueRequests,
} = await load('src/lessons/authoring/lesson-authoring.flattened-derivatives.ts');
const { buildIdentityProjection, buildManifest } = await load('src/lessons/lesson-manifest.logic.ts');
const { computeManifestChecksum } = await load('src/lessons/lesson.constants.ts');

const lesson = {
  id: '11111111-1111-4111-8111-111111111111',
  course_key: 'farm-course',
  lesson_key: 'tvideo-farm',
  lesson_version: 4,
  manifest_version: 'teebot-lesson-renderer.v4',
  title: 'Farm words',
  locale: 'en-US',
  age_band: '4-6',
  manifest_checksum: 'fixture-generated',
};
const steps = FARM_TVIDEO_JOURNEY_V1.steps.map((journeyStep: any, index: number) => ({
  step_key: journeyStep.stepKey,
  step_index: index,
  step_type: 'listen',
  entrance: index === 0 ? 'flyIn' : 'none',
  robot_state: 'listening',
  pose: 'teach',
  expression: 'teaching',
  phase: 'listen',
  prompt: journeyStep.teachingCopy.prompt,
  subject: journeyStep.targetWord,
  helper_text: null,
  l1_transfer_hint: null,
  choices: null,
  step_body: {
    backgroundScene: { mode: 'poster', poster: { key: 'scene.farm', src: 'scene.farm.png', sha256: 'a'.repeat(64) } },
    teachingObject: { asset: { key: 'object.farm', src: 'object.farm.png', sha256: 'b'.repeat(64) } },
    robotOverlay: { asset: { key: 'robotOverlay.teach', src: 'robotOverlay.teach.png', sha256: 'c'.repeat(64) } },
  },
}));
const assets = [
  asset('scene.farm', 'backgroundScene', 'poster', 'a'),
  asset('object.farm', 'teachingObject', 'primarySubject', 'b'),
  asset('robotOverlay.teach', 'robotOverlay', 'pose', 'c'),
];
const sources = authoritativeSources(FARM_TVIDEO_JOURNEY_V1);
const rendererBuildSha256 = '8'.repeat(64);
const fontSha256 = '9'.repeat(64);
const requests = buildTVideoAuthoringCueRequests({
  lessonId: lesson.id,
  lessonVersion: lesson.lesson_version,
  sourceRevision: 7,
  journey: FARM_TVIDEO_JOURNEY_V1,
  commonSources: sources.common,
  teachingObjectsByStep: sources.objects,
  rendererBuildSha256,
  fontSha256,
});
const cues = deriveTVideoJourneyCuePlan(FARM_TVIDEO_JOURNEY_V1);
const entries = buildReadyTVideoCinematicCues(cues, requests.map((request: any) => {
  const payload = Buffer.from(`TBOT_TVIDEO_FARM_SOFTWARE_FIXTURE_V1:${request.cueId}\n`, 'utf8');
  return {
    cueId: request.cueId,
    derivativeId: request.derivativeId,
    sourceRevision: request.sourceRevision,
    state: 'ready',
    errorCode: null,
    output: {
      path: `lessons/derivatives/${request.derivativeId}/${request.cueId}.mp4`,
      url: `https://fixtures.example.test/lessons/derivatives/${request.derivativeId}/${request.cueId}.mp4`,
      sha256: createHash('sha256').update(payload).digest('hex'),
      bytes: payload.length,
      metadata: {
        codec: 'mjpeg', width: 480, height: 320, fps: 10,
        durationMs: cues.find((cue: any) => cue.cueId === request.cueId).durationMs,
        frameCount: cues.find((cue: any) => cue.cueId === request.cueId).durationMs / 100,
        hasAudio: false,
      },
    },
  };
}));
const manifest = buildManifest(
  lesson,
  'espTft',
  steps,
  assets,
  () => 'interactive',
  [],
  entries,
  FARM_TVIDEO_JOURNEY_V1,
);
const projection = buildIdentityProjection(
  lesson, 'espTft', steps, assets, [], entries, FARM_TVIDEO_JOURNEY_V1,
);
const checksum = computeManifestChecksum(projection);
await mkdir(dirname(output), { recursive: true });
await writeFile(output, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
process.stdout.write(`${JSON.stringify({ checksum, cueCount: entries.length, rendererBuildSha256, fontSha256 })}\n`);

function asset(asset_key: string, layer: string, role: string, sha: string) {
  return {
    asset_key, layer, role, path: `${asset_key}.png`, sha256: sha.repeat(64),
    is_critical: layer !== 'robotOverlay', media_type: 'image/png', bytes: '1000',
    width: 100, height: 100,
  };
}

function authoritativeSources(journey: any) {
  const source = (role: string, id: string, mediaType: string, alpha: boolean) => ({
    role,
    assetVersionId: id,
    sourceVersion: 1,
    storagePath: `shared/${id}`,
    sha256: id.startsWith('1') ? '1'.repeat(64) : id.slice(-1).repeat(64),
    bytes: 1000,
    mediaType,
    width: mediaType === 'image/png' ? 192 : 480,
    height: mediaType === 'image/png' ? 192 : 320,
    metadata: mediaType === 'image/png'
      ? { codec: 'png', fpsNumerator: null, fpsDenominator: null, durationMs: null, frameCount: null, hasAudio: false }
      : { codec: mediaType === 'video/webm' ? 'vp9' : 'h264', fpsNumerator: 30, fpsDenominator: 1, durationMs: 9000, frameCount: 270, hasAudio: false },
    objectFit: role === 'background' ? 'cover' : 'contain',
    alpha,
    chromaKey: null,
  });
  return {
    common: [
      source('background', journey.assets.background.assetVersionId, 'video/mp4', false),
      ...journey.assets.robotClips.map((clip: any) => source(
        `robot-${clip.role}`, clip.assetVersionId, 'video/webm', true,
      )),
    ],
    objects: Object.fromEntries(journey.steps.map((step: any) => [
      step.stepKey,
      source('teachingObject', step.teachingObject.assetVersionId, 'image/png', true),
    ])),
  };
}
