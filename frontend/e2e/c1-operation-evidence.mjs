// Test-only evidence gates. Nothing here changes the application or its egress policy.
const permissionSuccess = 'Delegated API permission acquired. User provisioning is still required.'
const microsoftAuthority = 'https://login.microsoftonline.com'

export async function requireFreshPermission(button, status, expect) {
  // The previous silent probe calls MSAL directly and does not update the UI.
  // If success is already showing, this click cannot prove it caused that text.
  const before = (await status.textContent({ timeout: 5000 }))?.trim()
  if (!before || before === permissionSuccess) {
    throw new Error('Cannot establish a fresh API permission result')
  }
  await button.click()
  await expect(status).toHaveText(permissionSuccess)
  await expect(button).toBeEnabled()
}

export async function requireMicrosoftPopupOrigin(popup, timeout = 15000) {
  try {
    await popup.waitForURL(url => url.origin === microsoftAuthority, {
      waitUntil: 'commit',
      timeout,
    })
    // Verify the actual document origin, not merely a requested navigation URL.
    // Export only a boolean and discard errors that could include OAuth URLs.
    const reached = await popup.evaluate(expected => window.location.origin === expected,
      microsoftAuthority)
    if (reached === true && !popup.isClosed()) return
  } catch {
    // Report a fixed message below.
  }
  throw new Error('Microsoft authority was not verified before popup cancellation')
}
