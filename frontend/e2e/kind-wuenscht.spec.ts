/**
 * Das Kind wünscht sich etwas, das Elternteil entscheidet.
 *
 * Der Weg führt über **zwei Konten und zwei verschiedene Oberflächen**: Ein
 * Kinderkonto bekommt einen eigenen Seitenbaum mit eigener Farbwelt, eigenen
 * Endpunkten (`/api/kids/*`) und einem einzigen Knopf am Titel. Was dort
 * geklickt wird, taucht beim Elternteil unter „Kinder" wieder auf, und aus dem
 * Klick auf „Freigeben" wird eine ganz gewöhnliche Anfrage - **auf den Namen
 * des Elternteils**, mit seinem Kontingent und seinem Freigabeweg.
 *
 * Diese Kette lässt sich in keinem einzelnen Test nachstellen: Sie hängt an
 * einem Rollenwechsel mitten im Ablauf. Genau deshalb steht sie hier.
 *
 * ⚠️ **Das Kinderkonto entsteht über die Oberfläche, nicht über ein Skript.**
 * Anders als beim gewöhnlichen Konto (`konto_anlegen.py`) ist das Anlegen hier
 * Teil dessen, was geprüft werden soll: Das Formular vergibt Name, Passwort,
 * Alter, Sprache und Rubriken, und die Rubriken entscheiden anschließend
 * darüber, ob das Kind den Titel überhaupt findet. Ein Kind, das per SQL
 * entsteht, umginge genau die Stelle, an der das schiefgehen kann.
 *
 * ⚠️ **Radarr ist eingetragen und zeigt ins Leere** - dieselbe Lage wie in
 * `anfrage-stellen.spec.ts`, und dort steht auch der ausführliche Grund. Kurz:
 * Ohne Eintrag lehnt `create_request` jede Filmanfrage ab; erreichbar sein
 * muss Radarr auf diesem Weg nicht, solange Ordner und Qualität erst beim
 * Entscheider fallen.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'

/** Das Elternteil. Ein gewöhnliches Konto, das vom Administrator nur einen
 *  einzigen Haken bekommt: Es darf Kinderkonten führen. */
const ELTERNTEIL = { username: 'e2e-elternteil', password: 'Ein-langes-Passwort-1234' }

/** Das Kind. Kein Anzeigename, damit überall derselbe Name steht und der Test
 *  nicht an zwei Schreibweisen desselben Kontos vorbeiläuft. */
const KIND = { username: 'e2e-kind', password: 'Ein-langes-Passwort-1234', alter: '8' }

/**
 * „Papierboote" aus `backend/app/mocks/demo_data.py`: Animation, Freigabe ab 6.
 *
 * Die Rubrik ist der Grund für die Wahl. Ein Kinderkonto sieht nur die
 * freigeschalteten Rubriken, und von den acht überschneiden sich mit dem
 * Demo-Bestand genau drei (Animation, Komödie, Dokumentation). Ein Titel aus
 * „Thriller" wäre für das Kind schlicht nicht da, und der Test scheiterte an
 * der Testumgebung statt an Nexview.
 */
const FILM = 'Papierboote'

/** Siehe `anfrage-stellen.spec.ts`: Der Port muss frei bleiben, 8799 bis 8796
 *  und 5599 gehören den Servern dieses Aufbaus. */
const RADARR_INS_LEERE = 'http://127.0.0.1:8795'

const DATEN = { NEXVIEW_DATA_DIR: path.join(WURZEL, 'frontend', '.e2e-data') }

async function token(
  request: APIRequestContext,
  wer: { username: string; password: string },
): Promise<string> {
  const antwort = await request.post('/api/auth/login', { data: wer })
  expect(antwort.ok(), await antwort.text()).toBeTruthy()
  return (await antwort.json()).access_token as string
}

