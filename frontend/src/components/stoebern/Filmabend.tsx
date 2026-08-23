import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type {
  FilmabendFrage,
  FilmabendStapel,
  MediaItem,
  MediaType,
} from '../../api/types'
import { Button, ErrorBanner, Spinner } from '../ui'
import { MediaItemCard } from '../media/MediaCard'
import { useCardData } from '../media/useCardData'
import { Umschalter } from '../Umschalter'

type FilmabendProps = {
  mediaType: MediaType
  onMediaTypeChange: (neu: MediaType) => void
  onClose: () => void
  onQuickAdd: (item: MediaItem) => void
}

type Antworten = Record<string, string>

/**
 * Welche Antworten eine Frage angesichts der bisherigen noch anbietet.
 *
 * Spiegelt ``filmabend.verfuegbare_antworten`` im Server. Die Regeln kommen
 * dabei **vom Server** (`entfaellt_wenn` am Fragebaum) - hier steht nur, wie
 * sie angewandt werden. Der Server prüft dieselbe Bedingung noch einmal; wer
 * eine ausgeblendete Antwort trotzdem schickt, bekommt eine 422.
 */
function verfuegbareAntworten(frage: FilmabendFrage, antworten: Antworten): string[] {
  const regeln = frage.antworten_entfallen_wenn ?? {}
  return frage.antworten.filter((antwort) => {
    const bedingungen = regeln[antwort]
    if (!bedingungen) return true
    return !Object.entries(bedingungen).some(([vorher, ausloeser]) =>
      ausloeser.includes(antworten[vorher] ?? ''),
    )
  })
}

/** Entfällt diese Frage angesichts der bisherigen Antworten? */
function entfaellt(frage: FilmabendFrage, antworten: Antworten): boolean {
  return Object.entries(frage.entfaellt_wenn ?? {}).some(([vorher, ausloeser]) =>
    ausloeser.includes(antworten[vorher] ?? ''),
  )
}

/**
 * „Keine Ahnung, was ihr gucken sollt?"
 *
 * Ein paar Fragen statt dreizehn Regler - und vor allem: Fragen nach dem, was
 * den Abend wirklich bestimmt. Wer mitschaut, wie viel Zeit ist, ob es sofort
 * laufen muss. Nicht nach Originalsprache und Mindeststimmenzahl.
 *
 * Der Fragebaum ist **adaptiv**: Je nach Antwort verschwinden spätere Fragen
 * ganz (bei „muss sofort laufen" ist „Geheimtipp?" sinnlos) und einzelne
 * Antworten fallen weg (mit Kindern gibt es kein „zum Gruseln"). Die Regeln
 * dafür stehen im Server und reisen mit dem Baum mit - sonst liefen sie
 * auseinander.
 */
