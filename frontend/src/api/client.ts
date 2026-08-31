/**
 * Zugriff auf das Nexview-Backend.
 *
 * Der Access-Token liegt nur im Arbeitsspeicher. Der Refresh-Token liegt
 * ueberhaupt nicht mehr hier: Er ist seit 0.21 ein HttpOnly-Cookie, das der
 * Browser selbst verwaltet und das dieses Skript weder lesen noch schreiben
 * kann. Genau das ist der Punkt - vorher lag er dreissig Tage im
 * `localStorage`, wo jedes Skript ihn mitnehmen konnte.
 *
 * Nach einem Neuladen der Seite ist der Access-Token weg; `restoreSession`
 * holt sich mit dem Cookie einen neuen. Laeuft er waehrend der Sitzung ab,
 * wird er automatisch einmal erneuert und die Anfrage wiederholt.
 */

import i18n from '../i18n'
import { mitBasis } from '../lib/basis'

/**
 * Der Platz, an dem der Refresh-Token frueher lag.
 *
 * Wird beim Start einmal weggeraeumt. Ohne das bliebe bei jedem, der von
 * einer aelteren Fassung kommt, ein gueltiges Dreissig-Tage-Token im Browser
 * liegen - nutzlos fuer die Anwendung, aber weiterhin lesbar und bis zum
 * Ablauf gegen den Server verwendbar. Die Zeile darf fruehestens weg, wenn
 * niemand mehr von vor 0.21 umsteigt.
 */
const ALTER_SPEICHERPLATZ = 'nexview.refresh'

function alteAblageRaeumen(): void {
  try {
    localStorage.removeItem(ALTER_SPEICHERPLATZ)
  } catch {
    /* Privater Modus ohne localStorage - dann liegt dort auch nichts. */
  }
}

alteAblageRaeumen()

let accessToken: string | null = null
let onSessionLost: (() => void) | null = null

export type TokenPair = {
  access_token: string
  token_type: string
  expires_in: number
}

export class ApiError extends Error {
  status: number
  /**
   * Maschinenlesbare Kennung, falls der Server eine mitschickt.
   *
   * Gebraucht für Fälle, in denen die Oberfläche mehr tun soll als den Text
   * anzuzeigen - etwa bei einer noch unbestätigten Adresse, wo statt einer
   * Fehlermeldung der Ausweg angeboten wird.
   */
  code: string | null
  /** Zusätzliche Angaben zum Fall, z. B. die betroffene Adresse. */
  data: Record<string, unknown> | null

  constructor(
    status: number,
    message: string,
    code: string | null = null,
    data: Record<string, unknown> | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.data = data
  }
}

export function setTokens(tokens: TokenPair): void {
  accessToken = tokens.access_token
}

/**
 * Ortlich vergessen, dass jemand angemeldet war.
 *
 * ⚠️ Raeumt nur den Arbeitsspeicher. Das Cookie kann dieses Skript gar nicht
 * loeschen - dafuer gibt es `logout()`, das den Server darum bittet. Wer hier
 * aufraeumt, ohne dort zu fragen, hinterlaesst ein Cookie, mit dem der
 * naechste Seitenaufruf wieder in einer Sitzung landet.
 */
export function clearTokens(): void {
  accessToken = null
}

/** Abmelden: der Server nimmt das Cookie weg. */
export async function logout(): Promise<void> {
  accessToken = null
  try {
    await fetch(mitBasis('/api/auth/logout'), { method: 'POST', credentials: 'same-origin' })
  } catch {
    // Server nicht erreichbar. Der Arbeitsspeicher ist trotzdem leer, und
    // beim naechsten Versuch faellt das Cookie ohnehin auf.
  }
}

/** Wird aufgerufen, wenn die Sitzung endgueltig abgelaufen ist. */
export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler
}

type ErrorInfo = { message: string; code: string | null; data: Record<string, unknown> | null }

/**
 * Fehlermeldungen des Servers in der eingestellten Sprache.
 *
 * ⚠️ **Warum das hier passiert und nicht im Backend.** Der Server kennt die
 * eingestellte Sprache nicht: Sie liegt im `localStorage` dieses Browsers und
 * wird nirgends mitgeschickt - und auf der Anmeldeseite gibt es nicht einmal
 * ein Konto, an dem eine Sprache hinge. Ein Server, der dort übersetzt,
 * müsste raten und läge genau bei der Person falsch, die bewusst umgeschaltet
 * hat.
 *
 * Deshalb schickt das Backend eine **Kennung** (`code`) samt Zahlen, und der
 * Satz entsteht hier aus `de.json`/`en.json` unter `errors.byCode`.
 *
 * `message` kommt trotzdem mit und ist der Rückfall - für alles, was die API
 * ohne diese Oberfläche benutzt, und für jede Kennung, deren Übersetzung noch
 * fehlt. Lieber ein deutscher Satz als eine nackte Kennung im Fehlerbanner.
 *
 * **Eine neue Meldung anlegen:** im Backend eine Kennung vergeben, dann je
 * einen Eintrag unter `errors.byCode` in `de.json` **und** `en.json`. Sonst
 * nichts - `test_fehlermeldungen.py` schlägt fehl, wenn eine Übersetzung
 * fehlt.
 */
