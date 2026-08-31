import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Favorite, FavoritePerson, MediaItem } from '../../api/types'

/**
 * Die eigenen Favoriten - einmal geladen und überall verfügbar.
 *
 * Bewusst eine gemeinsame Abfrage statt einer pro Herz: auf einer Seite mit
 * zwanzig Kacheln wären das zwanzig Aufrufe für dieselbe Liste.
 */
export function useFavorites() {
  const query = useQuery({
    queryKey: ['favorites'],
    queryFn: () => api.get<Favorite[]>('/api/favorites'),
    staleTime: 5 * 60 * 1000,
  })

  const markiert = new Set((query.data ?? []).map((f) => `${f.media_type}-${f.tmdb_id}`))
  // ⚠️ `fehlgeschlagen` gehoert mit heraus. Ohne das gab der Haken bei einer
  // Stoerung dasselbe zurueck wie bei einer wirklich leeren Merkliste - und
  // die Seite darueber konnte gar nicht unterscheiden, was sie anzeigen soll.
  return { favorites: query.data ?? [], markiert, fehlgeschlagen: query.isError }
}

/**
 * Wie ein Herz aussieht, dessen Klick nicht angekommen ist.
 *
 * ⚠️ **Die Meldung sitzt am Knopf, nicht oben auf der Seite.** Herzen sitzen
 * auf Kacheln - in Regalreihen, auf der Titelseite, in Suchergebnissen. Ein
 * Banner am Seitenkopf wäre weit weg von der Stelle, auf die der Mensch gerade
 * gesehen hat. Vorher gab es gar nichts: Das Herz sprang zurück, und
 * „nicht gemerkt" sah genauso aus wie „danebengeklickt" - also klickte man
 * noch einmal. Und noch einmal.
 *
 * Der Beschriftungstext wechselt mit, damit ein Vorleseprogramm es mitsagt.
 */
const FEHLGESCHLAGEN =
  'border-bad-500 bg-bad-500/15 text-bad-500 hover:bg-bad-500/25'

function HeartIcon({ gefuellt, className = 'h-4 w-4' }: { gefuellt: boolean; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill={gefuellt ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21.2l7.7-7.7 1.1-1.1a5.5 5.5 0 0 0 0-7.8Z" />
    </svg>
  )
}

/**
 * Herz zum Markieren.
 *
 * Die Markierung ist die Grundlage der kuratierten Empfehlungen - deshalb
 * steht im Titel des Knopfes auch, wozu sie gut ist. Sonst wirkt sie wie eine
 * Merkliste, die nichts bewirkt.
 */
export function FavoriteButton({
  item,
  markiert,
  className = '',
  gross = false,
}: {
  item: MediaItem | { media_type: 'movie' | 'tv'; tmdb_id: number; title: string; poster_url: string | null }
  markiert: boolean
  className?: string
  gross?: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const umschalten = useMutation({
    mutationFn: async () => {
      if (markiert) {
        await api.delete(`/api/favorites/${item.media_type}/${item.tmdb_id}`)
        return
      }
      await api.post('/api/favorites', {
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        title: item.title,
        poster_url: item.poster_url,
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['favorites'] })
      // Die kuratierte Liste hängt direkt daran.
      void queryClient.invalidateQueries({ queryKey: ['home-curated'] })
      // Die Stöber-Übersicht führt je Herz eine eigene Reihe.
      void queryClient.invalidateQueries({ queryKey: ['regale'] })
    },
  })

  return (
    <button
      type="button"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        umschalten.mutate()
      }}
      disabled={umschalten.isPending}
      title={umschalten.isError ? t('favorites.failed') : t(markiert ? 'favorites.remove' : 'favorites.add')}
      aria-label={
        umschalten.isError
          ? `${item.title} – ${t('favorites.failed')}`
          : `${item.title} – ${t(markiert ? 'favorites.remove' : 'favorites.add')}`
      }
      aria-pressed={markiert}
      className={
        'shrink-0 rounded-full border transition-colors ' +
        (gross ? 'p-2.5 ' : 'p-1.5 ') +
        (umschalten.isError
          ? FEHLGESCHLAGEN
          : markiert
            ? 'border-accent-500 bg-accent-500/15 text-accent-400 hover:bg-accent-500/25'
            : 'border-ink-700 bg-ink-900 text-mist-400 hover:border-accent-500 hover:text-accent-400') +
        ' ' +
        className
      }
    >
      <HeartIcon gefuellt={markiert} className={gross ? 'h-5 w-5' : 'h-4 w-4'} />
    </button>
  )
}

// --- Personen --------------------------------------------------------------

/** Ein für den Herz-Knopf ausreichendes Personen-Objekt. */
type PersonLike = {
  person_id: number
  name: string
  photo_url: string | null
  department: string
}

/** Die gemerkten Personen - einmal geladen, überall verfügbar (siehe useFavorites). */
export function usePersonFavorites() {
  const query = useQuery({
    queryKey: ['person-favorites'],
    queryFn: () => api.get<FavoritePerson[]>('/api/favorites/people'),
    staleTime: 5 * 60 * 1000,
  })
  const markiert = new Set((query.data ?? []).map((p) => p.person_id))
  return { people: query.data ?? [], markiert, fehlgeschlagen: query.isError }
}

/** Herz zum Merken einer Person - gebaut wie das Herz an einem Titel. */
export function FavoritePersonButton({
  person,
  markiert,
  className = '',
  gross = false,
}: {
  person: PersonLike
  markiert: boolean
  className?: string
  gross?: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const umschalten = useMutation({
    mutationFn: async () => {
      if (markiert) {
        await api.delete(`/api/favorites/people/${person.person_id}`)
        return
      }
      await api.post('/api/favorites/people', {
        person_id: person.person_id,
        name: person.name,
        photo_url: person.photo_url,
        department: person.department,
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['person-favorites'] })
      // Die kuratierte Liste bezieht auch gemerkte Personen ein.
      void queryClient.invalidateQueries({ queryKey: ['home-curated'] })
      // Die Stöber-Übersicht führt je Herz eine eigene Reihe.
      void queryClient.invalidateQueries({ queryKey: ['regale'] })
    },
  })

  return (
    <button
      type="button"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        umschalten.mutate()
      }}
      disabled={umschalten.isPending}
      title={umschalten.isError ? t('favorites.failed') : t(markiert ? 'favorites.removePerson' : 'favorites.addPerson')}
      aria-label={
        umschalten.isError
          ? `${person.name} – ${t('favorites.failed')}`
          : `${person.name} – ${t(markiert ? 'favorites.removePerson' : 'favorites.addPerson')}`
      }
      aria-pressed={markiert}
      className={
        'shrink-0 rounded-full border transition-colors ' +
        (gross ? 'p-2.5 ' : 'p-1.5 ') +
        (umschalten.isError
          ? FEHLGESCHLAGEN
          : markiert
            ? 'border-accent-500 bg-accent-500/15 text-accent-400 hover:bg-accent-500/25'
            : 'border-ink-700 bg-ink-900/80 text-mist-300 hover:border-accent-500 hover:text-accent-400') +
        ' ' +
        className
      }
    >
      <HeartIcon gefuellt={markiert} className={gross ? 'h-5 w-5' : 'h-4 w-4'} />
    </button>
  )
}
