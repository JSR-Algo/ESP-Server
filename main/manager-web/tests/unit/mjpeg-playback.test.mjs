import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {MjpegPlayback} from '../../src/components/lesson/mjpeg-playback.mjs';
const data=readFileSync(new URL('../fixtures/mjpeg/current-flyIn.mp4',import.meta.url));
const identity=JSON.parse(readFileSync(new URL('../fixtures/mjpeg/current-flyIn.json',import.meta.url)));
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
function setup(options={}){let closed=0,draws=0,clock=0;const errors=[];
 const player=new MjpegPlayback({src:'/exact.mp4',identity,mode:'once',now:()=>clock,fetcher:async()=>new Response(data),crypto:webcrypto,decode:async()=>({width:240,height:240,close(){closed++;}}),present(){draws++;return true;},onError:e=>errors.push(e.message),...options});
 const tick=player.tick.bind(player);player.tick=t=>{clock=t;tick(t);};
 return {player,errors,get closed(){return closed;},get draws(){return draws;}};
}
test('verified first presented frame establishes readiness and closes bitmap',async()=>{const t=setup();await t.player.load();assert.equal(t.player.state().ready,true);assert.equal(t.draws,1);assert.equal(t.closed,1);t.player.dispose();assert.equal(t.player.state().ready,false);});
test('digest, length and metadata mismatch fail before any decode',async()=>{for(const change of [{sha256:'0'.repeat(64)},{bytes:data.length-1},{metadata:{...identity.metadata,width:1}}]){const t=setup({identity:{...identity,...change}});await assert.rejects(t.player.load());assert.equal(t.draws,0);assert.equal(t.closed,0);t.player.dispose();}});
test('declared length bounds streaming even when content length lies',async()=>{let cancelled=false;const t=setup({identity:{...identity,bytes:4},fetcher:async()=>new Response(new ReadableStream({pull(c){c.enqueue(data);},cancel(){cancelled=true;}}))});await assert.rejects(t.player.load());assert.ok(cancelled);});
test('duplicate play preserves clock and pending decode consumes no wall time',async()=>{const t=setup();await t.player.load();let release;t.player.decode=()=>new Promise(r=>release=r);t.player.play();t.player.tick(1000);t.player.play();t.player.tick(1100);await flush();t.player.tick(10000);assert.equal(t.player.state().currentTimeSec,0);release({width:240,height:240,close(){}});await t.player.settled();assert.equal(t.player.state().currentTimeSec,.1);t.player.pause();t.player.tick(11000);assert.equal(t.player.state().currentTimeSec,.1);t.player.play();t.player.tick(12000);t.player.tick(12100);await flush();release({width:240,height:240,close(){}});await t.player.settled();assert.equal(t.player.state().currentTimeSec,.2);});
test('seek supersedes stale decode, serializes work and presents only latest request',async()=>{let release,active=0,maxActive=0;const t=setup();await t.player.load();t.player.decode=()=>{active++;maxActive=Math.max(active,maxActive);return new Promise(r=>{release=()=>{active--;r({width:240,height:240,close(){}});};});};t.player.seek(.5);await flush();t.player.seek(1);release();await flush();assert.equal(t.player.state().currentTimeSec,0);assert.equal(t.draws,1);release();await t.player.settled();assert.equal(t.player.state().currentTimeSec,1);assert.equal(maxActive,1);});
test('dispose fences stale decode and error, closes images and releases buffers',async()=>{let release;const t=setup();await t.player.load();t.player.decode=()=>new Promise(r=>release=r);t.player.seek(.5);await flush();const done=t.player.dispose();release({width:240,height:240,close(){}});await done;assert.equal(t.draws,1);assert.equal(t.player.bytes,null);assert.deepEqual(t.errors,[]);});
test('once final hold, loop wrap and explicit replay follow sample timestamps',async()=>{for(const mode of ['once','loop']){const t=setup({mode});await t.player.load();t.player.seek(3.1);await t.player.settled();t.player.play();t.player.tick(0);t.player.tick(200);await t.player.settled();assert.equal(t.player.state().ended,mode==='once');assert.ok(Math.abs(t.player.state().currentTimeSec-(mode==='once'?3.2:.1))<1e-9);t.player.seek(0);await t.player.settled();assert.equal(t.player.state().ended,false);assert.equal(t.player.state().currentTimeSec,0);t.player.dispose();}});
test('empty presentation and decode rejection fail visibly without ready state',async()=>{for(const options of [{present:()=>false},{decode:async()=>{throw Error('bad JPEG');}}]){const t=setup(options);await assert.rejects(t.player.load());assert.equal(t.player.state().ready,false);t.player.dispose();}});

test('replacement layers cannot overlap browser bitmap allocations',async()=>{
 const a=setup(),b=setup();await a.player.load();await b.player.load();let release,active=0,maxActive=0;
 a.player.decode=()=>{active++;maxActive=Math.max(active,maxActive);return new Promise(r=>release=()=>r({width:240,height:240,close(){active--;}}));};
 b.player.decode=async()=>{active++;maxActive=Math.max(active,maxActive);return{width:240,height:240,close(){active--;}};};
 a.player.seek(.5);await flush();a.player.dispose();b.player.seek(.6);await flush();assert.equal(maxActive,1);release();await Promise.all([a.player.settled(),b.player.settled()]);assert.equal(active,0);assert.equal(b.player.time,.6);
});
