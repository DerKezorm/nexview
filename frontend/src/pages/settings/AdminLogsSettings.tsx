import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, downloadFile } from '../../api/client'
import type { LogEntry } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, ErrorBanner, Spinner } from '../../components/ui'

type Stufe = 'ALL' | 'INFO' | 'WARNING' | 'ERROR'

const STUFEN: Stufe[] = ['ALL', 'INFO', 'WARNING', 'ERROR']

const FARBEN: Record<string, string> = {
  INFO: 'text-mist-500',
  WARNING: 'text-warn-500',
  ERROR: 'text-bad-500',
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

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-mist-500">{t('logs.intro')}</p>

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
                <th className="px-3 py-2 font-medium">{t('logs.message')}</th>
              </tr>
            </thead>
            <tbody>
              {zeilen.map((zeile, index) => (
                <tr key={`${zeile.time}-${index}`} className="border-b border-ink-700/50 last:border-b-0">
                  <td className="px-3 py-2 whitespace-nowrap text-mist-600">{zeile.time}</td>
                  <td className={'px-3 py-2 font-semibold ' + (FARBEN[zeile.level] ?? '')}>
                    {zeile.level}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap text-mist-600">{zeile.logger}</td>
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