export function uebersetzeFehler(detail: Record<string, unknown>, status: number): string {
  const code = typeof detail.code === 'string' ? detail.code : null
  const rueckfall = String(detail.message ?? `HTTP ${status}`)
  if (!code) return rueckfall

  const sonderfall = MIT_EIGENER_LOGIK[code]
  if (sonderfall) return sonderfall(detail)

  const schluessel = `errors.byCode.${code}`
  // Die Zahlen aus der Antwort stehen als Platzhalter zur Verfügung, damit
  // ein Text wie "noch {{remaining}} übrig" ohne Sonderbehandlung auskommt.
  return i18n.exists(schluessel) ? i18n.t(schluessel, { ...detail }) : rueckfall
}

/**
 * Die wenigen Meldungen, deren Satz nicht durch bloßes Einsetzen entsteht.
 *
 * Alles andere gehört **nicht** hierher, sondern unter `errors.byCode` in die
 * Sprachdateien - sonst wächst diese Tabelle mit jeder Meldung mit.
 */
const MIT_EIGENER_LOGIK: Record<string, (detail: Record<string, unknown>) => string> = {
  // Die Vorgangsnummer gehört in die Meldung: Sie ist das, was der Nutzer
  // weitergibt und was der Administrator im Protokoll sucht.
  internal_error: (detail) =>
    i18n.t('errors.internal', { id: String(detail.request_id ?? '?') }),

  // ⚠️ **Eine Aufzählung lässt sich nicht durch Einsetzen übersetzen.**
  // Der Server schickte hier einen fertigen deutschen Satz ("die öffentliche
  // Adresse **und** ein Mailserver") - das "und" steckt mitten drin, und auf
  // Englisch stand es dann trotzdem auf Deutsch da. Jetzt kommen zwei
  // Schalter, und der Satz entsteht hier.
  invite_needs_setup: (detail) => {
    const adresse = detail.needs_public_url === true
    const mail = detail.needs_mail === true
    if (adresse && mail) return i18n.t('errors.inviteNeedsBoth')
    return i18n.t(adresse ? 'errors.inviteNeedsUrl' : 'errors.inviteNeedsMail')
  },

  // Anmeldebremse: Sekunden oder Minuten, je nachdem was sich besser liest.
  // "in 900 Sekunden" wäre richtig und trotzdem unbrauchbar.
  too_many_attempts: (detail) => {
    const sekunden = Math.max(1, Math.ceil(Number(detail.retry_after ?? 1)))
    return sekunden >= 60
      ? i18n.t('errors.tooManyAttemptsMinutes', { count: Math.ceil(sekunden / 60) })
      : i18n.t('errors.tooManyAttemptsSeconds', { count: sekunden })
  },
}

async function parseError(response: Response): Promise<ErrorInfo> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return { message: detail, code: null, data: null }
    // Pydantic-Validierungsfehler kommen als Liste an.
    if (Array.isArray(detail) && detail.length > 0) {
      return {
        message: detail.map((item: { msg?: string }) => item.msg ?? '').join(' '),
        code: null,
        data: null,
      }
    }
    // Eigene Fälle liefern ein Objekt mit Kennung.
    if (detail && typeof detail === 'object') {
      return {
        message: uebersetzeFehler(detail as Record<string, unknown>, response.status),
        code: typeof detail.code === 'string' ? detail.code : null,
        data: detail as Record<string, unknown>,
      }
    }
  } catch {
    /* Antwort war kein JSON */
  }
  return { message: `HTTP ${response.status}`, code: null, data: null }
}

/**
 * Laufende Erneuerung, damit nicht mehrere gleichzeitig starten.
 *
 * Ohne das loesen mehrere parallel abgelaufene Anfragen mehrere Erneuerungen
 * aus. Bisher fiel das nicht auf, weil alle gueltig waren und die letzte
 * gewann. Mit einem Cookie ist es unschoen bis gefaehrlich: Jede Antwort
 * setzt es neu, und die Reihenfolge, in der sie ankommen, ist nicht die, in
 * der sie losgeschickt wurden.
 */
let laufendeErneuerung: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  if (laufendeErneuerung) return laufendeErneuerung

  laufendeErneuerung = (async () => {
    try {
      // Das Erneuerungs-Token faehrt als Cookie mit - es steht nirgends im
      // Code, weil dieses Skript es gar nicht sehen darf.
      const response = await fetch(mitBasis('/api/auth/refresh'), {
        method: 'POST',
        credentials: 'same-origin',
      })

      if (!response.ok) {
        clearTokens()
        return false
      }

      setTokens((await response.json()) as TokenPair)
      return true
    } catch {
      return false
    } finally {
      laufendeErneuerung = null
    }
  })()

  return laufendeErneuerung
}

type RequestOptions = {
  method?: string
  body?: unknown
  auth?: boolean
}

