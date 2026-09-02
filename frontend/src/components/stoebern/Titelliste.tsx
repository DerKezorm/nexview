import { useTranslation } from 'react-i18next'

import type { MediaItem } from '../../api/types'
import { MediaItemCard } from '../media/MediaCard'
import { MediaListRow } from '../media/MediaListRow'
import { useCardData } from '../media/useCardData'
import { Umschalter } from '../Umschalter'

export type Ansicht = 'kacheln' | 'liste'

const ANSICHTEN: readonly Ansicht[] = ['kacheln', 'liste'] as const

/** Der Umschalter allein - er steht bei den übrigen Reglern, nicht über der Liste. */
export function AnsichtUmschalter({
  wert,
  onChange,
}: {
  wert: Ansicht
  onChange: (neu: Ansicht) => void
}) {
  const { t } = useTranslation()
  return (
    <Umschalter
      wert={wert}
      wahl={ANSICHTEN}
      onChange={onChange}
      beschriftung={t('stoebern.ansicht')}
      label={(eintrag) => t(`stoebern.ansicht_${eintrag}`)}
    />
  )
}

/**
 * Titel als Kacheln oder als Liste.
 *
 * Beide Darstellungen an einer Stelle, weil sie sonst auf Regalseite und
 * Filterleiste getrennt altern - genau so ist die Entdecken-Seite an
 * `useCardData` vorbeigelaufen und zeigte dort monatelang keine IMDb-Abzeichen
 * und immer leere Herzen.
 *
 * Die Listenansicht ist keine Kosmetik: Sie zeigt Laufzeit, Altersfreigabe und
 * zwei Zeilen Handlung nebeneinander. Wer zwischen fünf Titeln abwägt, liest
 * das lieber, als fünfmal ein Poster zu überfliegen.
 */
export function Titelliste({
  items,
  ansicht,
  onQuickAdd,
}: {
  items: MediaItem[]
  ansicht: Ansicht
  onQuickAdd: (item: MediaItem) => void
}) {
  const { ratingsFor, istFavorit } = useCardData(items)

  if (ansicht === 'liste') {
    return (
      <div className="flex flex-col gap-3">
        {items.map((item) => (
          <MediaListRow
            key={item.tmdb_id}
            item={item}
            onQuickAdd={onQuickAdd}
            ratings={ratingsFor(item)}
            favorit={istFavorit(item)}
          />
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
      {items.map((item) => (
        <MediaItemCard
          key={item.tmdb_id}
          item={item}
          onQuickAdd={onQuickAdd}
          ratings={ratingsFor(item)}
          favorit={istFavorit(item)}
        />
      ))}
    </div>
  )
}
