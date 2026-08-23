import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { KidsItems, MediaItem } from '../../api/types'
import { KidsCard } from './KidsCard'
import { KIDS } from './kidsTheme'
import { MediaSwitch } from './MediaSwitch'

/**
 * Suche in der Kinderansicht.
 *
 * Sie durchsucht **nur die freigeschalteten Rubriken** – so vom Nutzer
 * entschieden, und durchgesetzt wird es im Backend, nicht hier.
 *
 * Findet sie nichts, steht das ausdrücklich da („das gibt es hier nicht, frag
 * deine Eltern") statt eines anonymen „keine Treffer". Sonst tippt ein Kind
 * denselben Titel dreimal und glaubt, es habe sich verschrieben.
 */
export function KidsSearchPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [mediaType, setMediaType] = useState<'movie' | 'tv'>('movie')
  const [eingabe, setEingabe] = useState('')
  const [suche, setSuche] = useState('')

  const treffer = useQuery({
    queryKey: ['kids-search', mediaType, suche],
    queryFn: () =>
      api.get<KidsItems>(
        `/api/kids/search?media_type=${mediaType}&q=${encodeURIComponent(suche)}`,
      ),
    enabled: suche.length >= 2,
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setSuche(eingabe.trim())
  }

  const daten = treffer.data
  const gewuenscht = new Set(daten?.gewuenscht ?? [])
  const leer = daten !== undefined && daten.verfuegbar.length + daten.wuenschbar.length === 0

  return (
    <div className="flex flex-col gap-6">
      <MediaSwitch value={mediaType} onChange={setMediaType} />

      <form className="flex gap-3" onSubmit={absenden}>
        <input
          value={eingabe}
          onChange={(event) => setEingabe(event.target.value)}
          placeholder={t('kids.searchLabel')}
          aria-label={t('kids.searchLabel')}
          autoComplete="off"
          className="min-w-0 flex-1 rounded-3xl px-5 py-4 text-lg font-medium shadow-sm outline-none"
          style={{ backgroundColor: KIDS.flaeche, color: KIDS.text }}
        />
        <button
          type="submit"
          className="rounded-3xl px-6 py-4 text-lg font-extrabold text-white shadow-lg transition-transform active:scale-95"
          style={{ backgroundColor: KIDS.primaer }}
        >
          {t('kids.searchButton')}
        </button>
      </form>

      {treffer.isFetching && (
        <p
          className="rounded-3xl px-5 py-6 text-center text-base font-medium"
          style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
        >
          {t('common.loading')}
        </p>
      )}

      {leer && (
        <p
          className="rounded-3xl px-6 py-12 text-center text-lg font-medium"
          style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
        >
          {t('kids.searchEmpty')}
        </p>
      )}

      <Treffer
        titel={t('kids.sectionAvailable')}
        farbe={KIDS.fertig}
        items={daten?.verfuegbar ?? []}
        verfuegbar
        gewuenscht={gewuenscht}
        onTitel={(item) => navigate(`/titel/${item.media_type}/${item.tmdb_id}`)}
      />
      <Treffer
        titel={t('kids.sectionWishable')}
        farbe={KIDS.wunsch}
        items={daten?.wuenschbar ?? []}
        verfuegbar={false}
        gewuenscht={gewuenscht}
        onTitel={(item) => navigate(`/titel/${item.media_type}/${item.tmdb_id}`)}
      />
    </div>
  )
}

function Treffer({
  titel,
  farbe,
  items,
  verfuegbar,
  gewuenscht,
  onTitel,
}: {
  titel: string
  farbe: string
  items: MediaItem[]
  verfuegbar: boolean
  gewuenscht: Set<number>
  onTitel: (item: MediaItem) => void
}) {
  if (items.length === 0) return null

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-xl font-extrabold" style={{ color: farbe }}>
        {titel}
      </h2>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((item) => (
          <KidsCard
            key={`${item.media_type}-${item.tmdb_id}`}
            item={item}
            verfuegbar={verfuegbar}
            gewuenscht={gewuenscht.has(item.tmdb_id)}
            onClick={() => onTitel(item)}
          />
        ))}
      </div>
    </section>
  )
}
