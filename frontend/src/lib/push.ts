/* Web Push im Browser: Erlaubnis holen, anmelden, abmelden.
 *
 * ⚠️ **Drei Dinge müssen zugleich stimmen, und sie fühlen sich für den
 * Menschen alle gleich an.** Der Browser muss Push können, er muss die
 * Erlaubnis erteilt haben, und der Service Worker muss laufen. Fehlt eines,
 * passiert nichts. Deshalb gibt jede Funktion hier einen benannten Grund
 * zurück und nicht `false`.
 *
 * Die Entscheidung, welche Lage ein Browser hat, wohnt in `pushlage.ts`, ohne
 * Browser-Bezug, damit die schnelle Prüfebene sie prüfen kann. Hier steht nur,
 * was wirklich einen Browser braucht.
 */
import { api } from '../api/client'
import type { PushGeraet } from '../api/types'
import { mitBasis } from './basis'
import { lageAus } from './pushlage'
import type { PushLage } from './pushlage'

export type { PushLage, Umstaende } from './pushlage'
export { lageAus }

export interface Abonnement {
  endpoint: string
  p256dh: string
  auth: string
  language: string
}

/** Was der Server nach dem Anmelden sagt. */
export interface Anmeldung {
  abonnement: Abonnement
  geraet: PushGeraet
}

/* ⚠️ **Der Merker für „bewusst abgemeldet".** Die Erlaubnis des Browsers
   überlebt das Entfernen eines Geräts; ohne Merker meldete `sicherstellen`
   das Gerät beim nächsten Öffnen still wieder an, und „Gerät entfernen" wirkte
   kaputt. Er lebt im Browser, weil die Entscheidung zu diesem Browser gehört. */
const MERKER = 'nexview.push.abgemeldet'

/** Ob dieser Browser überhaupt in Frage kommt. */
export function moeglich(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

/** https, oder localhost. Über http gibt es die Schnittstelle gar nicht erst. */
function sicher(): boolean {
  return window.isSecureContext === true
}

/* ⚠️ **iOS liefert Push nur an Seiten auf dem Home-Bildschirm.** Safari zeigt
   im gewöhnlichen Reiter zwar `PushManager` an, aber die Nachfrage führt zu
   nichts, und sagt auch nicht, warum. Ohne diese Prüfung tippt jemand am
   iPhone auf „Erlauben", nichts passiert, und er hält Nexview für kaputt. */
function alsAppGestartet(): boolean {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    (navigator as { standalone?: boolean }).standalone === true
  )
}

function bewusstAbgemeldet(): boolean {
  try {
    return localStorage.getItem(MERKER) === '1'
  } catch {
    return false
  }
}

function abmeldungMerken(ja: boolean): void {
  try {
    if (ja) localStorage.setItem(MERKER, '1')
    else localStorage.removeItem(MERKER)
  } catch {
    /* Privater Modus ohne localStorage: dann gibt es auch keinen Merker. */
  }
}

/** Die Lage dieses Browsers, jetzt.
 *
 * ⚠️ **Asynchron, und das muss sie sein:** Ob dieses Gerät angemeldet ist,
 * steht im Service Worker und nicht in einer Eigenschaft von `window`.
 */
export async function lage(): Promise<PushLage> {
  const kannPush = moeglich()
  return lageAus({
    sicher: sicher(),
    kannPush,
    istApple: /iPad|iPhone|iPod/.test(navigator.userAgent),
    alsApp: alsAppGestartet(),
    erlaubnis: kannPush ? Notification.permission : 'default',
    angemeldet: (await vorhandene()) !== null,
    abgemeldet: bewusstAbgemeldet(),
  })
}

/** Den Service Worker registrieren, mehrfach aufrufbar. */
export async function arbeiter(): Promise<ServiceWorkerRegistration> {
  /* ⚠️ **`scope` wird ausdrücklich gesetzt.** Ein Worker bekommt sonst den
     Geltungsbereich seines eigenen Verzeichnisses; unter einem Vorbau wäre das
     zufällig richtig, aber ohne ihn stünde der Worker an der Wurzel, und zwei
     Nexviews auf derselben Domain überschrieben einander. */
  const wurzel = mitBasis('/').replace(/\/+$/, '/')
  return navigator.serviceWorker.register(mitBasis('/sw.js'), {
    scope: wurzel,
  })
}

function alsAbonnement(abo: PushSubscription, sprache: string): Abonnement {
  const roh = abo.toJSON() as {
    endpoint?: string
    keys?: Record<string, string>
  }
  return {
    endpoint: roh.endpoint ?? '',
    p256dh: roh.keys?.p256dh ?? '',
    auth: roh.keys?.auth ?? '',
    language: sprache,
  }
}

