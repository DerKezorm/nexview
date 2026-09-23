/**
 * Der Papierkorb der Beschaffung – und die zwei verschiedenen Nein.
 *
 * ⚠️ **Ein Knopf, der nicht kann, darf nicht klickbar sein.** Eine Datei kann
 * weg sein, oder ihr Titel hat den Bestand verlassen. Beides endet gleich:
 * Zurückholen führt zu nichts. Wer das erst nach dem Klick erfährt, glaubt
 * eine Weile, es sei zurück.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../../api/client'
import type { PapierkorbEintrag } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminPapierkorbNex } from './AdminPapierkorbNex'

const holen = vi.mocked(api.get)
const senden = vi.mocked(api.post)

const EINTRAG: PapierkorbEintrag = {
  eintrag_id: 7,
  media_type: 'movie',
  tmdb_id: 603,
  name: 'Example Movie',
  jahr: 1999,
  fassung: 'v_6a0763e8',
  staffel: null,
  folgen: [],
  dateiname: 'Example Movie (1999).mkv',
  size_bytes: 8_000_000_000,
  geloescht_am: '2026-09-23T06:00:00Z',
  geloescht_von: 'key',
  geloescht_von_name: 'Nexview',
  datei_da: true,
  im_bestand: true,
}

function liste(eintraege: PapierkorbEintrag[], sprung = '') {
  holen.mockResolvedValue({ eintraege, sprung } as never)
}

describe('Papierkorb der Beschaffung', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('zeigt einen Eintrag und holt ihn auf Klick zurück', async () => {
    liste([EINTRAG])
    senden.mockResolvedValue(undefined as never)
    rendernSchlicht(<AdminPapierkorbNex />)

    expect(await screen.findByText('Example Movie (1999)')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Zurückholen/ }))

    await waitFor(() => {
      expect(senden).toHaveBeenCalledWith('/api/beschaffung/papierkorb/7/zurueckholen')
    })
  })

  it('sperrt den Knopf, wenn die Datei weg ist', async () => {
    liste([{ ...EINTRAG, datei_da: false }])
    rendernSchlicht(<AdminPapierkorbNex />)

    expect(await screen.findByText(/Datei ist weg/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Zurückholen/ })).toBeDisabled()
  })

  it('sperrt ihn auch, wenn der Titel den Bestand verlassen hat', async () => {
    liste([{ ...EINTRAG, im_bestand: false }])
    rendernSchlicht(<AdminPapierkorbNex />)

    expect(await screen.findByText(/nicht mehr im Bestand/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Zurückholen/ })).toBeDisabled()
  })

  it('sagt es, wenn nichts drin ist', async () => {
    liste([])
    rendernSchlicht(<AdminPapierkorbNex />)

    expect(await screen.findByText(/Papierkorb ist leer/)).toBeInTheDocument()
  })

  it('zeigt den Sprung nur, wenn es einen gibt', async () => {
    liste([EINTRAG])
    const { unmount } = rendernSchlicht(<AdminPapierkorbNex />)
    expect(await screen.findByText('Example Movie (1999)')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    unmount()

    liste([EINTRAG], 'https://nexcrate.example.com/open/recycle-bin')
    rendernSchlicht(<AdminPapierkorbNex />)
    expect(await screen.findByRole('link')).toHaveAttribute(
      'href',
      'https://nexcrate.example.com/open/recycle-bin',
    )
  })
})
