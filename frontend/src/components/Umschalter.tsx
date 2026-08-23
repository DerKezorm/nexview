/**
 * Eine Reihe von Knöpfen, von denen genau einer aktiv ist.
 *
 * Liegt bewusst **nicht** bei einer einzelnen Seite: Stöbern, Kalender und
 * Personen brauchen ihn alle, und dreimal dieselbe Knopfleiste sieht nach dem
 * ersten Feinschliff dreimal leicht anders aus. Genau das war der Zustand
 * vorher — die Kalender-Pillen standen einzeln nebeneinander, die auf der
 * Stöber-Seite in einer gemeinsamen Fassung.
 *
 * Warum eine geschlossene Leiste und keine Einzelpillen: Sie zeigt auf einen
 * Blick, dass die Auswahl **eine** Frage beantwortet und dass genau eine
 * Antwort gilt. Einzelpillen sehen aus wie mehrere unabhängige Schalter.
 */
export function Umschalter<T extends string>({
  wert,
  wahl,
  onChange,
  label,
  beschriftung,
  deaktiviert = false,
  titel,
}: {
  wert: T
  wahl: readonly T[]
  onChange: (neu: T) => void
  label: (eintrag: T) => string
  /** Überschrift daneben. Ohne sie steht die Reihe für sich. */
  beschriftung?: string
  /**
   * Ganze Reihe stillgelegt.
   *
   * Sichtbar bleiben statt verschwinden: Ein Regler, der ohne Erklärung
   * wegfällt, wirkt wie ein Fehler. `titel` sagt dann, warum.
   */
  deaktiviert?: boolean
  titel?: string
}) {
  const reihe = (
    <div
      title={titel}
      className={
        // ⚠️ `w-fit` ist Pflicht, nicht Kosmetik: Steht die Leiste als
        // direktes Kind einer Spalte (`flex flex-col`), zieht `align-items:
        // stretch` sie über die **ganze Breite** - `inline-flex` allein hilft
        // dagegen nicht. Genau so sah die Personen-Seite aus.
        'inline-flex w-fit flex-wrap rounded-full border border-ink-700 bg-ink-850 p-1 ' +
        (deaktiviert ? 'opacity-40' : '')
      }
    >
      {wahl.map((eintrag) => (
        <button
          key={eintrag}
          type="button"
          disabled={deaktiviert}
          aria-pressed={wert === eintrag}
          onClick={() => onChange(eintrag)}
          className={
            'rounded-full px-4 py-1.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed ' +
            (wert === eintrag
              ? 'bg-accent-500 text-white'
              : 'text-mist-500 hover:text-mist-300')
          }
        >
          {label(eintrag)}
        </button>
      ))}
    </div>
  )

  if (!beschriftung) return reihe

  return (
    <div className="flex w-fit flex-wrap items-center gap-x-3 gap-y-2">
      <span className="text-sm text-mist-500">{beschriftung}</span>
      {reihe}
    </div>
  )
}
