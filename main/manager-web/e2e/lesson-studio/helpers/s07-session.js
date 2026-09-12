const fs = require('node:fs');
const { expect } = require('@playwright/test');
async function openS07Session(page) {
  if (!process.env.CPR_S07_SESSION_FILE) {
    await require('./session').loginAsLessonAuthor(page);
    return {};
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
module.exports={openS07Session,s07Api,s07Draft};
