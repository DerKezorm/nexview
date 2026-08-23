import { useTranslation } from 'react-i18next'

import { Umschalter } from '../Umschalter'

export type CalendarDatumsart = 'digital' | 'kino'
export type CalendarSchaerfe = 'sinnvoll' | 'none'
/** Filme, Folgen oder beides - die Frage, die man an eine Woche wirklich hat. */
export type CalendarArt = 'beides' | 'movie' | 'tv'

const ARTEN: readonly CalendarArt[] = ['beides', 'movie', 'tv'] as const
const DATUMSARTEN: readonly CalendarDatumsart[] = ['digital', 'kino'] as const
const SCHAERFEN: readonly CalendarSchaerfe[] = ['sinnvoll', 'none'] as const

type Props = {
  art: CalendarArt
  datumsart: CalendarDatumsart
  schaerfe: CalendarSchaerfe
  onArt: (wert: CalendarArt) => void
  onDatumsart: (wert: CalendarDatumsart) => void
  onSchaerfe: (wert: CalendarSchaerfe) => void
}

/**
 * Die drei Fragen über der Wochenansicht.
 *
 * Die Beschriftungen sind **Fragen**, keine Substantive. Vorher stand dort
 * „AUSWAHL / TERMIN / UMFANG" — drei abstrakte Wörter, aus denen niemand
 * ableiten kann, was der Regler tut.
 *
 * ⚠️ Der erste Regler hieß einmal „Alles / Bereits angefragt". Das war die
 * falsche Achse: Die eigenen Titel stehen ohnehin in jedem Tag oben in einer
 * eigenen Gruppe, der Schalter blendete also nur aus, was darunter kam. Die
 * Frage, die man an eine Woche wirklich hat, ist **Filme oder Folgen** — das
 * sind zwei verschiedene Anlässe („was läuft weiter?" gegen „was ist neu?").
 */
export function CalendarFilters({
  art,
  datumsart,
  schaerfe,
  onArt,
  onDatumsart,
  onSchaerfe,
}: Props) {
  const { t } = useTranslation()
  // Serien laufen nicht im Kino - unter dieser Auswahl gibt es keine Folgen.
  const nurFilme = datumsart === 'kino'

  return (
    // Nebeneinander, nicht untereinander: Drei Zeilen für drei kurze Fragen
    // kosten senkrecht Platz, den die Wochenansicht braucht.
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
      <Umschalter
        wert={nurFilme ? 'movie' : art}
        wahl={ARTEN}
        onChange={onArt}
        beschriftung={t('calendar.filterKind')}
        deaktiviert={nurFilme}
        titel={nurFilme ? t('calendar.kindCinemaHint') : undefined}
        label={(wert) => t(`calendar.kind_${wert}`)}
      />

      <Umschalter
        wert={datumsart}
        wahl={DATUMSARTEN}
        onChange={onDatumsart}
        beschriftung={t('calendar.filterDateType')}
        label={(wert) => t(wert === 'digital' ? 'calendar.dateDigital' : 'calendar.dateCinema')}
      />

      <Umschalter
        wert={schaerfe}
        wahl={SCHAERFEN}
        onChange={onSchaerfe}
        beschriftung={t('calendar.filterNoise')}
        label={(wert) =>
          t(wert === 'sinnvoll' ? 'calendar.noiseSensible' : 'calendar.noiseAll')
        }
      />
    </div>
  )
}
