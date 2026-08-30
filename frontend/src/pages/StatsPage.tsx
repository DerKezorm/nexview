import type { TFunction } from 'i18next'
import { useSearchParams } from 'react-router-dom'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { AnalyseStand, WiedergabeStand, Role, Stats, StorageOverview, UserStats } from '../api/types'
import { Avatar } from '../components/Avatar'
import { StarRating } from '../components/StarRating'
import { StorageDistribution } from '../components/StorageDistribution'
import { BereichsBefunde } from '../components/BereichsBefunde'
import { Reiterreihe } from '../components/Reiterreihe'
import { Card, ErrorBanner, Kennzahl, Spinner } from '../components/ui'
import { AnalyseBetrieb } from './stats/AnalyseBetrieb'
import { AnalyseBibliothek } from './stats/AnalyseBibliothek'
import { AnalyseDienste } from './stats/AnalyseDienste'
import { AnalyseWiedergabe } from './stats/AnalyseWiedergabe'
import { formatDate, formatSize } from '../lib/format'

const POOR_RATING = 2

const ROLE_LABELS: Record<Role, string> = {
  admin: 'adminUsers.roleAdmin',
  approver: 'adminUsers.roleApprover',
  user: 'adminUsers.roleUser',
  child: 'adminUsers.roleChild',
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
/**
 * Welcher Reiter hinter welchem Adress-Wort steckt.
 *
 * ⚠️ **Damit ein Verweis dort landet, wo es um etwas geht.** „47 Titel liegen
 * herum" auf die Zahlen-Übersicht zu schicken hieße, den Leser danach selbst
 * suchen zu lassen — dieselbe Beschwerde, die der Nutzer über die Einstellungen
 * hatte. Die Wörter sind eine Zusage: Sie stehen in Befund-Zielen.
 */
const REITER_AUS_ADRESSE: Record<string, Reiter> = {
  anfragen: 'anfragen',
  nutzer: 'nutzer',
  wiedergabe: 'wiedergabe',
  bibliothek: 'bibliothek',
  dienste: 'dienste',
  betrieb: 'betrieb',
  // ⚠️ **Die alten Namen bleiben gültig.** Sie stehen in Lesezeichen und in
  // Befund-Zielen, die vor dem Umbau gesetzt wurden. Ein Link, der ins Leere
  // zeigt, ist schlimmer als ein Link auf den zweitbesten Reiter — dasselbe
  // macht das Profil mit `sprache` und `sicherheit`.
  zahlen: 'anfragen',
  aufraeumen: 'bibliothek',
}

const REITER = [
  { wert: 'anfragen', schluessel: 'analyse.tabRequests', symbol: 'film' },
  { wert: 'nutzer', schluessel: 'analyse.tabUsers', symbol: 'benutzer' },
  { wert: 'wiedergabe', schluessel: 'wiedergabe.tabPlayback', symbol: 'medienserver' },
  { wert: 'bibliothek', schluessel: 'analyse.tabLibrary', symbol: 'kontingent' },
  { wert: 'dienste', schluessel: 'analyse.tabServices', symbol: 'dienste' },
  { wert: 'betrieb', schluessel: 'analyse.tabOperations', symbol: 'system' },
] as const

type Reiter = (typeof REITER)[number]['wert']

/**
 * Eine Dauer in Stunden lesbar machen — mit mitwandernder Einheit.
 *
 * ⚠️ **Ohne das steht bei einer typischen Anlage „0 Std.".** An echten Daten
 * gemessen: Der Median lag bei 0,1 Stunden, also sechs Minuten — gerundet auf
 * Stunden eine Null, und eine Null liest sich als „gibt es nicht" statt als
 * „geht schnell". Der längste offene Fall lag gleichzeitig bei 104 Stunden;
 * eine einzige Einheit kann beides nicht.
 */
function dauerText(stunden: number, t: TFunction): string {
  if (stunden < 1) return t('stats.minutes', { count: Math.max(1, Math.round(stunden * 60)) })
  if (stunden < 48) return t('stats.hours', { count: Math.round(stunden) })
  return t('stats.days', { count: Math.round(stunden / 24) })
}

export function StatsPage() {
  const { t, i18n } = useTranslation()
  const [suchparameter, setSuchparameter] = useSearchParams()
  const [reiter, setReiter] = useState<Reiter>(
    REITER_AUS_ADRESSE[suchparameter.get('reiter') ?? ''] ?? 'anfragen',
  )

  /** Beim Wechseln von Hand fliegt der Parameter raus — sonst springt ein
   *  Neuladen auf den Reiter aus der Adresse zurück. */
  const wechseln = (wert: Reiter) => {
    setReiter(wert)
    if (suchparameter.has('reiter')) {
      const rest = new URLSearchParams(suchparameter)
      rest.delete('reiter')
      setSuchparameter(rest, { replace: true })
    }
  }

  // ⚠️ **Zwei Abfragen, je nach Reiter — nicht eine grosse.** Die Zahlen zu
  // den Anfragen laufen ueber jede Zeile der Anfragetabelle; der Zustand der
  // Instanzen ist ein Blick in drei kleine Tabellen. Beides immer zu holen
  // hiesse, den teuren Teil auch dann zu rechnen, wenn jemand nur wissen
  // will, ob Sonarr antwortet.
  const zahlenNoetig = reiter === 'anfragen' || reiter === 'nutzer'
  const analyseNoetig = !zahlenNoetig

  const statsQuery = useQuery({
    queryKey: ['admin-stats'],
    queryFn: () => api.get<Stats>('/api/admin/stats'),
    enabled: zahlenNoetig,
  })

  const analyseQuery = useQuery({
    queryKey: ['admin-analyse'],
    queryFn: () => api.get<AnalyseStand>('/api/admin/analyse'),
    enabled: analyseNoetig && reiter !== 'wiedergabe',
  })

  // ⚠️ Eine eigene Abfrage: Sie rechnet ueber jede Wiedergabe und jeden
  // Speicherposten. Wer nur wissen will, ob Sonarr antwortet, soll das nicht
  // mitbezahlen.
  const wiedergabeQuery = useQuery({
    queryKey: ['admin-wiedergabe'],
    queryFn: () => api.get<WiedergabeStand>('/api/admin/analyse/wiedergabe'),
    enabled: reiter === 'wiedergabe',
  })

  const kopf = (
    <>
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('analyse.title')}
          <span className="text-accent-500">.</span>
        </h1>
      </header>
      <Reiterreihe
        eintraege={REITER.map((e) => ({
          value: e.wert,
          label: t(e.schluessel),
          symbol: e.symbol,
        }))}
        aktiv={reiter}
        onWechsel={wechseln}
        label={t('analyse.title')}
      />
    </>
  )

  const aktiveAbfrage = reiter === 'wiedergabe' ? wiedergabeQuery : analyseQuery

  if (analyseNoetig) {
    return (
      <div className="flex flex-col gap-6">
        {kopf}
        {/* ⚠️ **Die abgeschaltete Abfrage meldet trotzdem `isPending`.** In
            TanStack Query heißt `enabled: false` nicht „fertig", sondern „noch
            nicht angefangen" — und stünde der Spinner an beiden Abfragen, hinge
            er dauerhaft neben fertigem Inhalt. Gefragt wird deshalb die, die
            zu diesem Reiter gehört. */}
        {aktiveAbfrage.isPending && (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('common.loading')}
          </p>
        )}
        {aktiveAbfrage.isError && <ErrorBanner message={t('stats.failed')} />}
        {wiedergabeQuery.data && reiter === 'wiedergabe' && (
          <AnalyseWiedergabe stand={wiedergabeQuery.data} />
        )}
        {analyseQuery.data && reiter === 'bibliothek' && (
          <AnalyseBibliothek stand={analyseQuery.data} />
        )}
        {analyseQuery.data && reiter === 'dienste' && (
          <AnalyseDienste stand={analyseQuery.data} />
        )}
        {analyseQuery.data && reiter === 'betrieb' && (
          <AnalyseBetrieb stand={analyseQuery.data} />
        )}
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
      {kopf}
      {reiter === 'anfragen' && <BereichsBefunde bereiche={['nachschub']} />}

      {reiter === 'anfragen' && (
      <>
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kennzahl
          label={t('stats.totalRequests')}
          wert={String(totals.requests)}
          hinweis={
            t('stats.movieCount', { count: totals.movies }) +
            ' · ' +
            t('stats.seriesCount', { count: totals.series }) +
            ' · ' +
            t('stats.byUsers', { count: totals.active_users })
          }
        />
        <Kennzahl
          label={t('stats.downloaded')}
          wert={String(totals.downloaded)}
          hinweis={
            t('stats.movieCount', { count: totals.downloaded_movies }) +
            ' · ' +
            t('stats.seriesCount', { count: totals.downloaded_series })
          }
        />
        <Kennzahl
          label={t('stats.waiting')}
          wert={String(totals.pending)}
          hinweis={t('stats.rejectedCancelled', {
            rejected: totals.rejected,
            cancelled: totals.cancelled,
          })}
          ton={totals.pending > 0 ? 'warn' : 'normal'}
        />
        <Kennzahl
          label={t('stats.waitForApproval')}
          wert={
            totals.freigabe_median_stunden === null
              ? '–'
              : dauerText(totals.freigabe_median_stunden, t)
          }
          hinweis={
            totals.freigabe_laengste_offen_stunden === null
              ? t('stats.waitForApprovalHint')
              : t('stats.waitLongest', {
                  dauer: dauerText(totals.freigabe_laengste_offen_stunden, t),
                })
          }
          ton={
            (totals.freigabe_laengste_offen_stunden ?? 0) > 72 ? 'warn' : 'normal'
          }
        />
        <Kennzahl
          label={t('stats.averageRating')}
          wert={totals.average_rating === null ? '–' : totals.average_rating.toFixed(1)}
          hinweis={t('stats.ratingCount', { count: totals.ratings })}
        />
      </section>

      {(totals.unanswered_feedback > 0 || totals.poor_ratings > 0) && (
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Kennzahl
            label={t('stats.poorRatings')}
            wert={String(totals.poor_ratings)}
            hinweis={t('stats.poorRatingsHint', { max: POOR_RATING })}
            ton={totals.poor_ratings > 0 ? 'warn' : 'normal'}
          />
          <Kennzahl
            label={t('stats.unanswered')}
            wert={String(totals.unanswered_feedback)}
            hinweis={t('stats.unansweredHint')}
            ton={totals.unanswered_feedback > 0 ? 'warn' : 'normal'}
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

      </>
      )}

      {reiter === 'nutzer' && (
      <>
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
      </>
      )}

      {reiter === 'anfragen' && beliebt.length > 0 && (
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

      {reiter === 'anfragen' && totals.last_request_at && (
        <p className="text-xs text-mist-600">
          {t('stats.lastRequest', {
            date: formatDate(totals.last_request_at.slice(0, 10), i18n.language),
          })}
        </p>
      )}
    </div>
  )
}
