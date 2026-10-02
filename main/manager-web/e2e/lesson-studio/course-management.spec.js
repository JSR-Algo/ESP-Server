const { test, expect } = require('@playwright/test');
const { gotoAppRoute } = require('./helpers/navigation');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi, adminApiResponse } = require('./helpers/admin-api');
const { uniqueCourseKey, courseRow, fillCourseDialog, createCourseInUI } = require('./helpers/course-crud');

const pageErrors = new WeakMap();
test.beforeEach(async ({ page }) => { const errors=[]; pageErrors.set(page,errors); page.on('pageerror',e=>errors.push(e.message)); });
test.afterEach(async ({ page }) => { expect(pageErrors.get(page), 'no browser runtime exceptions').toEqual([]); });

// Real manager + author login, nginx proxy, BE-01/04 and native PostgreSQL.
test('real course CRUD survives readback and reload', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const key = uniqueCourseKey(); const title = `FE02 ${key}`;
  const created = await createCourseInUI(page,key,title);
  await page.reload(); await expect(courseRow(page,key)).toBeVisible();
  await courseRow(page,key).getByRole('button',{name:'Edit',exact:true}).click();
  const edit = page.getByRole('dialog',{name:'Edit course',exact:true});
  await edit.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox').fill(title+' edited');
  const patched = page.waitForResponse(r=>r.url().endsWith('/courses/'+created.id) && r.request().method()==='PATCH');
  await edit.getByTestId('course-submit').click(); const response = await patched;
  expect(response.status()).toBe(200); expect(response.request().postDataJSON()).not.toHaveProperty('courseKey');
  await expect(edit).toBeHidden(); expect((await adminApi(page,'GET','/courses/'+created.id)).title).toBe(title+' edited');
  await courseRow(page,key).getByRole('button',{name:'Mark template',exact:true}).click();
  await expect(courseRow(page,key).getByRole('button',{name:'Unmark template',exact:true})).toBeVisible();
  expect((await adminApi(page,'GET','/courses/'+created.id)).is_template).toBe(true);
  await courseRow(page,key).getByRole('button',{name:'Clone',exact:true}).click();
  const clone=page.getByRole('dialog',{name:'Clone → customize',exact:true});
  await clone.getByTestId('course-clone-submit').click(); await expect(clone).toBeHidden();
  await expect(page.getByRole('heading',{name:/lessons/i})).toBeVisible();
  await gotoAppRoute(page,'#/course-management');
  const copyKey=key+'-custom'; await expect(courseRow(page,copyKey)).toBeVisible();
  const copied=(await adminApi(page,'GET','/courses?kind=all')).find(c=>c.course_key===copyKey);
  expect(copied.source_course_id).toBe(created.id); expect(copied.status).toBe('draft');
  for(const target of [copyKey,key]) {
    await courseRow(page,target).getByRole('button',{name:'Delete',exact:true}).click();
    await page.locator('.el-message-box').getByRole('button',{name:'OK',exact:true}).click();
    await expect(courseRow(page,target)).toHaveCount(0);
  }
  expect((await adminApi(page,'GET','/courses?kind=all')).some(c=>[key,copyKey].includes(c.course_key))).toBe(false);
});

