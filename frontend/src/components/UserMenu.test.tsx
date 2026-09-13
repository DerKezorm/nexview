/**
 * Der Menüpunkt Downloads.
 *
 * ⚠️ **Er braucht zweierlei zugleich:** ein Administratorkonto und eine
 * Warteschlange. Der Filter prüfte bisher die Rolle und hörte dann auf; eine
 * Bedingung daneben wäre bei Administratoren nie gefragt worden, und der Punkt
 * stünde auch in einem Haus ohne Radarr und Sonarr da.
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
import { UserMenu } from './UserMenu'

const OHNE_GRENZE = { unlimited: true, used: 0, limit: 0, exhausted: false }

function angemeldetAls(konto: Record<string, unknown>, config: Record<string, unknown>) {
  // ``Once``: Der AuthProvider fragt beim Erscheinen genau einmal.
  vi.mocked(restoreSession).mockResolvedValueOnce(true)
  vi.mocked(api.get).mockImplementation(((pfad: string) => {
    const antworten: Record<string, unknown> = {
      '/api/setup/status': { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] },
      '/api/auth/me': { id: 1, username: 'chef', language: 'de', theme: 'dark', ...konto },
      '/api/config': config,
      '/api/admin/dashboard': { ungesehen: 0, befunde: [], zaehler: {} },
      '/api/admin/requests/pending/count': { pending: 0 },
      '/api/tickets/open-count': { count: 0 },
      '/api/requests/quota': { movie: OHNE_GRENZE, tv: OHNE_GRENZE },
      '/api/storage/overview': { total_bytes: 0 },
      '/api/storage/me': { used_bytes: 0, limit_bytes: null },
    }
    return Promise.resolve(pfad in antworten ? antworten[pfad] : [])
  }) as never)
}

async function oeffnen() {
  await userEvent.setup().click(await screen.findByRole('button', { name: /chef/ }))
  // Das Menü steht, sobald der Abmelden-Eintrag da ist.
  await screen.findByRole('menuitem', { name: 'Abmelden' })
}

describe('der Menüpunkt Downloads', () => {
  it('steht bei einem Administrator mit Radarr oder Sonarr', async () => {
    angemeldetAls({ role: 'admin', can_approve: true }, { radarr_configured: true })
    rendern(<UserMenu />)
    await oeffnen()
    expect(await screen.findByRole('menuitem', { name: 'Downloads' })).toHaveAttribute(
      'href',
      '/admin/downloads',
    )
  })

  it('ohne Radarr und Sonarr kein Menuepunkt, auch nicht fuer Administratoren', async () => {
    angemeldetAls(
      { role: 'admin', can_approve: true },
      {
        radarr_configured: false,
        sonarr_configured: false,
        radarr_uhd_configured: false,
        sonarr_uhd_configured: false,
      },
    )
    rendern(<UserMenu />)
    await oeffnen()
    expect(screen.getByRole('menuitem', { name: 'Einstellungen' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Downloads' })).not.toBeInTheDocument()
  })

  it('ein Entscheider sieht ihn nicht', async () => {
    angemeldetAls({ role: 'approver', can_approve: true }, { sonarr_configured: true })
    rendern(<UserMenu />)
    await oeffnen()
    expect(screen.getByRole('menuitem', { name: 'Alle Anfragen' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Downloads' })).not.toBeInTheDocument()
  })
})
