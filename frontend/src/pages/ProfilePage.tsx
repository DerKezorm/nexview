import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { User } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { Button, Card, ErrorBanner, Field } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { DiscoverDefaults } from './profile/DiscoverDefaults'
import { MediaServerLink } from './profile/MediaServerLink'
import { WatchlistPlex } from './profile/WatchlistPlex'
import { NotificationSettings } from './profile/NotificationSettings'
import { StorageMine } from './profile/StorageMine'

/**
 * Reiter des eigenen Profils.
 *
 * Untereinander waren das sechs Karten - eine Liste, in der man das Gesuchte
 * nur noch durch Scrollen findet. Aufgeteilt wie die Einstellungen des
 * Administrators, damit beide Seiten sich gleich anfühlen.
 */
type Tab = 'account' | 'notifications' | 'discover' | 'security' | 'watchlist' | 'storage'

export function ProfilePage() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4
  const [tab, setTab] = useState<Tab>('account')

  const fileRef = useRef<HTMLInputElement>(null)
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [repeatPassword, setRepeatPassword] = useState('')

  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setMessage(null)
    setError(null)
  }

  function fail(caught: unknown) {
    setMessage(null)
    setError(caught instanceof ApiError ? caught.message : t('errors.generic'))
  }

  const avatarMutation = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      // Kein JSON: der Browser setzt die passende Kopfzeile für den Upload selbst.
      return api.upload<User>('/api/auth/me/avatar', body)
    },
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      setMessage(t('profile.avatarSaved'))
    },
    onError: fail,
  })

  const removeAvatarMutation = useMutation({
    mutationFn: () => api.delete<User>('/api/auth/me/avatar'),
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      setMessage(t('profile.avatarRemoved'))
    },
    onError: fail,
  })

  const nameMutation = useMutation({
    mutationFn: () => api.patch<User>('/api/auth/me', { display_name: displayName.trim() }),
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      setMessage(t('profile.nameSaved'))
    },
    onError: fail,
  })

  const emailMutation = useMutation({
    mutationFn: () => api.put<User>('/api/auth/me/email', { email: email.trim() }),
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      // Die neue Adresse muss erst bestätigt werden - das soll man merken.
      setMessage(t('profile.emailChanged'))
    },
    onError: fail,
  })

  const resendMutation = useMutation({
    mutationFn: () =>
      api.post<{ sent: boolean; error: string | null }>('/api/auth/me/resend-verification'),
    onMutate: reset,
    onSuccess: (ergebnis) => {
      if (ergebnis.sent) setMessage(t('profile.verificationSent'))
      else setError(ergebnis.error ?? t('errors.generic'))
    },
    onError: fail,
  })

  const passwordMutation = useMutation({
    mutationFn: () =>
      api.post<void>('/api/auth/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      }),
    onMutate: reset,
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      setRepeatPassword('')
      setMessage(t('profile.passwordSaved'))
    },
    onError: fail,
  })

  function handlePassword(event: FormEvent) {
    event.preventDefault()
    reset()
    if (newPassword !== repeatPassword) {
      setError(t('setup.mismatch'))
      return
    }
    if (newPassword.length < minPassword) {
      setError(t('adminUsers.passwordTooShort', { count: minPassword }))
      return
    }
    passwordMutation.mutate()
  }

  if (!user) return null
  const name = user.display_name ?? user.username

  const tabs: { value: Tab; labelKey: string }[] = [
    { value: 'account', labelKey: 'profile.tabAccount' },
    { value: 'notifications', labelKey: 'profile.tabNotifications' },
    { value: 'discover', labelKey: 'profile.tabDiscover' },
    { value: 'security', labelKey: 'profile.tabSecurity' },
  ]
  // Nur wenn der Administrator die Merkliste freigeschaltet hat - sonst
  // stünde dort ein Reiter, hinter dem es nichts geben kann.
  if (config?.watchlist_enabled) {
    tabs.push({ value: 'watchlist', labelKey: 'profile.tabWatchlist' })
  }
  // Ohne eingeschaltete Speicher-Kontingente gibt es hier nichts zu sehen.
  if (config?.storage_enabled) {
    tabs.push({ value: 'storage', labelKey: 'profile.tabStorage' })
  }

  // Ueberschrift und Reiterreihe bekommen **immer** die volle Breite, nur der
  // Inhalt darunter wird eingeschnuert. Vorher hing beides an derselben
  // Breite - und weil breite Reiter (Merkliste, Speicher) einen breiteren
  // Inhalt haben, brach die Reiterreihe je nach gewaehltem Reiter um und bei
  // anderen nicht. Ein Menue darf sich nicht danach richten, was darunter steht.
  const schmal = tab !== 'watchlist' && tab !== 'storage'

  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('nav.profile')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('profile.intro')}</p>
      </header>

      <div className="flex flex-wrap gap-2" role="tablist">
        {tabs.map((entry) => (
          <button
            key={entry.value}
            type="button"
            role="tab"
            aria-selected={tab === entry.value}
            onClick={() => {
              setTab(entry.value)
              // Eine Erfolgsmeldung vom vorherigen Reiter hätte hier keinen
              // Bezug mehr - sie würde nur verwirren.
              reset()
            }}
            className={
              'rounded-full border px-4 py-2 text-sm font-medium transition-colors ' +
              (tab === entry.value
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {t(entry.labelKey)}
          </button>
        ))}
      </div>

      <div className={'flex flex-col gap-6 ' + (schmal ? 'max-w-2xl' : '')}>
      {error && <ErrorBanner message={error} />}
      {message && !error && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {message}
        </p>
      )}

      {tab === 'account' && (
        <>
          <Card className="flex flex-col gap-4">
            <h2 className="text-lg font-semibold">{t('profile.picture')}</h2>
            <div className="flex flex-wrap items-center gap-4">
              <Avatar url={user.avatar_url} name={name} className="h-20 w-20" />
              <div className="flex flex-wrap gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) avatarMutation.mutate(file)
                    event.target.value = ''
                  }}
                />
                <Button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  loading={avatarMutation.isPending}
                >
                  {t('profile.chooseImage')}
                </Button>
                {user.avatar_url && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => removeAvatarMutation.mutate()}
                    loading={removeAvatarMutation.isPending}
                  >
                    {t('profile.removeImage')}
                  </Button>
                )}
              </div>
            </div>
            <p className="text-xs text-mist-600">{t('profile.pictureHint')}</p>
          </Card>

          <Card className="flex flex-col gap-4">
            <h2 className="text-lg font-semibold">{t('profile.name')}</h2>

            {/* Nur zur Ansicht: der Benutzername steht in Anfragen, Freigaben und
            in den Radarr-/Sonarr-Etiketten. Ihn nachträglich zu ändern würde
            diese Spuren auseinanderreißen. */}
            <Field
              label={t('profile.username')}
              value={user.username}
              readOnly
              disabled
              hint={t('profile.usernameHint')}
            />

            <Field
              label={t('adminUsers.displayName')}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              hint={t('profile.nameHint')}
              autoComplete="nickname"
            />
            <div>
              {/* Gesperrt, solange nichts geändert wurde - genau wie bei der
                  Adresse darunter. Ein Knopf, der nichts bewirkt, sieht sonst
                  aus wie ein Knopf, der nicht funktioniert. */}
              <Button
                type="button"
                onClick={() => nameMutation.mutate()}
                loading={nameMutation.isPending}
                disabled={displayName.trim() === (user.display_name ?? '')}
              >
                {t('common.save')}
              </Button>
            </div>
          </Card>

          <Card className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{t('profile.email')}</h2>
              {user.email_verified ? (
                <span className="rounded-full border border-ok-500/40 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                  {t('profile.emailVerified')}
                </span>
              ) : (
                <span className="rounded-full border border-warn-500/40 bg-warn-500/10 px-2.5 py-0.5 text-xs text-warn-500">
                  {t('profile.emailUnverified')}
                </span>
              )}
            </div>

            <Field
              label={t('adminUsers.email')}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              hint={t('profile.emailHint')}
              autoComplete="email"
            />

            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                onClick={() => emailMutation.mutate()}
                loading={emailMutation.isPending}
                disabled={email.trim() === '' || email.trim() === (user.email ?? '')}
              >
                {t('common.save')}
              </Button>
              {/* Nur anbieten, wenn es auch etwas zu bestätigen gibt. */}
              {!user.email_verified && user.email && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => resendMutation.mutate()}
                  loading={resendMutation.isPending}
                >
                  {t('profile.resendVerification')}
                </Button>
              )}
            </div>
          </Card>
        </>
      )}

      {tab === 'storage' && <StorageMine />}
      {tab === 'notifications' && <NotificationSettings />}
      {tab === 'discover' && <DiscoverDefaults />}

      {tab === 'security' && (
        <Card>
          <h2 className="text-lg font-semibold">{t('profile.password')}</h2>
          <form onSubmit={handlePassword} className="mt-4 flex flex-col gap-4">
            <Field
              label={t('profile.currentPassword')}
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
            <Field
              label={t('profile.newPassword')}
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              hint={t('adminUsers.passwordHint', { count: minPassword })}
              autoComplete="new-password"
              required
              minLength={minPassword}
            />
            <Field
              label={t('setup.passwordRepeatLabel')}
              type="password"
              value={repeatPassword}
              onChange={(event) => setRepeatPassword(event.target.value)}
              autoComplete="new-password"
              required
            />
            <div>
              <Button type="submit" loading={passwordMutation.isPending}>
                {t('profile.changePassword')}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Die Verknüpfung gehört zur Anmeldung und damit neben das Passwort. */}
      {tab === 'security' && <MediaServerLink />}

      {/* Untermenü mit genau einem Eintrag - noch. Plex ist der einzige
          Anbieter mit Merkliste; Jellyfin und Emby haben keine. Die Reihe
          steht trotzdem da, damit ein zweiter Anbieter später kein Umbau
          der Navigation wird. */}
      {tab === 'watchlist' && (
        <>
          <div className="flex flex-wrap gap-2">
            <span className="rounded-full border border-accent-500/60 bg-accent-500/15 px-3.5 py-1.5 text-sm font-medium text-accent-400">
              Plex
            </span>
          </div>
          <WatchlistPlex />
        </>
      )}
      </div>
    </div>
  )
}
