/**
 * Das Abzeichen im Karussell „Aktuell beliebt“ klingt nicht nach einer Anfrage.
 *
 * Es steht auf **jeder** Karte des Karussells, fest für den ganzen Abschnitt,
 * direkt neben dem Knopf „Schnell anfragen“. Es hieß „Gerade gefragt“ bzw.
 * „In demand“ und wurde gelesen wie „dazu gibt es schon eine Anfrage“: auch auf
 * einem Titel, den nie jemand angefragt hatte, und auf einem, dessen Anfrage
 * eben abgelehnt worden war. Gemeint ist die Beliebtheit bei TMDB.
 */

import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

describe('das Abzeichen im Karussell', () => {
  it('spricht nicht die Sprache der Anfragen', () => {
    expect(de.home.trendingBadge).not.toMatch(/gefragt|anfrag/i)
    expect(en.home.trendingBadge).not.toMatch(/demand|request/i)
  })
})
