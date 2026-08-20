import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { ArrOptions, MediaType, QualityTier } from '../api/types'
import { ErrorBanner, Spinner } from './ui'

/** Was der Entscheider bei der Freigabe festlegt. */
export type Target = {
  root_folder_path: string
  quality_profile_id: number | null
}

/**
 * Zielordner und Qualitätsprofil wählen - für die Freigabe.
 *
 * Gebraucht, wenn der Betreiber „Zielordner wählt der Entscheider" gesetzt
 * hat: Der Anfragende lässt beides offen, weil er die Ordnerstruktur nicht
 * kennt. Die Listen kommen direkt aus Radarr bzw. Sonarr - es gibt also
 * nichts zu tippen und nichts, was dort nicht existiert.
 *
 * Die Auswahl gehört der Komponente selbst; nach außen geht sie erst, wenn
 * bestätigt wird. So kann ein halb ausgefüllter Zustand nie abgeschickt
 * werden.
 */
export function TargetPicker({
  mediaType,
  tier = 'standard',
  label,
  onChange,
}: {
  mediaType: MediaType
  /**
   * Welche Instanz? **Zwingend** bei einer 4K-Anfrage: Ordner und Profile der
   * beiden Instanzen sind vollkommen verschieden. Ohne die Angabe bekam der
   * Entscheider die Listen des Standard-Radarr zu sehen - und die Freigabe
   * scheiterte danach an der Pruefung, weil es den gewaehlten Ordner in der
   * 4K-Instanz gar nicht gibt.
   */
  tier?: QualityTier
  /** Überschrift - bei gemischten Stapeln steht hier die Medienart. */
  label?: string
  /** Meldet jede Änderung nach oben; null, solange nichts vollständig ist. */
  onChange: (target: Target | null) => void
}) {
  const { t } = useTranslation()
  const [folder, setFolder] = useState('')
  const [profileId, setProfileId] = useState<number | null>(null)

  const optionsQuery = useQuery({
    queryKey: ['arr-options', mediaType, tier],
    queryFn: () => api.get<ArrOptions>(`/api/arr/${mediaType}/options?tier=${tier}`),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  /**
   * Auswahl übernehmen und **sofort** nach oben melden.
   *
   * Bewusst hier und nicht in einem `useEffect`: Ein Effekt läuft erst nach
   * dem Rendern. Wer unmittelbar nach dem Umstellen auf „Freigeben" klickt,
   * verschickte dann noch den vorherigen Ordner - und der Titel läge still
   * im falschen Verzeichnis. Genau das ist beim Prüfen passiert.
   */
  function waehlen(neuerOrdner: string, neuesProfil: number | null) {
    setFolder(neuerOrdner)
    setProfileId(neuesProfil)
    onChange(
      neuerOrdner && neuesProfil !== null
        ? { root_folder_path: neuerOrdner, quality_profile_id: neuesProfil }
        : null,
    )
  }

  // Vorbelegen, sobald die Listen da sind - meist ist der Standard schon der
  // richtige, und dann ist die Freigabe wieder ein einziger Klick.
  useEffect(() => {
    const data = optionsQuery.data
    if (!data) return
    waehlen(
      folder || data.default_root_folder || data.root_folders[0]?.path || '',
      profileId ?? data.default_quality_profile_id ?? data.quality_profiles[0]?.id ?? null,
    )
    // Nur wenn die Listen eintreffen - sonst überschriebe das Vorbelegen
    // dauernd die Wahl des Benutzers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [optionsQuery.data])

  if (optionsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (optionsQuery.isError) {
    return (
      <ErrorBanner
        message={
          optionsQuery.error instanceof ApiError ? optionsQuery.error.message : t('errors.generic')
        }
      />
    )
  }

  const options = optionsQuery.data

  return (
    <div className="flex flex-col gap-2">
      {label && (
        <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">{label}</span>
      )}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-mist-600">{t('request.rootFolder')}</span>
          <select
            value={folder}
            onChange={(event) => waehlen(event.target.value, profileId)}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {options.root_folders.map((root) => (
              <option key={root.path} value={root.path}>
                {root.path}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-xs text-mist-600">{t('request.qualityProfile')}</span>
          <select
            value={profileId ?? ''}
            onChange={(event) => waehlen(folder, Number(event.target.value))}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {options.quality_profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}
