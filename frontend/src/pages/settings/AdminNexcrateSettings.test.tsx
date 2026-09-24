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

import { api, ApiError } from '../../api/client'
import type { AppConfig, AppSettings, Fassung, NexStand } from '../../api/types'
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
  pruefung: [],
  fehler: '',
  ...patch,
})

const fassungKonfig = (patch: Partial<Fassung> = {}): Fassung => ({
  kennung: 'v_1',
  media_type: 'movie',
  name: 'Full-HD',
  klasse: 'hd',
  quelle: 'nex',
  haupt: true,
  bereit: true,
  offen_fuer_alle: false,
  approver_picks_target: false,
  darf_anfragen: true,
  ...patch,
})

const konfiguration = (patch: Partial<AppConfig> = {}) =>
  ({
    beschaffung: 'arr',
    fassungen: [],
    ...patch,
  }) as AppConfig

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
      expect(api.put).toHaveBeenCalledWith('/api/settings', { beschaffung: 'nex' }),
    )
  })

  it('fragt beim Koppeln nach, bis nexcrate bestätigt ist', async () => {
    let bestaetigt = false
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad.startsWith('/api/settings/nexcrate/pairing/')) {
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
    expect(api.post).toHaveBeenCalledWith('/api/settings/nexcrate/pairing', {
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
      if (pfad === '/api/settings/nexcrate/status') {
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
      if (pfad === '/api/settings/nexcrate/status') return stand({ anime: false })
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
      if (pfad === '/api/settings/nexcrate/status') {
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
      '/api/settings/nexcrate/status',
    )
  })

  it('unterscheidet zwei gleichnamige Fassungen an der Medienart', async () => {
    // ⚠️ Genau so stand es beim ersten Umstieg an einer echten Anlage da:
    // zweimal „Full-HD" untereinander, und der Betreiber sollte Rechte
    // freigeben, ohne zu sehen, wofür.
    vi.mocked(api.get).mockImplementation(async (pfad: string) => {
      if (pfad === '/api/settings/nexcrate/status') {
        return stand({
          fassungen: [
            {
              kennung: 'v_11111111',
              media_type: 'movie',
              name: 'Full-HD',
              klasse: 'hd',
              bereit: true,
              gruende: [],
            },
            {
              kennung: 'v_22222222',
              media_type: 'tv',
              name: 'Full-HD',
              klasse: 'hd',
              bereit: true,
              gruende: [],
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

    const zeilen = await screen.findAllByText('Full-HD')
    expect(zeilen).toHaveLength(2)
    expect(zeilen[0].closest('li')).toHaveTextContent('Filme')
    expect(zeilen[1].closest('li')).toHaveTextContent('Serien')
  })

  it('führt zum Assistenten, wenn der Server den Wechsel ihm vorbehält', async () => {
    vi.mocked(api.get).mockResolvedValue(einstellungen())
    vi.mocked(api.put).mockRejectedValue(
      new ApiError(409, 'Nur über den Assistenten.', 'beschaffung_switch_needs_assistant'),
    )
    const zumUmstieg = vi.fn()

    rendernSchlicht(<AdminNexcrateSettings zumUmstieg={zumUmstieg} />)
    await userEvent.click(await screen.findByRole('button', { name: /^nexcrate/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Nur über den Assistenten.')
    await userEvent.click(screen.getByRole('button', { name: 'Umstieg auf nexcrate' }))
    expect(zumUmstieg).toHaveBeenCalledTimes(1)
  })

  it('bietet den Assistenten bei einem anderen Fehler nicht an', async () => {
    vi.mocked(api.get).mockResolvedValue(einstellungen())
    vi.mocked(api.put).mockRejectedValue(new ApiError(422, 'Unbekannt.', 'beschaffung_invalid'))

    rendernSchlicht(<AdminNexcrateSettings zumUmstieg={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^nexcrate/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Unbekannt.')
    expect(screen.queryByRole('button', { name: 'Umstieg auf nexcrate' })).toBeNull()
  })

  describe('Rechte je Fassung (C8)', () => {
    it('zeigt im NEX-Betrieb einen Rechte-Kasten und schickt beim Umschalten genau ein Paar', async () => {
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({
            fassungen: [
              { kennung: 'v_1', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
            ],
          })
        }
        if (pfad === '/api/config') {
          return konfiguration({
            beschaffung: 'nex',
            fassungen: [fassungKonfig({ offen_fuer_alle: false })],
          })
        }
        return einstellungen({
          beschaffung: 'nex',
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })
      vi.mocked(api.put).mockResolvedValue([])

      rendernSchlicht(<AdminNexcrateSettings />)

      const haken = await screen.findByRole('checkbox', { name: 'Full-HD' })
      expect(haken).not.toBeChecked()
      // Die reine Anzeige von vorher (ohne Haken) darf nicht zusätzlich auftauchen.
      expect(screen.queryByText('Fassungen')).not.toBeInTheDocument()

      await userEvent.click(haken)

      await waitFor(() =>
        expect(api.put).toHaveBeenCalledWith('/api/settings/fassungen', [
          { kennung: 'v_1', offen_fuer_alle: true },
        ]),
      )
    })

    it('zeigt keinen Rechte-Kasten im ARR-Betrieb, auch wenn nexcrate verbunden ist', async () => {
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({
            fassungen: [
              { kennung: 'v_1', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
            ],
          })
        }
        if (pfad === '/api/config') return konfiguration({ beschaffung: 'arr' })
        return einstellungen({
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })

      rendernSchlicht(<AdminNexcrateSettings />)

      await screen.findByText('Full-HD')
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
      // Die alte, reine Anzeige bleibt unverändert stehen.
      expect(screen.getByText('Fassungen')).toBeInTheDocument()
    })

    it('zeigt den Rechte-Kasten nicht ohne eingerichtete Verbindung', async () => {
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/config') return konfiguration({ beschaffung: 'nex' })
        return einstellungen({ beschaffung: 'nex' })
      })

      rendernSchlicht(<AdminNexcrateSettings />)

      await screen.findByRole('button', { name: /^nexcrate/ })
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
      expect(vi.mocked(api.get).mock.calls.map(([pfad]) => pfad)).not.toContain(
        '/api/settings/nexcrate/status',
      )
    })

    it('bleibt sichtbar, wenn nexcrate gerade nicht erreichbar ist - das Recht ist eine reine Nexview-Einstellung', async () => {
      // ⚠️ Befund eines unabhängigen Prüfers: Der Kasten steckte vorher im
      // "erreichbar"-Zweig und verschwand mit der Verbindung, obwohl
      // `PUT /api/settings/fassungen` nexcrate nie fragt. Gerade bei einer
      // Störung soll der Betreiber eine offene Fassung noch schließen können.
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({ erreichbar: false, fehler: 'nexcrate_unreachable' })
        }
        if (pfad === '/api/config') {
          return konfiguration({
            beschaffung: 'nex',
            fassungen: [fassungKonfig({ offen_fuer_alle: true })],
          })
        }
        return einstellungen({
          beschaffung: 'nex',
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })

      rendernSchlicht(<AdminNexcrateSettings />)

      await screen.findByText(/antwortet gerade nicht|nicht erreichbar/)
      const haken = await screen.findByRole('checkbox', { name: 'Full-HD' })
      expect(haken).toBeChecked()
    })

    it('zeigt je Fassung ihren eigenen Rechte- und Bereit-Stand, nicht den einer anderen mit gleicher Medienart', async () => {
      // ⚠️ Mit nur einer Fassung im Test wäre eine Zuordnung über die
      // Medienart statt über die Kennung nicht aufgefallen - deshalb hier
      // zwei Fassungen, beide "movie", mit unterschiedlichem Stand.
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({
            fassungen: [
              { kennung: 'v_hd', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
              {
                kennung: 'v_uhd',
                media_type: 'movie',
                name: '4K',
                klasse: 'uhd',
                bereit: false,
                gruende: ['no_profile'],
              },
            ],
          })
        }
        if (pfad === '/api/config') {
          return konfiguration({
            beschaffung: 'nex',
            fassungen: [
              fassungKonfig({ kennung: 'v_hd', name: 'Full-HD', klasse: 'hd', offen_fuer_alle: true }),
              fassungKonfig({ kennung: 'v_uhd', name: '4K', klasse: 'uhd', offen_fuer_alle: false }),
            ],
          })
        }
        return einstellungen({
          beschaffung: 'nex',
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })

      rendernSchlicht(<AdminNexcrateSettings />)

      const hd = await screen.findByRole('checkbox', { name: 'Full-HD' })
      const uhd = await screen.findByRole('checkbox', { name: '4K' })
      expect(hd).toBeChecked() // offen_fuer_alle: true
      expect(uhd).not.toBeChecked() // offen_fuer_alle: false - nicht von v_hd übernommen

      const hdZeile = hd.closest('li')
      const uhdZeile = uhd.closest('li')
      expect(hdZeile).toHaveTextContent('bereit')
      expect(hdZeile).not.toHaveTextContent('kein Profil')
      expect(uhdZeile).toHaveTextContent('nicht bereit')
      expect(uhdZeile).toHaveTextContent('kein Profil')
    })

    it('sperrt den Haken, während das Umschalten läuft', async () => {
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({
            fassungen: [
              { kennung: 'v_1', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
            ],
          })
        }
        if (pfad === '/api/config') {
          return konfiguration({
            beschaffung: 'nex',
            fassungen: [fassungKonfig({ offen_fuer_alle: false })],
          })
        }
        return einstellungen({
          beschaffung: 'nex',
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })
      let freigeben: (() => void) | null = null
      vi.mocked(api.put).mockImplementation(
        () =>
          new Promise((resolve) => {
            freigeben = () => resolve([])
          }),
      )

      rendernSchlicht(<AdminNexcrateSettings />)

      const haken = await screen.findByRole('checkbox', { name: 'Full-HD' })
      expect(haken).not.toBeDisabled()

      await userEvent.click(haken)

      await waitFor(() => expect(haken).toBeDisabled())

      // ⚠️ TypeScript engt `freigeben` sonst auf `null` ein - es wird ja nur
      // innerhalb des Promise-Executors zugewiesen, nie in diesem Ablauf hier.
      if (freigeben) (freigeben as () => void)()

      await waitFor(() => expect(haken).not.toBeDisabled())
    })

    it('zeigt einen Fehler, wenn das Umschalten eines Rechts scheitert', async () => {
      vi.mocked(api.get).mockImplementation(async (pfad: string) => {
        if (pfad === '/api/settings/nexcrate/status') {
          return stand({
            fassungen: [
              { kennung: 'v_1', media_type: 'movie', name: 'Full-HD', klasse: 'hd', bereit: true, gruende: [] },
            ],
          })
        }
        if (pfad === '/api/config') {
          return konfiguration({
            beschaffung: 'nex',
            fassungen: [fassungKonfig({ offen_fuer_alle: false })],
          })
        }
        return einstellungen({
          beschaffung: 'nex',
          nexcrate_url: 'https://nexcrate.example.com',
          nexcrate_api_key_set: true,
        })
      })
      vi.mocked(api.put).mockRejectedValue(
        new ApiError(404, 'Diese Fassung gibt es nicht.', 'fassung_unknown'),
      )

      rendernSchlicht(<AdminNexcrateSettings />)

      await userEvent.click(await screen.findByRole('checkbox', { name: 'Full-HD' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Diese Fassung gibt es nicht.')
    })
  })
})
