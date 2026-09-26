/**
 * Der Hinweis auf die Beispieldaten sagt, warum sie zu sehen sind.
 *
 * Nach dem Eintragen eines TMDB-Schlüssels stand weiter "Sobald ein
 * TMDB API-Key hinterlegt ist, erscheinen hier echte Neuerscheinungen" da -
 * der Schlüssel war längst hinterlegt, die Beispieldaten aber fest
 * eingeschaltet.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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

import { api } from '../api/client'
import i18n from '../i18n'
import { rendern } from '../test/rendern'
import { DemoBanner } from './DemoBanner'

function mitConfig(config: Record<string, unknown>) {
  vi.mocked(api.get).mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return config
    return []
  }) as never)
}

beforeEach(() => {
  vi.mocked(api.get).mockReset()
})

describe('der Hinweis auf die Beispieldaten', () => {
  it('verspricht echte Titel, solange der Schlüssel fehlt', async () => {
    mitConfig({ using_demo_data: true, tmdb_configured: false })
    rendern(<DemoBanner />)

    expect(await screen.findByText(i18n.t('discover.demoBanner'))).toBeInTheDocument()
  })

  it('sagt mit Schlüssel, dass die Beispieldaten fest eingeschaltet sind', async () => {
    mitConfig({ using_demo_data: true, tmdb_configured: true })
    rendern(<DemoBanner />)

    expect(await screen.findByText(i18n.t('discover.demoBannerForced'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('discover.demoBanner'))).not.toBeInTheDocument()
  })
})
