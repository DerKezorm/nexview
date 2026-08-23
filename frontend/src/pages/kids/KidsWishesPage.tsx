import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { KidsWish, KidsWishState } from '../../api/types'
import { KIDS } from './kidsTheme'

/**
 * „Meine Wünsche" – der Grund, warum der Wunsch-Knopf kein schwarzes Loch ist.
 *
 * Vier Zustände statt der acht aus `RequestStatus`. Ein Kind soll nicht lernen,
 * was `pending_approval` von `searching` unterscheidet; es will wissen, ob es
 * den Film sehen kann. Keine Glocke, keine Mail – nur diese Liste.
 */
const FARBEN: Record<KidsWishState, string> = {
  waiting: '#f2a33c',
  coming: KIDS.primaer,
  available: KIDS.fertig,
  declined: '#9aa0b4',
}

export function KidsWishesPage() {
  const { t } = useTranslation()

  const wuensche = useQuery({
    queryKey: ['kids-wishes'],
    queryFn: () => api.get<KidsWish[]>('/api/kids/wishes'),
  })

  if (wuensche.isPending) {
    return (
      <p
        className="rounded-3xl px-5 py-6 text-center text-base font-medium"
        style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
      >
        {t('common.loading')}
      </p>
    )
  }

  const liste = wuensche.data ?? []

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-3xl font-extrabold" style={{ color: KIDS.text }}>
        {t('kids.wishesTitle')}
      </h1>

      {liste.length === 0 && (
        <p
          className="rounded-3xl px-6 py-14 text-center text-lg font-medium"
          style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
        >
          {t('kids.wishesEmpty')}
        </p>
      )}

      {liste.map((wunsch) => (
        <div
          key={wunsch.id}
          className="flex items-center gap-4 rounded-3xl p-3 shadow-md"
          style={{ backgroundColor: KIDS.flaeche }}
        >
          <div
            className="h-28 w-20 shrink-0 overflow-hidden rounded-2xl"
            style={{ backgroundColor: KIDS.flaecheSanft }}
          >
            {wunsch.poster_path && (
              <img src={wunsch.poster_path} alt="" className="h-full w-full object-cover" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-lg font-extrabold" style={{ color: KIDS.text }}>
              {wunsch.title}
            </p>
            <span
              className="mt-2 inline-block rounded-full px-4 py-1.5 text-sm font-bold text-white"
              style={{ backgroundColor: FARBEN[wunsch.state] }}
            >
              {t(`kids.state.${wunsch.state}`)}
            </span>
            {/* Die Begründung steht nur bei einer Absage – und nur, wenn das
                Elternteil eine geschrieben hat. */}
            {wunsch.state === 'declined' && wunsch.decline_note && (
              <p className="mt-2 text-base" style={{ color: KIDS.textLeise }}>
                „{wunsch.decline_note}"
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
