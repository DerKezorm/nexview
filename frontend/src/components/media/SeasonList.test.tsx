/**
 * Die aufgeklappte Staffel, wenn der Beschaffungsweg nicht antwortet.
 *
 * Prüfgang 26.09.2026: Bei einer stummen nexcrate stand jede Folge als
 * „fehlt noch“ da, ohne Hinweis. Jetzt sagt die Staffel dazu, dass es der
 * zuletzt bekannte Stand ist, mit demselben Satz wie die Titelseite.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
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

import { api } from '../../api/client'
import type { SeasonDetail, SeasonInfo } from '../../api/types'
import i18n from '../../i18n'
import { NACHFRAGEN_MS } from '../../lib/weg'
import { rendern } from '../../test/rendern'
import { SeasonList } from './SeasonList'

const holen = vi.mocked(api.get)

const STAFFEL = {
  season_number: 1,
  name: 'Staffel 1',
  episode_count: 2,
  air_date: null,
  overview: '',
  poster_url: null,
  episodes_available: 0,
} as SeasonInfo

function antworten(
  unbestaetigt: boolean | (() => boolean),
  config: Record<string, unknown> = {},
  abgelehnt = false,
) {
  const staffel: SeasonDetail = {
    season_number: 1,
    name: 'Staffel 1',
    overview: '',
    air_date: null,
    episodes: [
      { episode_number: 1, name: 'Folge eins', available: false },
      { episode_number: 2, name: 'Folge zwei', available: false },
    ] as SeasonDetail['episodes'],
    status_unconfirmed: false,
    status_refused: abgelehnt,
  }
  holen.mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return config
    if (pfad === '/api/detail/tv/77/season/1') {
      return {
        ...staffel,
        status_unconfirmed: typeof unbestaetigt === 'function' ? unbestaetigt() : unbestaetigt,
      }
    }
    throw new Error(`Unerwarteter Aufruf: ${pfad}`)
  }) as never)
}

async function aufklappen() {
  const b = userEvent.setup()
  rendern(<SeasonList tmdbId={77} seasons={[STAFFEL]} />)
  await b.click(screen.getByRole('button', { expanded: false }))
  await screen.findByText('Folge eins')
}

beforeEach(() => {
  holen.mockReset()
})

describe('die aufgeklappte Staffel', () => {
  it('sagt dazu, wenn der Stand nicht bestätigt ist', async () => {
    antworten(true)
    await aufklappen()

    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('detail.statusUnconfirmed'))
  })

  it('nennt im NEX-Betrieb nexcrate', async () => {
    antworten(true, { beschaffung: 'nex' })
    await aufklappen()

    expect(
      await screen.findByText(i18n.t('detail.statusUnconfirmed', { context: 'nex' })),
    ).toBeInTheDocument()
  })

  it('sagt es anders, wenn der Weg ablehnt', async () => {
    antworten(true, {}, true)
    await aufklappen()

    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('detail.statusRefused'))
  })

  it('fragt nach, bis der Stand bestätigt ist, und nimmt den Hinweis dann weg', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let aufrufe = 0
      antworten(() => {
        aufrufe += 1
        return aufrufe === 1
      })
      await aufklappen()
      expect(screen.getByRole('status')).toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(NACHFRAGEN_MS)

      await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
      expect(aufrufe).toBe(2)
      await vi.advanceTimersByTimeAsync(3 * NACHFRAGEN_MS)
      expect(aufrufe).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('schweigt, solange der Weg geantwortet hat', async () => {
    antworten(false)
    await aufklappen()

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
