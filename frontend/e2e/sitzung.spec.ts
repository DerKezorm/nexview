/**
 * Bleibt man angemeldet, wenn man die Seite neu lädt?
 *
 * Klingt nach einer Kleinigkeit, ist aber der Punkt, an dem die ganze
 * Anmeldung hängt. Seit dem Umbau liegt der Nachweis, dass man angemeldet ist,
 * nicht mehr im `localStorage`, sondern in einem Cookie, das JavaScript **gar
 * nicht lesen kann** (`HttpOnly`). Der Vorteil: Kein fremdes Skript kommt
 * heran. Der Preis: Ob es funktioniert, weiß nur der Browser - er entscheidet
 * anhand von `SameSite` und `Path`, ob er das Cookie beim nächsten Aufruf
 * mitschickt.
 *
 * Genau das kann ein Test ohne Browser nicht beweisen. Deshalb dieser eine
 * hier, mit echtem Chromium und echtem Server.
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

  // ⚠️ **Auch der erste Administrator bestätigt seine Adresse.** Das ist
  // Absicht: Ein Tippfehler in der Adresse fiele sonst erst auf, wenn er
  // jemanden aussperrt. Der Assistent lässt sich deshalb ohne funktionierenden
  // Mailserver gar nicht abschließen.
  //
  // Ein echter SMTP-Server wäre für einen Test aber die falsche Abhängigkeit.
  // Ersetzt wird darum nur das Postfach - den Link holt ein kurzes Skript aus
  // derselben Stelle, aus der ihn auch die Mail bekäme. Bestätigt wird danach
  // über die reguläre Route.
  const link = execFileSync(PYTHON, [path.join(WURZEL, 'frontend', 'e2e', 'bestaetigungslink.py'), KONTO.email], {
    encoding: 'utf8',
    cwd: path.join(WURZEL, 'backend'),
    env: { ...process.env, NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') },
  }).trim()

  const bestaetigt = await request.post(`/api/onboarding/verify/${link}`)
  expect(bestaetigt.ok(), await bestaetigt.text()).toBeTruthy()
})

test('⚠️ die Anmeldung übersteht ein Neuladen - das Abmelden nimmt sie wieder weg', async ({
  page,
  context,
}) => {
  await page.goto('/')

  // --- Anmelden -----------------------------------------------------------
  await page.getByLabel('Username or e-mail').fill(KONTO.username)
  await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
  await page.getByRole('button', { name: 'Sign in' }).click()

  // Drin: Die Navigation der Erwachsenen ist da. `.first()`, weil die
  // Kopfzeile sie zweimal trägt - breit und schmal.
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()

  // --- Was den Browser jetzt angemeldet hält -------------------------------
  const cookies = await context.cookies()
  const sitzung = cookies.find((c) => c.name === 'nexview_refresh')
  expect(sitzung, 'Kein Sitzungs-Cookie gesetzt.').toBeTruthy()
  // ⚠️ Die drei Eigenschaften **sind** der Schutz. Fällt eine weg, merkt man
  // es sonst erst, wenn jemand sie ausnutzt.
  expect(sitzung!.httpOnly, 'Das Cookie wäre für fremde Skripte lesbar.').toBe(true)
  expect(sitzung!.sameSite, 'Das Cookie ginge auch von fremden Seiten mit.').toBe('Lax')
  expect(sitzung!.path, 'Das Cookie ginge an jeden Pfad mit.').toBe('/api/auth')

  // Und der Gegenbeweis: Im Speicher der Seite liegt nichts Dauerhaftes.
  const imSpeicher = await page.evaluate(() =>
    Object.keys(localStorage).filter((k) => /token|refresh/i.test(k)),
  )
  expect(imSpeicher, 'Es liegt doch wieder ein Token im localStorage.').toEqual([])

  // --- Neu laden ----------------------------------------------------------
  await page.reload()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign in' })).toHaveCount(0)

  // --- Abmelden -----------------------------------------------------------
  // Der Knopf zum Benutzermenü trägt den eigenen Namen - nicht „Menü öffnen".
  await page.getByRole('button', { name: KONTO.username, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()

  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Browse', exact: true })).toHaveCount(0)

  // ⚠️ **Was hier bewiesen ist - und was nicht.** Dass man nach dem Abmelden
  // draußen ist, heißt bis hierher nur: Der Browser hat das Cookie
  // weggeworfen. Ob der **Server** die Sitzung vergessen hat, steht damit
  // nicht fest. Der Test darunter fragt genau das - und ist noch nicht grün.
})

test.fixme(
  '⚠️ ein abgegriffenes Cookie gilt nach dem Abmelden weiter',
  async ({ page, context }) => {
    // ═══ Ein offener Punkt, kein kaputter Test ═══
    //
    // Das Abmelden löscht das Cookie **nur im Browser**. Serverseitig bleibt
    // das Erneuerungs-Token gültig, bis es von selbst abläuft - und das dauert
    // 30 Tage (`refresh_token_days`). Wer eine Kopie des Cookies hat, kommt
    // damit einen Monat lang weiter hinein, ganz gleich wie oft sich der
    // Besitzer abmeldet. Ungültig wird es einzig durch einen Passwortwechsel.
    //
    // Wie schlimm ist das? Der Alltagsfall ist in Ordnung: Auf einem fremden
    // Rechner nimmt das Abmelden das Cookie mit, dort bleibt nichts liegen.
    // Die Lücke trifft den Fall danach - wenn jemand vermutet, dass seine
    // Sitzung abgegriffen wurde, und sich abmeldet, um sie zu beenden. Das
    // tut dann nichts. Der einzige Weg, der wirklich wirkt, ist das Passwort
    // zu ändern.
    //
    // Der Weg dorthin ist eine Entscheidung, keine Kleinigkeit: Ein Stempel je
    // Konto (wie beim Passwortwechsel) würde beim Abmelden **alle** Geräte
    // hinauswerfen; eine Merkliste beendeter Token trifft nur diese eine
    // Sitzung, braucht aber eine Tabelle und deren Aufräumen. Deshalb steht
    // hier `fixme` und nicht schon eine Lösung.
    await page.goto('/')
    await page.getByLabel('Username or e-mail').fill(KONTO.username)
    await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()

    const kopie = (await context.cookies()).find((c) => c.name === 'nexview_refresh')!

    await page.getByRole('button', { name: KONTO.username, exact: true }).click()
    await page.getByRole('menuitem', { name: 'Sign out' }).click()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()

    // Zurückgelegt und noch einmal geklopft. Bleibt die Tür zu, hat der Server
    // die Sitzung wirklich vergessen.
    await context.addCookies([kopie])
    await page.reload()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  },
)
