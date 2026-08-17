import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { Role, Stats, UserStats } from '../api/types'
import { Avatar } from '../components/Avatar'
import { StarRating } from '../components/StarRating'
import { Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDate } from '../lib/format'

const POOR_RATING = 2

const ROLE_LABELS: Record<Role, string> = {
  admin: 'adminUsers.roleAdmin',
  approver: 'adminUsers.roleApprover',
  user: 'adminUsers.roleUser',
}

/** Eine große Zahl mit Beschriftung. */
function KeyFigure({
  label,
  value,
  hint,
  tone = 'normal',
}: {
  label: string
  value: string
  hint?: string
  tone?: 'normal' | 'warn'
}) {
  return (
    <div
      className={
        'rounded-2xl border px-4 py-3 ' +
        (tone === 'warn'
          ? 'border-bad-500/50 bg-bad-500/10'
          : 'border-ink-700 bg-ink-850/60')
      }
    >
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">{label}</p>
      <p
        className={
          'mt-1 text-3xl font-bold tabular-nums ' +
          (tone === 'warn' ? 'text-bad-500' : 'text-mist-100')
        }
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-xs text-mist-600">{hint}</p>}
    </div>
  )
}

/** Ein waagerechter Balken mit Beschriftung links und Zahl rechts. */
function Bar({
  label,
  value,
  max,
  accent = false,
}: {
  label: string
  value: number
  max: number
  accent?: boolean
}) {
  const breite = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="flex items-center gap-3">
      <span className="w-24 shrink-0 text-xs text-mist-500">{label}</span>
      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-ink-800">
        <div
          className={
            'h-full rounded-full transition-[width] duration-500 ' +
            (accent ? 'bg-bad-500' : 'bg-accent-500')
          }
          style={{ width: `${breite}%` }}
        />
      </div>
      <span className="w-8 shrink-0 text-right text-xs tabular-nums text-mist-400">{value}</span>
    </div>
  )
}

