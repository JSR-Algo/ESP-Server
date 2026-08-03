import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const headerPath = path.resolve(scriptDir, '../src/components/HeaderBar.vue')
const source = fs.readFileSync(headerPath, 'utf8')

const headerContainerCss = source.match(/\.header-container\s*\{([^}]*)\}/)?.[1] || ''
const headerCenterCss = source.match(/\.header-center\s*\{([^}]*)\}/)?.[1] || ''

if (!/display:\s*grid/.test(headerContainerCss)) {
  throw new Error('Desktop header must use a grid so Home search cannot cover navigation')
}

if (!/grid-template-columns:/.test(headerContainerCss)) {
  throw new Error('Desktop header grid must reserve independent navigation space')
}

if (!/position:\s*static/.test(headerCenterCss) || !/transform:\s*none/.test(headerCenterCss)) {
  throw new Error('Desktop navigation must participate in layout instead of being absolutely overlaid')
}

console.log('Header navigation layout contract passed')
