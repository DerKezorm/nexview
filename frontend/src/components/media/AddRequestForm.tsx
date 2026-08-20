import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, MediaItem, QualityTier } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { useConfig } from '../../hooks/useConfig'
import { anfragenStandNeuLaden } from '../../lib/refresh'
import { Button, ErrorBanner, Spinner } from '../ui'

type AddRequestFormProps = {
  item: MediaItem
  onDone: () => void
  /**
   * Serie läuft schon - dann kann nur noch eine einzelne Staffel dazu.
   * "Ganze Serie" steht dann nicht zur Wahl, das wäre ohnehin abgelehnt.
   */
  seasonOnly?: boolean
  /**
   * Kam der Klick von der Merklisten-Seite? Reine Herkunftsangabe – am
   * Ablauf ändert sie nichts, sie macht die Anfrage nur nachträglich
   * zuordenbar (Abzeichen und Filter „Über Merkliste angefragt").
   */
  fromWatchlist?: boolean
}

type CreatedRequest = { id: number; status: string; title: string }

/**
 * Auswahl von Qualitätsprofil und Zielordner, dann Anfrage abschicken.
 *
 * Die Auswahlmöglichkeiten kommen direkt aus Radarr bzw. Sonarr - es gibt
 * also nichts zu tippen und nichts, was dort nicht existiert.
 */
