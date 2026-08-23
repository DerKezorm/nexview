import { useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { Bestandsfilter, MediaItem, MediaType, RegalSeite } from '../api/types'
import { DetailModal } from '../components/media/DetailModal'
import type { RegalInfo } from '../api/types'
import { Umschalter } from '../components/Umschalter'
import {
  AnsichtUmschalter,
  Titelliste,
  type Ansicht,
} from '../components/stoebern/Titelliste'
import { Button, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { stoeberPath } from '../lib/routes'

/** So viele Titel lädt die volle Regalseite je Schritt. */
const SEITE_ANZAHL = 24

const BESTAND_WAHL: Bestandsfilter[] = ['egal', 'nur_vorhanden', 'nur_neu']

/**
 * Ein Regal in voller Länge.
 *
 * Bewusst **kein** endloses Nachladen bis Seite 500: Stöbern heißt Auswahl
 * verkleinern, nicht vergrößern. Es gibt einen Knopf, und der verschwindet,
 * sobald nichts mehr kommt.
 */
export function StoeberRegalPage() {
  const { t } = useTranslation()
  const { mediaType, kennung } = useParams<{ mediaType: MediaType; kennung: string }>()
  const { data: config } = useConfig()
  const [bestand, setBestand] = useState<Bestandsfilter>('egal')
  const [ansicht, setAnsicht] = useState<Ansicht>('kacheln')
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const art = (mediaType ?? 'movie') as MediaType

  const query = useInfiniteQuery({
    queryKey: ['regal-voll', art, kennung, bestand],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<RegalSeite>(
        `/api/stoebern/regal/${art}/${kennung}?page=${pageParam}&anzahl=${SEITE_ANZAHL}&bestand=${bestand}`,
      ),
    getNextPageParam: (letzte) => {
      // Der Server sagt, ob es noch etwas gibt. Ohne diese Auskunft böte die
      // Seite bei gesetztem Bestandsfilter einen Knopf an, hinter dem nichts
      // mehr kommt - er hat ja schon mehrere TMDB-Seiten durchgesehen.
      if (letzte.erschoepft) return undefined
      const naechste = letzte.page + letzte.seiten_durchsucht
      return naechste <= letzte.total_pages ? naechste : undefined
    },
  })

  // Der fertige Name eines persönlichen Regals steht nur in der Regalliste —
  // sonst hieße die Überschrift hier „stoebern.regal.weil_du_603.titel".
  const { data: regale = [] } = useQuery({
    queryKey: ['regale', art],
    queryFn: () => api.get<RegalInfo[]>(`/api/stoebern/regale/${art}`),
    staleTime: 5 * 60 * 1000,
  })
  const eigenerName = regale.find((r) => r.kennung === kennung)?.titel

  const items = query.data?.pages.flatMap((seite) => seite.items) ?? []
  const arrWarning = query.data?.pages[0]?.arr_warning ?? null
  const failure = query.error ?? query.failureReason

  const arrConfigured =
    art === 'movie' ? (config?.radarr_configured ?? false) : (config?.sonarr_configured ?? false)

  return (
    <div className="flex flex-col gap-6">
      <header>
        <Link
          to={stoeberPath(art)}
          className="text-sm font-semibold text-mist-500 transition-colors hover:text-mist-300"
        >
          ← {t('stoebern.titel')}
        </Link>
        <h1 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
          {eigenerName
            ? t('stoebern.regal.weil_du.titel', { titel: eigenerName })
            : t(`stoebern.regal.${kennung}.titel`)}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">
          {eigenerName
            ? t('stoebern.regal.weil_du.hinweis')
            : t(`stoebern.regal.${kennung}.hinweis`)}
        </p>
      </header>

      <div className="flex flex-wrap items-center justify-between gap-4">
        <Umschalter
          wert={bestand}
          wahl={BESTAND_WAHL}
          onChange={setBestand}
          beschriftung={t('stoebern.bestand.frage')}
          label={(eintrag) => t(`stoebern.bestand.${eintrag}`)}
        />
        {/* Die Darstellung steht neben dem Filter, nicht darunter: Sie
            schränkt nichts ein, sie bestimmt nur das Aussehen. */}
        <AnsichtUmschalter wert={ansicht} onChange={setAnsicht} />
      </div>

      {arrWarning && (
        <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {arrWarning}
        </div>
      )}

      {failure && (
        <ErrorBanner
          message={failure instanceof ApiError ? failure.message : t('errors.generic')}
        />
      )}

      {!query.isPending && !failure && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-700 px-4 py-12 text-center">
          {/* Ehrlich statt tiefer graben: Bei "nur was schon da ist" und einer
              dünnen Bibliothek ist "hier passt nichts" die richtige Auskunft
              und keine Frage der Geduld. */}
          <p className="text-sm text-mist-500">
            {bestand === 'egal' ? t('stoebern.leer') : t(`stoebern.leer_${bestand}`)}
          </p>
          {bestand !== 'egal' && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => setBestand('egal')}
              className="mt-4 !px-4 !py-2"
            >
              {t('stoebern.bestandZuruecksetzen')}
            </Button>
          )}
        </div>
      )}

      {items.length > 0 && (
        <Titelliste items={items} ansicht={ansicht} onQuickAdd={setSelected} />
      )}

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="ghost"
            onClick={() => void query.fetchNextPage()}
            loading={query.isFetchingNextPage}
          >
            {t('discover.loadMore')}
          </Button>
        </div>
      )}

      {query.isFetching && !query.isFetchingNextPage && (
        <p className="flex items-center justify-center gap-2 text-xs text-mist-600">
          <Spinner className="h-3 w-3" />
          {t('common.loading')}
        </p>
      )}

      <DetailModal
        item={selected}
        onClose={() => setSelected(null)}
        arrConfigured={arrConfigured}
      />
    </div>
  )
}
