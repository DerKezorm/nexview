import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Avatar } from '../../components/Avatar'
import { Button, ErrorBanner } from '../../components/ui'

/**
 * Schritt „Profilbild" im Einrichtungsassistenten.
 *
 * Rein optional - aber gleich hier angeboten, weil die anderen den
 * Administrator später an genau diesem Bild erkennen. Ohne Bild zeigt Nexview
 * die Anfangsbuchstaben.
 */
export function AvatarStep({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const fileRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      // Kein JSON: der Browser setzt die passende Kopfzeile für den Upload selbst.
      return api.upload<User>('/api/auth/me/avatar', body)
    },
    onMutate: () => setError(null),
    onSuccess: updateUser,
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const removeMutation = useMutation({
    mutationFn: () => api.delete<User>('/api/auth/me/avatar'),
    onMutate: () => setError(null),
    onSuccess: updateUser,
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const name = user?.display_name ?? user?.username ?? ''

  return (
    <>
      <h2 className="text-xl font-bold tracking-tight">{t('setup.avatarTitle')}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-mist-500">{t('setup.avatarText')}</p>

      <div className="mt-5 flex flex-wrap items-center gap-4">
        <Avatar url={user?.avatar_url ?? null} name={name} className="h-20 w-20" />
        <div className="flex flex-wrap gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) uploadMutation.mutate(file)
              // Zurücksetzen, damit dieselbe Datei erneut gewählt werden kann.
              event.target.value = ''
            }}
          />
          <Button
            type="button"
            variant="ghost"
            onClick={() => fileRef.current?.click()}
            loading={uploadMutation.isPending}
          >
            {t('profile.chooseImage')}
          </Button>
          {user?.avatar_url && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => removeMutation.mutate()}
              loading={removeMutation.isPending}
            >
              {t('profile.removeImage')}
            </Button>
          )}
        </div>
      </div>

      <p className="mt-2 text-xs text-mist-600">{t('profile.pictureHint')}</p>

      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} />
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={onDone}>
          {t('setup.continue')}
        </Button>
        {!user?.avatar_url && (
          <Button type="button" variant="ghost" onClick={onSkip}>
            {t('setup.later')}
          </Button>
        )}
      </div>
    </>
  )
}
