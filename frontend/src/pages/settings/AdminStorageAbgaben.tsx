import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { StorageAbgabe } from '../../api/types'
import { Button, Card, ErrorBanner } from '../../components/ui'
import { formatDateTime, formatSize } from '../../lib/format'

/**
 * Was auf eine Entscheidung wartet.
 *
 * Der Nutzer hat „brauche ich nicht mehr" gewählt – **passiert ist dabei
 * nichts.** Der Titel liegt weiter auf der Platte und zählt weiter auf seinem
 * Konto, bis hier jemand entscheidet. Genau deshalb darf diese Karte nicht
 * unauffällig sein: Wer sie übersieht, lässt jemanden auf einer Belastung
 * sitzen, die er losgeworden zu sein glaubt.
 *
 * **In dieser Stufe gibt es nur einen Ausgang: Das Haus übernimmt.** Löschen
 * kommt später und ist der einzige Schritt ohne Rückweg – bis dahin wird hier
 * keine Datei angefasst, sondern nur umgebucht.
 */
export function AdminStorageAbgaben() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const abfrage = useQuery({
    queryKey: ['storage-releases'],
    queryFn: () => api.get<StorageAbgabe[]>('/api/storage/releases'),
  })

  const insHaus = useMutation({
    mutationFn: (posten: number) => api.post(`/api/storage/entries/${posten}/haus`, {}),
    onSuccess: () => {
      // Alle drei Seiten der Rechnung: Die Warteschlange wird kürzer, das
      // Konto leichter, der Hausbestand schwerer.
      void queryClient.invalidateQueries({ queryKey: ['storage-releases'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-user'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-overview'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-house'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
    },
  })

  if (abfrage.isLoading) return null
  const zeilen = abfrage.data ?? []
  // Nichts zu entscheiden heißt: keine Karte. Eine leere Warteschlange ist
  // keine Nachricht.
  if (zeilen.length === 0) return null

  return (
    <Card className="flex flex-col gap-3 border-warn-500/40 p-5">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-ink-700 pb-3">
        <h3 className="font-medium">{t('storageReleases.title')}</h3>
        <span className="text-sm font-semibold text-warn-500">
          {t('storageReleases.count', { count: zeilen.length })}
        </span>
      </div>

      <p className="text-sm text-mist-500">{t('storageReleases.intro')}</p>

      {insHaus.error ? (
        <ErrorBanner
          message={
            insHaus.error instanceof ApiError ? insHaus.error.message : t('errors.generic')
          }
        />
      ) : null}

      <ul className="flex flex-col">
        {zeilen.map((zeile) => (
          <li
            key={zeile.entry.id}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-800 py-2.5 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <p
                className="line-clamp-1 text-sm font-medium"
                title={zeile.entry.path || undefined}
              >
                {zeile.entry.title}
              </p>
              <p className="text-xs text-mist-600">
                {zeile.display_name || zeile.username}
                {zeile.entry.season !== null && (
                  <span className="ml-1.5">
                    {t('storage.season', { number: zeile.entry.season })}
                  </span>
                )}
                {zeile.entry.tier === 'uhd' && (
                  <span className="ml-1.5 text-accent-500">4K</span>
                )}
                {/* Seit wann – ohne das lässt sich nicht erkennen, ob die
                    Warteschlange stockt. */}
                {zeile.released_at && (
                  <span className="ml-1.5">
                    ·{' '}
                    {t('storageReleases.since', {
                      date: formatDateTime(zeile.released_at, i18n.language),
                    })}
                  </span>
                )}
              </p>
            </div>

            <span className="shrink-0 tabular-nums">
              {formatSize(zeile.entry.size_bytes, i18n.language)}
            </span>

            <Button
              onClick={() => insHaus.mutate(zeile.entry.id)}
              disabled={insHaus.isPending}
              className="shrink-0 px-3 py-1 text-xs"
            >
              {t('storage.toHouse')}
            </Button>
          </li>
        ))}
      </ul>

      <p className="text-xs leading-relaxed text-mist-600">
        {t('storageReleases.hint')}
      </p>
    </Card>
  )
}
