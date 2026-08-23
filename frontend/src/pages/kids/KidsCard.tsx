import { useTranslation } from 'react-i18next'

import type { MediaItem } from '../../api/types'
import { KIDS } from './kidsTheme'

/**
 * Eine Kachel in der Kinderansicht.
 *
 * Bewusst nicht die `MediaCard` der Erwachsenen: Die trägt Zustandsabzeichen,
 * IMDb-Wertung, Auge, Herz und Einkaufswagen – fünf Bedeutungen, die ein Kind
 * erst lernen müsste. Hier gibt es Poster und Titel, und der ganze Klick führt
 * zum Titel mit dem einen Knopf.
 *
 * Zwei Abzeichen gibt es doch, und beide beantworten eine Frage, die ein Kind
 * wirklich hat: „kann ich das jetzt schauen?" (Haken) und „habe ich das schon
 * gefragt?" (Herz).
 */
export function KidsCard({
  item,
  gewuenscht = false,
  verfuegbar = false,
  onClick,
}: {
  item: MediaItem
  gewuenscht?: boolean
  verfuegbar?: boolean
  onClick: () => void
}) {
  const { t } = useTranslation()
  const jahr = item.release_date?.slice(0, 4)

  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex flex-col gap-2 text-left transition-transform active:scale-95"
    >
      <div
        className="relative aspect-[2/3] w-full overflow-hidden rounded-3xl shadow-md"
        style={{ backgroundColor: KIDS.flaecheSanft }}
      >
        {item.poster_url ? (
          <img
            src={item.poster_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
          />
        ) : (
          <div
            className="flex h-full items-center justify-center px-2 text-center text-sm"
            style={{ color: KIDS.textLeise }}
          >
            {item.title}
          </div>
        )}

        {verfuegbar && (
          <span
            className="absolute top-2 left-2 flex h-9 w-9 items-center justify-center rounded-full text-white shadow-lg"
            style={{ backgroundColor: KIDS.fertig }}
            title={t('kids.availableBadge')}
          >
            <svg
              viewBox="0 0 24 24"
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-label={t('kids.availableBadge')}
            >
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
          </span>
        )}

        {gewuenscht && (
          <span
            className="absolute top-2 right-2 flex h-9 w-9 items-center justify-center rounded-full text-white shadow-lg"
            style={{ backgroundColor: KIDS.wunsch }}
            title={t('kids.alreadyWished')}
          >
            <svg
              viewBox="0 0 24 24"
              className="h-5 w-5"
              fill="currentColor"
              aria-label={t('kids.alreadyWished')}
            >
              <path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.6-7 9-7 9z" />
            </svg>
          </span>
        )}
      </div>
      <div>
        <p
          className="line-clamp-2 text-base leading-snug font-bold"
          style={{ color: KIDS.text }}
        >
          {item.title}
        </p>
        {jahr && (
          <p className="text-sm font-medium" style={{ color: KIDS.textLeise }}>
            {jahr}
          </p>
        )}
      </div>
    </button>
  )
}
