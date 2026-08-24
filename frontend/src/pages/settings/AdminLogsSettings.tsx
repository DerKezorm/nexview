import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, downloadFile } from '../../api/client'
import type { LogEntry, LogModeState } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, ErrorBanner, Spinner } from '../../components/ui'

type Stufe = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'

const STUFEN: Stufe[] = ['ALL', 'INFO', 'WARNING', 'ERROR']

/** Reihenfolge wie im Backend: von sparsam nach gesprächig. */
const MODI = ['quiet', 'normal', 'detailed', 'trace'] as const
type Modus = (typeof MODI)[number]

/** Diese beiden schalten sich nach der gewählten Zeit selbst wieder ab. */
const TIEFE_MODI: readonly string[] = ['detailed', 'trace']

const DAUERN = [30, 120, 480, 0] as const

const FARBEN: Record<string, string> = {
  DEBUG: 'text-mist-600',
  INFO: 'text-mist-500',
  WARNING: 'text-warn-500',
  ERROR: 'text-bad-500',
  CRITICAL: 'text-bad-500',
}

/**
 * Protokoll der Anwendung - nur für Administratoren.
 *
 * Die Meldungen sind bewusst englisch: Log-Zeilen landen in Fehlerberichten
 * und Suchanfragen, dort hilft Englisch weiter.
 */
