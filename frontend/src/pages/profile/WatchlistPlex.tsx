/**
 * Die eigene Plex-Merkliste — als Kacheln wie im Katalog.
 *
 * Bewusst **keine** Sonderdarstellung: dieselben Karten, dieselben Abzeichen
 * („bereits geladen", „angefragt", „4K", „gesehen"), derselbe Anfrage-Dialog.
 * Die Merkliste ist damit nur eine weitere Quelle von Titeln, kein zweiter
 * Weg mit eigenen Regeln — Kontingent und Freigabe gelten unverändert.
 *
 * Hier passiert nichts von selbst. Wer etwas will, klickt darauf.
 */

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MediaItem } from '../../api/types'
import type { ViewMode } from '../../components/media/optionen'
import { DetailModal } from '../../components/media/DetailModal'
import { MediaItemCard } from '../../components/media/MediaCard'
import { MediaListRow } from '../../components/media/MediaListRow'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'

type WatchlistAntwort = {
  movies: MediaItem[]
  series: MediaItem[]
  unmatched: number
}

type VerbindungsStand = { status: string; connected: boolean; linked: boolean }

/** Zustände, in denen ein Titel nichts mehr braucht. */
const ERLEDIGT = new Set(['downloaded', 'partial', 'in_library'])

export function WatchlistPlex() {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const queryClient = useQueryClient()

  const [gewaehlt, setGewaehlt] = useState<MediaItem | null>(null)
  const [nurFehlende, setNurFehlende] = useState(false)
  // Kachel oder Liste - dieselbe Wahl wie auf der Entdecken-Seite, damit die
  // Merkliste sich nicht anders anfühlt als der Rest.
  const [ansicht, setAnsicht] = useState<ViewMode>('grid')

  const stand = useQuery({
    queryKey: ['watchlist-status'],
    queryFn: () => api.get<VerbindungsStand>('/api/watchlist/status'),
  })

  const merkliste = useQuery({
    queryKey: ['watchlist-plex'],
    queryFn: () => api.get<WatchlistAntwort>('/api/watchlist/plex'),
    // Ohne hinterlegten Zugang wäre die Abfrage ein sicherer Fehlschlag.
    enabled: Boolean(stand.data?.connected),
    // Die Merkliste ändert sich in Plex, nicht hier - beim Zurückkommen auf
    // die Seite also ruhig neu holen. Der Zwischenspeicher für die
    // Zuordnungen macht das billig.
    staleTime: 60_000,
  })

  const verbinden = useMediaServerChallenge<VerbindungsStand>({
    startPfad: '/api/watchlist/connect/start',
    abfragePfad: '/api/watchlist/connect/poll',
    onFertig: (ergebnis) => {
      verbinden.abbrechen()
      queryClient.setQueryData(['watchlist-status'], ergebnis)
      void queryClient.invalidateQueries({ queryKey: ['watchlist-plex'] })
    },
  })

  const alle = useMemo(
    () => [...(merkliste.data?.movies ?? []), ...(merkliste.data?.series ?? [])],
    [merkliste.data],
  )
  const sichtbar = nurFehlende ? alle.filter((i) => !ERLEDIGT.has(i.status)) : alle
  const fehlende = alle.filter((i) => !ERLEDIGT.has(i.status)).length

  if (stand.isLoading) {
    return (
      <Card>
        <Spinner />
      </Card>
    )
  }

  // Ohne verknüpftes Plex-Konto gibt es keine Merkliste, die man lesen dürfte.
  if (!stand.data?.linked) {
    return (
      <Card>
        <h2 className="text-lg font-semibold">{t('watchlist.title')}</h2>
        <p className="mt-1.5 text-sm text-mist-500">{t('watchlist.needsLink')}</p>
      </Card>
    )
  }

  const fehlermeldung =
    merkliste.error instanceof ApiError ? merkliste.error.message : null
  // Ein abgelehnter Zugang ist kein Fehler zum Wegklicken, sondern eine
  // Handlungsanweisung: neu anmelden.
  const zugangKaputt =
    merkliste.error instanceof ApiError &&
    merkliste.error.code === 'watchlist_token_invalid'

  if (!stand.data.connected || zugangKaputt) {
    return (
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('watchlist.title')}</h2>
          <p className="mt-1.5 text-sm text-mist-500">
            {zugangKaputt ? t('watchlist.tokenInvalid') : t('watchlist.needsSignIn')}
          </p>
        </div>
        {verbinden.start ? (
          <MediaServerPrompt start={verbinden.start} onAbbrechen={verbinden.abbrechen} />
        ) : (
          <Button onClick={() => void verbinden.starten()} loading={verbinden.laeuft}>
            {t('watchlist.connect')}
          </Button>
        )}
        {verbinden.fehler && <ErrorBanner message={verbinden.fehler} />}
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{t('watchlist.title')}</h2>
            <p className="mt-1.5 text-sm text-mist-500">{t('watchlist.intro')}</p>
          </div>
          <Button
            variant="ghost"
            onClick={() => void merkliste.refetch()}
            loading={merkliste.isFetching}
          >
            {t('watchlist.reload')}
          </Button>
        </div>

        {alle.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                [false, t('watchlist.filterAll'), alle.length],
                [true, t('watchlist.filterMissing'), fehlende],
              ] as const
            ).map(([wert, beschriftung, anzahl]) => (
              <button
                key={String(wert)}
                type="button"
                onClick={() => setNurFehlende(wert)}
                aria-pressed={nurFehlende === wert}
                className={
                  'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                  (nurFehlende === wert
                    ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                    : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
                }
              >
                {beschriftung}
                <span className="ml-1.5 text-xs tabular-nums opacity-70">{anzahl}</span>
              </button>
            ))}

            <div
              className="ml-auto flex rounded-full border border-ink-700 bg-ink-900 p-0.5"
              role="group"
              aria-label={t('filters.view')}
            >
              {(['grid', 'list'] as const).map((modus) => (
                <button
                  key={modus}
                  type="button"
                  onClick={() => setAnsicht(modus)}
                  aria-pressed={ansicht === modus}
                  className={
                    'rounded-full px-3 py-1 text-xs font-semibold transition-colors ' +
                    (ansicht === modus
                      ? 'bg-accent-500 text-white'
                      : 'text-mist-500 hover:text-mist-100')
                  }
                >
                  {t(modus === 'grid' ? 'filters.viewGrid' : 'filters.viewList')}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Was Plex nicht zuordnen kann, verschwindet nicht stillschweigend. */}
        {(merkliste.data?.unmatched ?? 0) > 0 && (
          <p className="text-xs leading-relaxed text-mist-600">
            {t('watchlist.unmatched', { count: merkliste.data?.unmatched ?? 0 })}
          </p>
        )}
      </Card>

      {merkliste.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}
      {fehlermeldung && !zugangKaputt && <ErrorBanner message={fehlermeldung} />}

      {merkliste.isSuccess && alle.length === 0 && (
        <Card>
          <p className="text-sm text-mist-500">{t('watchlist.empty')}</p>
        </Card>
      )}

      {sichtbar.length > 0 &&
        (ansicht === 'grid' ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {sichtbar.map((item) => (
              <MediaItemCard
                key={`${item.media_type}-${item.tmdb_id}`}
                item={item}
                onQuickAdd={setGewaehlt}
                fromWatchlist
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {sichtbar.map((item) => (
              <MediaListRow
                key={`${item.media_type}-${item.tmdb_id}`}
                item={item}
                onQuickAdd={setGewaehlt}
                fromWatchlist
              />
            ))}
          </div>
        ))}

      <DetailModal
        item={gewaehlt}
        onClose={() => setGewaehlt(null)}
        arrConfigured={Boolean(
          gewaehlt?.media_type === 'movie'
            ? config?.radarr_configured
            : config?.sonarr_configured,
        )}
        fromWatchlist
      />
    </div>
  )
}
