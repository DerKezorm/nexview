import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError, api, clearTokens } from '../api/client'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Logo } from '../components/Logo'
import { Button, Card, ErrorBanner, Field, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'

type InvitationInfo = { email: string; role: string }
type PasswordInfo = { username: string }

/** Rahmen für alle Seiten, die man ohne Anmeldung erreicht. */
function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="nv-glow flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="relative z-10 w-full max-w-md">
        <div className="mb-6 flex items-center justify-between">
          <Logo withWordmark />
          <LanguageSwitcher />
        </div>
        <Card>{children}</Card>
      </div>
    </div>
  )
}

function Laden() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 text-sm text-mist-500">
      <Spinner /> {t('common.loading')}
    </p>
  )
}

function Abgelaufen({ nachricht }: { nachricht: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.expiredTitle')}</h1>
      <p className="mt-2 text-sm text-mist-500">{nachricht}</p>
      <Button className="mt-6 w-full" onClick={() => navigate('/')}>
        {t('onboarding.toLogin')}
      </Button>
    </>
  )
}

/** Passwortfelder mit Gleichheitsprüfung - für beide Seiten gleich. */
function PasswordFields({
  password,
  repeat,
  onPassword,
  onRepeat,
  minLength,
}: {
  password: string
  repeat: string
  onPassword: (value: string) => void
  onRepeat: (value: string) => void
  minLength: number
}) {
  const { t } = useTranslation()
  const passtNicht = repeat !== '' && password !== repeat

  return (
    <>
      <Field
        label={t('onboarding.password')}
        type="password"
        value={password}
        onChange={(event) => onPassword(event.target.value)}
        hint={t('adminUsers.passwordHint', { count: minLength })}
        autoComplete="new-password"
        required
      />
      <Field
        label={t('onboarding.passwordRepeat')}
        type="password"
        value={repeat}
        onChange={(event) => onRepeat(event.target.value)}
        hint={passtNicht ? t('onboarding.passwordMismatch') : undefined}
        autoComplete="new-password"
        required
      />
    </>
  )
}

/** Einladung einlösen: Benutzername, Name und Passwort selbst wählen. */
export function InvitationPage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4

  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [angelegt, setAngelegt] = useState(false)

  const infoQuery = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => api.get<InvitationInfo>(`/api/onboarding/invitation/${token}`),
    retry: false,
  })

  // Schon beim Tippen zeigen, ob der Name noch frei ist - sonst erfährt man
  // es erst nach dem Absenden.
  const [geprueft, setGeprueft] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setGeprueft(username.trim()), 400)
    return () => clearTimeout(timer)
  }, [username])

  const verfuegbar = useQuery({
    queryKey: ['username-available', geprueft],
    queryFn: () =>
      api.get<{ available: boolean }>(
        `/api/onboarding/username-available?username=${encodeURIComponent(geprueft)}`,
      ),
    enabled: geprueft.length >= 3,
  })

  const annehmen = useMutation({
    mutationFn: () =>
      api.post(`/api/onboarding/invitation/${token}`, {
        username: username.trim(),
        display_name: displayName.trim() || null,
        password,
      }),
    onSuccess: () => {
      // Wichtig: Eine eventuell offene fremde Sitzung beenden. Sonst landet
      // der Eingeladene in dem Konto, das im selben Browser noch angemeldet
      // war - typischerweise beim Administrator, der die Einladung gerade
      // verschickt hat.
      clearTokens()
      setAngelegt(true)
    },
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.network')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password !== repeat) {
      setError(t('onboarding.passwordMismatch'))
      return
    }
    annehmen.mutate()
  }

  if (infoQuery.isPending) return <Frame><Laden /></Frame>
  if (infoQuery.isError || !infoQuery.data) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            infoQuery.error instanceof ApiError
              ? infoQuery.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  if (angelegt) {
    return (
      <Frame>
        <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.readyTitle')}</h1>
        <p className="mt-2 text-sm text-mist-500">
          {t('onboarding.readyText', { username: username.trim() })}
        </p>
        <Button
          className="mt-6 w-full"
          onClick={() => {
            // Neu laden, damit die App den alten Anmeldezustand vergisst.
            window.location.href = '/'
          }}
        >
          {t('onboarding.toLogin')}
        </Button>
      </Frame>
    )
  }

  const nameFrei = verfuegbar.data?.available
  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.inviteTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">
        {t('onboarding.inviteIntro', { email: infoQuery.data.email })}
      </p>

      <form onSubmit={absenden} className="mt-6 flex flex-col gap-4">
        <Field
          label={t('onboarding.username')}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          hint={
            geprueft.length < 3
              ? t('onboarding.usernameHint')
              : verfuegbar.isPending
                ? t('onboarding.usernameChecking')
                : nameFrei
                  ? t('onboarding.usernameFree')
                  : t('onboarding.usernameTaken')
          }
          autoComplete="username"
          required
          autoFocus
        />
        <Field
          label={t('onboarding.displayName')}
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          hint={t('onboarding.displayNameHint')}
          autoComplete="name"
        />
        <PasswordFields
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
          minLength={minPassword}
        />

        {error && <ErrorBanner message={error} />}

        <Button
          type="submit"
          loading={annehmen.isPending}
          disabled={nameFrei === false}
          className="mt-1 w-full"
        >
          {t('onboarding.createAccount')}
        </Button>
      </form>
    </Frame>
  )
}

