/**
 * Der rote Balken bei abgelaufenem Medienserver-Zugang.
 *
 * ⚠️ **Gemeldet am 17.09.2026:** Emby lehnte den Zugang ab, der Balken sagte
 * nur „Dein Medienserver-Zugang ist abgelaufen" - und „Erneut
 * authentifizieren" öffnete die Plex-Anmeldung. Er muss den Server nennen und
 * dessen Anmeldung anbieten.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(),
    restoreSession: vi.fn(async () => false),
    setSessionLostHandler: vi.fn(),
  }
})

import { api, restoreSession } from '../api/client'
import { rendern } from '../test/rendern'
import { WatchlistExpiredBanner } from './WatchlistExpiredBanner'

function angemeldetMit(konten: Record<string, unknown>[]) {
  vi.mocked(restoreSession).mockResolvedValueOnce(true)
  vi.mocked(api.get).mockImplementation(((pfad: string) => {
    const antworten: Record<string, unknown> = {
      '/api/setup/status': { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] },
      '/api/auth/me': {
        id: 1,
        username: 'chef',
        language: 'de',
        theme: 'dark',
        watchlist_token_invalid: true,
        mediaserver_accounts: konten,
      },
      '/api/config': {
        mediaserver_providers: ['plex', 'emby'],
        mediaserver_password_login: ['jellyfin', 'emby'],
      },
    }
    return Promise.resolve(pfad in antworten ? antworten[pfad] : [])
  }) as never)
}

describe('der Balken bei abgelaufenem Zugang', () => {
  it('nennt Emby und bietet dessen Anmeldung an statt der von Plex', async () => {
    angemeldetMit([
      { provider: 'plex', username: 'Chef', token_abgelehnt: false },
      { provider: 'emby', username: 'admin', token_abgelehnt: true },
    ])
    rendern(<WatchlistExpiredBanner />)

    expect(await screen.findByText(/Dein Zugang bei Emby ist abgelaufen/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Plex/ })).not.toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Bei Emby neu anmelden' }))

    // Benutzername und Passwort für Emby - kein Code-Ablauf bei plex.tv.
    expect(await screen.findByLabelText(/Passwort/)).toBeInTheDocument()
    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith(
      '/api/watchlist/connect/start',
      expect.anything(),
    )
  })

  it('nennt beide, wenn beide abgelaufen sind', async () => {
    angemeldetMit([
      { provider: 'plex', username: 'Chef', token_abgelehnt: true },
      { provider: 'emby', username: 'admin', token_abgelehnt: true },
    ])
    rendern(<WatchlistExpiredBanner />)

    expect(await screen.findByText(/Dein Zugang bei Plex, Emby ist abgelaufen/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bei Plex neu anmelden' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bei Emby neu anmelden' })).toBeInTheDocument()
  })
})
