/**
 * Eine Regel entscheidet — und der Anfragende erfährt, welche.
 *
 * Der Weg, den `test_regeln_anfrage.py` am Server misst, hier einmal durch
 * alle Schichten: Der Administrator legt im Reiter „Regeln" eine Regel an,
 * jemand anders fragt einen Titel an, und die Anfrage steht danach als
 * abgelehnt in seiner Liste — mit der Begründung, die der Administrator
 * geschrieben hat, und mit dem Namen der Regel im Verlauf.
 *
 * ⚠️ **Warum das trotz 63 Server-Tests hier noch einmal steht.** Am Server ist
 * geprüft, *dass* die Regel entscheidet. Ungeprüft wäre die Kette darum herum:
 * ob die Oberfläche eine Regel so speichert, dass der Server sie annimmt, ob
 * die Bedingung, die man anklickt, dieselbe ist, die dann greift, und ob der
 * Anfragende die Begründung überhaupt zu sehen bekommt. Genau dazwischen
 * verschwinden Fehler, die einzeln jede Schicht bestehen lässt.
 *
 * ⚠️ **Die Regel lehnt ab, sie gibt nicht frei.** Eine Freigabe schickt den
 * Titel wirklich an Radarr, und das steht in dieser Umgebung nur auf dem
 * Papier. Eine Ablehnung endet dagegen im Server selbst — sie ist der einzige
 * Ausgang, der sich hier vollständig messen lässt.
 *
 * ⚠️ **Die Bedingung ist `Typ = Film`, nicht die Bewertung.** Sie muss auf den
 * Demo-Titel sicher zutreffen, sonst misst der Lauf das Gegenteil von dem, was
 * er messen soll — und Bewertungen in `demo_data.py` können sich ändern, ohne
 * dass jemand an diese Datei denkt.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import { KONTO, PYTHON, WURZEL } from './konto'

const ANFRAGER = { username: 'e2e-regel-nutzer', password: 'Ein-langes-Passwort-1234' }

const FILM = 'Nordlicht'
const REGELNAME = 'E2E: Filme nicht'
const BEGRUENDUNG = 'Filme holen wir dieses Jahr nicht mehr.'

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

/** Abmelden: Der Knopf zum Benutzermenü trägt den eigenen Namen. */
async function abmelden(page: Page, name: string) {
  await page.getByRole('button', { name, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
}

async function anmelden(page: Page, wer: { username: string; password: string }) {
  await page.goto('/')
  await page.getByLabel('Username or e-mail').fill(wer.username)
  await page.getByLabel('Password', { exact: true }).fill(wer.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('link', { name: 'Browse', exact: true }).first()).toBeVisible()
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

  const token = await verwalterToken(request)
  const kopf = { Authorization: `Bearer ${token}` }

  const eingestellt = await request.put('/api/settings', {
    headers: kopf,
    data: {
      radarr_url: RADARR_INS_LEERE,
      radarr_api_key: 'e2e-kein-echter-schluessel',
      movie_root_folder_mode: 'approver',
    },
  })
  expect(eingestellt.ok(), await eingestellt.text()).toBeTruthy()

  konto(ANFRAGER.username, ANFRAGER.password)

  // ⚠️ **Aufräumen vor dem Lauf, nicht danach** - wie in den übrigen Läufen:
  // Ein zweiter Anlauf nach einem Fehlschlag stolperte sonst über die Reste
  // des ersten, und der Bericht zeigte auf die falsche Stelle.
  const vorhandene = await request.get('/api/admin/regeln', { headers: kopf })
  expect(vorhandene.ok(), await vorhandene.text()).toBeTruthy()
  for (const regel of await vorhandene.json()) {
    if (String(regel.name).startsWith('E2E:')) {
      const weg = await request.delete(`/api/admin/regeln/${regel.id}`, { headers: kopf })
      expect(weg.ok(), await weg.text()).toBeTruthy()
    }
  }

  // ⚠️ **Jede Anfrage auf diesen Titel weg, nicht nur die eigenen.**
  //
  // Zwei Gründe, und beide haben je einen Lauf gekostet. Erstens nimmt
  // `DELETE /api/requests/{id}` nur *wartende* Anfragen zurück; eine von einer
  // Regel abgelehnte bleibt stehen — also über den Administrator. Zweitens
  // blockiert eine laufende Anfrage den Titel **für alle**: Ein früherer Lauf
  // in derselben Instanz hatte `Nordlicht` schon angefragt, und dann steht auf
  // der Titelseite kein Anfrage-Knopf mehr. Allein lief der Test grün, in der
  // Reihe nicht.
  const alle = await request.get('/api/admin/requests', { headers: kopf })
  expect(alle.ok(), await alle.text()).toBeTruthy()
  for (const zeile of await alle.json()) {
    if (zeile.title !== FILM) continue
    const weg = await request.delete(`/api/admin/requests/${zeile.id}`, { headers: kopf })
    expect(weg.ok(), await weg.text()).toBeTruthy()
  }
})

test.afterAll(async ({ request }) => {
  // Die Regel darf nicht stehenbleiben: Die anderen Läufe teilen sich diese
  // Instanz, und eine Regel „Filme ablehnen" würde ihnen den Boden wegziehen.
  const token = await verwalterToken(request)
  const kopf = { Authorization: `Bearer ${token}` }
  const vorhandene = await request.get('/api/admin/regeln', { headers: kopf })
  if (!vorhandene.ok()) return
  for (const regel of await vorhandene.json()) {
    if (String(regel.name).startsWith('E2E:')) {
      await request.delete(`/api/admin/regeln/${regel.id}`, { headers: kopf })
    }
  }
})

test('⚠️ der Admin legt eine Regel an, sie lehnt ab, und der Nutzer liest warum', async ({
  page,
  request,
}) => {
  // --- Der Administrator legt die Regel an, über die Oberfläche -------------
  await anmelden(page, KONTO)
  await page.goto('/admin/settings?reiter=regeln')
  await expect(page.getByRole('heading', { name: 'Rules', exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Add a rule' }).click()
  await expect(page.getByText('New rule')).toBeVisible()

  await page.getByLabel('Name').fill(REGELNAME)

  // „Typ" steht als erste Bedingung schon da, mit „Film" angehakt. Bleibt es
  // dabei, trifft die Regel auf jeden Film zu - genau das ist gewollt.
  await expect(page.getByRole('checkbox', { name: 'Film' })).toBeChecked()

  await page.getByRole('button', { name: 'reject', exact: true }).click()
  await page.getByLabel('What the requester reads').fill(BEGRUENDUNG)
  await page.getByRole('button', { name: 'Save' }).click()

  // ⚠️ **Erst der Beweis, dass sie wirklich gespeichert wurde.** Ohne diese
  // Zeile ginge ein stiller Fehler beim Speichern als Erfolg durch, und der
  // Rest des Laufs maße nur noch, dass ohne Regel nichts abgelehnt wird.
  await expect(page.getByText(REGELNAME)).toBeVisible()
  await expect(page.getByText('reject', { exact: true })).toBeVisible()

  const gespeichert = await request.get('/api/admin/regeln', {
    headers: { Authorization: `Bearer ${await verwalterToken(request)}` },
  })
  const regeln = await gespeichert.json()
  const meine = regeln.find((r: { name: string }) => r.name === REGELNAME)
  expect(meine, `Die Regel steht nicht in der Liste: ${JSON.stringify(regeln)}`).toBeTruthy()
  expect(meine.entscheidung).toBe('ablehnen')
  expect(meine.bedingungen).toEqual([{ feld: 'typ', werte: ['movie'] }])

  // --- Jemand anders fragt an ----------------------------------------------
  // ⚠️ Erst abmelden. Ohne das zeigt `/` die Startseite des Administrators
  // statt des Anmeldeformulars, und der Lauf liefe unter dem falschen Konto
  // weiter - dem einzigen, für das Regeln ausdrücklich **nicht** gelten.
  await abmelden(page, KONTO.username)
  await anmelden(page, ANFRAGER)
  await page.goto('/suche')
  await page.getByRole('searchbox').fill(FILM)
  const treffer = page.getByRole('link', { name: FILM })
  await expect(treffer).toHaveCount(1)
  await treffer.click()
  await expect(page.getByRole('heading', { name: FILM, level: 1 })).toBeVisible()

  await page.getByRole('button', { name: 'Request', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Before adding' })).toBeVisible()
  await page.getByRole('button', { name: 'Request now' }).click()

  // --- Und findet sie abgelehnt vor ----------------------------------------
  await page.goto('/requests')
  await expect(page.getByRole('heading', { name: 'My requests' })).toBeVisible()
  await expect(page.getByText("You haven't requested anything yet.")).toHaveCount(0)

  // Der Kern: Der Anfragende liest den Satz, den der Administrator in die
  // Regel geschrieben hat - nicht ein anonymes „abgelehnt".
  await expect(page.getByText(BEGRUENDUNG)).toBeVisible()

  // Und im Verlauf steht, **wer** entschieden hat: keine Person, eine Regel.
  await page.getByRole('button', { name: 'Progress' }).first().click()
  await expect(page.getByText(`by rule “${REGELNAME}”`)).toBeVisible()
})
