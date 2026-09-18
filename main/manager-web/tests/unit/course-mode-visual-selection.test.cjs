const { test } = require('node:test');
const assert = require('node:assert/strict');
const selection = require('../../src/components/lesson/lesson-visual-selection');
const phases = ['flyIn', 'walk', 'teach', 'listen', 'thinking', 'celebrate', 'exit'];
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const snapshot = () => ({ lessonId: 'A', checksum: 'a'.repeat(64), visualChecksum: 'b'.repeat(64), cinematicPhases: [], refs: [
  {stepKey:'a',slot:'backgroundScene',assetVersionId:id(1)},
  {stepKey:'a',slot:'teachingObject',assetVersionId:id(2)},
  {stepKey:'b',slot:'teachingObject',assetVersionId:id(3)},
  ...phases.map((p,i)=>({stepKey:'a',slot:`robotOverlay.${p}`,assetVersionId:id(i+10)})),
] });
test('reads all phase pins without collapsing them to the first-step alias', () => {
  const s = snapshot(); const pins = selection.courseModeVisualSelection(s);
  assert.deepEqual(pins.robotAssetVersionIds, Object.fromEntries(phases.map((p,i)=>[p,id(i+10)])));
  const next = selection.buildCourseModeVisualRequest(s, {...pins, backgroundAssetVersionId:id(4)});
  assert.equal(next.expectedChecksum,s.checksum); assert.equal(next.expectedVisualChecksum,s.visualChecksum);
  assert.equal(next.objectAssetVersionId,id(2)); assert.deepEqual(next.robotAssetVersionIds,pins.robotAssetVersionIds);
  assert.equal(s.refs[0].assetVersionId,id(1));
});
test('object-free selection omits the object without inventing a demo pin', () => {
 const s=snapshot();s.refs=s.refs.filter(r=>r.slot!=='teachingObject');
 const request=selection.buildCourseModeVisualRequest(s,selection.courseModeVisualSelection(s));
 assert.equal(Object.hasOwn(request,'objectAssetVersionId'),false);
});
test('partial phase set and duplicate phase versions cannot be saved',()=>{
 const s=snapshot();const p=selection.courseModeVisualSelection(s);delete p.robotAssetVersionIds.exit;
 assert.throws(()=>selection.buildCourseModeVisualRequest(s,p),/Exit/);
 p.robotAssetVersionIds.exit=p.robotAssetVersionIds.walk;
 assert.throws(()=>selection.buildCourseModeVisualRequest(s,p),/distinct/);
});
test('inconsistent stored phase pins are rejected',()=>{
 const s=snapshot();s.refs.push({stepKey:'b',slot:'robotOverlay.teach',assetVersionId:id(99)});
 assert.throws(()=>selection.courseModeVisualSelection(s),/inconsistent/i);
});
const robot = () => ({versionId:id(1),category:'robotPose',profile:'espTft',publicationState:'published',mimeType:'video/mp4',sha256:'a'.repeat(64),bytes:123,width:240,height:240,compatibilityMetadata:{mediaKind:'video',mediaType:'video/mp4',codec:'mjpeg',hasAudio:false,width:240,height:240,fps:10,durationMs:1000,frameCount:10,chromaKey:{keyColor:'#00ff00',tolerance:30,featherPx:1},rect:{x:200,y:80,width:240,height:240}}});
for (const [field,value,reason] of [['publicationState','archived','published'],['profile','mobile','espTft'],['sha256','broken','checksum'],['mimeType','image/png','MP4']]) test(`rejects ${field} with an explanation`,()=>{const a=robot();a[field]=value;assert.match(selection.courseModeAssetRejection(a,'robotOverlay','teach'),new RegExp(reason,'i'));});
test('codec, audio, frame timing and chroma failures are explained',()=>{
 for(const [field,value,reason] of [['codec','h264','MJPEG'],['hasAudio',true,'silent'],['frameCount',9,'timing'],['chromaKey',{},'chroma']]) {const a=robot();a.compatibilityMetadata[field]=value;assert.match(selection.courseModeAssetRejection(a,'robotOverlay','teach'),new RegExp(reason,'i'));}
});

test('valid phase-specific clip is selectable; wrong dimensions are explained',()=>{const a=robot();a.width=150;a.height=150;Object.assign(a.compatibilityMetadata,{width:150,height:150,rect:{x:118,y:160,width:150,height:150}});assert.equal(selection.courseModeAssetRejection(a,'robotOverlay','teach'),'');assert.match(selection.courseModeAssetRejection(a,'robotOverlay','flyIn'),/dimensions/i);});
test('activity choices reject incompatible profile and unpublished images',()=>{const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'../../src/views/LessonEditor.vue'),'utf8');const body=source.slice(source.indexOf('    courseModeAssets() {'),source.indexOf('    ',source.indexOf('    courseModeAssets() {')+25));const start=source.indexOf('    courseModeAssets() {');const end=source.indexOf('\n    },',start)+7;const computed=new Function('courseModeAssetRejection',`return ({${source.slice(start,end)}}).courseModeAssets`)(selection.courseModeAssetRejection);const invalid={category:'teachingObject',assetKey:'object.bad',versionId:id(1),publicationState:'published',profile:'mobile'};const e={isCourseModeV5:true,rawCinematicLibraries:{backgroundScene:[],teachingObject:[invalid]},bundleAssets:[],sharedVisualAssets:[]};assert.deepEqual(computed.call(e),[]);});

// S07 run12 (T07 run02): the backend publish gate accepts only the newest published version of each
// asset key, so the picker must not offer superseded published versions as if they were selectable.
test('superseded published versions are explained, the newest published version stays selectable', () => {
  const image = (n, version, key = 'object.no') => ({ versionId: id(n), assetKey: key, category: 'teachingObject', profile: 'espTft', publicationState: 'published', mimeType: 'image/png', sha256: 'a'.repeat(64), bytes: 12913, width: 95, height: 95, compatibilityMetadata: { mediaKind: 'image', mediaType: 'image/png', width: 95, height: 95, fit: 'contain', rect: { x: 20, y: 168, width: 95, height: 95 } }, version });
  const rows = [image(1, 1), image(3, 3), image(5, 2, 'object.please'), { ...image(6, 4, 'object.please'), publicationState: 'draft' }];
  const newest = selection.newestPublishedAssetVersions(rows);
  assert.equal(selection.courseModeAssetRejection(rows[1], 'teachingObject', null, newest), '');
  assert.match(selection.courseModeAssetRejection(rows[0], 'teachingObject', null, newest), /superseded by v3/i);
  assert.equal(selection.courseModeAssetRejection(rows[2], 'teachingObject', null, newest), '', 'newest published is v2 because v4 is only a draft');
  assert.match(selection.courseModeAssetRejection(rows[3], 'teachingObject', null, newest), /draft/i, 'unpublished stays rejected for its own reason');
  assert.equal(selection.courseModeAssetRejection(rows[0], 'teachingObject', null), '', 'without a catalog map the version-level checks are unchanged');
});
