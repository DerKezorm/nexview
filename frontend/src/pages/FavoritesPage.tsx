import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Favorite } from '../api/types'
import { useFavorites } from '../components/media/FavoriteButton'
import { Spinner } from '../components/ui'
import { formatDate } from '../lib/format'
import { titlePath } from '../lib/routes'

/** Eine Zeile der Liste: Cover, Titel, und der Knopf zum Herausnehmen. */
function Zeile({ eintrag }: { eintrag: Favorite }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const entfernen = useMutation({
    mutationFn: () => api.delete(`/api/favorites/${eintrag.media_type}/${eintrag.tmdb_id}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['favorites'] })
      // Die kuratierte Liste auf der Startseite hängt direkt daran.
      void queryClient.invalidateQueries({ queryKey: ['home-curated'] })
    },
  })

  return (
    <div className="flex items-center gap-4 rounded-xl border border-ink-700 bg-ink-850 p-3 transition-colors hover:border-accent-600/60">
      <Link
        to={titlePath(eintrag.media_type, eintrag.tmdb_id)}
        className="flex min-w-0 flex-1 items-center gap-4"
      >
        <div className="aspect-[2/3] w-12 shrink-0 overflow-hidden rounded-lg bg-ink-900 sm:w-16">
          {eintrag.poster_url ? (
            <img src={eintrag.poster_url} alt="" loading="lazy" className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full w-full items-center justify-center px-1 text-center text-[10px] text-mist-600">
              <span className="w-full break-words">{eintrag.title}</span>
            </span>
          )}
        </div>

        <div className="min-w-0">
          {/* line-clamp statt truncate: zwei Zeilen Titel sind lesbar,
              eine abgeschnittene Silbe nicht. */}
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

      <button
        type="button"
        onClick={() => entfernen.mutate()}
        disabled={entfernen.isPending}
        className="shrink-0 rounded-full border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-400 transition-colors hover:border-accent-600 hover:text-accent-400"
      >
        {entfernen.isPending ? <Spinner /> : t('favorites.remove')}
      </button>
    </div>
  )
}

/**
 * „Mag ich" - die eigenen Markierungen.
 *
 * Der Menüeintrag dorthin erscheint erst, wenn es etwas zu sehen gibt. Wer
 * die Adresse trotzdem direkt aufruft, bekommt hier den Hinweis, wozu die
 * Markierungen gut sind.
 */
export function FavoritesPage() {
  const { t } = useTranslation()
  const { favorites } = useFavorites()

  const filme = favorites.filter((f) => f.media_type === 'movie')
  const serien = favorites.filter((f) => f.media_type === 'tv')

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('nav.favorites')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('favorites.intro')}</p>
      </header>

      {favorites.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center">
          <p className="text-lg font-semibold">{t('home.curatedEmptyTitle')}</p>
          <p className="mx-auto mt-1 max-w-lg text-sm leading-relaxed text-mist-500">
            {t('home.curatedEmptyText')}
          </p>
        </div>
      ) : (
        <>
          {filme.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold tracking-wide text-mist-500 uppercase">
                {t('common.movies')}
              </h2>
              <div className="flex flex-col gap-2">
                {filme.map((eintrag) => (
                  <Zeile key={`${eintrag.media_type}-${eintrag.tmdb_id}`} eintrag={eintrag} />
                ))}
              </div>
            </section>
          )}

          {serien.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold tracking-wide text-mist-500 uppercase">
                {t('common.series')}
              </h2>
              <div className="flex flex-col gap-2">
                {serien.map((eintrag) => (
                  <Zeile key={`${eintrag.media_type}-${eintrag.tmdb_id}`} eintrag={eintrag} />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
