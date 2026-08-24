/**
 * Das eigene Konto mit den Medienservern verbinden.
 *
 * Der Weg für alle, die schon ein Nexview-Konto haben – eingeladene Benutzer
 * ebenso wie der Administrator nach der Einrichtung. Danach führen alle Wege,
 * Passwort und Medienserver, in dasselbe Konto.
 *
 * ⚠️ **Eine Zeile je Anbieter, nicht eine für alle.** Bis 0.18.0 stand hier
 * genau eine Verbindung, fest mit „Plex" beschriftet. Im Parallelbetrieb war
 * das gleich doppelt falsch: Die Überschrift sagte Plex, während darunter der
 * Jellyfin-Kontoname stand – und der zweite Anbieter ließ sich gar nicht
 * verbinden, ohne den ersten zu verlieren.
 *
 * Verbinden setzt Zugriff auf die Bibliothek voraus. Das prüft der Server;
 * hier wird nur seine Antwort angezeigt.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { MediaServerLogo } from '../../components/MediaServerLogo'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { Button, Card, ErrorBanner, Field } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'
import { providerName } from '../../lib/mediaserver'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'

export function MediaServerLink() {
  const { t } = useTranslation()
  const { user, updateUser, mediaServerLogin } = useAuth()
  const { data: config } = useConfig()
  const [hinweis, setHinweis] = useState<string | null>(null)
  /** Für welchen Anbieter läuft gerade ein Verbinden-Vorgang? */
  const [aktiv, setAktiv] = useState<string | null>(null)

  const verbinden = useMediaServerChallenge<{ status: string; user: User | null }>({
    startPfad: '/api/auth/mediaserver/link/start',
    abfragePfad: '/api/auth/mediaserver/link/poll',
    onFertig: (ergebnis) => {
      setAktiv(null)
      if (!ergebnis.user) return
      updateUser(ergebnis.user)
      setHinweis(t('mediaserver.linked'))
    },
  })

  const trennen = useMutation({
    mutationFn: (provider: string) =>
      api.delete<User>(
        `/api/auth/mediaserver/link?provider=${encodeURIComponent(provider)}`,
      ),
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

  const verknuepft = user?.mediaserver_accounts ?? []
  // Verbunden ist Sache des Administrators; verknüpfen kann man sich nur mit
  // dem, was auch dasteht.
  const anbieter = config?.mediaserver_providers ?? []
  const mitPasswort = config?.mediaserver_password_login ?? []
  // Ein Anbieter, der nicht mehr verbunden ist, an dem das eigene Konto aber
  // noch hängt, gehört trotzdem in die Liste - sonst gäbe es keinen Weg, ihn
  // wieder loszuwerden.
  const zeigen = [...new Set([...anbieter, ...verknuepft.map((v) => v.provider)])]

  if (zeigen.length === 0 && !mediaServerLogin) return null

  // Das letzte verknüpfte Konto zu lösen sperrt aus, wer kein Passwort hat.
  const letzte = verknuepft.length <= 1

  return (
    <Card>
      <h2 className="text-lg font-semibold">{t('mediaserver.profileTitle')}</h2>
      <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.profileIntro')}</p>

      <div className="mt-4 flex flex-col gap-3">
        {zeigen.map((provider) => {
          const konto = verknuepft.find((v) => v.provider === provider)
          const name = providerName(provider)
          const sperrt = !!konto && letzte && !user?.has_password
          // ⚠️ Verknüpft, aber der Server ist weg.
          //
          // Das Trennen einer Server-Verbindung lässt die persönlichen
          // Verknüpfungen absichtlich stehen – wer den Server später wieder
          // verbindet, findet alles vor. Ohne diesen Hinweis las sich das
          // hier aber als „alles in Ordnung", während in den Einstellungen
          // „Nicht verbunden" stand. Zwei Seiten, zwei Antworten.
          const serverWeg = !!konto && !anbieter.includes(provider)

          return (
            <div
              key={provider}
              className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-sm">
                  <MediaServerLogo
                    provider={provider}
                    className={
                      'h-4 w-4 ' +
                      (konto && !serverWeg ? 'text-ok-500' : 'text-mist-700')
                    }
                  />
                  <span className="font-medium text-mist-200">{name}</span>
                  {konto ? (
                    <span className="text-mist-500">
                      {t('mediaserver.connected')}{' '}
                      <span className="text-mist-300">{konto.username}</span>
                    </span>
                  ) : (
                    <span className="text-mist-600">
                      {t('mediaserver.tileNotConnected')}
                    </span>
                  )}
                </span>

                {konto ? (
                  <Button
                    variant="ghost"
                    onClick={() => trennen.mutate(provider)}
                    loading={trennen.isPending && trennen.variables === provider}
                    disabled={sperrt}
                  >
                    {t('mediaserver.disconnect')}
                  </Button>
                ) : mitPasswort.includes(provider) ? (
                  /* Kein Code-Weg bei diesem Anbieter - hier werden
                     Benutzername und Passwort direkt eingegeben. */
                  <Button
                    variant="ghost"
                    onClick={() => setAktiv(aktiv === provider ? null : provider)}
                  >
                    {t('mediaserver.connectWith', { name })}
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setAktiv(provider)
                      void verbinden.starten()
                    }}
                    loading={verbinden.laeuft && aktiv === provider}
                  >
                    {t('mediaserver.connectWith', { name })}
                  </Button>
                )}
              </div>

              {serverWeg && (
                <p className="text-xs text-mist-600">{t('mediaserver.serverGone')}</p>
              )}

              {/* Wer kein Passwort hat, würde sich mit dem Trennen aussperren –
                  aber nur, wenn dies die letzte Verknüpfung ist. Bleibt noch
                  ein zweiter Server, führt der weiterhin hinein. */}
              {sperrt && (
                <p className="text-xs text-warn-500">{t('mediaserver.needsPassword')}</p>
              )}

              {aktiv === provider && mitPasswort.includes(provider) && (
                <PasswortVerknuepfen
                  provider={provider}
                  name={name}
                  onFertig={(aktualisiert) => {
                    updateUser(aktualisiert)
                    setAktiv(null)
                    setHinweis(t('mediaserver.linked'))
                  }}
                  onAbbrechen={() => setAktiv(null)}
                />
              )}

              {aktiv === provider && !mitPasswort.includes(provider) && verbinden.start && (
                <MediaServerPrompt
                  start={verbinden.start}
                  onAbbrechen={() => {
                    verbinden.abbrechen()
                    setAktiv(null)
                  }}
                />
              )}
            </div>
          )
        })}
      </div>

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

/**
 * Ein Medienserver-Konto mit Benutzername und Passwort verknüpfen.
 *
 * ⚠️ Das Passwort geht einmal an Nexview, von dort an den Medienserver – und
 * wird danach verworfen. Gespeichert wird nur das Token, das zurückkommt.
 */
function PasswortVerknuepfen({
  provider,
  name,
  onFertig,
  onAbbrechen,
}: {
  provider: string
  name: string
  onFertig: (user: User) => void
  onAbbrechen: () => void
}) {
  const { t } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  async function absenden(event: FormEvent) {
    event.preventDefault()
    setFehler(null)
    setLaeuft(true)
    try {
      const ergebnis = await api.post<{ status: string; user: User }>(
        '/api/auth/mediaserver/link/password',
        { provider, username, password },
      )
      setPassword('')
      onFertig(ergebnis.user)
    } catch (caught) {
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
      setPassword('')
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <form onSubmit={absenden} className="mt-1 flex flex-col gap-3">
      <Field
        label={t('mediaserver.serverUser', { name })}
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        autoComplete="off"
        required
        autoFocus
      />
      <Field
        label={t('login.passwordLabel')}
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        hint={t('mediaserver.adminPasswordHint')}
        autoComplete="off"
        required
      />
      {fehler && <ErrorBanner message={fehler} />}
      <div className="flex items-center gap-2">
        <Button type="submit" loading={laeuft}>
          {t('mediaserver.connectWith', { name })}
        </Button>
        <button
          type="button"
          onClick={onAbbrechen}
          className="text-xs text-mist-600 hover:text-mist-300"
        >
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}
