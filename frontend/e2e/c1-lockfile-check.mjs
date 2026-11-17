// npm lockfile integrity check for C1 runs (PR #71 review F-1).
// Verifies, after `npm ci` plus the exact-version, no-save Playwright install:
//   - every lockfile entry is registry-resolved with an integrity hash;
//   - every installed package matches its locked version exactly;
//   - locked non-optional packages are all present;
//   - the only extra top-level packages are the pinned Playwright test runner;
//   - package.json and package-lock.json are unmodified (git).
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'

const frontend = process.cwd()
const lock = JSON.parse(readFileSync(join(frontend, 'package-lock.json'), 'utf8'))
const PLAYWRIGHT_VERSION = '1.65.1'
const allowedExtras = new Map([['@playwright/test', PLAYWRIGHT_VERSION], ['playwright', PLAYWRIGHT_VERSION], ['playwright-core', PLAYWRIGHT_VERSION]])
const problems = []

if (lock.lockfileVersion < 2) problems.push('lockfileVersion ' + lock.lockfileVersion + ' lacks a packages map')
let checked = 0
for (const [path, entry] of Object.entries(lock.packages ?? {})) {
  if (path === '' || entry.link) continue
  if (!/^https:\/\/registry\.npmjs\.org\//.test(entry.resolved ?? '')) problems.push(path + ': non-registry resolved')
  if (!/^sha(?:512|384|256)-/.test(entry.integrity ?? '')) problems.push(path + ': missing integrity')
  const manifest = join(frontend, path, 'package.json')
  if (!existsSync(manifest)) {
    if (!entry.optional && !entry.devOptional) problems.push(path + ': locked package not installed')
    continue
  }
  const installed = JSON.parse(readFileSync(manifest, 'utf8')).version
  if (installed !== entry.version) problems.push(path + ': installed ' + installed + ' != locked ' + entry.version)
  checked++
}

const locked = new Set(Object.keys(lock.packages ?? {}))
const top = join(frontend, 'node_modules')
for (const name of readdirSync(top)) {
  if (name.startsWith('.')) continue
  const names = name.startsWith('@') ? readdirSync(join(top, name)).map(n => name + '/' + n) : [name]
  for (const pkg of names) {
    if (locked.has('node_modules/' + pkg)) continue
    const version = existsSync(join(top, pkg, 'package.json')) ? JSON.parse(readFileSync(join(top, pkg, 'package.json'), 'utf8')).version : 'unknown'
    if (allowedExtras.get(pkg) !== version) problems.push('node_modules/' + pkg + '@' + version + ': not in lockfile')
  }
}

try {
  execFileSync('git', ['diff', '--quiet', 'HEAD', '--', 'package.json', 'package-lock.json'], { cwd: frontend, stdio: 'pipe' })
} catch {
  problems.push('package.json or package-lock.json modified relative to HEAD')
}

if (problems.length) {
  console.error('C1 LOCKFILE INTEGRITY: FAIL\n  ' + problems.slice(0, 40).join('\n  '))
  process.exit(1)
}
console.log('C1 LOCKFILE INTEGRITY: PASS (' + checked + ' installed packages match package-lock.json; extras limited to @playwright/test ' + PLAYWRIGHT_VERSION + ')')
