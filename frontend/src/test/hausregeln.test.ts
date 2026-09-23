/**
 * Hausregeln, die sich nicht von selbst durchsetzen.
 *
 * ⚠️ **Warum ein Test und keine Lint-Regel.** ESLint kennt `no-alert`, aber die
 * Fließbandprüfung ruft `pytest` und `npm test` auf — **nicht** `npm run lint`.
 * Eine Regel, die nur im Editor anschlägt, hält niemanden auf, der sie nicht
 * sieht. Hier steht sie da, wo sie wirklich greift.
 *
 * ⚠️ **Weil genau das schon zweimal passiert ist.** Die Browser-Rückfragen
 * waren einmal abgeräumt („das waren die letzten zwei Stellen"), und eine
 * später gebaute Seite brachte sie wieder mit. Ein Kommentar über der richtigen
 * Lösung schützt die Datei, in der er steht — nicht die nächste.
 *
 * ⚠️ **Gelesen wird über `import.meta.glob`, nicht über `node:fs`.** Der
 * naheliegende Weg über das Dateisystem bräuchte `@types/node`; das Paket fehlt
 * hier bewusst, und `npm run build` prüft die Typen mit. Ein Wächter, der die
 * Auslieferung zerlegt, kostet mehr, als er einbringt.
 */

import { describe, expect, it } from 'vitest'

/**
 * Jede Quelldatei als Text.
 *
 * Vite löst das beim Übersetzen auf - zur Laufzeit steht der Inhalt einfach da.
 */
const DATEIEN = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  eager: true,
  import: 'default',
}) as Record<string, string>

/**
 * Die Browser-Dialoge.
 *
 * Gesucht wird der **Aufruf** (mit Klammer), nicht die Erwähnung: In den
 * Erklärungen über `ConfirmDialog` steht `window.confirm` absichtlich - als
 * das, was man gerade nicht tun soll.
 */
