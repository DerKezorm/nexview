import { useState } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { Bestandsfilter, FilterSeite, Genre, MediaItem, MediaType } from '../api/types'
import { DetailModal } from '../components/media/DetailModal'
import { Umschalter } from '../components/Umschalter'
import {
  AnsichtUmschalter,
  Titelliste,
  type Ansicht,
} from '../components/stoebern/Titelliste'
import { Button, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { stoeberPath } from '../lib/routes'

const SEITE_ANZAHL = 24

const ZEITEN = ['egal', 'kurz', 'mittel', 'lang'] as const
const BEKANNTHEIT = ['egal', 'bekannt', 'geheimtipp'] as const
const SORTIERUNG = ['rating', 'popular', 'newest'] as const
const BESTAND: Bestandsfilter[] = ['egal', 'nur_vorhanden', 'nur_neu']
const EPOCHEN = ['egal', 'aktuell', '2020', '2010', '2000', '1990', '1980', '1970', 'aelter']

/** Genrenummern aus einem Adress-Parameter lesen. */
function nummern(text: string | null): number[] {
  if (!text) return []
  return text
    .split(',')
    .map((teil) => Number(teil.trim()))
    .filter((zahl) => Number.isInteger(zahl) && zahl > 0)
}

/**
 * Selbst filtern — sechs Fragen statt dreizehn Reglern.
 *
 * Was hier **fehlt**, ist Absicht: Originalsprache, Region, „Nur in DE
 * erschienen", „Ohne Bewertung ausblenden", „Ohne Beschreibung ausblenden",
 * „Nur Spielfilme", „Nur bekannte Titel". Das sind allesamt keine Fragen, die
 * sich ein Mensch stellt, sondern Notbehelfe gegen verrauschte TMDB-Daten.
 * Sie gelten hier stillschweigend — Müll rauszufiltern ist nicht die Aufgabe
 * dessen, der einen Film für heute Abend sucht.
 *
 * Was dagegen **neu** ist: die Laufzeit-Obergrenze und echte Jahrzehnte. Beide
 * gab es auf der Entdecken-Seite nie, und beide sind das, wonach am
 * Filmabend wirklich gefragt wird.
 *
 * Der Zustand liegt in der Adresse, nicht im Bauteil: So lässt sich ein
 * Ergebnis verschicken und wiederfinden. Die alte Seite vergisst jede
 * Einstellung, sobald man sie verlässt.
 */
export function StoeberFilterPage() {
  const { t } = useTranslation()
  const { mediaType } = useParams<{ mediaType: MediaType }>()
  const [params, setParams] = useSearchParams()
  const { data: config } = useConfig()
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const art = (mediaType ?? 'movie') as MediaType

  const zeit = params.get('zeit') ?? 'egal'
  const epoche = params.get('epoche') ?? 'egal'
  const bekanntheit = params.get('bekanntheit') ?? 'egal'
  const sortierung = params.get('sortierung') ?? 'rating'
  const bestand = (params.get('bestand') ?? 'egal') as Bestandsfilter
  // Auch die Darstellung steht in der Adresse - wer eine Liste verschickt,
  // soll beim Empfänger auch eine Liste öffnen.
  const ansicht = (params.get('ansicht') === 'liste' ? 'liste' : 'kacheln') as Ansicht
  const genres = nummern(params.get('genres'))
  const ohneGenres = nummern(params.get('ohne_genres'))

  function setze(schluessel: string, wert: string) {
    const neu = new URLSearchParams(params)
    if (!wert || wert === 'egal') neu.delete(schluessel)
    else neu.set(schluessel, wert)
    setParams(neu, { replace: true })
  }

  /**
   * Ein Genre durchläuft drei Zustände: aus → dabei → ausgeschlossen → aus.
   *
   * Zwei getrennte Listen („Ich mag" und „Heute nicht") wären auf den ersten
   * Blick klarer, brauchten aber die doppelte Fläche — bei zwölf Genres auf
   * dem Handy zwei volle Bildschirme.
   */
  function genreKlick(id: number) {
    const drin = genres.includes(id)
    const raus = ohneGenres.includes(id)
    const neueGenres = genres.filter((g) => g !== id)
    const neueOhne = ohneGenres.filter((g) => g !== id)
    if (!drin && !raus) neueGenres.push(id)
    else if (drin) neueOhne.push(id)

    const neu = new URLSearchParams(params)
    if (neueGenres.length) neu.set('genres', neueGenres.join(','))
    else neu.delete('genres')
    if (neueOhne.length) neu.set('ohne_genres', neueOhne.join(','))
    else neu.delete('ohne_genres')
    setParams(neu, { replace: true })
  }

  const { data: alleGenres = [] } = useQuery({
    queryKey: ['genres', art],
    queryFn: () => api.get<Genre[]>(`/api/genres/${art}`),
    staleTime: 24 * 60 * 60 * 1000,
  })

  const abfrage = new URLSearchParams({
    zeit,
    epoche,
    bekanntheit,
    sortierung,
    bestand,
    anzahl: String(SEITE_ANZAHL),
  })
  if (genres.length) abfrage.set('genres', genres.join(','))
  if (ohneGenres.length) abfrage.set('ohne_genres', ohneGenres.join(','))

  const query = useInfiniteQuery({
    queryKey: ['stoeber-filter', art, abfrage.toString()],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api.get<FilterSeite>(`/api/stoebern/filter/${art}?${abfrage}&page=${pageParam}`),
    getNextPageParam: (letzte) => {
      if (letzte.erschoepft) return undefined
      const naechste = letzte.page + letzte.seiten_durchsucht
      return naechste <= letzte.total_pages ? naechste : undefined
    },
  })

  const items = query.data?.pages.flatMap((seite) => seite.items) ?? []
  const arrWarning = query.data?.pages[0]?.arr_warning ?? null
  const failure = query.error ?? query.failureReason
  const etwasGesetzt = [...params.keys()].length > 0

  const arrConfigured =
    art === 'movie' ? (config?.radarr_configured ?? false) : (config?.sonarr_configured ?? false)

  /**
   * Welche Einstellungen schränken gerade ein — und wie wird man sie los?
   *
   * Wird nur im leeren Zustand gebraucht, aber hier gebaut, weil nur hier
   * bekannt ist, wie jede einzelne zurückgesetzt wird.
   */
  const gesetzteFilter: { schluessel: string; name: string; entfernen: () => void }[] = []
  if (zeit !== 'egal') {
    gesetzteFilter.push({
      schluessel: 'zeit',
      name: t('stoebern.filter.zeit'),
      entfernen: () => setze('zeit', ''),
    })
  }
  if (epoche !== 'egal') {
    gesetzteFilter.push({
      schluessel: 'epoche',
      name: t('stoebern.filter.epoche'),
      entfernen: () => setze('epoche', ''),
    })
  }
  if (genres.length > 0 || ohneGenres.length > 0) {
    gesetzteFilter.push({
      schluessel: 'genres',
      name: t('stoebern.filter.genres'),
      entfernen: () => {
        const neu = new URLSearchParams(params)
        neu.delete('genres')
        neu.delete('ohne_genres')
        setParams(neu, { replace: true })
      },
    })
  }
  if (bekanntheit !== 'egal') {
    gesetzteFilter.push({
      schluessel: 'bekanntheit',
      name: t('stoebern.filter.bekanntheit'),
      entfernen: () => setze('bekanntheit', ''),
    })
  }
  if (bestand !== 'egal') {
    gesetzteFilter.push({
      schluessel: 'bestand',
      name: t(`stoebern.bestand.${bestand}`),
      entfernen: () => setze('bestand', ''),
    })
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <Link
          to={stoeberPath(art)}
          className="text-sm font-semibold text-mist-500 transition-colors hover:text-mist-300"
        >
          ← {t('stoebern.titel')}
        </Link>
        <h1 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
          {t('stoebern.filter.titel')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('stoebern.filter.intro')}</p>
      </header>

      <div className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-850/40 p-4 sm:p-5">
        <Umschalter
          wert={zeit}
          wahl={ZEITEN}
          onChange={(neu) => setze('zeit', neu)}
          beschriftung={t('stoebern.filter.zeit')}
          label={(eintrag) => t(`stoebern.filter.zeit_${eintrag}`)}
        />

        <Umschalter
          wert={epoche}
          wahl={EPOCHEN}
          onChange={(neu) => setze('epoche', neu)}
          beschriftung={t('stoebern.filter.epoche')}
          label={(eintrag) =>
            /^\d+$/.test(eintrag)
              ? t('stoebern.filter.jahrzehnt', { jahr: eintrag })
              : t(`stoebern.filter.epoche_${eintrag}`)
          }
        />

        <div className="flex flex-col gap-2">
          <span className="text-sm text-mist-500">{t('stoebern.filter.genres')}</span>
          <div className="flex flex-wrap gap-2">
            {alleGenres.map((genre) => {
              const drin = genres.includes(genre.id)
              const raus = ohneGenres.includes(genre.id)
              return (
                <button
                  key={genre.id}
                  type="button"
                  aria-pressed={drin}
                  onClick={() => genreKlick(genre.id)}
                  className={`rounded-full border px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                    drin
                      ? 'border-accent-500 bg-accent-500 text-white'
                      : raus
                        ? 'border-warn-500/60 bg-warn-500/10 text-warn-500 line-through'
                        : 'border-ink-700 bg-ink-850 text-mist-500 hover:text-mist-300'
                  }`}
                >
                  {genre.name}
                </button>
              )
            })}
          </div>
          <p className="text-xs text-mist-600">{t('stoebern.filter.genreHinweis')}</p>
        </div>

        <Umschalter
          wert={bekanntheit}
          wahl={BEKANNTHEIT}
          onChange={(neu) => setze('bekanntheit', neu)}
          beschriftung={t('stoebern.filter.bekanntheit')}
          label={(eintrag) => t(`stoebern.filter.bekanntheit_${eintrag}`)}
        />

        <Umschalter
          wert={bestand}
          wahl={BESTAND}
          onChange={(neu) => setze('bestand', neu)}
          beschriftung={t('stoebern.bestand.frage')}
          label={(eintrag) => t(`stoebern.bestand.${eintrag}`)}
        />

        <Umschalter
          wert={sortierung}
          wahl={SORTIERUNG}
          onChange={(neu) => setze('sortierung', neu)}
          beschriftung={t('stoebern.filter.sortierung')}
          label={(eintrag) => t(`stoebern.filter.sortierung_${eintrag}`)}
        />


        {etwasGesetzt && (
          <div>
            <Button
              variant="ghost"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
              className="!px-4 !py-2"
            >
              {t('stoebern.filter.zuruecksetzen')}
            </Button>
          </div>
        )}
      </div>

      {arrWarning && (
        <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {arrWarning}
        </div>
      )}

      {failure && (
        <ErrorBanner
          message={failure instanceof ApiError ? failure.message : t('errors.generic')}
        />
      )}

      {!query.isPending && !failure && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-700 px-4 py-10 text-center">
          {/*
            ⚠️ Nicht raten, welche Einstellung schuld ist.
            Die erste Fassung riet pauschal „am ehesten das Jahrzehnt oder die
            Genres" — und stand dann bei jemandem, der gar kein Jahrzehnt
            gesetzt hatte. Eine leere Liste ohne Grund sieht aus wie ein
            Defekt; ein *falsch* geratener Grund ist schlimmer.
            Deshalb: genau die Einstellungen anbieten, die gerade gesetzt sind,
            jede einzeln wegklickbar.
          */}
          <p className="text-sm text-mist-500">{t('stoebern.filter.leer')}</p>

          {gesetzteFilter.length > 0 && (
            <>
              <p className="mt-4 text-xs text-mist-600">
                {t('stoebern.filter.leerHinweis')}
              </p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {gesetzteFilter.map((eintrag) => (
                  <button
                    key={eintrag.schluessel}
                    type="button"
                    onClick={eintrag.entfernen}
                    className="rounded-full border border-ink-700 bg-ink-850 px-3.5 py-1.5 text-sm font-semibold text-mist-300 transition-colors hover:border-accent-500/60 hover:text-mist-100"
                  >
                    {t('stoebern.filter.ohne', { was: eintrag.name })}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Die Darstellung gehört nicht in die Filterkarte: Sie schränkt nichts
          ein, sondern bestimmt nur, wie das Ergebnis aussieht. Zwischen
          Reglern versteckt findet sie niemand. */}
      {items.length > 0 && (
        <div className="flex justify-end">
          <AnsichtUmschalter
            wert={ansicht}
            onChange={(neu) => setze('ansicht', neu === 'liste' ? 'liste' : '')}
          />
        </div>
      )}

      {items.length > 0 && (
        <Titelliste items={items} ansicht={ansicht} onQuickAdd={setSelected} />
      )}

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="ghost"
            onClick={() => void query.fetchNextPage()}
            loading={query.isFetchingNextPage}
          >
            {t('discover.loadMore')}
          </Button>
        </div>
      )}

      {query.isFetching && !query.isFetchingNextPage && (
        <p className="flex items-center justify-center gap-2 text-xs text-mist-600">
          <Spinner className="h-3 w-3" />
          {t('common.loading')}
        </p>
      )}

      <DetailModal
        item={selected}
        onClose={() => setSelected(null)}
        arrConfigured={arrConfigured}
      />
    </div>
  )
}