export function AddRequestForm({
  item,
  onDone,
  seasonOnly = false,
  fromWatchlist = false,
}: AddRequestFormProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { data: config } = useConfig()

  // Sortiert der Betreiber seine Mediathek in mehrere Ordner, waehlt erst der
  // Entscheider bei der Freigabe. Dann gibt es hier nichts zu entscheiden -
  // und die Listen aus Radarr braucht dieser Benutzer gar nicht erst zu laden.
  //
  // Wer selbst freigeben darf, ist ausgenommen: Er waere es, der spaeter
  // waehlt, also waehlt er gleich jetzt.
  const zielSpaeter =
    Boolean(
      item.media_type === 'movie'
        ? config?.approver_picks_target_movie
        : config?.approver_picks_target_tv,
    ) && !user?.can_approve

  // Gibt es fuer diese Medienart ueberhaupt eine 4K-Instanz, und darf dieser
  // Benutzer sie nutzen? Nur dann erscheint der Umschalter.
  const uhdEingerichtet =
    item.media_type === 'movie'
      ? Boolean(config?.radarr_uhd_configured)
      : Boolean(config?.sonarr_uhd_configured)
  const darfUhd =
    user?.role === 'admin' ||
    (item.media_type === 'movie'
      ? Boolean(user?.can_request_uhd_movies)
      : Boolean(user?.can_request_uhd_series))
  const uhdMoeglich = uhdEingerichtet && darfUhd

  // Welche Stufen sind ueberhaupt noch offen? Ein Film, der in 1080p schon
  // liegt, laesst sich nur noch in 4K holen - und umgekehrt. Genau dafuer gibt
  // es die zweite Instanz.
  const standardOffen = item.status === 'not_requested'
  const uhdOffen = uhdMoeglich && item.status_uhd === 'not_requested'
  const [tier, setTier] = useState<QualityTier>(
    standardOffen || !uhdOffen ? 'standard' : 'uhd',
  )

  const [profileId, setProfileId] = useState<number | null>(null)
  const [folder, setFolder] = useState('')
  // Leerer String = ganze Serie. Bei Filmen bleibt das Feld ungenutzt.
  // Geht es nur noch um eine Nachlieferung, ist die neueste Staffel
  // vorausgewählt - die ist praktisch immer gemeint.
  const [season, setSeason] = useState(() =>
    seasonOnly && item.seasons.length > 0
      ? String(item.seasons[item.seasons.length - 1].season_number)
      : '',
  )

  const optionsQuery = useQuery({
    queryKey: ['arr-options', item.media_type, tier],
    queryFn: () =>
      api.get<ArrOptions>(`/api/arr/${item.media_type}/options?tier=${tier}`),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: !zielSpaeter,
  })

  // Vorauswahl treffen, sobald die Listen da sind - meist gibt es ohnehin
  // nur einen Zielordner. Welches Profil vorausgewählt wird, entscheidet der
  // Server: das vom Admin gesetzte Standardprofil, oder das erste erlaubte,
  // falls der Standard für diesen Benutzer gesperrt ist.
  useEffect(() => {
    const data = optionsQuery.data
    if (!data) return
    setProfileId(
      (current) =>
        current ?? data.default_quality_profile_id ?? data.quality_profiles[0]?.id ?? null,
    )
    setFolder((current) => current || data.default_root_folder || data.root_folders[0]?.path || '')
  }, [optionsQuery.data])

  // Stufenwechsel setzt die Auswahl zurueck. Die Profil-Kennungen der beiden
  // Instanzen kollidieren: Profil 1 der 1080p-Instanz ist ein voellig anderes
  // als Profil 1 der 4K-Instanz. Bliebe die alte Wahl stehen, ginge sie an die
  // falsche Instanz - und Radarr nimmt eine unbekannte Kennung je nach Fassung
  // kommentarlos an.
  function stufeWechseln(neu: QualityTier) {
    if (neu === tier) return
    setTier(neu)
    setProfileId(null)
    setFolder('')
  }

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<CreatedRequest>('/api/requests', {
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        tier,
        quality_profile_id:
          zielSpaeter || !options?.quality_profile_choice ? null : profileId,
        // Ohne Auswahlrecht bewusst nichts mitschicken: welcher Ordner gilt,
        // entscheidet dann allein der Server.
        root_folder_path: !zielSpaeter && options?.root_folder_choice ? folder : null,
        season: season === '' ? null : Number(season),
        from_watchlist: fromWatchlist,
      }),
    onSuccess: () => {
      // Badges und Kontingent neu laden - auch auf der Seite, die hinter
      // diesem Fenster liegt und gleich wieder sichtbar wird.
      anfragenStandNeuLaden(queryClient)
      onDone()
    },
  })

  // Die Abfrage ist abgeschaltet, wenn spaeter gewaehlt wird - eine
  // abgeschaltete Abfrage bleibt dauerhaft "pending", deshalb hier zuerst.
  if (!zielSpaeter && optionsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (!zielSpaeter && optionsQuery.isError) {
    return (
      <ErrorBanner
        message={
          optionsQuery.error instanceof ApiError
            ? optionsQuery.error.message
            : t('errors.generic')
        }
      />
    )
  }

  const options = optionsQuery.data ?? null
  const stufeOffen = tier === 'standard' ? standardOffen : uhdOffen
  const ready = !stufeOffen
    ? false
    : zielSpaeter
    ? true
    : options !== null &&
      (!options.quality_profile_choice || profileId !== null) &&
      (!options.root_folder_choice || folder !== '')

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <h3 className="text-sm font-semibold">{t('request.chooseOptions')}</h3>

      {/* Nur wenn es beide Stufen gibt und der Benutzer beide darf. Sonst
          bleibt der Dialog genau so, wie er immer war. */}
      {uhdMoeglich && (
        <div
          className="mt-3 flex rounded-full border border-ink-700 bg-ink-900 p-0.5"
          role="group"
          aria-label={t('uhd.tier')}
        >
          {(['standard', 'uhd'] as const).map((stufe) => {
            const offen = stufe === 'standard' ? standardOffen : uhdOffen
            return (
              <button
                key={stufe}
                type="button"
                onClick={() => stufeWechseln(stufe)}
                aria-pressed={tier === stufe}
                disabled={!offen}
                title={offen ? undefined : t('uhd.tierBelegt')}
                className={
                  'flex-1 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ' +
                  (tier === stufe
                    ? 'bg-accent-500 text-white'
                    : offen
                      ? 'text-mist-500 hover:text-mist-100'
                      : 'cursor-not-allowed text-mist-700')
                }
              >
                {t(stufe === 'standard' ? 'uhd.tierStandard' : 'uhd.tierUhd')}
              </button>
            )
          })}
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Nur bei Serien. Beim Nachfordern immer zeigen - auch wenn nur eine
            Staffel fehlt: sonst sähe man nicht, was da eigentlich bestellt
            wird. Sonst erst ab zwei Staffeln, darunter gäbe es keine Wahl. */}
        {item.media_type === 'tv' && (item.seasons.length > 1 || seasonOnly) && (
          <label className="flex flex-col gap-1.5 sm:col-span-2">
            <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t('request.season')}
            </span>
            <select
              value={season}
              onChange={(event) => setSeason(event.target.value)}
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              {!seasonOnly && <option value="">{t('request.wholeSeries')}</option>}
              {item.seasons.map((staffel) => (
                <option key={staffel.season_number} value={staffel.season_number}>
                  {staffel.name} · {t('request.episodes', { count: staffel.episode_count })}
                </option>
              ))}
            </select>
            <span className="text-xs text-mist-600">{t('request.seasonHint')}</span>
          </label>
        )}

        {/* Darf der Benutzer das Profil gar nicht waehlen, gibt es hier nichts
            zu entscheiden - dann wird das Feld weggelassen wie beim Ordner. */}
        {!zielSpaeter && options !== null && options.quality_profile_choice && (
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
            {t('request.qualityProfile')}
          </span>
          <select
            value={profileId ?? ''}
            onChange={(event) => setProfileId(Number(event.target.value))}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {options.quality_profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        )}

        {/* Hat der Administrator die Auswahl abgeschaltet, gibt es hier nichts
            zu entscheiden - dann wird das Feld gar nicht erst gezeigt. Welcher
            Ordner gilt, setzt der Server ohnehin selbst. */}
        {!zielSpaeter && options !== null && options.root_folder_choice && (
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t('request.rootFolder')}
            </span>
            <select
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              {options.root_folders.map((root) => (
                <option key={root.path} value={root.path}>
                  {root.path}
                  {root.free_space ? ` (${formatSpace(root.free_space)})` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        {zielSpaeter && (
          <p className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs leading-relaxed text-mist-500 sm:col-span-2">
            {t('request.targetLater')}
          </p>
        )}
      </div>

      {createMutation.isError && (
        <div className="mt-3">
          <ErrorBanner
            message={
              createMutation.error instanceof ApiError
                ? createMutation.error.message
                : t('errors.generic')
            }
          />
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => createMutation.mutate()}
          loading={createMutation.isPending}
          disabled={!ready}
        >
          {t('request.submit')}
        </Button>
        <p className="text-xs text-mist-600">{t('request.hint')}</p>
      </div>
    </div>
  )
}

/** Freier Speicherplatz lesbar machen: 1234567890 -> "1,1 TB" */
function formatSpace(bytes: number): string {
  const terabytes = bytes / 1024 ** 4
  if (terabytes >= 1) return `${terabytes.toFixed(1)} TB`
  return `${Math.round(bytes / 1024 ** 3)} GB`
}
