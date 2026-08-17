import { useIsFetching } from '@tanstack/react-query'

/**
 * Schmaler Fortschrittsbalken unter der Kopfzeile.
 *
 * Er läuft, solange irgendeine Abfrage unterwegs ist. Gerade der erste Aufruf
 * eines Filters dauert ein bis zwei Sekunden, weil die Angaben zu jedem Titel
 * einzeln von TMDB geholt werden - ohne sichtbares Zeichen wirkt die App dann
 * eingefroren.
 */
export function LoadingBar() {
  const fetching = useIsFetching()

  return (
    <div
      className="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 overflow-hidden"
      aria-hidden="true"
    >
      <div
        className={
          'h-full w-full bg-linear-to-r from-transparent via-accent-500 to-transparent ' +
          'transition-opacity duration-200 ' +
          (fetching > 0 ? 'animate-nv-sweep opacity-100' : 'opacity-0')
        }
      />
    </div>
  )
}
