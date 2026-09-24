/**
 * Der Umstiegsassistent: die Riegel, die der Betreiber im Browser spürt.
 *
 * ⚠️ **Geprüft werden die Stellen, an denen es nicht weitergeht.** Der Server
 * hält dieselben Riegel noch einmal - aber ein Knopf, der sich drücken lässt
 * und dann eine Fehlermeldung bringt, ist trotzdem falsch: Er führt jemanden
 * an eine Entscheidung heran, die er gar nicht getroffen hat.
 */

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

describe('Umstiegsassistent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
      await screen.findByText(/Beispielserie — keine TMDB-Nummer in nexcrate, die offene Anfrage bleibt/),
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
      await screen.findByText(/Example Series — zwei Einträge fielen auf denselben Platz/),
    ).toBeInTheDocument()
    expect(screen.getByText(/denselben Platz belegen/)).toBeInTheDocument()
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
