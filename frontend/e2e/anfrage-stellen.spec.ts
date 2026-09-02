/**
 * Der gewöhnlichste Weg durch Nexview: jemand will einen Film.
 *
 * Suchen, öffnen, anfragen, nachsehen. Vier Schritte, die im Alltag jeder
 * geht, und die über sechs Schichten laufen: Suchfeld, TMDB-Ersatz im
 * Demo-Betrieb, Detailseite, Anfrageformular, `create_request`, eigene Liste.
 * Jede davon ist einzeln geprüft. Dass die Kette hält, beweist erst ein
 * Browser mit einem echten Server dahinter.
 *
 * ⚠️ **Warum hier kein Radarr steht und trotzdem eines eingetragen ist.**
 * `create_request` weist eine Filmanfrage mit 409 ab, solange `radarr_url`
 * und `radarr_api_key` leer sind - eine Anfrage, aus der nie etwas werden
 * kann, soll gar nicht erst entstehen. Eingetragen sein muss Radarr also.
 * Erreichbar sein muss es nicht: Die Adresse unten zeigt ins Leere, und der
 * Server fragt auf diesem Weg auch nirgends nach. Für den Zustand der Kachel
 * (`library.apply_status`) ist das ausdrücklich vorgesehen, ein Ausfall bleibt
 * dort eine Warnung; und Zielordner und Qualitätsprofil werden gar nicht erst
 * aufgelöst, weil die Einstellung darunter sie dem Entscheider überlässt.
 *
 * ⚠️ **Deshalb ist „der Entscheider wählt das Ziel" hier Pflicht und keine
 * Laune.** Ohne sie läuft `resolve_profile` in `_ziel_auswahl`, das holt die
 * Profilliste bei Radarr, und die Anfrage scheitert mit 502 - an der
 * Testumgebung, nicht an Nexview. Wer diese Zeile herausnimmt, misst nicht
 * mehr, was hier gemessen werden soll.
 *
 * ⚠️ **Freigegeben wird hier nichts.** Die Freigabe durch einen Administrator
 * schickt den Titel wirklich an Radarr; ohne Radarr scheitert sie. Geprüft ist
 * deshalb genau bis `pending_approval` - und das ist auch der Zustand, in dem
 * eine Anfrage in einer frischen Installation stehenbleibt.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'

/**
 * Das Konto, das anfragt.
 *
 * ⚠️ **Kein Administrator.** Der gibt sich selbst frei (`can_approve`), seine
 * Anfrage ginge sofort an Radarr und scheiterte dort. Gemessen werden soll
 * ohnehin der Normalfall: jemand ohne Sonderrechte, dessen Anfrage wartet.
 */
const ANFRAGER = { username: 'e2e-anfrager', password: 'Ein-langes-Passwort-1234' }

/**
 * Die Titel kommen aus `backend/app/mocks/demo_data.py` und stehen dort fest.
 * Zwei verschiedene, weil der zweite die Gegenprobe ist: Er darf in der
 * Anfrageliste **nicht** auftauchen. Ohne ihn bewiese ein Treffer auf den
 * ersten nur, dass irgendwo der richtige Text steht.
 */
const FILM = 'Nordlicht'
const NICHT_ANGEFRAGT = 'Tiefenrausch'

/**
 * Eine Adresse, an der nichts horcht.
 *
 * Auf dem Rückkanal (`127.0.0.1`) scheitert der Verbindungsversuch sofort,
 * statt in eine Zeitüberschreitung zu laufen; Namensauflösung ist gar nicht
 * erst nötig.
 *
 * ⚠️ **Der Port muss frei bleiben.** 8799 und 5599 gehören dem Haupt-Aufbau,
 * 8798 bis 8796 dem Unterpfad-Aufbau (siehe `playwright.config.ts` und
 * `unterpfad-ports.ts`). Stünde hier einer davon, redete Nexview mit sich
 * selbst statt ins Leere - beim ersten Versuch stand hier 8798, und im
 * Protokoll beantwortete das zweite Backend brav die Radarr-Anfragen.
 */
const RADARR_INS_LEERE = 'http://127.0.0.1:8795'

