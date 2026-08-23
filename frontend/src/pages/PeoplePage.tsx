import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { PersonDepartment, PersonSummary } from '../api/types'
import {
  FavoritePersonButton,
  usePersonFavorites,
} from '../components/media/FavoriteButton'
import { PersonPhoto } from '../components/media/PersonPhoto'
import { SearchInput } from '../components/SearchInput'
import { Umschalter } from '../components/Umschalter'
import { Button, Spinner } from '../components/ui'
import { personPath } from '../lib/routes'

type PeopleResult = { items: PersonSummary[]; has_more: boolean }

const FAECHER: PersonDepartment[] = ['acting', 'directing', 'writing']

function PersonKachel({ person, markiert }: { person: PersonSummary; markiert: boolean }) {
  return (
    <Link
      to={personPath(person.person_id)}
      className="group flex flex-col text-left"
    >
      <div className="relative aspect-2/3 w-full overflow-hidden rounded-xl border border-ink-700 bg-ink-900">
        <PersonPhoto
          url={person.photo_url}
          name={person.name}
          className="transition-transform duration-300 group-hover:scale-105"
        />
        <FavoritePersonButton
          person={person}
          markiert={markiert}
          className="absolute top-2 right-2"
        />
      </div>
      <p className="mt-2 line-clamp-2 text-sm leading-snug font-semibold group-hover:text-accent-400">
        {person.name}
      </p>
      {person.known_for && (
        <p className="line-clamp-2 text-xs text-mist-600">{person.known_for}</p>
      )}
    </Link>
  )
}

/**
 * Personen entdecken: beliebte Schauspieler zum Stöbern, dazu eine Suche, die
 * sich mit den drei Knöpfen aufs Fach eingrenzen lässt.
 *
 * Warum die Knöpfe vor allem die Suche filtern: TMDB kennt keine Liste
 * „beliebte Regisseure" (gemessen 1 unter 100), die Übersicht trägt also nur
 * die Schauspielerei. Regie und Drehbuch findet man über den Namen.
 */
export function PeoplePage() {
  const { t } = useTranslation()
  const [suche, setSuche] = useState('')
  const [fach, setFach] = useState<PersonDepartment>('acting')
  const { markiert } = usePersonFavorites()

  const query = useInfiniteQuery({
    queryKey: ['people', fach, suche],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<PeopleResult>(
        `/api/people?department=${fach}&q=${encodeURIComponent(suche.trim())}&page=${pageParam}`,
      ),
    getNextPageParam: (letzte, alle) => (letzte.has_more ? alle.length + 1 : undefined),
    staleTime: 10 * 60 * 1000,
  })

  const personen = query.data?.pages.flatMap((seite) => seite.items) ?? []
  const sucht = suche.trim().length > 0
  // Ohne Suche haben Regie/Drehbuch praktisch keine Übersicht - dann steht dort
  // der Hinweis, dass der Weg über die Suche führt, statt einer leeren Fläche.
  const nurUeberSuche = !sucht && fach !== 'acting'

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('people.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('people.intro')}</p>
      </header>

      <SearchInput value={suche} onChange={setSuche} placeholder={t('people.searchPlaceholder')} />

      <Umschalter
        wert={fach}
        wahl={FAECHER}
        onChange={setFach}
        label={(wert) => t(`people.dept.${wert}`)}
      />

      {query.isPending ? (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      ) : nurUeberSuche && personen.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {t('people.searchHint')}
        </p>
      ) : personen.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {sucht ? t('people.noResults') : t('people.empty')}
        </p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-4 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
            {personen.map((person) => (
              <PersonKachel
                key={person.person_id}
                person={person}
                markiert={markiert.has(person.person_id)}
              />
            ))}
          </div>

          {query.hasNextPage && (
            <div className="mt-2 flex justify-center">
              <Button
                type="button"
                variant="ghost"
                onClick={() => void query.fetchNextPage()}
                loading={query.isFetchingNextPage}
              >
                {t('discover.loadMore')}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
