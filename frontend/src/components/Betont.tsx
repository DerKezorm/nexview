/**
 * Fett und Code in einem Text sichtbar machen — `**so**` und `` `so` ``.
 *
 * ⚠️ **Das gibt es, weil es lange gefehlt hat.** Die redaktionellen Texte im
 * „Was ist neu"-Fenster sind seit jeher mit Markdown geschrieben; gerendert
 * hat sie nie jemand. Über fünf Fassungen standen dadurch 17 fette Stellen
 * und 9 Code-Stellen mit ihren Sternchen und Backticks **wörtlich** auf dem
 * Bildschirm — es sah aus wie Absicht, war aber keine.
 *
 * Bewusst **kein** Markdown-Paket und kein `dangerouslySetInnerHTML`: Gebraucht
 * werden genau zwei Auszeichnungen, und die entstehen hier als React-Elemente.
 * Damit kann aus einem Text nie Markup werden — was in einer Anwendung, die
 * fremde Titel und Kommentare anzeigt, die einzige vertretbare Bauart ist.
 *
 * Alles Unpaarige bleibt stehen, wie es ist: Ein einzelnes Sternchen in
 * „3 * 4" soll ein Sternchen bleiben.
 */

import type { ReactNode } from 'react'

/** Trennt an `**…**` und `` `…` `` — die Klammern bleiben in den Treffern. */
const MUSTER = /(\*\*[^*]+\*\*|`[^`]+`)/g

function betont(text: string): ReactNode[] {
  return text.split(MUSTER).map((teil, index) => {
    if (teil.startsWith('**') && teil.endsWith('**') && teil.length > 4) {
      return (
        <strong key={index} className="font-semibold text-mist-100">
          {teil.slice(2, -2)}
        </strong>
      )
    }
    if (teil.startsWith('`') && teil.endsWith('`') && teil.length > 2) {
      return (
        <code
          key={index}
          className="rounded bg-ink-800 px-1 py-0.5 font-mono text-[0.9em] text-accent-400"
        >
          {teil.slice(1, -1)}
        </code>
      )
    }
    return teil
  })
}

/** Dasselbe als Bauteil, wo ein Ausdruck unpraktisch wäre. */
export function Betont({ text }: { text: string }) {
  return <>{betont(text)}</>
}
