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

  // ⚠️ **Was hier bewiesen ist - und was ausdrücklich nicht.**
  //
  // Dass man nach dem Abmelden draußen ist, heißt nur: Der Browser hat das
  // Cookie weggeworfen. Der **Server** hat die Sitzung *nicht* vergessen - das
  // Erneuerungs-Token bleibt gültig, bis es von selbst abläuft (30 Tage). Wer
  // vorher eine Kopie gezogen hat, kommt damit weiter herein.
  //
  // **Das ist so gewollt.** Würde jedes Abmelden alle Sitzungen beenden, flöge
  // man beim Abmelden auf dem Handy auch vom Fernseher. Für den Fall, dass man
  // wirklich alle beenden will, gibt es seit 0.22 „Überall abmelden" - der
  // Test darunter prüft genau das.
  //
  // **Offen bleibt die feine Variante:** Abmelden entwertet *genau diese eine*
  // Sitzung, ohne die anderen anzufassen. Dafür bräuchte es eine Merkliste
  // beendeter Token - siehe den Docstring von ``logout`` in ``routers/auth.py``.
  // Solange die fehlt, steht diese Einschränkung hier, damit sie nicht in
  // Vergessenheit gerät.
})

test('⚠️ „Überall abmelden" entwertet auch ein abgegriffenes Cookie', async ({
  page,
  context,
}) => {
  // ═══ Warum es diesen Test gibt ═══
  //
  // Er stand hier lange als `fixme`, weil er eine Lücke beschrieb: Das
  // gewöhnliche Abmelden nimmt das Cookie **nur aus diesem Browser**.
  // Serverseitig blieb das Erneuerungs-Token gültig, bis es von selbst ablief -
  // bis zu 30 Tage. Wer eine Kopie hatte, kam damit weiter herein, und der
  // einzige Riegel war ein Passwortwechsel. Man musste also sein Passwort
  // ändern, obwohl mit dem Passwort nichts war.
  //
  // Seit 0.22 gibt es den passenden Ausweg, und **das** prüft dieser Test:
  // „Überall abmelden" setzt eine Grenze am Konto, hinter der jedes ältere
  // Token verfällt - auch eins, das längst jemand anderes hat.
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(KONTO.username)
  await page.getByLabel('Password', { exact: true }).fill(KONTO.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()

  // Die Kopie, die jemand abgegriffen haben könnte.
  const kopie = (await context.cookies()).find((c) => c.name === 'nexview_refresh')!
  expect(kopie, 'Kein Sitzungs-Cookie zum Kopieren.').toBeTruthy()

  await page.goto('/profil')
  // ⚠️ Seit 0.22 liegt der Knopf eine Ebene tiefer: „Konto" hat ein
  // Untermenue bekommen, und „Ueberall abmelden" steht unter „Sicherheit".
  // Ohne diesen Klick findet der Test den Knopf nicht - und das waere ein
  // Testfehler, der wie eine kaputte Funktion aussieht.
  await page.getByRole('tab', { name: 'Security' }).click()
  await page.getByRole('button', { name: 'Sign out everywhere' }).click()
  await page.getByRole('button', { name: 'Sign out everywhere' }).last().click()
  await expect(page.getByText(/all other devices have been signed out/i)).toBeVisible()

  // ⚠️ Der eigentliche Beweis: Die Kopie zurücklegen und noch einmal klopfen.
  // Vorher stand die Tür damit offen.
  await context.clearCookies()
  await context.addCookies([kopie])
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Browse', exact: true })).toHaveCount(0)
})
