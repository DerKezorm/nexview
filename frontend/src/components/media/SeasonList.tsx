import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { SeasonDetail, SeasonInfo } from '../../api/types'
import { formatDate } from '../../lib/format'
import { Spinner } from '../ui'

/** Häkchen für "liegt vor", Strich für "fehlt noch". */
function Zustand({ vorhanden }: { vorhanden: boolean }) {
  const { t } = useTranslation()
  return (
    <span
      title={t(vorhanden ? 'detail.episodeHere' : 'detail.episodeMissing')}
      className={
        'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ' +
        (vorhanden ? 'bg-ok-500/15 text-ok-500' : 'bg-ink-800 text-mist-600')
      }
    >
      {vorhanden ? '✓' : '–'}
    </span>
  )
}

/** Die Folgen einer Staffel - erst geladen, wenn sie aufgeklappt wird. */
function Folgen({ tmdbId, season }: { tmdbId: number; season: number }) {
  const { t, i18n } = useTranslation()

  const query = useQuery({
    queryKey: ['season', tmdbId, season],
    queryFn: () => api.get<SeasonDetail>(`/api/detail/tv/${tmdbId}/season/${season}`),
    staleTime: 60 * 60 * 1000,
  })

  if (query.isPending) {
    return (
      <p className="flex items-center gap-2 px-4 py-3 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (query.error || query.data.episodes.length === 0) {
    return <p className="px-4 py-3 text-sm text-mist-600">{t('detail.noEpisodes')}</p>
  }

  return (
    <ul className="divide-y divide-ink-700/60">
      {query.data.episodes.map((folge) => (
        <li key={folge.episode_number} className="flex gap-3 px-4 py-3">
          <Zustand vorhanden={folge.available} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-mist-100">
              <span className="text-mist-600">{folge.episode_number}.</span> {folge.name}
            </p>
            {folge.overview && (
              <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-mist-500">
                {folge.overview}
              </p>
            )}
            <p className="mt-1 text-[11px] text-mist-600">
              {folge.air_date ? formatDate(folge.air_date, i18n.language) : '—'}
              {folge.runtime_minutes ? ` · ${folge.runtime_minutes} Min.` : ''}
            </p>
          </div>
        </li>
      ))}
    </ul>
  )
}

/**
 * Staffeln zum Aufklappen, mit dem Hinweis, wie viel davon schon da ist.
 *
 * Die Folgen werden erst beim Aufklappen geholt: eine Serie mit zehn Staffeln
 * würde sonst zehn Abfragen auslösen, von denen man meist keine braucht.
 */
export function SeasonList({ tmdbId, seasons }: { tmdbId: number; seasons: SeasonInfo[] }) {
  const { t, i18n } = useTranslation()
  const [offen, setOffen] = useState<number | null>(null)

  if (seasons.length === 0) return null

  /**
   * Liegt von dieser Serie überhaupt etwas vor?
   *
   * Wenn nicht, bleibt das Abzeichen weg: neben jeder Staffel "0 von 10"
   * zu schreiben ist zwar richtig, sagt aber nichts und lenkt vom Rest ab.
   */
  const etwasVorhanden = seasons.some((staffel) => staffel.episodes_available > 0)

  return (
    <section>
      <h2 className="mb-3 text-lg font-semibold">{t('detail.seasons')}</h2>
      <div className="flex flex-col gap-2">
        {seasons.map((staffel) => {
          const auf = offen === staffel.season_number
          const komplett =
            staffel.episode_count > 0 && staffel.episodes_available >= staffel.episode_count
          const teilweise = staffel.episodes_available > 0 && !komplett

          return (
            <div
              key={staffel.season_number}
              className="overflow-hidden rounded-xl border border-ink-700 bg-ink-850/60"
            >
              <button
                type="button"
                onClick={() => setOffen(auf ? null : staffel.season_number)}
                aria-expanded={auf}
                className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-ink-850"
              >
                <span
                  className={'text-xs transition-transform ' + (auf ? 'rotate-90' : '')}
                  aria-hidden="true"
                >
                  ▶
                </span>

                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{staffel.name}</span>
                  <span className="block text-xs text-mist-600">
                    {t('detail.episodeCount', { count: staffel.episode_count })}
                    {staffel.air_date && ` · ${formatDate(staffel.air_date, i18n.language)}`}
                  </span>
                </span>

                {etwasVorhanden && (
                  <span
                    className={
                      'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ' +
                      (komplett
                        ? 'bg-ok-500/15 text-ok-500'
                        : teilweise
                          ? 'bg-warn-500/15 text-warn-500'
                          : 'bg-ink-800 text-mist-600')
                    }
                  >
                    {komplett
                      ? t('detail.seasonComplete')
                      : t('detail.seasonPartial', {
                          have: staffel.episodes_available,
                          total: staffel.episode_count,
                        })}
                  </span>
                )}
              </button>

              {auf && (
                <div className="border-t border-ink-700/60">
                  <Folgen tmdbId={tmdbId} season={staffel.season_number} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
