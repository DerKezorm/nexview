import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Favorite, FavoritePerson } from '../api/types'
import { useFavorites, usePersonFavorites } from '../components/media/FavoriteButton'
import { PersonPhoto } from '../components/media/PersonPhoto'
import { Poster } from '../components/media/Poster'
import { Spinner } from '../components/ui'
import { formatDate } from '../lib/format'
import { personPath, titlePath } from '../lib/routes'

type Filter = 'movie' | 'tv' | 'person'
type Ansicht = 'grid' | 'list'

function TrashIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

/** Der kleine Mülleimer zum Herausnehmen - als Auflage oben rechts. */
function EntfernenKnopf({
  onClick,
  laedt,
  label,
  overlay = false,
}: {
  onClick: () => void
  laedt: boolean
  label: string
  overlay?: boolean
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        onClick()
      }}
      disabled={laedt}
      title={label}
      aria-label={label}
      className={
        'shrink-0 rounded-full border p-1.5 transition-colors ' +
        'border-ink-700 text-mist-300 hover:border-bad-500 hover:bg-bad-500 hover:text-white ' +
        (overlay ? 'absolute top-2 right-2 z-10 bg-ink-950/85 backdrop-blur' : 'bg-ink-900')
      }
    >
      {laedt ? <Spinner className="h-4 w-4" /> : <TrashIcon />}
    </button>
  )
}

function useEntfernen(pfad: string) {
  const queryClient = useQueryClient()
  const schluessel = pfad.includes('/people/') ? 'person-favorites' : 'favorites'
  return useMutation({
    mutationFn: () => api.delete(pfad),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [schluessel] })
      // Die kuratierte Liste auf der Startseite hängt direkt daran.
      void queryClient.invalidateQueries({ queryKey: ['home-curated'] })
      void queryClient.invalidateQueries({ queryKey: ['regale'] })
    },
  })
}

// --- Titel (Film/Serie) ----------------------------------------------------

function TitelKachel({ eintrag }: { eintrag: Favorite }) {
  const { t } = useTranslation()
  const entfernen = useEntfernen(`/api/favorites/${eintrag.media_type}/${eintrag.tmdb_id}`)
  return (
    <div className="group relative flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850">
      <Link to={titlePath(eintrag.media_type, eintrag.tmdb_id)} className="flex flex-1 flex-col">
        <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
          <Poster
            url={eintrag.poster_url}
            title={eintrag.title}
            className="h-full w-full transition-transform duration-300 group-hover:scale-105"
          />
        </div>
        <div className="p-3">
          <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{eintrag.title}</h3>
          <p className="mt-0.5 text-xs text-mist-500">
            {t(eintrag.media_type === 'movie' ? 'common.movies' : 'common.series')}
          </p>
        </div>
      </Link>
      <EntfernenKnopf
        onClick={() => entfernen.mutate()}
        laedt={entfernen.isPending}
        label={t('favorites.remove')}
        overlay
      />
    </div>
  )
}

function TitelZeile({ eintrag }: { eintrag: Favorite }) {
  const { t, i18n } = useTranslation()
  const entfernen = useEntfernen(`/api/favorites/${eintrag.media_type}/${eintrag.tmdb_id}`)
  return (
    <div className="flex items-center gap-4 rounded-xl border border-ink-700 bg-ink-850 p-3 transition-colors hover:border-accent-600/60">
      <Link
        to={titlePath(eintrag.media_type, eintrag.tmdb_id)}
        className="flex min-w-0 flex-1 items-center gap-4"
      >
        <div className="aspect-[2/3] w-12 shrink-0 overflow-hidden rounded-lg bg-ink-900 sm:w-16">
          <Poster url={eintrag.poster_url} title={eintrag.title} />
        </div>
        <div className="min-w-0">
          <p className="line-clamp-2 font-semibold break-words">
            {eintrag.title || `#${eintrag.tmdb_id}`}
          </p>
          <p className="text-xs text-mist-600">
            {t(eintrag.media_type === 'movie' ? 'common.movies' : 'common.series')}
            {` · ${t('favorites.since', {
              date: formatDate(eintrag.created_at.slice(0, 10), i18n.language),
            })}`}
          </p>
        </div>
      </Link>
      <EntfernenKnopf
        onClick={() => entfernen.mutate()}
        laedt={entfernen.isPending}
        label={t('favorites.remove')}
      />
    </div>
  )
}

// --- Personen --------------------------------------------------------------