/* Der Schlüssel kommt als base64url vom Server, `subscribe` will Bytes. */
function alsBytes(base64url: string): Uint8Array {
  const gepolstert = base64url.padEnd(base64url.length + ((4 - (base64url.length % 4)) % 4), '=')
  const roh = atob(gepolstert.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(roh, (z) => z.charCodeAt(0))
}

/** Die Anmeldung dieses Browsers, falls es eine gibt. */
export async function vorhandene(): Promise<Abonnement | null> {
  if (!moeglich() || Notification.permission !== 'granted') return null
  const reg = await navigator.serviceWorker.getRegistration(mitBasis('/sw.js'))
  const abo = await reg?.pushManager.getSubscription()
  return abo ? alsAbonnement(abo, '') : null
}

/** Erlaubnis holen, anmelden, beim Server eintragen.
 *
 * ⚠️ **Der Browser fragt genau einmal.** Ein „Nein" lässt sich von hier aus
 * nie wieder aufheben, nur in den Website-Einstellungen des Browsers. Diese
 * Funktion darf deshalb nur aus einem Klick heraus laufen, nie beim Laden der
 * Seite: Eine Nachfrage, die aus dem Nichts kommt, klickt man weg, und danach
 * ist die Funktion für dieses Gerät dauerhaft zu.
 */
export async function anmelden(sprache: string): Promise<Anmeldung> {
  if (!moeglich()) throw new Error('push_unmoeglich')

  /* ⚠️ **Kein `await` vor dieser Zeile.** Safari verlangt, dass die Nachfrage
     in derselben Aufgabe wie der Klick läuft; ein Umweg über eine Zusage
     davor, und sie kommt gar nicht erst hoch. */
  const erlaubnis = await Notification.requestPermission()
  if (erlaubnis === 'denied') throw new Error('push_abgelehnt')
  /* ⚠️ **„default" ist nicht „denied".** Manche Browser, iOS voran, geben die
     Nachfrage still zurück, ohne sie zu zeigen. Das als Ablehnung zu
     verbuchen hieße: Der Knopf tut nichts und sagt nichts. */
  if (erlaubnis !== 'granted') throw new Error('push_keine_antwort')

  return wiederAnmelden(sprache)
}

/** Anmelden ohne Nachfrage: Die Erlaubnis steht schon. */
export async function wiederAnmelden(sprache: string): Promise<Anmeldung> {
  abmeldungMerken(false)
  const daten = await sicherstellen(sprache)
  if (daten === null) throw new Error('push_anmeldung_gescheitert')
  return daten
}

/** Dafür sorgen, dass ein Browser mit erteilter Erlaubnis auch angemeldet ist.
 *
 * ⚠️ **Fragt nichts und darf deshalb beim Laden laufen.** Nur
 * `requestPermission` braucht einen Klick; `subscribe` mit längst erteilter
 * Erlaubnis nicht. Ohne diese Stelle bliebe ein Gerät für immer stumm, dessen
 * Erlaubnis noch steht, dessen Abonnement aber weg ist: nach einem Löschen der
 * Browserdaten, nach einem Serverumzug, oder weil der Server neu aufgesetzt
 * wurde und seine Tabelle leer ist.
 *
 * ⚠️ **Außer der Mensch hat dieses Gerät selbst entfernt.** Dann steht der
 * Merker, und hier passiert nichts, bis er es ausdrücklich wieder anmeldet.
 */
export async function sicherstellen(sprache: string): Promise<Anmeldung | null> {
  if (!moeglich() || Notification.permission !== 'granted' || bewusstAbgemeldet()) return null

  const reg = await arbeiter()
  await navigator.serviceWorker.ready

  const { public_key } = await api.get<{ public_key: string }>('/api/push/key')

  /* ⚠️ **Ein vorhandenes Abonnement wird wiederverwendet, nicht ersetzt.**
     `subscribe` wirft, wenn schon eines mit einem anderen Schlüssel besteht,
     und das passiert wirklich, nämlich nach einem Serverumzug. */
  const abo =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: alsBytes(public_key) as BufferSource,
    }))

  const abonnement = alsAbonnement(abo, sprache)
  /* ⚠️ **Immer melden, auch bei einem vorhandenen Abonnement.** Der Server
     kennt es womöglich nicht; er legt dieselbe Adresse kein zweites Mal an. */
  const geraet = await api.post<PushGeraet>('/api/push/devices', abonnement)
  return { abonnement, geraet }
}

/** Dieses Gerät abmelden: im Browser **und** im Server, und den Merker setzen.
 *
 * ⚠️ **Beides, und in dieser Reihenfolge.** Bliebe das Abonnement im Browser
 * stehen, meldete er sich beim nächsten Öffnen sofort wieder an, und der
 * Knopf wirkte kaputt. Der Merker verhindert dasselbe über die Erlaubnis, die
 * sich nicht zurücknehmen lässt.
 */
export async function abmelden(geraetId: number): Promise<void> {
  abmeldungMerken(true)
  const reg = await navigator.serviceWorker.getRegistration(mitBasis('/sw.js'))
  const abo = await reg?.pushManager.getSubscription()
  await abo?.unsubscribe()
  await api.delete(`/api/push/devices/${geraetId}`)
}
