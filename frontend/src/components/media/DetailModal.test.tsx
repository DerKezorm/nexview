/**
 * Das Detailfenster: der Anfrageknopf einer Serie.
 *
 * Befund: Auf einer Serie, die noch niemand angefragt hatte, hieß der Knopf
 * „Staffel nachfordern“, und darüber stand „Die Serie läuft bereits mit“.
 * Beides gilt erst, wenn von der Serie etwas angefragt oder da ist.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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
import type { MediaItem } from '../../api/types'
import i18n from '../../i18n'
import { rendern } from '../../test/rendern'
import { DetailModal } from './DetailModal'

const holen = vi.mocked(api.get)

function staffel(nummer: number, da = 0) {
  return {
    season_number: nummer,
    name: `Staffel ${nummer}`,
    episode_count: 6,
    air_date: '2019-05-06',
    overview: '',
    poster_url: null,
    episodes_available: da,
  }
}

function serie(status: string, staffeln: ReturnType<typeof staffel>[]): MediaItem {
  return {
    media_type: 'tv',
    tmdb_id: 1399,
    tvdb_id: null,
    title: 'Eine Serie',
    original_title: null,
    overview: '',
    poster_url: null,
    backdrop_url: null,
    release_date: '2019-05-06',
    vote_average: 7,
    vote_count: 100,
    genres: [],
    runtime_minutes: null,
    certification: null,
    original_language: null,
    seasons: staffeln,
    status,
  } as unknown as MediaItem
}

function oeffnen(item: MediaItem) {
  holen.mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
    if (pfad === '/api/media/tv/1399') return item
    throw new Error(`Unerwartet: ${pfad}`)
  }) as never)
  rendern(<DetailModal item={item} onClose={() => {}} arrConfigured quelleBereit />)
}

beforeEach(() => {
  holen.mockReset()
})

describe('der Anfrageknopf im Detailfenster', () => {
  it('sagt bei einer nie angefragten Serie weder „nachfordern“ noch „läuft bereits mit“', async () => {
    oeffnen(serie('not_requested', [staffel(1), staffel(2)]))

    expect(
      await screen.findByRole('button', { name: i18n.t('request.chooseSeason') }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: i18n.t('request.addSeason') }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/läuft bereits mit/)).not.toBeInTheDocument()
  })

  it('sagt „nachfordern“, sobald von der Serie etwas da ist', async () => {
    oeffnen(serie('partial', [staffel(1, 6), staffel(2)]))

    expect(
      await screen.findByRole('button', { name: i18n.t('request.addSeason') }),
    ).toBeInTheDocument()
    expect(screen.getByText(/läuft bereits mit/)).toBeInTheDocument()
  })
})
