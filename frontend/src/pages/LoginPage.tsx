import { useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Logo } from '../components/Logo'
import { MediaServerLoginRow } from '../components/MediaServerLoginRow'
import { OidcLoginRow } from '../components/OidcLoginRow'
import { ThemeSwitcher } from '../components/ThemeSwitcher'
import { Button, Card, ErrorBanner, Field } from '../components/ui'
import i18n from '../i18n'

/**
 * Die Kennung aus einer gescheiterten OIDC-Rückkehr – einmal gelesen, dann
 * aus der Adresse geräumt.
 *
 * Der Rückweg vom Anbieter ist eine Browser-Weiterleitung; scheitert er,
 * steht die Kennung als `?oidc_fehler=...` in der Adresse statt in einer
 * API-Antwort. Sie wird sofort per `replaceState` entfernt: Ein neu geladenes
 * Fenster soll die alte Meldung nicht noch einmal zeigen, und in kopierten
 * Adressen hat sie nichts verloren.
 */
function oidcFehlerAusAdresse(): string | null {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('oidc_fehler')
  if (!code) return null
  params.delete('oidc_fehler')
  const rest = params.toString()
  window.history.replaceState(
    null,
    '',
    window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash,
  )
  return code
}

export function LoginPage() {
  const { t } = useTranslation()
  const { login, loginWithTokens, mediaServerWays } = useAuth()
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(() => {
    const code = oidcFehlerAusAdresse()
    if (!code) return null
    const schluessel = `errors.byCode.${code}`
    return i18n.exists(schluessel) ? i18n.t(schluessel) : i18n.t('errors.generic')
  })
  const [busy, setBusy] = useState(false)
  /** Adresse noch nicht bestätigt: dann statt Fehlermeldung den Ausweg zeigen. */
  const [pending, setPending] = useState<{ email: string; message: string } | null>(null)

  async function anmelden() {
    if (busy) return
    setError(null)
    setPending(null)
    setBusy(true)
    try {
      await login(username.trim(), password)
      // Nach jeder Anmeldung auf die Startseite - sonst landet man wieder
      // dort, wo die vorherige Sitzung abgelaufen ist.
      navigate('/', { replace: true })
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'email_unverified') {
        setPending({
          email: String(caught.data?.email ?? ''),
          message: caught.message,
        })
      } else {
        setError(caught instanceof ApiError ? caught.message : t('errors.network'))
      }
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    void anmelden()
  }

  /** Enter im Passwortfeld meldet an - auch wenn der Browser das Formular
      nicht von sich aus abschickt (manche Passwortverwaltungen stören). */
  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return
    event.preventDefault()
    void anmelden()
  }

  return (
    <div className="nv-glow flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="relative z-10 w-full max-w-sm">
        <div className="mb-6 flex items-center justify-between">
          <Logo withWordmark />
          {/* Hell/Dunkel schon vor dem Anmelden - wer es zu dunkel findet,
              soll nicht erst hineinkommen muessen, um das zu aendern. */}
          <div className="flex items-center gap-2">
            <ThemeSwitcher />
            <LanguageSwitcher />
          </div>
        </div>

        {pending ? (
          <PendingVerification
            username={username.trim()}
            password={password}
            email={pending.email}
            message={pending.message}
            onBack={() => setPending(null)}
          />
        ) : (
        <Card>
          <h1 className="text-2xl font-bold tracking-tight">{t('login.title')}</h1>
          <p className="mt-1.5 text-sm text-mist-500">{t('app.tagline')}</p>

          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
            <Field
              label={t('login.usernameLabel')}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              onKeyDown={handleKeyDown}
              autoComplete="username"
              required
              autoFocus
            />
            <Field
              label={t('login.passwordLabel')}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              onKeyDown={handleKeyDown}
              autoComplete="current-password"
              required
            />

            {error && <ErrorBanner message={error} />}

            <Button type="submit" loading={busy} className="mt-1 w-full">
              {t('login.submit')}
            </Button>
            <button
              type="button"
              onClick={() => navigate('/passwort-vergessen')}
              className="text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
            >
              {t('login.forgot')}
            </button>
          </form>

          {/* Eine Reihe Logos statt eines festen Knopfes - und nur für
              Anbieter, die auch wirklich einen Anmeldeweg haben. */}
          <MediaServerLoginRow
            ways={mediaServerWays}
            onTokens={async (tokens) => {
              await loginWithTokens(tokens)
              navigate('/', { replace: true })
            }}
          />

          {/* Die eingerichteten OIDC-Anbieter. Nach der Rückkehr baut der
              Start der App die Sitzung aus dem frischen Cookie – hier gibt es
              deshalb keinen onTokens-Weg, nur den Absprung. */}
          <OidcLoginRow />
        </Card>
        )}
      </div>
    </div>
  )
}

