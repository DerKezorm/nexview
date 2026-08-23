import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, ParentWish } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { TitelVerweis } from '../../components/TitelVerweis'

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
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
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
  quality_profile_id: number | null
  root_folder_path: string | null
}

/**
 * Profil und Ordner wählen – aber nur, soweit der Betreiber die Wahl überhaupt
 * freigegeben hat.
 *
 * Ein Feld anzuzeigen, dessen Wert der Server verwirft, wäre eine Lüge; hängt
 * die Wahl am Entscheider, steht hier deshalb nur ein Satz und ein Knopf.
 */
function Zielwahl({
  wunsch,
  zielSpaeter,
  laeuft,
  onFreigeben,
  onAbbrechen,
}: {
  wunsch: ParentWish
  zielSpaeter: boolean
  laeuft: boolean
  onFreigeben: (ziel: Ziel) => void
  onAbbrechen: () => void
}) {
  const { t } = useTranslation()
  const [profil, setProfil] = useState<number | null>(null)
  const [ordner, setOrdner] = useState('')

  const optionen = useQuery({
    queryKey: ['arr-options', wunsch.media_type, 'standard'],
    queryFn: () => api.get<ArrOptions>(`/api/arr/${wunsch.media_type}/options?tier=standard`),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: !zielSpaeter,
  })

  const daten = optionen.data
  // Die Vorauswahl bestimmt der Server – sie hängt an den Sperren des Kontos.
  const gewaehltesProfil = profil ?? daten?.default_quality_profile_id ?? null
  const gewaehlterOrdner = ordner || daten?.default_root_folder || ''

  const bereit =
    zielSpaeter ||
    (daten !== undefined &&
      (!daten.quality_profile_choice || gewaehltesProfil !== null) &&
      (!daten.root_folder_choice || gewaehlterOrdner !== ''))

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

      {zielSpaeter ? (
        <p className="text-sm text-mist-500">{t('children.wishTargetLater')}</p>
      ) : optionen.isPending ? (
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
              quality_profile_id: zielSpaeter ? null : gewaehltesProfil,
              root_folder_path: zielSpaeter ? null : gewaehlterOrdner || null,
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
