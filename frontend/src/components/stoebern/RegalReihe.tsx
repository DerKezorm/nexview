import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { api } from '../../api/client'
import type { Bestandsfilter, MediaItem, MediaType, RegalSeite } from '../../api/types'
import { regalPath } from '../../lib/routes'
import { Titelliste, type Ansicht } from './Titelliste'

/** So viele Kacheln zeigt eine Reihe auf der Übersicht. */
const REIHE_ANZAHL = 12

type RegalReiheProps = {
  mediaType: MediaType
  kennung: string
  bestand: Bestandsfilter
  ansicht: Ansicht
  onQuickAdd: (item: MediaItem) => void
  /**
   * Fertiger Name statt Übersetzung — nur bei „Weil dir *X* gefällt", wo im
   * Namen ein Filmtitel steckt.
   */
  titel?: string | null
}

function Platzhalter() {
  return (
    <div className="overflow-hidden rounded-xl border border-ink-700 bg-ink-850">
      <div className="aspect-2/3 animate-pulse bg-ink-800" />
      <div className="space-y-2 p-3">
        <div className="h-3.5 w-4/5 animate-pulse rounded bg-ink-800" />
        <div className="h-3 w-2/5 animate-pulse rounded bg-ink-800" />
      </div>
    </div>
  )
}

/**
 * Eine Regalzeile auf der Stöber-Übersicht.
 *
 * Jede Reihe hat ihre **eigene** Abfrage. Das ist Absicht: Die Übersicht
 * erscheint sofort und füllt sich nacheinander, statt als Ganzes auf den
 * langsamsten Abruf zu warten. Ein Regal kostet einen Discover-Abruf plus bis
 * zu zwanzig schlanke Detailabrufe - gebündelt wäre das ein spürbarer
 * Wartebalken vor einer leeren Seite.
 */
export function RegalReihe({
  mediaType,
  kennung,
  bestand,
  ansicht,
  onQuickAdd,
  titel,
}: RegalReiheProps) {
  const { t } = useTranslation()

  const query = useQuery({
    queryKey: ['regal', mediaType, kennung, bestand, REIHE_ANZAHL],
    queryFn: () =>
      api.get<RegalSeite>(
        `/api/stoebern/regal/${mediaType}/${kennung}?anzahl=${REIHE_ANZAHL}&bestand=${bestand}`,
      ),
  })

  const items = query.data?.items ?? []

  // Eine Reihe, die nichts hergibt, verschwindet - statt eine leere Fläche mit
  // Überschrift stehen zu lassen. Beim Laden bleibt sie sichtbar, sonst
  // springt das Layout.
  if (!query.isPending && items.length === 0) return null

  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h2 className="text-xl font-bold tracking-tight">
            {titel ? t('stoebern.regal.weil_du.titel', { titel }) : t(`stoebern.regal.${kennung}.titel`)}
          </h2>
          <p className="mt-0.5 text-sm text-mist-500">
            {titel
              ? t('stoebern.regal.weil_du.hinweis')
              : t(`stoebern.regal.${kennung}.hinweis`)}
          </p>
        </div>
        <Link
          to={regalPath(mediaType, kennung)}
          className="shrink-0 text-sm font-semibold text-accent-500 transition-colors hover:text-accent-400"
        >
          {t('stoebern.alleAnsehen')}
        </Link>
      </header>

      {query.isPending ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => (
            <Platzhalter key={index} />
          ))}
        </div>
      ) : (
        <Titelliste items={items} ansicht={ansicht} onQuickAdd={onQuickAdd} />
      )}
    </section>
  )
}
