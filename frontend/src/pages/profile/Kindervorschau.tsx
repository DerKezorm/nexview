import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Child, KidsItems, MediaItem } from '../../api/types'
import { Button, ErrorBanner, Spinner } from '../../components/ui'
import { KidsCard } from '../kids/KidsCard'
import { KidsKatalog, KidsTitel } from '../kids/KidsKatalog'
import { KidsHintergrund } from '../kids/KidsHintergrund'
import { KIDS } from '../kids/kidsTheme'

/**
 * „Was würde mein Kind sehen?"
 *
 * Zeigt dem Elternteil **exakt** die Kinderansicht dieses Kindes – dieselbe
 * Komponente, dieselben Regeln, nur eine andere Datenquelle
 * (`/api/children/<id>/preview`) und ohne Wunsch-Knopf.
 *
 * Dass es dieselbe Komponente ist, ist der ganze Punkt: Eine nachgebaute
 * Vorschau würde mit der Zeit von der echten Ansicht abweichen, und dann
 * kontrollierte das Elternteil etwas, das es so gar nicht gibt.
 */
export function Kindervorschau({ kind, onZurueck }: { kind: Child; onZurueck: () => void }) {
  const { t } = useTranslation()
  const [bereich, setBereich] = useState<'entdecken' | 'suchen'>('entdecken')
  const quelle = `/api/children/${kind.id}/preview`
  const name = kind.display_name ?? kind.username

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={onZurueck}>
          ← {t('children.previewBack')}
        </Button>
        <p className="text-sm text-mist-500">{t('children.previewIntro', { name })}</p>
      </div>

      {/* Ein deutlicher Rahmen: Es soll keinen Moment unklar sein, dass man
          gerade nicht die eigene App sieht. */}
      <div className="overflow-hidden rounded-3xl border-2 border-accent-500/40">
        <div className="flex items-center justify-between gap-3 border-b border-accent-500/30 bg-accent-500/10 px-4 py-2.5">
          <p className="text-sm font-semibold text-accent-400">
            {t('children.previewBanner', { name })}
          </p>
          <div className="flex gap-1">
            {(['entdecken', 'suchen'] as const).map((wert) => (
              <button
                key={wert}
                type="button"
                onClick={() => setBereich(wert)}
                className={
                  'rounded-full px-3 py-1 text-xs font-medium transition-colors ' +
                  (bereich === wert
                    ? 'bg-accent-500/25 text-accent-300'
                    : 'text-mist-500 hover:text-mist-200')
                }
              >
                {t(wert === 'entdecken' ? 'kids.navDiscover' : 'kids.navSearch')}
              </button>
            ))}
          </div>
        </div>

        {/* Der Inhalt bekommt den Grund der Kinderansicht - sonst stünde die
            helle App in einem dunklen Kasten und sähe kaputt aus. */}
        <div className="relative p-4" style={{ background: KIDS.seite }}>
          <KidsHintergrund quelle={quelle} />
          <div className="relative">
            {bereich === 'entdecken' ? (
              <KidsKatalog quelle={quelle} vorschau />
            ) : (
              <VorschauSuche quelle={quelle} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Die Suche in der Vorschau.
 *
 * Eigene kleine Fassung statt `KidsSearchPage`: Die springt beim Klick auf
 * eine Route der Kinderansicht, die es im Elternkonto gar nicht gibt.
 */
function VorschauSuche({ quelle }: { quelle: string }) {
  const { t } = useTranslation()
  const [mediaType, setMediaType] = useState<'movie' | 'tv'>('movie')
  const [eingabe, setEingabe] = useState('')
  const [suche, setSuche] = useState('')
  const [titel, setTitel] = useState<number | null>(null)

  const treffer = useQuery({
    queryKey: [quelle, 'search', mediaType, suche],
    queryFn: () =>
      api.get<KidsItems>(
        `${quelle}/search?media_type=${mediaType}&q=${encodeURIComponent(suche)}`,
      ),
    enabled: suche.length >= 2,
  })

  const alle: { item: MediaItem; verfuegbar: boolean }[] = [
    ...(treffer.data?.verfuegbar ?? []).map((item) => ({ item, verfuegbar: true })),
    ...(treffer.data?.wuenschbar ?? []).map((item) => ({ item, verfuegbar: false })),
  ]

  if (titel !== null) {
    return (
      <KidsTitel
        quelle={quelle}
        vorschau
        mediaType={mediaType}
        tmdbId={titel}
        onZurueck={() => setTitel(null)}
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-2">
        {(['movie', 'tv'] as const).map((wert) => (
          <button
            key={wert}
            type="button"
            onClick={() => setMediaType(wert)}
            className={
              'rounded-full px-4 py-2 text-sm font-medium transition-colors ' +
              (mediaType === wert
                ? 'bg-accent-500/20 text-accent-400'
                : 'bg-ink-900 text-mist-500')
            }
          >
            {t(wert === 'movie' ? 'kids.movies' : 'kids.series')}
          </button>
        ))}
      </div>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          setSuche(eingabe.trim())
        }}
      >
        <input
          value={eingabe}
          onChange={(event) => setEingabe(event.target.value)}
          placeholder={t('kids.searchLabel')}
          aria-label={t('kids.searchLabel')}
          className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100"
        />
        <Button type="submit">{t('kids.searchButton')}</Button>
      </form>

      {treffer.isFetching && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}
      {treffer.error && <ErrorBanner message={(treffer.error as Error).message} />}
      {treffer.data && alle.length === 0 && (
        <p className="text-sm text-mist-500">{t('kids.searchEmpty')}</p>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {alle.map(({ item, verfuegbar }) => (
          <KidsCard
            key={`${item.media_type}-${item.tmdb_id}`}
            item={item}
            verfuegbar={verfuegbar}
            onClick={() => setTitel(item.tmdb_id)}
          />
        ))}
      </div>
    </div>
  )
}
