/**
 * Der ganze Weg der Hausordnung – schreiben, lesen, abhaken.
 *
 * Was die 213 Vitest-Tests nicht können: den Weg über beide Seiten hinweg.
 * Der Text wird im Editor geschrieben, landet in der Datenbank, kommt über
 * `/api/config` zurück, lässt den Knopf erscheinen, wird im Fenster angezeigt
 * und beim Abhaken am Konto vermerkt – und **danach ist der Knopf weg**.
 *
 * Genau diese Kette hat sechs Glieder in vier Dateien. Jedes einzelne ist
 * geprüft; dass sie zusammenpassen, beweist erst dieser Lauf.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect, test } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'

test.beforeAll(async ({ request }) => {
  const status = await request.get('/api/setup/status')
  expect(status.ok(), 'Das Backend antwortet nicht.').toBeTruthy()
  if (!(await status.json()).needs_setup) return

  const angelegt = await request.post('/api/setup/admin', { data: KONTO })
  expect(angelegt.ok(), await angelegt.text()).toBeTruthy()

  const link = execFileSync(
    PYTHON,
    [path.join(WURZEL, 'frontend', 'e2e', 'bestaetigungslink.py'), KONTO.email],
    {
      encoding: 'utf8',
      cwd: path.join(WURZEL, 'backend'),
      env: { ...process.env, NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') },
    },
  ).trim()
  const bestaetigt = await request.post(`/api/onboarding/verify/${link}`)
  expect(bestaetigt.ok(), await bestaetigt.text()).toBeTruthy()
})

const TEXT = '## The rules\n\nPlease read this before you request anything.'

/**
 * ⚠️ **Der Administrator taugt hier nicht als Leser.** Er ist von der
 * Hausordnung ausgenommen - er schreibt sie ja. Gelesen wird deshalb mit
 * einem gewöhnlichen Konto.
 */
const LESER = { username: 'e2e-leser', password: 'Ein-langes-Passwort-1234' }

