const fs = require('node:fs');
const { expect } = require('@playwright/test');
async function openS07Session(page) {
  if (!process.env.CPR_S07_SESSION_FILE) {
    // A second tab of the same browser context shares the origin's storage, exactly as a real
    // second tab does, and the manager login route redirects to /home while a token exists. So the
    // real login runs once per context; a later tab only navigates and verifies both real sessions.
    await page.goto('/');
    const signedIn = await page.evaluate(() => {
      try {
        const manager = JSON.parse(localStorage.getItem('token') || 'null');
        return Boolean(manager && manager.token && localStorage.getItem('nestjs_session_token'));
      } catch { return false; }
    });
    if (!signedIn) await require('./session').loginAsLessonAuthor(page);
    return { source: process.env.LESSON_STUDIO_E2E_VISUAL_SOURCE_LESSON_ID || '00000006-0002-0000-0000-000000000001' };
  }
  const config = JSON.parse(fs.readFileSync(process.env.CPR_S07_SESSION_FILE, 'utf8'));
  await page.addInitScript(({token}) => {
    localStorage.setItem('token', JSON.stringify({token}));
    localStorage.setItem('nestjs_session_token', token);
    localStorage.setItem('userInfo', JSON.stringify({superAdmin:true,username:'cpr-s07-browser'}));
    localStorage.setItem('language', 'en');
  }, config);
  await page.goto('/');
  return config;
}
async function s07Api(page, method, route, data) {
  if (!process.env.CPR_S07_SESSION_FILE) return require('./admin-api').adminApi(page,method,route,data);
  const config=JSON.parse(fs.readFileSync(process.env.CPR_S07_SESSION_FILE,'utf8'));
  const response=await page.request.fetch(`/nestjs/v1/admin${route}`,{method,data,timeout:15000,headers:{'X-Nest-Authorization':`Bearer ${config.token}`}});
  expect(response.ok(), `${method} ${route}: ${response.status()} ${await response.text()}`).toBe(true);
  return (await response.json()).data;
}
async function s07Draft(page) {
  // Without a session file the draft is created through the real manager+author session held in
  // the page's localStorage. A test whose first action is s07Draft still has its page on
  // about:blank, and that opaque origin denies every localStorage access, so adminAuthHeaders
  // throws SecurityError before any request is made. Open the session first: openS07Session is
  // idempotent, and the manager login page redirects to /home while a token exists, so a second
  // real login is neither attempted nor possible.
  if (!process.env.CPR_S07_SESSION_FILE) {
    await openS07Session(page);
    return require('./admin-api').createCourseModeDraft(page);
  }
  const config=await openS07Session(page);
  const original=await s07Api(page,'GET',`/lessons/${config.source}/course-mode`);
  const key=`cpr-s07-${Date.now()}-${Math.random().toString(36).slice(2,7)}`;
  const course=await s07Api(page,'POST','/courses',{courseKey:key,title:key,locale:'en-US',ageBand:'4-6'});
  const lesson=await s07Api(page,'POST',`/courses/${course.id}/lessons`,{lessonKey:key,title:key,locale:'en-US',ageBand:'4-6',rendererVersion:'teebot-lesson-renderer.v5',estimatedDurationSec:480,durationPreset:8});
  const initial=await s07Api(page,'GET',`/lessons/${lesson.id}/visuals`);
  await s07Api(page,'PUT',`/lessons/${lesson.id}/course-mode`,{contract:original.contract,expectedChecksum:initial.checksum,expectedVisualChecksum:initial.visualChecksum});
  return {lesson,course,original};
}
async function publishedCourseModeSelection(page, lessonId) {
  const config = process.env.CPR_S07_SESSION_FILE
    ? JSON.parse(fs.readFileSync(process.env.CPR_S07_SESSION_FILE, 'utf8'))
    : { source: process.env.LESSON_STUDIO_E2E_VISUAL_SOURCE_LESSON_ID || '00000006-0002-0000-0000-000000000001' };
  const source = await s07Api(page, 'GET', `/lessons/${config.source}/visuals`);
  const { contract } = await s07Api(page, 'GET', `/lessons/${lessonId}/course-mode`);
  const catalog = await s07Api(page, 'GET', '/lesson-visual-assets?profile=espTft');
  // Publication rejects any binding that is not the newest published espTft version of its key
  // (DISTINCT ON asset_key ORDER BY version DESC in the publish gate), so the fixture must select
  // exactly that row instead of whatever the catalog happens to list first.
  const assetKey = asset => asset.asset_key || asset.assetKey;
  const versionId = asset => asset.version_id || asset.versionId;
  const published = catalog.filter(asset => (asset.publication_state || asset.publicationState) === 'published');
  const newestByKey = new Map();
  for (const asset of published) {
    const current = newestByKey.get(assetKey(asset));
    if (!current || Number(asset.version) > Number(current.version)) newestByKey.set(assetKey(asset), asset);
  }
  const image = key => {
    const row = newestByKey.get(key);
    expect(row, `published canonical image required for ${key}`).toBeTruthy();
    return versionId(row);
  };
  const keyByVersionId = new Map(catalog.map(asset => [versionId(asset), assetKey(asset)]));
  const robotAssetVersionIds = Object.fromEntries(['flyIn','walk','teach','listen','thinking','celebrate','exit'].map(phase => {
    const ref = source.refs.find(row => row.slot === `robotOverlay.${phase}`);
    expect(ref, `source ${config.source} must provide a real published ${phase} binding`).toBeTruthy();
    const key = keyByVersionId.get(ref.assetVersionId);
    expect(key, `source ${phase} binding must resolve to a real asset key`).toBeTruthy();
    return [phase, image(key)];
  }));
  const objectKey = contract.activities.find(activity => activity.visual?.objectAssetKey)?.visual.objectAssetKey;
  return { robotAssetVersionIds, ids: {
    background: image(contract.activities[0].visual.backgroundAssetKey),
    ...(objectKey ? { object: image(objectKey) } : {}), ...robotAssetVersionIds,
  } };
}
module.exports={openS07Session,s07Api,s07Draft,publishedCourseModeSelection};
