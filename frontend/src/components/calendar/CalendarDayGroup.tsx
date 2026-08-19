import { useTranslation } from 'react-i18next'

import type { CalendarDay, CalendarEntry, MediaItem, MovieRatings } from '../../api/types'
import { fromCalendarEntry } from '../media/cardItem'
import { MediaCard } from '../media/MediaCard'

type Props = {
  tag: CalendarDay
  heute: string
  onQuickAdd: (item: MediaItem) => void
  ratingsFor: (item: { tmdb_id: number }) => MovieRatings | undefined
  istFavorit: (item: { media_type: string; tmdb_id: number }) => boolean
}

/**
 * Überschrift eines Tages.
 *
 * Das `T12:00:00` ist kein Zierrat: `new Date('2026-08-19')` liest JavaScript
 * als UTC-Mitternacht. In allen Zeitzonen westlich von Greenwich stünde dann
 * der Vortag da. Mittags ist der Tag in jeder Zone derselbe.
 */
function ueberschrift(iso: string, heute: string, t: (k: string) => string, locale: string) {
  const tagAb = (versatz: number) => {
    const basis = new Date(heute + 'T12:00:00')
    basis.setDate(basis.getDate() + versatz)
    return basis.toISOString().slice(0, 10)
  }

  if (iso === heute) return t('calendar.today')
  if (iso === tagAb(1)) return t('calendar.tomorrow')
  if (iso === tagAb(-1)) return t('calendar.yesterday')

  return new Date(iso + 'T12:00:00').toLocaleDateString(locale, {
    weekday: 'long',
    day: '2-digit',
    month: 'long',
  })
}

/** Das „fehlt noch“-Schild – dieselbe Bildsprache wie „erscheint am“. */
function FehltSchild({ eintrag }: { eintrag: CalendarEntry }) {
  const { t } = useTranslation()
  const anzahl = eintrag.missing_episodes.length

  return (
    <span className="rounded-md bg-warn-500 px-1.5 py-0.5 text-[11px] font-semibold text-ink-950">
      {anzahl > 1 ? t('calendar.missingCount', { count: anzahl }) : t('calendar.missing')}
    </span>
  )
}

function Block({
  titel,
  hinweis,
  eintraege,
  onQuickAdd,
  ratingsFor,
  istFavorit,
}: {
  titel: string
  hinweis: string
  eintraege: CalendarEntry[]
} & Pick<Props, 'onQuickAdd' | 'ratingsFor' | 'istFavorit'>) {
  if (eintraege.length === 0) return null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-ink-700 pb-2">
        <h3 className="text-xs font-medium tracking-wide text-mist-500 uppercase">{titel}</h3>
        <span className="text-xs text-mist-600">{eintraege.length}</span>
        {/* Ohne diesen Halbsatz war unklar, was die beiden Blöcke unterscheidet
            – „meine“ klingt nach Anfragen, gemeint ist der eigene Bestand. */}
        <span className="text-xs text-mist-700">{hinweis}</span>
      </div>

      {/* Wörtlich dasselbe Raster wie auf Entdecken – die Kacheln sollen sich
          zwischen den Seiten nicht in der Größe unterscheiden. */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
        {eintraege.map((eintrag) => {
          const karte = fromCalendarEntry(eintrag)
          return (
            <div
              key={eintrag.key}
              className={eintrag.missing ? 'rounded-xl ring-2 ring-warn-500/50' : undefined}
            >
              <MediaCard
                item={karte}
                onQuickAdd={onQuickAdd}
                ratings={ratingsFor(karte)}
                favorit={istFavorit(karte)}
                badge={eintrag.missing ? <FehltSchild eintrag={eintrag} /> : undefined}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * Ein Tag auf der Zeitleiste.
 *
 * Die beiden Herkünfte stehen als eigene Blöcke untereinander, nicht als
 * Farbe auf der Kachel: So ist die Unterscheidung lesbar, funktioniert auf
 * dem Telefon und beim Vorlesen – und der Filter „nur meine“ blendet sichtbar
 * einen ganzen Block aus, statt die Kacheln unerklärt zu verändern.
 */
export function CalendarDayGroup({ tag, heute, onQuickAdd, ratingsFor, istFavorit }: Props) {
  const { t, i18n } = useTranslation()
  const meine = tag.entries.filter((e) => e.source === 'meine')
  const neu = tag.entries.filter((e) => e.source === 'neu')
  const istHeute = tag.date === heute

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2
          className={
            'text-lg font-semibold ' + (istHeute ? 'text-accent-400' : 'text-mist-200')
          }
        >
          {ueberschrift(tag.date, heute, t, i18n.language)}
        </h2>
        {!istHeute && (
          <span className="text-xs text-mist-600">
            {new Date(tag.date + 'T12:00:00').toLocaleDateString(i18n.language)}
          </span>
        )}
      </div>

      <div className="flex flex-col gap-5 border-l-2 border-ink-700 pl-4 sm:pl-6">
        <Block
          titel={t('calendar.blockMine')}
          hinweis={t('calendar.blockMineHint')}
          eintraege={meine}
          onQuickAdd={onQuickAdd}
          ratingsFor={ratingsFor}
          istFavorit={istFavorit}
        />
        <Block
          titel={t('calendar.blockNew')}
          hinweis={t('calendar.blockNewHint')}
          eintraege={neu}
          onQuickAdd={onQuickAdd}
          ratingsFor={ratingsFor}
          istFavorit={istFavorit}
        />
      </div>
    </section>
  )
}
