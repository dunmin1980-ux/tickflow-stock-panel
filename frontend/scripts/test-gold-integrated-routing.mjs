import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8')
}

const router = read('../src/router.tsx')
const layout = read('../src/components/Layout.tsx')

assert.match(router, /path:\s*['\"]gold['\"]/)
assert.match(router, /lazy.*GoldWorkspace|GoldWorkspace.*lazy/s)
assert.doesNotMatch(router, /GoldShadowLayout/)
assert.match(layout, /中金黄金/)
assert.match(layout, /api\.goldStatus/)
assert.match(layout, /goldStatus\?\.enabled/)
