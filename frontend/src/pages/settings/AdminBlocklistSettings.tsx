import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../../api/client'
import type { MediaType } from '../../api/types'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatDate } from '../../lib/format'

type BlockedTitle = {
  media_type: MediaType
  tmdb_id: number
  title: string
  poster_url: string | null
  reason: string | null
  created_at: string
}

/**
 * Die Sperrliste: was in dieser Bibliothek nichts zu suchen hat.
 *
 * Bewusst getrennt von der Altersbeschränkung im Benutzerbereich. Die gilt je
 * Benutzer und macht Titel unsichtbar; diese hier gilt für alle und lässt sie
 * sichtbar - mit dem Abzeichen "Gesperrt" und ohne Einkaufswagen. Wer danach
 * sucht, soll erfahren, dass es den Titel hier nicht geben wird, statt dreimal
 * vergeblich anzufragen.
 */
export function AdminBlocklistSettings() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const liste = useQuery({
    queryKey: ['blocklist'],
    queryFn: () => api.get<BlockedTitle[]>('/api/admin/blocklist'),
  })

  const freigeben = useMutation({
    mutationFn: (eintrag: BlockedTitle) =>
      api.delete<void>(`/api/admin/blocklist/${eintrag.media_type}/${eintrag.tmdb_id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['blocklist'] })
      // Die Kacheln tragen sonst weiter "Gesperrt".
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      void queryClient.invalidateQueries({ queryKey: ['title-detail'] })
    },
  })

  if (liste.isLoading) {
    return (
      <Card className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </Card>
    )
  }

  const eintraege = liste.data ?? []

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('blocklist.title')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('blocklist.intro')}</p>
      </div>

      {liste.error && <ErrorBanner message={t('errors.generic')} />}
      {freigeben.error && (
        <ErrorBanner
          message={
            freigeben.error instanceof ApiError ? freigeben.error.message : t('errors.generic')
          }
        />
      )}

      {eintraege.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500">
          {t('blocklist.empty')}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {eintraege.map((eintrag) => (
            <li
              key={`${eintrag.media_type}-${eintrag.tmdb_id}`}
              className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-2"
            >
              {eintrag.poster_url ? (
                <img
                  src={eintrag.poster_url}
                  alt=""
                  className="h-16 w-11 shrink-0 self-start rounded-lg object-cover"
                />
              ) : (
                <div className="h-16 w-11 shrink-0 self-start rounded-lg bg-ink-850" />
              )}

              <div className="min-w-0 flex-1">
                {/* Der Titel führt auf seine Seite - dort sieht man, worum es
                    geht, bevor man wieder freigibt. */}
                <Link
                  to={`/titel/${eintrag.media_type}/${eintrag.tmdb_id}`}
                  className="font-medium text-mist-100 hover:text-accent-400"
                >
                  {eintrag.title || `#${eintrag.tmdb_id}`}
                </Link>
                <p className="text-xs text-mist-600">
                  {t(eintrag.media_type === 'movie' ? 'common.movie' : 'common.series')} ·{' '}
                  {t('blocklist.since', {
                    date: formatDate(eintrag.created_at.slice(0, 10), i18n.language),
                  })}
                </p>
                {eintrag.reason && <p className="mt-0.5 text-sm text-mist-300">{eintrag.reason}</p>}
              </div>

              <Button
                variant="ghost"
                onClick={() => freigeben.mutate(eintrag)}
                loading={freigeben.isPending && freigeben.variables?.tmdb_id === eintrag.tmdb_id}
              >
                {t('blocklist.unblock')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
