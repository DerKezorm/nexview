import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MailSecurity, TestResult } from '../../api/types'
import { Button, ErrorBanner, Field } from '../../components/ui'

const SECURITIES: MailSecurity[] = ['starttls', 'ssl', 'none']

/** Übliche Ports je Verschlüsselung – als Vorschlag beim Umschalten. */
const PORT_FOR: Record<MailSecurity, string> = {
  starttls: '587',
  ssl: '465',
  none: '25',
}

/**
 * Schritt „E-Mail" im Einrichtungsassistenten.
 *
 * Zugangsdaten des SMTP-Servers. Ohne ihn verschickt Nexview nichts: keine
 * Einladungen und keine Links zum Zurücksetzen von Passwörtern.
 */
export function MailStep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()

  const [host, setHost] = useState('')
  const [port, setPort] = useState('587')
  const [security, setSecurity] = useState<MailSecurity>('starttls')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fromAddress, setFromAddress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  /** Erst ein bestandener Test schaltet Weiter frei. */
  const geprueft = testResult?.ok === true

  function changeSecurity(wert: MailSecurity) {
    const istStandardPort = Object.values(PORT_FOR).includes(port)
    setSecurity(wert)
    if (istStandardPort) setPort(PORT_FOR[wert])
    setTestResult(null)
  }

  const testMutation = useMutation({
    mutationFn: () =>
      api.post<TestResult>('/api/settings/test/smtp', {
        host: host.trim() || null,
        port: Number(port) || null,
        security,
        username: username.trim() || null,
        password: password || null,
      }),
    onMutate: () => setTestResult(null),
    onSuccess: setTestResult,
    onError: (caught) =>
      setTestResult({
        ok: false,
        message: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      api.put('/api/settings', {
        smtp_host: host.trim(),
        smtp_port: Number(port) || 587,
        smtp_security: security,
        smtp_username: username.trim(),
        smtp_password: password,
        smtp_from_address: fromAddress.trim(),
      }),
    onSuccess: onDone,
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.network')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setError(null)
    saveMutation.mutate()
  }

  return (
    <>
      <h2 className="text-xl font-bold tracking-tight">{t('setup.mailTitle')}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-mist-500">{t('setup.mailText')}</p>

      <form onSubmit={absenden} className="mt-5 flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Field
              label={t('mail.host')}
              value={host}
              onChange={(event) => {
              setHost(event.target.value)
              setTestResult(null)
            }}
              placeholder="smtp.beispiel.de"
              autoComplete="off"
            />
          </div>
          <Field
            label={t('mail.port')}
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(event) => {
              setPort(event.target.value)
              setTestResult(null)
            }}
            autoComplete="off"
          />
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-mist-300">{t('mail.security')}</span>
          <select
            value={security}
            onChange={(event) => changeSecurity(event.target.value as MailSecurity)}
            className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {SECURITIES.map((wert) => (
              <option key={wert} value={wert}>
                {t(`mail.security_${wert}`)}
              </option>
            ))}
          </select>
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label={t('mail.username')}
            value={username}
            onChange={(event) => {
              setUsername(event.target.value)
              setTestResult(null)
            }}
            autoComplete="off"
          />
          <Field
            label={t('mail.password')}
            type="password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value)
              setTestResult(null)
            }}
            autoComplete="new-password"
          />
        </div>

        <Field
          label={t('mail.fromAddress')}
          type="email"
          value={fromAddress}
          onChange={(event) => {
              setFromAddress(event.target.value)
              setTestResult(null)
            }}
          placeholder="nexview@beispiel.de"
          hint={t('mail.fromAddressHint')}
          autoComplete="off"
        />

        {error && <ErrorBanner message={error} />}

        {/* Kein Ueberspringen: ohne Mailserver geht die Bestaetigungsmail nie
            raus, und danach kommt niemand mehr in die frische Installation. */}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!host.trim()}
          >
            {t('mail.testConnection')}
          </Button>
          <Button type="submit" loading={saveMutation.isPending} disabled={!geprueft}>
            {t('setup.saveAndContinue')}
          </Button>
        </div>

        {testResult && (
          <p className={'text-sm ' + (testResult.ok ? 'text-ok-500' : 'text-bad-500')}>
            {testResult.message}
          </p>
        )}

        <p className="text-xs text-mist-600">
          {geprueft ? t('setup.mailReady') : t('setup.mailRequired')}
        </p>
      </form>
    </>
  )
}