async function send<T>(path: string, options: RequestOptions, retry: boolean): Promise<T> {
  const { method = 'GET', body, auth = true } = options
  const headers: Record<string, string> = {}

  // Bei FormData (Datei-Upload) setzt der Browser die Kopfzeile selbst -
  // eine eigene würde die Trennmarkierung zerstören.
  const isFormData = body instanceof FormData
  if (body !== undefined && !isFormData) headers['Content-Type'] = 'application/json'
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`

  // Der Unterpfad (NEXVIEW_URL_BASE) kommt genau hier davor - die vielen
  // '/api/...'-Aufrufstellen in den Seiten bleiben wurzel-absolut.
  const response = await fetch(mitBasis(path), {
    method,
    headers,
    body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
  })

  if (response.status === 401 && auth && retry) {
    if (await refreshAccessToken()) {
      return send<T>(path, options, false)
    }
    clearTokens()
    onSessionLost?.()
  }

  if (!response.ok) {
    const info = await parseError(response)
    throw new ApiError(response.status, info.message, info.code, info.data)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}) =>
    send<T>(path, { ...options, method: 'GET' }, true),
  post: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}) =>
    send<T>(path, { ...options, method: 'POST', body }, true),
  put: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}) =>
    send<T>(path, { ...options, method: 'PUT', body }, true),
  patch: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}) =>
    send<T>(path, { ...options, method: 'PATCH', body }, true),
  // Ein optionaler Rumpf, weil eine Löschung Beipack tragen kann - die
  // Konto-Auflösung schickt die Entscheidungen über den Bestand mit.
  delete: <T>(path: string, body?: unknown, options: Omit<RequestOptions, 'method'> = {}) =>
    send<T>(path, { ...options, method: 'DELETE', body }, true),
  /** Datei-Upload (FormData statt JSON). */
  upload: <T>(path: string, body: FormData) => send<T>(path, { method: 'POST', body }, true),
}

/**
 * Datei herunterladen und dem Browser übergeben.
 *
 * Ein einfacher Link würde den Anmelde-Token nicht mitschicken - deshalb wird
 * die Datei angemeldet geholt und danach als Download angeboten.
 */
export async function downloadFile(
  path: string,
  fallbackName: string,
  /**
   * Wird ein Rumpf mitgegeben, geht die Anfrage als `POST` hinaus.
   *
   * ⚠️ Gebraucht fuer das Sicherungs-Archiv: Dessen Passwort gehoert in den
   * Rumpf. In einer Adresse landete es im Verlauf des Browsers und in jedem
   * Protokoll, durch das die Anfrage unterwegs kommt.
   */
  body?: unknown,
): Promise<void> {
  const bauen = (): RequestInit => {
    const headers: Record<string, string> = {}
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`
    if (body === undefined) return { headers }
    headers['Content-Type'] = 'application/json'
    return { method: 'POST', headers, body: JSON.stringify(body) }
  }

  let response = await fetch(mitBasis(path), bauen())
  if (response.status === 401 && (await refreshAccessToken())) {
    response = await fetch(mitBasis(path), bauen())
  }
  if (!response.ok) {
    const info = await parseError(response)
    throw new ApiError(response.status, info.message, info.code, info.data)
  }

  // Dateinamen aus der Antwort übernehmen, falls der Server einen vorgibt.
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const treffer = /filename="([^"]+)"/.exec(disposition)

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = treffer?.[1] ?? fallbackName
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * Beim Seitenstart die Sitzung aus dem Cookie wiederherstellen.
 *
 * Anders als frueher laesst sich vorher **nicht** feststellen, ob ueberhaupt
 * eine Sitzung existiert: Das Cookie ist fuer dieses Skript unsichtbar. Der
 * Versuch ist deshalb immer eine echte Anfrage, und sein 401 ist keine
 * Stoerung, sondern die Antwort "niemand angemeldet".
 */
export async function restoreSession(): Promise<boolean> {
  if (accessToken) return true
  return refreshAccessToken()
}

/**
 * Der Satz zu einer **gespeicherten** Meldung, in der eingestellten Sprache.
 *
 * Fehler beim Übergeben an Radarr/Sonarr landen als fertiger deutscher Satz in
 * der Anfrage und stehen dort Wochen später im Verlauf - lange nachdem die
 * Antwort weg ist, die sie erzeugt hat. Seit `error_detail` liegt dieselbe
 * Kennung daneben, aus der auch eine Fehlerantwort gebaut wird; damit ist das
 * hier derselbe Weg wie oben.
 *
 * Ohne Kennung bleibt der gespeicherte Satz stehen - so sehen Anfragen, die
 * vor dieser Änderung fehlgeschlagen sind, weiterhin ihre Begründung.
 */
export function gespeicherterFehler(
  detail: Record<string, unknown> | null | undefined,
  rueckfall: string | null | undefined,
): string | null {
  if (detail && typeof detail === 'object') {
    return uebersetzeFehler({ message: rueckfall ?? '', ...detail }, 0)
  }
  return rueckfall ?? null
}