const VERBOTEN = [
  { name: 'window.confirm()', muster: /window\.confirm\s*\(/ },
  { name: 'window.alert()', muster: /window\.alert\s*\(/ },
  { name: 'window.prompt()', muster: /window\.prompt\s*\(/ },
  { name: 'confirm()', muster: /(^|[^.\w])confirm\s*\(/m },
  { name: 'alert()', muster: /(^|[^.\w])alert\s*\(/m },
  { name: 'prompt()', muster: /(^|[^.\w])prompt\s*\(/m },
]

/** Diese Datei nennt die verbotenen Namen zwangsläufig selbst. */
const AUSGENOMMEN = /hausregeln\.test\.ts$/

describe('keine Browser-Popups', () => {
  /**
   * ⚠️ **Zuerst prüfen, dass überhaupt etwas geprüft wird.**
   *
   * Greift das Muster ins Leere - falscher Pfad, umbenannter Ordner -, dann
   * bestünde jede Regel darunter mit einer leeren Liste. Ein Wächter, der
   * nichts sieht, meldet lebenslang „alles in Ordnung".
   */
  it('sieht die Quelldateien überhaupt', () => {
    expect(Object.keys(DATEIEN).length).toBeGreaterThan(100)
    // Und es ist wirklich Quelltext, nicht ein Bündel leerer Zeichenketten.
    expect(DATEIEN['../pages/settings/AdminQualitaetsprofile.tsx']).toContain('ConfirmDialog')
  })

  it.each(VERBOTEN)('$name kommt in keiner Quelldatei vor', ({ muster }) => {
    const treffer = Object.entries(DATEIEN)
      .filter(([pfad]) => !AUSGENOMMEN.test(pfad))
      .filter(([, inhalt]) => muster.test(inhalt))
      .map(([pfad]) => pfad)

    expect(
      treffer,
      'Rückfragen gehören in <ConfirmDialog>, Meldungen in die Seite selbst. ' +
        'Der Browser-Dialog schreibt „Auf localhost:5180 wird Folgendes angezeigt" ' +
        'darüber, ignoriert jede Gestaltung und kann nichts erklären.',
    ).toEqual([])
  })

  it('findet den Verstoß, wenn es einen gäbe', () => {
    // ⚠️ Ein Wächter, der nie etwas findet, ist von einem kaputten Wächter
    // nicht zu unterscheiden. Hier steht der Beweis, dass er greift.
    expect(VERBOTEN[0].muster.test("if (window.confirm(t('x'))) weg()")).toBe(true)
    expect(VERBOTEN[3].muster.test('  if (confirm(frage)) weg()')).toBe(true)
    // Und dass er die Erwähnung in einer Erklärung in Ruhe lässt.
    expect(VERBOTEN[0].muster.test(' * nicht als `window.confirm`.')).toBe(false)
    expect(VERBOTEN[3].muster.test('        onConfirm={() => weg()}')).toBe(false)
  })
})

/**
 * Jedes `<input …>` bis zu seinem Ende.
 *
 * Mit geschweiften Klammern mitgezählt: `onChange={(e) => …}` enthält ein `>`,
 * an dem ein einfacher Ausdruck das Element zu früh abschneiden würde.
 */
function eingaben(text: string): string[] {
  const gefunden: string[] = []
  for (const treffer of text.matchAll(/<input\b/g)) {
    const anfang = treffer.index ?? 0
    let tiefe = 0
    for (let i = anfang + treffer[0].length; i < text.length; i++) {
      const zeichen = text[i]
      if (zeichen === '{') tiefe++
      else if (zeichen === '}') tiefe--
      else if (zeichen === '>' && tiefe === 0) {
        gefunden.push(text.slice(anfang, i + 1))
        break
      }
    }
  }
  return gefunden
}

const haekchen = (text: string) => eingaben(text).filter((tag) => /type="checkbox"/.test(tag))
const ohneFarbe = (text: string) => haekchen(text).filter((tag) => !/accent-/.test(tag))

const OBERFLAECHE = Object.entries(DATEIEN).filter(
  ([pfad]) => pfad.endsWith('.tsx') && !pfad.includes('.test.'),
)

/**
 * ⚠️ **Ohne `accent-…` zeichnet der Browser sein eigenes Häkchen**, im dunklen
 * Nexview hell und blau statt rot. Am 12.09.2026 standen fünf so da, alle nur
 * mit `mt-0.5`: drei in den OIDC-Einstellungen, eines bei den API-Schlüsseln und
 * eines im Dialog „Konto löschen“. Aufgefallen ist es erst beim Durchklicken.
 */
describe('Häkchen tragen die Nexview-Farbe', () => {
  it('sieht die Häkchen überhaupt', () => {
    const anzahl = OBERFLAECHE.reduce((summe, [, inhalt]) => summe + haekchen(inhalt).length, 0)
    expect(anzahl).toBeGreaterThan(40)
  })

  it('jedes Häkchen hat eine accent-Klasse', () => {
    const treffer = OBERFLAECHE.flatMap(([pfad, inhalt]) => ohneFarbe(inhalt).map(() => pfad))

    expect(
      treffer,
      'Ohne accent-Klasse steht ein helles Browser-Häkchen in der dunklen Seite. ' +
        'Vorlage: className="h-4 w-4 shrink-0 accent-accent-500"',
    ).toEqual([])
  })

  it('findet ein Häkchen ohne Farbe, auch hinter einem Pfeil im Handler', () => {
    expect(ohneFarbe('<input type="checkbox" onChange={(e) => weg(e)} className="mt-0.5" />')).toHaveLength(1)
    expect(
      ohneFarbe('<input type="checkbox" onChange={(e) => weg(e)} className="h-4 w-4 accent-accent-500" />'),
    ).toHaveLength(0)
    expect(ohneFarbe('<input type="text" className="mt-0.5" />')).toHaveLength(0)
  })
})

/**
 * ⚠️ **Jeder Aufruf an das Backend beginnt mit `/api/`.**
 *
 * `api.get('/settings')` sieht richtig aus und ist es nicht: Der Client hängt
 * nichts davor (nur den Unterpfad aus `NEXVIEW_URL_BASE`). Die Anfrage geht
 * dann an die Oberfläche selbst, der Entwicklungsserver antwortet mit
 * `index.html`, und `response.json()` scheitert an einem `<`.
 *
 * Das Tückische daran: In einem Test mit ersetzter API-Schicht fällt es nie
 * auf - der Mock antwortet auf jeden Pfad. Die nexcrate-Seite hat so ein
 * halbes Jahrhundert Zeilen lang niemandem etwas getan, weil ihre einzige
 * Prüfung eine Attrappe war. Gefunden hat es erst ein Playwright-Lauf.
 */
it('ruft das Backend nur unter /api/', () => {
  const falsch: string[] = []
  const muster = /\bapi\.(?:get|post|put|patch|delete|upload)(?:<[^>]*>)?\(\s*(['"`])([^'"`]*)\1/g
  for (const [pfad, inhalt] of Object.entries(DATEIEN)) {
    if (pfad.includes('/api/client')) continue
    for (const treffer of inhalt.matchAll(muster)) {
      const ziel = treffer[2]
      // Nur wurzel-absolute Pfade sind Backend-Adressen; alles andere ist eine
      // Variable oder ein zusammengesetzter Pfad und wird hier nicht geraten.
      if (ziel.startsWith('/') && !ziel.startsWith('/api/')) {
        falsch.push(`${pfad}: ${ziel}`)
      }
    }
  }
  expect(
    falsch,
    'Diese Aufrufe gehen an die Oberfläche statt ans Backend - es fehlt /api davor:',
  ).toEqual([])
})
