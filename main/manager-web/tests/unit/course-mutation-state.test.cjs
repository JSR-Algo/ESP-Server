const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function setup(view = 'CourseInsights') {
  const pending = {};
  const api = new Proxy({}, { get: (_, group) => new Proxy({}, { get: (_, method) => (...args) => {
    (pending[method] ||= []).push({ args, ok: args.at(-2), fail: args.at(-1) });
  } }) });
  const source = fs.readFileSync(path.join(process.env.FE02_SOURCE_ROOT || path.join(__dirname, '../../src/views'), `${view}.vue`), 'utf8');
  const script = source.split('<script>')[1].split('</script>')[0]
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
  const component = new Function('Api', 'HeaderBar', 'AGE_BANDS', 'LOCALES', 'DEFAULT_AGE_BAND', 'DEFAULT_LOCALE', 'validateCourseForm', 'mutationDetails', 'uncertainMutation', script)(api, {}, [], [], '3-5', 'en', ...Object.values(require('../../src/utils/courseForm.cjs')));
  const messages = [];
  const state = { ...component.data(), $route: { query: { courseId: 'A' } }, $router: { replace() {}, push() {} },
    $t: k => k, $message: Object.fromEntries(['error','warning','success'].map(k => [k, msg => messages.push([k, msg])])) };
  if (view === 'CourseLessons') {
    // These mutation cases start on an already-loaded route; initial metadata
    // loading and invalid route denial are covered by course-route-version-state.
    state.courseInfo = { courseId: state.$route.query.courseId, title: 'Loaded Course A' };
  }
  for (const [k, f] of Object.entries(component.methods)) state[k] = f.bind(state);
  for (const [k, f] of Object.entries(component.computed || {})) Object.defineProperty(state, k, { get: () => f.call(state) });
  return { state, component, pending, messages };
}
const row = id => ({courseId:id, courseKey:`course-${id}`, title:`Course ${id}`, locale:'en-US', ageBand:'4-6'});
for (const action of ['create','edit','clone']) test(`${action} sends only one in-flight mutation`, () => {
  const {state:s,pending:p}=setup('CourseManagement');
  if(action==='clone') { s.openClone(row('A')); s.doClone(); s.doClone(); }
  else { if(action==='edit') s.openEdit(row('A')); else { s.openCreate(); Object.assign(s.form,row('A')); } s.submit(); s.submit(); }
  assert.equal(p[action==='clone'?'cloneCourse':action==='edit'?'updateCourse':'createCourse'].length,1);
});
test('A edit completion preserves newly opened B dialog', () => {
 const {state:s,pending:p}=setup('CourseManagement');
 s.openEdit(row('A')); s.submit(); s.dialogVisible=false; s.openEdit(row('B'));
 p.updateCourse[0].ok(row('A'));
 assert.equal(s.dialogVisible,true); assert.equal(s.form.courseId,'B'); assert.equal(s.form.title,'Course B');
});
test('whitespace-only create is rejected without transport', () => {
 const {state:s,pending:p}=setup('CourseManagement'); s.openCreate(); Object.assign(s.form,row('A'),{title:'   '}); s.submit();
 assert.equal(p.createCourse,undefined);
});
test('update excludes immutable course key', () => {
 const {state:s,pending:p}=setup('CourseManagement'); s.openEdit(row('A')); s.submit();
 assert.equal(Object.hasOwn(p.updateCourse[0].args[1],'courseKey'),false);
});

