/**
 * Eine Reihe Reiter - oben wie unten, überall gleich.
 *
 * ⚠️ **Warum das ein eigenes Bauteil ist.** Vorher zeichnete jede Seite ihre
 * Reihe selbst, und es gab drei Bauweisen: eine mit senkrechtem Strich links,
 * eine ohne, eine mit Symbolen. Die Größen unterschieden sich um Kleinigkeiten
 * (`px-4 py-2` gegen `px-3.5 py-1.5`), die einzeln niemand bemerkt — aber beim
 * Wechseln zwischen den Seiten wirkte jede anders, ohne dass man den Grund
 * hätte benennen können.
 *
 * Einmal geradezurücken hätte nichts genützt: Die nächste Seite wäre wieder
 * abgewichen. Deshalb entscheidet ab jetzt dieses Bauteil, wie eine Reihe
 * aussieht, und die Seiten geben nur noch her, *was* darin steht.
 */

import type { ReactNode } from 'react'

import { Symbol, type SymbolName } from './Symbol'

export type Reiter<T extends string> = {
  value: T
  label: string
  /** Symbol aus dem gemeinsamen Satz. */
  symbol?: SymbolName
  /** Eigenes Symbol - für Dienstlogos, die kein Strichsymbol sind. */
  eigenesSymbol?: ReactNode
  /** Kleiner Zusatz rechts, etwa ein Hinweis auf den Baustand. */
  abzeichen?: string
}

export function Reiterreihe<T extends string>({
  eintraege,
  aktiv,
  onWechsel,
  /**
   * Untergeordnete Reihe? Bekommt den senkrechten Strich links, der sie sichtbar
   * an die Reihe darüber bindet. Ohne ihn stehen zwei Reihen gleichrangig
   * untereinander, und man sieht nicht, dass die untere zur oberen gehört.
   */
  unter = false,
  label,
  className = '',
}: {
  eintraege: Reiter<T>[]
  aktiv: T
  onWechsel: (wert: T) => void
  unter?: boolean
  label?: string
  className?: string
}) {
  return (
    <div
      className={
        'flex flex-wrap items-center gap-2 ' +
        (unter ? 'border-l-2 border-accent-500/40 pl-4 ' : '') +
        className
      }
      role="tablist"
      aria-label={label}
    >
      {eintraege.map((eintrag) => {
        const gewaehlt = aktiv === eintrag.value
        return (
          <button
            key={eintrag.value}
            type="button"
            role="tab"
            aria-selected={gewaehlt}
            onClick={() => onWechsel(eintrag.value)}
            className={
              'inline-flex items-center gap-2 rounded-full border text-sm font-medium transition-colors ' +
              (unter ? 'px-3.5 py-1.5 ' : 'px-4 py-2 ') +
              (gewaehlt
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {eintrag.eigenesSymbol ?? (eintrag.symbol && <Symbol name={eintrag.symbol} />)}
            {eintrag.label}
            {eintrag.abzeichen && (
              <span className="rounded-full border border-warn-500/50 bg-warn-500/10 px-1.5 py-0.5 text-[0.65rem] font-semibold tracking-wide text-warn-500 uppercase">
                {eintrag.abzeichen}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
