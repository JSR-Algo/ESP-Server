const assert = require('assert');
const { readFileSync } = require('fs');
const { resolve } = require('path');

const source = readFileSync(resolve(__dirname, '../src/views/Login.vue'), 'utf8');
assert.match(
  source,
  /setTimeout\(\(\) => \{\s*if \(this\.\$route\.name === ['"]login['"]\) this\.fetchCaptcha\(\);\s*\}, 1000\);/,
  'post-login captcha refresh must not redirect a user who already navigated away from Login',
);

console.log('login captcha refresh navigation race guard PASS');
