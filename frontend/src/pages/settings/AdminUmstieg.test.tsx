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
})
