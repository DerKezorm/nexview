/**
 * Der Umstiegsassistent: die Riegel, die der Betreiber im Browser spürt.
 *
 * ⚠️ **Geprüft werden die Stellen, an denen es nicht weitergeht.** Der Server
 * hält dieselben Riegel noch einmal - aber ein Knopf, der sich drücken lässt
 * und dann eine Fehlermeldung bringt, ist trotzdem falsch: Er führt jemanden
 * an eine Entscheidung heran, die er gar nicht getroffen hat.
 */

import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  }
})

import { api } from '../../api/client'
import type { UmstiegAbbildung, UmstiegProbe, UmstiegVorab } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminUmstieg } from './AdminUmstieg'

const VORAB: UmstiegVorab = {
  downloads_laufend: 2,
  anfragen_offen: 7,
  posten: 41,
  instanzen: ['Radarr', 'Sonarr'],
}

const ABBILDUNG: UmstiegAbbildung = {
  pruefung: [],
  sperrt: false,
  arr_fassungen: [
    { kennung: 'radarr-standard', media_type: 'movie', name: 'Radarr', klasse: 'hd' },
  ],
  nex_fassungen: [{ kennung: 'v_6a0763e8', media_type: 'movie', name: 'Movies', klasse: 'hd' }],
  vorschlag: { 'radarr-standard': 'v_6a0763e8' },
}

const PROBE: UmstiegProbe = {
  fehler: [],
  bekannt: 40,
  ohne_fassung: 0,
  unbekannt: 1,
  anime_offen: 0,
  rechte_entfallen: 0,
  zu_entscheiden: [],
}

function antworten(abbildung: UmstiegAbbildung = ABBILDUNG) {
  vi.mocked(api.get).mockImplementation(async (pfad: string) => {
    if (pfad === '/api/umstieg/vorab') return VORAB as never
    if (pfad === '/api/umstieg/abbildung') return abbildung as never
    if (pfad === '/api/settings') return { nexcrate_url: '', nexcrate_api_key_set: false } as never
    return {} as never
  })
}

/**
 * Steht für die Dienste-Seite: Dort hängt `<AdminUmstieg />` nur, solange der
 * Unterreiter „Umstieg" gewählt ist (`unterTab === "umstieg" && ...`). Ein
 * Klick auf einen anderen Unterreiter und zurück hängt die Komponente aus
 * und wieder ein, ohne Reload und ohne dass jemand „Abbrechen" gedrückt
 * hätte - genau das reproduziert dieser Knopf, mit demselben QueryClient wie
 * bei einem echten Reiterwechsel.
 */
function ReiterHuelle() {
  const [zeigen, setZeigen] = useState(true)
  return (
    <div>
      <button type="button" onClick={() => setZeigen((v) => !v)}>
        Reiter wechseln
      </button>
      {zeigen ? <AdminUmstieg /> : <div>Anderer Reiter</div>}
    </div>
  )
}

