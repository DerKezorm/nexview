/**
 * Der Weg ohne Rückweg: von Radarr und Sonarr auf nexcrate (Bauplan 7.3).
 *
 * Sieben Schritte, ein Browser, ein echtes Backend — und eine nexcrate, die
 * nur so viel kann, wie der Assistent fragt (`nexcrate-attrappe.mjs`). Was die
 * Vitest-Tests prüfen, ist jeder Schritt für sich gegen eine ersetzte
 * API-Schicht. Dass die Kette hält, beweist erst das hier: dass der Server die
 * Zuordnung annimmt, dass die Sicherung wirklich auf der Platte landet, dass
 * das Umschalten die Betriebsart des ganzen Hauses ändert.
 *
 * ⚠️ **Dieser Lauf lässt die Installation im NEX-Betrieb zurück.** Alle Specs
 * teilen sich ein Backend und eine Datenbank; Playwright arbeitet sie in der
 * Reihenfolge ihrer Dateinamen ab. `umsteiger` steht hinter `anfrage-stellen`,
 * `hausordnung`, `kind-wuenscht`, `regel-entscheidet` und `sitzung` — die
 * brauchen alle noch Radarr. Danach kommt nur `unterpfad`, und das hat ein
 * eigenes Backend mit eigener Datenbank. **Wer diese Datei umbenennt oder eine
 * neue davorstellt, muss das nachrechnen.**
 *
 * ⚠️ **Der Umstieg ist einmalig.** Ein zweiter Lauf auf derselben Datenbank
 * bekommt `409 not_in_this_mode` — der Spec prüft das ausdrücklich, statt es
 * als Fehlschlag zu erleben.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'
import { attrappeStarten, SCHLUESSEL } from './nexcrate-attrappe.mjs'

const DATEN = { NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') }

/** Port 9 lehnt Verbindungen sofort ab — ein Radarr, das nie antwortet. */
const RADARR_INS_LEERE = 'http://127.0.0.1:9'

let nexcrate: { url: string; stoppen: () => Promise<unknown> }

async function verwalterToken(request: APIRequestContext): Promise<string> {
  const antwort = await request.post('/api/auth/login', {
    data: { username: KONTO.username, password: KONTO.password },
  })
  expect(antwort.ok(), await antwort.text()).toBeTruthy()
  return (await antwort.json()).access_token as string
}

async function anmelden(page: Page) {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(KONTO.username)
  await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
}

test.beforeAll(async ({ request }) => {
  nexcrate = await attrappeStarten()

  const status = await request.get('/api/setup/status')
  expect(status.ok(), 'Das Backend antwortet nicht.').toBeTruthy()
  if ((await status.json()).needs_setup) {
    const angelegt = await request.post('/api/setup/admin', { data: KONTO })
    expect(angelegt.ok(), await angelegt.text()).toBeTruthy()
    const link = execFileSync(
      PYTHON,
      [path.join(WURZEL, 'frontend', 'e2e', 'bestaetigungslink.py'), KONTO.email],
      { encoding: 'utf8', cwd: path.join(WURZEL, 'backend'), env: { ...process.env, ...DATEN } },
    ).trim()
    const bestaetigt = await request.post(`/api/onboarding/verify/${link}`)
    expect(bestaetigt.ok(), await bestaetigt.text()).toBeTruthy()
  }

  const kopf = { Authorization: `Bearer ${await verwalterToken(request)}` }
  // ⚠️ Radarr muss eingetragen sein, damit es etwas zu verlassen gibt.
  // Erreichbar sein muss es nicht: ``weg_verlassen`` hält eine stumme Instanz
  // aus und sagt es im Bericht.
  const eingestellt = await request.put('/api/settings', {
    headers: kopf,
    data: {
      beschaffung: 'arr',
      radarr_url: RADARR_INS_LEERE,
      radarr_api_key: 'e2e-kein-echter-schluessel',
      nexcrate_url: nexcrate.url,
      nexcrate_api_key: SCHLUESSEL,
    },
  })
  expect(eingestellt.ok(), await eingestellt.text()).toBeTruthy()

  const probe = await request.get('/api/umstieg/abbildung', { headers: kopf })
  expect(probe.ok(), await probe.text()).toBeTruthy()
  const daten = await probe.json()
  expect(JSON.stringify(daten)).toContain('v_6a0763e8')
})

