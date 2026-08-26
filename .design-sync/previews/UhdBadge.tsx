import { UhdBadge } from 'nexview-ui'

/**
 * Die zweite Achse: derselbe Titel in der 4K-Instanz. Immer dieselbe deckende
 * dunkle Platte, die Farbe steckt in Schrift und Rand - auf einem hellen
 * Poster bliebe von einem eingefaerbten Hintergrund nur ein blasser Fleck.
 */
export const Kompakt = () => (
  <div className="bg-ink-950 p-6 flex flex-wrap items-center gap-2">
    <UhdBadge status="not_requested" kompakt />
    <UhdBadge status="pending_approval" kompakt />
    <UhdBadge status="requested" kompakt />
    <UhdBadge status="downloaded" kompakt />
    <UhdBadge status="failed" kompakt />
  </div>
)

/** Ausgeschrieben nur dort, wo Platz ist - auf der Detailseite. */
export const Ausgeschrieben = () => (
  <div className="bg-ink-950 p-6 flex flex-wrap items-center gap-2">
    <UhdBadge status="downloaded" />
    <UhdBadge status="searching" />
    <UhdBadge status="blocked" />
  </div>
)
