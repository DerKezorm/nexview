/**
 * Die Dienste-Seite für nexcrate: Betriebsart, Koppeln, Stand.
 *
 * ⚠️ **Der Schlüssel ist der Prüfgegenstand beim Koppeln.** Der Browser
 * bekommt ihn nie zu sehen — er kennt nur die Kennung der Bitte und den Code,
 * den der Betreiber in nexcrate wiedererkennt. Geprüft wird deshalb, dass die
 * Seite bis zur Bestätigung nachfragt und danach aufhört.
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

import { api } from '../../api/client'
import type { AppSettings, NexStand } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminNexcrateSettings } from './AdminNexcrateSettings'

const einstellungen = (patch: Partial<AppSettings> = {}) =>
  ({
    beschaffung: 'arr',
    nexcrate_url: '',
    nexcrate_api_key: '',
    nexcrate_api_key_set: false,
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
  installation_id: 'mv77y2nj5lqkwsdi',
  web_url: '',
  update_verfuegbar: false,
  update_version: '',
  anime: true,
  fassungen: [],
  probleme: [],
  fehler: '',
  ...patch,
})

describe('Dienste-Seite für nexcrate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('zeigt beide Betriebsarten und schaltet auf Klick um', async () => {
    vi.mocked(api.get).mockResolvedValue(einstellungen())
    vi.mocked(api.put).mockResolvedValue(einstellungen({ beschaffung: 'nex' }))

    rendernSchlicht(<AdminNexcrateSettings />)

    const arr = await screen.findByRole('button', { name: /Radarr und Sonarr/ })
    const nex = screen.getByRole('button', { name: /^nexcrate/ })
    expect(arr).toHaveAttribute('aria-pressed', 'true')
    expect(nex).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(nex)

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/settings', { beschaffung: 'nex' }),
    )
  })

  it('fragt beim Koppeln nach, bis nexcrate bestätigt ist', async () => {
    let bestaetigt = false
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad.startsWith('/settings/nexcrate/pairing/')) {
        if (!bestaetigt) {
          bestaetigt = true
          return { state: 'pending', gespeichert: false, installation_id: '', version: '', fassungen: 0 }
        }
        return { state: 'confirmed', gespeichert: true, installation_id: 'i1', version: '0.1.0', fassungen: 4 }
      }
      return einstellungen({ nexcrate_url: 'https://nexcrate.example.com' })
    })
    vi.mocked(api.post).mockResolvedValue({
      pairing_id: 'pr_0001',
      code: '5Z3-M4G',
      poll_seconds: 1,
      expires_at: null,
    })

    rendernSchlicht(<AdminNexcrateSettings />)

    await userEvent.click(await screen.findByRole('button', { name: /Um einen Schlüssel bitten/ }))

    // Der Code steht da, damit der Betreiber die Bitte in nexcrate wiedererkennt.
    expect(await screen.findByText('5Z3-M4G')).toBeInTheDocument()
    expect(api.post).toHaveBeenCalledWith('/settings/nexcrate/pairing', {
      url: 'https://nexcrate.example.com',
    })

    // Beim nächsten Takt ist sie bestätigt - und das Nachfragen hört auf.
    expect(
      await screen.findByText(/4 Fassungen gefunden/, undefined, { timeout: 4000 }),
    ).toBeInTheDocument()
    expect(screen.queryByText('5Z3-M4G')).not.toBeInTheDocument()
  })

  it('nennt je Fassung, ob sie bereit ist, und warum nicht', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/settings/nexcrate/status') {
        return stand({
          fassungen: [
            { kennung: 'v_1', media_type: 'movie', name: 'Movies', klasse: 'hd', bereit: true, gruende: [] },
            {
              kennung: 'v_2',
              media_type: 'movie',
              name: 'Movies 4K',
              klasse: 'uhd',
              bereit: false,
              gruende: ['no_profile'],
            },
          ],
        })
      }
      return einstellungen({
        nexcrate_url: 'https://nexcrate.example.com',
        nexcrate_api_key_set: true,
      })
    })

    rendernSchlicht(<AdminNexcrateSettings />)

    expect(await screen.findByText('Movies 4K')).toBeInTheDocument()
    expect(screen.getByText('bereit')).toBeInTheDocument()
    expect(screen.getByText('nicht bereit')).toBeInTheDocument()
    expect(screen.getByText('kein Profil')).toBeInTheDocument()
  })

  it('sagt es, wenn diese nexcrate noch kein Anime sucht', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/settings/nexcrate/status') return stand({ anime: false })
      return einstellungen({
        nexcrate_url: 'https://nexcrate.example.com',
        nexcrate_api_key_set: true,
      })
    })

    rendernSchlicht(<AdminNexcrateSettings />)

    expect(await screen.findByText(/sucht noch kein Anime/)).toBeInTheDocument()
  })

  it('zeigt einen Fehler statt einer leeren Seite, wenn nexcrate nicht antwortet', async () => {
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/settings/nexcrate/status') {
        return stand({ erreichbar: false, fehler: 'nexcrate_unreachable' })
      }
      return einstellungen({
        nexcrate_url: 'https://nexcrate.example.com',
        nexcrate_api_key_set: true,
      })
    })

    rendernSchlicht(<AdminNexcrateSettings />)

    expect(await screen.findByText(/antwortet gerade nicht|nicht erreichbar/)).toBeInTheDocument()
  })

  it('fragt den Stand gar nicht erst ab, solange nichts hinterlegt ist', async () => {
    vi.mocked(api.get).mockResolvedValue(einstellungen())

    rendernSchlicht(<AdminNexcrateSettings />)

    await screen.findByRole('button', { name: /Radarr und Sonarr/ })
    expect(vi.mocked(api.get).mock.calls.map(([pfad]) => pfad)).not.toContain(
      '/settings/nexcrate/status',
    )
  })
})
