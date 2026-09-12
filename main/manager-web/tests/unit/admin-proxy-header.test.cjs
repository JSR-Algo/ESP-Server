const {test}=require('node:test');
const assert=require('node:assert/strict');
process.env.REWARDS_ADMIN_BROWSER_E2E='1';
const config=require('../../vue.config');
test('session-only proxy omits an unconfigured admin key',()=>{
 const headers={'x-nest-authorization':'Bearer local-session','x-tbot-admin-key':'untrusted-browser-key'};
 const proxy={setHeader:(key,value)=>{headers[key.toLowerCase()]=value},getHeader:key=>headers[key.toLowerCase()],removeHeader:key=>{delete headers[key.toLowerCase()]}};
 config.devServer.proxy['/nestjs'].onProxyReq(proxy);
 assert.equal(headers.authorization,'Bearer local-session');assert.equal(headers['x-tbot-admin-key'],undefined);assert.equal(headers['x-nest-authorization'],undefined);
});
