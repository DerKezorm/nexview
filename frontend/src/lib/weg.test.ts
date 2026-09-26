/**
 * Ein unbestätigter Stand gilt sofort als veraltet.
 *
 * Zweite Prüfrunde 26.09.2026: Nach einem Seitenwechsel kam der Hinweis
 * „antwortet gerade nicht“ aus dem Zwischenspeicher zurück, obwohl der Weg
 * längst wieder antwortete. Das Nachfragen im Takt deckt die offene Seite ab,
 * `staleTime` die Rückkehr auf sie.
 */

import { describe, expect, it } from 'vitest'

import { NACHFRAGEN_MS, nachfragenSolangeUnbestaetigt } from './weg'

const regel = nachfragenSolangeUnbestaetigt(30 * 60 * 1000)
const mit = (data: object | undefined) => ({ state: { data } })

describe('nachfragenSolangeUnbestaetigt', () => {
  it('lässt einen bestätigten Stand so lange stehen wie bisher', () => {
    expect(regel.staleTime(mit({ status_unconfirmed: false }))).toBe(30 * 60 * 1000)
    expect(regel.refetchInterval(mit({ status_unconfirmed: false }))).toBe(false)
  })

  it('behandelt einen unbestätigten Stand als sofort veraltet und fragt nach', () => {
    expect(regel.staleTime(mit({ status_unconfirmed: true }))).toBe(0)
    expect(regel.refetchInterval(mit({ status_unconfirmed: true }))).toBe(NACHFRAGEN_MS)
  })

  it('fragt auch nach einer Ablehnung nach', () => {
    expect(regel.staleTime(mit({ status_refused: true }))).toBe(0)
    expect(regel.refetchInterval(mit({ status_refused: true }))).toBe(NACHFRAGEN_MS)
  })

  it('fragt vor der ersten Antwort nichts Besonderes', () => {
    expect(regel.staleTime(mit(undefined))).toBe(30 * 60 * 1000)
    expect(regel.refetchInterval(mit(undefined))).toBe(false)
  })
})
