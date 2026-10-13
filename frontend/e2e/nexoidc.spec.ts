/**
 * Pflichttests 33 und 34 (Bauplan „Anmeldung mit OIDC und authentik“, 05) für
 * den Ende-zu-Ende-Lauf. Angelegt aus der Vorlage des Anmeldemoduls
 * (`bauplaene/oidc/tests-fuer-apps/frontend/nexoidc.spec.ts`), dann Nexviews
 * Datei.
 *
 * Der Anbieter ist der Nachbau `e2e/fake-oidc.mjs` (startet die
 * Konfiguration): Discovery, Schlüssel, Token und userinfo mit einem Schlüssel,
 * den er beim Start erzeugt; beim Anmelden schickt er den Browser sofort mit
 * einem Code zurück. Nie ein echter Anbieter, nie das Netz.
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { KONTO } from './konto'

const PROVIDER = 'http://127.0.0.1:8794'
const ANMELDUNG = '/admin/settings?reiter=anmeldung'

test.beforeAll(async ({ request }) => {
  const status = await request.get('/api/setup/status')
  expect(status.ok(), 'Das Backend antwortet nicht.').toBeTruthy()
  if (!(await status.json()).needs_setup) return
  const angelegt = await request.post('/api/setup/admin', { data: KONTO })
  expect(angelegt.ok(), await angelegt.text()).toBeTruthy()
})

async function signInAsOperator(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(KONTO.username)
  await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
}

/** Ein Zugangs-Token des Betreibers für Aufrufe neben der Oberfläche. */
async function betreiberKopf(request: APIRequestContext): Promise<Record<string, string>> {
  const antwort = await request.post('/api/auth/login', {
    data: { username: KONTO.username, password: KONTO.password },
  })
  expect(antwort.ok(), await antwort.text()).toBeTruthy()
  return { Authorization: `Bearer ${(await antwort.json()).access_token}` }
}

test('34: add, edit and remove a provider; the authentik card lists its steps', async ({ page }) => {
  await signInAsOperator(page)
  await page.goto(ANMELDUNG)
  await page.getByRole('button', { name: /Add a provider|Anbieter hinzufügen/ }).click()
  const formular = page.getByRole('dialog')
  await formular.getByLabel(/Name on the button|Name auf dem Knopf/).fill('Stand-in')
  await formular.getByLabel(/^Issuer$|Aussteller/).fill(PROVIDER)
  await formular.getByLabel(/Client ID|Client-ID/).fill('e2e-client')
  await formular.getByLabel(/Client secret|Client-Geheimnis/).fill(`e2e-${Date.now()}`)
  // Die Rückleitadresse steht schon vor dem Speichern zum Kopieren da.
  await expect(formular.getByTestId('rueckleitadresse')).toContainText('/api/oidc/stand-in/callback')
  await formular.getByRole('button', { name: /^Save$|^Speichern$/ }).click()
  const liste = page.getByTestId('anbieter-liste')
  await expect(liste.getByText('Stand-in', { exact: true })).toBeVisible()
  await expect(liste.getByText(PROVIDER)).toBeVisible()
  await liste.getByRole('button', { name: /^Edit$|^Bearbeiten$/ }).first().click()
  await page.getByRole('dialog').getByLabel(/Name on the button|Name auf dem Knopf/).fill('Stand-in 2')
  await page.getByRole('dialog').getByRole('button', { name: /^Save$|^Speichern$/ }).click()
  await expect(liste.getByText('Stand-in 2', { exact: true })).toBeVisible()
  await liste.getByRole('button', { name: /^Remove$|^Entfernen$/ }).first().click()
  await expect(page.getByRole('dialog')).toContainText(/accounts lose the link|Konten verlieren die Verknüpfung/)
  await page.getByRole('dialog').getByRole('button', { name: /^Remove$|^Entfernen$/ }).click()
  await expect(page.getByText('Stand-in 2', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/No provider yet|Noch kein Anbieter/)).toBeVisible()
  // The authentik card: an address nobody answers at gives the step list with the first step failed.
  await page.getByLabel(/Address of authentik|Adresse von authentik/).fill('http://127.0.0.1:9')
  await page.getByLabel(/API token|API-Token/).fill(`e2e-${Date.now()}`)
  // Without a public address (a fresh app) the card suggests the browser's own and the button reads "Use it and set
  // up" (1.0.10); it stores the suggestion first.
  await page.getByRole('button', { name: /^Set up$|^Einrichten$|^Use it and set up$|^Übernehmen und einrichten$/ }).click()
  await expect(page.getByText(/Connection to authentik|Verbindung zu authentik/)).toBeVisible()
  await expect(page.getByText(/cannot be reached at this address|unter dieser Adresse nicht erreichbar/)).toBeVisible()
})