const DATEN = { NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') }

function konto(benutzername: string, passwort: string): void {
  execFileSync(
    PYTHON,
    [path.join(WURZEL, 'frontend', 'e2e', 'konto_anlegen.py'), benutzername, passwort],
    { cwd: path.join(WURZEL, 'backend'), env: { ...process.env, ...DATEN } },
  )
}

async function verwalterToken(request: APIRequestContext): Promise<string> {
  const antwort = await request.post('/api/auth/login', {
    data: { username: KONTO.username, password: KONTO.password },
  })
  expect(antwort.ok(), await antwort.text()).toBeTruthy()
  return (await antwort.json()).access_token as string
}

async function anmelden(page: Page, wer: { username: string; password: string }) {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(wer.username)
  await page.getByLabel('Password', { exact: true }).fill(wer.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // `.first()`, weil die Kopfzeile die Navigation zweimal trägt: breit und schmal.
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
}

test.beforeAll(async ({ request }) => {
  const status = await request.get('/api/setup/status')
  expect(status.ok(), 'Das Backend antwortet nicht.').toBeTruthy()

  // Die Einrichtung läuft nur beim ersten Lauf durch. Steht der Administrator
  // schon (weil eine andere Datei vorher dran war), ist hier nichts zu tun.
  if ((await status.json()).needs_setup) {
    const angelegt = await request.post('/api/setup/admin', { data: KONTO })
    expect(angelegt.ok(), await angelegt.text()).toBeTruthy()

    // Ohne bestätigte Adresse kommt auch der erste Administrator nicht hinein.
    // Den Link holt dasselbe Skript wie in den übrigen Läufen aus der Stelle,
    // aus der ihn sonst die Mail bekäme.
    const link = execFileSync(
      PYTHON,
      [path.join(WURZEL, 'frontend', 'e2e', 'bestaetigungslink.py'), KONTO.email],
      { encoding: 'utf8', cwd: path.join(WURZEL, 'backend'), env: { ...process.env, ...DATEN } },
    ).trim()
    const bestaetigt = await request.post(`/api/onboarding/verify/${link}`)
    expect(bestaetigt.ok(), await bestaetigt.text()).toBeTruthy()
  }

  const token = await verwalterToken(request)
  const eingestellt = await request.put('/api/settings', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      radarr_url: RADARR_INS_LEERE,
      radarr_api_key: 'e2e-kein-echter-schluessel',
      // Der Server zieht `movie_profile_mode` von selbst nach: Sobald eines
      // von beiden beim Entscheider liegt, wartet die ganze Anfrage auf ihn.
      movie_root_folder_mode: 'approver',
    },
  })
  expect(eingestellt.ok(), await eingestellt.text()).toBeTruthy()

  konto(ANFRAGER.username, ANFRAGER.password)

  // ⚠️ **Aufräumen vor dem Lauf, nicht danach.** In der CI läuft ein
  // gescheiterter Test ein zweites Mal (`retries: 1`). Bliebe die Anfrage aus
  // dem ersten Anlauf stehen, wiese der Server den zweiten mit „wurde bereits
  // angefragt" ab - und der Bericht zeigte auf die falsche Stelle.
  const anmeldung = await request.post('/api/auth/login', { data: ANFRAGER })
  expect(anmeldung.ok(), await anmeldung.text()).toBeTruthy()
  const kopf = { Authorization: `Bearer ${(await anmeldung.json()).access_token}` }
  const meine = await request.get('/api/requests/mine', { headers: kopf })
  expect(meine.ok(), await meine.text()).toBeTruthy()
  for (const zeile of await meine.json()) {
    const weg = await request.delete(`/api/requests/${zeile.id}`, { headers: kopf })
    expect(weg.ok(), await weg.text()).toBeTruthy()
  }
})

test('⚠️ suchen, anfragen, wiederfinden - und der Zustand stimmt', async ({ page }) => {
  await anmelden(page, ANFRAGER)

  // --- Suchen ---------------------------------------------------------------
  await page.goto('/suche')
  // Das Suchfeld ist das einzige seiner Art auf dieser Seite; die Kopfzeile
  // trägt keines. Über die Rolle statt über den Platzhaltertext, der einen
  // Auslassungspunkt enthält.
  await page.getByRole('searchbox').fill(FILM)

  // ⚠️ **Hier muss etwas stehen.** Ein Test, der weiterklickt und dabei auf
  // eine leere Trefferliste stößt, prüft ab hier gar nichts mehr. Die Kachel
  // ist ein Verweis, dessen Beschriftung mit dem Titel beginnt.
  const treffer = page.getByRole('link', { name: FILM })
  await expect(treffer).toHaveCount(1)

  // --- Öffnen ---------------------------------------------------------------
  await treffer.click()
  await expect(page.getByRole('heading', { name: FILM, level: 1 })).toBeVisible()
  await expect(page).toHaveURL(/\/titel\/movie\/\d+$/)

  // --- Anfragen -------------------------------------------------------------
  await page.getByRole('button', { name: 'Request', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Before adding' })).toBeVisible()
  // Weil der Entscheider Ordner und Qualität wählt, steht hier nichts zur
  // Auswahl - und genau das sagt das Formular auch, statt leere Felder zu
  // zeigen. Steht der Satz nicht da, ist die Einstellung nicht angekommen und
  // alles Weitere wäre eine Messung an der falschen Lage.
  await expect(
    page.getByText('Where the title goes and in which quality is decided by the approver'),
  ).toBeVisible()

  await page.getByRole('button', { name: 'Request now' }).click()

  // --- Was die Anfrage am Titel ändert ---------------------------------------
  // ⚠️ Der Kern: Derselbe Titel trägt jetzt einen anderen Zustand, ohne dass
  // die Seite neu geladen wurde. Der Knopf ist weg, weil es nichts mehr zu
  // holen gibt, und an seiner Stelle steht, worauf gewartet wird.
  await expect(
    page.getByText('This request is waiting for administrator approval.'),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Request', exact: true })).toHaveCount(0)

  // --- Und in der eigenen Liste ----------------------------------------------
  await page.goto('/requests')
  await expect(page.getByRole('heading', { name: 'My requests' })).toBeVisible()

  // Erst der Beweis, dass überhaupt etwas dasteht: Der Leer-Satz und ein
  // Treffer schließen einander aus, und ohne diese Zeile ginge eine leere
  // Liste als Erfolg durch.
  await expect(page.getByText("You haven't requested anything yet.")).toHaveCount(0)

  // Genau eine Zeile, genau dieser Titel, genau dieser Zustand. Bei einer
  // einzigen Anfrage zeigt die Seite keine Zustandsfilter (die gibt es erst ab
  // zwei verschiedenen Zuständen), das Etikett kommt also aus der Zeile selbst
  // und nicht aus einem Filterknopf daneben.
  await expect(page.getByRole('link', { name: FILM, exact: true })).toHaveCount(1)
  await expect(page.getByText('Awaiting approval', { exact: true })).toHaveCount(1)

  // Die Gegenprobe: Was niemand angefragt hat, steht auch nicht hier. Ohne sie
  // bestünde der Test auch dann, wenn die Liste stur alles zeigte, was es im
  // Katalog gibt.
  await expect(page.getByText(NICHT_ANGEFRAGT)).toHaveCount(0)
})
