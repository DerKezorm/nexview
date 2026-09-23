/**
 * Woran es hängt – und wann die Oberfläche lieber schweigt.
 *
 * ⚠️ **„Der Weg sagt es nicht" ist nicht „alles in Ordnung".** Radarr und
 * Sonarr können keinen Grund nennen; dann darf dort kein Abschnitt stehen,
 * der Vollständigkeit vortäuscht.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../../api/client'
import type { BeschaffungWarum } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { WarumNochNicht } from './WarumNochNicht'

const holen = vi.mocked(api.get)

const WARUM: BeschaffungWarum = {
  beantwortbar: true,
  bekannt: true,
  automatisch: true,
  suchwunsch: false,
  zuletzt_gesucht: null,
  naechste_suche: null,
  gruende: [
    { fassung: 'v_6a0763e8', code: 'no_fitting_release', werte: {}, darunter: [] },
    {
      fassung: 'v_b4272077',
      code: 'version_not_ready',
      werte: {},
      darunter: ['no_indexer'],
    },
  ],
}

function antworten(kann: boolean, warum: Partial<BeschaffungWarum> = {}) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.startsWith('/api/config')) {
      return Promise.resolve({
        beschaffung: kann ? 'nex' : 'arr',
        beschaffung_kann: { warum: kann, papierkorb: kann, anime: true, kalender: true, wertungen: [] },
        beschaffung_sprung: {},
        fassungen: [
          { kennung: 'v_6a0763e8', media_type: 'movie', name: 'Movies', klasse: 'hd', quelle: 'nex' },
          { kennung: 'v_b4272077', media_type: 'movie', name: 'Movies 4K', klasse: 'uhd', quelle: 'nex' },
        ],
      })
    }
    if (pfad.startsWith('/api/beschaffung/warum/')) {
      return Promise.resolve({ ...WARUM, ...warum })
    }
    return Promise.resolve({})
  })
}

describe('Woran es hängt', () => {
  beforeEach(() => {
    holen.mockReset()
  })

  it('nennt den Grund je Fassung, mit ihrem Namen', async () => {
    antworten(true)
    rendernSchlicht(<WarumNochNicht mediaType="movie" tmdbId={603} />)

    expect(await screen.findByText(/Nichts gefunden, das zum Profil passt/)).toBeInTheDocument()
    expect(screen.getByText(/Movies:/)).toBeInTheDocument()
    // ⚠️ Der Untergrund gehört zur Fassung, nicht in eine eigene Zeile.
    expect(screen.getByText(/kein Indexer/)).toBeInTheDocument()
  })

  it('schweigt, wo der Weg keinen Grund kennt', async () => {
    antworten(false)
    rendernSchlicht(<WarumNochNicht mediaType="movie" tmdbId={603} />)

    // Kein Abschnitt, keine Abfrage: Der Server wird gar nicht erst gefragt.
    await new Promise((fertig) => setTimeout(fertig, 30))
    expect(screen.queryByText(/hängt/i)).not.toBeInTheDocument()
    expect(holen.mock.calls.some(([pfad]) => String(pfad).includes('/warum/'))).toBe(false)
  })

  it('schweigt auch, wenn der Weg den Titel gar nicht kennt', async () => {
    antworten(true, { bekannt: false, gruende: [] })
    rendernSchlicht(<WarumNochNicht mediaType="movie" tmdbId={603} />)

    await new Promise((fertig) => setTimeout(fertig, 30))
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })

  it('sagt es, wenn die Automatik aus ist', async () => {
    antworten(true, { automatisch: false })
    rendernSchlicht(<WarumNochNicht mediaType="movie" tmdbId={603} />)

    expect(await screen.findByText(/Automatik ist aus/)).toBeInTheDocument()
  })
})
