/* Die Lage eines Browsers gegenüber Web Push, als Entscheidung.
 *
 * ⚠️ **Ein eigenes Modul, und der Grund ist der Testlauf.** Die Anbindung in
 * `push.ts` hängt über `api/client` und `lib/basis` am Dokument; die schnelle
 * Prüfebene läuft ohne Browser und könnte sie nicht einmal importieren. Eine
 * Datei ohne Browser-Bezug kostet nichts und lässt sich in Millisekunden
 * prüfen. Was wirklich einen Browser braucht, steht daneben.
 */

/** Warum es hier weitergeht oder nicht. Die Oberfläche übersetzt die Kennung. */
export type PushLage =
  | 'bereit' // erlaubt UND angemeldet
  | 'erlaubt_ohne_anmeldung' // erlaubt, aber dieser Server kennt das Gerät nicht
  | 'abgemeldet' // erlaubt, aber der Mensch hat dieses Gerät bewusst entfernt
  | 'offen' // der Browser hat noch nicht gefragt
  | 'abgelehnt' // der Browser hat Nein gesagt, nur in seinen Einstellungen zurückzunehmen
  | 'unmoeglich' // dieser Browser kann kein Push
  | 'kein_home' // iOS ohne Home-Bildschirm
  | 'kein_https' // der Zugang läuft über http

/** Woraus sich die Lage ergibt. Als Werte, damit sie prüfbar bleibt. */
export interface Umstaende {
  /** Die Seite läuft in einem sicheren Kontext: https, oder localhost. */
  sicher: boolean
  /** Service Worker, PushManager und Notification sind alle da. */
  kannPush: boolean
  istApple: boolean
  /** Vom Home-Bildschirm gestartet, nicht als gewöhnlicher Reiter. */
  alsApp: boolean
  erlaubnis: NotificationPermission
  /** Es gibt ein Abonnement für dieses Gerät. */
  angemeldet: boolean
  /** Der Mensch hat dieses Gerät entfernt und will nicht still wieder angemeldet werden. */
  abgemeldet: boolean
}

/** Die Entscheidung, ohne Browser.
 *
 * ⚠️ **Die Reihenfolge ist die der Wahrheit, nicht die der Bequemlichkeit.**
 * Über http gibt es die Schnittstelle gar nicht erst, und das ist der Grund,
 * den jemand im Heimnetz wirklich hat. Fehlt sie trotz https, ist die
 * Erlaubnis bedeutungslos. Und auf einem iPhone im gewöhnlichen Reiter liefert
 * iOS auch mit erteilter Erlaubnis nichts aus. Alles drei muss vor der
 * Erlaubnis stehen, sonst nennt die Meldung den nächstbesten Grund statt des
 * wahren.
 *
 * ⚠️ **„Erlaubt" und „angemeldet" sind zwei Dinge.** Die Erlaubnis lebt im
 * Browser und überlebt alles; die Anmeldung lebt im Server und ist nach einem
 * Umzug, einem gelöschten Browserzustand oder einer frischen Installation
 * weg. Und wer sein Gerät bewusst entfernt hat, soll nicht beim nächsten
 * Öffnen still wieder angemeldet werden, nur weil die Erlaubnis noch steht.
 */
export function lageAus(u: Umstaende): PushLage {
  if (!u.sicher) return 'kein_https'
  if (!u.kannPush) return 'unmoeglich'
  if (u.istApple && !u.alsApp) return 'kein_home'
  if (u.erlaubnis === 'denied') return 'abgelehnt'
  if (u.erlaubnis !== 'granted') return 'offen'
  if (u.angemeldet) return 'bereit'
  return u.abgemeldet ? 'abgemeldet' : 'erlaubt_ohne_anmeldung'
}
