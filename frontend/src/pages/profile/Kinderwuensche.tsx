import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, MediaItem, ParentWish } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { TitelVerweis } from '../../components/TitelVerweis'
import { FolgenAuswahl } from '../../components/media/StaffelFolgenWaehler'
import { belegungsWort, staffelBelegt } from '../../components/media/staffelbelegung'
import {
  anfragbareFassungen,
  fassungName,
  staffelFassung,
  zielWaehlbar,
} from '../../lib/fassungen'
import { folgenKompakt } from '../../lib/format'

/**
 * Die offenen Wünsche der eigenen Kinder.
 *
 * „Freigeben" macht daraus über `requests_service.create_request` eine ganz
 * gewöhnliche Anfrage **auf den Namen des Elternteils** – mit seinem
 * Kontingent, seinen Profilsperren und seinem üblichen Freigabeweg. Scheitert
 * das (Kontingent voll, Sperrliste, liegt schon da), bleibt der Wunsch offen
 * und der Grund steht hier.
 *
 * Zielordner und Qualitätsprofil werden gefragt, **wenn** der Betreiber die
 * Wahl freigegeben hat – dieselbe Regel wie bei einer eigenen Anfrage. Wählt
 * ohnehin erst der Entscheider (`approver_picks_target`), bleibt beides offen
 * und es gibt hier nichts zu klicken.
 */
