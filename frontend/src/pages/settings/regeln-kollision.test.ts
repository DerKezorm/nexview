/**
 * Die Kollisionsrechnung — das einzige Stück echter Logik in dieser Seite.
 *
 * ⚠️ **Warum das hier steht.** Der Kopf von `AdminRegeln.tsx` nennt sie „eine
 * Rechnung und keine Schätzung", und der ganze Umgang mit Widersprüchen hängt
 * daran: Was sie meldet, liest der Betreiber als Tatsache. Eine unabhängige
 * Prüfung fand am 03.09.2026, dass sie ungeprüft war.
 *
 * ⚠️ **Die Grenzen sind der Kern:** „von" schließt ein, „bis" schließt aus.
 * Nur deshalb überschneiden sich „ab 5" und „unter 5" nicht — mit zwei
 * einschließenden Grenzen meldete die Oberfläche einen Widerspruch, den es
 * nicht gibt, und das war beim ersten Blick sofort zu sehen.
 */

import { describe, expect, it } from 'vitest'

import { ueberschneiden, type Bedingung, type Feld, type Regel } from './regeln-kollision'

const FELDER: Feld[] = [
  { kennung: 'typ', name: 'Typ', art: 'menge' },
  { kennung: 'genre', name: 'Genre', art: 'menge' },
  { kennung: 'bewertung', name: 'Bewertung', art: 'zahl' },
  { kennung: 'jahr', name: 'Jahr', art: 'zahl' },
]

function regel(bedingungen: Bedingung[], id = 1): Regel {
  return {
    id,
    position: id,
    name: `Regel ${id}`,
    aktiv: true,
    bedingungen,
    entscheidung: 'freigeben',
    hausbestand: false,
    begruendung: '',
    trotzdem_fragen: false,
  }
}

const stossen = (a: Bedingung[], b: Bedingung[]) =>
  ueberschneiden(regel(a, 1), regel(b, 2), FELDER)

describe('ueberschneiden', () => {
  it('meldet „ab 5" und „unter 5" nicht als Widerspruch', () => {
    expect(
      stossen([{ feld: 'bewertung', von: 5, bis: null }], [{ feld: 'bewertung', von: null, bis: 5 }]),
    ).toBe(false)
  })

  it('meldet überlappende Bereiche sehr wohl', () => {
    expect(
      stossen([{ feld: 'bewertung', von: 5, bis: null }], [{ feld: 'bewertung', von: null, bis: 6 }]),
    ).toBe(true)
  })

  it('schließt sich ausschließende Mengen aus', () => {
    expect(stossen([{ feld: 'typ', werte: ['movie'] }], [{ feld: 'typ', werte: ['tv'] }])).toBe(false)
  })

  it('erkennt einen gemeinsamen Wert in zwei Mengen', () => {
    expect(
      stossen([{ feld: 'genre', werte: ['99', '18'] }], [{ feld: 'genre', werte: ['18', '28'] }]),
    ).toBe(true)
  })

  it('lässt ein Feld, das nur eine Regel nennt, durch', () => {
    // Die andere sagt dazu nichts - sie erlaubt damit alles, und das kann die
    // Überschneidung nicht verhindern.
    expect(stossen([{ feld: 'typ', werte: ['movie'] }], [{ feld: 'bewertung', von: 5, bis: null }])).toBe(
      true,
    )
  })

  it('genügt ein sich ausschließendes Paar, um alles auszuschließen', () => {
    expect(
      stossen(
        [
          { feld: 'typ', werte: ['movie'] },
          { feld: 'bewertung', von: 8, bis: null },
        ],
        [
          { feld: 'typ', werte: ['tv'] },
          { feld: 'bewertung', von: 8, bis: null },
        ],
      ),
    ).toBe(false)
  })

  it('rechnet mit offenen Grenzen', () => {
    expect(
      stossen([{ feld: 'jahr', von: 2026, bis: null }], [{ feld: 'jahr', von: null, bis: 2020 }]),
    ).toBe(false)
    expect(
      stossen([{ feld: 'jahr', von: 2026, bis: null }], [{ feld: 'jahr', von: null, bis: null }]),
    ).toBe(true)
  })

  it('meldet zwei gleiche Regeln als Überschneidung', () => {
    const gleich: Bedingung[] = [{ feld: 'typ', werte: ['movie'] }]
    expect(stossen(gleich, gleich)).toBe(true)
  })
})
