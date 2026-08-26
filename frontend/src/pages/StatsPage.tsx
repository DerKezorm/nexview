import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Role, Stats, StorageOverview, UserStats } from '../api/types'
import { AufraeumTabelle } from '../components/AufraeumTabelle'
import { Avatar } from '../components/Avatar'
import { StarRating } from '../components/StarRating'
import { StorageDistribution } from '../components/StorageDistribution'
import { Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDate, formatSize } from '../lib/format'

const POOR_RATING = 2

const ROLE_LABELS: Record<Role, string> = {
  admin: 'adminUsers.roleAdmin',
  approver: 'adminUsers.roleApprover',
  user: 'adminUsers.roleUser',
  child: 'adminUsers.roleChild',
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

/**
 * Belegter Platz gegen Grenze — die GB-Fassung von `QuotaCell`.
 *
 * Eigene Zelle statt einer erweiterten `QuotaCell`: Die eine zählt Stücke und
 * schreibt „3/5", die andere formatiert Bytes und schreibt „218 GB von 500 GB".
 * In einem Bauteil wären das zwei Darstellungen mit einem gemeinsamen `if` —
 * getrennt bleibt jede für sich lesbar.
 */
function SpeicherCell({ used, limit }: { used: number | null; limit: number | null }) {
  const { t, i18n } = useTranslation()
  if (used === null) return <span className="text-mist-600">–</span>

  const belegt = formatSize(used, i18n.language)
  if (limit === null) {
    return (
      <span className="tabular-nums text-mist-300">
        {belegt} <span className="text-mist-600">· {t('stats.unlimited')}</span>
      </span>
    )
  }

  const erschoepft = used >= limit
  return (
    <span className={'tabular-nums ' + (erschoepft ? 'text-bad-500' : 'text-mist-300')}>
      {t('stats.storageOf', { used: belegt, limit: formatSize(limit, i18n.language) })}
    </span>
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


/**
 * Wer belegt wieviel Platz.
 *
 * Bewusst eine eigene Abfrage statt eines weiteren Feldes in der Statistik:
 * Die Belegung wird an ganz anderer Stelle erhoben, aendert sich in einem
 * anderen Takt, und der Statistik-Dienst bleibt so unberuehrt.
 */
function SpeicherAbschnitt() {
  const { t } = useTranslation()
  const abfrage = useQuery({
    queryKey: ['storage-overview'],
    queryFn: () => api.get<StorageOverview>('/api/storage/overview'),
  })

  const daten = abfrage.data
  // Solange nichts gemessen wurde, gibt es hier nichts zu sagen - dann bleibt
  // der Abschnitt ganz weg, statt eine Reihe von Nullen zu zeigen.
  if (!daten || daten.total_bytes === 0) return null

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-mist-300">{t('storage.usedLabel')}</h2>
      <Card className="p-4">
        <StorageDistribution shares={daten.shares} houseBytes={daten.house_bytes} />
      </Card>
    </section>
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
      {/* Alle drei Grenzen nebeneinander: Sie gelten zusammen, und welche
          davon jemanden gerade aufhält, sieht man nur im Vergleich. Bis 0.19
          stand hier entweder die Stückzahl oder der Platz - nie beides. */}
      <td className="px-2 text-center text-sm">
        <QuotaCell used={eintrag.quota_movie_used} limit={eintrag.quota_movie_limit} />
      </td>
      <td className="px-2 text-center text-sm">
        <QuotaCell used={eintrag.quota_series_used} limit={eintrag.quota_series_limit} />
      </td>
      <td className="px-2 text-center text-sm">
        <SpeicherCell
          used={eintrag.storage_used_bytes}
          limit={eintrag.storage_limit_bytes}
        />
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

/** Die zwei Ansichten dieser Seite. */
const REITER = [
  { wert: 'zahlen', schluessel: 'stats.title' },
  { wert: 'aufraeumen', schluessel: 'cleanup.title' },
] as const

type Reiter = (typeof REITER)[number]['wert']

/**
 * Überschrift und Reiterleiste - für beide Ansichten dieselbe.
 *
 * Bewusst ein gemeinsames Stück und keine zwei Kopfzeilen: Sonst springt beim
 * Umschalten die Überschrift um ein paar Pixel, weil zwei fast gleiche
 * Bausteine nie ganz gleich bleiben.
 */
function StatsKopf({
  t,
  reiter,
  setReiter,
}: {
  t: (key: string) => string
  reiter: Reiter
  setReiter: (wert: Reiter) => void
}) {
  return (
    <>
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t(reiter === 'zahlen' ? 'stats.title' : 'cleanup.title')}
          <span className="text-accent-500">.</span>
        </h1>
      </header>

      <div className="flex flex-wrap gap-2" role="tablist">
        {REITER.map((eintrag) => (
          <button
            key={eintrag.wert}
            type="button"
            role="tab"
            aria-selected={reiter === eintrag.wert}
            onClick={() => setReiter(eintrag.wert)}
            className={
              'rounded-full border px-4 py-2 text-sm font-medium transition-colors ' +
              (reiter === eintrag.wert
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {t(eintrag.schluessel)}
          </button>
        ))}
      </div>
    </>
  )
}

export function StatsPage() {
  const { t, i18n } = useTranslation()
  const [reiter, setReiter] = useState<Reiter>('zahlen')

  const statsQuery = useQuery({
    queryKey: ['admin-stats'],
    queryFn: () => api.get<Stats>('/api/admin/stats'),
    // Nur im ersten Reiter gebraucht - im zweiten waere es eine Abfrage
    // fuer nichts.
    enabled: reiter === 'zahlen',
  })

  if (reiter === 'aufraeumen') {
    return (
      <div className="flex flex-col gap-6">
        <StatsKopf t={t} reiter={reiter} setReiter={setReiter} />
        <AufraeumTabelle pfad="/api/admin/stats/aufraeumen" schluessel="admin-aufraeumen" />
      </div>
    )
  }

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
      <StatsKopf t={t} reiter={reiter} setReiter={setReiter} />
      <p className="-mt-4 text-mist-500">{t('stats.intro')}</p>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
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

      <SpeicherAbschnitt />

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
                <th className="px-2 pb-2 text-center font-medium">
                  {t('stats.colQuotaMovies')}
                </th>
                <th className="px-2 pb-2 text-center font-medium">
                  {t('stats.colQuotaSeries')}
                </th>
                <th className="px-2 pb-2 text-center font-medium">
                  {t('stats.colStorage')}
                </th>
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