test('unknown create outcome reads all courses then UUID before allowing another write', () => {
 const {state:s,pending:p,messages}=setup('CourseManagement'); s.openCreate(); Object.assign(s.form,row('A')); s.submit();
 p.createCourse[0].fail('timeout',{status:0}); s.submit(); assert.equal(p.createCourse.length,1);
 p.getCourseList.at(-1).ok([row('A')]); p.getCourse.at(-1).ok(row('A'));
 assert.equal(p.createCourse.length,1); assert.equal(s.dialogVisible,true); assert.equal(messages.some(m=>m[0]==='success'),false);
});
test('duplicate key maps server field error and retains draft', () => {
 const {state:s,pending:p}=setup('CourseManagement'); s.openCreate(); Object.assign(s.form,row('A')); s.submit();
 p.createCourse[0].fail('duplicate',{status:409,data:{details:{field:'courseKey',reason:'duplicate'}}});
 assert.ok(s.formErrors.courseKey); assert.equal(s.form.title,'Course A'); assert.equal(s.dialogVisible,true);
});

for (const action of ['create','edit']) test(`lesson ${action} guards duplicates and preserves new dialog`, () => {
 const {state:s,pending:p}=setup('CourseLessons');
 if(action==='edit') s.openMetadata({lessonId:'A',lessonKey:'lesson-a',title:'A',locale:'en-US',ageBand:'4-6'});
 else {s.openCreate(); Object.assign(s.form,{lessonKey:'lesson-a',title:'A',locale:'en-US',ageBand:'4-6'});}
 s.submit();s.submit(); const calls=p[action==='edit'?'updateLesson':'createLesson']; assert.equal(calls.length,1);
 s.dialogVisible=false; s.openCreate(); s.form.title='B'; calls[0].ok({lessonId:'A'});
 assert.equal(s.dialogVisible,true);assert.equal(s.form.title,'B');
});
test('template handler guards duplicates', () => {
 const {state:s,pending:p}=setup('CourseManagement'); s.toggleTemplate(row('A'));s.toggleTemplate(row('A')); assert.equal(p.setTemplate.length,1);
});
test('uncertain readback failure remains locked against write replay', () => {
 const {state:s,pending:p}=setup('CourseManagement'); s.openCreate();Object.assign(s.form,row('A'));s.submit();p.createCourse[0].fail('timeout',{status:0});
 p.getCourseList.at(-1).fail('offline',{status:0});s.submit();assert.equal(p.createCourse.length,1);assert.equal(p.getCourseList.length,2);
});

test('encoded request size beyond canonical JSON parser limit is rejected locally', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'),{title:'é'.repeat(60000)});s.submit();
 assert.equal(p.createCourse,undefined);assert.ok(s.formNotice);
});
for (const field of ['courseKey','title','locale']) for (const value of ['  ','x\0y',{}]) test(`invalid ${field} ${JSON.stringify(value)} has field error`, () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'),{[field]:value});s.submit();assert.equal(p.createCourse,undefined);assert.ok(s.formErrors[field]);
});
for (const band of ['19-20','6-4','4 - 6','K-2']) test(`invalid ageBand ${band} never writes`, () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'),{ageBand:band});s.submit();assert.equal(p.createCourse,undefined);assert.ok(s.formErrors.ageBand);
});
test('unicode metadata and custom locale are preserved without invented field maxima', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'),{courseKey:'Khóa học',title:'é'.repeat(1000),locale:'custom',ageBand:' 00-18 '});s.submit();assert.equal(p.createCourse[0].args[0].locale,'custom');assert.equal(p.createCourse[0].args[0].courseKey,'Khóa học');
});

