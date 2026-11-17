import { test, expect } from '@playwright/test'
const live = process.env.C1_LIVE === '1'
const configured = process.env.C1_SPA_CONFIGURED === '1'
const apiAudience = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
function leakGuard(page) {
  const incidents = []
  page.on('console', msg => {
    if (/Bearer\s+[\w.\-]+|eyJ[A-Za-z0-9_-]{20,}\.|client_secret\s*[:=]/i.test(msg.text())) incidents.push('Credential-like console output')
  })
  page.on('pageerror', error => {
    if (/Bearer\s+[\w.\-]+|eyJ[A-Za-z0-9_-]{20,}\./i.test(error.message)) incidents.push('Credential-like page error')
  })
  return incidents
}
test('unconfigured SPA fails closed', async ({ page }) => {
  test.skip(configured, 'Requires unconfigured SPA environment')
  const incidents = leakGuard(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeDisabled()
  await expect(page.getByRole('status').first()).toContainText('registration is required')
  expect(incidents).toEqual([])
})
test('redirect origin and API scopes restricted', async ({ page }) => {
  await page.goto('/')
  const config = await page.evaluate(async () => {
    const { getRedirectUri, apiScopes } = await import('/src/entraAuth.ts')
    let rejected = false
    try { getRedirectUri('https://untrusted.example') } catch { rejected = true }
    return { rejected, redirect: getRedirectUri('http://localhost:5173'), scopes: apiScopes }
  })
  expect(config.rejected).toBe(true)
  expect(config.redirect).toBe('http://localhost:5173')
  expect(config.scopes).toEqual(['api://' + apiAudience + '/access_as_user'])
})
test('LIVE supervised login, scope/audience, silent token and logout', async ({ page }) => {
  test.skip(!live || !configured, 'LIVE C1 incomplete; requires real account and registered SPA')
  const incidents = leakGuard(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // complete actual Microsoft popup before resuming inspector
  await expect(page.getByText(/^Signed in:/)).toBeVisible()
  const verdict = await page.evaluate(async () => {
    const { acquireApiToken } = await import('/src/entraAuth.ts')
    const result = await acquireApiToken()
    const part = result?.accessToken?.split('.')[1]
    if (!part) return { valid: false }
    const base64 = part.replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '=')))
    return { valid: (claims.aud === 'api://a5a09b74-aead-455f-9de7-9b844c8a8465' || claims.aud === 'a5a09b74-aead-455f-9de7-9b844c8a8465') && String(claims.scp || '').split(' ').includes('access_as_user') }
  })
  expect(verdict.valid).toBe(true)
  let popupCount = 0
  page.on('popup', () => { popupCount++ })
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('Delegated API permission acquired')
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('Delegated API permission acquired')
  expect(popupCount).toBe(0)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.pause() // complete logout popup if needed
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeVisible()
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  expect(incidents).toEqual([])
})
test('LIVE cancelled login does not report success', async ({ page }) => {
  test.skip(!live || !configured, 'LIVE cancellation must be manually executed')
  const incidents = leakGuard(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // cancel popup, then resume
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeVisible()
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await expect(page.getByRole('status').first()).toContainText('failed')
  expect(incidents).toEqual([])
})