export function Kinderwuensche() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { data: config } = useConfig()
  const [fehler, setFehler] = useState<string | null>(null)
  const [absage, setAbsage] = useState<ParentWish | null>(null)
  const [freigabe, setFreigabe] = useState<ParentWish | null>(null)
  const [notiz, setNotiz] = useState('')

  const wuensche = useQuery({
    queryKey: ['parent-wishes'],
    queryFn: () => api.get<ParentWish[]>('/api/children/wishes'),
  })

  const auffrischen = () => {
    void queryClient.invalidateQueries({ queryKey: ['parent-wishes'] })
    // Die eigene Anfrageliste und das Kontingent ändern sich mit.
    void queryClient.invalidateQueries({ queryKey: ['my-requests'] })
    void queryClient.invalidateQueries({ queryKey: ['quota'] })
  }

  const freigeben = useMutation({
    mutationFn: ({ wunsch, ziel }: { wunsch: ParentWish; ziel: Ziel }) =>
      api.post(`/api/children/wishes/${wunsch.id}/release`, ziel),
    onSuccess: () => {
      setFehler(null)
      setFreigabe(null)
      auffrischen()
    },
    // ⚠️ **Auch der Fehlschlag frischt auf.** Ein Wunsch, dessen Titel längst
    // im Haus ist, wird beim Freigeben serverseitig als erledigt geschlossen —
    // die Freigabe selbst scheitert dabei mit einer Meldung. Ohne dieses
    // Auffrischen bliebe er in der Liste stehen und ließe sich immer wieder
    // anklicken, obwohl über ihn längst entschieden ist.
    onError: (error) => {
      setFehler(error instanceof ApiError ? error.message : String(error))
      setFreigabe(null)
      auffrischen()
    },
  })

  const ablehnen = useMutation({
    mutationFn: (wunsch: ParentWish) =>
      api.post(`/api/children/wishes/${wunsch.id}/decline`, { note: notiz.trim() || null }),
    onSuccess: () => {
      setFehler(null)
      setAbsage(null)
      setNotiz('')
      auffrischen()
    },
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
  })

  if (wuensche.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  const liste = wuensche.data ?? []

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('children.wishesTitle')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('children.wishReleaseHint')}</p>
      </div>

      {fehler && <ErrorBanner message={fehler} />}

      {liste.length === 0 && (
        <p className="text-sm text-mist-500">{t('children.wishesEmpty')}</p>
      )}

      {liste.map((wunsch) => (
        <Card key={wunsch.id} className="flex flex-wrap items-center gap-4">
          <div className="h-24 w-16 shrink-0 overflow-hidden rounded-lg bg-ink-900">
            {wunsch.poster_path && (
              <img src={wunsch.poster_path} alt="" className="h-full w-full object-cover" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium text-mist-100">
              <TitelVerweis
                mediaType={wunsch.media_type}
                tmdbId={wunsch.tmdb_id}
                titel={wunsch.title}
                erschienen={wunsch.release_date}
              />
            </p>
            <p className="text-xs text-mist-600">
              {t('children.wishFrom', { name: wunsch.child_name })}
              {wunsch.release_date && ` · ${wunsch.release_date.slice(0, 4)}`}
            </p>
            {/* Läuft in einem Abo, das **du** hast - das Kind guckt darüber
                mit. Ein Hinweis für die Entscheidung, keine Sperre. */}
            {(wunsch.in_my_subscriptions ?? []).length > 0 && (
              <p className="mt-1 text-xs text-warn-500">
                {t('children.wishInSubscription', {
                  name: wunsch.child_name,
                  services: (wunsch.in_my_subscriptions ?? []).join(', '),
                })}
                {wunsch.media_type === 'tv' && ` ${t('children.wishSeriesNote')}`}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              onClick={() => {
                setAbsage(null)
                setFreigabe(freigabe?.id === wunsch.id ? null : wunsch)
              }}
            >
              {t('children.wishRelease')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setNotiz('')
                setAbsage(wunsch)
              }}
            >
              {t('children.wishDecline')}
            </Button>
          </div>
        </Card>
      ))}

      {freigabe && (
        <Zielwahl
          wunsch={freigabe}
          zielSpaeter={Boolean(
            user &&
              !user.can_approve &&
              (freigabe.media_type === 'movie'
                ? config?.approver_picks_target_movie
                : config?.approver_picks_target_tv),
          )}
          folgenErlaubt={Boolean(config?.episode_requests_enabled)}
          laeuft={freigeben.isPending}
          onFreigeben={(ziel) => freigeben.mutate({ wunsch: freigabe, ziel })}
          onAbbrechen={() => setFreigabe(null)}
        />
      )}

      {absage && (
        <Card className="flex flex-col gap-4">
          <h3 className="text-base font-semibold">{t('children.wishDeclineTitle')}</h3>
          <p className="text-sm text-mist-500">
            {t('children.wishDeclineText', { title: absage.title })}
          </p>
          <Field
            label={t('children.wishDeclineNote')}
            value={notiz}
            onChange={(event) => setNotiz(event.target.value)}
            maxLength={500}
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => ablehnen.mutate(absage)} loading={ablehnen.isPending}>
              {t('children.wishDecline')}
            </Button>
            <Button variant="ghost" onClick={() => setAbsage(null)}>
              {t('common.cancel')}
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}

/** Was beim Freigeben mitgeschickt wird. */
type Ziel = {
  /** In welcher Fassung – die Eltern wählen wie im Anfrageformular. */
  fassung: string | undefined
  quality_profile_id: number | null
  root_folder_path: string | null
  season: number | null
  episodes: number[] | null
}

/**
 * Profil, Ordner – und bei Serien Staffel und Folgen wählen.
 *
 * Ein Feld anzuzeigen, dessen Wert der Server verwirft, wäre eine Lüge; hängt
 * die Ziel-Wahl am Entscheider, bleiben Profil und Ordner weg. **Die Staffel
 * bleibt trotzdem Pflicht**: Ohne sie hieß die Freigabe stillschweigend
 * „ganze Serie" – samt allem, was noch kommt. Eltern dosieren jetzt: eine
 * Staffel je Freigabe, auf Wunsch nur einzelne Folgen zum Antesten. Für
 * „mehr davon" wünscht das Kind einfach erneut.
 */
function Zielwahl({
  wunsch,
  zielSpaeter: zielSpaeterVorgabe,
  folgenErlaubt,
  laeuft,
  onFreigeben,
  onAbbrechen,
}: {
  wunsch: ParentWish
  zielSpaeter: boolean
  folgenErlaubt: boolean
  laeuft: boolean
  onFreigeben: (ziel: Ziel) => void
  onAbbrechen: () => void
}) {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const { user } = useAuth()
  const [profil, setProfil] = useState<number | null>(null)
  const [ordner, setOrdner] = useState('')
  const [staffel, setStaffel] = useState<number | null>(null)
  const [folgen, setFolgen] = useState<Set<number>>(new Set())
  const [folgenSicht, setFolgenSicht] = useState(false)

  const istSerie = wunsch.media_type === 'tv'

  // ⚠️ **Eltern wählen die Fassung, aber nur aus dem, was sie selbst dürfen.**
  // Bis 0.35 ging jede Freigabe fest in die Standard-Instanz; wer 4K durfte,
  // konnte seinem Kind trotzdem kein 4K freigeben. Die Liste kommt fertig vom
  // Server - Recht und eingerichtete Quelle stecken schon darin.
  const fassungen = anfragbareFassungen(config, wunsch.media_type)
  const [kennung, setKennung] = useState<string>(() => fassungen[0]?.kennung ?? '')
  const fassung = fassungen.find((f) => f.kennung === kennung) ?? fassungen[0] ?? null

  // Wer wählt Ordner und Profil? Das gilt je Fassung - eine 4K-Freigabe kann
  // auf den Entscheider warten, während Standard durchläuft.
  const zielSpaeter = fassung
    ? fassung.approver_picks_target && !user?.can_approve
    : zielSpaeterVorgabe
  // ⚠️ Im NEX-Betrieb gibt es Ordner und Profil nicht zu wählen, die
  // Listen-Adresse antwortet `409` (Rundgang-Befund 6).
  const ohneZiel = zielSpaeter || !zielWaehlbar(config)

  const optionen = useQuery({
    queryKey: ['arr-options', wunsch.media_type, fassung?.kennung ?? ''],
    queryFn: () =>
      api.get<ArrOptions>(
        `/api/arr/${wunsch.media_type}/options?fassung=${encodeURIComponent(
          fassung?.kennung ?? '',
        )}`,
      ),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: config !== undefined && !ohneZiel,
  })

  // Die Staffelliste samt Belegt-Angaben - dieselbe Quelle wie das
  // Anfrage-Formular, damit hier nichts wählbar aussieht, was der Server
  // gleich mit 409 ablehnt.
  const detail = useQuery({
    queryKey: ['freigabe-detail', wunsch.tmdb_id],
    queryFn: () => api.get<MediaItem>(`/api/detail/tv/${wunsch.tmdb_id}`),
    enabled: istSerie,
  })
  const staffeln = detail.data?.seasons ?? []
  const waehlbare = staffeln.filter((s) => !fassung || !staffelBelegt(s, fassung))
  const gewaehlteStaffel = staffel ?? waehlbare[0]?.season_number ?? null

  const daten = optionen.data
  // Die Vorauswahl bestimmt der Server – sie hängt an den Sperren des Kontos.
  const gewaehltesProfil = profil ?? daten?.default_quality_profile_id ?? null
  const gewaehlterOrdner = ordner || daten?.default_root_folder || ''

  const zielBereit =
    ohneZiel ||
    (daten !== undefined &&
      (!daten.quality_profile_choice || gewaehltesProfil !== null) &&
      (!daten.root_folder_choice || gewaehlterOrdner !== ''))
  const bereit = zielBereit && (!istSerie || gewaehlteStaffel !== null)

  return (
    <Card className="flex flex-col gap-4">
      <h3 className="text-base font-semibold">
        <TitelVerweis
          mediaType={wunsch.media_type}
          tmdbId={wunsch.tmdb_id}
          titel={wunsch.title}
          erschienen={wunsch.release_date}
        />
      </h3>

      {/* Erst die Fassung, sobald es mehr als eine gibt - dieselbe Wahl wie
          im Anfrageformular, begrenzt auf das, was dieses Konto selbst darf. */}
      {fassungen.length > 1 && (
        <div
          className="flex rounded-full border border-ink-700 bg-ink-900 p-0.5"
          role="group"
          aria-label={t('uhd.tier')}
        >
          {fassungen.map((kandidat) => (
            <button
              key={kandidat.kennung}
              type="button"
              onClick={() => {
                setKennung(kandidat.kennung)
                setProfil(null)
                setOrdner('')
                setStaffel(null)
                setFolgen(new Set())
              }}
              aria-pressed={fassung?.kennung === kandidat.kennung}
              className={
                'flex-1 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ' +
                (fassung?.kennung === kandidat.kennung
                  ? 'bg-accent-500 text-white'
                  : 'text-mist-500 hover:text-mist-100')
              }
            >
              {fassungName(t, kandidat)}
            </button>
          ))}
        </div>
      )}

      {/* Bei Serien zuerst: Welche Staffel - und auf Wunsch welche Folgen?
          Das bleibt auch dann Pflicht, wenn Profil und Ordner erst der
          Entscheider wählt: Der Umfang der Zusage gehört den Eltern. */}
      {istSerie &&
        (detail.isPending ? (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('common.loading')}
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
              {t('children.wishSeason')}
              <select
                value={gewaehlteStaffel ?? ''}
                onChange={(event) => {
                  setStaffel(Number(event.target.value))
                  setFolgen(new Set())
                  setFolgenSicht(false)
                }}
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100"
              >
                {staffeln.map((eintrag) => {
                  const stand = fassung ? staffelFassung(eintrag, fassung) : null
                  const belegt = Boolean(fassung && staffelBelegt(eintrag, fassung))
                  const gesamt = stand?.episodes_total ?? eintrag.episode_count
                  const vorhanden =
                    gesamt > 0 && (stand?.episodes_available ?? 0) >= gesamt
                  return (
                    <option
                      key={eintrag.season_number}
                      value={eintrag.season_number}
                      disabled={belegt}
                    >
                      {eintrag.name}
                      {belegt
                        ? ` · ${t(belegungsWort(stand?.requested_status, vorhanden))}`
                        : ''}
                    </option>
                  )
                })}
              </select>
            </label>

            {gewaehlteStaffel !== null && folgenErlaubt && (
              <div className="flex flex-col gap-2">
                <button
                  type="button"
                  onClick={() => setFolgenSicht((wert) => !wert)}
                  className="self-start text-sm text-mist-400 underline-offset-2 hover:text-accent-500 hover:underline"
                >
                  {folgen.size > 0
                    ? t('request.episodesPicked', { list: folgenKompakt([...folgen]) })
                    : t('children.wishEpisodes')}
                </button>
                {folgen.size === 0 && !folgenSicht && (
                  <p className="text-xs text-mist-600">{t('children.wishWholeSeason')}</p>
                )}
                {folgenSicht && fassung && (
                  <div className="rounded-xl border border-ink-700 bg-ink-900/60">
                    <FolgenAuswahl
                      tmdbId={wunsch.tmdb_id}
                      season={gewaehlteStaffel}
                      fassung={fassung}
                      ganzGewaehlt={false}
                      paket={folgen}
                      restGewuenscht={false}
                      onRestErledigt={() => {}}
                      onSetzen={(menge, ganz) => setFolgen(ganz ? new Set() : menge)}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

      {zielSpaeter ? (
        <p className="text-sm text-mist-500">{t('children.wishTargetLater')}</p>
      ) : ohneZiel ? null : optionen.isPending ? (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      ) : optionen.error ? (
        <ErrorBanner message={(optionen.error as Error).message} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {daten?.quality_profile_choice && (
            <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
              {t('children.wishProfile')}
              <select
                value={gewaehltesProfil ?? ''}
                onChange={(event) => setProfil(Number(event.target.value))}
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100"
              >
                <option value="">—</option>
                {daten.quality_profiles.map((eintrag) => (
                  <option key={eintrag.id} value={eintrag.id}>
                    {eintrag.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {daten?.root_folder_choice && (
            <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
              {t('children.wishFolder')}
              <select
                value={gewaehlterOrdner}
                onChange={(event) => setOrdner(event.target.value)}
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100"
              >
                <option value="">—</option>
                {daten.root_folders.map((eintrag) => (
                  <option key={eintrag.path} value={eintrag.path}>
                    {eintrag.path}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!bereit}
          loading={laeuft}
          onClick={() =>
            onFreigeben({
              fassung: fassung?.kennung || undefined,
              quality_profile_id: ohneZiel ? null : gewaehltesProfil,
              root_folder_path: ohneZiel ? null : gewaehlterOrdner || null,
              season: istSerie ? gewaehlteStaffel : null,
              episodes:
                istSerie && folgen.size > 0
                  ? [...folgen].sort((a, b) => a - b)
                  : null,
            })
          }
        >
          {t('children.wishRelease')}
        </Button>
        <Button variant="ghost" onClick={onAbbrechen}>
          {t('common.cancel')}
        </Button>
      </div>
    </Card>
  )
}
