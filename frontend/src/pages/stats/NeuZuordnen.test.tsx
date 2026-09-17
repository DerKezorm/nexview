/**
 * „Neu zuordnen" in der Vergleichstabelle.
 *
 * Der Fall ist echt (17.09.2026): Plex führte „Irenas Geheimnis" unter TMDB
 * 1291936, Jellyfin, Emby und der Dateiname unter 1026880.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  }
})

import { api } from '../../api/client'
import type { VergleichZeile, VergleichZelle } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { NeuZuordnen } from './NeuZuordnen'

const PFAD = '/data/Movies/Irena (2023) {tmdb-1026880}/Irena.mkv'

function zelle(teil: Partial<VergleichZelle>): VergleichZelle {
  return {
    zustand: 'da',
    tmdb: [],
    tvdb: [],
    imdb: [],
    jahr: 2023,
    titel: null,
    pfade: [],
    schluessel: null,
    ...teil,
  }
}

const ZEILE: VergleichZeile = {
  kennung: 'movie:tmdb:1026880',
  titel: 'Irenas Geheimnis',
  jahr: 2023,
  art: 'movie',
  zuordnung: 'tmdb',
  jahr_uneinig: false,
  ohne_kennung: false,
  zellen: {
    plex: zelle({
      zustand: 'andere_nummer',
      tmdb: [1291936],
      titel: 'Irenas Geheimnis',
      schluessel: '4711',
      pfade: [PFAD],
    }),
    jellyfin: zelle({ tmdb: [1026880], titel: "Irena's Vow", schluessel: 'j1', pfade: [PFAD] }),
    emby: zelle({ tmdb: [1026880], titel: "Irena's Vow", schluessel: 'e1' }),
  },
}

describe('Neu zuordnen', () => {
  it('schlägt die Nummer aus dem Dateinamen vor und korrigiert nur den abweichenden Server', async () => {
    vi.mocked(api.post).mockResolvedValue({ ergebnis: 'korrigiert', titel: "Irena's Vow" } as never)
    rendernSchlicht(
      <NeuZuordnen zeile={ZEILE} server={['plex', 'jellyfin', 'emby']} onSchliessen={() => {}} />,
    )

    const optionen = screen.getAllByRole('radio')
    expect(optionen).toHaveLength(2)
    expect(optionen[0]).toBeChecked()
    expect(screen.getByText('steht im Dateinamen')).toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Auf Plex korrigieren' }))

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenCalledWith('/api/admin/analyse/server-vergleich/zuordnen', {
      anbieter: 'plex',
      schluessel: '4711',
      art: 'movie',
      tmdb: 1026880,
    })
    expect(await screen.findByText(/Plex: korrigiert, jetzt „Irena's Vow“/)).toBeInTheDocument()
  })

  it('wer die andere Nummer wählt, korrigiert die anderen beiden', async () => {
    rendernSchlicht(
      <NeuZuordnen zeile={ZEILE} server={['plex', 'jellyfin', 'emby']} onSchliessen={() => {}} />,
    )
    await userEvent.setup().click(screen.getAllByRole('radio')[1])
    expect(screen.getByRole('button', { name: 'Auf Jellyfin, Emby korrigieren' })).toBeInTheDocument()
  })

  it('meldet ehrlich, wenn der Server die Korrektur wieder überschrieben hat', async () => {
    vi.mocked(api.post).mockResolvedValue({ ergebnis: 'zurueckgesprungen', titel: null } as never)
    rendernSchlicht(
      <NeuZuordnen zeile={ZEILE} server={['plex', 'jellyfin', 'emby']} onSchliessen={() => {}} />,
    )
    await userEvent.setup().click(screen.getByRole('button', { name: 'Auf Plex korrigieren' }))
    expect(await screen.findByText(/vom Server selbst wieder überschrieben/)).toBeInTheDocument()
  })
})