test('33: sign-in through the provider button ends in a session', async ({ page, request }) => {
  const kopf = await betreiberKopf(request)
  // Die Rückleitadresse führt über die Oberfläche (Vite reicht /api weiter).
  const adresse = await request.put('/api/settings', {
    headers: kopf,
    data: { public_url: test.info().project.use.baseURL ?? 'http://127.0.0.1:5599' },
  })
  expect(adresse.ok(), await adresse.text()).toBeTruthy()
  const angelegt = await request.post('/api/oidc/admin/providers', {
    headers: kopf,
    data: {
      label: 'Stand-in',
      slug: 'stand-in',
      issuer: PROVIDER,
      client_id: 'e2e-client',
      client_secret: `e2e-${Date.now()}`,
    },
  })
  expect(angelegt.status(), await angelegt.text()).toBe(201)

  try {
    // Das Konto verknüpft sich selbst: Mein Konto, Sicherheit, „Verknüpfen“ mit dem Passwort.
    await signInAsOperator(page)
    await page.goto('/profil?reiter=sicherheit')
    const zeile = page.getByTestId('meine-anbieter').getByRole('listitem').filter({ hasText: 'Stand-in' })
    await expect(zeile.getByText('Not linked')).toBeVisible()
    await zeile.getByRole('button', { name: 'Link' }).click()
    await page.getByRole('dialog').getByLabel('Your password').fill(KONTO.password)
    await page.getByRole('dialog').getByRole('button', { name: 'Link' }).click()
    await expect(page.getByText(/Linked to Stand-in/)).toBeVisible()
    await expect(zeile.getByText('Linked', { exact: true })).toBeVisible()

    // ⚠️ Das Abmelden kommt erst beim Server an, wenn der Anbieter zurückgeleitet hat (spätestens nach zwei
    // Sekunden). So lief es am 13.10.2026 in CI von selbst: Die Antwort des Abmeldens löschte das Cookie der neuen
    // Sitzung, und der Browser stand wieder auf der Anmeldeseite. Wartet der Knopf, kommt es nie so weit.
    let losLassen: () => void = () => {}
    const gehalten = new Promise<void>((fertig) => (losLassen = fertig))
    page.on('response', (antwort) => {
      if (antwort.url().includes('/api/oidc/stand-in/callback')) losLassen()
    })
    await page.route('**/api/auth/logout', async (route) => {
      await Promise.race([gehalten, new Promise((fertig) => setTimeout(fertig, 2000))])
      await route.continue()
    })

    // Abmelden, dann über den Knopf auf der Anmeldeseite wieder hinein.
    await page.getByRole('button', { name: KONTO.username, exact: true }).click()
    await page.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible()
    await page.getByRole('button', { name: /Sign in with|Mit .* anmelden/ }).first().click()
    await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
    await expect(page).not.toHaveURL(/error=/)
    // Und die Sitzung steckt im Cookie, nicht nur im Arbeitsspeicher.
    await page.reload()
    await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
  } finally {
    // Aufräumen für die übrigen Specs, auch nach einem Fehlschlag: Sonst steht der Anbieter in allen weiteren, und
    // ihr „Sign in“ trifft zwei Knöpfe. Der Anbieter geht samt Verknüpfung.
    const liste = await request.get('/api/oidc/admin/providers', { headers: kopf })
    for (const eintrag of (await liste.json()) as { id: number }[]) {
      await request.delete(`/api/oidc/admin/providers/${eintrag.id}`, { headers: kopf })
    }
  }
})
