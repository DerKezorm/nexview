/**
 * Verhaltensfestschreibung fuer NexcrateStep (kein eigener Test vorher).
 * Laeuft gleich gegen die alte und die neue Fassung (605f976 / 3b55ae2).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
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

import { api, ApiError } from '../../api/client'
import type { AppSettings, NexStand } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { NexcrateStep } from './NexcrateStep'

const einstellungen = (patch: Partial<AppSettings> = {}) =>
  ({
    beschaffung: 'nex',
    nexcrate_url: 'https://nexcrate.example.com',
    nexcrate_api_key: '',
    nexcrate_api_key_set: true,
    nexcrate_name: '',
    nexcrate_web_url: '',
    nexcrate_installation_id: '',
    nexcrate_anzeigename: false,
    ...patch,
  }) as AppSettings

const stand = (patch: Partial<NexStand> = {}): NexStand => ({
  eingerichtet: true,
  erreichbar: true,
  version: '0.1.0',
  vertrag: 'V5',
  installation_id: 'x',
  web_url: '',
  update_verfuegbar: false,
  update_version: '',
  anime: true,
  fassungen: [
    { kennung: 'v_hd', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
    { kennung: 'v_uhd', media_type: 'movie', name: '4K', klasse: 'uhd', bereit: true, gruende: [] },
  ],
  probleme: [],
  pruefung: [],
  fehler: '',
  ...patch,
})

describe('NexcrateStep Verhaltensfestschreibung (C8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('belegt nur die hd-Fassung vor und schickt beim Weiter die ganze Liste', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/settings/nexcrate/status') return stand()
      return einstellungen()
    })
    vi.mocked(api.put).mockResolvedValue({})

    const onDone = vi.fn()
    rendernSchlicht(<NexcrateStep onDone={onDone} onSkip={vi.fn()} />)

    const hd = await screen.findByRole('checkbox', { name: 'Full-HD' })
    const uhd = await screen.findByRole('checkbox', { name: '4K' })
    expect(hd).toBeChecked()
    expect(uhd).not.toBeChecked()

    await userEvent.click(screen.getByRole('button', { name: 'Speichern und weiter' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/api/settings/fassungen', [
        { kennung: 'v_hd', offen_fuer_alle: true },
        { kennung: 'v_uhd', offen_fuer_alle: false },
      ]),
    )
    expect(onDone).toHaveBeenCalled()
  })

  it('schickt den vom Betreiber geaenderten Stand, nicht die Vorbelegung', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/settings/nexcrate/status') return stand()
      return einstellungen()
    })
    vi.mocked(api.put).mockResolvedValue({})

    rendernSchlicht(<NexcrateStep onDone={vi.fn()} onSkip={vi.fn()} />)

    const uhd = await screen.findByRole('checkbox', { name: '4K' })
    await userEvent.click(uhd) // 4K zusaetzlich oeffnen

    await userEvent.click(screen.getByRole('button', { name: 'Speichern und weiter' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/api/settings/fassungen', [
        { kennung: 'v_hd', offen_fuer_alle: true },
        { kennung: 'v_uhd', offen_fuer_alle: true },
      ]),
    )
  })

  it('zeigt einen Fehler und bleibt auf der Seite, wenn das Speichern scheitert', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/settings/nexcrate/status') return stand()
      return einstellungen()
    })
    vi.mocked(api.put).mockRejectedValue(new ApiError(500, 'Unbekannt.', null))

    const onDone = vi.fn()
    rendernSchlicht(<NexcrateStep onDone={onDone} onSkip={vi.fn()} />)

    await screen.findByRole('checkbox', { name: 'Full-HD' })
    await userEvent.click(screen.getByRole('button', { name: 'Speichern und weiter' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('bietet keinen Rechte-Kasten, solange nexcrate gesperrt ist (sperrt-Befund)', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/settings/nexcrate/status') {
        return stand({ pruefung: [{ code: 'x', stufe: 'sperrt', werte: {} }] })
      }
      return einstellungen()
    })

    rendernSchlicht(<NexcrateStep onDone={vi.fn()} onSkip={vi.fn()} />)

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/api/settings/nexcrate/status'))
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument())
  })
})
