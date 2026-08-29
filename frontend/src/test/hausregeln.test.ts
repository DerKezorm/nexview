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
