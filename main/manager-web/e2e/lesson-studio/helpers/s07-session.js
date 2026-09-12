const fs = require('node:fs');
const { expect } = require('@playwright/test');
async function openS07Session(page) {
  if (!process.env.CPR_S07_SESSION_FILE) {
    await require('./session').loginAsLessonAuthor(page);
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
  const config=await openS07Session(page);
  if (!process.env.CPR_S07_SESSION_FILE) return require('./admin-api').createCourseModeDraft(page);
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
  const image = key => {
    const row = catalog.find(asset => (asset.asset_key || asset.assetKey) === key
      && (asset.publication_state || asset.publicationState) === 'published');
    expect(row, `published canonical image required for ${key}`).toBeTruthy();
    return row.version_id || row.versionId;
  };
  const robotAssetVersionIds = Object.fromEntries(['flyIn','walk','teach','listen','thinking','celebrate','exit'].map(phase => {
    const ref = source.refs.find(row => row.slot === `robotOverlay.${phase}`);
    expect(ref, `source ${config.source} must provide a real published ${phase} binding`).toBeTruthy();
    return [phase, ref.assetVersionId];
  }));
  const objectKey = contract.activities.find(activity => activity.visual?.objectAssetKey)?.visual.objectAssetKey;
  return { robotAssetVersionIds, ids: {
    background: image(contract.activities[0].visual.backgroundAssetKey),
    ...(objectKey ? { object: image(objectKey) } : {}), ...robotAssetVersionIds,
  } };
}
module.exports={openS07Session,s07Api,s07Draft,publishedCourseModeSelection};