test('clone source and trimmed readback identity survive reopen after response loss', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openClone(row('A'));s.cloneForm.courseKey=' copy ';s.doClone();s.cloneVisible=false;s.openClone(row('B'));
 p.cloneCourse[0].fail('lost',{status:0});p.getCourseList.at(-1).ok([{...row('copy'),courseKey:'copy'}]);p.getCourse.at(-1).ok(row('copy'));
 assert.equal(p.cloneCourse[0].args[0],'A');assert.equal(s.cloneVisible,true);assert.equal(s.cloneSource.courseId,'B');assert.equal(s.cloneFoundCourse,null);
});
test('empty authoritative key lookup allows only a subsequent deliberate retry', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'));s.submit();p.createCourse[0].fail('lost',{status:0});p.getCourseList[0].ok([]);
 assert.equal(p.createCourse.length,1);s.submit();assert.equal(p.createCourse.length,2);
});
test('delete captures target and prevents duplicate confirmations', async () => {
 const {state:s,pending:p}=setup('CourseManagement');let confirmations=0;let confirm;s.$confirm=()=>{confirmations++;return new Promise(ok=>{confirm=ok;});};
 const a=row('A');s.confirmDelete(a);s.confirmDelete(a);assert.equal(confirmations,1);a.courseId='B';confirm();await Promise.resolve();assert.equal(p.deleteCourse[0].args[0],'A');
 p.deleteCourse[0].fail('nonempty',{status:409,data:{details:{reason:'nonempty'}}});assert.equal(s.actionPending['delete:A'],false);
});
test('reviewed existing metadata requires explicit approval before another PATCH', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openEdit(row('A'));s.submit();p.updateCourse[0].fail('lost',{status:0});p.getCourse[0].ok(row('A'));
 s.submit();p.getCourse[1].ok(row('A'));assert.equal(p.updateCourse.length,1);s.allowReviewedRetry();s.submit();assert.equal(p.updateCourse.length,2);
});

test('uncertain lesson deletion reconciles UUID before any repeated DELETE', async () => {
 const {state:s,pending:p}=setup('CourseLessons');s.$confirm=()=>Promise.resolve();const lesson={lessonId:'A',lessonKey:'lesson-a'};
 s.confirmDelete(lesson);await Promise.resolve();p.deleteLesson[0].fail('lost',{status:0});s.confirmDelete(lesson);await Promise.resolve();assert.equal(p.deleteLesson.length,1);assert.equal(p.getLesson[0].args[0],'A');
});

test('reconciliation adapter rejects non-exhaustive list shape rather than proving absence', () => {
 const source=fs.readFileSync(path.join(__dirname,'../../src/apis/module/course.js'),'utf8').replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','return');
 let request;const adapter=new Function('getNestUrl','nestRequest','normalizeCourse',source)(()=>'/v1/admin',r=>{request=r;},r=>r);
 let rows,failed;adapter.getCourseList(r=>{rows=r;},(msg,res)=>{failed=res;});request.onSuccess({items:[],hasMore:true});assert.equal(rows,undefined);assert.ok(failed);
});

for (const excess of [0,1]) test(`encoded JSON 100KiB boundary plus ${excess} bytes`, () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openCreate();Object.assign(s.form,row('A'),{title:''});
 const body={courseKey:s.form.courseKey,title:'',locale:s.form.locale,ageBand:s.form.ageBand};
 s.form.title='x'.repeat(102400-new TextEncoder().encode(JSON.stringify(body)).length+excess);s.submit();
 assert.equal(Boolean(p.createCourse),excess===0);
});
for (const outcome of ['ok','fail']) test(`destroyed course view ignores mutation ${outcome}`, () => {
 const {state:s,component,pending:p,messages}=setup('CourseManagement');s.openEdit(row('A'));s.submit();component.beforeDestroy.call(s);
 p.updateCourse[0][outcome](outcome==='ok'?row('A'):'late',{status:400});assert.equal(messages.length,0);assert.equal(p.getCourseList,undefined);
});

test('lesson invalid numeric age range is blocked at the field', () => {
 const {state:s,pending:p}=setup('CourseLessons');s.openCreate();Object.assign(s.form,{lessonKey:'lesson-a',title:'A',locale:'custom',ageBand:'6-4'});s.submit();assert.equal(p.createLesson,undefined);assert.ok(s.formErrors.ageBand);
});

test('row action readback cannot overwrite an unrelated dialog notice', () => {
 const {state:s,pending:p}=setup('CourseManagement');s.openEdit(row('B'));s.formNotice='B notice';s.toggleTemplate(row('A'));p.setTemplate[0].fail('lost',{status:0});p.getCourse[0].ok(row('A'));assert.equal(s.formNotice,'B notice');
});