/** Passwort setzen - erstes nach dem Anlegen oder ein vergessenes ersetzen. */
export function SetPasswordPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { token = '' } = useParams()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4

  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [fertig, setFertig] = useState(false)

  const infoQuery = useQuery({
    queryKey: ['password-token', token],
    queryFn: () => api.get<PasswordInfo>(`/api/onboarding/password/${token}`),
    retry: false,
  })

  const setzen = useMutation({
    mutationFn: () => api.post(`/api/onboarding/password/${token}`, { password }),
    onSuccess: () => setFertig(true),
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.network')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password !== repeat) {
      setError(t('onboarding.passwordMismatch'))
      return
    }
    setzen.mutate()
  }

  if (infoQuery.isPending) return <Frame><Laden /></Frame>
  if (infoQuery.isError || !infoQuery.data) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            infoQuery.error instanceof ApiError
              ? infoQuery.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  if (fertig) {
    return (
      <Frame>
        <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.doneTitle')}</h1>
        <p className="mt-2 text-sm text-mist-500">{t('onboarding.doneText')}</p>
        <Button className="mt-6 w-full" onClick={() => navigate('/', { replace: true })}>
          {t('onboarding.toLogin')}
        </Button>
      </Frame>
    )
  }

  // Konten entstehen nur über Einladungen - dieser Weg ist deshalb immer
  // ein vergessenes Passwort, nie das allererste.
  const { username } = infoQuery.data
  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.resetTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">
        {t('onboarding.resetIntro', { username })}
      </p>

      <form onSubmit={absenden} className="mt-6 flex flex-col gap-4">
        <PasswordFields
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
          minLength={minPassword}
        />

        {error && <ErrorBanner message={error} />}

        <Button type="submit" loading={setzen.isPending} className="mt-1 w-full">
          {t('onboarding.savePassword')}
        </Button>
      </form>
    </Frame>
  )
}

/** Passwort vergessen: Adresse eingeben, Link anfordern. */
export function ForgotPasswordPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')

  const anfordern = useMutation({
    mutationFn: () =>
      api.post<{ message: string }>('/api/onboarding/forgot-password', {
        email: email.trim(),
      }),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    anfordern.mutate()
  }

  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.forgotTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">{t('onboarding.forgotIntro')}</p>

      {anfordern.isSuccess ? (
        <>
          {/* Absichtlich dieselbe Antwort, ob es die Adresse gibt oder nicht -
              sonst könnte hier jeder durchprobieren, wer ein Konto hat. */}
          <p className="mt-6 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {anfordern.data.message}
          </p>
          <Button className="mt-4 w-full" onClick={() => navigate('/', { replace: true })}>
            {t('onboarding.toLogin')}
          </Button>
        </>
      ) : (
        <form onSubmit={absenden} className="mt-6 flex flex-col gap-4">
          <Field
            label={t('onboarding.email')}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            required
            autoFocus
          />
          <Button type="submit" loading={anfordern.isPending} className="mt-1 w-full">
            {t('onboarding.requestLink')}
          </Button>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
          >
            {t('onboarding.toLogin')}
          </button>
        </form>
      )}
    </Frame>
  )
}

/** Adresse bestätigen - ein Klick, dann steht das Ergebnis da. */
export function VerifyEmailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { token = '' } = useParams()

  const bestaetigen = useMutation({
    mutationFn: () => api.post(`/api/onboarding/verify/${token}`),
  })

  // Der Link soll ohne weiteres Zutun wirken.
  useEffect(() => {
    bestaetigen.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  if (bestaetigen.isPending || bestaetigen.isIdle) return <Frame><Laden /></Frame>

  if (bestaetigen.isError) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            bestaetigen.error instanceof ApiError
              ? bestaetigen.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.verifiedTitle')}</h1>
      <p className="mt-2 text-sm text-mist-500">{t('onboarding.verifiedText')}</p>
      <Button className="mt-6 w-full" onClick={() => navigate('/', { replace: true })}>
        {t('onboarding.continue')}
      </Button>
    </Frame>
  )
}
