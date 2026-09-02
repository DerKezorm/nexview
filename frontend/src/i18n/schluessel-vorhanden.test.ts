/**
 * Jeder Schlüssel, den der Code nachschlägt, muss es auch geben — in beiden Sprachen.
 *
 * ⚠️ **Warum das ein zweiter Test ist und nicht in `vollstaendig.test.ts` passt.**
 * Der dortige Wächter vergleicht `de.json` gegen `en.json`. Er findet damit den
 * Fall „ein Text fehlt auf einer Seite" — aber nicht den Fall, in dem ein
 * Schlüssel auf **beiden** Seiten fehlt. Dann sind beide Mengen gleich groß,
 * und der Vergleich bleibt grün, während auf dem Bildschirm die rohe
 * Schlüsselzeile steht.
 *
 * Genau so ist `adminUsers.passwordTooShort` durchgerutscht: Im Profil las
 * jeder, der ein zu kurzes Passwort eingab, statt „Mindestens acht Zeichen."
 * die Zeichenkette `adminUsers.passwordTooShort` — in beiden Sprachen, weil
 * `fallbackLng: false` gesetzt ist und i18next dann den Schlüssel selbst
 * zurückgibt.
 *
 * Geprüft werden nur **wörtliche** Schlüssel. Zusammengesetzte
 * (`t('storageAdmin.period' + art)`) kann kein Test auflösen; sie fallen
 * bewusst durch, statt eine Ausnahmeliste zu erzwingen, die jemand pflegen
 * müsste.
 */

import { describe, expect, it } from 'vitest'

import de from './de.json'
import deWasNeu from './de.wasneu.json'
import en from './en.json'
import enWasNeu from './en.wasneu.json'

/**
 * Alle Knoten, nicht nur die Blätter.
 *
 * `t('whatsNew.entries', { returnObjects: true })` holt einen ganzen Teilbaum;
 * ein Test, der nur Blattpfade kennt, hielte das für einen fehlenden Eintrag.
 */
function knoten(wert: unknown, praefix = ''): string[] {
  if (typeof wert !== 'object' || wert === null || Array.isArray(wert)) {
    return praefix ? [praefix] : []
  }
  return Object.entries(wert as Record<string, unknown>).flatMap(([k, v]) => {
    const pfad = praefix ? `${praefix}.${k}` : k
    return [pfad, ...knoten(v, pfad)]
  })
}

/** i18next hängt bei `{ count }` eine Endung an. Beide Schreibweisen zählen. */
const PLURAL = ['', '_one', '_other', '_zero', '_two', '_few', '_many']

function kennt(menge: Set<string>, schluessel: string): boolean {
  return PLURAL.some((endung) => menge.has(schluessel + endung))
}

/* ⚠️ **Zusammengeführt, weil die Oberfläche es auch zusammenführt.**
   Die redaktionellen `whatsNew.entries` liegen seit dem Auslagern in einer
   eigenen Datei, die `i18n/wasneu.ts` später tief in denselben Namensraum hängt.
   Wer hier nur `de.json` prüft, hält jeden dieser Schlüssel fälschlich für
   fehlend; wer nur die eine Datei prüft, sieht den Rest nicht. Geprüft wird
   deshalb, was am Ende im Namensraum steht. */
const deutsch = new Set(knoten({ ...de, whatsNew: { ...de.whatsNew, ...deWasNeu.whatsNew } }))
const englisch = new Set(knoten({ ...en, whatsNew: { ...en.whatsNew, ...enWasNeu.whatsNew } }))

const DATEIEN = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  eager: true,
  import: 'default',
}) as Record<string, string>

/** `t('a.b')` oder `t("a.b", …)` — mindestens ein Punkt, sonst ist es kein Schlüssel. */
const AUFRUF = /\bt\(\s*(['"])([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)\1\s*[,)]/g

/** Diese Datei nennt zwangsläufig Schlüssel, die es nicht gibt. */
const AUSGENOMMEN = /schluessel-vorhanden\.test\.ts$/

function gefundeneSchluessel(): Array<{ schluessel: string; datei: string }> {
  const treffer: Array<{ schluessel: string; datei: string }> = []
  for (const [datei, inhalt] of Object.entries(DATEIEN)) {
    if (AUSGENOMMEN.test(datei)) continue
    for (const m of inhalt.matchAll(AUFRUF)) {
      treffer.push({ schluessel: m[2], datei })
    }
  }
  return treffer
}

describe('Übersetzungsschlüssel', () => {
  /**
   * ⚠️ **Zuerst prüfen, dass überhaupt etwas geprüft wird.**
   * Greift das Muster ins Leere, bestünde die Regel darunter mit einer leeren
   * Liste — ein Wächter, der nichts sieht, meldet lebenslang „alles in Ordnung".
   */
  it('findet die Aufrufe überhaupt', () => {
    expect(Object.keys(DATEIEN).length).toBeGreaterThan(100)
    expect(gefundeneSchluessel().length).toBeGreaterThan(1000)
  })

  it('erkennt einen erfundenen Schlüssel', () => {
    // Ohne das wäre nicht bewiesen, dass `kennt` überhaupt Nein sagen kann.
    expect(kennt(deutsch, 'gibt.es.nicht')).toBe(false)
    expect(kennt(deutsch, 'common.close')).toBe(true)
  })

  it('gibt es alle, in beiden Sprachen', () => {
    const fehlend: string[] = []
    for (const { schluessel, datei } of gefundeneSchluessel()) {
      const kurz = datei.replace(/^\.\.\//, 'src/')
      if (!kennt(deutsch, schluessel)) fehlend.push(`de.json: ${schluessel}   (${kurz})`)
      if (!kennt(englisch, schluessel)) fehlend.push(`en.json: ${schluessel}   (${kurz})`)
    }

    expect(
      [...new Set(fehlend)].sort(),
      'Diese Schlüssel schlägt der Code nach, aber es gibt sie nicht. ' +
        'Auf dem Bildschirm steht dann der Schlüssel selbst — ein Rückfall ' +
        'auf die andere Sprache ist abgeschaltet (i18n/index.ts).',
    ).toEqual([])
  })
})
