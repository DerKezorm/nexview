/**
 * Befunde aus nexcrate kommen als Kennung (Rundgang-Befund 5).
 *
 * ⚠️ Das Dashboard zeigte unter „Dienste“ nexcrates englischen Satz
 * („The automatic for album is off; nothing loads by itself.“) wörtlich in
 * der deutschen Oberfläche. Der Server schickt jetzt Kennung und Werte.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(async () => ({ beschaffung: 'nex' })),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

import type { Befund } from '../api/types'
import { rendernSchlicht } from '../test/rendern'
import { Befundliste } from './Befundliste'

function befund(werte: Record<string, string | number>, wortlaut: string | null = null): Befund {
  return {
    schluessel: 'dienst.meldet_problem:1',
    kennung: 'dienst.meldet_problem',
    schwere: 'warnung',
    bereich: 'dienste',
    werte,
    ziel: null,
    wortlaut,
  }
}

describe('ein Befund mit Kennung', () => {
  it('steht übersetzt da, mit der Art', async () => {
    rendernSchlicht(
      <Befundliste befunde={[befund({ instanz: 'nexcrate', code: 'automatic_off', kind: 'movie' })]} />,
    )
    expect(
      await screen.findByText('Die Automatik für Filme ist aus; von selbst lädt nichts.'),
    ).toBeInTheDocument()
  })

  it('ohne eigenen Text der Art steht der Grundtext', async () => {
    rendernSchlicht(<Befundliste befunde={[befund({ instanz: 'nexcrate', code: 'indexer_none' })]} />)
    expect(
      await screen.findByText('Kein Indexer ist verbunden und eingeschaltet.'),
    ).toBeInTheDocument()
  })

  it('ein Wortlaut von Radarr bleibt, wie er ist', async () => {
    rendernSchlicht(
      <Befundliste befunde={[befund({ instanz: 'Radarr' }, 'Indexers unavailable due to failures')]} />,
    )
    expect(await screen.findByText('Indexers unavailable due to failures')).toBeInTheDocument()
  })
})
