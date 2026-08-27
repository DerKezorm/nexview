import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { KidsCategory, KidsItems, KidsWish, MediaDetail, MediaItem, Trailer } from '../../api/types'
import { TrailerModal } from '../../components/media/TrailerModal'
import { KidsCard } from './KidsCard'
import { KIDS, rubrikFarbe, RUBRIK_SYMBOLE } from './kidsTheme'
import { MediaSwitch } from './MediaSwitch'
import { useSaat, ziehen } from '../../lib/zufall'

type Ansicht =
  | { art: 'kategorien' }
  | { art: 'rubrik'; rubrik: string }
  | { art: 'titel'; mediaType: 'movie' | 'tv'; tmdbId: number }

/**
 * Der ganze Kinderkatalog in einer Komponente: Kategorien → Rubrik → Titel.
 *
 * Zwei Dinge stecken bewusst hier und nicht in Routen:
 *
 * 1. Die **Eltern-Vorschau** („Was würde mein Kind sehen?") benutzt exakt
 *    dieselbe Ansicht, nur mit einer anderen Datenquelle und ohne Wunsch-Knopf.
 *    Mit Routen bräuchte es dafür einen zweiten Seitenbaum, der stillschweigend
 *    ausei­nanderläuft – und dann kontrollierte das Elternteil etwas, das es so
 *    gar nicht gibt.
 * 2. Für ein Kind ist der Zurück-Knopf auf dem Schirm wichtiger als der des
 *    Browsers.
 *
 * `quelle` ist der Pfad-Vorsatz: `/api/kids` für das Kind selbst,
 * `/api/children/<id>/preview` für die Eltern.
 */