async function anmelden(page: Page, wer: { username: string; password: string }) {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(wer.username)
  await page.getByLabel('Password', { exact: true }).fill(wer.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  // ⚠️ **Abwarten, bis die Anmeldung wirklich durch ist.** Ohne diese Zeile
  // fuhr der nächste `goto` in die noch offene Anmeldung hinein und landete
  // wieder auf dem Formular - der Test scheiterte dann am Folgeschritt und
  // zeigte damit auf die falsche Stelle. Geprüft wird das Verschwinden des
  // Knopfes und nicht ein Element der Zielseite: Ein Kind bekommt einen ganz
  // anderen Rahmen als ein Erwachsener, und beide sollen durch diese Tür.
  await expect(page.getByRole('button', { name: 'Sign in' })).toHaveCount(0)
}

/** Abmelden aus der Erwachsenen-Ansicht: Der Knopf zum Benutzermenü trägt den
 *  eigenen Namen, nicht „Menü öffnen". */
async function abmelden(page: Page, name: string) {
  await page.getByRole('button', { name, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
}

test.beforeAll(async ({ request }) => {
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

  const verwalter = { Authorization: `Bearer ${await token(request, KONTO)}` }
  const eingestellt = await request.put('/api/settings', {
    headers: verwalter,
    data: {
      radarr_url: RADARR_INS_LEERE,
      radarr_api_key: 'e2e-kein-echter-schluessel',
      movie_root_folder_mode: 'approver',
    },
  })
  expect(eingestellt.ok(), await eingestellt.text()).toBeTruthy()

  execFileSync(
    PYTHON,
    [
      path.join(WURZEL, 'frontend', 'e2e', 'konto_anlegen.py'),
      ELTERNTEIL.username,
      ELTERNTEIL.password,
    ],
    { cwd: path.join(WURZEL, 'backend'), env: { ...process.env, ...DATEN } },
  )

  // ⚠️ **Den Haken setzt der Administrator, und nur er.** Kinderkonten sind
  // echte Konten auf dieser Installation; wer welche anlegen darf, ist keine
  // Entscheidung des Mitbenutzers. Deshalb geht der Weg hier über die
  // Verwaltungsroute und nicht an ihr vorbei in die Datenbank - hätte sie eine
  // Lücke, soll der Test sie mitnehmen.
  const alle = await request.get('/api/users', { headers: verwalter })
  expect(alle.ok(), await alle.text()).toBeTruthy()
  const zeile = (await alle.json()).find(
    (k: { username: string }) => k.username === ELTERNTEIL.username,
  )
  expect(zeile, `Konto ${ELTERNTEIL.username} steht nicht in der Verwaltung.`).toBeTruthy()
  const berechtigt = await request.patch(`/api/users/${zeile.id}`, {
    headers: verwalter,
    data: { can_manage_children: true },
  })
  expect(berechtigt.ok(), await berechtigt.text()).toBeTruthy()

  // ⚠️ **Aufräumen vor dem Lauf, nicht danach.** In der CI läuft ein
  // gescheiterter Test ein zweites Mal (`retries: 1`). Blieben Kind und
  // Anfrage aus dem ersten Anlauf stehen, scheiterte der zweite schon am
  // vergebenen Benutzernamen - und der Bericht zeigte auf die falsche Stelle.
  const eltern = { Authorization: `Bearer ${await token(request, ELTERNTEIL)}` }
  const kinder = await request.get('/api/children', { headers: eltern })
  expect(kinder.ok(), await kinder.text()).toBeTruthy()
  for (const kind of await kinder.json()) {
    // Das Löschen nimmt die Wünsche gleich mit.
    const weg = await request.delete(`/api/children/${kind.id}`, { headers: eltern })
    expect(weg.ok(), await weg.text()).toBeTruthy()
  }
  const meine = await request.get('/api/requests/mine', { headers: eltern })
  expect(meine.ok(), await meine.text()).toBeTruthy()
  for (const anfrage of await meine.json()) {
    const weg = await request.delete(`/api/requests/${anfrage.id}`, { headers: eltern })
    expect(weg.ok(), await weg.text()).toBeTruthy()
  }
})

test('⚠️ das Kind wünscht, das Elternteil gibt frei - und beide sehen es', async ({ page }) => {
  // ═══ Das Elternteil legt das Kind an ═════════════════════════════════════
  await anmelden(page, ELTERNTEIL)
  await page.goto('/profil?reiter=kinder')

  // Vorher wartet nichts. Der Satz ist die Gegenprobe zum Schluss: Ohne ihn
  // ließe sich nicht unterscheiden, ob der Wunsch später wirklich ankam oder
  // ob dort ohnehin schon etwas stand.
  await expect(page.getByText('No wish is waiting for you right now.')).toBeVisible()

  await page.getByRole('button', { name: 'Add child account' }).click()
  await page.getByLabel('Username', { exact: true }).fill(KIND.username)
  await page.getByLabel('Age', { exact: true }).fill(KIND.alter)
  // ⚠️ Ein Kind stellt seine Sprache nicht selbst um - in der Kinderansicht
  // gibt es dafür bewusst keinen Schalter. Ohne diese Zeile erbte es die
  // Vorgabe „Deutsch" und der Test suchte englische Beschriftungen auf einer
  // deutschen Seite.
  await page.getByRole('combobox', { name: /Language/ }).selectOption('en')
  await page.getByLabel('Password', { exact: true }).fill(KIND.password)
  await page.getByLabel('Repeat password', { exact: true }).fill(KIND.password)
  await page.getByRole('button', { name: 'Create' }).click()

  // Die Kachel trägt den Benutzernamen und das Alter - erst damit steht fest,
  // dass das Konto wirklich entstanden ist und nicht nur das Formular zuging.
  await expect(page.getByText(`${KIND.alter} years old`)).toBeVisible()
  await abmelden(page, ELTERNTEIL.username)

  // ═══ Das Kind sucht und wünscht ══════════════════════════════════════════
  await anmelden(page, KIND)
  // Eigene Oberfläche, eigener Rahmen: Die Begrüßung gibt es nur dort. Stünde
  // hier die Erwachsenen-Navigation, wäre die Rollentrennung gebrochen.
  await expect(page.getByText(`Hi ${KIND.username}`)).toBeVisible()

  await page.getByRole('link', { name: 'Search' }).click()
  await page.getByLabel('What are you looking for?').fill(FILM)
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  // ⚠️ **Der Titel muss unter „wünschen" stehen, nicht unter „ist schon da".**
  // Beide Bereiche zeigen Kacheln mit demselben Aussehen; nur die Überschrift
  // sagt, was ein Klick darauf bedeutet. Der zweite Vergleich schließt aus,
  // dass die Suche in Wahrheit einen vorhandenen Titel gefunden hat und der
  // Wunsch-Knopf später gar nicht erscheint.
  await expect(page.getByRole('heading', { name: 'You can wish for these' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'You can watch these now' })).toHaveCount(0)

  const kachel = page.getByRole('button', { name: FILM })
  await expect(kachel).toHaveCount(1)
  await kachel.click()

  await expect(page.getByRole('heading', { name: FILM, level: 1 })).toBeVisible()
  await page.getByRole('button', { name: 'I want to watch this' }).click()

  // Vier Zustände statt der acht aus `RequestStatus` - hier der erste. Der
  // Knopf ist weg, an seiner Stelle steht, worauf gewartet wird.
  await expect(page.getByText('Mum or dad is looking at it')).toBeVisible()
  await expect(page.getByRole('button', { name: 'I want to watch this' })).toHaveCount(0)

  await page.getByRole('link', { name: 'Wishes' }).click()
  await expect(page.getByText('You have not wished for anything yet.')).toHaveCount(0)
  await expect(page.getByText(FILM)).toBeVisible()
  await expect(page.getByText('Mum or dad is looking at it')).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()

  // ═══ Das Elternteil entscheidet ══════════════════════════════════════════
  await anmelden(page, ELTERNTEIL)
  await page.goto('/profil?reiter=kinder')

  // Der Wunsch steht jetzt da, mit Titel und mit dem Namen des Kindes. Der
  // Name gehört dazu: Wer mehrere Kinder hat, entscheidet sonst blind.
  await expect(page.getByRole('link', { name: FILM })).toBeVisible()
  await expect(page.getByText(`from ${KIND.username}`)).toBeVisible()

  await page.getByRole('button', { name: 'Approve' }).click()
  // Ordner und Qualität wählt der Entscheider, also gibt es hier nichts
  // auszuwählen - und das Formular sagt es, statt leere Felder zu zeigen.
  await expect(page.getByText('The approver picks folder and quality when approving.')).toBeVisible()
  // `.last()`: Der Knopf auf der Wunsch-Karte heißt genauso wie der, der die
  // Freigabe wirklich abschickt. Das Auswahlfeld steht darunter, also ist es
  // der letzte.
  await page.getByRole('button', { name: 'Approve' }).last().click()

  // ⚠️ Entschieden ist entschieden: Der Wunsch verlässt die Liste. Bliebe er
  // stehen, ließe er sich ein zweites Mal freigeben.
  await expect(page.getByText('No wish is waiting for you right now.')).toBeVisible()

  // ═══ Und was daraus geworden ist ═════════════════════════════════════════
  // Die Anfrage läuft auf den Namen des **Elternteils** - das ist der ganze
  // Sinn des Aufbaus: Das Kind hat kein eigenes Kontingent und keinen eigenen
  // Weg zum Administrator.
  await page.goto('/requests')
  await expect(page.getByText("You haven't requested anything yet.")).toHaveCount(0)
  await expect(page.getByRole('link', { name: FILM, exact: true })).toHaveCount(1)
  await expect(page.getByText('Awaiting approval', { exact: true })).toHaveCount(1)

  await abmelden(page, ELTERNTEIL.username)

  // ═══ Das Kind sieht die Entscheidung ═════════════════════════════════════
  // ⚠️ Der Schlusspunkt, und der eigentliche Beweis: Aus „Mama oder Papa guckt
  // sich das an" ist „unterwegs" geworden. Dass beide Seiten dieselbe Anfrage
  // meinen, steht sonst nirgends.
  await anmelden(page, KIND)
  await page.getByRole('link', { name: 'Wishes' }).click()
  await expect(page.getByText(FILM)).toBeVisible()
  await expect(page.getByText('On its way')).toBeVisible()
  await expect(page.getByText('Mum or dad is looking at it')).toHaveCount(0)
})