/** Monatsverlauf als gestapelte Säulen. */
function History({ data }: { data: Stats['history'] }) {
  const { t, i18n } = useTranslation()
  const max = Math.max(1, ...data.map((punkt) => punkt.movies + punkt.series))

  return (
    <div className="flex items-end gap-2 sm:gap-4">
      {data.map((punkt) => {
        const gesamt = punkt.movies + punkt.series
        const monat = new Date(`${punkt.month}-01T00:00:00`)
        return (
          <div key={punkt.month} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
            <span className="text-xs tabular-nums text-mist-500">{gesamt || ''}</span>
            <div
              className="flex w-full flex-col justify-end overflow-hidden rounded-lg bg-ink-800/60"
              style={{ height: '120px' }}
              title={t('stats.monthTooltip', { movies: punkt.movies, series: punkt.series })}
            >
              <div
                className="w-full bg-accent-700 transition-[height] duration-500"
                style={{ height: `${(punkt.series / max) * 100}%` }}
              />
              <div
                className="w-full bg-accent-500 transition-[height] duration-500"
                style={{ height: `${(punkt.movies / max) * 100}%` }}
              />
            </div>
            <span className="truncate text-xs text-mist-600">
              {monat.toLocaleDateString(i18n.language, { month: 'short' })}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function QuotaCell({ used, limit }: { used: number; limit: number | null }) {
  const { t } = useTranslation()
  if (limit === null) return <span className="text-mist-600">{t('stats.unlimited')}</span>

  const erschoepft = used >= limit
  return (
    <span className={'tabular-nums ' + (erschoepft ? 'text-bad-500' : 'text-mist-300')}>
      {used}/{limit}
    </span>
  )
}

function UserRow({ eintrag }: { eintrag: UserStats }) {
  const { t } = useTranslation()

  return (
    <tr className="border-t border-ink-700/70">
      <td className="py-2.5 pr-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <Avatar
            url={eintrag.avatar_url}
            name={eintrag.display_name ?? eintrag.username}
            className="h-8 w-8 shrink-0"
          />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">
              {eintrag.display_name ?? eintrag.username}
            </p>
            <p className="text-xs text-mist-600">{t(ROLE_LABELS[eintrag.role])}</p>
          </div>
        </div>
      </td>
      <td className="px-2 text-center text-sm tabular-nums">{eintrag.total}</td>
      <td className="px-2 text-center text-sm tabular-nums text-mist-500">
        {eintrag.movies} / {eintrag.series}
      </td>
      <td className="px-2 text-center text-sm tabular-nums">{eintrag.downloaded}</td>
      <td className="px-2 text-center text-sm tabular-nums">
        {eintrag.success_rate === null ? (
          <span className="text-mist-600">–</span>
        ) : (
          `${eintrag.success_rate}%`
        )}
      </td>
      <td className="px-2 text-center text-sm">
        <QuotaCell used={eintrag.quota_movie_used} limit={eintrag.quota_movie_limit} />
      </td>
      <td className="px-2 text-center text-sm">
        <QuotaCell used={eintrag.quota_series_used} limit={eintrag.quota_series_limit} />
      </td>
      <td className="py-2.5 pl-2">
        {eintrag.average_rating === null ? (
          <span className="text-xs text-mist-600">{t('stats.noRating')}</span>
        ) : (
          <span className="flex items-center justify-end gap-2">
            <StarRating value={Math.round(eintrag.average_rating)} size="sm" />
            <span className="text-xs tabular-nums text-mist-500">
              {eintrag.average_rating.toFixed(1)}
              {eintrag.poor_ratings > 0 && (
                <span className="ml-1 text-bad-500">({eintrag.poor_ratings})</span>
              )}
            </span>
          </span>
        )}
      </td>
    </tr>
  )
}

export function StatsPage() {
  const { t, i18n } = useTranslation()

  const statsQuery = useQuery({
    queryKey: ['admin-stats'],
    queryFn: () => api.get<Stats>('/api/admin/stats'),
  })

  if (statsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (statsQuery.isError || !statsQuery.data) {
    return <ErrorBanner message={t('stats.failed')} />
  }

  const { totals, users, history, most_requested: beliebt } = statsQuery.data
  const maxBewertung = Math.max(1, ...Object.values(totals.rating_distribution))

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t('stats.title')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-mist-500">{t('stats.intro')}</p>
        </div>
        <Link
          to="/admin/requests"
          className="rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
        >
          {t('stats.backToRequests')}
        </Link>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KeyFigure
          label={t('stats.totalRequests')}
          value={String(totals.requests)}
          hint={
            t('stats.movieCount', { count: totals.movies }) +
            ' · ' +
            t('stats.seriesCount', { count: totals.series }) +
            ' · ' +
            t('stats.byUsers', { count: totals.active_users })
          }
        />
        <KeyFigure
          label={t('stats.downloaded')}
          value={String(totals.downloaded)}
          hint={
            t('stats.movieCount', { count: totals.downloaded_movies }) +
            ' · ' +
            t('stats.seriesCount', { count: totals.downloaded_series })
          }
        />
        <KeyFigure
          label={t('stats.waiting')}
          value={String(totals.pending)}
          hint={t('stats.rejectedCancelled', {
            rejected: totals.rejected,
            cancelled: totals.cancelled,
          })}
          tone={totals.pending > 0 ? 'warn' : 'normal'}
        />
        <KeyFigure
          label={t('stats.averageRating')}
          value={totals.average_rating === null ? '–' : totals.average_rating.toFixed(1)}
          hint={t('stats.ratingCount', { count: totals.ratings })}
        />
      </section>

      {(totals.unanswered_feedback > 0 || totals.poor_ratings > 0) && (
        <section className="grid gap-3 sm:grid-cols-2">
          <KeyFigure
            label={t('stats.poorRatings')}
            value={String(totals.poor_ratings)}
            hint={t('stats.poorRatingsHint', { max: POOR_RATING })}
            tone={totals.poor_ratings > 0 ? 'warn' : 'normal'}
          />
          <KeyFigure
            label={t('stats.unanswered')}
            value={String(totals.unanswered_feedback)}
            hint={t('stats.unansweredHint')}
            tone={totals.unanswered_feedback > 0 ? 'warn' : 'normal'}
          />
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-mist-300">{t('stats.history')}</h2>
          <p className="mb-4 text-xs text-mist-600">
            <span className="mr-3">
              <span className="mr-1 inline-block h-2 w-2 rounded-full bg-accent-500 align-middle" />
              {t('common.movies')}
            </span>
            <span>
              <span className="mr-1 inline-block h-2 w-2 rounded-full bg-accent-700 align-middle" />
              {t('common.series')}
            </span>
          </p>
          <History data={history} />
        </Card>

        <Card className="p-5">
          <h2 className="text-sm font-semibold text-mist-300">{t('stats.ratingSpread')}</h2>
          <p className="mb-4 text-xs text-mist-600">{t('stats.ratingSpreadHint')}</p>
          {totals.ratings === 0 ? (
            <p className="py-8 text-center text-sm text-mist-600">{t('stats.noRatingsYet')}</p>
          ) : (
            <div className="flex flex-col gap-2">
              {[5, 4, 3, 2, 1, 0].map((note) => (
                <Bar
                  key={note}
                  label={'★'.repeat(note) || t('stats.zeroStars')}
                  value={totals.rating_distribution[note] ?? 0}
                  max={maxBewertung}
                  accent={note <= POOR_RATING}
                />
              ))}
            </div>
          )}
        </Card>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-mist-300">{t('stats.perUser')}</h2>
        <Card className="overflow-x-auto p-4">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr className="text-xs tracking-wide text-mist-600 uppercase">
                <th className="pb-2 text-left font-medium">{t('stats.colUser')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colTotal')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colSplit')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colDownloaded')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colSuccess')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colQuotaMovies')}</th>
                <th className="px-2 pb-2 text-center font-medium">{t('stats.colQuotaSeries')}</th>
                <th className="pb-2 pl-2 text-right font-medium">{t('stats.colRating')}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((eintrag) => (
                <UserRow key={eintrag.user_id} eintrag={eintrag} />
              ))}
            </tbody>
          </table>
        </Card>
      </section>

      {beliebt.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-mist-300">{t('stats.mostRequested')}</h2>
          <Card className="flex flex-col gap-2 p-4">
            {beliebt.map((titel) => (
              <div
                key={`${titel.media_type}-${titel.tmdb_id}`}
                className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 px-3 py-2"
              >
                <span className="min-w-0 flex-1 truncate text-sm">{titel.title}</span>
                <span className="text-xs text-mist-600">
                  {t(titel.media_type === 'movie' ? 'common.movies' : 'common.series')}
                </span>
                <span className="rounded-full border border-ink-700 px-2 py-0.5 text-xs tabular-nums text-mist-400">
                  {t('stats.requestedTimes', { count: titel.count })}
                </span>
              </div>
            ))}
          </Card>
        </section>
      )}

      {totals.last_request_at && (
        <p className="text-xs text-mist-600">
          {t('stats.lastRequest', {
            date: formatDate(totals.last_request_at.slice(0, 10), i18n.language),
          })}
        </p>
      )}
    </div>
  )
}
