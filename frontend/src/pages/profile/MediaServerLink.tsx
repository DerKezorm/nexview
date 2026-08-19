/**
 * Das eigene Konto mit dem Media-Server verbinden.
 *
 * Der Weg für alle, die schon ein Nexview-Konto haben - eingeladene Benutzer
 * ebenso wie der Administrator nach der Einrichtung. Danach führen beide Wege,
 * Passwort und Plex, in dasselbe Konto.
 *
 * Verbinden setzt Zugriff auf die Bibliothek voraus. Das prüft der Server;
 * hier wird nur seine Antwort angezeigt.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { Button, Card, ErrorBanner } from '../../components/ui'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'

export function MediaServerLink() {
  const { t } = useTranslation()
  const { user, updateUser, mediaServerLogin } = useAuth()
  const [hinweis, setHinweis] = useState<string | null>(null)

  const verbinden = useMediaServerChallenge<{ status: string; user: User | null }>({
    startPfad: '/api/auth/mediaserver/link/start',
    abfragePfad: '/api/auth/mediaserver/link/poll',
    onFertig: (ergebnis) => {
      if (!ergebnis.user) return
      updateUser(ergebnis.user)
      setHinweis(t('mediaserver.linked'))
    },
  })

  const trennen = useMutation({
    mutationFn: () => api.delete<User>('/api/auth/mediaserver/link'),
    onMutate: () => {
      setHinweis(null)
      verbinden.setFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setHinweis(t('mediaserver.disconnected'))
    },
    onError: (caught) =>
      verbinden.setFehler(
        caught instanceof ApiError ? caught.message : t('errors.generic'),
      ),
  })

  // Ohne verbundenen Server gibt es hier nichts zu tun - und wer bereits
  // verknüpft ist, soll die Karte trotzdem sehen, um trennen zu können.
  if (!mediaServerLogin && !user?.mediaserver_linked) return null

  return (
    <Card>
      <h2 className="text-lg font-semibold">{t('mediaserver.profileTitle')}</h2>
      <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.profileIntro')}</p>

      {user?.mediaserver_linked ? (
        <div className="mt-4">
          <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm">
            <span className="text-mist-500">{t('mediaserver.connected')} </span>
            <span className="font-medium text-mist-200">{user.mediaserver_username}</span>
          </p>

          {/* Wer kein Passwort hat, würde sich mit dem Trennen aussperren.
              Der Server lehnt das ab - hier steht der Grund vorher. */}
          {!user.has_password && (
            <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {t('mediaserver.needsPassword')}
            </p>
          )}

          <Button
            variant="ghost"
            onClick={() => trennen.mutate()}
            loading={trennen.isPending}
            disabled={!user.has_password}
            className="mt-4 w-full"
          >
            {t('mediaserver.disconnect')}
          </Button>
        </div>
      ) : verbinden.start ? (
        <MediaServerPrompt start={verbinden.start} onAbbrechen={verbinden.abbrechen} />
      ) : (
        <Button
          onClick={() => void verbinden.starten()}
          loading={verbinden.laeuft}
          className="mt-4 w-full"
        >
          {t('mediaserver.connect')}
        </Button>
      )}

      {hinweis && (
        <p className="mt-3 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {hinweis}
        </p>
      )}
      {verbinden.fehler && (
        <div className="mt-3">
          <ErrorBanner message={verbinden.fehler} />
        </div>
      )}
    </Card>
  )
}
