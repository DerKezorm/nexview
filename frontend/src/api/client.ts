/**
 * Zugriff auf das Nexview-Backend.
 *
 * Der Access-Token liegt nur im Arbeitsspeicher, der Refresh-Token im
 * localStorage - so bleibt man nach einem Neuladen der Seite angemeldet.
 * Laeuft der Access-Token ab, wird er automatisch einmal erneuert und die
 * Anfrage wiederholt.
 */

import i18n from '../i18n'

const REFRESH_STORAGE_KEY = 'nexview.refresh'

let accessToken: string | null = null
let onSessionLost: (() => void) | null = null

export type TokenPair = {
  access_token: string
  refresh_token: string
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
  localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refresh_token)
}

export function clearTokens(): void {
  accessToken = null
  localStorage.removeItem(REFRESH_STORAGE_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_STORAGE_KEY)
}

export function hasSession(): boolean {
  return accessToken !== null || getRefreshToken() !== null
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
function uebersetzeFehler(detail: Record<string, unknown>, status: number): string {
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

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false

  const response = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })

  if (!response.ok) {
    clearTokens()
    return false
  }

  setTokens((await response.json()) as TokenPair)
  return true
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

  const response = await fetch(path, {
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
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const headers: Record<string, string> = {}
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  let response = await fetch(path, { headers })
  if (response.status === 401 && (await refreshAccessToken())) {
    response = await fetch(path, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
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

/** Beim Seitenstart die Sitzung aus dem gespeicherten Refresh-Token wiederherstellen. */
export async function restoreSession(): Promise<boolean> {
  if (accessToken) return true
  return refreshAccessToken()
}