test('validation and real conflicts preserve draft without success', async ({ page }) => {
  await loginAsLessonAuthor(page); const key=uniqueCourseKey('conflict');
  const course=await createCourseInUI(page,key,'FE02 conflict');
  await page.getByRole('button',{name:'Create course',exact:true}).click();
  const dialog=await fillCourseDialog(page,key,'   '); let posts=0;
  page.on('request',r=>{if(/\/courses$/.test(r.url())&&r.method()==='POST')posts++;});
  await dialog.getByTestId('course-submit').click();await expect(dialog.locator('.el-form-item__error')).toContainText('Enter nonblank');expect(posts).toBe(0);
  await dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox').fill('FE02 kept draft');
  const duplicate=page.waitForResponse(r=>/\/courses$/.test(r.url())&&r.status()===409);
  await dialog.getByTestId('course-submit').click();await duplicate;
  await expect(dialog).toBeVisible();await expect(dialog.locator('.el-form-item').filter({hasText:'Course key'}).locator('.el-form-item__error')).toContainText('already exists');
  await expect(dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox')).toHaveValue('FE02 kept draft');
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  const lesson=await adminApi(page,'POST',`/courses/${course.id}/lessons`,{lessonKey:uniqueCourseKey('lesson'),title:'FE02 retained lesson',locale:'en-US',ageBand:'4-6'});
  await courseRow(page,key).getByRole('button',{name:'Delete',exact:true}).click();
  const nonempty=page.waitForResponse(r=>r.url().endsWith('/courses/'+course.id)&&r.status()===409);
  await page.locator('.el-message-box').getByRole('button',{name:'OK',exact:true}).click();await nonempty;
  await expect(page.locator('.el-message--error').filter({hasText:'contains lessons'})).toBeVisible();
  expect((await adminApi(page,'GET','/courses/'+course.id)).id).toBe(course.id);
  await adminApi(page,'DELETE','/lessons/'+lesson.id);await adminApi(page,'DELETE','/courses/'+course.id);
});

// Controlled browser response loss AFTER a real backend commit, never a fake success.
test('one create in flight and lost committed response reconciles before retry', async ({ page }) => {
 await loginAsLessonAuthor(page);const key=uniqueCourseKey('lost');let count=0;let release;
 const gate=new Promise(resolve=>{release=resolve;});
 await page.route('**/v1/admin/courses',async route=>{
   if(route.request().method()!=='POST')return route.continue();count++;
   const response=await route.fetch();expect(response.status()).toBe(201);await gate;await route.abort('failed');
 });
 await page.getByRole('button',{name:'Create course',exact:true}).click();const dialog=await fillCourseDialog(page,key,'FE02 lost response');
 const titleInput=dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox');
 await titleInput.press('Enter');await titleInput.press('Enter');
 await expect.poll(()=>count).toBe(1);release();
 await expect(dialog).toContainText('A course with this identity exists');
 await expect(dialog.getByRole('button',{name:'Review stored course'})).toBeVisible();
 await dialog.getByTestId('course-submit').click();await expect(dialog).toContainText('A course with this identity exists');expect(count).toBe(1);
 const rows=(await adminApi(page,'GET','/courses?kind=all')).filter(c=>c.course_key===key);expect(rows).toHaveLength(1);
 await adminApi(page,'DELETE','/courses/'+rows[0].id);
});

test('late edit completion preserves course B dialog', async ({ page }) => {
 await loginAsLessonAuthor(page);const keyA=uniqueCourseKey('a'),keyB=uniqueCourseKey('b');
 const a=await createCourseInUI(page,keyA,'FE02 A');const b=await createCourseInUI(page,keyB,'FE02 B');let release,started=false;
 const gate=new Promise(resolve=>{release=resolve;});
 await page.route('**/courses/'+a.id,async route=>{
   if(route.request().method()!=='PATCH')return route.continue();const response=await route.fetch();started=true;await gate;await route.fulfill({response});
 });
 await courseRow(page,keyA).getByRole('button',{name:'Edit',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Edit course',exact:true});
 await dialog.getByTestId('course-submit').click();await expect.poll(()=>started).toBe(true);
 await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog).toBeHidden();
 await courseRow(page,keyB).getByRole('button',{name:'Edit',exact:true}).click();release();
 await expect(dialog).toBeVisible();await expect(dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox')).toHaveValue('FE02 B');
 await expect(dialog.getByTestId('course-submit')).not.toHaveClass(/is-loading/);
 await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
 await adminApi(page,'DELETE','/courses/'+a.id);await adminApi(page,'DELETE','/courses/'+b.id);
});

test('real revoked author session rejects mutation and retains draft', async ({ page }) => {
 await loginAsLessonAuthor(page);const key=uniqueCourseKey('expired');
 await page.getByRole('button',{name:'Create course',exact:true}).click();const dialog=await fillCourseDialog(page,key,'FE02 preserved after expiry');
 const revoked=await adminApiResponse(page,'POST','/auth/logout',{});expect(revoked.ok()).toBe(true);
 const rejected=page.waitForResponse(r=>/\/courses$/.test(r.url())&&r.request().method()==='POST'&&r.status()===401);
 await dialog.getByTestId('course-submit').click();await rejected;
 await expect(page.getByRole('dialog',{name:/sign in as author/i})).toBeVisible();
 await expect(dialog).toBeVisible();await expect(dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox')).toHaveValue('FE02 preserved after expiry');
 expect(await page.evaluate(()=>localStorage.getItem('nestjs_session_token'))).toBe(null);
});

test('clone double submit with lost committed response keeps identity and reviews readback', async ({ page }) => {
 await loginAsLessonAuthor(page);const key=uniqueCourseKey('clone-source');const a=await createCourseInUI(page,key,'FE02 clone source');let count=0,release;
 const gate=new Promise(resolve=>{release=resolve;});
 await page.route('**/courses/'+a.id+'/clone',async route=>{count++;const response=await route.fetch();expect(response.status()).toBe(201);await gate;await route.abort('failed');});
 await courseRow(page,key).getByRole('button',{name:'Clone',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Clone → customize',exact:true});
 const titleInput=dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox');
 await titleInput.press('Enter');await titleInput.press('Enter');
 await expect.poll(()=>count).toBe(1);release();await expect(dialog).toContainText('A course with this identity exists');
 await dialog.getByTestId('course-clone-submit').click();await expect(dialog).toContainText('A course with this identity exists');expect(count).toBe(1);
 const copies=(await adminApi(page,'GET','/courses?kind=all')).filter(c=>c.course_key===key+'-custom');expect(copies).toHaveLength(1);expect(copies[0].source_course_id).toBe(a.id);
 await adminApi(page,'DELETE','/courses/'+copies[0].id);await adminApi(page,'DELETE','/courses/'+a.id);
});

test('lessons validate metadata and persist draft edits through real API', async ({ page }) => {
 await loginAsLessonAuthor(page);const key=uniqueCourseKey('lesson-course');const course=await createCourseInUI(page,key,'FE02 lesson course');
 const lesson=await adminApi(page,'POST',`/courses/${course.id}/lessons`,{lessonKey:uniqueCourseKey('metadata'),title:'FE02 lesson metadata',locale:'en-US',ageBand:'4-6'});
 await courseRow(page,key).getByRole('button',{name:'Lessons',exact:true}).click();
 const row=page.locator('.el-table__body-wrapper tr').filter({has:page.getByText(lesson.lesson_key,{exact:true})});
 await expect(row).toBeVisible();await page.getByRole('button',{name:'Metadata',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Edit lesson metadata',exact:true});
 const title=dialog.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox');await title.fill('  ');
 await dialog.getByRole('button',{name:'Save',exact:true}).click();await expect(dialog.locator('.el-form-item__error')).toContainText('Enter nonblank');
 await title.fill('FE02 lesson metadata saved');const response=page.waitForResponse(r=>r.url().endsWith('/lessons/'+lesson.id)&&r.request().method()==='PATCH');
 await dialog.getByRole('button',{name:'Save',exact:true}).click();const saved=await response;expect(saved.status()).toBe(200);expect(saved.request().postDataJSON()).not.toHaveProperty('lessonKey');await expect(dialog).toBeHidden();
 expect((await adminApi(page,'GET','/lessons/'+lesson.id)).title).toBe('FE02 lesson metadata saved');await page.reload();await expect(row).toContainText('FE02 lesson metadata saved');
 await page.getByRole('button',{name:'Create lesson',exact:true}).click();const create=page.getByRole('dialog',{name:'Create lesson',exact:true});
 await create.locator('.el-form-item').filter({hasText:'Lesson key'}).getByRole('textbox').fill('Invalid KEY');
 await create.locator('.el-form-item').filter({hasText:'Title'}).getByRole('textbox').fill('FE02 invalid lesson');await create.getByRole('button',{name:'Save',exact:true}).click();await expect(create.locator('.el-form-item__error')).toContainText('lowercase');
 await create.getByRole('button',{name:'Cancel',exact:true}).click();await adminApi(page,'DELETE','/lessons/'+lesson.id);await adminApi(page,'DELETE','/courses/'+course.id);
});

// FE-03: real manager/proxy/API/DB lifecycle; public reads use real parent signup.
async function lifecycleDialog(page, key, selector) {
  await courseRow(page,key).getByTestId(selector).click();
  const dialog=page.getByRole('dialog',{name:'Course availability',exact:true});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId('course-lifecycle-confirm')).toBeEnabled();
  await expect(dialog).toContainText(key);return dialog;
}
async function parentCatalog(page) {
  const origin=`http://127.0.0.1:${process.env.LESSON_STUDIO_E2E_BACKEND_HOST_PORT}`;
  const response=await page.request.post(origin+'/v1/auth/signup',{data:{name:'FE03 synthetic parent',email:`fe03-${Date.now()}-${Math.random().toString(16).slice(2)}@example.invalid`,password:'Fe03Proof!2026'}});
  expect(response.status()).toBe(201);const {access_token}=await response.json();expect(access_token).toBeTruthy();
  return async()=>{const r=await page.request.get(origin+'/v1/courses',{headers:{Authorization:`Bearer ${access_token}`}});expect(r.status()).toBe(200);return (await r.json()).data;};
}
test('FE03 real empty and draft-only publication refuses with visible prerequisites and unchanged catalog',async({page},testInfo)=>{
  await loginAsLessonAuthor(page);const catalog=await parentCatalog(page);const key=uniqueCourseKey('fe03-empty');const course=await createCourseInUI(page,key,'FE03 empty course');
  const dialog=await lifecycleDialog(page,key,'course-publish');await expect(dialog).toContainText('every latest published lesson');await expect(dialog.locator('p').first()).toHaveCSS('word-break','normal');await expect(dialog.locator('p').first()).toHaveCSS('text-align','left');await testInfo.attach('course-publication-prerequisites',{body:await dialog.screenshot(),contentType:'image/png'});
  const reject=page.waitForResponse(r=>r.url().endsWith('/courses/'+course.id)&&r.request().method()==='PATCH');await dialog.getByTestId('course-lifecycle-confirm').click();expect((await reject).status()).toBe(422);
  await expect(dialog.getByTestId('course-lifecycle-notice')).toContainText('Publish at least one playable lesson');
  expect((await adminApi(page,'GET','/courses/'+course.id)).status).toBe('draft');expect((await catalog()).some(c=>c.courseId===key)).toBe(false);
  const lesson=await adminApi(page,'POST',`/courses/${course.id}/lessons`,{lessonKey:key,title:'FE03 draft-only',locale:'en-US',ageBand:'4-6'});
  const rejectDraft=page.waitForResponse(r=>r.url().endsWith('/courses/'+course.id)&&r.request().method()==='PATCH');await dialog.getByTestId('course-lifecycle-confirm').click();expect((await rejectDraft).status()).toBe(422);
  expect((await catalog()).some(c=>c.courseId===key)).toBe(false);
  await testInfo.attach('lifecycle-refusal',{body:JSON.stringify({course:await adminApi(page,'GET','/courses/'+course.id),publicVisible:false}),contentType:'application/json'});
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await adminApi(page,'DELETE','/lessons/'+lesson.id);await adminApi(page,'DELETE','/courses/'+course.id);
});
test('FE03 lesson publication keeps course draft then explicit publish reload catalog and archive agree',async({page},testInfo)=>{
  await loginAsLessonAuthor(page);const catalog=await parentCatalog(page);const key=uniqueCourseKey('fe03-live');
  const {createLegacyFixtureCourse}=require('./helpers/legacy-fixtures');const fixture=await createLegacyFixtureCourse(page,key);
  const published=await adminApi(page,'POST',`/lessons/${fixture.lesson.id}/publish`,{});
  expect((await adminApi(page,'GET','/lessons/'+fixture.lesson.id)).status).toBe('published');expect((await adminApi(page,'GET','/courses/'+fixture.course.id)).status).toBe('draft');expect((await catalog()).some(c=>c.courseId===key)).toBe(false);
  await page.reload();const dialog=await lifecycleDialog(page,key,'course-publish');let count=0,release;const gate=new Promise(r=>release=r);
  await page.route('**/courses/'+fixture.course.id,async route=>{if(route.request().method()!=='PATCH')return route.continue();count++;const response=await route.fetch();expect(response.status()).toBe(200);expect(route.request().postDataJSON().expectedRevision).toMatch(/^[a-f0-9]{32}$/);await gate;await route.fulfill({response});});
  await dialog.getByTestId('course-lifecycle-confirm').click();await page.keyboard.press('Enter');await page.keyboard.press('Enter');await expect.poll(()=>count).toBe(1);await expect(dialog.getByTestId('course-lifecycle-confirm')).toBeDisabled();release();await expect(dialog).toBeHidden();await page.unroute('**/courses/'+fixture.course.id);
  await page.reload();await expect(courseRow(page,key)).toContainText('published');const revalidate=await lifecycleDialog(page,key,'course-publish');await revalidate.getByTestId('course-lifecycle-confirm').click();await expect(revalidate).toBeHidden();expect((await catalog()).find(c=>c.courseId===key)).toMatchObject({lessonCount:1});
  const archive=await lifecycleDialog(page,key,'course-archive');await expect(archive).toContainText('Existing enrollments, progress and pinned lesson versions remain');await expect(archive).toContainText('active lessons are not canceled');await archive.getByTestId('course-lifecycle-confirm').click();await expect(archive).toBeHidden();await page.reload();await expect(courseRow(page,key)).toContainText('archived');expect((await catalog()).some(c=>c.courseId===key)).toBe(false);
  const lessonAfter=await adminApi(page,'GET','/lessons/'+fixture.lesson.id);expect(lessonAfter.status).toBe('published');expect(lessonAfter.manifest_checksum).toBe(published.checksum);const restore=await lifecycleDialog(page,key,'course-publish');await restore.getByTestId('course-lifecycle-confirm').click();await expect(restore).toBeHidden();expect((await catalog()).some(c=>c.courseId===key)).toBe(true);const rearchive=await lifecycleDialog(page,key,'course-archive');await rearchive.getByTestId('course-lifecycle-confirm').click();await expect(rearchive).toBeHidden();expect((await catalog()).some(c=>c.courseId===key)).toBe(false);
  await testInfo.attach('lifecycle-live-readback',{body:JSON.stringify({course:await adminApi(page,'GET','/courses/'+fixture.course.id),lesson:lessonAfter,publicVisible:false,duplicateRequestCount:count}),contentType:'application/json'});
  const draft=await lifecycleDialog(page,key,'course-return-draft');await draft.getByTestId('course-lifecycle-confirm').click();await expect(draft).toBeHidden();expect((await adminApi(page,'GET','/courses/'+fixture.course.id)).status).toBe('draft');
  // Published versions are immutable; retain the task-owned fixture for QA-01 readback.
});
test('FE03 stale second author conflicts then explicit reviewed retry succeeds',async({page,context},testInfo)=>{
  await loginAsLessonAuthor(page);const key=uniqueCourseKey('fe03-stale');const course=await createCourseInUI(page,key,'FE03 stale course');const dialog=await lifecycleDialog(page,key,'course-archive');
  const second=await context.browser().newContext({baseURL:process.env.LESSON_STUDIO_E2E_WEB_ORIGIN||`http://127.0.0.1:${process.env.LESSON_STUDIO_E2E_WEB_HOST_PORT}`,serviceWorkers:'block'});const other=await second.newPage();
  try{
   await loginAsLessonAuthor(other);const newer=await adminApi(other,'GET','/courses/'+course.id);await adminApi(other,'PATCH','/courses/'+course.id,{status:'archived',expectedRevision:newer.revision});
   const conflict=page.waitForResponse(r=>r.url().endsWith('/courses/'+course.id)&&r.request().method()==='PATCH');await dialog.getByTestId('course-lifecycle-confirm').click();const r=await conflict;expect(r.status()).toBe(409);expect((await r.json()).details.reason).toBe('stale');
   await expect(dialog.getByTestId('course-lifecycle-notice')).toContainText('Another change was saved');await expect(dialog.getByTestId('course-lifecycle-readback')).toContainText('archived');await expect(dialog.getByTestId('course-lifecycle-confirm')).toBeDisabled();
   await dialog.getByTestId('course-lifecycle-review').click();await dialog.getByTestId('course-lifecycle-confirm').click();await expect(dialog).toBeHidden();expect((await adminApi(page,'GET','/courses/'+course.id)).status).toBe('archived');
   await testInfo.attach('stale-real-readback',{body:JSON.stringify(await adminApi(page,'GET','/courses/'+course.id)),contentType:'application/json'});
  }finally{await second.close();await adminApi(page,'DELETE','/courses/'+course.id);}
});
test('FE03 response loss after real transition commit reviews stored state without automatic replay',async({page})=>{
  await loginAsLessonAuthor(page);const key=uniqueCourseKey('fe03-loss');const course=await createCourseInUI(page,key,'FE03 lost archive response');const dialog=await lifecycleDialog(page,key,'course-archive');let count=0;
  await page.route('**/courses/'+course.id,async route=>{if(route.request().method()!=='PATCH')return route.continue();count++;const response=await route.fetch();expect(response.status()).toBe(200);await route.abort('failed');});
  await dialog.getByTestId('course-lifecycle-confirm').click();await expect(dialog.getByTestId('course-lifecycle-notice')).toContainText('outcome is uncertain');await expect(dialog.getByTestId('course-lifecycle-readback')).toContainText('archived');await expect(dialog.getByTestId('course-lifecycle-confirm')).toBeDisabled();expect(count).toBe(1);expect((await adminApi(page,'GET','/courses/'+course.id)).status).toBe('archived');await adminApi(page,'DELETE','/courses/'+course.id);
});
