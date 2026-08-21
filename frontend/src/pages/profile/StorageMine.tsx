import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../../api/client'
import type { StorageEntry, StorageMine as StorageMineData } from '../../api/types'
import { Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatDateTime, formatSize } from '../../lib/format'

/**
 * Was belege ich - das Groesste zuerst.
 *
 * Die Sortierung ist der eigentliche Zweck der Seite. Eine Liste nach Titel
 * oder Datum beantwortet die Frage nicht, die jemand hier stellt: "Wo steckt
 * mein Platz?" Eine einzige Serie kann so viel wiegen wie zweihundert Filme.
 */
export function StorageMine() {
  const { t, i18n } = useTranslation()

  const abfrage = useQuery({
    queryKey: ['storage-mine'],
    queryFn: () => api.get<StorageMineData>('/api/storage/me'),
  })

  // TanStack Query behaelt alte Daten, wenn ein Nachladen scheitert - ohne
  // failureReason bliebe ein Fehler unsichtbar.
  const fehler = abfrage.error ?? abfrage.failureReason

  if (abfrage.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    )
  }

  if (fehler) {
    return (
      <ErrorBanner
        message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
      />
    )
  }

  const daten = abfrage.data
  if (!daten) return null

  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-col gap-1 p-5">
        <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
          {t('storage.usedLabel')}
        </p>
        <p className="text-3xl font-semibold tabular-nums">
          {formatSize(daten.used_bytes, i18n.language)}
        </p>
        <p className="text-sm text-mist-500">
          {t('storage.itemCount', { count: daten.items })}
        </p>
        <p className="mt-2 text-sm text-mist-500">{t('storage.measureOnlyHint')}</p>
      </Card>

      {daten.entries.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center">
          <p className="text-mist-400">{t('storage.empty')}</p>
          <p className="mt-1 text-sm text-mist-600">{t('storage.emptyHint')}</p>
        </div>
      ) : (
        <Card className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-center gap-3 border-b border-ink-700 pb-3">
            <h3 className="font-medium">{t('storage.listTitle')}</h3>
            <span className="text-sm text-mist-600">{t('storage.listHint')}</span>
          </div>
          <ul className="flex flex-col">
            {daten.entries.map((eintrag) => (
              <PostenZeile key={eintrag.id} eintrag={eintrag} />
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function PostenZeile({ eintrag }: { eintrag: StorageEntry }) {
  const { t, i18n } = useTranslation()

  // Ohne TMDB-Nummer gibt es keine Detailseite - das ist bei Serien der
  // Normalfall, weil Sonarr nur TVDB-Nummern kennt.
  const ziel = eintrag.tmdb_id
    ? `/titel/${eintrag.media_type}/${eintrag.tmdb_id}`
    : null

  const untertitel =
    eintrag.season !== null
      ? t('storage.season', { number: eintrag.season })
      : t(eintrag.media_type === 'movie' ? 'common.movie' : 'common.series')

  return (
    <li className="flex items-center gap-3 border-b border-ink-800 py-2.5 last:border-b-0">
      <div className="min-w-0 flex-1">
        {ziel ? (
          <Link to={ziel} className="line-clamp-1 font-medium hover:text-accent-500">
            {eintrag.title}
          </Link>
        ) : (
          <p className="line-clamp-1 font-medium">{eintrag.title}</p>
        )}
        <p className="text-xs text-mist-600">
          {untertitel}
          {eintrag.tier === 'uhd' && <span className="ml-1.5 text-accent-500">4K</span>}
          <span className="ml-1.5">
            · {t('storage.measuredAt', {
              date: formatDateTime(eintrag.measured_at, i18n.language),
            })}
          </span>
        </p>
      </div>
      <span className="shrink-0 tabular-nums">
        {formatSize(eintrag.size_bytes, i18n.language)}
      </span>
    </li>
  )
}