describe('Umstiegsassistent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // ⚠️ Die Attrappe aus test/globals.ts ist eine einzige Map je Testdatei,
    // nicht je Test - ohne das liest ein späterer Test die Abbildung eines
    // früheren.
    sessionStorage.clear()
  })

  it('nennt die Zahlen und sagt vorher, dass es keinen Rückweg gibt', async () => {
    antworten()
    rendernSchlicht(<AdminUmstieg />)

    expect(await screen.findByText(/kein.*Rückweg außer der Sicherung/i)).toBeInTheDocument()
    expect(await screen.findByText(/2 Downloads laufen/)).toBeInTheDocument()
    expect(screen.getByText(/7 offene Anfragen/)).toBeInTheDocument()
  })

  it('lässt eine gesperrte nexcrate nicht weiter', async () => {
    antworten({
      ...ABBILDUNG,
      sperrt: true,
      nex_fassungen: [],
      pruefung: [{ code: 'nexcrate_zu_alt', stufe: 'sperrt', werte: { stage: 'V2' } }],
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    expect(await screen.findByText(/braucht mindestens/i)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /weiter/i })).toBeDisabled()
    })
  })

  it('schaltet ohne Sicherung nicht um', async () => {
    antworten()
    vi.mocked(api.post).mockResolvedValue(PROBE as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Schritt 5: Der Knopf nach vorn bleibt zu, bis die Datei liegt.
    expect(await screen.findByText(/einzige Rückweg/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^weiter$/i })).toBeDisabled()
  })

  it('sagt es, wenn kein Vorschlag möglich ist', async () => {
    // ⚠️ Gefunden im Durchlauf gegen eine echte nexcrate: Ihre Fassungen
    // hatten keine Klasse (`tier: null`), also schlug Nexview nichts vor – und
    // vier leere Auswahllisten sehen aus wie ein Fehler.
    antworten({ ...ABBILDUNG, vorschlag: { 'radarr-standard': null } })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    expect(await screen.findByText(/schlägt nichts vor/i)).toBeInTheDocument()
  })

  it('verlangt eine Entscheidung zu Posten ohne Gegenstück', async () => {
    antworten()
    vi.mocked(api.post).mockResolvedValue({
      ...PROBE,
      zu_entscheiden: [
        {
          media_type: 'movie',
          tmdb_id: 604,
          titel: 'Example Movie',
          fassung: 'radarr-standard',
          ergebnis: 'unbekannt',
          ohne_uebersetzung: false,
          kollidiert: false,
        },
      ],
    } as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))

    expect(await screen.findByText('Example Movie')).toBeInTheDocument()
    const weiter = screen.getByRole('button', { name: /^weiter$/i })
    expect(weiter).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
  })

  it('sagt vorher, welche Anfrage stehen bleibt und welche Rechte entfallen', async () => {
    antworten()
    vi.mocked(api.post).mockResolvedValue({
      ...PROBE,
      rechte_entfallen: 2,
      zu_entscheiden: [
        {
          media_type: 'tv',
          tmdb_id: 555555,
          titel: 'Beispielserie',
          fassung: 'sonarr-standard',
          ergebnis: 'unbekannt',
          ohne_uebersetzung: true,
          kollidiert: false,
          anfrage_bleibt: true,
        },
      ],
    } as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))

    expect(
      await screen.findByText(/Beispielserie: keine TMDB-Nummer in nexcrate, die offene Anfrage bleibt/),
    ).toBeInTheDocument()
    expect(screen.getByText(/2 Rechte zeigen auf Fassungen ohne Gegenstück/)).toBeInTheDocument()
  })

  it('nennt einen Titel, der einen anderen überschreiben würde, und erklärt ihn', async () => {
    // ⚠️ Der Fall, der beim ersten Umstieg an einer echten Anlage mitten im
    // Schreiben abbrach: Sonarr führt zwei Serien, die TMDB als eine zählt.
    // Der Betreiber muss das **vorher** sehen, nicht als Fehlercode danach.
    antworten()
    vi.mocked(api.post).mockResolvedValue({
      ...PROBE,
      zu_entscheiden: [
        {
          media_type: 'tv',
          tmdb_id: 1399,
          titel: 'Example Series',
          fassung: 'sonarr-standard',
          ergebnis: 'bekannt',
          ohne_uebersetzung: false,
          kollidiert: true,
        },
      ],
    } as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))

    expect(
      await screen.findByText(/Example Series: zwei Einträge fielen auf denselben Platz/),
    ).toBeInTheDocument()
    expect(screen.getByText(/denselben Platz belegen/)).toBeInTheDocument()
  })

  it('verwendet einen eindeutigen Key, wenn derselbe Titel in zwei Fassungen zu entscheiden ist', async () => {
    // ⚠️ Der React-Key war `${media_type}:${tmdb_id}` - hängt derselbe Titel
    // (Standard **und** 4K) in beiden Fassungen ohne Gegenstück, bekommen
    // beide Zeilen denselben Key. React beschwert sich darüber laut über
    // `console.error`, und beim Aktualisieren der Liste verwechselt es die
    // Zeilen.
    antworten()
    const fehler = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(api.post).mockResolvedValue({
      ...PROBE,
      zu_entscheiden: [
        {
          media_type: 'movie',
          tmdb_id: 604,
          titel: 'Example Movie',
          fassung: 'radarr-standard',
          ergebnis: 'unbekannt',
          ohne_uebersetzung: false,
          kollidiert: false,
        },
        {
          media_type: 'movie',
          tmdb_id: 604,
          titel: 'Example Movie',
          fassung: 'radarr-uhd',
          ergebnis: 'unbekannt',
          ohne_uebersetzung: false,
          kollidiert: false,
        },
      ],
    } as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))

    await waitFor(() => {
      expect(screen.getAllByText('Example Movie')).toHaveLength(2)
    })
    const doppelterKey = fehler.mock.calls.some((aufruf) =>
      String(aufruf[0]).includes('same key'),
    )
    expect(doppelterKey).toBe(false)
  })

  it('lässt zwei bisherige Fassungen nicht auf dieselbe nexcrate-Fassung zeigen', async () => {
    // ⚠️ **Das war die Ursache des ersten Fehlschlags an einer echten Anlage**
    // (23.09.2026): „Radarr FHD" und „Radarr-4K" zeigten auf dieselbe Fassung,
    // acht Filme lagen in beiden Stufen, und die Wanderung brach mitten im
    // Schreiben ab. Hier fällt es auf, während er es einstellt.
    antworten({
      ...ABBILDUNG,
      arr_fassungen: [
        { kennung: 'radarr-standard', media_type: 'movie', name: 'Radarr', klasse: 'hd' },
        { kennung: 'radarr-uhd', media_type: 'movie', name: 'Radarr 4K', klasse: 'uhd' },
      ],
      vorschlag: { 'radarr-standard': 'v_6a0763e8', 'radarr-uhd': null },
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Noch ist alles in Ordnung: „Keine" darf mehrfach vorkommen.
    expect(screen.getByRole('button', { name: /prüfen/i })).toBeEnabled()

    await userEvent.selectOptions(screen.getByLabelText('Radarr 4K'), 'v_6a0763e8')

    expect(await screen.findByText(/zeigen auf dieselbe Fassung/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /prüfen/i })).toBeDisabled()
  })

  async function bisZumNachreichen(liegen: number) {
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      if (pfad === '/api/umstieg/umschalten') {
        return {
          fassungen: 1, verlassen: [], anfragen: 4, anfragen_ohne_uebersetzung: 0,
          posten: 40, posten_schluessel: 0, posten_ohne_uebersetzung: 0, posten_doppelt: 0,
          rechte: 0, rechte_entfallen: 0, einladungen: 0, regeln: 0, zeilen_entfernt: 0,
        } as never
      }
      if (pfad === '/api/umstieg/nachreichen') return { gereicht: 1, weiter: false, liegen } as never
      return {} as never
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /sicherung anlegen/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
    })
    await userEvent.click(screen.getByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /jetzt umschalten/i }))
    await userEvent.click(await screen.findByRole('button', { name: /nachreichen/i }))
    expect(await screen.findByText('Eine Anfrage nachgereicht.')).toBeInTheDocument()
  }

  it('sagt in Schritt 7, wie viele Anfragen auf einer unbekannten Fassung liegen', async () => {
    // Bis zum 24.09.2026 stand dort nur die Zahl der übergebenen. Die
    // liegenden kamen nie an, und niemand sagte es an der Stelle, an der man
    // gerade nachgesehen hatte.
    await bisZumNachreichen(3)

    expect(
      screen.getByText(/3 freigegebene Anfragen liegen auf einer Fassung, die nexcrate nicht kennt/),
    ).toBeInTheDocument()
  })

  it('schweigt in Schritt 7, wenn nichts liegt', async () => {
    await bisZumNachreichen(0)

    expect(screen.queryByText(/nexcrate nicht kennt/)).not.toBeInTheDocument()
  })
})

