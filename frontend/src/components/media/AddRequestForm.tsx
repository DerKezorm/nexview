import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, MediaItem } from '../../api/types'
import { Button, ErrorBanner, Spinner } from '../ui'

type AddRequestFormProps = {
  item: MediaItem
  onDone: () => void
}

type CreatedRequest = { id: number; status: string; title: string }

/**
 * Auswahl von Qualitätsprofil und Zielordner, dann Anfrage abschicken.
 *
 * Die Auswahlmöglichkeiten kommen direkt aus Radarr bzw. Sonarr - es gibt
 * also nichts zu tippen und nichts, was dort nicht existiert.
 */
export function AddRequestForm({ item, onDone }: AddRequestFormProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [profileId, setProfileId] = useState<number | null>(null)
  const [folder, setFolder] = useState('')

  const optionsQuery = useQuery({
    queryKey: ['arr-options', item.media_type],
    queryFn: () => api.get<ArrOptions>(`/api/arr/${item.media_type}/options`),
    staleTime: 5 * 60 * 1000,
    retry: false,
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
    setFolder((current) => current || (data.root_folders[0]?.path ?? ''))
  }, [optionsQuery.data])

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<CreatedRequest>('/api/requests', {
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        quality_profile_id: profileId,
        root_folder_path: folder,
      }),
    onSuccess: () => {
      // Badges und Kontingent neu laden.
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      void queryClient.invalidateQueries({ queryKey: ['my-requests'] })
      void queryClient.invalidateQueries({ queryKey: ['quota'] })
      onDone()
    },
  })

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
          optionsQuery.error instanceof ApiError
            ? optionsQuery.error.message
            : t('errors.generic')
        }
      />
    )
  }

  const options = optionsQuery.data
  const ready = profileId !== null && folder !== ''

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <h3 className="text-sm font-semibold">{t('request.chooseOptions')}</h3>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
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
