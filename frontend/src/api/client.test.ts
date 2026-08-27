/**
 * Der API-Client — die Schicht, durch die jede Anfrage geht.
 *
 * ⚠️ **Hier zuerst, und das ist kein Zufall.** Diese Datei wurde in 0.21
 * grundlegend umgebaut: Das Erneuerungs-Token liegt nicht mehr im
 * `localStorage`, sondern in einem HttpOnly-Cookie, das dieses Skript weder
 * lesen noch löschen kann. Damit hängt die gesamte Anmeldung an Verhalten,
 * das man von außen nicht sieht — abmelden muss den Server fragen, Erneuern
 * darf nur einspurig laufen, und ein 401 heißt etwas anderes als vorher.
 *
 * Was diese Tests **nicht** können: beweisen, dass der Browser die Anmeldung
 * über ein Neuladen hinweg hält. Das ist Sache des Browsers, nicht unseres
 * Codes — dafür gibt es den Playwright-Test in `e2e/`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '../i18n'
import {
  ApiError,
  api,
  clearTokens,
  gespeicherterFehler,
  logout,
  restoreSession,
  setSessionLostHandler,
  setTokens,
} from './client'

/** Eine Antwort bauen, wie `fetch` sie liefert. */
function antwort(status: number, koerper: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => koerper,
    blob: async () => new Blob(),
  } as unknown as Response
}

let holen: ReturnType<typeof vi.fn>

