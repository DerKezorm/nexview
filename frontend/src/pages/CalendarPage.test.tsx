/**
 * Der Warnkasten bei einem Radarr- oder Sonarr-Ausfall.
 *
 * ⚠️ **`arr_warning` ist seit 24.09.2026 eine Kennung, kein fertiger Satz**
 * (siehe `errors.byCode`). Sätze wie `arr_timeout` tragen einen Platzhalter
 * (`{{service}}`), den nur die Oberfläche füllen kann - der Server kennt die
 * eingestellte Sprache nicht. Bis zum Prüfgang (26.09.2026) übergab die Seite
 * dabei keinen Wert für `service`, und die Klammern standen wörtlich im
 * Warnkasten, egal in welcher Sprache.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

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
import type { CalendarResult } from '../api/types'
import { rendern } from '../test/rendern'
import { CalendarPage } from './CalendarPage'

const holen = vi.mocked(api.get)

function kalender(teil: Partial<CalendarResult> = {}): CalendarResult {
  return {
    date_from: '2026-09-21',
    date_to: '2026-09-27',
    days: [],
    arr_warning: null,
    arr_warning_service: null,
    tmdb_warning: null,
    demo: false,
    ...teil,
  }
}

function antworten(daten: CalendarResult) {
  holen.mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return {}
    if (pfad === '/api/favorites') return []
    if (pfad === '/api/favorites/people') return []
    if (pfad.startsWith('/api/calendar')) return daten
    throw new Error(`Unerwarteter Aufruf: ${pfad}`)
  }) as never)
}

function seiteOeffnen() {
  return rendern(
    <Routes>
      <Route path="/kalender" element={<CalendarPage />} />
    </Routes>,
    { pfad: '/kalender' },
  )
}

beforeEach(() => {
  holen.mockReset()
})

describe('Hinweis bei einem Radarr- oder Sonarr-Ausfall', () => {
  it('setzt den Dienstnamen in den Platzhalter ein, statt ihn wörtlich zu zeigen', async () => {
    antworten(kalender({ arr_warning: 'arr_timeout', arr_warning_service: 'Radarr' }))
    seiteOeffnen()

    expect(
      await screen.findByText('Radarr antwortet nicht (Zeitüberschreitung).'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/\{\{service\}\}/)).not.toBeInTheDocument()
  })

  it('zeigt gar keinen Warnkasten, wenn beide Quellen antworten', async () => {
    antworten(kalender())
    seiteOeffnen()

    await screen.findByRole('heading', { name: /./ })
    expect(screen.queryByText(/antwortet nicht/)).not.toBeInTheDocument()
  })
})
