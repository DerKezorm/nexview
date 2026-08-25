/**
 * Verbinden mit Adresse, Benutzername und Passwort.
 *
 * Das Gegenstück zu {@link MediaServerPrompt}: Plex schickt zu plex.tv, wo man
 * einen Code bestätigt – Jellyfin und Emby haben keine solche Zwischenstelle,
 * dort werden die Angaben direkt eingegeben.
 *
 * ⚠️ **Das Passwort bleibt nirgends.** Es steht im Zustand dieses Formulars,
 * geht einmal an Nexview, von dort an den Medienserver – und wird danach
 * verworfen. Gespeichert wird ausschließlich das Token, das zurückkommt.
 * Deshalb wird das Feld nach dem Absenden auch geleert und nicht etwa
 * „vorgehalten, falls es nochmal gebraucht wird".
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../api/client'
import type { User } from '../api/types'
import { Button, ErrorBanner, Field } from './ui'
import { providerName } from '../lib/mediaserver'

type Ergebnis = {
  user: User
  server_name: string
  server_url: string
  reachable: boolean
  warning: string | null
}

export function MediaServerPasswordForm({
  provider,
  onVerbunden,
}: {
  provider: string
  onVerbunden: (ergebnis: Ergebnis) => void
}) {
  const { t } = useTranslation()
  const [url, setUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  const name = providerName(provider)

  async function absenden(event: FormEvent) {
    event.preventDefault()
    setFehler(null)
    setLaeuft(true)
    try {
      const ergebnis = await api.post<Ergebnis>(
        '/api/admin/mediaserver/connect/password',
        { provider, url, username, password },
      )
      // Zuerst leeren, dann melden: Was danach passiert, ist nicht mehr Sache
      // dieses Formulars – das Passwort soll aber in keinem Fall stehen
      // bleiben.
      setPassword('')
      onVerbunden(ergebnis)
    } catch (caught) {
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
      setPassword('')
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <form onSubmit={absenden} className="flex flex-col gap-4">
      <p className="text-sm text-mist-500">
        {t('mediaserver.passwordIntro', { name })}
      </p>

      <Field
        label={t('mediaserver.serverAddress')}
        type="url"
        value={url}
        onChange={(event) => setUrl(event.target.value)}
        placeholder="http://10.0.0.10:8096"
        hint={t('mediaserver.serverAddressHint')}
        autoComplete="off"
        required
      />
      <Field
        label={t('mediaserver.adminUser', { name })}
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        hint={t('mediaserver.adminUserHint')}
        autoComplete="off"
        required
      />
      <Field
        label={t('mediaserver.adminPassword')}
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        hint={t('mediaserver.adminPasswordHint')}
        autoComplete="off"
        required
      />

      {fehler && <ErrorBanner message={fehler} />}

      {/* Das Verbinden liest die Bibliothek des Servers ein, und das dauert
          bei ein paar tausend Titeln Minuten. Ohne diesen Hinweis sieht ein
          Knopf, der sich dreht, nach einem Aufhänger aus – und der nächste
          Griff ist das Neuladen der Seite, mitten im Vorgang. */}
      {laeuft && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('mediaserver.connectSlow')}
        </p>
      )}

      <div>
        <Button type="submit" loading={laeuft}>
          {t('mediaserver.connectWith', { name })}
        </Button>
      </div>
    </form>
  )
}