/**
 * Anmeldung gesperrt, weil die Adresse noch nicht bestätigt ist.
 *
 * Der Ausweg, damit ein Tippfehler in der Adresse die Installation nicht
 * unbrauchbar macht: neu senden oder Adresse korrigieren. Beides verlangt
 * erneut das Passwort - eine Sitzung gibt es hier ja gerade nicht.
 */
function PendingVerification({
  username,
  password,
  email,
  message,
  onBack,
}: {
  username: string
  password: string
  email: string
  message: string
  onBack: () => void
}) {
  const { t } = useTranslation()
  const [neueAdresse, setNeueAdresse] = useState(email)
  const [aendern, setAendern] = useState(false)
  const [ergebnis, setErgebnis] = useState<{ ok: boolean; text: string } | null>(null)

  const senden = useMutation({
    mutationFn: () =>
      api.post<{ sent: boolean; error: string | null }>('/api/onboarding/pending/resend', {
        username,
        password,
      }),
    onMutate: () => setErgebnis(null),
    onSuccess: (antwort) =>
      setErgebnis(
        antwort.sent
          ? { ok: true, text: t('login.verifySent', { email }) }
          : { ok: false, text: antwort.error ?? t('errors.generic') },
      ),
    onError: (caught) =>
      setErgebnis({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('errors.network'),
      }),
  })

  const korrigieren = useMutation({
    mutationFn: () =>
      api.put<{ sent: boolean; error: string | null }>('/api/onboarding/pending/email', {
        username,
        password,
        email: neueAdresse.trim(),
      }),
    onMutate: () => setErgebnis(null),
    onSuccess: (antwort) => {
      setAendern(false)
      setErgebnis(
        antwort.sent
          ? { ok: true, text: t('login.verifySent', { email: neueAdresse.trim() }) }
          : { ok: false, text: antwort.error ?? t('errors.generic') },
      )
    },
    onError: (caught) =>
      setErgebnis({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('errors.network'),
      }),
  })

  return (
    <Card>
      <h1 className="text-2xl font-bold tracking-tight">{t('login.verifyTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">{message}</p>
      <p className="mt-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-300">
        {email}
      </p>

      {aendern && (
        <div className="mt-4">
          <Field
            label={t('login.newAddress')}
            type="email"
            value={neueAdresse}
            onChange={(event) => setNeueAdresse(event.target.value)}
            hint={t('login.newAddressHint')}
            autoComplete="email"
            autoFocus
          />
        </div>
      )}

      {ergebnis && (
        <p
          className={
            'mt-4 rounded-xl border px-4 py-3 text-sm ' +
            (ergebnis.ok
              ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
              : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
          }
        >
          {ergebnis.text}
        </p>
      )}

      <div className="mt-5 flex flex-col gap-2">
        {aendern ? (
          <>
            <Button
              onClick={() => korrigieren.mutate()}
              loading={korrigieren.isPending}
              disabled={!neueAdresse.trim() || neueAdresse.trim() === email}
              className="w-full"
            >
              {t('login.saveAddress')}
            </Button>
            <Button variant="ghost" onClick={() => setAendern(false)} className="w-full">
              {t('common.cancel')}
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => senden.mutate()} loading={senden.isPending} className="w-full">
              {t('login.resend')}
            </Button>
            <Button variant="ghost" onClick={() => setAendern(true)} className="w-full">
              {t('login.changeAddress')}
            </Button>
          </>
        )}
        <button
          type="button"
          onClick={onBack}
          className="mt-1 text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
        >
          {t('login.backToLogin')}
        </button>
      </div>
    </Card>
  )
}