function leserAnlegen(): void {
  execFileSync(
    PYTHON,
    [path.join(WURZEL, 'frontend', 'e2e', 'konto_anlegen.py'), LESER.username, LESER.password],
    {
      cwd: path.join(WURZEL, 'backend'),
      env: { ...process.env, NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') },
    },
  )
}

async function anmelden(
  page: import('@playwright/test').Page,
  konto: { username: string; password: string },
) {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(konto.username)
  await page.getByLabel('Password', { exact: true }).fill(konto.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

test('⚠️ vom Editor bis zum Abhaken – und danach ist der Knopf weg', async ({ page }) => {
  // ⚠️ **Die Konsole zählt mit.** Eine zu enge Inhaltsregel (CSP) zeigt keine
  // Fehlermeldung, sondern eine halb geladene Seite - und meldet sich nur
  // hier. Genau das ist der Grund, warum es `tools/konsole-pruefen.mjs` gibt;
  // für die Hausordnung läuft die Prüfung gleich mit.
  //
  // ⚠️ **Ein Rauschen wird ausgeklammert, und nur eines:** In dieser Umgebung
  // gibt es keinen TMDB-Schlüssel, deshalb antwortet der Server auf die
  // Anbieter-Abfrage mit 401 - und der Browser schreibt das in die Konsole.
  // Das ist die Testumgebung, kein Befund. Alles andere zählt, insbesondere
  // abgewiesene Inhalte (CSP) und Ausnahmen aus dem Programm selbst: Beide
  // melden sich mit eigenen Texten und gehen hier nicht durch.
  const RAUSCHEN = /401 \(Unauthorized\)/
  const konsole: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error' && !RAUSCHEN.test(m.text())) konsole.push(m.text())
  })
  page.on('pageerror', (f) => konsole.push(String(f)))

  await anmelden(page, KONTO)
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()

  // --- Ohne Hausordnung gibt es keinen Knopf -------------------------------
  await expect(page.getByRole('button', { name: 'Open the house rules' })).toHaveCount(0)

  // --- Schreiben -----------------------------------------------------------
  await page.goto('/admin/settings?reiter=hausordnung')
  await page.getByLabel('Heading').fill('How things work here')
  await page.getByPlaceholder('Write your rules here', { exact: false }).fill(TEXT)

  // Die Vorschau daneben zeigt das Ergebnis sofort – noch vor dem Speichern.
  await expect(page.getByRole('heading', { name: 'The rules' })).toBeVisible()

  // --- Speichern und der Zustand des Knopfes danach -------------------------
  const speichern = page.getByRole('button', { name: 'Save' })
  // Vor dem Tippen war nichts zu speichern; jetzt schon.
  await expect(speichern).toBeEnabled()
  await speichern.click()
  await expect(page.getByText('Saved.')).toBeVisible()
  // ⚠️ Danach ist nichts mehr offen - ein Knopf, der weiter einlädt, lässt
  // einen rätseln, ob das Speichern überhaupt angekommen ist.
  await expect(speichern).toBeDisabled()

  // Der Haken „alle müssen erneut lesen" zählt als Änderung, obwohl er nicht
  // zum Text gehört - sonst ließe er sich allein gar nicht speichern.
  await page.getByLabel('Everyone has to read it again').check()
  await expect(speichern).toBeEnabled()
  await page.getByLabel('Everyone has to read it again').uncheck()
  await expect(speichern).toBeDisabled()

  // --- Entwurf bleibt unsichtbar -------------------------------------------
  await page.goto('/')
  // Noch nicht veröffentlicht: kein Knopf, kein Verweis in der Fußzeile.
  await expect(page.getByRole('button', { name: 'Open the house rules' })).toHaveCount(0)

  // --- Veröffentlichen ------------------------------------------------------
  await page.goto('/admin/settings?reiter=hausordnung')
  await page.getByLabel('Published').check()
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved.')).toBeVisible()

  // --- Der Knopf ist da - beim gewöhnlichen Konto ---------------------------
  // Der Administrator selbst sieht ihn nie: Er hat die Regeln geschrieben.
  await expect(page.getByRole('button', { name: 'Open the house rules' })).toHaveCount(0)

  leserAnlegen()
  await page.getByRole('button', { name: KONTO.username, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await anmelden(page, LESER)

  const knopf = page.getByRole('button', { name: 'Open the house rules' })
  await expect(knopf).toBeVisible()
  await expect(knopf).toHaveText('§')

  // --- Lesen und abhaken ----------------------------------------------------
  await knopf.click()
  const fenster = page.getByRole('dialog')
  await expect(fenster).toBeVisible()
  await expect(fenster.getByRole('heading', { name: 'The rules' })).toBeVisible()
  await expect(fenster.getByText('Please read this before you request anything.')).toBeVisible()

  await fenster.getByRole('button', { name: 'Accept' }).click()

  // ⚠️ Der Kern: Der Knopf verschwindet - ohne Neuladen.
  await expect(knopf).toHaveCount(0)

  // --- Und er bleibt weg ----------------------------------------------------
  await page.reload()
  await expect(page.getByRole('button', { name: 'Open the house rules' })).toHaveCount(0)

  // --- Der dauerhafte Weg: die Fußzeile ------------------------------------
  // Ohne ihn wäre die Hausordnung nach dem Abhaken nicht mehr erreichbar.
  await page.getByRole('button', { name: 'House rules' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(
    page.getByRole('dialog').getByText('Please read this before you request anything.'),
  ).toBeVisible()
  // Abgehakt ist abgehakt - der Knopf dazu ist nicht mehr dabei.
  await expect(page.getByRole('dialog').getByRole('button', { name: 'Accept' })).toHaveCount(0)

  expect(konsole, 'Die Browserkonsole hat sich gemeldet.').toEqual([])
})

test('auf einem schmalen Bildschirm passt alles', async ({ page }) => {
  // ⚠️ Der Knopf sitzt fest unten rechts. Auf einem Telefon ist das die Ecke,
  // in der andere Anwendungen ihre Navigation haben - Nexview hat dort keine,
  // aber prüfen muss man es trotzdem.
  await page.setViewportSize({ width: 375, height: 812 })
  leserAnlegen()
  await anmelden(page, LESER)

  // Die Hausordnung steht aus dem Test davor noch – als quittierte Fassung.
  // Über die Fußzeile ist sie weiterhin erreichbar.
  await page.getByRole('button', { name: 'House rules' }).click()
  const fenster = page.getByRole('dialog')
  await expect(fenster).toBeVisible()

  // Nichts ragt seitlich heraus - weder die Seite noch das Fenster.
  const quer = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(quer, 'Die Seite lässt sich seitlich schieben.').toBe(false)

  const kasten = await fenster.boundingBox()
  expect(kasten!.width).toBeLessThanOrEqual(375)
})