beforeEach(() => {
  holen = vi.fn()
  vi.stubGlobal('fetch', holen)
  clearTokens()
  setSessionLostHandler(null)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// --------------------------------------------------------------------------
// Das Cookie — was dieses Skript darf und was nicht
// --------------------------------------------------------------------------

describe('Erneuern über das Cookie', () => {
  it('schickt keinen Token mit, sondern lässt das Cookie mitfahren', async () => {
    holen.mockResolvedValueOnce(antwort(200, { access_token: 'neu', expires_in: 1800 }))

    await restoreSession()

    const [pfad, optionen] = holen.mock.calls[0]
    expect(pfad).toBe('/api/auth/refresh')
    expect(optionen.method).toBe('POST')
    // ⚠️ Der springende Punkt: kein Körper. Das Token steht im Cookie, und
    // dieses Skript kennt es gar nicht.
    expect(optionen.body).toBeUndefined()
    expect(optionen.credentials).toBe('same-origin')
  })

  it('gibt beim ersten Aufruf ohne Sitzung sauber auf', async () => {
    // Genau der Fall beim Umstieg auf 0.21: kein Cookie, also 401.
    holen.mockResolvedValueOnce(antwort(401))
    await expect(restoreSession()).resolves.toBe(false)
  })

  it('räumt den alten localStorage-Platz weg', () => {
    // Wer von vor 0.21 kommt, hat dort ein gültiges Dreißig-Tage-Token
    // liegen. Es nützt der Anwendung nichts mehr, wäre aber weiterhin
    // lesbar — deshalb muss es beim Start verschwinden.
    expect(localStorage.getItem('nexview.refresh')).toBeNull()
  })
})

// --------------------------------------------------------------------------
// Die Einzelspur — ohne sie überholen sich die Erneuerungen
// --------------------------------------------------------------------------

describe('Gleichzeitige Erneuerungen', () => {
  it('läuft nur einmal, auch wenn drei Anfragen gleichzeitig ablaufen', async () => {
    // Drei Anfragen, die alle 401 bekommen und danach durchgehen.
    holen
      .mockResolvedValueOnce(antwort(401))
      .mockResolvedValueOnce(antwort(401))
      .mockResolvedValueOnce(antwort(401))
      .mockResolvedValueOnce(antwort(200, { access_token: 'neu', expires_in: 1800 }))
      .mockResolvedValue(antwort(200, { ok: true }))

    setTokens({ access_token: 'alt', token_type: 'bearer', expires_in: 1800 })
    await Promise.all([api.get('/api/a'), api.get('/api/b'), api.get('/api/c')])

    const erneuerungen = holen.mock.calls.filter(([pfad]) => pfad === '/api/auth/refresh')
    // ⚠️ Genau eine. Ohne die Einzelspur wären es drei — und weil jede Antwort
    // das Cookie neu setzt und die Reihenfolge des Eintreffens nicht die des
    // Losschickens ist, gewönne am Ende ein zufälliges.
    expect(erneuerungen).toHaveLength(1)
  })
})

// --------------------------------------------------------------------------
// Abmelden
// --------------------------------------------------------------------------

describe('Abmelden', () => {
  it('fragt den Server, statt nur den Arbeitsspeicher zu leeren', async () => {
    holen.mockResolvedValueOnce(antwort(204))
    await logout()

    expect(holen).toHaveBeenCalledWith(
      '/api/auth/logout',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
  })

  it('vergisst die Sitzung auch, wenn der Server nicht antwortet', async () => {
    holen.mockRejectedValueOnce(new Error('Netzwerk weg'))
    // Darf nicht werfen: Der Arbeitsspeicher ist danach trotzdem leer, und
    // beim nächsten Versuch fällt das Cookie ohnehin auf.
    await expect(logout()).resolves.toBeUndefined()
  })
})

// --------------------------------------------------------------------------
// Wenn die Sitzung endgültig weg ist
// --------------------------------------------------------------------------

describe('Verlorene Sitzung', () => {
  it('meldet sie genau einmal, nicht bei jeder Anfrage', async () => {
    const verloren = vi.fn()
    setSessionLostHandler(verloren)

    holen
      .mockResolvedValueOnce(antwort(401)) // die Anfrage
      .mockResolvedValueOnce(antwort(401)) // die Erneuerung scheitert
      .mockResolvedValueOnce(antwort(401)) // der Wiederholungsversuch

    setTokens({ access_token: 'alt', token_type: 'bearer', expires_in: 1800 })
    await expect(api.get('/api/geschuetzt')).rejects.toThrow(ApiError)

    expect(verloren).toHaveBeenCalledTimes(1)
  })

  it('wiederholt die Anfrage nach erfolgreicher Erneuerung genau einmal', async () => {
    holen
      .mockResolvedValueOnce(antwort(401))
      .mockResolvedValueOnce(antwort(200, { access_token: 'neu', expires_in: 1800 }))
      .mockResolvedValueOnce(antwort(200, { fertig: true }))

    setTokens({ access_token: 'alt', token_type: 'bearer', expires_in: 1800 })
    await expect(api.get('/api/geschuetzt')).resolves.toEqual({ fertig: true })

    // Nicht mehr als einmal: Sonst liefe man bei einem dauerhaft kaputten
    // Zugang in eine Schleife.
    const versuche = holen.mock.calls.filter(([pfad]) => pfad === '/api/geschuetzt')
    expect(versuche).toHaveLength(2)
  })
})

// --------------------------------------------------------------------------
// Fehlermeldungen in der eingestellten Sprache
// --------------------------------------------------------------------------

/**
 * Den Fehler einholen, den ein Aufruf wirft.
 *
 * ⚠️ Nicht `.catch((e) => e as ApiError)`: Daraus macht TypeScript die
 * Vereinigung aus Antwort **und** Fehler - und die ist `unknown`, womit jeder
 * Zugriff auf `.code` beim Bauen scheitert. Der Bau lief nur deshalb lange
 * durch, weil ihn niemand angestoßen hat.
 */
async function fehlerVon(aufruf: Promise<unknown>): Promise<ApiError> {
  try {
    await aufruf
  } catch (e) {
    return e as ApiError
  }
  throw new Error('Der Aufruf ging durch, obwohl er scheitern sollte.')
}

describe('Fehlermeldungen', () => {
  it('macht aus einer Kennung einen Satz', async () => {
    holen.mockResolvedValueOnce(
      antwort(403, { detail: { code: 'not_for_children', message: 'Deutscher Rückfall.' } }),
    )

    const fehler = await fehlerVon(api.get('/api/x'))
    expect(fehler).toBeInstanceOf(ApiError)
    expect(fehler.code).toBe('not_for_children')
    // Der Satz kommt aus de.json, nicht vom Server.
    expect(fehler.message).not.toBe('Deutscher Rückfall.')
    expect(fehler.message.length).toBeGreaterThan(0)
  })

  it('nimmt den mitgeschickten Satz, wenn die Kennung unbekannt ist', async () => {
    holen.mockResolvedValueOnce(
      antwort(400, { detail: { code: 'gibt_es_nicht', message: 'Etwas ging schief.' } }),
    )

    const fehler = await fehlerVon(api.get('/api/x'))
    // ⚠️ Lieber ein deutscher Satz als eine nackte Kennung im Fehlerbanner.
    expect(fehler.message).toBe('Etwas ging schief.')
  })

  it('kommt auch mit einer Antwort ohne JSON zurecht', async () => {
    holen.mockResolvedValueOnce({
      ok: false,
      status: 502,
      headers: new Headers(),
      json: async () => {
        throw new Error('kein JSON')
      },
    } as unknown as Response)

    const fehler = await fehlerVon(api.get('/api/x'))
    expect(fehler.status).toBe(502)
    expect(fehler.message).toContain('502')
  })

  it('fasst Pydantic-Listen zu einem Satz zusammen', async () => {
    holen.mockResolvedValueOnce(
      antwort(422, { detail: [{ msg: 'Feld fehlt.' }, { msg: 'Zu kurz.' }] }),
    )

    const fehler = await fehlerVon(api.get('/api/x'))
    expect(fehler.message).toBe('Feld fehlt. Zu kurz.')
  })
})

// --------------------------------------------------------------------------
// Kopfzeilen
// --------------------------------------------------------------------------

describe('Anfragen', () => {
  it('hängt den Zugangs-Token an, sobald einer da ist', async () => {
    holen.mockResolvedValueOnce(antwort(200, {}))
    setTokens({ access_token: 'abc', token_type: 'bearer', expires_in: 1800 })

    await api.get('/api/etwas')
    expect(holen.mock.calls[0][1].headers.Authorization).toBe('Bearer abc')
  })

  it('lässt ihn weg, wo er nicht hingehört', async () => {
    holen.mockResolvedValueOnce(antwort(200, {}))
    setTokens({ access_token: 'abc', token_type: 'bearer', expires_in: 1800 })

    await api.get('/api/setup/status', { auth: false })
    expect(holen.mock.calls[0][1].headers.Authorization).toBeUndefined()
  })

  it('setzt bei FormData keine eigene Kopfzeile', async () => {
    // ⚠️ Eine eigene würde die Trennmarkierung zerstören, die der Browser
    // selbst erzeugt - der Upload käme als Datenmüll an.
    holen.mockResolvedValueOnce(antwort(200, {}))
    await api.upload('/api/auth/me/avatar', new FormData())

    expect(holen.mock.calls[0][1].headers['Content-Type']).toBeUndefined()
  })

  it('gibt bei 204 nichts zurück, statt an leerem JSON zu scheitern', async () => {
    holen.mockResolvedValueOnce(antwort(204))
    await expect(api.post('/api/etwas')).resolves.toBeUndefined()
  })
})

describe('gespeicherterFehler', () => {
  /**
   * Fehler beim Übergeben an Radarr/Sonarr nehmen einen anderen Weg als alle
   * übrigen Meldungen: Sie landen als fertiger Satz in der Anfrage und stehen
   * von dort Wochen später im Verlauf — lange nachdem die Antwort weg ist, die
   * sie erzeugt hat.
   *
   * Gemeldet aus dem Betrieb: Die Oberfläche stand auf Englisch, und unter der
   * fehlgeschlagenen Serie stand trotzdem der deutsche Satz.
   */
  it('baut den Satz aus der Kennung', () => {
    const text = gespeicherterFehler(
      { code: 'tvdb_id_missing' },
      'Für diese Serie kennt TMDB noch keine TVDB-Kennung.',
    )
    expect(text).toBe(i18n.t('errors.byCode.tvdb_id_missing'))
  })

  it('setzt die mitgelieferten Werte ein', () => {
    const text = gespeicherterFehler(
      { code: 'arr_http_error', service: 'Sonarr', status: 500 },
      'Sonarr meldet einen Fehler (HTTP 500).',
    )
    expect(text).toContain('Sonarr')
    expect(text).toContain('500')
  })

  it('fällt ohne Kennung auf den gespeicherten Satz zurück', () => {
    // ⚠️ Der Fall älterer Anfragen: Sie sind fehlgeschlagen, bevor es die
    // Kennung gab. Ihre Begründung darf trotzdem nicht verschwinden.
    expect(gespeicherterFehler(null, 'Alter Satz aus der Datenbank.')).toBe(
      'Alter Satz aus der Datenbank.',
    )
  })

  it('behält den gespeicherten Satz, solange eine Übersetzung fehlt', () => {
    expect(
      gespeicherterFehler({ code: 'gibt_es_nicht' }, 'Der gespeicherte Satz.'),
    ).toBe('Der gespeicherte Satz.')
  })

  it('macht aus nichts nichts', () => {
    expect(gespeicherterFehler(null, null)).toBeNull()
  })
})