export function KidsKatalog({
  quelle = '/api/kids',
  vorschau = false,
}: {
  quelle?: string
  vorschau?: boolean
}) {
  const { t } = useTranslation()
  const [mediaType, setMediaType] = useState<'movie' | 'tv'>('movie')
  const [ansicht, setAnsicht] = useState<Ansicht>({ art: 'kategorien' })

  const kategorien = useQuery({
    queryKey: [quelle, 'categories', mediaType],
    queryFn: () => api.get<KidsCategory[]>(`${quelle}/categories?media_type=${mediaType}`),
  })

  /**
   * Je Kachel ein zufälliges Szenenbild – aber kein Bild zweimal.
   *
   * Zufällig **hier** und nicht in der Kachel selbst: Derselbe Film steckt oft
   * in mehreren Rubriken (Toy Story ist Animation *und* Familie), und zwei
   * Kacheln nebeneinander mit demselben Bild sehen nach einem Fehler aus.
   * `useMemo` hält die Wahl fest, solange die Daten stehen – ohne das würfelte
   * jedes Neuzeichnen neu und die Bilder flackerten.
   */
  //
  // ⚠️ Der Würfel fällt in `useSaat` **nach** dem Zeichnen. Hier stand einmal
  // `Math.random()` mitten im `useMemo` - und das hält gerade nicht fest, was
  // der Kommentar darüber verspricht: React darf den gemerkten Wert verwerfen
  // und neu rechnen. Siehe `lib/zufall.ts`.
  const saat = useSaat()
  const bilder = useMemo(() => {
    const vergeben = new Set<string>()
    const wahl: Record<string, string | null> = {}
    let strang = 0
    for (const eintrag of kategorien.data ?? []) {
      const frei = eintrag.bilder.filter((bild) => !vergeben.has(bild))
      // Sind alle schon vergeben, lieber doppelt als leer.
      const auswahl = frei.length > 0 ? frei : eintrag.bilder
      // Je Rubrik ein eigener Strang - sonst zöge jede denselben Wurf.
      const bild = ziehen(auswahl, saat, strang++)
      if (bild) vergeben.add(bild)
      wahl[eintrag.rubrik] = bild
    }
    return wahl
  }, [kategorien.data, saat])

  function wechsle(wert: 'movie' | 'tv') {
    setMediaType(wert)
    // Eine Rubrik, die es bei der anderen Medienart nicht gibt, wäre eine
    // leere Seite – zurück auf die Übersicht.
    setAnsicht({ art: 'kategorien' })
  }

  if (ansicht.art === 'titel') {
    return (
      <KidsTitel
        quelle={quelle}
        vorschau={vorschau}
        mediaType={ansicht.mediaType}
        tmdbId={ansicht.tmdbId}
        onZurueck={() => setAnsicht({ art: 'kategorien' })}
      />
    )
  }

  if (ansicht.art === 'rubrik') {
    return (
      <KidsRubrik
        quelle={quelle}
        mediaType={mediaType}
        rubrik={ansicht.rubrik}
        onZurueck={() => setAnsicht({ art: 'kategorien' })}
        onTitel={(tmdbId) => setAnsicht({ art: 'titel', mediaType, tmdbId })}
      />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <MediaSwitch value={mediaType} onChange={wechsle} />

      {kategorien.isPending && <Laedt />}
      {kategorien.error && <Hinweis text={(kategorien.error as Error).message} />}
      {kategorien.data?.length === 0 && <Hinweis text={t('kids.nothingHere')} />}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {kategorien.data?.map((eintrag) => (
          <KategorieKachel
            key={eintrag.rubrik}
            eintrag={eintrag}
            bild={bilder[eintrag.rubrik] ?? null}
            onClick={() => setAnsicht({ art: 'rubrik', rubrik: eintrag.rubrik })}
          />
        ))}
      </div>
    </div>
  )
}

function Laedt() {
  const { t } = useTranslation()
  return (
    <p
      className="rounded-3xl px-5 py-6 text-center text-base font-medium"
      style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
    >
      {t('common.loading')}
    </p>
  )
}

function Hinweis({ text }: { text: string }) {
  return (
    <p
      className="rounded-3xl px-6 py-12 text-center text-lg font-medium"
      style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
    >
      {text}
    </p>
  )
}

/**
 * Eine Kategorie als große, farbige Fläche.
 *
 * Das Poster liegt gedimmt dahinter: Es sagt einem Kind mehr als das Wort
 * darüber, soll aber nicht mit ihm um die Aufmerksamkeit streiten.
 */
function KategorieKachel({
  eintrag,
  bild,
  onClick,
}: {
  eintrag: KidsCategory
  bild: string | null
  onClick: () => void
}) {
  const { t } = useTranslation()
  const farbe = rubrikFarbe(eintrag.rubrik)

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative flex aspect-[4/3] flex-col justify-end overflow-hidden rounded-[28px] p-4 text-left shadow-lg transition-transform active:scale-95"
      style={{ backgroundColor: farbe }}
    >
      {bild && (
        <img
          src={bild}
          alt=""
          className="absolute inset-0 h-full w-full object-cover opacity-60 transition-transform duration-300 group-hover:scale-105"
        />
      )}
      {/* Der Verlauf sorgt dafür, dass die Schrift auf jedem Poster lesbar
          bleibt - manche sind unten hell, manche dunkel. */}
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(to top, ${farbe} 8%, ${farbe}cc 38%, ${farbe}22 100%)`,
        }}
      />
      <svg
        viewBox="0 0 24 24"
        className="relative mb-2 h-10 w-10 text-white"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d={RUBRIK_SYMBOLE[eintrag.rubrik] ?? RUBRIK_SYMBOLE.animation} />
      </svg>
      <span className="relative text-xl font-extrabold text-white drop-shadow">
        {t(`children.genre.${eintrag.rubrik}`)}
      </span>
    </button>
  )
}

/** Der Zurück-Knopf – groß genug für einen Daumen. */
function Zurueck({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-fit items-center gap-2 rounded-2xl px-4 py-3 text-base font-bold shadow-sm transition-transform active:scale-95"
      style={{ backgroundColor: KIDS.flaeche, color: KIDS.text }}
    >
      <span aria-hidden="true">←</span>
      {t('kids.back')}
    </button>
  )
}

/** Eine Überschrift über einem Kachelraster. */
function Bereich({
  titel,
  farbe,
  items,
  gewuenscht,
  verfuegbar,
  onTitel,
}: {
  titel: string
  farbe: string
  items: MediaItem[]
  gewuenscht: Set<number>
  verfuegbar: boolean
  onTitel: (tmdbId: number) => void
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
            onClick={() => onTitel(item.tmdb_id)}
          />
        ))}
      </div>
    </section>
  )
}

/**
 * Alle Titel einer Rubrik – in zwei Bereichen.
 *
 * Oben, was das Kind **jetzt schon schauen kann**, darunter, was es sich
 * wünschen kann. Beides zu mischen hieße, dass der Wunsch-Knopf mal etwas tut
 * und mal nicht; das Vorhandene wegzulassen hieße, dass ein Kind über Nexview
 * nie findet, was zu Hause längst liegt (gemessen: 90 % der Kacheln).
 */
function KidsRubrik({
  quelle,
  mediaType,
  rubrik,
  onZurueck,
  onTitel,
}: {
  quelle: string
  mediaType: 'movie' | 'tv'
  rubrik: string
  onZurueck: () => void
  onTitel: (tmdbId: number) => void
}) {
  const { t } = useTranslation()
  const [seiten, setSeiten] = useState(1)
  const farbe = rubrikFarbe(rubrik)

  const abfrage = useQuery({
    queryKey: [quelle, 'rubrik', mediaType, rubrik, seiten],
    queryFn: async () => {
      // Alle bisher geholten Seiten zusammen - „Mehr anzeigen" hängt an, statt
      // die Liste auszutauschen.
      const teile = await Promise.all(
        Array.from({ length: seiten }, (_, i) =>
          api.get<KidsItems>(
            `${quelle}/rubrik/${rubrik}?media_type=${mediaType}&page=${i + 1}`,
          ),
        ),
      )
      return {
        verfuegbar: teile.flatMap((teil) => teil.verfuegbar),
        wuenschbar: teile.flatMap((teil) => teil.wuenschbar),
        gewuenscht: new Set(teile.flatMap((teil) => teil.gewuenscht ?? [])),
        nachschub: teile[teile.length - 1].verfuegbar.length +
          teile[teile.length - 1].wuenschbar.length > 0,
      }
    },
  })

  const daten = abfrage.data

  return (
    <div className="flex flex-col gap-5">
      <Zurueck onClick={onZurueck} />

      <h1 className="text-3xl font-extrabold" style={{ color: farbe }}>
        {t(`children.genre.${rubrik}`)}
      </h1>

      {abfrage.isPending && <Laedt />}
      {abfrage.error && <Hinweis text={(abfrage.error as Error).message} />}

      {daten && daten.verfuegbar.length === 0 && daten.wuenschbar.length === 0 && (
        <Hinweis text={t('kids.nothingHere')} />
      )}

      {daten && (
        <>
          <Bereich
            titel={t('kids.sectionAvailable')}
            farbe={KIDS.fertig}
            items={daten.verfuegbar}
            gewuenscht={daten.gewuenscht}
            verfuegbar
            onTitel={onTitel}
          />
          <Bereich
            titel={t('kids.sectionWishable')}
            farbe={KIDS.wunsch}
            items={daten.wuenschbar}
            gewuenscht={daten.gewuenscht}
            verfuegbar={false}
            onTitel={onTitel}
          />
        </>
      )}

      {daten?.nachschub && (
        <button
          type="button"
          onClick={() => setSeiten((wert) => wert + 1)}
          className="mx-auto rounded-3xl px-7 py-4 text-lg font-extrabold text-white shadow-lg transition-transform active:scale-95"
          style={{ backgroundColor: farbe }}
        >
          {t('kids.more')}
        </button>
      )}
    </div>
  )
}

/** Ein Titel – Poster, Beschreibung, ein Knopf. */
export function KidsTitel({
  quelle,
  vorschau,
  mediaType,
  tmdbId,
  onZurueck,
}: {
  quelle: string
  vorschau: boolean
  mediaType: 'movie' | 'tv'
  tmdbId: number
  onZurueck: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [trailer, setTrailer] = useState<Trailer | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

  const titel = useQuery({
    queryKey: [quelle, 'titel', mediaType, tmdbId],
    queryFn: () => api.get<MediaDetail>(`${quelle}/title/${mediaType}/${tmdbId}`),
  })

  const wuensche = useQuery({
    queryKey: ['kids-wishes'],
    queryFn: () => api.get<KidsWish[]>('/api/kids/wishes'),
    enabled: !vorschau,
  })

  const wuenschen = useMutation({
    mutationFn: () =>
      api.post<KidsWish>('/api/kids/wishes', { media_type: mediaType, tmdb_id: tmdbId }),
    onSuccess: () => {
      setFehler(null)
      void queryClient.invalidateQueries({ queryKey: ['kids-wishes'] })
    },
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
  })

  if (titel.isPending) {
    return (
      <div className="flex flex-col gap-5">
        <Zurueck onClick={onZurueck} />
        <Laedt />
      </div>
    )
  }

  if (titel.error || !titel.data) {
    return (
      <div className="flex flex-col gap-5">
        <Zurueck onClick={onZurueck} />
        <Hinweis text={t('kids.titleMissing')} />
      </div>
    )
  }

  const daten = titel.data
  const verfuegbar = daten.status === 'downloaded'
  const vorhandener = (wuensche.data ?? []).find(
    (wunsch) => wunsch.tmdb_id === daten.tmdb_id && wunsch.media_type === daten.media_type,
  )
  const jahr = daten.release_date?.slice(0, 4)

  return (
    <div className="flex flex-col gap-5">
      <Zurueck onClick={onZurueck} />

      <div
        className="flex flex-col items-center gap-5 rounded-[28px] p-5 shadow-lg sm:flex-row sm:items-start"
        style={{ backgroundColor: KIDS.flaeche }}
      >
        <div className="w-44 shrink-0 self-start overflow-hidden rounded-3xl shadow-md">
          {daten.poster_url && (
            <img src={daten.poster_url} alt="" className="aspect-[2/3] w-full object-cover" />
          )}
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div>
            <h1 className="text-3xl leading-tight font-extrabold" style={{ color: KIDS.text }}>
              {daten.title}
            </h1>
            <p className="mt-2 flex flex-wrap items-center gap-2 text-sm font-bold">
              {jahr && (
                <span
                  className="rounded-full px-3 py-1"
                  style={{ backgroundColor: KIDS.flaecheSanft, color: KIDS.textLeise }}
                >
                  {jahr}
                </span>
              )}
              {daten.certification && (
                <span
                  className="rounded-full px-3 py-1 text-white"
                  style={{ backgroundColor: KIDS.fertig }}
                >
                  {t('kids.fromAge', { age: daten.certification })}
                </span>
              )}
            </p>
          </div>

          {daten.overview && (
            <p className="text-lg leading-relaxed" style={{ color: KIDS.text }}>
              {daten.overview}
            </p>
          )}

          {daten.trailer && (
            <button
              type="button"
              onClick={() => setTrailer(daten.trailer!)}
              className="flex w-fit items-center gap-2 rounded-2xl px-5 py-3 text-base font-bold transition-transform active:scale-95"
              style={{ backgroundColor: KIDS.flaecheSanft, color: KIDS.primaer }}
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden="true">
                <path d="M8 5v14l11-7z" />
              </svg>
              {t('kids.trailer')}
            </button>
          )}

          {fehler && (
            <p
              className="rounded-2xl px-4 py-3 text-base font-medium text-white"
              style={{ backgroundColor: KIDS.wunsch }}
            >
              {fehler}
            </p>
          )}

          {verfuegbar ? (
            <div
              className="rounded-3xl px-5 py-5 text-center text-xl font-extrabold text-white"
              style={{ backgroundColor: KIDS.fertig }}
            >
              {t('kids.canWatch')}
            </div>
          ) : vorschau ? (
            <p
              className="rounded-3xl px-5 py-4 text-center text-base font-medium"
              style={{ backgroundColor: KIDS.flaecheSanft, color: KIDS.textLeise }}
            >
              {t('children.previewNoWish')}
            </p>
          ) : vorhandener ? (
            <>
              <div
                className="rounded-3xl px-5 py-5 text-center text-xl font-extrabold text-white"
                style={{ backgroundColor: KIDS.wunsch }}
              >
                {t(`kids.state.${vorhandener.state}`)}
              </div>
              {/* „Mehr davon": Bei Serien darf nach einer erledigten
                  Teil-Freigabe erneut gewünscht werden - die Eltern dosieren
                  ja staffel- oder folgenweise, und ohne diesen Knopf käme das
                  Kind nie wieder an die nächsten Folgen. Der Server erlaubt
                  den neuen Wunsch, sobald kein offener mehr existiert. */}
              {vorhandener.state === 'available' &&
                daten.media_type === 'tv' &&
                !verfuegbar && (
                  <button
                    type="button"
                    onClick={() => wuenschen.mutate()}
                    disabled={wuenschen.isPending}
                    className="rounded-3xl px-6 py-4 text-lg font-extrabold text-white shadow-lg transition-transform active:scale-95 disabled:opacity-60"
                    style={{ backgroundColor: KIDS.primaer }}
                  >
                    {t('kids.wishMoreButton')}
                  </button>
                )}
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => wuenschen.mutate()}
                disabled={wuenschen.isPending}
                className="rounded-3xl px-6 py-5 text-xl font-extrabold text-white shadow-lg transition-transform active:scale-95 disabled:opacity-60"
                style={{ backgroundColor: KIDS.wunsch }}
              >
                {t('kids.wishButton')}
              </button>
              <p
                className="text-center text-base font-medium"
                style={{ color: KIDS.textLeise }}
              >
                {t('kids.wishHint')}
              </p>
            </>
          )}
        </div>
      </div>

      <TrailerModal trailer={trailer} onClose={() => setTrailer(null)} />
    </div>
  )
}
