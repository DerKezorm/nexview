/**
 * Kein Gedankenstrich in Texten, die dieser Lauf angefasst hat oder die zum
 * NEX-Modus gehören.
 *
 * ⚠️ Die Projektregel gilt für alle Texte, die der Nutzer sieht. Ein Griff
 * durch beide Sprachdateien fand 384 (de) bzw. 365 (en) Stellen mit „–" oder
 * „—" - zu viele für einen Lauf. Geprüft (und repariert) wird hier deshalb
 * nur, was zu diesem Auftrag gehört: `umstieg.*`, `nexcrate.*`, Schlüssel auf
 * `_nex`, und `befunde.*fremde*`. Der Rest ist gezählt, nicht angefasst -
 * siehe Bericht.
 */

import { describe, expect, it } from 'vitest'

import de from '../i18n/de.json'
import en from '../i18n/en.json'

const DASH = /[–—]/

type Baum = { [schluessel: string]: Baum | string }

function gehoertZumLauf(pfad: string[]): boolean {
  const voll = pfad.join('.')
  const letztes = pfad[pfad.length - 1] ?? ''
  return (
    pfad[0] === 'umstieg' ||
    pfad[0] === 'nexcrate' ||
    letztes.endsWith('_nex') ||
    (pfad[0] === 'befunde' && voll.includes('fremde'))
  )
}

function sammleTreffer(knoten: unknown, pfad: string[], raus: string[]) {
  if (typeof knoten === 'string') {
    if (DASH.test(knoten) && gehoertZumLauf(pfad)) raus.push(pfad.join('.'))
    return
  }
  if (knoten && typeof knoten === 'object') {
    for (const [k, v] of Object.entries(knoten as Baum)) {
      sammleTreffer(v, [...pfad, k], raus)
    }
  }
}

describe('Keine Gedankenstriche im Bereich dieses Laufs', () => {
  it.each([
    ['de', de],
    ['en', en],
  ])('%s: umstieg.*, nexcrate.*, *_nex und befunde.*fremde* sind sauber', (_sprache, baum) => {
    const treffer: string[] = []
    sammleTreffer(baum, [], treffer)
    expect(treffer, `Gedankenstrich gefunden in:\n${treffer.join('\n')}`).toEqual([])
  })
})