export function AdminLogsSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [stufe, setStufe] = useState<Stufe>('ALL')
  const [suche, setSuche] = useState('')
  // Welche tiefe Stufe gerade nach ihrer Dauer gefragt wird (null = keine).
  const [gefragt, setGefragt] = useState<Modus | null>(null)
  const [clearing, setClearing] = useState(false)

  const logsQuery = useQuery({
    queryKey: ['logs', stufe, suche],
    queryFn: () => {
      const params = new URLSearchParams({ limit: '300' })
      if (stufe !== 'ALL') params.set('level', stufe)
      if (suche.trim()) params.set('search', suche.trim())
      return api.get<LogEntry[]>(`/api/logs?${params.toString()}`)
    },
    refetchInterval: 30_000,
  })

  const modusQuery = useQuery({
    queryKey: ['logs', 'level'],
    queryFn: () => api.get<LogModeState>('/api/logs/level'),
    // Häufiger als die Zeilen: Läuft eine Diagnose-Stufe ab, soll die Anzeige
    // das zeitnah zeigen und nicht eine halbe Stunde lang lügen.
    refetchInterval: 60_000,
  })

  const modusMutation = useMutation({
    mutationFn: (wunsch: { mode: Modus; minutes: number }) =>
      api.put<LogModeState>('/api/logs/level', wunsch),
    onSuccess: (stand) => {
      setGefragt(null)
      queryClient.setQueryData(['logs', 'level'], stand)
      void queryClient.invalidateQueries({ queryKey: ['logs'] })
    },
  })

  const downloadMutation = useMutation({
    mutationFn: () => downloadFile('/api/logs/download', 'nexview-log.txt'),
  })

  const clearMutation = useMutation({
    mutationFn: () => api.delete<void>('/api/logs'),
    onSuccess: () => {
      setClearing(false)
      void queryClient.invalidateQueries({ queryKey: ['logs'] })
    },
  })

  const zeilen = logsQuery.data ?? []
  const modus = modusQuery.data
  const gesperrt = modus?.fixed_by_env ?? false

  /**
   * Sparsam und Normal gelten unbegrenzt - ein Klick genügt.
   *
   * Die beiden tiefen Stufen fragen zuerst nach der Dauer, und zwar **erst
   * hier**: Ein immer sichtbares Dauer-Feld liest sich wie „Normal für zwei
   * Stunden", obwohl Normal gar keine Frist hat.
   */
  function umschalten(ziel: Modus) {
    if (gesperrt) return
    if (TIEFE_MODI.includes(ziel)) {
      // Auch bei der bereits laufenden Stufe: So lässt sich die Frist verlängern.
      setGefragt(gefragt === ziel ? null : ziel)
      return
    }
    setGefragt(null)
    if (ziel !== modus?.mode) modusMutation.mutate({ mode: ziel, minutes: 0 })
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-mist-500">{t('logs.intro')}</p>

      {/* --- Aufzeichnungsstufe ------------------------------------------- */}
      <section className="flex flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/40 p-4">
        <div>
          <h3 className="text-sm font-semibold text-mist-100">{t('logs.modeTitle')}</h3>
          <p className="mt-1 text-xs text-mist-500">{t('logs.modeIntro')}</p>
        </div>

        <div className="flex flex-wrap gap-2">
          {MODI.map((wert) => (
            <button
              key={wert}
              type="button"
              disabled={gesperrt || modusMutation.isPending}
              onClick={() => umschalten(wert)}
              aria-pressed={modus?.mode === wert}
              title={t(`logs.modeDesc.${wert}`)}
              className={
                'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                'disabled:cursor-not-allowed disabled:opacity-50 ' +
                (modus?.mode === wert
                  ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                  : gefragt === wert
                    ? 'border-accent-500/40 bg-ink-800 text-mist-100'
                    : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              {t(`logs.mode.${wert}`)}
              {TIEFE_MODI.includes(wert) && (
                <span className="ml-1.5 text-xs font-normal opacity-60">
                  {t('logs.modeTemporary')}
                </span>
              )}
            </button>
          ))}
        </div>

        {gefragt && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent-500/30 bg-ink-900/60 px-3 py-2">
            <span className="text-xs text-mist-300">
              {t('logs.durationQuestion', { mode: t(`logs.mode.${gefragt}`) })}
            </span>
            {DAUERN.map((wert) => (
              <button
                key={wert}
                type="button"
                disabled={modusMutation.isPending}
                onClick={() => modusMutation.mutate({ mode: gefragt, minutes: wert })}
                className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-xs text-mist-100 transition-colors hover:border-accent-500/60 hover:text-accent-400 disabled:opacity-50"
              >
                {t(`logs.duration.${wert}`)}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setGefragt(null)}
              className="text-xs text-mist-600 underline hover:text-mist-300"
            >
              {t('common.cancel')}
            </button>
          </div>
        )}

        <p className="text-xs text-mist-500">
          {gesperrt
            ? t('logs.modeEnv')
            : modus?.until
              ? t('logs.modeUntil', { time: new Date(modus.until).toLocaleString() })
              : t(`logs.modeDesc.${modus?.mode ?? 'normal'}`) + ' ' + t('logs.modeNoLimit')}
        </p>

        {modus?.mode === 'trace' && !gesperrt && (
          <p className="text-xs text-warn-500">{t('logs.traceWarning')}</p>
        )}

        {modusMutation.isError && (
          <ErrorBanner
            message={
              modusMutation.error instanceof ApiError
                ? modusMutation.error.message
                : t('errors.generic')
            }
          />
        )}
      </section>

      {/* --- Filter -------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        {STUFEN.map((wert) => (
          <button
            key={wert}
            type="button"
            onClick={() => setStufe(wert)}
            aria-pressed={stufe === wert}
            className={
              'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
              (stufe === wert
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {wert === 'ALL' ? t('logs.levelAll') : wert}
          </button>
        ))}

        <input
          type="search"
          value={suche}
          onChange={(event) => setSuche(event.target.value)}
          placeholder={t('logs.searchPlaceholder')}
          aria-label={t('logs.searchPlaceholder')}
          className="min-w-40 flex-1 rounded-full border border-ink-700 bg-ink-900 px-4 py-1.5 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
        />

        <Button
          variant="ghost"
          onClick={() => downloadMutation.mutate()}
          loading={downloadMutation.isPending}
        >
          {t('logs.download')}
        </Button>
        <Button variant="ghost" onClick={() => setClearing(true)} loading={clearMutation.isPending}>
          {t('logs.clear')}
        </Button>
      </div>

      <p className="-mt-2 text-xs text-mist-600">{t('logs.levelHint')}</p>

      {downloadMutation.isError && (
        <ErrorBanner
          message={
            downloadMutation.error instanceof ApiError
              ? downloadMutation.error.message
              : t('errors.generic')
          }
        />
      )}

      <ConfirmDialog
        open={clearing}
        title={t('logs.clear')}
        description={t('logs.confirmClear')}
        confirmLabel={t('logs.clearConfirm')}
        loading={clearMutation.isPending}
        onCancel={() => setClearing(false)}
        onConfirm={() => clearMutation.mutate()}
      />

      {logsQuery.isError && (
        <ErrorBanner
          message={
            logsQuery.error instanceof ApiError ? logsQuery.error.message : t('errors.generic')
          }
        />
      )}

      {logsQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      {!logsQuery.isPending && zeilen.length === 0 && (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500">
          {t('logs.empty')}
        </p>
      )}

      {zeilen.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/60">
          <table className="w-full min-w-160 text-left text-xs">
            <thead className="border-b border-ink-700 text-mist-600">
              <tr>
                <th className="px-3 py-2 font-medium">{t('logs.time')}</th>
                <th className="px-3 py-2 font-medium">{t('logs.level')}</th>
                <th className="px-3 py-2 font-medium">{t('logs.source')}</th>
                <th className="px-3 py-2 font-medium">{t('logs.requestId')}</th>
                <th className="px-3 py-2 font-medium">{t('logs.message')}</th>
              </tr>
            </thead>
            <tbody>
              {zeilen.map((zeile, index) => (
                <tr
                  key={`${zeile.time}-${index}`}
                  className="border-b border-ink-700/50 last:border-b-0"
                >
                  <td className="px-3 py-2 whitespace-nowrap text-mist-600">{zeile.time}</td>
                  <td className={'px-3 py-2 font-semibold ' + (FARBEN[zeile.level] ?? '')}>
                    {zeile.level}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-mist-600">{zeile.logger}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {zeile.request_id ? (
                      // Ein Klick filtert auf genau diese eine Anfrage - das ist
                      // der Weg von "Nummer aus der Fehlermeldung" zum Ablauf.
                      <button
                        type="button"
                        onClick={() => setSuche(zeile.request_id ?? '')}
                        title={t('logs.requestIdHint')}
                        className="font-mono text-accent-400 hover:underline"
                      >
                        {zeile.request_id}
                      </button>
                    ) : (
                      <span className="text-mist-700">–</span>
                    )}
                    {zeile.user && <span className="ml-2 text-mist-600">{zeile.user}</span>}
                  </td>
                  <td className="px-3 py-2 text-mist-300">{zeile.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-mist-600">{t('logs.retention')}</p>
    </div>
  )
}
