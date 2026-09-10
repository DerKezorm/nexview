/* Wer darf in 4K anfragen – dieselbe Regel wie ``User.may_request_uhd``.
 *
 * Der Fall, um den es geht, steht zuerst: ein Entscheider ohne Häkchen. Für
 * ihn fragte das Formular nach der Rolle und blendete den Umschalter aus,
 * obwohl der Server die Anfrage angenommen hätte.
 */
import { describe, expect, it } from 'vitest'

import { darfUhdAnfragen } from './uhd'

const OHNE_HAEKCHEN = {
  can_approve: false,
  can_request_uhd_movies: false,
  can_request_uhd_series: false,
}

describe('darfUhdAnfragen', () => {
  it('wer freigeben darf, darf immer – auch ohne Häkchen', () => {
    const entscheider = { ...OHNE_HAEKCHEN, can_approve: true }
    expect(darfUhdAnfragen(entscheider, 'movie')).toBe(true)
    expect(darfUhdAnfragen(entscheider, 'tv')).toBe(true)
  })

  it('alle anderen brauchen das Häkchen, und zwar je Medienart', () => {
    const nurFilme = { ...OHNE_HAEKCHEN, can_request_uhd_movies: true }
    expect(darfUhdAnfragen(nurFilme, 'movie')).toBe(true)
    expect(darfUhdAnfragen(nurFilme, 'tv')).toBe(false)
  })

  it('ohne Häkchen und ohne Freigaberecht nicht', () => {
    expect(darfUhdAnfragen(OHNE_HAEKCHEN, 'movie')).toBe(false)
    expect(darfUhdAnfragen(OHNE_HAEKCHEN, 'tv')).toBe(false)
  })

  it('ohne Anmeldung nicht', () => {
    expect(darfUhdAnfragen(null, 'movie')).toBe(false)
    expect(darfUhdAnfragen(undefined, 'tv')).toBe(false)
  })
})
