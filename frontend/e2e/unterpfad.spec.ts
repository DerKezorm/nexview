/**
 * Läuft Nexview unter einem Unterpfad hinter dem Reverse Proxy?
 *
 * Der Nutzer dieses Features betreibt Nexview unter
 * `https://domain.tld/nexview/` - eine Umgebung, die es in keiner
 * Entwicklungs-Installation gibt. Deshalb baut die Playwright-Konfiguration
 * sie hier nach: das Backend mit `NEXVIEW_URL_BASE=/nexview` und dem
 * **gebauten** Frontend, davor ein Mini-Pförtner in beiden Betriebsarten
 * (durchreichen und abschneiden, siehe `unterpfad-proxy.mjs`).
 *
 * Der Pförtner beantwortet alles außerhalb von /nexview mit 404 - fragt die
 * Seite auch nur ein Bild an der Domain-Wurzel an, bricht der Test. Genau so
 * fällt es sonst erst beim Fremden auf.
 */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'
import {
  UNTERPFAD_BACKEND_PORT,
  UNTERPFAD_DATEN,
  PROXY_ABSCHNEIDEN_PORT,
  PROXY_DURCHREICHEN_PORT,
} from './unterpfad-ports'

const hier = path.dirname(fileURLToPath(import.meta.url))

/** Das Backend direkt, am Pförtner vorbei - nur für die Test-Vorbereitung. */
const DIREKT = `http://127.0.0.1:${UNTERPFAD_BACKEND_PORT}`
const DURCHREICHEN = `http://127.0.0.1:${PROXY_DURCHREICHEN_PORT}`
const ABSCHNEIDEN = `http://127.0.0.1:${PROXY_ABSCHNEIDEN_PORT}`

/**
 * Ohne gebautes Frontend gibt es nichts auszuliefern. In der CI läuft der
 * Bau immer vor den Tests; lokal wird übersprungen statt rot - aber laut,
 * mit dem Handgriff in der Begründung.
 */
const distDa = existsSync(path.join(hier, '..', 'dist', 'index.html'))
test.skip(!distDa && !process.env.CI, 'frontend/dist fehlt - erst `npm run build`, dann wieder `npm run e2e`.')

test.beforeAll(async ({ request }) => {
  const status = await request.get(`${DIREKT}/api/setup/status`)
  expect(status.ok(), 'Das Unterpfad-Backend antwortet nicht.').toBeTruthy()
  if (!(await status.json()).needs_setup) return

  // Konto anlegen und die Adresse bestätigen - derselbe Weg wie in
  // sitzung.spec.ts, nur gegen die eigene, frische Datenbank dieses Backends.
  const angelegt = await request.post(`${DIREKT}/api/setup/admin`, { data: KONTO })
  expect(angelegt.ok(), await angelegt.text()).toBeTruthy()

  const link = execFileSync(
    PYTHON,
    [path.join(WURZEL, 'frontend', 'e2e', 'bestaetigungslink.py'), KONTO.email],
    {
      encoding: 'utf8',
      cwd: path.join(WURZEL, 'backend'),
      env: { ...process.env, NEXVIEW_DATA_DIR: UNTERPFAD_DATEN },
    },
  ).trim()

  const bestaetigt = await request.post(`${DIREKT}/api/onboarding/verify/${link}`)
  expect(bestaetigt.ok(), await bestaetigt.text()).toBeTruthy()
})

/** Anmelden über den angegebenen Pförtner - bis die Navigation da ist. */
async function anmelden(page: Page, ursprung: string): Promise<void> {
  await page.goto(`${ursprung}/nexview/`)
  await page.getByLabel('Username or e-mail').fill(KONTO.username)
  await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
}

test('⚠️ durchreichender Proxy: anmelden, neu laden, tiefe Adresse - alles unter /nexview', async ({
  page,
  context,
}) => {
  // Jede Anfrage, die den Vorbau verliert, landet beim Pförtner im 404 -
  // hier wird mitgeschrieben, ob es eine gab.
  const ohneVorbau: string[] = []
  page.on('request', (anfrage) => {
    const adresse = new URL(anfrage.url())
    if (adresse.origin === DURCHREICHEN && !adresse.pathname.startsWith('/nexview')) {
      ohneVorbau.push(adresse.pathname)
    }
  })

  await anmelden(page, DURCHREICHEN)

  // ⚠️ Der Kern des Features: Das Cookie trägt den Vorbau im Pfad. Ohne ihn
  // schickte der Browser es nie zurück, und jedes Neuladen meldete ab.
  const sitzung = (await context.cookies()).find((c) => c.name === 'nexview_refresh')
  expect(sitzung, 'Kein Sitzungs-Cookie gesetzt.').toBeTruthy()
  expect(sitzung!.path, 'Das Cookie kennt den Unterpfad nicht.').toBe('/nexview/api/auth')

  // Neuladen überstehen = der Browser hat das Cookie wirklich mitgeschickt.
  await page.reload()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()

  // Eine tiefe Adresse direkt öffnen: SPA-Rückfall unter dem Vorbau.
  await page.goto(`${DURCHREICHEN}/nexview/profil`)
  await expect(page.getByRole('tab', { name: 'Security' })).toBeVisible()

  expect(ohneVorbau, 'Anfragen ohne Vorbau - sie liefen am Proxy vorbei.').toEqual([])
})

test('abschneidender Proxy: dieselbe Anmeldung funktioniert auch ohne Kopfzeilen-Hilfe', async ({
  page,
}) => {
  // Der Pförtner entfernt /nexview, bevor das Backend die Anfrage sieht.
  // Aus Sicht des Browsers ändert sich nichts - genau das ist der Beweis.
  await anmelden(page, ABSCHNEIDEN)
  await page.reload()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
})

test('Direktzugriff ohne Vorbau wird in den Unterpfad umgelenkt', async ({ page }) => {
  // Wer das Backend bei gesetzter Basis direkt öffnet (am Proxy vorbei),
  // wird im Browser auf die Adresse mit Vorbau umgelenkt - serverseitig
  // ginge das nicht, ohne hinter abschneidenden Proxys einen
  // Umleitungs-Kreisverkehr zu bauen (siehe lib/basis.ts).
  await page.goto(`${DIREKT}/`)
  await page.waitForURL(`${DIREKT}/nexview/`)

  await page.goto(`${DIREKT}/profil`)
  await page.waitForURL(`${DIREKT}/nexview/profil`)
})
