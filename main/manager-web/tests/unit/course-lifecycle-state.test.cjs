const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const row = (id='A', status='draft', revision='a'.repeat(32)) => ({courseId:id,courseKey:'course-'+id,title:'Course '+id,status,revision});
function setup() {
 const pending={},messages=[];
 const api={course:new Proxy({}, {get:(_,method)=>(...args)=>(pending[method]||=[]).push({args,ok:args.at(-2),fail:args.at(-1)})})};
 const source=fs.readFileSync(path.join(__dirname,'../../src/views/CourseManagement.vue'),'utf8');
 const script=source.split('<script>')[1].split('</script>')[0].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','return');
 const component=new Function('Api','HeaderBar','AGE_BANDS','LOCALES','DEFAULT_AGE_BAND','DEFAULT_LOCALE','validateCourseForm','mutationDetails','uncertainMutation',script)(api,{},[],[],'4-6','en-US',...Object.values(require('../../src/utils/courseForm.cjs')));
 const s={...component.data(),$t:(k,v)=>k+(v?JSON.stringify(v):''),$message:{success:m=>messages.push(m)}};
 Object.entries(component.methods).forEach(([k,f])=>s[k]=f.bind(s));
 Object.entries(component.computed||{}).forEach(([k,f])=>Object.defineProperty(s,k,{get:()=>f.call(s)}));
 return {s,p:pending,messages,component};
}
function opened(status='published') { const c=setup(); c.s.openLifecycle(row(),status); c.p.getCourse[0].ok(row()); return c; }
test('UUID course read retains the server revision dropped by the old normalizer',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../../src/apis/module/course.js'),'utf8').replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g,'').replace('export default','return');
 let request,result;const api=new Function('getNestUrl','nestRequest','normalizeCourse',source)(()=>'/v1/admin',r=>request=r,raw=>({courseId:raw.id,status:raw.status}));
 api.getCourse('A',r=>result=r,()=>{});request.onSuccess({id:'A',status:'draft',revision:'a'.repeat(32)});assert.equal(result.revision,'a'.repeat(32));
});
test('confirmation loads authoritative identity and revision before any write',()=>{
 const {s,p}=setup();s.openLifecycle(row(),'published');s.confirmLifecycle();assert.equal(p.transitionCourse,undefined);p.getCourse[0].ok(row('A','archived'));assert.equal(s.lifecycle.course.status,'archived');
});
for(const status of ['draft','published','archived']) test(`transition ${status} captures revision and prevents duplicate confirmation`,()=>{
 const {s,p}=opened(status);s.confirmLifecycle();s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);assert.deepEqual(p.transitionCourse[0].args.slice(0,3),['A',status,'a'.repeat(32)]);
});
test('PATCH completion requires GET readback before success and uses persisted state',()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();p.transitionCourse[0].ok(row('A','published'));assert.equal(messages.length,0);assert.equal(s.lifecycleVisible,true);
 p.getCourse[1].ok(row('A','published','b'.repeat(32)));assert.equal(messages.length,1);assert.equal(s.lifecycleVisible,false);assert.equal(p.getCourseList.length,1);
});
for(const reason of ['no_published_lessons','lesson_not_playable']) test(`eligibility ${reason} preserves visible server reason and repair identity`,()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();p.transitionCourse[0].fail('canonical reason',{status:422,data:{details:{reason,lessonKey:'broken',lessonVersion:2}}});
 assert.ok(s.lifecycle.notice.includes('canonical reason'));assert.equal(s.lifecycle.details.lessonKey,'broken');assert.equal(s.lifecycleVisible,true);assert.equal(messages.length,0);
});
test('stale conflict refreshes but never auto-replays with the new revision',()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();p.transitionCourse[0].fail('stale',{status:409,data:{details:{reason:'stale',currentRevision:'b'.repeat(32)}}});
 s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);p.getCourse[1].ok(row('A','archived','b'.repeat(32)));assert.equal(s.lifecycle.review.status,'archived');s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);
 s.reviewLifecycle();s.confirmLifecycle();assert.equal(p.transitionCourse.length,2);assert.equal(p.transitionCourse[1].args[2],'b'.repeat(32));assert.equal(messages.length,0);
});
test('lost response reads back without claiming attributable success or replay',()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();p.transitionCourse[0].fail('lost',{status:0});p.getCourse[1].ok(row('A','published','b'.repeat(32)));s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);assert.equal(messages.length,0);assert.equal(s.lifecycle.review.status,'published');
});
test('failed readback retains write lock across dialog reopen',()=>{
 const {s,p}=opened();s.confirmLifecycle();p.transitionCourse[0].fail('lost',{status:503});p.getCourse[1].fail('offline',{status:0});s.lifecycleVisible=false;s.openLifecycle(row(),'published');p.getCourse[2].ok(row('A','published','b'.repeat(32)));s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);assert.equal(s.lifecycle.needsReview,true);
});
for(const outcome of ['ok','fail']) test(`late A ${outcome} cannot close or overwrite B lifecycle dialog`,()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();s.lifecycleVisible=false;s.openLifecycle(row('B'),'archived');p.getCourse[1].ok(row('B'));
 p.transitionCourse[0][outcome](outcome==='ok'?row('A','published'):'late',{status:422});if(outcome==='ok')p.getCourse[2].ok(row('A','published','b'.repeat(32)));
 assert.equal(s.lifecycle.course.courseId,'B');assert.equal(s.lifecycleVisible,true);assert.equal(s.lifecycle.notice,'');assert.equal(messages.length,0);
});
for(const revision of ['',null,'INVALID']) test(`missing/malformed revision ${revision} never permits legacy unguarded PATCH`,()=>{
 const {s,p}=setup();s.openLifecycle(row(),'published');p.getCourse[0].ok(row('A','draft',revision));s.confirmLifecycle();assert.equal(p.transitionCourse,undefined);assert.ok(s.lifecycle.notice);
});
test('successful PATCH followed by contradictory readback is review, never invented success',()=>{
 const {s,p,messages}=opened();s.confirmLifecycle();p.transitionCourse[0].ok(row('A','published'));p.getCourse[1].ok(row('A','archived','b'.repeat(32)));assert.equal(messages.length,0);assert.equal(s.lifecycle.needsReview,true);
});
test('destroyed view suppresses late lifecycle side effects',()=>{
 const {s,p,messages,component}=opened();s.confirmLifecycle();component.beforeDestroy.call(s);p.transitionCourse[0].ok(row('A','published'));assert.equal(p.getCourse.length,1);assert.equal(messages.length,0);
});
test('unsupported status is refused before reads/writes',()=>{const {s,p}=setup();s.openLifecycle(row(),'deleted');assert.equal(p.getCourse,undefined);assert.equal(p.transitionCourse,undefined);});
test('opening the same course while its GET is pending cannot create an older read that unlocks a write',()=>{
 const {s,p}=setup();s.openLifecycle(row(),'published');s.openLifecycle(row(),'archived');assert.equal(p.getCourse.length,1);
 p.getCourse[0].ok(row());s.confirmLifecycle();assert.equal(p.transitionCourse[0].args[1],'published');
});
test('malformed post-write read stays uncertain across reopen',()=>{
 const {s,p}=opened();s.confirmLifecycle();p.transitionCourse[0].ok(row('A','published'));p.getCourse[1].ok(row('A','published',''));s.lifecycleVisible=false;s.openLifecycle(row(),'archived');p.getCourse[2].ok(row('A','published','b'.repeat(32)));s.confirmLifecycle();assert.equal(p.transitionCourse.length,1);
});
test('retryable serving refusal remains visible alongside uncertainty/readback guidance',()=>{
 const {s,p}=opened();s.confirmLifecycle();p.transitionCourse[0].fail('Course Mode v2 serving is disabled',{status:503,data:{retryable:true}});assert.ok(s.lifecycle.notice.includes('Course Mode v2 serving is disabled'));
});
