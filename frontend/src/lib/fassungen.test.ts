/**
 * Die Regeln, nach denen die Oberfläche Fassungen liest.
 *
 * Nachfolger von `uhd.test.ts`: Dort stand eine einzige Frage („darf dieses
 * Konto 4K?"), hier stehen die drei, an denen sich das Bild entscheidet -
 * welche Fassung angeboten wird, wie sie heißt, und was „unbekannt" heißt.
 */

import { describe, expect, it } from 'vitest'

import type { AppConfig, Fassung, MediaItem, SeasonInfo, User } from '../api/types'
import {
  anfragbareFassungen,
  darfFassungAnfragen,
  fassungMitArt,
  fassungName,
  fassungStatus,
  staffelFassung,
} from './fassungen'

function fassung(teil: Partial<Fassung> & Pick<Fassung, 'kennung'>): Fassung {
  return {
    media_type: 'movie',
    name: teil.kennung,
    klasse: 'hd',
    quelle: 'arr',
    haupt: false,
    bereit: true,
    offen_fuer_alle: false,
    approver_picks_target: false,
    darf_anfragen: true,
    ...teil,
  }
}

const HAUPT = fassung({ kennung: 'radarr-standard', haupt: true, offen_fuer_alle: true })
const VIERK = fassung({ kennung: 'radarr-uhd', klasse: 'uhd', name: 'Radarr 4K' })

function config(fassungen: Fassung[]): AppConfig {
  return { fassungen } as AppConfig
}

/** Wörtliche Übersetzung genügt: Geprüft wird die Auswahl, nicht der Satz. */
const t = ((key: string, werte?: Record<string, string>) =>
  werte ? `${key}:${Object.values(werte).join('|')}` : key) as never

describe('anfragbareFassungen', () => {
  it('nimmt nur, was dieses Konto darf - das hat der Server entschieden', () => {
    const gesperrt = fassung({ kennung: 'radarr-uhd', darf_anfragen: false })
    expect(
      anfragbareFassungen(config([HAUPT, gesperrt]), 'movie').map((f) => f.kennung),
    ).toEqual(['radarr-standard'])
  })

  it('lässt die Hauptfassung auch ohne eingerichtete Quelle stehen', () => {
    // Ohne Radarr sah das Formular immer schon so aus - die Anfrage sagt
    // danach selbst, dass nichts eingerichtet ist.
    const ohne = fassung({ ...HAUPT, bereit: false })
    const zweite = fassung({ kennung: 'radarr-uhd', bereit: false })
    expect(anfragbareFassungen(config([ohne, zweite]), 'movie').map((f) => f.kennung)).toEqual(
      ['radarr-standard'],
    )
  })

  it('geht je Medienart', () => {
    const serie = fassung({ kennung: 'sonarr-standard', media_type: 'tv', haupt: true })
    expect(anfragbareFassungen(config([HAUPT, serie]), 'tv').map((f) => f.kennung)).toEqual([
      'sonarr-standard',
    ])
  })
})

describe('fassungName', () => {
  it('nennt die Instanzen des ARR-Betriebs weiter Standard und 4K', () => {
    expect(fassungName(t, HAUPT)).toBe('uhd.tierStandard')
    expect(fassungName(t, VIERK)).toBe('uhd.tierUhd')
  })

  it('nennt jede andere Fassung beim Namen', () => {
    const deutsch = fassung({ kennung: 'v_7c1e90ab', name: 'Deutsch', quelle: 'nex', klasse: null })
    expect(fassungName(t, deutsch)).toBe('Deutsch')
  })

  it('nimmt für Schalter die Medienart dazu', () => {
    expect(fassungMitArt(t, VIERK)).toBe('fassung.uhdMovies')
    expect(fassungMitArt(t, fassung({ ...VIERK, media_type: 'tv' }))).toBe('fassung.uhdSeries')
  })
})

describe('fassungStatus', () => {
  it('liest den Zustand aus der Achse der Karte', () => {
    const item = {
      status: 'not_requested',
      fassungen: [
        { kennung: 'radarr-standard', name: '', klasse: 'hd', quelle: 'arr', haupt: true, status: 'downloaded' },
        { kennung: 'radarr-uhd', name: '', klasse: 'uhd', quelle: 'arr', haupt: false, status: 'requested' },
      ],
    } as MediaItem
    expect(fassungStatus(item, VIERK)).toBe('requested')
  })

  it('⚠️ ohne Achse ist der Zustand unbekannt, nicht belegt', () => {
    // Aus dem Kalender und von der Merkliste kommt die Liste nicht mit. Als
    // "liegt schon vor" gelesen, sperrte das eine erlaubte Anfrage.
    const item = { status: 'downloaded' } as MediaItem
    expect(fassungStatus(item, VIERK)).toBeNull()
  })

  it('nimmt für die Hauptfassung den Zustand der Karte', () => {
    const item = { status: 'downloaded' } as MediaItem
    expect(fassungStatus(item, HAUPT)).toBe('downloaded')
  })
})

describe('staffelFassung', () => {
  it('nimmt die Angaben der Fassung, nicht die alten Felder', () => {
    const staffel = {
      season_number: 1,
      episode_count: 10,
      episodes_available: 10,
      episodes_total_arr: 10,
      fassungen: [
        {
          kennung: 'sonarr-uhd',
          episodes_available: 0,
          requested: false,
          requested_episodes: [],
          requested_status: null,
          episodes_total: 10,
        },
      ],
    } as unknown as SeasonInfo
    expect(staffelFassung(staffel, { kennung: 'sonarr-uhd', haupt: false })?.episodes_available).toBe(0)
  })

  it('fällt für die Hauptfassung auf die alten Felder zurück', () => {
    const staffel = { season_number: 1, episode_count: 10, episodes_available: 4 } as SeasonInfo
    expect(
      staffelFassung(staffel, { kennung: 'sonarr-standard', haupt: true })?.episodes_available,
    ).toBe(4)
  })

  it('kennt eine Fassung ohne Angaben nicht', () => {
    const staffel = { season_number: 1, episode_count: 10, episodes_available: 4 } as SeasonInfo
    expect(staffelFassung(staffel, { kennung: 'sonarr-uhd', haupt: false })).toBeNull()
  })
})

describe('darfFassungAnfragen', () => {
  it('wer freigeben darf, darf jede Fassung', () => {
    const user = { can_approve: true, fassung_rechte: [] } as unknown as User
    expect(darfFassungAnfragen(user, VIERK)).toBe(true)
  })

  it('eine offene Fassung darf jeder', () => {
    const user = { can_approve: false, fassung_rechte: [] } as unknown as User
    expect(darfFassungAnfragen(user, HAUPT)).toBe(true)
  })

  it('sonst entscheidet das Recht am Konto', () => {
    const ohne = { can_approve: false, fassung_rechte: [] } as unknown as User
    const mit = {
      can_approve: false,
      fassung_rechte: [{ kennung: 'radarr-uhd', anfragen: true, auto_freigabe: false }],
    } as unknown as User
    expect(darfFassungAnfragen(ohne, VIERK)).toBe(false)
    expect(darfFassungAnfragen(mit, VIERK)).toBe(true)
  })
})