export function Filmabend({
  mediaType,
  onMediaTypeChange,
  onClose,
  onQuickAdd,
}: FilmabendProps) {
  const { t } = useTranslation()
  const [antworten, setAntworten] = useState<Antworten>({})
  const [runde, setRunde] = useState(0)
  const [fertig, setFertig] = useState(false)

  const fragenQuery = useQuery({
    queryKey: ['filmabend-fragen', mediaType],
    queryFn: () =>
      api.get<FilmabendFrage[]>(`/api/stoebern/filmabend/fragen/${mediaType}`),
    // ⚠️ Kein `Infinity`: Der Baum hängt am eigenen Sehverlauf — „lange nicht
    // gesehen" fehlt, solange nichts lange genug her ist. Der Verlauf wächst
    // stündlich nach, also darf die Antwort nicht ewig stehen bleiben.
    staleTime: 5 * 60 * 1000,
  })

  const fragen = useMemo(() => fragenQuery.data ?? [], [fragenQuery.data])

  /** Die Fragen, die auf diesem Weg tatsächlich gestellt werden. */
  const weg = useMemo(() => {
    const gestellt: FilmabendFrage[] = []
    const bisher: Antworten = {}
    for (const frage of fragen) {
      if (entfaellt(frage, bisher)) continue
      gestellt.push(frage)
      const gewaehlt = antworten[frage.kennung]
      if (gewaehlt === undefined) break
      bisher[frage.kennung] = gewaehlt
    }
    return gestellt
  }, [fragen, antworten])

  const aktuelle = weg.find((frage) => antworten[frage.kennung] === undefined)

  const stapel = useMutation({
    mutationFn: (naechsteRunde: number) =>
      api.post<FilmabendStapel>(`/api/stoebern/filmabend/ergebnis/${mediaType}`, {
        antworten,
        runde: naechsteRunde,
      }),
  })

  // Sobald keine Frage mehr offen ist, das Ergebnis holen.
  useEffect(() => {
    if (fertig && !stapel.data && !stapel.isPending) stapel.mutate(runde)
    // stapel absichtlich nicht in den Abhängigkeiten - sonst liefe es endlos.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fertig])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  function antworte(frage: string, antwort: string) {
    // Alles, was nach dieser Frage kam, verwirft die neue Antwort - sonst
    // bliebe eine Antwort auf eine Frage stehen, die es jetzt nicht mehr gibt.
    const index = fragen.findIndex((f) => f.kennung === frage)
    const behalten: Antworten = {}
    for (const [kennung, wert] of Object.entries(antworten)) {
      if (fragen.findIndex((f) => f.kennung === kennung) < index) behalten[kennung] = wert
    }
    const neu = { ...behalten, [frage]: antwort }
    setAntworten(neu)
    stapel.reset()
    setRunde(0)

    // Ist damit alles beantwortet? Der Rest des Baums wird dafür durchgespielt.
    const offen = fragen.some((f) => !entfaellt(f, neu) && neu[f.kennung] === undefined)
    setFertig(!offen)
  }

  function zurueck() {
    const letzte = weg[weg.length - (aktuelle ? 2 : 1)]
    if (!letzte) return
    const behalten: Antworten = { ...antworten }
    delete behalten[letzte.kennung]
    for (const frage of fragen) {
      if (fragen.indexOf(frage) > fragen.indexOf(letzte)) delete behalten[frage.kennung]
    }
    setAntworten(behalten)
    setFertig(false)
    stapel.reset()
    setRunde(0)
  }

  function nochmal() {
    const naechste = runde + 1
    setRunde(naechste)
    stapel.mutate(naechste)
  }

  function vonVorn() {
    setAntworten({})
    setFertig(false)
    setRunde(0)
    stapel.reset()
  }

  /**
   * Wird geduzt oder geihrzt?
   *
   * Wer "Ich allein" gewählt hat, darf danach nicht mehr gefragt werden,
   * worauf *ihr* Lust habt. i18next sucht mit `context` zuerst nach
   * `<schlüssel>_allein` und fällt sonst auf den normalen Schlüssel zurück -
   * so brauchen nur die Fragen eine zweite Fassung, bei denen es auffällt.
   */
  const anrede = antworten.gesellschaft === 'allein' ? 'allein' : undefined

  const ergebnis = stapel.data
  const items = ergebnis?.items ?? []
  const { ratingsFor, istFavorit } = useCardData(items)
  const schritt = weg.length
  const fehler = stapel.error

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={t('stoebern.filmabend.titel')}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      {/*
        ⚠️ Höchsthöhe plus eigenes Scrollen ist Pflicht, nicht Kosmetik.
        Der Ergebnisstapel ist höher als der Bildschirm, und ein zentriertes
        Kind, das größer als sein Behälter ist, wird oben **abgeschnitten** —
        man kommt an den Anfang dann nicht mehr heran, egal wie man scrollt.
        Deshalb bleibt der Kopf stehen und nur der Inhalt darunter scrollt.
      */}
      <div className="relative flex max-h-[calc(100vh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/60">
        <div className="shrink-0 px-6 pt-6 pb-2 sm:px-8 sm:pt-8">
          {/* Genau ein sichtbarer Ausgang. „Zurück" und „Von vorn" sind
              Navigation innerhalb des Fensters, kein zweiter Weg hinaus. */}
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close')}
            className="absolute top-4 right-4 rounded-full border border-ink-700 bg-ink-900/80 px-3 py-1 text-sm text-mist-400 transition-colors hover:text-mist-100"
          >
            ✕
          </button>

          <h2 className="pr-12 text-2xl font-bold tracking-tight">
            {t('stoebern.filmabend.titel')}
          </h2>
          <p className="mt-1 text-sm text-mist-500">{t('stoebern.filmabend.intro')}</p>

          <div className="mt-4">
            <Umschalter
              wert={mediaType}
              wahl={['movie', 'tv'] as const}
              onChange={onMediaTypeChange}
              label={(eintrag) =>
                t(eintrag === 'movie' ? 'stoebern.filme' : 'stoebern.serien')
              }
            />
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-6 sm:px-8 sm:pb-8">
        {fragenQuery.isPending && (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner className="h-4 w-4" />
            {t('common.loading')}
          </p>
        )}

        {/* --- Die Fragen --- */}
        {aktuelle && (
          <div>
            <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t('stoebern.filmabend.schritt', { nummer: schritt })}
            </p>
            <p className="mt-2 text-lg font-semibold">
              {t(`stoebern.filmabend.${aktuelle.kennung}.frage`, { context: anrede })}
            </p>

            <div className="mt-5 flex flex-wrap gap-3">
              {verfuegbareAntworten(aktuelle, antworten).map((antwort) => (
                <button
                  key={antwort}
                  type="button"
                  onClick={() => antworte(aktuelle.kennung, antwort)}
                  className="rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3 text-left text-sm font-semibold transition-colors hover:border-accent-500/70 hover:bg-ink-800"
                >
                  {t(`stoebern.filmabend.${aktuelle.kennung}.${antwort}`)}
                </button>
              ))}
            </div>

            {schritt > 1 && (
              <Button variant="ghost" onClick={zurueck} className="mt-6 !px-4 !py-2">
                {t('stoebern.filmabend.zurueck')}
              </Button>
            )}
          </div>
        )}

        {/* --- Das Ergebnis --- */}
        {fertig && (
          <div>
            {stapel.isPending && (
              <p className="flex items-center gap-2 text-sm text-mist-500">
                <Spinner className="h-4 w-4" />
                {t('stoebern.filmabend.suche')}
              </p>
            )}

            {fehler && (
              <ErrorBanner
                message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
              />
            )}

            {ergebnis && items.length === 0 && (
              <div className="rounded-xl border border-dashed border-ink-700 px-4 py-10 text-center">
                {/* Ehrlich sein, statt eine leere Fläche zu zeigen: Ohne
                    verknüpften Media-Server gibt es keinen Sehverlauf, und das
                    ist eine andere Auskunft als „nichts gefunden". */}
                <p className="text-sm text-mist-500">
                  {ergebnis.quelle_leer
                    ? t('stoebern.filmabend.keinVerlauf')
                    : t('stoebern.filmabend.nichtsGefunden', { context: anrede })}
                </p>
              </div>
            )}

            {items.length > 0 && (
              <>
                <p className="mb-4 text-sm text-mist-500">
                  {t('stoebern.filmabend.vorschlaege', { context: anrede })}
                </p>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
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
              </>
            )}

            <div className="mt-6 flex flex-wrap gap-3">
              <Button onClick={nochmal} loading={stapel.isPending} disabled={items.length === 0}>
                {t('stoebern.filmabend.nochmal')}
              </Button>
              <Button variant="ghost" onClick={vonVorn}>
                {t('stoebern.filmabend.vonVorn')}
              </Button>
            </div>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
