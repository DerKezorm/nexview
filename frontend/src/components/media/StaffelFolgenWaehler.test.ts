/**
 * Die Wort-Verträge des Wählers - live gemeldet, als „läuft" für alles stand.
 *
 * „wartet" ist kein „läuft": Eine wartende Freigabe kann noch abgelehnt
 * werden. Und „schon da" entscheidet der Anfrage-Status, nicht die
 * Zahlen-Arithmetik - TMDB und Sonarr zählen Folgen gern verschieden
 * (Baywatch S1: 22 gegen 21), und daran darf das Wort nicht scheitern.
 */

import { describe, expect, it } from 'vitest'

import type { SeasonInfo } from '../../api/types'
import { folgenKompakt } from '../../lib/format'
import { belegungsWort, staffelBelegt } from './staffelbelegung'

describe('belegungsWort', () => {
  it('wartende Freigaben heißen „wartet", nicht „läuft"', () => {
    expect(belegungsWort('pending_approval', false)).toBe('request.seasonPending')
  })

  it('freigegeben und suchend heißt „läuft"', () => {
    expect(belegungsWort('approved', false)).toBe('request.seasonRunning')
    expect(belegungsWort('searching', false)).toBe('request.seasonRunning')
  })

  it('geladen heißt „schon da" - auch wenn die Folgenzählung hinkt', () => {
    // Der Baywatch-S1-Fall: Anfrage längst geladen, aber TMDB zählt eine
    // Folge mehr als Sonarr - früher stand deshalb fälschlich „läuft".
    expect(belegungsWort('downloaded', false)).toBe('request.seasonHere')
  })

  it('vorhandener Bestand heißt „schon da", auch ohne Anfrage', () => {
    expect(belegungsWort(null, true)).toBe('request.seasonHere')
    expect(belegungsWort(undefined, true)).toBe('request.seasonHere')
  })
})

describe('staffelBelegt', () => {
  it('rechnet „vollständig" mit Sonarrs Zählung, nicht mit TMDBs', () => {
    // Der Baywatch-S1-Fall: TMDB zählt 22 Folgen, Sonarr kennt und hat 21 -
    // die Staffel ist vollständig, auch wenn TMDBs Phantomfolge nie kommt.
    const staffel = {
      season_number: 1,
      episode_count: 22,
      episodes_available: 21,
      episodes_total_arr: 21,
    } as SeasonInfo
    expect(staffelBelegt(staffel, 'standard')).toBe(true)
  })

  it('ohne Sonarr-Zählung bleibt TMDB der Maßstab', () => {
    const staffel = {
      season_number: 1,
      episode_count: 22,
      episodes_available: 21,
    } as SeasonInfo
    expect(staffelBelegt(staffel, 'standard')).toBe(false)
  })

  it('eine laufende Anfrage belegt die Staffel unabhängig vom Zählstand', () => {
    const staffel = {
      season_number: 2,
      episode_count: 10,
      episodes_available: 0,
      requested: true,
    } as SeasonInfo
    expect(staffelBelegt(staffel, 'standard')).toBe(true)
  })
})

describe('folgenKompakt', () => {
  it('fasst zusammenhängende Nummern zu Bereichen', () => {
    expect(folgenKompakt([1, 2, 3, 5, 8])).toBe('1–3, 5, 8')
  })

  it('sortiert, was unsortiert kommt', () => {
    expect(folgenKompakt([7, 3])).toBe('3, 7')
  })

  it('eine Nummer bleibt eine Nummer', () => {
    expect(folgenKompakt([5])).toBe('5')
  })

  it('leer bleibt leer', () => {
    expect(folgenKompakt([])).toBe('')
  })
})