function PersonKachel({ person }: { person: FavoritePerson }) {
  const { t } = useTranslation()
  const entfernen = useEntfernen(`/api/favorites/people/${person.person_id}`)
  return (
    <div className="group relative flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850">
      <Link to={personPath(person.person_id)} className="flex flex-1 flex-col">
        <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
          <PersonPhoto
            url={person.photo_url}
            name={person.name}
            className="transition-transform duration-300 group-hover:scale-105"
          />
        </div>
        <div className="p-3">
          <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{person.name}</h3>
          {person.department && (
            <p className="mt-0.5 text-xs text-mist-500">{person.department}</p>
          )}
        </div>
      </Link>
      <EntfernenKnopf
        onClick={() => entfernen.mutate()}
        laedt={entfernen.isPending}
        label={t('favorites.removePerson')}
        overlay
      />
    </div>
  )
}

function PersonZeile({ person }: { person: FavoritePerson }) {
  const { t } = useTranslation()
  const entfernen = useEntfernen(`/api/favorites/people/${person.person_id}`)
  return (
    <div className="flex items-center gap-4 rounded-xl border border-ink-700 bg-ink-850 p-3 transition-colors hover:border-accent-600/60">
      <Link to={personPath(person.person_id)} className="flex min-w-0 flex-1 items-center gap-4">
        <div className="aspect-[2/3] w-12 shrink-0 overflow-hidden rounded-lg bg-ink-900 sm:w-16">
          <PersonPhoto url={person.photo_url} name={person.name} />
        </div>
        <div className="min-w-0">
          <p className="line-clamp-2 font-semibold break-words">{person.name}</p>
          {person.department && <p className="text-xs text-mist-600">{person.department}</p>}
        </div>
      </Link>
      <EntfernenKnopf
        onClick={() => entfernen.mutate()}
        laedt={entfernen.isPending}
        label={t('favorites.removePerson')}
      />
    </div>
  )
}

// --- Seite -----------------------------------------------------------------

const FILTER: Filter[] = ['movie', 'tv', 'person']
const FILTER_LABEL: Record<Filter, string> = {
  movie: 'favorites.filterMovies',
  tv: 'favorites.filterSeries',
  person: 'favorites.sectionPeople',
}

/**
 * „Mag ich" - die eigenen Markierungen, aufgebaut wie das Entdecken-Raster:
 * oben die Auswahl (Personen / Filme / Serien) und der Umschalter Kacheln /
 * Liste, darunter die Cover. Statt des Wagens ein Mülleimer zum Herausnehmen.
 */
export function FavoritesPage() {
  const { t } = useTranslation()
  const { favorites } = useFavorites()
  const { people } = usePersonFavorites()

  const [filter, setFilter] = useState<Filter>('movie')
  const [ansicht, setAnsicht] = useState<Ansicht>('grid')

  const filme = favorites.filter((f) => f.media_type === 'movie')
  const serien = favorites.filter((f) => f.media_type === 'tv')
  const anzahl: Record<Filter, number> = {
    movie: filme.length,
    tv: serien.length,
    person: people.length,
  }
  const titel = filter === 'movie' ? filme : filter === 'tv' ? serien : []
  const leer = favorites.length === 0 && people.length === 0

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('nav.favorites')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('favorites.intro')}</p>
      </header>

      {leer ? (
        <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center">
          <p className="text-lg font-semibold">{t('home.curatedEmptyTitle')}</p>
          <p className="mx-auto mt-1 max-w-lg text-sm leading-relaxed text-mist-500">
            {t('home.curatedEmptyText')}
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-2" role="group" aria-label={t('nav.favorites')}>
              {FILTER.map((wert) => {
                const aktiv = filter === wert
                return (
                  <button
                    key={wert}
                    type="button"
                    onClick={() => setFilter(wert)}
                    aria-pressed={aktiv}
                    className={
                      'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                      (aktiv
                        ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                        : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
                    }
                  >
                    {t(FILTER_LABEL[wert])}
                    <span className="ml-1.5 text-xs tabular-nums opacity-70">{anzahl[wert]}</span>
                  </button>
                )
              })}
            </div>

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

          {anzahl[filter] === 0 ? (
            <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
              {t(filter === 'person' ? 'favorites.emptyPeople' : 'favorites.emptyTitles')}
            </p>
          ) : ansicht === 'grid' ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
              {filter === 'person'
                ? people.map((p) => <PersonKachel key={p.person_id} person={p} />)
                : titel.map((e) => (
                    <TitelKachel key={`${e.media_type}-${e.tmdb_id}`} eintrag={e} />
                  ))}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {filter === 'person'
                ? people.map((p) => <PersonZeile key={p.person_id} person={p} />)
                : titel.map((e) => (
                    <TitelZeile key={`${e.media_type}-${e.tmdb_id}`} eintrag={e} />
                  ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
