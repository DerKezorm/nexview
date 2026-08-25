import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import { Button, Card, ErrorBanner, Spinner } from '../../components/ui'
import { useRegionen } from '../../hooks/useRegionen'

/**
 * Die eigenen Streaming-Abos.
 *
 * Wozu: Wer einen Titel anfragt, der in seinem eigenen Abo läuft, bekommt es
 * beim Anfragen gesagt - ein Hinweis, keine Sperre. Ohne diese Angabe passiert
 * schlicht nichts; das Feature schaltet sich durch Benutzung ein und braucht
 * keinen Schalter beim Betreiber.
 *
 * Die **Region** wird hier nur benannt, nicht gesetzt. Sie entscheidet, was zur
 * Auswahl steht - WOW und RTL+ in Deutschland, Hulu und Peacock in den USA -,
 * ist aber dasselbe Feld wie unter „Sprache und Region". Zwei Bedienelemente
 * für eine Einstellung laufen auseinander, und hier täten sie es sofort: Dort
 * gibt es den leeren Eintrag „Vorgabe des Betreibers", der mitwandert, wenn
 * der Betreiber sie ändert. Ein zweites Feld hätte daraus beim ersten
 * Speichern stillschweigend „selbst gewählt" gemacht.
 *
 * Genannt wird sie trotzdem, und zwar mit dem Unterschied zwischen „selbst
 * gewählt" und „geerbt": Der Einrichtungsassistent fragt nie nach der Region,
 * also hat die Mehrheit die Vorgabe des Betreibers, ohne davon zu wissen.
 */

type Dienst = {
  slug: string
  name: string
  logo_url: string | null
}

type Uebersicht = {
  region: string
  region_selbst_gewaehlt: boolean
  dienste: Dienst[]
  meine: string[]
}

export function StreamingDienste({ aufSpracheUndRegion }: { aufSpracheUndRegion: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const uebersicht = useQuery({
    queryKey: ['streaming'],
    queryFn: () => api.get<Uebersicht>('/api/streaming'),
  })
  const regionen = useRegionen()

  const [gewaehlt, setGewaehlt] = useState<Set<string>>(new Set())
  const [gespeichert, setGespeichert] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  // Nur einmal vorbelegen - sonst überschreibt ein Nachladen im Hintergrund
  // die gerade getroffene Auswahl. Dasselbe Muster wie in DiscoverDefaults.
  const vorbelegt = useRef(false)
  useEffect(() => {
    if (!uebersicht.data || vorbelegt.current) return
    vorbelegt.current = true
    setGewaehlt(new Set(uebersicht.data.meine))
  }, [uebersicht.data])

  const speichern = useMutation({
    mutationFn: () => api.put<Uebersicht>('/api/streaming', { slugs: [...gewaehlt] }),
    onMutate: () => {
      setGespeichert(false)
      setFehler(null)
    },
    onSuccess: (frisch) => {
      queryClient.setQueryData(['streaming'], frisch)
      setGewaehlt(new Set(frisch.meine))
      setGespeichert(true)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const geaendert = useMemo(() => {
    if (!uebersicht.data) return false
    const vorher = new Set(uebersicht.data.meine)
    if (vorher.size !== gewaehlt.size) return true
    for (const slug of gewaehlt) if (!vorher.has(slug)) return true
    return false
  }, [uebersicht.data, gewaehlt])

  if (uebersicht.isLoading) {
    return (
      <Card className="flex items-center gap-3 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </Card>
    )
  }

  if (uebersicht.isError || !uebersicht.data) {
    return <ErrorBanner message={t('profile.streaming.unavailable')} />
  }

  const daten = uebersicht.data
  const regionName =
    (regionen.data ?? []).find((eintrag) => eintrag.code === daten.region)?.name ??
    daten.region

  function umschalten(slug: string) {
    setGespeichert(false)
    setGewaehlt((vorher) => {
      const naechste = new Set(vorher)
      if (naechste.has(slug)) naechste.delete(slug)
      else naechste.add(slug)
      return naechste
    })
  }

  return (
    <Card className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t('profile.streaming.title')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('profile.streaming.intro')}</p>
      </div>

      {/* Die Region **nur benannt**, nicht hier einstellbar.
          Sie steckt in demselben Feld wie unter „Sprache und Region"
          (``discover_region``) - zwei Bedienelemente für eine Einstellung
          laufen früher oder später auseinander. Konkret liefe es hier schon
          heute anders: Dort gibt es den leeren Eintrag „Vorgabe des
          Betreibers", der mitwandert, wenn der Betreiber sie ändert. Ein
          Feld an dieser Stelle hätte aus „geerbt" beim ersten Speichern
          stillschweigend „selbst gewählt" gemacht. */}
      <div className="flex flex-col gap-1">
        <p className="text-sm text-mist-300">
          {t('profile.streaming.regionIs', { region: regionName })}{' '}
          <button
            type="button"
            onClick={aufSpracheUndRegion}
            className="text-accent-400 underline underline-offset-2 hover:text-accent-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
          >
            {t('profile.streaming.regionChange')}
          </button>
        </p>
        <span className="text-xs leading-relaxed text-mist-600">
          {daten.region_selbst_gewaehlt
            ? t('profile.streaming.regionHint')
            : t('profile.streaming.regionInherited')}
        </span>
      </div>

      {daten.dienste.length === 0 ? (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('profile.streaming.noneHere')}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-mist-300">
            {t('profile.streaming.pickLabel')}
          </span>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {daten.dienste.map((dienst) => {
              const an = gewaehlt.has(dienst.slug)
              return (
                <button
                  key={dienst.slug}
                  type="button"
                  role="switch"
                  aria-checked={an}
                  onClick={() => umschalten(dienst.slug)}
                  disabled={speichern.isPending}
                  className={
                    'flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 disabled:opacity-50 ' +
                    (an
                      ? 'border-accent-500 bg-accent-500/10 text-mist-100'
                      : 'border-ink-700 bg-ink-900 text-mist-400 hover:border-ink-600')
                  }
                >
                  {dienst.logo_url ? (
                    <img
                      src={dienst.logo_url}
                      alt=""
                      className={
                        'h-9 w-9 shrink-0 rounded-lg object-contain transition ' +
                        (an ? '' : 'opacity-60 grayscale')
                      }
                    />
                  ) : (
                    <span className="h-9 w-9 shrink-0 rounded-lg bg-ink-800" />
                  )}
                  <span className="min-w-0 font-medium leading-tight">{dienst.name}</span>
                </button>
              )
            })}
          </div>
          <span className="text-xs leading-relaxed text-mist-600">
            {t('profile.streaming.pickHint')}
          </span>
        </div>
      )}

      {fehler && <ErrorBanner message={fehler} />}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => speichern.mutate()}
          loading={speichern.isPending}
          disabled={!geaendert}
        >
          {t('common.save')}
        </Button>
        {gespeichert && !geaendert && (
          <span className="text-sm text-ok-500">{t('profile.streaming.saved')}</span>
        )}
        {geaendert && !speichern.isPending && (
          <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
        )}
      </div>
    </Card>
  )
}
