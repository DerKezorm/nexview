/**
 * Die Einträge fürs „Alles, was neu ist"-Fenster sind vollständig.
 *
 * ⚠️ **Warum es diesen Test gibt.** Ein Eintrag gilt der Oberfläche nur dann
 * als Eintrag, wenn er `lead`, `sections` **und** `small` hat — `istEintrag()`
 * in `WasNeuFenster.tsx` prüft alle drei. Fehlt eines, wird der Eintrag
 * **stillschweigend aussortiert**: Das Fenster zeigt dann die Fassung davor,
 * und der Balken erscheint gar nicht erst.
 *
 * Und genau das ist mehrfach passiert — zuletzt bei 0.22.0, wo `smallTitle`
 * und `small` fehlten. Der Fehler ist besonders tückisch, weil nichts kaputt
 * aussieht: Es steht ein vollständiges, hübsches Fenster da, nur mit dem
 * Inhalt der letzten Fassung. Wer nicht auf die Versionsnummer im Kopf achtet,
 * merkt es nicht.
 *
 * Deshalb prüft dieser Test die Form aller Einträge, statt sich darauf zu
 * verlassen, dass beim Schreiben jemand an alle vier Felder denkt.
 */

import { describe, expect, it } from 'vitest'

// ⚠️ Die redaktionellen Texte stehen seit dem Auslagern nicht mehr in
// de.json/en.json, sondern in eigenen Dateien, die die Oberflaeche erst holt,
// wenn ein Betreiber sie braucht. Dieser Waechter muss ihnen folgen: Er ist
// der einzige, der prueft, dass jeder Eintrag seine vier Pflichtfelder hat,
// und ein Eintrag ohne sie wird im Fenster lautlos verworfen.
import de from '../i18n/de.wasneu.json'
import en from '../i18n/en.wasneu.json'

/** Muss zu `istEintrag()` in WasNeuFenster.tsx passen. */
const PFLICHTFELDER = ['lead', 'sections', 'smallTitle', 'small'] as const

const SPRACHEN = { de, en } as const

type Eintrag = Record<string, unknown>

function eintraege(datei: unknown): Record<string, Eintrag> {
  return (datei as { whatsNew: { entries: Record<string, Eintrag> } }).whatsNew.entries
}

describe('„Alles, was neu ist"', () => {
  for (const [sprache, datei] of Object.entries(SPRACHEN)) {
    const alle = eintraege(datei)

    it(`${sprache}: jeder Eintrag hat alle Pflichtfelder`, () => {
      const unvollstaendig: string[] = []
      for (const [fassung, eintrag] of Object.entries(alle)) {
        const fehlend = PFLICHTFELDER.filter((feld) => !(feld in eintrag))
        if (fehlend.length > 0) unvollstaendig.push(`${fassung}: ${fehlend.join(', ')}`)
      }

      expect(
        unvollstaendig,
        'Einem Eintrag fehlen Felder. Die Oberfläche sortiert ihn dann stillschweigend ' +
          'aus und zeigt die Fassung davor — es sieht nicht kaputt aus, es ist nur ' +
          'falsch. Fehlende Felder:\n  ' + unvollstaendig.join('\n  '),
      ).toEqual([])
    })

    it(`${sprache}: die Felder haben die richtige Art`, () => {
      for (const [fassung, eintrag] of Object.entries(alle)) {
        expect(typeof eintrag.lead, `${fassung}.lead`).toBe('string')
        expect(typeof eintrag.smallTitle, `${fassung}.smallTitle`).toBe('string')
        expect(Array.isArray(eintrag.sections), `${fassung}.sections ist eine Liste`).toBe(true)
        expect(Array.isArray(eintrag.small), `${fassung}.small ist eine Liste`).toBe(true)

        // Eine leere Liste ist erlaubt an der Form, aber nie gewollt: Ein
        // Abschnitt ohne Inhalt ist eine Überschrift über nichts.
        expect((eintrag.sections as unknown[]).length, `${fassung}.sections ist nicht leer`)
          .toBeGreaterThan(0)
      }
    })

    it(`${sprache}: jeder Abschnitt sagt, wo man es findet`, () => {
      for (const [fassung, eintrag] of Object.entries(alle)) {
        for (const abschnitt of eintrag.sections as Record<string, unknown>[]) {
          // ⚠️ `where` ist der Punkt des ganzen Fensters. Ohne Wegbeschreibung
          // ist es ein Changelog mit größerer Schrift.
          for (const feld of ['title', 'where', 'body']) {
            expect(typeof abschnitt[feld], `${fassung} → Abschnitt ohne ${feld}`).toBe('string')
          }
        }
      }
    })
  }

  it('beide Sprachen kennen dieselben Fassungen', () => {
    // Sonst sieht ein Teil der Nutzer das Fenster und der andere nicht — und
    // zwar ohne dass irgendwo ein Fehler erscheint.
    expect(Object.keys(eintraege(de)).sort()).toEqual(Object.keys(eintraege(en)).sort())
  })

  it('beide Sprachen haben je Fassung gleich viele Abschnitte', () => {
    for (const fassung of Object.keys(eintraege(de))) {
      const links = (eintraege(de)[fassung].sections as unknown[]).length
      const rechts = (eintraege(en)[fassung].sections as unknown[]).length
      expect(rechts, `${fassung}: deutsch ${links} Abschnitte, englisch ${rechts}`).toBe(links)
    }
  })
})