describe('Umstiegsassistent: Reload während des Umstiegs (C6)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  function beforeUnloadAusloesen(): Event {
    const ereignis = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(ereignis)
    return ereignis
  }

  it('warnt vor dem Verlassen erst ab der Sicherung, nicht davor', async () => {
    antworten()
    vi.mocked(api.post).mockResolvedValue(PROBE as never)
    rendernSchlicht(<AdminUmstieg />)

    // Schritt 1: vorab.
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(false)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    // Schritt 2: verbinden.
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(false)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    // Schritt 3: abbildung.
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(false)

    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    // Schritt 4: probe.
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(false)

    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    // Schritt 5: sicherung - ab hier warnt es.
    const ereignis = beforeUnloadAusloesen()
    expect(ereignis.defaultPrevented).toBe(true)
    expect(ereignis.returnValue).toBeFalsy()
  })

  it('entfernt den Hinweis wieder, sobald ein anderer Schritt erreicht ist', async () => {
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      return {} as never
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(true)

    // Zurück zur Probe: Der Hinweis dieses Schritts ist wieder weg.
    await userEvent.click(screen.getByRole('button', { name: /zurück/i }))
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(false)
  })

  it('verweist beim Umschalten auf den Schritt, in dem die Abbildung steht', async () => {
    // Rundgang-Befund 3: „die du oben zugeordnet hast" zeigte ins Leere. Der
    // Assistent zeigt je Schritt nur einen Abschnitt; oben steht nichts.
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      return {} as never
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    const nummer = (await screen.findByText(/^Schritt \d+ von \d+$/)).textContent?.match(/\d+/)?.[0]
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /sicherung anlegen/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
    })
    await userEvent.click(screen.getByRole('button', { name: /^weiter$/i }))

    const text = await screen.findByText(/Was ist eine Fassungskennung\?/)
    expect(text.textContent).toContain(`in Schritt ${nummer} zugeordnet`)
    expect(text.textContent).not.toMatch(/oben/)
  })

  it('bleibt bestehen, wenn vom Sicherungs- in den Umschalten-Schritt gewechselt wird', async () => {
    // ⚠️ Eine auf „sicherung" verengte Bedingung bliebe grün, ohne dass ein
    // Test je den Schritt „umschalten" selbst auslöst - genau dort darf der
    // Hinweis erst recht nicht fehlen, denn dort läuft der eigentliche Aufruf.
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      return {} as never
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /sicherung anlegen/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
    })
    await userEvent.click(screen.getByRole('button', { name: /^weiter$/i }))

    // Schritt 6: umschalten.
    expect(beforeUnloadAusloesen().defaultPrevented).toBe(true)
  })

  it('übersteht einen Reload: die Abbildung steht vorbelegt, nicht nur zufällig gleich dem Vorschlag', async () => {
    // ⚠️ Eine andere Wahl als der normale Vorschlag ('v_6a0763e8'), sonst
    // bewiese der Test nichts: Beide Werte gleich zu wählen, käme auch dann
    // heraus, wenn die Wiederherstellung gar nicht liefe und nur der
    // Vorschlag füllte.
    antworten({
      ...ABBILDUNG,
      nex_fassungen: [
        ...ABBILDUNG.nex_fassungen,
        { kennung: 'v_andere', media_type: 'movie', name: 'Andere Fassung', klasse: 'hd' },
      ],
    })
    sessionStorage.setItem(
      'nexview.umstieg.abbildung',
      JSON.stringify({ 'radarr-standard': 'v_andere' }),
    )
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Die Auswahl steht schon auf dem gespeicherten Wert, nicht auf dem
    // Vorschlag - sie kam also wirklich aus dem Sitzungsspeicher.
    expect(await screen.findByLabelText('Radarr')).toHaveValue('v_andere')
  })

  it('übersteht einen Reload: der Sicherungsname steht vorbelegt, kein erneutes Anlegen nötig', async () => {
    sessionStorage.setItem('nexview.umstieg.sicherungName', 'sicherung-alt.db')
    antworten()
    vi.mocked(api.post).mockResolvedValue(PROBE as never)
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))

    // Der alte Name steht schon da; „Weiter" ist offen, ohne dass hier neu
    // gesichert werden musste - der Server prüft beim Umschalten ohnehin, ob
    // die Datei wirklich liegt.
    expect(await screen.findByText(/sicherung-alt\.db/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
    expect(api.post).not.toHaveBeenCalledWith('/api/umstieg/sicherung')
  })

  it('verwirft eine gespeicherte Abbildung, die eine Fassung nennt, die es nicht mehr gibt', async () => {
    // Die gespeicherte Abbildung zeigt auf eine Fassung, die nach dem Reload
    // nicht mehr in der Liste steht - etwa weil sie in nexcrate inzwischen
    // umbenannt oder entfernt wurde.
    sessionStorage.setItem(
      'nexview.umstieg.abbildung',
      JSON.stringify({ 'radarr-standard': 'v_verschwunden' }),
    )
    antworten()
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Verworfen, nicht angewendet: Der normale Vorschlag springt stattdessen
    // ein - stünde die verschwundene Kennung noch in der Auswahl, zeigte das
    // Feld sie nicht als gültig gewählten Wert.
    expect(await screen.findByLabelText('Radarr')).toHaveValue('v_6a0763e8')
  })

  it('verwirft nur die veraltete Zeile, nicht die ganze gespeicherte Abbildung', async () => {
    // ⚠️ Vorher warf eine einzige ungültige Zeile die GANZE Abbildung weg -
    // auch eine andere Zeile, die von Hand richtig gesetzt war. Zwei
    // bisherige Fassungen, nur eine davon zeigt auf eine verschwundene
    // nexcrate-Fassung.
    const ABBILDUNG_ZWEI_ARR: UmstiegAbbildung = {
      ...ABBILDUNG,
      arr_fassungen: [
        ...ABBILDUNG.arr_fassungen,
        { kennung: 'radarr-uhd', media_type: 'movie', name: 'Radarr 4K', klasse: 'uhd' },
      ],
      vorschlag: { ...ABBILDUNG.vorschlag, 'radarr-uhd': null },
    }
    antworten(ABBILDUNG_ZWEI_ARR)
    sessionStorage.setItem(
      'nexview.umstieg.abbildung',
      JSON.stringify({ 'radarr-standard': 'v_verschwunden', 'radarr-uhd': 'v_6a0763e8' }),
    )
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Nur „Radarr" fällt auf den Vorschlag zurück; „Radarr 4K" bleibt bei der
    // von Hand gewählten, weiterhin gültigen Fassung stehen (sie weicht
    // bewusst vom eigenen Vorschlag „Keine" ab).
    expect(await screen.findByLabelText('Radarr')).toHaveValue('v_6a0763e8')
    expect(await screen.findByLabelText('Radarr 4K')).toHaveValue('v_6a0763e8')
  })

  it('verwirft eine gespeicherte Zeile, deren Arr-Instanz es nicht mehr gibt', async () => {
    // ⚠️ Ohne die Prüfung auf die Arr-Kennung selbst bliebe eine Zeile für
    // eine längst entfernte Radarr/Sonarr-Instanz stehen - unsichtbar, denn
    // dafür gibt es gar keine Auswahlliste mehr, aber ihr Ziel zählt bei der
    // Dopplungsprüfung weiter mit und blockiert „Prüfen" grundlos.
    antworten()
    sessionStorage.setItem(
      'nexview.umstieg.abbildung',
      JSON.stringify({ 'radarr-standard': 'v_6a0763e8', 'radarr-entfernt': 'v_6a0763e8' }),
    )
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    expect(await screen.findByLabelText('Radarr')).toHaveValue('v_6a0763e8')
    expect(screen.queryByText(/zeigen auf dieselbe Fassung/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /prüfen/i })).toBeEnabled()
  })

  it('räumt den Sitzungsspeicher nach dem Umschalten auf', async () => {
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      if (pfad === '/api/umstieg/umschalten') {
        return {
          fassungen: 1, verlassen: [], anfragen: 0, anfragen_ohne_uebersetzung: 0,
          posten: 0, posten_schluessel: 0, posten_ohne_uebersetzung: 0, posten_doppelt: 0,
          rechte: 0, rechte_entfallen: 0, einladungen: 0, regeln: 0, zeilen_entfernt: 0,
        } as never
      }
      return {} as never
    })
    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /sicherung anlegen/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^weiter$/i })).toBeEnabled()
    })
    expect(sessionStorage.getItem('nexview.umstieg.abbildung')).not.toBeNull()
    expect(sessionStorage.getItem('nexview.umstieg.sicherungName')).not.toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /jetzt umschalten/i }))
    await screen.findByRole('button', { name: /nachreichen/i })

    expect(sessionStorage.getItem('nexview.umstieg.abbildung')).toBeNull()
    expect(sessionStorage.getItem('nexview.umstieg.sicherungName')).toBeNull()
  })

  it('übersteht ein Aushängen, das kein Abbrechen ist (Reiterwechsel weg und zurück, kein Reload)', async () => {
    // ⚠️ Befund des unabhängigen Prüfers: `AdminUmstieg` hängt nur, solange
    // der Unterreiter „Umstieg" gewählt ist. Ein Klick auf einen anderen
    // Unterreiter und zurück hängt die Komponente aus und wieder ein - das
    // ist kein „Abbrechen", und darf deshalb nichts löschen.
    antworten()
    rendernSchlicht(<ReiterHuelle />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    expect(sessionStorage.getItem('nexview.umstieg.abbildung')).not.toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))
    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))

    expect(sessionStorage.getItem('nexview.umstieg.abbildung')).not.toBeNull()
  })

  it('behält eine bewusst auf „Keine" gestellte Abbildung über einen Reiterwechsel', async () => {
    // ⚠️ Der eigentliche Kern des Befunds: Eine mit Absicht abweichend vom
    // Vorschlag getroffene Wahl darf nicht still durch den Vorschlag ersetzt
    // werden, nur weil die Komponente kurz aus- und wieder eingehängt wurde.
    antworten()
    rendernSchlicht(<ReiterHuelle />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    await userEvent.selectOptions(await screen.findByLabelText('Radarr'), '')
    expect(await screen.findByLabelText('Radarr')).toHaveValue('')

    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))
    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    expect(await screen.findByLabelText('Radarr')).toHaveValue('')
  })

  it('behält einen angelegten Sicherungsnamen über einen Reiterwechsel', async () => {
    antworten()
    vi.mocked(api.post).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/umstieg/probe') return PROBE as never
      if (pfad === '/api/umstieg/sicherung') {
        return { name: 'sicherung-echt.db', groesse: 1, erstellt: '2026-09-24T10:00:00' } as never
      }
      return {} as never
    })
    rendernSchlicht(<ReiterHuelle />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /prüfen/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^weiter$/i }))
    await userEvent.click(await screen.findByRole('button', { name: /sicherung anlegen/i }))
    await screen.findByText(/sicherung-echt\.db/)

    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))
    await userEvent.click(screen.getByRole('button', { name: /reiter wechseln/i }))

    expect(sessionStorage.getItem('nexview.umstieg.sicherungName')).toBe('sicherung-echt.db')
  })

  it('läuft weiter, wenn sessionStorage wirft (privates Fenster, gesperrter Speicher)', async () => {
    antworten()
    vi.spyOn(window.sessionStorage, 'getItem').mockImplementation(() => {
      throw new Error('gesperrt')
    })
    vi.spyOn(window.sessionStorage, 'setItem').mockImplementation(() => {
      throw new Error('gesperrt')
    })
    vi.spyOn(window.sessionStorage, 'removeItem').mockImplementation(() => {
      throw new Error('gesperrt')
    })

    rendernSchlicht(<AdminUmstieg />)

    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))
    await userEvent.click(await screen.findByRole('button', { name: /weiter/i }))

    // Ohne Speicher trotzdem nutzbar: der Vorschlag füllt die Auswahl wie eh.
    expect(await screen.findByLabelText('Radarr')).toHaveValue('v_6a0763e8')
  })
})
