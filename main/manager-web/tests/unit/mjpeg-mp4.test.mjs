import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseMjpegMp4, validateJpegDimensions, MjpegError} from '../../src/components/lesson/mjpeg-mp4.mjs';
const u32=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const box=(type,...parts)=>{const b=Buffer.concat(parts);return Buffer.concat([u32(b.length+8),Buffer.from(type),b]);};
const full=(type,...parts)=>box(type,u32(0),...parts);
function fixture(n=3,chunkSize=1,wide=false) {
 const samples=Array.from({length:n},(_,i)=>Buffer.from([255,216,...Array(i+1).fill(i),255,217]));
 const ftyp=box('ftyp',Buffer.from('isom'),u32(0)),mdat=box('mdat',...samples);
 const offsets=[];let offset=ftyp.length+8;
 samples.forEach((b,i)=>{if(i%chunkSize===0)offsets.push(offset);offset+=b.length;});
 const header=full('mvhd',u32(0),u32(0),u32(1000),u32(n*100));
 const entry=Buffer.alloc(78);entry.writeUInt16BE(1,6);entry.writeUInt16BE(150,24);entry.writeUInt16BE(150,26);
 const rows=[u32(1),u32(Math.min(chunkSize,n)),u32(1)];if(n%chunkSize&&n>chunkSize)rows.push(u32(offsets.length),u32(n%chunkSize),u32(1));
 const stbl=box('stbl',full('stsd',u32(1),box('mp4v',entry)),full('stts',u32(1),u32(n),u32(100)),full('stsc',u32(rows.length/3),...rows),full('stsz',u32(0),u32(n),...samples.map(b=>u32(b.length))),full(wide?'co64':'stco',u32(offsets.length),...offsets.map(x=>wide?Buffer.concat([u32(0),u32(x)]):u32(x))));
 const moov=box('moov',header,box('trak',full('tkhd'),box('mdia',full('mdhd',u32(0),u32(0),u32(1000),u32(n*100)),full('hdlr',u32(0),Buffer.from('vide')),box('minf',full('vmhd'),stbl))));
 return {bytes:Buffer.concat([ftyp,mdat,moov]),samples,offsets};
}
test('generated chunk partitions preserve every sample and timestamp (seed 0x7411)',()=>{
 let seed=0x7411;for(let i=0;i<200;i++){seed=(Math.imul(seed,1664525)+1013904223)>>>0;const n=1+seed%100,k=1+(seed>>>8)%n;const f=fixture(n,k,i%2===0),p=parseMjpegMp4(f.bytes);assert.equal(p.samples.length,n);assert.equal(p.durationMs,n*100);assert.equal(p.fps,10);p.samples.forEach((s,j)=>{assert.deepEqual(f.bytes.subarray(s.offset,s.offset+s.size),f.samples[j]);assert.equal(s.ptsTicks,j*100);});}
});
test('every proper truncation of a canonical small clip is rejected',()=>{const {bytes}=fixture();for(let n=0;n<bytes.length;n++)assert.throws(()=>parseMjpegMp4(bytes.subarray(0,n)),MjpegError);});
test('reject unsafe offsets, sample bombs, dimensions, timing, audio and ambiguous boxes',()=>{
 const cases=[['stco',12,0xffffffff],['stsz',12,901],['stsz',16,1048577],['stts',16,0],['mdhd',20,0],['mvhd',20,99],['stsc',20,2]];
 for(const [tag,relative,value]of cases){const b=Buffer.from(fixture().bytes);const start=b.indexOf(tag)-4;b.writeUInt32BE(value,start+relative);assert.throws(()=>parseMjpegMp4(b),MjpegError,tag);}
 for(const tag of ['moof','sidx','mdat','ftyp'])assert.throws(()=>parseMjpegMp4(Buffer.concat([fixture().bytes,box(tag)])),MjpegError);
 for(const [before,after]of [['vide','soun'],['mp4v','avc1'],['stts','ctts']]){const b=Buffer.from(fixture().bytes);b.write(after,b.indexOf(before));assert.throws(()=>parseMjpegMp4(b),MjpegError);}
 const wide=fixture(3,1,true).bytes;wide.writeUInt32BE(0x200000,wide.indexOf('co64')+12);assert.throws(()=>parseMjpegMp4(wide),MjpegError);
 const size=Buffer.from(fixture().bytes);size.writeUInt32BE(0,0);assert.throws(()=>parseMjpegMp4(size),MjpegError);
});
test('all accepted sample ranges stay ordered and bounded under deterministic mutation',()=>{
 let seed=19;const original=fixture(7,3).bytes;for(let i=0;i<1000;i++){seed=(Math.imul(seed,1103515245)+12345)>>>0;const b=Buffer.from(original);b[seed%b.length]^=1<<(seed%8);try{const p=parseMjpegMp4(b);let end=0;for(const s of p.samples){assert.ok(s.offset>=end&&s.size>0&&s.offset+s.size<=b.length);end=s.offset+s.size;}}catch(e){assert.ok(e instanceof MjpegError,e.stack);}}
});
test('JPEG SOF dimensions must agree before allocating decoded pixels',()=>{
 const jpeg=Uint8Array.from([255,216,255,192,0,8,8,0,150,0,150,1,255,217]);validateJpegDimensions(jpeg,150,150);assert.throws(()=>validateJpegDimensions(jpeg,1920,1080),MjpegError);jpeg[8]=255;assert.throws(()=>validateJpegDimensions(jpeg,150,150),MjpegError);
});
export {fixture,box,u32};
