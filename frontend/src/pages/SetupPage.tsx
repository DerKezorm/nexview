import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { SetupStatus } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Logo } from '../components/Logo'
import { Button, Card, ErrorBanner, Field } from '../components/ui'
import { AddressStep } from './setup/AddressStep'
import { AvatarStep } from './setup/AvatarStep'
import { DoneStep } from './setup/DoneStep'
import { MailStep } from './setup/MailStep'
import { ServiceStep } from './setup/ServiceStep'
import { StepIndicator } from './setup/SetupSteps'
import type { SetupStep } from './setup/SetupSteps'

/**
 * Einmaliger Assistent beim allerersten Start.
 *
 * Führt durch Administrator-Konto, TMDB und optional Radarr/Sonarr. Die
 * Dienst-Schritte lassen sich überspringen - Nexview läuft dann zunächst mit
 * Beispieldaten bzw. ohne Anfragemöglichkeit.
 */
export function SetupPage() {
  const { t, i18n } = useTranslation()
  const { completeSetup, finishSetup } = useAuth()
  const [step, setStep] = useState<SetupStep>('account')

  return (
    <div className="nv-glow flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="relative z-10 w-full max-w-lg">
        <div className="mb-6 flex items-center justify-between">
          <Logo withWordmark />
          <LanguageSwitcher />
        </div>

        <Card>
          <StepIndicator current={step} />

          {step === 'account' && (
            <AccountStep
              onDone={() => setStep('avatar')}
              completeSetup={completeSetup}
              language={i18n.language}
            />
          )}

          {step === 'avatar' && (
            <AvatarStep onDone={() => setStep('tmdb')} onSkip={() => setStep('tmdb')} />
          )}

          {step === 'tmdb' && (
            <ServiceStep
              service="tmdb"
              title={t('setup.tmdbTitle')}
              description={t('setup.tmdbText')}
              onDone={() => setStep('radarr')}
              onSkip={() => setStep('radarr')}
            />
          )}

          {step === 'radarr' && (
            <ServiceStep
              service="radarr"
              title={t('setup.radarrTitle')}
              description={t('setup.radarrText')}
              withUrl
              urlPlaceholder="http://192.168.1.10:7878"
              onDone={() => setStep('sonarr')}
              onSkip={() => setStep('sonarr')}
            />
          )}

          {step === 'sonarr' && (
            <ServiceStep
              service="sonarr"
              title={t('setup.sonarrTitle')}
              description={t('setup.sonarrText')}
              withUrl
              urlPlaceholder="http://192.168.1.10:8989"
              onDone={() => setStep('address')}
              onSkip={() => setStep('address')}
            />
          )}

          {step === 'address' && (
            <AddressStep onDone={() => setStep('mail')} />
          )}

          {step === 'mail' && (
            <MailStep onDone={() => setStep('done')} />
          )}

          {step === 'done' && <DoneStep onFinish={finishSetup} />}
        </Card>
      </div>
    </div>
  )
}

type AccountStepProps = {
  onDone: () => void
  completeSetup: ReturnType<typeof useAuth>['completeSetup']
  language: string
}

function AccountStep({ onDone, completeSetup, language }: AccountStepProps) {
  const { t } = useTranslation()

  // Die Mindestlänge kommt vom Server. /api/config setzt eine Anmeldung
  // voraus - die gibt es hier noch nicht, also liefert sie der Setup-Status.
  const statusQuery = useQuery({
    queryKey: ['setup-status'],
    queryFn: () => api.get<SetupStatus>('/api/setup/status', { auth: false }),
  })
  const minPassword = statusQuery.data?.min_password_length ?? 4

  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [passwordRepeat, setPasswordRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)

    if (password !== passwordRepeat) {
      setError(t('setup.mismatch'))
      return
    }

    setBusy(true)
    try {
      await completeSetup({
        username: username.trim(),
        password,
        email: email.trim(),
        display_name: displayName.trim() || undefined,
        language,
      })
      onDone()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t('errors.network'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h2 className="text-xl font-bold tracking-tight">{t('setup.title')}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-mist-500">{t('setup.intro')}</p>

      <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-4">
        <Field
          label={t('setup.usernameLabel')}
          hint={t('setup.usernameHint')}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          required
          autoFocus
        />
        <Field
          label={`${t('setup.displayNameLabel')} (${t('common.optional')})`}
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          autoComplete="nickname"
        />
        {/* Ohne Adresse gäbe es später keinen Weg zurück ins eigene Konto. */}
        <Field
          label={t('setup.emailLabel')}
          hint={t('setup.emailHint')}
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          required
        />
        <Field
          label={t('setup.passwordLabel')}
          hint={t('setup.passwordHint', { count: minPassword })}
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="new-password"
          required
          minLength={minPassword}
        />
        <Field
          label={t('setup.passwordRepeatLabel')}
          type="password"
          value={passwordRepeat}
          onChange={(event) => setPasswordRepeat(event.target.value)}
          autoComplete="new-password"
          required
        />

        {error && <ErrorBanner message={error} />}

        <Button type="submit" loading={busy} className="mt-1 w-full">
          {t('setup.submit')}
        </Button>
        <p className="text-xs leading-relaxed text-mist-600">{t('setup.note')}</p>
      </form>
    </>
  )
}
