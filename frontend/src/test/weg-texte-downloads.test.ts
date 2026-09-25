/**
 * Die Downloads-Texte nennen im NEX-Betrieb nexcrate, nicht Radarr und Sonarr.
 *
 * ⚠️ Der Wächter `weg-texte.test.tsx` liest nur `de.json` und `en.json`. Die
 * Downloads-Texte liegen in eigenen Dateien, und so sagte der Untertitel im
 * NEX-Betrieb „Was in Radarr und Sonarr nicht weitergeht“ (Rundgang-Befund 8),
 * dazu vier Rückfragen und Meldungen der Knöpfe (nexbase #job-17).
 *
 * Ohne Liste: Jeder Text mit Radarr oder Sonarr braucht eine `_nex`-Fassung.
 * Ausgenommen sind nur die Gründe unter `downloads.grund`: Das sind Nexviews
 * Gründe für Radarr und Sonarr; im NEX-Betrieb kommen die Gründe als nexcrates
 * Kennungen mit eigenen Texten.
 */

import { describe, expect, it } from 'vitest'

import de from '../i18n/de.downloads.json'
import en from '../i18n/en.downloads.json'
import seite from '../pages/AdminDownloadsPage.tsx?raw'

const ARR = /Radarr|Sonarr/

function blaetter(baum: unknown, pfad = ''): [string, string][] {
  if (typeof baum === 'string') return [[pfad, baum]]
  if (typeof baum !== 'object' || baum === null) return []
  return Object.entries(baum).flatMap(([k, v]) => blaetter(v, pfad ? `${pfad}.${k}` : k))
}

function arrTexte(baum: unknown): string[] {
  return blaetter(baum)
    .filter(([pfad, text]) => ARR.test(text) && !pfad.startsWith('downloads.grund.') && !pfad.endsWith('_nex'))
    .map(([pfad]) => pfad)
}

describe('die Downloads-Texte', () => {
  for (const [sprache, baum] of Object.entries({ de, en })) {
    it(`${sprache}: jeder Text mit Radarr oder Sonarr hat eine NEX-Fassung ohne sie`, () => {
      const schluessel = arrTexte(baum)
      // Bodenschwelle: Untertitel, leere Seite, eine Rückfrage, zwei Meldungen, Import.
      expect(schluessel.length).toBeGreaterThanOrEqual(6)
      const alle = new Map(blaetter(baum))
      for (const k of schluessel) {
        const nex = alle.get(`${k}_nex`)
        expect(nex, `${k}_nex fehlt`).toBeDefined()
        expect(nex).not.toMatch(ARR)
      }
    })
  }

  it('die Seite gibt an jeder dieser Stellen den Kontext mit', () => {
    // Ein Text mit NEX-Fassung, den die Seite ohne Kontext nachschlägt, zeigt
    // trotzdem Radarr und Sonarr.
    const aufrufe = [
      "t('downloads.subtitle', weg)",
      "t('downloads.noInstances', weg)",
      "t('downloads.done.entfernen_ohne_suche', weg)",
      "t('downloads.done.import_failed', weg)",
      "t('downloads.import.empty', weg)",
      't(`downloads.confirm.${frage}.text`, { release: haenger.release, ...weg })',
    ]
    for (const aufruf of aufrufe) expect(seite, aufruf).toContain(aufruf)
  })
})
