import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { CalendarResult, MediaItem } from '../api/types'
import { DemoBanner } from '../components/DemoBanner'
import { CalendarDayGroup } from '../components/calendar/CalendarDayGroup'
import type {
  CalendarDatumsart,
  CalendarArt,
  CalendarSchaerfe,
} from '../components/calendar/CalendarFilters'
import { CalendarFilters } from '../components/calendar/CalendarFilters'
import { WeekPicker } from '../components/calendar/WeekPicker'
import { DetailModal } from '../components/media/DetailModal'
import { useCardData } from '../components/media/useCardData'
import { ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import type { Woche } from '../lib/kalenderwoche'
import { heutigeWoche, wochenSpanne } from '../lib/kalenderwoche'

/**
 * Der heutige Tag in der Zeitzone des Browsers.
 *
 * Bewusst nicht `today()` aus `lib/format`: Das rechnet über `toISOString()`
 * und damit in UTC – abends um elf käme dort schon der nächste Tag heraus, und
 * die Zeitleiste markierte den falschen Tag als „Heute“.
 */
function heuteLokal(): string {
  const jetzt = new Date()
  const monat = String(jetzt.getMonth() + 1).padStart(2, '0')
  const tag = String(jetzt.getDate()).padStart(2, '0')
  return `${jetzt.getFullYear()}-${monat}-${tag}`
}

/**
 * Erscheinungs-Kalender – eine Kalenderwoche auf einmal.
 *
 * Zwei Fragen auf einer Zeitleiste: „Läuft meine Serie diese Woche – und ist
 * die Folge schon da?“ und „Was erscheint sonst noch?“ Beides nach Tagen
 * sortiert, beides in der Region des Benutzers.
 */
export function CalendarPage() {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const heute = useMemo(() => heuteLokal(), [])

  const [woche, setWoche] = useState<Woche>(heutigeWoche)
  const [art, setArt] = useState<CalendarArt>('beides')
  const [datumsart, setDatumsart] = useState<CalendarDatumsart>('digital')
  const [schaerfe, setSchaerfe] = useState<CalendarSchaerfe>('sinnvoll')
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const { von, bis } = wochenSpanne(woche)

  const parameter = new URLSearchParams({
    date_from: von,
    date_to: bis,
    // Die eigenen Titel kommen immer mit - die Trennung Film/Folge passiert
    // hier vorn, weil jeder Eintrag seine Medienart ohnehin mitbringt und die
    // Woche eine feste Menge ist (nichts kann dadurch "zu kurz" werden).
    sources: 'all',
    date_type: datumsart,
    noise: schaerfe,
  })

  const query = useQuery({
    queryKey: ['calendar', von, bis, datumsart, schaerfe],
    queryFn: () => api.get<CalendarResult>(`/api/calendar?${parameter.toString()}`),
    staleTime: 5 * 60 * 1000,
  })

  // React Query behält bei einem fehlgeschlagenen Nachladen die alten Daten;
  // dann steht der Fehler nur in `failureReason`.
  const fehler = query.error ?? query.failureReason
  // Filme/Folgen wird hier gesiebt, nicht beim Server: Die Woche ist eine
  // feste Menge, es kann also nichts "zu kurz" werden - anders als bei einer
  // blätternden Liste.
  //
  // ⚠️ Die rohe Liste entsteht **im** useMemo. Draußen wäre `?? []` bei
  // fehlenden Daten in jedem Render ein neues leeres Array, und der Vergleich
  // der Abhängigkeiten schlüge jedes Mal an - gerechnet würde dann immer.
  const tage = useMemo(() => {
    const roheTage = query.data?.days ?? []
    if (art === 'beides') return roheTage
    return roheTage
      .map((tag) => ({ ...tag, entries: tag.entries.filter((e) => e.media_type === art) }))
      .filter((tag) => tag.entries.length > 0)
  }, [query.data, art])

  const alleEintraege = useMemo(() => tage.flatMap((tag) => tag.entries), [tage])
  const { ratingsFor, istFavorit } = useCardData(
    alleEintraege
      .filter((eintrag) => eintrag.tmdb_id)
      .map((eintrag) => ({ media_type: eintrag.media_type, tmdb_id: eintrag.tmdb_id as number })),
  )

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('nav.calendar')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('calendar.intro')}</p>
      </header>

      <DemoBanner />

      <WeekPicker woche={woche} onChange={setWoche} />

      <CalendarFilters
        art={art}
        datumsart={datumsart}
        schaerfe={schaerfe}
        onArt={setArt}
        onDatumsart={setDatumsart}
        onSchaerfe={setSchaerfe}
      />

      {/* Fällt eine Quelle aus, bleibt die andere stehen – der Hinweis erklärt
          die Lücke, statt sie wie „diese Woche kommt nichts“ aussehen zu
          lassen. */}
      {(query.data?.arr_warning || query.data?.tmdb_warning) && (
        <div className="flex flex-col gap-2">
          {query.data.arr_warning && (
            <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {query.data.arr_warning}
            </div>
          )}
          {query.data.tmdb_warning && (
            <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {query.data.tmdb_warning}
            </div>
          )}
        </div>
      )}

      {fehler && (
        <ErrorBanner
          message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
        />
      )}

      {query.isPending && (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-mist-600">
          <Spinner className="h-4 w-4" />
          {t('common.loading')}
        </div>
      )}

      {!query.isPending && !fehler && tage.length === 0 && (
        <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center">
          <p className="text-sm text-mist-500">
            {t('calendar.empty')}
          </p>
        </div>
      )}

      <div className="flex flex-col gap-10">
        {tage.map((tag) => (
          <CalendarDayGroup
            key={tag.date}
            tag={tag}
            heute={heute}
            onQuickAdd={setSelected}
            ratingsFor={ratingsFor}
            istFavorit={istFavorit}
          />
        ))}
      </div>

      <DetailModal
        item={selected}
        onClose={() => setSelected(null)}
        arrConfigured={
          selected?.media_type === 'tv'
            ? (config?.sonarr_configured ?? false)
            : (config?.radarr_configured ?? false)
        }
      />
    </div>
  )
}
