import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from '@playwright/test'

export const WEBRTC_UDP_POLICY_ARG = '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'
export const headedWebrtcLaunchOptions = { executablePath: fileURLToPath(new URL('./c1-webrtc-chromium.sh', import.meta.url)) }

// Playwright supplies a fresh profile for each launch. Seed Chromium's
// profile preference before the shell launcher execs Chromium.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const args = process.argv.slice(2)
  const dirs = args.filter(arg => arg.startsWith('--user-data-dir='))
  if (dirs.length !== 1 || !dirs[0].startsWith('--user-data-dir=/')) {
    throw new Error('L-2 requires one absolute disposable Chromium profile')
  }
  const profile = join(dirs[0].slice('--user-data-dir='.length), 'Default')
  mkdirSync(profile, { mode: 0o700 })
  writeFileSync(join(profile, 'Preferences'), JSON.stringify({
    webrtc: { ip_handling_policy: 'disable_non_proxied_udp' },
  }), { mode: 0o600, flag: 'wx' })
  const browser = chromium.executablePath()
  process.stdout.write(browser + '\n')
}
