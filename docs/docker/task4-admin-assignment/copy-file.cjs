'use strict';

const { copyFile, rm, stat } = require('node:fs/promises');

async function replaceWithCopy(source, destination, copy = copyFile) {
  await stat(source);
  await rm(destination, { force: true });
  await copy(source, destination);
}

module.exports = { replaceWithCopy };