test.afterAll(async () => {
  await nexcrate?.stoppen()
})

test('der Betreiber steigt über den Assistenten auf nexcrate um', async ({ page, request }) => {
  // ⚠️ **Eine Adresse des Assistenten, die 500 antwortet, ist ein Fehlschlag.**
  // Der erste Lauf fand genau so einen: `GET /vorab` scheiterte an einer
  // Zählung, und der Test lief trotzdem durch, weil die Seite daneben ihren
  // festen Satz zeigte. Seitdem hört er mit.
  const kaputt: string[] = []
  page.on('response', (a) => {
    if (a.url().includes('/api/umstieg/') && a.status() >= 500) {
      kaputt.push(`${a.status()} ${a.url()}`)
    }
  })
  await anmelden(page)
  // ⚠️ **Deutsche Wörter in der Adresse**, englische Werte im Code – wie in
  // `hausordnung` und `regel-entscheidet`. `?unter=` führt direkt in den
  // Unterreiter, ohne erst zu klicken.
  //
  // ⚠️ **Die Dienste-Seite braucht länger als die Vorgabe von fünf Sekunden.**
  // Sie wartet auf die Einstellungen und auf die Regionenliste, und die geht
  // hier ins Leere (kein TMDB-Schlüssel). Deshalb hier ausdrücklich warten,
  // statt am ersten Satz des Assistenten zu scheitern.
  await page.goto('/admin/settings?reiter=dienste&unter=umstieg')
  await expect(page.getByRole('heading', { name: 'What changes' })).toBeVisible({
    timeout: 30_000,
  })


  // Schritt 1: die Zahlen und der Satz, der alles Weitere trägt.
  await expect(page.getByText('There is no way back except the backup.')).toBeVisible()
  await expect(page.getByText(/storage (entry|entries) will be rewritten/)).toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  // Schritt 2: verbunden ist schon, die Standprüfung muss schweigen.
  await expect(page.getByRole('heading', { name: 'Connect to nexcrate' })).toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  // Schritt 3: der Vorschlag steht, ohne dass jemand etwas wählt.
  const ziel = page.getByLabel('Radarr', { exact: true })
  await expect(ziel).toHaveValue('v_6a0763e8')
  await page.getByRole('button', { name: 'Check' }).click()

  // Schritt 4: eine frische Installation hat nichts, was hängt.
  await expect(page.getByRole('heading', { name: 'Does nexcrate know the titles?' })).toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  // Schritt 5: ohne Sicherung geht es nicht weiter.
  await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled()
  await page.getByRole('button', { name: 'Create backup' }).click()
  await expect(page.getByText(/^Created: /)).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Schritt 6: der eine Schritt, der sich nicht zurücknehmen lässt.
  await page.getByRole('button', { name: 'Switch now' }).click()
  await expect(page.getByRole('heading', { name: 'Switched' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('4 versions from nexcrate are in place.')).toBeVisible()

  // Schritt 7: nachreichen, auch wenn es nichts nachzureichen gibt.
  await page.getByRole('button', { name: 'Hand over approved requests' }).click()
  await expect(page.getByText(/requests? handed over/)).toBeVisible()

  // Und danach: Die Installation beschafft über nexcrate, Radarrs Zugang ist weg.
  const kopf = { Authorization: `Bearer ${await verwalterToken(request)}` }
  const einstellungen = await (await request.get('/api/settings', { headers: kopf })).json()
  expect(einstellungen.beschaffung).toBe('nex')
  expect(einstellungen.radarr_url).toBe('')

  // ⚠️ Ein zweiter Umstieg schriebe Kennungen um, die schon stimmen.
  const nochmal = await request.get('/api/umstieg/vorab', { headers: kopf })
  expect(nochmal.status()).toBe(409)

  expect(kaputt, 'Eine Adresse des Assistenten hat mit 5xx geantwortet.').toEqual([])
})
